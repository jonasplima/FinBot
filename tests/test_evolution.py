"""Tests for EvolutionService message extraction."""

from unittest.mock import patch

from app.services.evolution import EvolutionService


class TestEvolutionServiceMessageExtraction:
    """Tests for extracting data from incoming Evolution webhooks."""

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
