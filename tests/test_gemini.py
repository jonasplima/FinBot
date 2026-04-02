"""Tests for GeminiService with mocked API calls."""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.services.gemini import MODEL_FALLBACK_CHAIN, VISION_CAPABLE_MODELS, GeminiService


class TestGeminiServiceInit:
    """Tests for GeminiService initialization."""

    def test_init_models(self):
        """Test that service initializes with correct models."""
        with patch("app.services.gemini.genai"):
            service = GeminiService()

            assert service.models == MODEL_FALLBACK_CHAIN
            assert service.vision_models == VISION_CAPABLE_MODELS


class TestGeminiServiceQuotaDetection:
    """Tests for quota error detection."""

    def test_is_quota_error_true(self):
        """Test that quota errors are detected correctly."""
        with patch("app.services.gemini.genai"):
            service = GeminiService()

            assert service._is_quota_error(Exception("quota exceeded"))
            assert service._is_quota_error(Exception("rate limit reached"))
            assert service._is_quota_error(Exception("429 Too Many Requests"))
            assert service._is_quota_error(Exception("ResourceExhausted"))

    def test_is_quota_error_false(self):
        """Test that non-quota errors are not detected as quota errors."""
        with patch("app.services.gemini.genai"):
            service = GeminiService()

            assert not service._is_quota_error(Exception("connection error"))
            assert not service._is_quota_error(Exception("invalid api key"))
            assert not service._is_quota_error(Exception("timeout"))


class TestGeminiServiceModelExhaustion:
    """Tests for model exhaustion tracking."""

    def test_mark_model_exhausted(self):
        """Test marking a model as exhausted."""
        with patch("app.services.gemini.genai"):
            service = GeminiService()
            service._mark_model_exhausted("gemini-2.5-flash-lite")

            assert "gemini-2.5-flash-lite" in service._exhausted_models

    def test_get_available_model_skips_exhausted(self):
        """Test that exhausted models are skipped."""
        with patch("app.services.gemini.genai"):
            service = GeminiService()
            service._exhausted_models["gemini-2.5-flash-lite"] = datetime.now()

            available = service._get_available_model()

            assert available != "gemini-2.5-flash-lite"

    def test_exhausted_model_recovers_after_timeout(self):
        """Test that exhausted models become available after timeout."""
        with patch("app.services.gemini.genai"):
            service = GeminiService()
            # Mark as exhausted more than 1 hour ago
            service._exhausted_models["gemini-2.5-flash-lite"] = datetime.now() - timedelta(hours=2)

            available = service._get_available_model()

            # Model should be available again
            assert available == "gemini-2.5-flash-lite"
            assert "gemini-2.5-flash-lite" not in service._exhausted_models


class TestGeminiServiceProcessMessage:
    """Tests for GeminiService.process_message method."""

    @pytest.fixture
    def mock_model(self):
        """Create a mock Gemini model."""
        mock = MagicMock()
        mock.generate_content = MagicMock()
        return mock

    async def test_process_message_register_expense(self, mock_model):
        """Test processing a message that registers an expense."""
        expected_response = {
            "intent": "register_expense",
            "data": {
                "description": "almoco",
                "amount": 45.00,
                "category": "Alimentacao",
                "payment_method": "Pix",
                "installments": None,
                "is_shared": False,
                "shared_percentage": None,
                "recurring_day": None,
                "month": None,
                "year": None,
            },
            "confidence": 0.95,
        }

        mock_response = MagicMock()
        mock_response.text = json.dumps(expected_response)
        mock_model.generate_content.return_value = mock_response

        with patch("app.services.gemini.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            service = GeminiService()
            result = await service.process_message("gastei 45 reais no almoco no pix")

            assert result["intent"] == "register_expense"
            assert result["data"]["amount"] == 45.00
            assert result["data"]["category"] == "Alimentacao"

    async def test_process_message_query_month(self, mock_model):
        """Test processing a query month message."""
        expected_response = {
            "intent": "query_month",
            "data": {
                "month": None,
                "year": None,
            },
            "confidence": 0.95,
        }

        mock_response = MagicMock()
        mock_response.text = json.dumps(expected_response)
        mock_model.generate_content.return_value = mock_response

        with patch("app.services.gemini.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            service = GeminiService()
            result = await service.process_message("quanto gastei esse mes?")

            assert result["intent"] == "query_month"

    async def test_process_message_undo_last(self, mock_model):
        """Test processing an undo message."""
        expected_response = {
            "intent": "undo_last",
            "data": {},
            "confidence": 0.95,
        }

        mock_response = MagicMock()
        mock_response.text = json.dumps(expected_response)
        mock_model.generate_content.return_value = mock_response

        with patch("app.services.gemini.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            service = GeminiService()
            result = await service.process_message("desfaz")

            assert result["intent"] == "undo_last"

    async def test_process_message_invalid_json(self, mock_model):
        """Test handling invalid JSON response."""
        mock_response = MagicMock()
        mock_response.text = "invalid json response"
        mock_model.generate_content.return_value = mock_response

        with patch("app.services.gemini.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            service = GeminiService()
            result = await service.process_message("test message")

            assert result["intent"] == "unknown"
            assert result["confidence"] == 0

    async def test_process_message_api_error(self, mock_model):
        """Test handling API error."""
        mock_model.generate_content.side_effect = Exception("API Error")

        with patch("app.services.gemini.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            service = GeminiService()
            result = await service.process_message("test message")

            assert result["intent"] == "unknown"


class TestGeminiServiceEvaluateConfirmation:
    """Tests for GeminiService.evaluate_confirmation_response method."""

    async def test_fast_path_confirm(self):
        """Test fast path for confirmation responses."""
        with patch("app.services.gemini.genai"):
            service = GeminiService()

            # Test various confirmation phrases
            for phrase in ["sim", "ok", "confirma", "pode", "certo", "beleza"]:
                result = await service.evaluate_confirmation_response("expense summary", phrase)
                assert result["action"] == "confirm"
                assert result["confidence"] == 1.0

    async def test_fast_path_cancel(self):
        """Test fast path for cancellation responses."""
        with patch("app.services.gemini.genai"):
            service = GeminiService()

            # Test various cancellation phrases
            for phrase in ["nao", "não", "cancela", "desisto"]:
                result = await service.evaluate_confirmation_response("expense summary", phrase)
                assert result["action"] == "cancel"
                assert result["confidence"] == 1.0

    async def test_llm_path_adjustment(self):
        """Test LLM path for adjustment responses."""
        expected_response = {
            "action": "adjust",
            "adjustments": {"amount": 60.00},
            "confidence": 0.9,
        }

        mock_response = MagicMock()
        mock_response.text = json.dumps(expected_response)

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        with patch("app.services.gemini.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            service = GeminiService()
            result = await service.evaluate_confirmation_response(
                "expense summary", "muda pra 60 reais"
            )

            assert result["action"] == "adjust"
            assert result["adjustments"]["amount"] == 60.00


class TestGeminiServiceProcessImage:
    """Tests for GeminiService.process_image method."""

    async def test_process_image_success(self):
        """Test successful image processing."""
        expected_response = {
            "success": True,
            "intent": "register_expense",
            "data": {
                "description": "Restaurante XYZ",
                "amount": 89.90,
                "category": "Alimentacao",
                "payment_method": None,
            },
            "confidence": 0.9,
        }

        mock_response = MagicMock()
        mock_response.text = json.dumps(expected_response)

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        with patch("app.services.gemini.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            service = GeminiService()
            result = await service.process_image(b"fake_image_data")

            assert result["success"] is True
            assert result["data"]["amount"] == 89.90

    async def test_process_image_failure(self):
        """Test image processing failure."""
        expected_response = {
            "success": False,
            "error": "Could not read receipt",
        }

        mock_response = MagicMock()
        mock_response.text = json.dumps(expected_response)

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        with patch("app.services.gemini.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            service = GeminiService()
            result = await service.process_image(b"fake_image_data")

            assert result["success"] is False


class TestGeminiServiceModelStatus:
    """Tests for GeminiService.get_model_status method."""

    def test_get_model_status_all_available(self):
        """Test getting model status when all models are available."""
        with patch("app.services.gemini.genai"):
            service = GeminiService()
            status = service.get_model_status()

            for model in MODEL_FALLBACK_CHAIN:
                assert model in status
                assert status[model]["available"] is True

    def test_get_model_status_some_exhausted(self):
        """Test getting model status with some exhausted models."""
        with patch("app.services.gemini.genai"):
            service = GeminiService()
            service._exhausted_models["gemini-2.5-flash-lite"] = datetime.now()

            status = service.get_model_status()

            assert status["gemini-2.5-flash-lite"]["available"] is False
            assert "exhausted_at" in status["gemini-2.5-flash-lite"]
