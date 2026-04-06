"""User management service for onboarding and preferences."""

import logging
import re
import unicodedata
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import User, UserAuthorizedPhone
from app.utils.validators import normalize_phone

logger = logging.getLogger(__name__)
settings = get_settings()

LIMIT_FIELD_MAP = {
    "texto": "daily_text_limit",
    "text": "daily_text_limit",
    "mensagem": "daily_text_limit",
    "mensagens": "daily_text_limit",
    "midia": "daily_media_limit",
    "media": "daily_media_limit",
    "arquivo": "daily_media_limit",
    "arquivos": "daily_media_limit",
    "ia": "daily_ai_limit",
    "ai": "daily_ai_limit",
}


class UserService:
    """Service for managing users, onboarding and preferences."""

    def __init__(self) -> None:
        self.settings = settings

    async def get_or_create_user(
        self,
        session: AsyncSession,
        phone: str,
    ) -> User:
        """Get an existing user or create a new profile with default limits."""
        normalized_phone = normalize_phone(phone)
        user = await self.get_user_by_phone(session, normalized_phone)

        if user:
            if not user.backup_owner_id:
                user.backup_owner_id = self._generate_backup_owner_id()
            user.last_seen_at = datetime.now()
            await session.commit()
            return user

        defaults = self.settings.user_limit_defaults()
        user = User(
            phone=normalized_phone,
            backup_owner_id=self._generate_backup_owner_id(),
            accepted_terms=False,
            terms_version=None,
            is_active=True,
            preferred_channel="whatsapp",
            timezone="America/Sao_Paulo",
            base_currency="BRL",
            decimal_separator=",",
            thousands_separator=".",
            limits_enabled=True,
            daily_text_limit=defaults["daily_text_limit"],
            daily_media_limit=defaults["daily_media_limit"],
            daily_ai_limit=defaults["daily_ai_limit"],
            notification_preferences={"whatsapp": True},
            last_seen_at=datetime.now(),
        )

        session.add(user)
        await session.commit()
        await session.refresh(user)
        logger.info(f"Created new user profile for {normalized_phone}")
        return user

    async def get_user_by_phone(
        self,
        session: AsyncSession,
        phone: str,
    ) -> User | None:
        """Return a persisted user by normalized phone when it exists."""
        normalized_phone = normalize_phone(phone)
        result = await session.execute(select(User).where(User.phone == normalized_phone))
        user = result.scalar_one_or_none()
        if user is not None:
            return user

        alias_result = await session.execute(
            select(User)
            .join(UserAuthorizedPhone, UserAuthorizedPhone.user_id == User.id)
            .where(UserAuthorizedPhone.phone == normalized_phone)
        )
        return alias_result.scalar_one_or_none()

    async def list_authorized_phones(
        self,
        session: AsyncSession,
        user: User,
    ) -> list[dict[str, str | bool]]:
        """Return primary and additional authorized numbers for a user account."""
        result = await session.execute(
            select(UserAuthorizedPhone)
            .where(UserAuthorizedPhone.user_id == user.id)
            .order_by(UserAuthorizedPhone.phone.asc())
        )
        aliases = result.scalars().all()

        phones: list[dict[str, str | bool]] = [
            {"phone": str(user.phone), "is_primary": True},
        ]
        for alias in aliases:
            phones.append({"phone": str(alias.phone), "is_primary": False})
        return phones

    async def add_authorized_phone(
        self,
        session: AsyncSession,
        user: User,
        phone: str,
    ) -> list[dict[str, str | bool]]:
        """Authorize an additional WhatsApp number for the same account."""
        normalized_phone = normalize_phone(phone)
        if normalized_phone == normalize_phone(str(user.phone)):
            return await self.list_authorized_phones(session, user)

        existing_primary = await session.execute(select(User).where(User.phone == normalized_phone))
        user_by_phone = existing_primary.scalar_one_or_none()
        if user_by_phone is not None and user_by_phone.id != user.id:
            raise ValueError("Esse numero ja pertence a outra conta.")

        existing_alias_query = await session.execute(
            select(UserAuthorizedPhone).where(UserAuthorizedPhone.phone == normalized_phone)
        )
        existing_alias = existing_alias_query.scalar_one_or_none()
        if existing_alias is not None:
            if existing_alias.user_id != user.id:
                raise ValueError("Esse numero ja esta autorizado em outra conta.")
            return await self.list_authorized_phones(session, user)

        session.add(
            UserAuthorizedPhone(
                user_id=user.id,
                phone=normalized_phone,
                created_at=datetime.now(),
            )
        )
        await session.commit()
        return await self.list_authorized_phones(session, user)

    async def remove_authorized_phone(
        self,
        session: AsyncSession,
        user: User,
        phone: str,
    ) -> list[dict[str, str | bool]]:
        """Remove an additional authorized number from the account."""
        normalized_phone = normalize_phone(phone)
        if normalized_phone == normalize_phone(str(user.phone)):
            raise ValueError("O numero principal da conta nao pode ser removido.")

        result = await session.execute(
            select(UserAuthorizedPhone)
            .where(UserAuthorizedPhone.user_id == user.id)
            .where(UserAuthorizedPhone.phone == normalized_phone)
        )
        alias = result.scalar_one_or_none()
        if alias is None:
            return await self.list_authorized_phones(session, user)

        await session.delete(alias)
        await session.commit()
        return await self.list_authorized_phones(session, user)

    async def is_phone_authorized(
        self,
        session: AsyncSession,
        phone: str,
    ) -> bool:
        """Check whether a phone is authorized by bootstrap list or web onboarding."""
        normalized_phone = normalize_phone(phone)

        if not self.settings.allowed_phones:
            return True

        if normalized_phone in {normalize_phone(item) for item in self.settings.allowed_phones}:
            return True

        user = await self.get_user_by_phone(session, normalized_phone)
        return bool(user and user.web_access_enabled and user.is_active)

    async def adopt_backup_owner_identity(
        self,
        session: AsyncSession,
        user: User,
        backup_owner_id: str,
    ) -> User:
        """Adopt a stable backup identity after an explicit account migration."""
        normalized_backup_owner_id = backup_owner_id.strip()
        if not normalized_backup_owner_id:
            raise ValueError("Identificador de backup invalido.")

        user.backup_owner_id = normalized_backup_owner_id
        user.updated_at = datetime.now()
        user.last_seen_at = datetime.now()
        await session.commit()
        await session.refresh(user)
        return user

    def has_accepted_current_terms(self, user: User) -> bool:
        """Check whether user accepted the current terms version."""
        return bool(
            user.accepted_terms
            and user.is_active
            and user.terms_version == self.settings.terms_version
        )

    async def accept_terms(self, session: AsyncSession, user: User) -> User:
        """Mark terms as accepted for a user."""
        user.accepted_terms = True
        user.accepted_terms_at = datetime.now()
        user.terms_version = self.settings.terms_version
        user.is_active = True
        user.last_seen_at = datetime.now()
        await session.commit()
        await session.refresh(user)
        return user

    async def update_web_profile(
        self,
        session: AsyncSession,
        user: User,
        *,
        name: str | None = None,
        display_name: str | None = None,
        timezone: str | None = None,
        email: str | None = None,
    ) -> User:
        """Update basic profile fields from the web onboarding flow."""
        if name is not None:
            normalized_name = name.strip()
            if not normalized_name:
                raise ValueError("Nome e obrigatorio.")
            user.name = normalized_name

        if display_name is not None:
            normalized_display_name = display_name.strip()
            user.display_name = normalized_display_name or None

        if timezone is not None:
            normalized_timezone = timezone.strip()
            if not normalized_timezone:
                raise ValueError("Timezone e obrigatorio.")
            user.timezone = normalized_timezone

        if email is not None:
            normalized_email = email.strip().lower()
            if normalized_email:
                if "@" not in normalized_email or normalized_email.startswith("@"):
                    raise ValueError("Email invalido.")
                local_part, _, domain = normalized_email.partition("@")
                if not local_part or "." not in domain:
                    raise ValueError("Email invalido.")
                user.email = normalized_email

        user.updated_at = datetime.now()
        user.last_seen_at = datetime.now()
        await session.commit()
        await session.refresh(user)
        return user

    async def update_notification_preferences(
        self,
        session: AsyncSession,
        user: User,
        *,
        budget_alerts: bool,
        recurring_reminders: bool,
        goal_updates: bool,
    ) -> User:
        """Update notification preferences exposed in the web settings panel."""
        current_preferences = dict(user.notification_preferences or {})
        current_preferences.update(
            {
                "whatsapp": True,
                "budget_alerts": budget_alerts,
                "recurring_reminders": recurring_reminders,
                "goal_updates": goal_updates,
            }
        )
        user.notification_preferences = current_preferences
        user.updated_at = datetime.now()
        user.last_seen_at = datetime.now()
        await session.commit()
        await session.refresh(user)
        return user

    async def update_base_currency(
        self,
        session: AsyncSession,
        user: User,
        *,
        base_currency: str,
    ) -> User:
        """Update the user's preferred base currency for web registration and conversion."""
        normalized_currency = base_currency.strip().upper()
        if len(normalized_currency) != 3 or not normalized_currency.isalpha():
            raise ValueError("Moeda base invalida.")

        user.base_currency = normalized_currency
        user.updated_at = datetime.now()
        user.last_seen_at = datetime.now()
        await session.commit()
        await session.refresh(user)
        return user

    async def update_number_format_preferences(
        self,
        session: AsyncSession,
        user: User,
        *,
        decimal_separator: str,
        thousands_separator: str,
    ) -> User:
        """Update the user's preferred numeric separators."""
        normalized_decimal = decimal_separator.strip()
        normalized_thousands = thousands_separator.strip()
        allowed_separators = {".", ",", " "}

        if normalized_decimal not in allowed_separators:
            raise ValueError("Separador decimal invalido.")
        if normalized_thousands not in allowed_separators:
            raise ValueError("Separador de milhar invalido.")
        if normalized_decimal == normalized_thousands:
            raise ValueError("Os separadores decimal e de milhar devem ser diferentes.")

        user.decimal_separator = normalized_decimal
        user.thousands_separator = normalized_thousands
        user.updated_at = datetime.now()
        user.last_seen_at = datetime.now()
        await session.commit()
        await session.refresh(user)
        return user

    async def update_limits(
        self,
        session: AsyncSession,
        user: User,
        *,
        limits_enabled: bool,
        daily_text_limit: int,
        daily_media_limit: int,
        daily_ai_limit: int,
    ) -> User:
        """Update all user daily limits in one operation."""
        for value in (daily_text_limit, daily_media_limit, daily_ai_limit):
            if value < 0:
                raise ValueError("Os limites nao podem ser negativos.")

        user.limits_enabled = limits_enabled
        user.daily_text_limit = daily_text_limit
        user.daily_media_limit = daily_media_limit
        user.daily_ai_limit = daily_ai_limit
        user.updated_at = datetime.now()
        user.last_seen_at = datetime.now()
        await session.commit()
        await session.refresh(user)
        return user

    async def reject_terms(self, session: AsyncSession, user: User) -> User:
        """Record a terms rejection and keep account inactive."""
        user.accepted_terms = False
        user.is_active = False
        user.last_seen_at = datetime.now()
        await session.commit()
        await session.refresh(user)
        return user

    def build_terms_message(self) -> str:
        """Build the onboarding terms message."""
        return (
            "Ola! Antes de usar o FinBot, preciso do seu aceite nos termos.\n\n"
            "1. Este servico roda em ambiente self-hosted.\n"
            "2. Seus dados ficam na infraestrutura administrada pelo operador desta instancia.\n"
            "3. A guarda, seguranca, backup, disponibilidade e eventuais incidentes "
            "dependem desse ambiente self-hosted.\n"
            "4. Nao existe custodia externa centralizada dos seus dados alem das integracoes "
            "configuradas nesta instancia.\n\n"
            "Responda *sim* para aceitar os termos e continuar ou *nao* para recusar."
        )

    def is_terms_acceptance(self, text: str) -> bool:
        """Check whether the user accepted the terms."""
        normalized = self._normalize_text(text)
        return normalized in {"sim", "s", "aceito", "concordo", "ok", "aceito os termos"}

    def is_terms_rejection(self, text: str) -> bool:
        """Check whether the user rejected the terms."""
        normalized = self._normalize_text(text)
        return normalized in {"nao", "não", "n", "recuso", "nao aceito", "não aceito"}

    def parse_limit_command(self, text: str) -> dict[str, Any] | None:
        """Parse direct commands for showing or updating limits without using AI."""
        normalized = self._normalize_text(text)

        if any(
            phrase in normalized
            for phrase in ("meus limites", "mostrar limites", "mostra meus limites", "ver limites")
        ):
            return {"action": "show"}

        match = re.search(
            r"(ajustar|alterar|mudar|definir)\s+limite\s+de\s+([a-z]+)\s+(?:para\s+)?(\d+)",
            normalized,
        )
        if not match:
            return None

        limit_key = LIMIT_FIELD_MAP.get(match.group(2))
        if not limit_key:
            return None

        return {
            "action": "set",
            "limit_type": limit_key,
            "limit_value": int(match.group(3)),
        }

    async def update_user_limit(
        self,
        session: AsyncSession,
        user: User,
        limit_field: str,
        limit_value: int,
    ) -> User:
        """Update one of the user's daily limits."""
        if limit_field not in {"daily_text_limit", "daily_media_limit", "daily_ai_limit"}:
            raise ValueError("Campo de limite invalido.")
        if limit_value < 0:
            raise ValueError("O limite nao pode ser negativo.")

        setattr(user, limit_field, limit_value)
        user.updated_at = datetime.now()
        user.last_seen_at = datetime.now()
        await session.commit()
        await session.refresh(user)
        return user

    def format_user_limits(
        self,
        user: User,
        usage: dict[str, dict[str, int]] | None = None,
    ) -> str:
        """Format a user-facing limits summary."""
        usage = usage or {}
        lines = ["Seus limites diarios atuais:"]
        label_map = {
            "daily_text_limit": "Mensagens de texto",
            "daily_media_limit": "Midias/documentos",
            "daily_ai_limit": "Chamadas de IA",
        }
        for field, label in label_map.items():
            limit_value = getattr(user, field)
            used_value = usage.get(field, {}).get("used", 0)
            remaining_value = max(limit_value - used_value, 0)
            lines.append(
                f"- {label}: {used_value}/{limit_value} usados ({remaining_value} restantes)"
            )

        lines.append(
            "\nPara ajustar: 'ajustar limite de ia para 30 por dia' ou "
            "'ajustar limite de midia para 10 por dia'."
        )
        return "\n".join(lines)

    def format_updated_limit_message(self, user: User, limit_field: str) -> str:
        """Format a confirmation message after updating a limit."""
        label_map = {
            "daily_text_limit": "texto",
            "daily_media_limit": "midia",
            "daily_ai_limit": "ia",
        }
        label = label_map.get(limit_field, limit_field)
        value = getattr(user, limit_field)
        return f"Limite diario de {label} atualizado para {value}."

    def _normalize_text(self, text: str) -> str:
        """Normalize text for direct command matching."""
        normalized = unicodedata.normalize("NFD", text.lower().strip())
        normalized = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized

    def _generate_backup_owner_id(self) -> str:
        """Generate a stable identifier to carry backup ownership across phone changes."""
        return uuid4().hex
