"""Tests for EvolutionService."""

from unittest.mock import AsyncMock, patch

from app.services.evolution import EvolutionService


class TestEvolutionServiceMessageExtraction:
    """Tests for extracting data from incoming Evolution webhooks."""

    def test_extract_message_data_includes_instance_name(self):
        """Test extraction keeps the source Evolution instance for replies."""
        webhook_data = {
            "event": "messages.upsert",
            "instance": "FinBot-user-12-abcdef12",
            "data": {
                "key": {
                    "id": "abc123",
                    "remoteJid": "5511999999999@s.whatsapp.net",
                },
                "message": {
                    "conversation": "teste",
                },
            },
        }

        with patch("app.services.evolution.settings"):
            service = EvolutionService()

        result = service.extract_message_data(webhook_data)

        assert result is not None
        assert result["instance_name"] == "FinBot-user-12-abcdef12"

    def test_extract_message_data_document_pdf(self):
        """Test extraction of PDF document metadata from webhook payload."""
        webhook_data = {
            "event": "messages.upsert",
            "data": {
                "key": {
                    "id": "abc123",
                    "remoteJid": "5511999999999@s.whatsapp.net",
                },
                "message": {
                    "documentMessage": {
                        "fileName": "comprovante.pdf",
                        "mimetype": "application/pdf",
                        "caption": "segue comprovante",
                    }
                },
            },
        }

        with patch("app.services.evolution.settings"):
            service = EvolutionService()

        result = service.extract_message_data(webhook_data)

        assert result is not None
        assert result["phone"] == "5511999999999"
        assert result["has_document"] is True
        assert result["document_mimetype"] == "application/pdf"
        assert result["document_filename"] == "comprovante.pdf"
        assert result["text"] == "segue comprovante"
        assert result["has_image"] is False

    def test_extract_message_data_does_not_deduplicate_in_memory(self):
        """Test message extraction no longer drops duplicates by local memory state."""
        webhook_data = {
            "event": "messages.upsert",
            "data": {
                "key": {
                    "id": "dup-123",
                    "remoteJid": "5511999999999@s.whatsapp.net",
                },
                "message": {
                    "conversation": "teste",
                },
            },
        }

        with patch("app.services.evolution.settings"):
            service = EvolutionService()

        first = service.extract_message_data(webhook_data)
        second = service.extract_message_data(webhook_data)

        assert first is not None
        assert second is not None
        assert first["message_key"]["id"] == "dup-123"
        assert second["message_key"]["id"] == "dup-123"


class TestEvolutionServiceWebhookSetup:
    """Tests for webhook setup payload sent to Evolution API."""

    async def test_setup_webhook_sends_authorization_header(self):
        """Test webhook configuration includes the authorization header."""
        with patch("app.services.evolution.settings") as mock_settings:
            mock_settings.evolution_api_url = "http://localhost:8080"
            mock_settings.evolution_api_key = "test-key"
            mock_settings.evolution_instance = "test-instance"
            mock_settings.webhook_secret = "test-webhook-secret"

            service = EvolutionService()
            service._request = AsyncMock(return_value={"success": True})

            await service.setup_webhook()

            service._request.assert_awaited_once_with(
                "POST",
                "/webhook/set/test-instance",
                json={
                    "webhook": {
                        "enabled": True,
                        "url": "http://finbot:3003/webhook/evolution",
                        "headers": {
                            "Authorization": "Bearer test-webhook-secret",
                        },
                        "webhookByEvents": False,
                        "webhookBase64": True,
                        "events": [
                            "MESSAGES_UPSERT",
                            "CONNECTION_UPDATE",
                        ],
                    },
                },
            )


class TestEvolutionServiceReplyInstance:
    """Tests for contextual reply instance selection."""

    async def test_send_text_uses_context_reply_instance(self):
        """Test send_text targets the contextual instance when one is set."""
        with patch("app.services.evolution.settings") as mock_settings:
            mock_settings.evolution_api_url = "http://localhost:8080"
            mock_settings.evolution_api_key = "test-key"
            mock_settings.evolution_instance = "default-instance"

            service = EvolutionService()
            service._request = AsyncMock(return_value={})
            token = service.set_reply_instance("user-instance-123")
            try:
                await service.send_text("5511999999999", "teste")
            finally:
                service.reset_reply_instance(token)

            service._request.assert_awaited_once_with(
                "POST",
                "/message/sendText/user-instance-123",
                json={
                    "number": "5511999999999@s.whatsapp.net",
                    "text": "teste",
                },
            )
