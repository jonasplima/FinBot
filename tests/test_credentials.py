"""Tests for user-scoped provider credential resolution."""

from decimal import Decimal

from app.database.models import UserProviderCredential
from app.services.credentials import CredentialService
from app.services.currency import CurrencyService
from app.services.security import SecurityService


class TestCredentialService:
    """Tests for provider credential resolution with instance fallback."""

    async def test_user_credential_overrides_instance(self, seeded_session, accepted_user_in_db):
        """A user-scoped credential should take precedence over global settings."""
        security = SecurityService()
        seeded_session.add(
            UserProviderCredential(
                user_id=accepted_user_in_db.id,
                provider="groq",
                api_key_encrypted=security.encrypt_api_key("user-groq-key-9999"),
                api_key_last4="9999",
                is_active=True,
            )
        )
        await seeded_session.commit()

        service = CredentialService()
        result = await service.resolve_api_key(
            "groq", user=accepted_user_in_db, session=seeded_session
        )

        assert result == "user-groq-key-9999"

    async def test_instance_credential_is_used_as_fallback(
        self, seeded_session, accepted_user_in_db
    ):
        """When the user has no custom key, the instance key should be used."""
        service = CredentialService()

        result = await service.resolve_api_key(
            "gemini", user=accepted_user_in_db, session=seeded_session
        )

        assert result == service.settings.gemini_api_key


class TestCurrencyCredentialIntegration:
    """Tests for currency service using user-scoped provider keys."""

    async def test_currency_service_uses_user_wise_key(
        self, seeded_session, accepted_user_in_db, monkeypatch
    ):
        """Currency conversion should use the user key before the instance fallback."""
        service = CurrencyService()

        async def fake_resolve_many(
            providers: list[str], user=None, session=None
        ) -> dict[str, str]:
            assert user == accepted_user_in_db
            assert providers == ["wise", "exchange_rate"]
            return {"wise": "wise-user-key-1234", "exchange_rate": ""}

        async def fake_wise_rate(from_currency: str, api_key: str) -> dict:
            assert from_currency == "USD"
            assert api_key == "wise-user-key-1234"
            return {"rate": Decimal("5.20"), "source": "wise"}

        async def fake_exchange_rate_api(from_currency: str, api_key: str) -> dict | None:
            raise AssertionError("ExchangeRate fallback should not be needed in this scenario")

        monkeypatch.setattr(service.credential_service, "resolve_many", fake_resolve_many)
        monkeypatch.setattr(service, "_get_wise_rate", fake_wise_rate)
        monkeypatch.setattr(service, "_get_exchange_rate_api_rate", fake_exchange_rate_api)

        result = await service.get_exchange_rate("USD", user=accepted_user_in_db)

        assert result["success"] is True
        assert result["rate"] == Decimal("5.20")
