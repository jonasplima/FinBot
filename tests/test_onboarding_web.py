from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, Response
from sqlalchemy import delete
from starlette.requests import Request

from app.config import get_settings
from app.database.connection import async_session, init_db
from app.database.models import User, UserOnboardingState, UserWebSession, UserWhatsAppSession
from app.database.seed import seed_all
from app.main import (
    RegisterRequest,
    auth_register,
    onboarding_accept_terms,
    onboarding_categories,
    onboarding_category_visibility,
    onboarding_complete,
    onboarding_create_category,
    onboarding_credentials,
    onboarding_credentials_upsert,
    onboarding_profile,
    onboarding_state,
    onboarding_step,
    onboarding_whatsapp_prepare,
    onboarding_whatsapp_qrcode,
    onboarding_whatsapp_refresh,
    onboarding_whatsapp_status,
    web_login_page,
    web_onboarding_page,
)


class TestOnboardingWeb:
    """Tests for the first web onboarding shell and state endpoints."""

    EVOLUTION_PREFIX = get_settings().evolution_instance

    async def _reset_real_db(self) -> None:
        await init_db()
        async with async_session() as session:
            await seed_all(session)
            await session.execute(delete(UserWebSession))
            await session.execute(delete(UserOnboardingState))
            await session.execute(delete(UserWhatsAppSession))
            await session.execute(delete(User))
            await session.commit()

    @staticmethod
    def _request_with_cookie(path: str, cookie_value: str | None = None) -> Request:
        headers = []
        if cookie_value is not None:
            headers.append((b"cookie", f"finbot_session={cookie_value}".encode("latin-1")))

        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": b"",
            "headers": headers,
            "client": ("testclient", 123),
            "server": ("testserver", 80),
        }
        return Request(scope, receive)

    @staticmethod
    def _extract_cookie(response: Response) -> str:
        return (
            response.headers.get("set-cookie", "")
            .split("finbot_session=", maxsplit=1)[1]
            .split(";", maxsplit=1)[0]
        )

    async def test_web_login_page_renders(self):
        """The login/register entry page should return HTML content."""
        response = await web_login_page()

        assert "Criar acesso" in response.body.decode("utf-8")
        assert "FinBot Web" in response.body.decode("utf-8")

    async def test_web_onboarding_redirects_when_unauthenticated(self):
        """The onboarding shell should redirect unauthenticated browsers."""
        response = await web_onboarding_page(self._request_with_cookie("/web/onboarding"))

        assert response.status_code == 303
        assert response.headers["location"] == "/web/login"

    async def test_onboarding_state_terms_profile_and_complete_flow(self):
        """Authenticated users should be able to move through the onboarding state."""
        await self._reset_real_db()
        register_response = Response()
        await auth_register(
            RegisterRequest(
                name="Clara",
                email="clara@example.com",
                password="senha-super-segura",
                phone="5511944444444",
            ),
            register_response,
        )
        session_cookie = self._extract_cookie(register_response)
        request = self._request_with_cookie("/onboarding/state", session_cookie)

        state_payload = await onboarding_state(request)
        assert state_payload["onboarding"]["current_step"] == "terms"
        assert state_payload["user"]["accepted_terms"] is False

        accepted_payload = await onboarding_accept_terms(request)
        assert accepted_payload["user"]["accepted_terms"] is True
        assert accepted_payload["onboarding"]["current_step"] == "api_keys"

        credential_payload = await onboarding_credentials(request)
        assert "gemini" in credential_payload["credentials"]

        upsert_payload = await onboarding_credentials_upsert(
            request,
            payload=type(
                "CredentialPayload",
                (),
                {"provider": "groq", "api_key": "user-groq-key-1234"},
            )(),
        )
        assert upsert_payload["credential"]["provider"] == "groq"

        review_after_credential = await onboarding_state(request)
        assert review_after_credential["review"]["configured_providers"] == ["Groq"]

        profile_payload = await onboarding_profile(
            request,
            payload=type(
                "ProfilePayload",
                (),
                {"display_name": "Clara", "timezone": "UTC", "name": None},
            )(),
        )
        assert profile_payload["user"]["name"] == "Clara"
        assert profile_payload["user"]["display_name"] == "Clara"
        assert profile_payload["user"]["timezone"] == "UTC"
        assert profile_payload["onboarding"]["current_step"] == "categories"

        stepped_payload = await onboarding_step(
            request,
            payload=type("StepPayload", (), {"current_step": "review"})(),
        )
        assert stepped_payload["onboarding"]["current_step"] == "review"

        completed_payload = await onboarding_complete(request)
        assert completed_payload["user"]["onboarding_completed"] is True
        assert completed_payload["onboarding"]["is_completed"] is True
        assert completed_payload["onboarding"]["current_step"] == "completed"

    async def test_onboarding_step_rejects_invalid_values(self):
        """Unsupported onboarding steps should return a validation HTTP error."""
        await self._reset_real_db()
        register_response = Response()
        await auth_register(
            RegisterRequest(
                name="Leo",
                email="leo@example.com",
                password="senha-super-segura",
                phone="5511933333333",
            ),
            register_response,
        )
        session_cookie = self._extract_cookie(register_response)
        request = self._request_with_cookie("/onboarding/step", session_cookie)

        try:
            await onboarding_step(
                request,
                payload=type("StepPayload", (), {"current_step": "nao-existe"})(),
            )
        except HTTPException as exc:
            assert exc.status_code == 400
        else:
            raise AssertionError("Expected HTTPException for invalid onboarding step")

    async def test_whatsapp_prepare_creates_user_session(self):
        """Preparing WhatsApp onboarding should create a dedicated session per user."""
        await self._reset_real_db()
        register_response = Response()
        await auth_register(
            RegisterRequest(
                name="Nina",
                email="nina@example.com",
                password="senha-super-segura",
                phone="5511922222222",
            ),
            register_response,
        )
        session_cookie = self._extract_cookie(register_response)
        request = self._request_with_cookie("/onboarding/whatsapp/prepare", session_cookie)

        await onboarding_accept_terms(
            self._request_with_cookie("/onboarding/terms/accept", session_cookie)
        )
        payload = await onboarding_whatsapp_prepare(request)

        assert payload["session"]["session_key"].startswith("user-")
        assert payload["session"]["evolution_instance"].startswith(f"{self.EVOLUTION_PREFIX}-user-")
        assert payload["session"]["connection_status"] == "pending"
        assert payload["onboarding_step"] == "whatsapp_prepare"

    async def test_whatsapp_qrcode_uses_user_scoped_instance(self):
        """Generating QR should use the dedicated Evolution instance for that user."""
        await self._reset_real_db()
        register_response = Response()
        await auth_register(
            RegisterRequest(
                name="Lia",
                email="lia@example.com",
                password="senha-super-segura",
                phone="5511911111111",
            ),
            register_response,
        )
        session_cookie = self._extract_cookie(register_response)
        request = self._request_with_cookie("/onboarding/whatsapp/qrcode", session_cookie)

        await onboarding_accept_terms(
            self._request_with_cookie("/onboarding/terms/accept", session_cookie)
        )
        with patch("app.services.whatsapp_onboarding.EvolutionService") as mock_evolution_cls:
            mock_evolution = mock_evolution_cls.return_value
            mock_evolution.get_qrcode = AsyncMock(
                return_value={
                    "status": "waiting_qrcode",
                    "qrcode": "data:image/png;base64,abc123",
                    "message": "Scan the QR code with WhatsApp",
                }
            )

            payload = await onboarding_whatsapp_qrcode(request)

        assert payload["session"]["connection_status"] == "pending"
        assert payload["qrcode"] == "data:image/png;base64,abc123"
        target_instance = mock_evolution.get_qrcode.await_args.args[0]
        assert target_instance.startswith(f"{self.EVOLUTION_PREFIX}-user-")

    async def test_whatsapp_refresh_marks_onboarding_connection(self):
        """Refreshing the status should mark the WhatsApp step as connected when open."""
        await self._reset_real_db()
        register_response = Response()
        await auth_register(
            RegisterRequest(
                name="Maya",
                email="maya@example.com",
                password="senha-super-segura",
                phone="5511900000000",
            ),
            register_response,
        )
        session_cookie = self._extract_cookie(register_response)
        request = self._request_with_cookie("/onboarding/whatsapp/refresh", session_cookie)

        await onboarding_accept_terms(
            self._request_with_cookie("/onboarding/terms/accept", session_cookie)
        )
        await onboarding_whatsapp_prepare(request)

        with patch("app.services.whatsapp_onboarding.EvolutionService") as mock_evolution_cls:
            mock_evolution = mock_evolution_cls.return_value
            mock_evolution.get_connection_state = AsyncMock(
                return_value={"instance": {"state": "open"}}
            )

            payload = await onboarding_whatsapp_refresh(request)

        assert payload["session"]["connection_status"] == "connected"

        state_payload = await onboarding_state(
            self._request_with_cookie("/onboarding/state", session_cookie)
        )
        assert state_payload["onboarding"]["whatsapp_connected_at"] is not None

    async def test_whatsapp_status_returns_existing_session(self):
        """The onboarding screen should be able to poll current WhatsApp session metadata."""
        await self._reset_real_db()
        register_response = Response()
        await auth_register(
            RegisterRequest(
                name="Bia",
                email="bia@example.com",
                password="senha-super-segura",
                phone="5511977777777",
            ),
            register_response,
        )
        session_cookie = self._extract_cookie(register_response)
        request = self._request_with_cookie("/onboarding/whatsapp/status", session_cookie)

        await onboarding_accept_terms(
            self._request_with_cookie("/onboarding/terms/accept", session_cookie)
        )
        await onboarding_whatsapp_prepare(request)

        with patch("app.services.whatsapp_onboarding.EvolutionService") as mock_evolution_cls:
            mock_evolution = mock_evolution_cls.return_value
            mock_evolution.get_connection_state = AsyncMock(
                return_value={"instance": {"state": "connecting"}}
            )

            payload = await onboarding_whatsapp_status(request)

        assert payload["session"]["connection_status"] == "pending"
        assert payload["session"]["evolution_instance"].startswith(f"{self.EVOLUTION_PREFIX}-user-")

    async def test_onboarding_categories_can_create_and_hide(self):
        """Authenticated users should manage category customization from onboarding."""
        await self._reset_real_db()
        register_response = Response()
        await auth_register(
            RegisterRequest(
                name="Tami",
                email="tami@example.com",
                password="senha-super-segura",
                phone="5511966666666",
            ),
            register_response,
        )
        session_cookie = self._extract_cookie(register_response)
        request = self._request_with_cookie("/onboarding/categories", session_cookie)

        initial_payload = await onboarding_categories(request)
        assert any(item["name"] == "Lazer" for item in initial_payload["active"])

        create_payload = await onboarding_create_category(
            request,
            payload=type("CategoryPayload", (), {"name": "Pets", "type": "Negativo"})(),
        )
        assert create_payload["category"]["name"] == "Pets"
        assert create_payload["category"]["is_custom"] is True

        visibility_payload = await onboarding_category_visibility(
            request,
            payload=type(
                "VisibilityPayload",
                (),
                {"category_name": "Lazer", "is_active": False},
            )(),
        )
        assert visibility_payload["category"]["name"] == "Lazer"
        assert visibility_payload["category"]["is_active"] is False

        updated_payload = await onboarding_categories(request)
        assert any(item["name"] == "Pets" for item in updated_payload["custom"])
        assert any(item["name"] == "Lazer" for item in updated_payload["inactive"])

        review_payload = await onboarding_state(
            self._request_with_cookie("/onboarding/state", session_cookie)
        )
        assert review_payload["review"]["custom_categories"] == ["Pets"]
        assert review_payload["review"]["hidden_categories"] == ["Lazer"]

    async def test_whatsapp_endpoints_require_terms_acceptance(self):
        """WhatsApp onboarding must stay blocked until terms are accepted."""
        await self._reset_real_db()
        register_response = Response()
        await auth_register(
            RegisterRequest(
                name="Duda",
                email="duda@example.com",
                password="senha-super-segura",
                phone="5511955555555",
            ),
            register_response,
        )
        session_cookie = self._extract_cookie(register_response)
        request = self._request_with_cookie("/onboarding/whatsapp/prepare", session_cookie)

        try:
            await onboarding_whatsapp_prepare(request)
        except HTTPException as exc:
            assert exc.status_code == 400
        else:
            raise AssertionError("Expected HTTPException when terms are not accepted")

    async def test_credentials_require_terms_acceptance(self):
        """Credential setup should also stay blocked until the terms are accepted."""
        await self._reset_real_db()
        register_response = Response()
        await auth_register(
            RegisterRequest(
                name="Mila",
                email="mila@example.com",
                password="senha-super-segura",
                phone="5511944447777",
            ),
            register_response,
        )
        session_cookie = self._extract_cookie(register_response)
        request = self._request_with_cookie("/onboarding/credentials", session_cookie)

        try:
            await onboarding_credentials(request)
        except HTTPException as exc:
            assert exc.status_code == 400
        else:
            raise AssertionError("Expected HTTPException when terms are not accepted")
