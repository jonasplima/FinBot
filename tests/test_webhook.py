"""Integration tests for webhook handler."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.database.models import PendingConfirmation
from app.handlers.webhook import WebhookHandler
from app.main import evolution_webhook, get_qrcode, get_status
from app.services.rate_limit import RateLimitService


class TestWebhookHandlerMessageExtraction:
    """Tests for webhook message extraction."""

    @pytest.fixture
    def handler(self):
        """Create a webhook handler with mocked services."""
        with (
            patch("app.handlers.webhook.EvolutionService") as MockEvolution,
            patch("app.handlers.webhook.GeminiService") as MockGemini,
        ):
            mock_evolution = MagicMock()
            mock_evolution.send_text = AsyncMock()
            mock_evolution.send_document = AsyncMock()
            mock_evolution.extract_message_data = MagicMock()
            MockEvolution.return_value = mock_evolution

            mock_gemini = MagicMock()
            mock_gemini.process_message = AsyncMock()
            mock_gemini.evaluate_confirmation_response = AsyncMock()
            MockGemini.return_value = mock_gemini

            handler = WebhookHandler()
            handler.evolution = mock_evolution
            handler.gemini = mock_gemini
            RateLimitService._fallback_counters.clear()

            yield handler


class TestWebhookHandlerPendingConfirmation:
    """Tests for pending confirmation handling."""

    @pytest.fixture
    def handler(self):
        """Create a webhook handler with mocked services."""
        with (
            patch("app.handlers.webhook.EvolutionService") as MockEvolution,
            patch("app.handlers.webhook.GeminiService") as MockGemini,
        ):
            mock_evolution = MagicMock()
            mock_evolution.send_text = AsyncMock()
            MockEvolution.return_value = mock_evolution

            mock_gemini = MagicMock()
            MockGemini.return_value = mock_gemini

            handler = WebhookHandler()
            handler.evolution = mock_evolution
            handler.gemini = mock_gemini

            yield handler

    async def test_get_pending_confirmation_exists(
        self, handler, seeded_session, pending_confirmation_in_db, test_phone
    ):
        """Test getting an existing pending confirmation."""
        result = await handler.get_pending_confirmation(seeded_session, test_phone)

        assert result is not None
        assert result.user_phone == test_phone

    async def test_get_pending_confirmation_not_exists(self, handler, seeded_session, test_phone):
        """Test getting pending confirmation when none exists."""
        result = await handler.get_pending_confirmation(seeded_session, test_phone)

        assert result is None

    async def test_get_pending_confirmation_expired(self, handler, seeded_session, test_phone):
        """Test that expired confirmations are not returned."""
        # Create expired pending confirmation
        pending = PendingConfirmation(
            user_phone=test_phone,
            data={"type": "expense", "data": {}},
            expires_at=datetime.now() - timedelta(minutes=1),
        )
        seeded_session.add(pending)
        await seeded_session.commit()

        result = await handler.get_pending_confirmation(seeded_session, test_phone)

        assert result is None

    async def test_save_pending_confirmation(self, handler, seeded_session, test_phone):
        """Test saving a pending confirmation."""
        data = {
            "type": "expense",
            "data": {"description": "Test", "amount": 50.00},
        }

        await handler.save_pending_confirmation(seeded_session, test_phone, data)

        # Verify it was saved
        result = await handler.get_pending_confirmation(seeded_session, test_phone)
        assert result is not None
        assert result.data["data"]["description"] == "Test"

    async def test_save_pending_confirmation_replaces_existing(
        self, handler, seeded_session, test_phone, pending_confirmation_in_db
    ):
        """Test that saving a new confirmation replaces the existing one."""
        new_data = {
            "type": "expense",
            "data": {"description": "New Test", "amount": 100.00},
        }

        await handler.save_pending_confirmation(seeded_session, test_phone, new_data)

        result = await handler.get_pending_confirmation(seeded_session, test_phone)
        assert result.data["data"]["description"] == "New Test"


class TestEvolutionWebhookAuthentication:
    """Tests for webhook authentication at the FastAPI boundary."""

    @staticmethod
    def _build_request(headers: dict[str, str] | None = None, body: dict | None = None) -> Request:
        payload = body or {"event": "messages.upsert", "data": {}}
        header_pairs = []
        for key, value in (headers or {}).items():
            header_pairs.append((key.lower().encode("latin-1"), value.encode("latin-1")))

        async def receive() -> dict:
            return {
                "type": "http.request",
                "body": __import__("json").dumps(payload).encode("utf-8"),
                "more_body": False,
            }

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/webhook/evolution",
            "raw_path": b"/webhook/evolution",
            "query_string": b"",
            "headers": header_pairs,
            "client": ("testclient", 123),
            "server": ("testserver", 80),
        }
        return Request(scope, receive)

    async def test_rejects_when_webhook_secret_is_missing(self):
        """Test webhook is rejected if authentication is not configured."""
        request = self._build_request(headers={"Authorization": "Bearer anything"})

        with (
            patch("app.main.settings.webhook_secret", ""),
            pytest.raises(HTTPException) as exc_info,
        ):
            await evolution_webhook(request)

        assert exc_info.value.status_code == 503

    async def test_rejects_when_authorization_is_invalid(self):
        """Test webhook is rejected before processing when auth is invalid."""
        request = self._build_request(headers={"Authorization": "Bearer wrong-secret"})

        with (
            patch("app.main.settings.webhook_secret", "test-webhook-secret"),
            patch("app.handlers.webhook.WebhookHandler") as mock_handler_cls,
            pytest.raises(HTTPException) as exc_info,
        ):
            await evolution_webhook(request)

        assert exc_info.value.status_code == 401
        mock_handler_cls.assert_not_called()

    async def test_accepts_when_authorization_is_valid(self):
        """Test webhook is processed when the Authorization header is valid."""
        request = self._build_request(headers={"Authorization": "Bearer test-webhook-secret"})

        with (
            patch("app.main.settings.webhook_secret", "test-webhook-secret"),
            patch("app.handlers.webhook.WebhookHandler") as mock_handler_cls,
        ):
            mock_handler = MagicMock()
            mock_handler.handle = AsyncMock()
            mock_handler_cls.return_value = mock_handler

            response = await evolution_webhook(request)

        assert response == {"status": "ok"}
        mock_handler_cls.assert_called_once()
        mock_handler.handle.assert_awaited_once()


class TestAdminAuthentication:
    """Tests for admin endpoint authentication via Authorization header."""

    @staticmethod
    def _build_get_request(path: str, headers: dict[str, str] | None = None) -> Request:
        header_pairs = []
        for key, value in (headers or {}).items():
            header_pairs.append((key.lower().encode("latin-1"), value.encode("latin-1")))

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
            "headers": header_pairs,
            "client": ("testclient", 123),
            "server": ("testserver", 80),
        }
        return Request(scope, receive)

    async def test_admin_qrcode_rejects_invalid_authorization(self):
        """Test QR code endpoint rejects invalid admin auth."""
        request = self._build_get_request(
            "/admin/qrcode",
            headers={"Authorization": "Bearer wrong-secret"},
        )

        with (
            patch("app.main.settings.admin_secret", "test-secret"),
            pytest.raises(HTTPException) as exc_info,
        ):
            await get_qrcode(request)

        assert exc_info.value.status_code == 401

    async def test_admin_status_accepts_valid_authorization(self):
        """Test status endpoint accepts valid admin auth."""
        request = self._build_get_request(
            "/admin/status",
            headers={"Authorization": "Bearer test-secret"},
        )

        with (
            patch("app.main.settings.admin_secret", "test-secret"),
            patch("app.services.evolution.EvolutionService") as mock_evolution_cls,
        ):
            mock_evolution = MagicMock()
            mock_evolution.get_connection_state = AsyncMock(return_value={"instance": "ok"})
            mock_evolution_cls.return_value = mock_evolution

            result = await get_status(request)

        assert result == {"instance": "ok"}


class TestWebhookHandlerBuildExpenseSummary:
    """Tests for building expense summary."""

    @pytest.fixture
    def handler(self):
        with (
            patch("app.handlers.webhook.EvolutionService"),
            patch("app.handlers.webhook.GeminiService"),
        ):
            return WebhookHandler()

    def test_build_expense_summary_simple(self, handler):
        """Test building summary for a simple expense."""
        expense_data = {
            "amount": 50.00,
            "description": "Almoco",
            "category": "Alimentação",
            "payment_method": "Pix",
        }

        summary = handler._build_expense_summary(expense_data, "expense")

        assert "R$ 50.00" in summary
        assert "Almoco" in summary
        assert "Alimentação" in summary
        assert "Pix" in summary

    def test_build_expense_summary_with_installments(self, handler):
        """Test building summary for installment expense."""
        expense_data = {
            "amount": 300.00,
            "description": "Tenis",
            "category": "Vestuario",
            "payment_method": "Cartão de Crédito",
            "installments": 3,
            "is_shared": False,
            "shared_percentage": None,
        }

        summary = handler._build_expense_summary(expense_data, "expense")

        assert "3x" in summary

    def test_build_expense_summary_with_shared(self, handler):
        """Test building summary for shared expense."""
        expense_data = {
            "amount": 200.00,
            "description": "Mercado",
            "category": "Mercado",
            "payment_method": "Pix",
            "installments": None,
            "is_shared": True,
            "shared_percentage": 60.0,
        }

        summary = handler._build_expense_summary(expense_data, "expense")

        assert "60%" in summary

    def test_build_expense_summary_recurring(self, handler):
        """Test building summary for recurring expense."""
        expense_data = {
            "amount": 55.00,
            "description": "Netflix",
            "category": "Assinatura",
            "payment_method": "Cartão de Crédito",
            "recurring_day": 15,
            "is_shared": False,
            "shared_percentage": None,
            "installments": None,
        }

        summary = handler._build_expense_summary(expense_data, "recurring")

        assert "recorrente" in summary.lower()
        assert "15" in summary


class TestWebhookHandlerApplyAdjustments:
    """Tests for applying adjustments to expense data."""

    @pytest.fixture
    def handler(self):
        with (
            patch("app.handlers.webhook.EvolutionService"),
            patch("app.handlers.webhook.GeminiService"),
        ):
            return WebhookHandler()

    def test_apply_adjustments_amount(self, handler):
        """Test applying amount adjustment."""
        expense_data = {"amount": 50.00, "description": "Test"}
        adjustments = {"amount": 60.00}

        result = handler._apply_adjustments(expense_data, adjustments)

        assert result["amount"] == 60.00
        assert result["description"] == "Test"

    def test_apply_adjustments_description(self, handler):
        """Test applying description adjustment."""
        expense_data = {"amount": 50.00, "description": "Test"}
        adjustments = {"description": "New description"}

        result = handler._apply_adjustments(expense_data, adjustments)

        assert result["description"] == "New description"

    def test_apply_adjustments_category(self, handler):
        """Test applying category adjustment."""
        expense_data = {"amount": 50.00, "category": "Alimentação"}
        adjustments = {"category": "Lazer"}

        result = handler._apply_adjustments(expense_data, adjustments)

        assert result["category"] == "Lazer"

    def test_apply_adjustments_payment_method(self, handler):
        """Test applying payment method adjustment."""
        expense_data = {"amount": 50.00, "payment_method": "Pix"}
        adjustments = {"payment_method": "Cartão de Crédito"}

        result = handler._apply_adjustments(expense_data, adjustments)

        assert result["payment_method"] == "Cartão de Crédito"

    def test_apply_adjustments_multiple(self, handler):
        """Test applying multiple adjustments."""
        expense_data = {
            "amount": 50.00,
            "description": "Test",
            "category": "Alimentação",
            "payment_method": "Pix",
        }
        adjustments = {
            "amount": 75.00,
            "category": "Lazer",
        }

        result = handler._apply_adjustments(expense_data, adjustments)

        assert result["amount"] == 75.00
        assert result["category"] == "Lazer"
        assert result["description"] == "Test"  # Unchanged
        assert result["payment_method"] == "Pix"  # Unchanged

    def test_apply_adjustments_none_values_ignored(self, handler):
        """Test that None values in adjustments are ignored."""
        expense_data = {"amount": 50.00, "description": "Test"}
        adjustments = {"amount": None, "description": None}

        result = handler._apply_adjustments(expense_data, adjustments)

        assert result["amount"] == 50.00
        assert result["description"] == "Test"


class TestWebhookHandlerIntentHandling:
    """Tests for handling different intents."""

    @pytest.fixture
    def handler(self):
        """Create handler with mocked services."""
        with (
            patch("app.handlers.webhook.EvolutionService") as MockEvolution,
            patch("app.handlers.webhook.GeminiService") as MockGemini,
        ):
            mock_evolution = MagicMock()
            mock_evolution.send_text = AsyncMock()
            mock_evolution.send_document = AsyncMock()
            mock_evolution.download_media = AsyncMock()
            MockEvolution.return_value = mock_evolution

            mock_gemini = MagicMock()
            mock_gemini.process_message = AsyncMock()
            mock_gemini.process_image = AsyncMock()
            mock_gemini.process_pdf_text = AsyncMock()
            mock_gemini.evaluate_confirmation_response = AsyncMock()
            MockGemini.return_value = mock_gemini

            handler = WebhookHandler()
            handler.evolution = mock_evolution
            handler.gemini = mock_gemini

            yield handler

    async def test_handle_register_expense_asks_confirmation(
        self, handler, seeded_session, test_phone
    ):
        """Test that registering expense asks for confirmation."""
        data = {
            "intent": "register_expense",
            "data": {
                "description": "Almoco",
                "amount": 50.00,
                "category": "Alimentação",
                "payment_method": "Pix",
            },
        }

        await handler.handle_register_expense(seeded_session, test_phone, data)

        # Should send confirmation message
        handler.evolution.send_text.assert_called_once()
        call_args = handler.evolution.send_text.call_args
        assert "correto" in call_args[0][1].lower()

        # Should save pending confirmation
        pending = await handler.get_pending_confirmation(seeded_session, test_phone)
        assert pending is not None

    async def test_handle_register_expense_missing_payment_asks(
        self, handler, seeded_session, test_phone
    ):
        """Test that missing payment method triggers question."""
        data = {
            "intent": "register_expense",
            "data": {
                "description": "Almoco",
                "amount": 50.00,
                "category": "Alimentação",
                "payment_method": None,
            },
        }

        await handler.handle_register_expense(seeded_session, test_phone, data)

        # Should ask for payment method
        handler.evolution.send_text.assert_called_once()
        call_args = handler.evolution.send_text.call_args
        assert "forma de pagamento" in call_args[0][1].lower()

    async def test_process_message_new_user_starts_onboarding(
        self, handler, seeded_session, test_phone
    ):
        """Test that a new user is asked to accept terms before using the bot."""
        msg_data = {
            "phone": test_phone,
            "text": "oi",
            "has_image": False,
            "has_document": False,
            "message_key": {"id": "onboarding-1"},
        }

        await handler.process_message(seeded_session, msg_data)

        handler.evolution.send_text.assert_awaited_once()
        call_args = handler.evolution.send_text.call_args
        assert "aceite nos termos" in call_args.args[1].lower()

        pending = await handler.get_pending_confirmation(seeded_session, test_phone)
        assert pending is not None
        assert pending.data["type"] == "user_onboarding"

    async def test_process_message_accepts_terms_and_unlocks_user(
        self, handler, seeded_session, test_phone
    ):
        """Test that the user can accept terms via onboarding."""
        await handler.save_pending_confirmation(
            seeded_session,
            test_phone,
            {"type": "user_onboarding", "terms_version": "2026-04"},
        )
        await handler.user_service.get_or_create_user(seeded_session, test_phone)

        await handler.process_message(
            seeded_session,
            {
                "phone": test_phone,
                "text": "sim",
                "has_image": False,
                "has_document": False,
                "message_key": {"id": "onboarding-2"},
            },
        )

        handler.evolution.send_text.assert_awaited_once()
        call_args = handler.evolution.send_text.call_args
        assert "termos aceitos" in call_args.args[1].lower()

    async def test_process_message_direct_show_limits_bypasses_gemini(
        self, handler, seeded_session, test_phone, accepted_user_in_db
    ):
        """Test that direct limit commands are handled locally."""
        await handler.process_message(
            seeded_session,
            {
                "phone": test_phone,
                "text": "meus limites",
                "has_image": False,
                "has_document": False,
                "message_key": {"id": "limit-show"},
            },
        )

        handler.gemini.process_message.assert_not_awaited()
        handler.evolution.send_text.assert_awaited_once()
        call_args = handler.evolution.send_text.call_args
        assert "seus limites diarios atuais" in call_args.args[1].lower()

    async def test_process_message_blocks_when_text_limit_reached(
        self, handler, seeded_session, test_phone, accepted_user_in_db
    ):
        """Test that text messages are blocked when the daily text limit is reached."""
        accepted_user_in_db.daily_text_limit = 0
        await seeded_session.commit()

        await handler.process_message(
            seeded_session,
            {
                "phone": test_phone,
                "text": "oi de novo",
                "has_image": False,
                "has_document": False,
                "message_key": {"id": "limit-block"},
            },
        )

        handler.evolution.send_text.assert_awaited_once()
        call_args = handler.evolution.send_text.call_args
        assert "atingiu seu limite diario" in call_args.args[1].lower()

    async def test_process_message_routes_pdf_to_specific_handler(
        self, handler, seeded_session, test_phone, accepted_user_in_db
    ):
        """Test that PDF documents are routed to the PDF handler."""
        handler.handle_pdf_message = AsyncMock()

        msg_data = {
            "phone": test_phone,
            "text": "",
            "has_image": False,
            "has_document": True,
            "document_mimetype": "application/pdf",
            "message_key": {"id": "123"},
        }

        await handler.process_message(seeded_session, msg_data)

        handler.handle_pdf_message.assert_awaited_once()

    async def test_process_message_routes_json_document_to_backup_handler(
        self, handler, seeded_session, test_phone, accepted_user_in_db
    ):
        """Test that JSON documents are routed to the backup handler."""
        handler.handle_backup_document = AsyncMock()

        msg_data = {
            "phone": test_phone,
            "text": "",
            "has_image": False,
            "has_document": True,
            "document_mimetype": "application/json",
            "document_filename": "backup.json",
            "message_key": {"id": "backup-123"},
        }

        await handler.process_message(seeded_session, msg_data)

        handler.handle_backup_document.assert_awaited_once_with(seeded_session, msg_data)

    async def test_handle_pdf_message_success(
        self, handler, seeded_session, test_phone, accepted_user_in_db
    ):
        """Test successful PDF receipt processing."""
        handler.evolution.download_media.return_value = b"%PDF fake"
        handler.gemini.process_pdf_text.return_value = {
            "success": True,
            "intent": "register_expense",
            "data": {
                "description": "Uber",
                "amount": 42.50,
                "category": "Transporte",
                "payment_method": "Pix",
            },
        }
        handler.handle_register_expense = AsyncMock()

        with (
            patch.object(handler, "_validate_pdf_document", return_value=None),
            patch.object(handler, "_extract_text_from_pdf", return_value="COMPROVANTE PIX UBER"),
        ):
            await handler.handle_pdf_message(
                seeded_session,
                {
                    "phone": test_phone,
                    "text": "comprovante uber",
                    "message_key": {"id": "123"},
                },
                accepted_user_in_db,
            )

        handler.gemini.process_pdf_text.assert_awaited_once_with(
            "COMPROVANTE PIX UBER",
            "comprovante uber",
        )
        handler.handle_register_expense.assert_awaited_once()

    async def test_handle_pdf_message_without_extractable_text(
        self, handler, seeded_session, test_phone, accepted_user_in_db
    ):
        """Test PDF processing when text extraction fails."""
        handler.evolution.download_media.return_value = b"%PDF fake"

        with (
            patch.object(handler, "_validate_pdf_document", return_value=None),
            patch.object(handler, "_extract_text_from_pdf", return_value=""),
        ):
            await handler.handle_pdf_message(
                seeded_session,
                {
                    "phone": test_phone,
                    "text": "",
                    "message_key": {"id": "123"},
                },
                accepted_user_in_db,
            )

        handler.evolution.send_text.assert_awaited_once()
        call_args = handler.evolution.send_text.call_args
        assert "extrair texto do pdf" in call_args.args[1].lower()

    async def test_handle_pdf_message_rejects_oversized_pdf(
        self, handler, seeded_session, test_phone, accepted_user_in_db
    ):
        """Test PDF processing rejects files above the safe size limit."""
        handler.evolution.download_media.return_value = b"x" * 32

        with patch("app.handlers.webhook.settings.max_pdf_size_bytes", 16):
            await handler.handle_pdf_message(
                seeded_session,
                {
                    "phone": test_phone,
                    "text": "",
                    "message_key": {"id": "123"},
                },
                accepted_user_in_db,
            )

        handler.gemini.process_pdf_text.assert_not_called()
        handler.evolution.send_text.assert_awaited_once()
        assert "excede o limite" in handler.evolution.send_text.call_args.args[1].lower()

    async def test_handle_export_backup_sends_json_document(
        self, handler, seeded_session, test_phone
    ):
        """Test exporting backup as JSON document."""
        handler.backup_service.export_user_backup = AsyncMock(
            return_value={
                "success": True,
                "file_base64": "json-base64",
                "filename": "finbot_backup.json",
                "mimetype": "application/json",
            }
        )

        await handler.handle_export_backup(seeded_session, test_phone)

        handler.evolution.send_document.assert_awaited_once_with(
            test_phone,
            "json-base64",
            "finbot_backup.json",
            caption="Seu backup completo do FinBot",
            mimetype="application/json",
        )

    async def test_handle_backup_document_saves_confirmation(
        self, handler, seeded_session, test_phone
    ):
        """Test JSON backup document processing before restore."""
        handler.evolution.download_media.return_value = b'{"metadata":{"schema_version":1,"source_phone":"5511888888888"},"expenses":[],"budgets":[],"goals":[]}'
        handler.backup_service.parse_backup_document = MagicMock(
            return_value={
                "success": True,
                "backup_data": {
                    "metadata": {"schema_version": 1, "source_phone": "5511888888888"},
                    "expenses": [],
                    "budgets": [],
                    "goals": [],
                },
            }
        )
        handler.backup_service.summarize_backup = MagicMock(
            return_value={
                "source_phone": "5511888888888",
                "expenses": 0,
                "budgets": 0,
                "budget_alerts": 0,
                "goals": 0,
                "goal_updates": 0,
            }
        )
        handler.backup_service.store_temporary_backup = AsyncMock(
            return_value={
                "success": True,
                "backup_ref": "finbot:backup:test",
                "backup_hash": "abc123",
            }
        )

        await handler.handle_backup_document(
            seeded_session,
            {
                "phone": test_phone,
                "text": "",
                "message_key": {"id": "backup-123"},
            },
        )

        pending = await handler.get_pending_confirmation(seeded_session, test_phone)
        assert pending is not None
        assert pending.data["type"] == "backup_restore"
        assert pending.data["backup_ref"] == "finbot:backup:test"
        assert "backup_data" not in pending.data
        handler.evolution.send_text.assert_awaited_once()

    async def test_handle_backup_restore_confirmation_success(
        self, handler, seeded_session, test_phone, accepted_user_in_db
    ):
        """Test confirmed backup restore."""
        handler.backup_service.load_temporary_backup = AsyncMock(
            return_value={"metadata": {"schema_version": 1}, "expenses": [], "budgets": [], "goals": []}
        )
        handler.backup_service.delete_temporary_backup = AsyncMock()
        handler.backup_service.restore_user_backup = AsyncMock(
            return_value={
                "success": True,
                "restored": {
                    "expenses": 2,
                    "budgets": 1,
                    "budget_alerts": 1,
                    "goals": 1,
                    "goal_updates": 2,
                },
            }
        )

        await handler._handle_backup_restore_confirmation(
            seeded_session,
            test_phone,
            "sim",
            {
                "type": "backup_restore",
                "backup_ref": "finbot:backup:test",
                "summary": {
                    "source_phone": "5511888888888",
                    "expenses": 2,
                    "budgets": 1,
                    "budget_alerts": 1,
                    "goals": 1,
                    "goal_updates": 2,
                },
            },
            accepted_user_in_db,
        )

        handler.backup_service.restore_user_backup.assert_awaited_once()
        handler.backup_service.delete_temporary_backup.assert_awaited_once_with(
            "finbot:backup:test"
        )
        handler.evolution.send_text.assert_awaited_once()
        call_args = handler.evolution.send_text.call_args
        assert "backup restaurado com sucesso" in call_args.args[1].lower()

    async def test_handle_backup_restore_confirmation_rejects_expired_reference(
        self, handler, seeded_session, test_phone, accepted_user_in_db
    ):
        """Test restore confirmation fails safely when temporary backup is gone."""
        handler.backup_service.load_temporary_backup = AsyncMock(return_value=None)
        handler.backup_service.delete_temporary_backup = AsyncMock()
        handler.backup_service.restore_user_backup = AsyncMock()

        await handler._handle_backup_restore_confirmation(
            seeded_session,
            test_phone,
            "sim",
            {
                "type": "backup_restore",
                "backup_ref": "finbot:backup:missing",
                "summary": {},
            },
            accepted_user_in_db,
        )

        handler.backup_service.restore_user_backup.assert_not_awaited()
        handler.evolution.send_text.assert_awaited_once()
        assert "expirou" in handler.evolution.send_text.call_args.args[1].lower()

    async def test_handle_export_sends_xlsx_by_default(self, handler, seeded_session, test_phone):
        """Test that export uses XLSX by default."""
        with patch("app.services.export.ExportService") as MockExportService:
            export_service = MagicMock()
            export_service.export_month = AsyncMock(
                return_value={
                    "success": True,
                    "file_base64": "xlsx-base64",
                    "filename": "gastos_abril_2026.xlsx",
                    "month_name": "Abril de 2026",
                }
            )
            export_service.export_month_pdf = AsyncMock()
            MockExportService.return_value = export_service

            data = {"intent": "export", "data": {"month": 4, "year": 2026}}

            await handler.handle_export(seeded_session, test_phone, data)

            export_service.export_month.assert_awaited_once_with(
                seeded_session,
                test_phone,
                4,
                2026,
            )
            export_service.export_month_pdf.assert_not_called()
            handler.evolution.send_document.assert_awaited_once_with(
                test_phone,
                "xlsx-base64",
                "gastos_abril_2026.xlsx",
                caption="Seus gastos de Abril de 2026",
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    async def test_handle_export_sends_pdf_when_requested(
        self, handler, seeded_session, test_phone
    ):
        """Test that export uses PDF when explicitly requested."""
        with patch("app.services.export.ExportService") as MockExportService:
            export_service = MagicMock()
            export_service.export_month = AsyncMock()
            export_service.export_month_pdf = AsyncMock(
                return_value={
                    "success": True,
                    "file_base64": "pdf-base64",
                    "filename": "gastos_marco_2026.pdf",
                    "month_name": "Marco de 2026",
                    "mimetype": "application/pdf",
                }
            )
            MockExportService.return_value = export_service

            data = {
                "intent": "export",
                "data": {"month": 3, "year": 2026, "export_format": "pdf"},
            }

            await handler.handle_export(seeded_session, test_phone, data)

            export_service.export_month_pdf.assert_awaited_once_with(
                seeded_session,
                test_phone,
                3,
                2026,
            )
            export_service.export_month.assert_not_called()
            handler.evolution.send_document.assert_awaited_once_with(
                test_phone,
                "pdf-base64",
                "gastos_marco_2026.pdf",
                caption="Seus gastos de Marco de 2026",
                mimetype="application/pdf",
            )

    async def test_handle_query_month(self, handler, seeded_session, test_phone, expense_in_db):
        """Test handling query month intent."""
        data = {"data": {"month": None, "year": None}}

        await handler.handle_query_month(seeded_session, test_phone, data)

        handler.evolution.send_text.assert_called_once()

    async def test_handle_list_recurring(self, handler, seeded_session, test_phone):
        """Test handling list recurring intent."""
        await handler.handle_list_recurring(seeded_session, test_phone)

        handler.evolution.send_text.assert_called_once()
        call_args = handler.evolution.send_text.call_args
        assert "recorrentes" in call_args[0][1].lower()

    async def test_handle_undo_last_no_expenses(self, handler, seeded_session, test_phone):
        """Test undo when there are no expenses."""
        await handler.handle_undo_last(seeded_session, test_phone)

        handler.evolution.send_text.assert_called_once()
        call_args = handler.evolution.send_text.call_args
        assert "nenhum" in call_args[0][1].lower() or "não" in call_args[0][1].lower()

    async def test_handle_undo_last_success(
        self, handler, seeded_session, test_phone, expense_in_db
    ):
        """Test successful undo."""
        await handler.handle_undo_last(seeded_session, test_phone)

        handler.evolution.send_text.assert_called_once()
        call_args = handler.evolution.send_text.call_args
        assert "removido" in call_args[0][1].lower()
