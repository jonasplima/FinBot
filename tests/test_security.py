"""Tests for security primitives used by onboarding and web auth."""

from datetime import datetime

import pytest

from app.database.models import (
    UserCategory,
    UserOnboardingState,
    UserProviderCredential,
    UserWebSession,
    UserWhatsAppSession,
)
from app.services.security import SecurityService
from app.services.user import UserService


class TestSecurityService:
    """Tests for password, session and secret helpers."""

    def test_hash_and_verify_password(self):
        """Passwords should verify after hashing."""
        service = SecurityService()

        password_hash = service.hash_password("senha-super-segura")

        assert password_hash.startswith("scrypt$")
        assert service.verify_password("senha-super-segura", password_hash) is True
        assert service.verify_password("senha-errada", password_hash) is False

    def test_short_password_is_rejected(self):
        """Weak passwords should be rejected before hashing."""
        service = SecurityService()

        with pytest.raises(ValueError):
            service.hash_password("1234567")

    def test_encrypt_and_decrypt_api_key(self):
        """Provider API keys should round-trip through encryption."""
        service = SecurityService()

        encrypted = service.encrypt_api_key("sk-secret-123456")

        assert encrypted != "sk-secret-123456"
        assert service.decrypt_api_key(encrypted) == "sk-secret-123456"
        assert service.key_last4("sk-secret-123456") == "3456"
        assert service.mask_api_key("sk-secret-123456").endswith("3456")

    def test_web_session_helpers(self):
        """Session tokens should be generated, hashed and expired predictably."""
        service = SecurityService()

        token = service.generate_web_session_token()
        token_hash = service.hash_web_session_token(token)
        expiry = service.web_session_expiry(datetime(2026, 1, 1, 12, 0, 0))

        assert token
        assert len(token_hash) == 64
        assert expiry > datetime(2026, 1, 1, 12, 0, 0)


class TestPhaseOneModels:
    """Tests for the new persistence models introduced in phase 1."""

    async def test_new_user_fields_are_available(self, seeded_session, test_phone):
        """User records should support onboarding and web auth fields."""
        user = await UserService().get_or_create_user(seeded_session, test_phone)

        assert user.password_hash is None
        assert user.onboarding_completed is False
        assert user.last_login_at is None

    async def test_can_persist_provider_credential_and_sessions(
        self, seeded_session, accepted_user_in_db
    ):
        """Credential, onboarding and session models should persist correctly."""
        security = SecurityService()

        credential = UserProviderCredential(
            user_id=accepted_user_in_db.id,
            provider="groq",
            api_key_encrypted=security.encrypt_api_key("groq-api-key-1234"),
            api_key_last4=security.key_last4("groq-api-key-1234"),
            is_active=True,
        )
        onboarding = UserOnboardingState(
            user_id=accepted_user_in_db.id,
            current_step="ai_keys",
            is_completed=False,
        )
        web_session = UserWebSession(
            user_id=accepted_user_in_db.id,
            session_token_hash=security.hash_web_session_token("session-token-123"),
            expires_at=security.web_session_expiry(),
        )
        whatsapp_session = UserWhatsAppSession(
            user_id=accepted_user_in_db.id,
            evolution_instance="FinBot",
            session_key="wa-session-123",
            connection_status="pending",
        )
        user_category = UserCategory(
            user_id=accepted_user_in_db.id,
            name="Pets",
            type="Negativo",
            is_active=True,
            is_system_default=False,
        )

        seeded_session.add(credential)
        seeded_session.add(onboarding)
        seeded_session.add(web_session)
        seeded_session.add(whatsapp_session)
        seeded_session.add(user_category)
        await seeded_session.commit()

        assert credential.id is not None
        assert onboarding.id is not None
        assert web_session.id is not None
        assert whatsapp_session.id is not None
        assert user_category.id is not None
