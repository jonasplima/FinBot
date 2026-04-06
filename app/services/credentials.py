"""Credential resolution for user-scoped providers with instance fallback."""

import logging

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
