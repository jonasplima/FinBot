"""Tests for web authentication endpoints and service."""

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import delete
from starlette.requests import Request

from app.database.connection import async_session, init_db
from app.database.models import User, UserOnboardingState, UserWebSession
from app.main import (
    LoginRequest,
    RegisterRequest,
    auth_login,
    auth_logout,
    auth_me,
    auth_register,
)


class TestAuthEndpoints:
    """Tests for registration, login and session handling."""

    @pytest.fixture(autouse=True)
    async def _setup_auth_tables(self):
        """Initialize and clean the real app database used by auth endpoints."""
        await init_db()
        async with async_session() as session:
            await session.execute(delete(UserWebSession))
            await session.execute(delete(UserOnboardingState))
            await session.execute(delete(User))
            await session.commit()

    @staticmethod
    def _build_request_with_cookie(cookie_value: str | None = None) -> Request:
        """Create a minimal request carrying the auth cookie when needed."""
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
            "path": "/auth/me",
            "raw_path": b"/auth/me",
            "query_string": b"",
            "headers": headers,
            "client": ("testclient", 123),
            "server": ("testserver", 80),
        }
        return Request(scope, receive)

    async def test_register_sets_cookie_and_returns_user(self):
        """Registration should create a session cookie and serialize the user."""
        payload = RegisterRequest(
            name="Jonas",
            email="jonas@example.com",
            password="senha-super-segura",
            phone="5511999999999",
        )
        response = Response()

        result = await auth_register(payload, response)

        assert result["status"] == "ok"
        assert result["user"]["email"] == "jonas@example.com"
        assert "finbot_session=" in response.headers.get("set-cookie", "")

    async def test_register_rejects_duplicate_email(self):
        """A second registration with the same configured account should fail."""
        payload = RegisterRequest(
            name="Jonas",
            email="jonas-duplicate@example.com",
            password="senha-super-segura",
            phone="5511888888888",
        )
        response = Response()

        await auth_register(payload, response)

        with HTTPExceptionContext() as ctx:
            await auth_register(payload, Response())

        assert ctx.exception.status_code == 400

    async def test_login_and_me_flow(self):
        """Logging in should return a cookie usable by /auth/me."""
        register_payload = RegisterRequest(
            name="Maria",
            email="maria@example.com",
            password="senha-super-segura",
            phone="5511977777777",
        )
        await auth_register(register_payload, Response())

        login_response = Response()
        login_result = await auth_login(
            LoginRequest(email="maria@example.com", password="senha-super-segura"),
            login_response,
        )

        set_cookie_header = login_response.headers.get("set-cookie", "")
        session_cookie = set_cookie_header.split("finbot_session=", maxsplit=1)[1].split(
            ";", maxsplit=1
        )[0]

        me_result = await auth_me(self._build_request_with_cookie(session_cookie))

        assert login_result["status"] == "ok"
        assert me_result["user"]["email"] == "maria@example.com"

    async def test_invalid_login_is_rejected(self):
        """Login should reject wrong credentials."""
        with HTTPExceptionContext() as ctx:
            await auth_login(
                LoginRequest(email="naoexiste@example.com", password="senha-errada"),
                Response(),
            )

        assert ctx.exception.status_code == 401

    async def test_logout_revokes_session(self):
        """Logout should revoke the current session and clear the cookie."""
        payload = RegisterRequest(
            name="Ana",
            email="ana@example.com",
            password="senha-super-segura",
            phone="5511966666666",
        )
        register_response = Response()
        await auth_register(payload, register_response)
        session_cookie = (
            register_response.headers.get("set-cookie", "")
            .split("finbot_session=", maxsplit=1)[1]
            .split(";", maxsplit=1)[0]
        )

        logout_response = Response()
        await auth_logout(self._build_request_with_cookie(session_cookie), logout_response)

        assert "finbot_session=" in logout_response.headers.get("set-cookie", "")
        with HTTPExceptionContext() as ctx:
            await auth_me(self._build_request_with_cookie(session_cookie))
        assert ctx.exception.status_code == 401


class HTTPExceptionContext:
    """Minimal async-friendly context manager for asserting HTTPException."""

    def __enter__(self):
        self.exception = None
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is None:
            raise AssertionError("Expected HTTPException to be raised.")
        if not isinstance(exc, HTTPException):
            return False
        self.exception = exc
        return True
