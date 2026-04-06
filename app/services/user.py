"""User management service for onboarding and preferences."""

import logging
import re
import unicodedata
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import User
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

        result = await session.execute(select(User).where(User.phone == normalized_phone))
        user = result.scalar_one_or_none()

        if user:
            user.last_seen_at = datetime.now()
            await session.commit()
            return user

        defaults = self.settings.user_limit_defaults()
        user = User(
            phone=normalized_phone,
            accepted_terms=False,
            terms_version=None,
            is_active=True,
            preferred_channel="whatsapp",
            timezone="America/Sao_Paulo",
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
