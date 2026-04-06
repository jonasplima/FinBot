import json

from fastapi import HTTPException, Response
from sqlalchemy import delete
from starlette.requests import Request

from app.database.connection import async_session, init_db
from app.database.models import BackupRestoreAudit, User, UserOnboardingState, UserWebSession
from app.database.seed import seed_all
from app.main import (
    RegisterRequest,
    SettingsAuthorizedPhoneRequest,
    SettingsBackupApplyRequest,
    SettingsBackupImportRequest,
    SettingsLimitsRequest,
    SettingsNotificationsRequest,
    SettingsProfileRequest,
    auth_register,
    settings_add_authorized_phone,
    settings_backup_export,
    settings_backup_import_apply,
    settings_backup_import_preview,
    settings_limits,
    settings_notifications,
    settings_profile,
    settings_remove_authorized_phone,
    settings_state,
    web_settings_page,
)


class TestSettingsWeb:
    """Tests for the post-onboarding settings panel and endpoints."""

    async def _reset_real_db(self) -> None:
        await init_db()
        async with async_session() as session:
            await seed_all(session)
            await session.execute(delete(BackupRestoreAudit))
            await session.execute(delete(UserWebSession))
            await session.execute(delete(UserOnboardingState))
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

    async def _register_and_get_request(self, *, email: str, phone: str) -> Request:
        register_response = Response()
        await auth_register(
            RegisterRequest(
                name="Helena",
                email=email,
                password="senha-super-segura",
                phone=phone,
            ),
            register_response,
        )
        session_cookie = self._extract_cookie(register_response)
        return self._request_with_cookie("/settings/state", session_cookie)

    async def test_web_settings_redirects_when_unauthenticated(self):
        """The settings panel should redirect unauthenticated browsers."""
        response = await web_settings_page(self._request_with_cookie("/web/settings"))

        assert response.status_code == 303
        assert response.headers["location"] == "/web/login"

    async def test_settings_state_profile_notifications_and_limits(self):
        """Authenticated users should manage profile, notifications and limits."""
        await self._reset_real_db()
        request = await self._register_and_get_request(
            email="helena@example.com",
            phone="5511910101010",
        )

        initial_payload = await settings_state(request)
        assert initial_payload["user"]["phone"] == "5511910101010"
        assert initial_payload["notifications"]["budget_alerts"] is True
        assert initial_payload["user"]["decimal_separator"] == ","
        assert initial_payload["user"]["thousands_separator"] == "."

        profile_payload = await settings_profile(
            request,
            SettingsProfileRequest(
                name="Helena Costa",
                display_name="Lena",
                timezone="UTC",
                email="helena.costa@example.com",
                base_currency="USD",
                decimal_separator=".",
                thousands_separator=",",
            ),
        )
        assert profile_payload["user"]["name"] == "Helena Costa"
        assert profile_payload["user"]["display_name"] == "Lena"
        assert profile_payload["user"]["email"] == "helena.costa@example.com"
        assert profile_payload["user"]["timezone"] == "UTC"
        assert profile_payload["user"]["base_currency"] == "USD"
        assert profile_payload["user"]["decimal_separator"] == "."
        assert profile_payload["user"]["thousands_separator"] == ","

        notifications_payload = await settings_notifications(
            request,
            SettingsNotificationsRequest(
                budget_alerts=False,
                recurring_reminders=True,
                goal_updates=False,
            ),
        )
        assert notifications_payload["notifications"]["budget_alerts"] is False
        assert notifications_payload["notifications"]["goal_updates"] is False

        limits_payload = await settings_limits(
            request,
            SettingsLimitsRequest(
                limits_enabled=True,
                daily_text_limit=12,
                daily_media_limit=4,
                daily_ai_limit=7,
            ),
        )
        assert limits_payload["limits"]["daily_text_limit"] == 12
        assert limits_payload["limits"]["daily_media_limit"] == 4
        assert limits_payload["limits"]["daily_ai_limit"] == 7

    async def test_settings_manage_authorized_phones(self):
        """Authenticated users should add and remove additional authorized numbers."""
        await self._reset_real_db()
        request = await self._register_and_get_request(
            email="phones@example.com",
            phone="5511910505050",
        )

        initial_payload = await settings_state(request)
        assert initial_payload["authorized_phones"] == [
            {"phone": "5511910505050", "is_primary": True}
        ]

        updated_payload = await settings_add_authorized_phone(
            request,
            SettingsAuthorizedPhoneRequest(phone="5511910606060"),
        )
        assert any(
            item["phone"] == "5511910606060" and item["is_primary"] is False
            for item in updated_payload["authorized_phones"]
        )

        removed_payload = await settings_remove_authorized_phone(
            request,
            SettingsAuthorizedPhoneRequest(phone="5511910606060"),
        )
        assert removed_payload["authorized_phones"] == [
            {"phone": "5511910505050", "is_primary": True}
        ]

    async def test_settings_export_and_restore_backup_same_profile(self):
        """The settings panel should preview and restore a backup for the same profile."""
        await self._reset_real_db()
        request = await self._register_and_get_request(
            email="maria@example.com",
            phone="5511910202020",
        )

        export_payload = await settings_backup_export(request)
        assert export_payload["filename"].endswith(".json")
        assert '"metadata"' in export_payload["backup_json"]

        preview_payload = await settings_backup_import_preview(
            request,
            SettingsBackupImportRequest(backup_json=export_payload["backup_json"]),
        )
        assert preview_payload["requires_migration_confirmation"] is False

        apply_payload = await settings_backup_import_apply(
            request,
            SettingsBackupApplyRequest(
                backup_ref=preview_payload["backup_ref"],
                explicit_migration_confirmation=False,
            ),
        )
        assert apply_payload["status"] == "ok"
        assert apply_payload["restored"]["expenses"] == 0

    async def test_settings_restore_requires_explicit_migration_confirmation(self):
        """Backup imports from another logical profile must require explicit confirmation."""
        await self._reset_real_db()

        source_request = await self._register_and_get_request(
            email="origem@example.com", phone="5511910303030"
        )
        export_payload = await settings_backup_export(source_request)
        legacy_backup = json.loads(export_payload["backup_json"])
        legacy_backup["metadata"]["source_phone"] = "5511988887777"
        legacy_backup["metadata"]["source_backup_owner_id"] = "legacy-owner-external-001"
        legacy_backup_json = json.dumps(legacy_backup, ensure_ascii=True, indent=2)

        target_request = await self._register_and_get_request(
            email="destino@example.com",
            phone="5511910404040",
        )
        preview_payload = await settings_backup_import_preview(
            target_request,
            SettingsBackupImportRequest(backup_json=legacy_backup_json),
        )
        assert preview_payload["requires_migration_confirmation"] is True

        try:
            await settings_backup_import_apply(
                target_request,
                SettingsBackupApplyRequest(
                    backup_ref=preview_payload["backup_ref"],
                    explicit_migration_confirmation=False,
                ),
            )
        except HTTPException as exc:
            assert exc.status_code == 400
        else:
            raise AssertionError("Expected HTTPException when migration confirmation is missing")

        apply_payload = await settings_backup_import_apply(
            target_request,
            SettingsBackupApplyRequest(
                backup_ref=preview_payload["backup_ref"],
                explicit_migration_confirmation=True,
            ),
        )
        assert apply_payload["status"] == "ok"
        assert (
            apply_payload["settings"]["backup"]["backup_owner_id"]
            == preview_payload["summary"]["source_backup_owner_id"]
        )
