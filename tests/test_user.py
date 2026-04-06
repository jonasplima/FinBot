"""Tests for UserService."""

from app.services.user import UserService
from app.utils.validators import mask_phone, sanitize_for_log


class TestUserService:
    """Tests for user creation, onboarding and limits parsing."""

    async def test_get_or_create_user_creates_defaults(self, seeded_session, test_phone):
        """Test that a new user is created with default limits."""
        service = UserService()

        user = await service.get_or_create_user(seeded_session, test_phone)

        assert user.phone == test_phone
        assert user.accepted_terms is False
        assert user.daily_text_limit == 100
        assert user.daily_media_limit == 20
        assert user.daily_ai_limit == 50

    async def test_accept_terms_marks_current_version(
        self, seeded_session, test_phone, accepted_user_in_db
    ):
        """Test that accepting terms stores the current version."""
        service = UserService()
        accepted_user_in_db.accepted_terms = False
        accepted_user_in_db.terms_version = None
        await seeded_session.commit()

        user = await service.accept_terms(seeded_session, accepted_user_in_db)

        assert user.accepted_terms is True
        assert user.terms_version == service.settings.terms_version
        assert user.accepted_terms_at is not None

    def test_parse_limit_command_show(self):
        """Test parsing direct show limits commands."""
        service = UserService()

        result = service.parse_limit_command("Meus limites")

        assert result == {"action": "show"}

    def test_parse_limit_command_set(self):
        """Test parsing direct update limit commands."""
        service = UserService()

        result = service.parse_limit_command("ajustar limite de ia para 30 por dia")

        assert result is not None
        assert result["action"] == "set"
        assert result["limit_type"] == "daily_ai_limit"
        assert result["limit_value"] == 30


class TestLogSafetyHelpers:
    """Tests for helpers used to reduce sensitive logging."""

    def test_mask_phone(self):
        """Test phone masking for logs."""
        assert mask_phone("5511999999999") == "5511*****999"

    def test_sanitize_for_log(self):
        """Test log sanitization and truncation."""
        result = sanitize_for_log("mensagem muito longa com dados sensiveis", max_length=10)
        assert result.endswith("...")
