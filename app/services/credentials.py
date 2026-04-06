"""Credential resolution and storage for user-scoped providers with instance fallback."""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.connection import async_session
from app.database.models import User, UserProviderCredential
from app.services.security import SecurityService


class CredentialService:
    """Resolve provider credentials for a user with instance fallback."""

    _provider_settings_map = {
        "gemini": "gemini_api_key",
        "groq": "groq_api_key",
        "wise": "wise_api_key",
        "exchange_rate": "exchange_rate_api_key",
    }

    def __init__(self) -> None:
        self.settings = get_settings()
        self.security = SecurityService()
        self.logger = logging.getLogger(__name__)

    def supported_providers(self) -> list[str]:
        """Return providers supported by the credential layer."""
        return list(self._provider_settings_map.keys())

    async def resolve_api_key(
        self,
        provider: str,
        user: User | None = None,
        session: AsyncSession | None = None,
    ) -> str:
        """Resolve the active API key for a provider, prioritizing the user credential."""
        normalized_provider = provider.strip().lower()
        if normalized_provider not in self._provider_settings_map:
            raise ValueError(f"Unsupported provider: {provider}")

        user_key = await self._resolve_user_api_key(normalized_provider, user, session)
        if user_key:
            return user_key

        return str(
            getattr(self.settings, self._provider_settings_map[normalized_provider], "") or ""
        )

    async def resolve_many(
        self,
        providers: list[str],
        user: User | None = None,
        session: AsyncSession | None = None,
    ) -> dict[str, str]:
        """Resolve multiple provider keys for the same user."""
        resolved: dict[str, str] = {}
        for provider in providers:
            resolved[provider] = await self.resolve_api_key(provider, user=user, session=session)
        return resolved

    async def _resolve_user_api_key(
        self,
        provider: str,
        user: User | None,
        session: AsyncSession | None,
    ) -> str:
        """Fetch and decrypt the active user-scoped API key when available."""
        if user is None or getattr(user, "id", None) is None:
            return ""

        if session is not None:
            return await self._resolve_user_api_key_with_session(session, int(user.id), provider)

        async with async_session() as db_session:
            return await self._resolve_user_api_key_with_session(db_session, int(user.id), provider)

    async def _resolve_user_api_key_with_session(
        self,
        session: AsyncSession,
        user_id: int,
        provider: str,
    ) -> str:
        """Read an encrypted provider key from the database and decrypt it."""
        try:
            result = await session.execute(
                select(UserProviderCredential)
                .where(UserProviderCredential.user_id == user_id)
                .where(UserProviderCredential.provider == provider)
                .where(UserProviderCredential.is_active == True)
                .order_by(UserProviderCredential.validated_at.desc().nullslast())
                .order_by(UserProviderCredential.id.desc())
            )
            credential = result.scalars().first()
            if credential is None:
                return ""

            return self.security.decrypt_api_key(credential.api_key_encrypted)
        except Exception as exc:
            self.logger.warning(
                "Could not resolve user credential for provider %s, using instance fallback: %s",
                provider,
                exc,
            )
            return ""

    async def list_user_credentials(
        self,
        session: AsyncSession,
        user: User,
    ) -> dict[str, dict[str, str | bool | None]]:
        """Return a safe summary of user-scoped provider credentials."""
        result = await session.execute(
            select(UserProviderCredential)
            .where(UserProviderCredential.user_id == user.id)
            .where(UserProviderCredential.is_active == True)
            .order_by(UserProviderCredential.id.desc())
        )
        credentials = result.scalars().all()

        summary: dict[str, dict[str, str | bool | None]] = {}
        for provider in self.supported_providers():
            summary[provider] = {
                "configured": False,
                "last4": None,
                "validated_at": None,
                "help_url": self._provider_help_url(provider),
                "label": self._provider_label(provider),
                "optional": provider in {"wise", "exchange_rate"},
            }

        for credential in credentials:
            provider = str(credential.provider)
            if provider not in summary:
                continue
            if summary[provider]["configured"]:
                continue
            summary[provider] = {
                "configured": True,
                "last4": str(credential.api_key_last4) if credential.api_key_last4 else None,
                "validated_at": credential.validated_at.isoformat()
                if credential.validated_at
                else None,
                "help_url": self._provider_help_url(provider),
                "label": self._provider_label(provider),
                "optional": provider in {"wise", "exchange_rate"},
            }

        return summary

    async def upsert_user_credential(
        self,
        session: AsyncSession,
        user: User,
        *,
        provider: str,
        api_key: str,
    ) -> UserProviderCredential:
        """Create or replace an active user-scoped provider credential."""
        normalized_provider = provider.strip().lower()
        if normalized_provider not in self._provider_settings_map:
            raise ValueError("Provider nao suportado.")

        normalized_key = api_key.strip()
        if not normalized_key:
            raise ValueError("API key obrigatoria.")

        result = await session.execute(
            select(UserProviderCredential)
            .where(UserProviderCredential.user_id == user.id)
            .where(UserProviderCredential.provider == normalized_provider)
            .where(UserProviderCredential.is_active == True)
        )
        existing_credentials = result.scalars().all()
        for existing in existing_credentials:
            existing.is_active = False
            existing.updated_at = datetime.now()

        credential = UserProviderCredential(
            user_id=user.id,
            provider=normalized_provider,
            api_key_encrypted=self.security.encrypt_api_key(normalized_key),
            api_key_last4=self.security.key_last4(normalized_key),
            is_active=True,
            validated_at=datetime.now(),
            created_at=datetime.now(),
        )
        session.add(credential)
        await session.commit()
        await session.refresh(credential)
        return credential

    async def deactivate_user_credential(
        self,
        session: AsyncSession,
        user: User,
        *,
        provider: str,
    ) -> bool:
        """Deactivate the active credential for a provider."""
        normalized_provider = provider.strip().lower()
        if normalized_provider not in self._provider_settings_map:
            raise ValueError("Provider nao suportado.")

        result = await session.execute(
            select(UserProviderCredential)
            .where(UserProviderCredential.user_id == user.id)
            .where(UserProviderCredential.provider == normalized_provider)
            .where(UserProviderCredential.is_active == True)
        )
        credential = result.scalars().first()
        if credential is None:
            return False

        credential.is_active = False
        credential.updated_at = datetime.now()
        await session.commit()
        return True

    def _provider_label(self, provider: str) -> str:
        """Return a user-facing provider label."""
        labels = {
            "gemini": "Google Gemini",
            "groq": "Groq",
            "wise": "Wise",
            "exchange_rate": "ExchangeRate API",
        }
        return labels.get(provider, provider)

    def _provider_help_url(self, provider: str) -> str:
        """Return a help URL explaining where to obtain the provider key."""
        urls = {
            "gemini": "https://aistudio.google.com/apikey",
            "groq": "https://console.groq.com/keys",
            "wise": "https://wise.com/your-account/integrations-and-tools/api-tokens",
            "exchange_rate": "https://www.exchangerate-api.com/",
        }
        return urls.get(provider, "")
