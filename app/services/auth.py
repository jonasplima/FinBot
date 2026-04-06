"""Authentication service for the web onboarding and future dashboard."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User, UserOnboardingState, UserWebSession
from app.services.security import SecurityService
from app.services.user import UserService
from app.utils.validators import normalize_phone


class AuthService:
    """Handle account registration, login and browser sessions."""

    def __init__(self) -> None:
        self.security = SecurityService()
        self.user_service = UserService()

    async def register_user(
        self,
        session: AsyncSession,
        *,
        name: str,
        email: str,
        password: str,
        phone: str,
    ) -> tuple[User, str]:
        """Create or attach web access credentials to a user and open a session."""
        normalized_email = self._normalize_email(email)
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Nome e obrigatorio.")

        normalized_phone = normalize_phone(phone)

        existing_by_email = await session.execute(
            select(User).where(User.email == normalized_email)
        )
        user_by_email = existing_by_email.scalar_one_or_none()

        existing_by_phone = await session.execute(
            select(User).where(User.phone == normalized_phone)
        )
        user_by_phone = existing_by_phone.scalar_one_or_none()

        if user_by_email and user_by_phone and user_by_email.id != user_by_phone.id:
            raise ValueError("Email e telefone ja pertencem a contas diferentes.")

        user = user_by_phone or user_by_email
        if user is None:
            user = await self.user_service.get_or_create_user(session, normalized_phone)

        if user.email and user.email != normalized_email:
            raise ValueError("Este telefone ja esta vinculado a outro email.")
        if user.password_hash:
            raise ValueError("Esta conta ja possui acesso web configurado.")

        user.name = normalized_name
        if not user.display_name:
            user.display_name = normalized_name
        user.email = normalized_email
        user.password_hash = self.security.hash_password(password)
        user.web_access_enabled = True
        user.last_login_at = datetime.now()
        user.last_seen_at = datetime.now()
        await session.commit()
        await session.refresh(user)

        await self._ensure_onboarding_state(session, user)
        token = await self.create_web_session(session, user)
        return user, token

    async def login_user(
        self,
        session: AsyncSession,
        *,
        email: str,
        password: str,
    ) -> tuple[User, str]:
        """Authenticate a user with email and password and open a session."""
        normalized_email = self._normalize_email(email)
        result = await session.execute(select(User).where(User.email == normalized_email))
        user = result.scalar_one_or_none()

        if user is None or not self.security.verify_password(password, user.password_hash):
            raise ValueError("Email ou senha invalidos.")

        user.last_login_at = datetime.now()
        user.last_seen_at = datetime.now()
        await session.commit()
        await session.refresh(user)

        token = await self.create_web_session(session, user)
        return user, token

    async def create_web_session(self, session: AsyncSession, user: User) -> str:
        """Create a persisted browser session for a user."""
        token = self.security.generate_web_session_token()
        token_hash = self.security.hash_web_session_token(token)

        web_session = UserWebSession(
            user_id=user.id,
            session_token_hash=token_hash,
            expires_at=self.security.web_session_expiry(),
            created_at=datetime.now(),
            last_seen_at=datetime.now(),
        )
        session.add(web_session)
        await session.commit()
        return token

    async def get_user_by_session_token(
        self,
        session: AsyncSession,
        session_token: str,
    ) -> User | None:
        """Resolve the current user from a valid browser session token."""
        token_hash = self.security.hash_web_session_token(session_token)
        now = datetime.now()

        result = await session.execute(
            select(UserWebSession, User)
            .join(User, User.id == UserWebSession.user_id)
            .where(UserWebSession.session_token_hash == token_hash)
        )
        row = result.first()
        if row is None:
            return None

        web_session, user = row
        if web_session.revoked_at is not None or web_session.expires_at <= now:
            return None

        web_session.last_seen_at = now
        user.last_seen_at = now
        await session.commit()
        return user

    async def logout_session(self, session: AsyncSession, session_token: str) -> bool:
        """Revoke a browser session token."""
        token_hash = self.security.hash_web_session_token(session_token)
        result = await session.execute(
            select(UserWebSession).where(UserWebSession.session_token_hash == token_hash)
        )
        web_session = result.scalar_one_or_none()
        if web_session is None or web_session.revoked_at is not None:
            return False

        web_session.revoked_at = datetime.now()
        await session.commit()
        return True

    async def _ensure_onboarding_state(self, session: AsyncSession, user: User) -> None:
        """Ensure a user has an onboarding progress record."""
        result = await session.execute(
            select(UserOnboardingState).where(UserOnboardingState.user_id == user.id)
        )
        onboarding_state = result.scalar_one_or_none()
        if onboarding_state is not None:
            return

        session.add(UserOnboardingState(user_id=user.id, current_step="terms"))
        await session.commit()

    def build_session_cookie_settings(self) -> dict[str, Any]:
        """Return standard settings for the FinBot web session cookie."""
        return {
            "key": "finbot_session",
            "httponly": True,
            "samesite": "lax",
            "secure": False,
            "max_age": self.security.settings.effective_web_session_ttl_hours * 3600,
            "path": "/",
        }

    def serialize_user(self, user: User) -> dict[str, Any]:
        """Return a safe user payload for auth responses."""
        return {
            "id": user.id,
            "phone": user.phone,
            "name": user.name,
            "display_name": user.display_name,
            "email": user.email,
            "accepted_terms": user.accepted_terms,
            "terms_version": user.terms_version,
            "web_access_enabled": user.web_access_enabled,
            "onboarding_completed": user.onboarding_completed,
            "timezone": user.timezone,
        }

    def _normalize_email(self, email: str) -> str:
        """Normalize and validate an email address minimally."""
        normalized = email.strip().lower()
        if not normalized or "@" not in normalized or normalized.startswith("@"):
            raise ValueError("Email invalido.")
        local_part, _, domain = normalized.partition("@")
        if not local_part or "." not in domain:
            raise ValueError("Email invalido.")
        return normalized
