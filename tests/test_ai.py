"""Tests for AIService with mocked API calls."""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai import (
    GROQ_MODEL_FALLBACK_CHAIN,
    MODEL_FALLBACK_CHAIN,
    VISION_CAPABLE_MODELS,
    AIService,
)


@pytest.fixture(autouse=True)
def clear_model_exhaustion():
    """Isolate class-level exhaustion tracking between tests."""
    AIService._exhausted_models.clear()
    yield
    AIService._exhausted_models.clear()


class TestAIServiceInit:
    """Tests for AIService initialization."""

    def test_init_models(self):
        """Test that service initializes with correct models."""
        with patch("app.services.ai.genai"):
            service = AIService()

            assert service.models == MODEL_FALLBACK_CHAIN
            assert service.vision_models == VISION_CAPABLE_MODELS


class TestAIServiceQuotaDetection:
    """Tests for quota error detection."""

    def test_is_quota_error_true(self):
        """Test that quota errors are detected correctly."""
        with patch("app.services.ai.genai"):
            service = AIService()

            assert service._is_quota_error(Exception("quota exceeded"))
            assert service._is_quota_error(Exception("rate limit reached"))
            assert service._is_quota_error(Exception("429 Too Many Requests"))
            assert service._is_quota_error(Exception("ResourceExhausted"))

    def test_is_quota_error_false(self):
        """Test that non-quota errors are not detected as quota errors."""
        with patch("app.services.ai.genai"):
            service = AIService()

            assert not service._is_quota_error(Exception("connection error"))
            assert not service._is_quota_error(Exception("invalid api key"))
            assert not service._is_quota_error(Exception("timeout"))


class TestAIServiceModelExhaustion:
    """Tests for model exhaustion tracking."""

    def test_mark_model_exhausted(self):
        """Test marking a model as exhausted."""
        with patch("app.services.ai.genai"):
            service = AIService()
            service._mark_model_exhausted("gemini-2.5-flash-lite")

            assert "gemini:gemini-2.5-flash-lite" in service._exhausted_models

    def test_get_available_model_skips_exhausted(self):
        """Test that exhausted models are skipped."""
        with patch("app.services.ai.genai"):
            service = AIService()
            service._exhausted_models["gemini:gemini-2.5-flash-lite"] = datetime.now()

            available = service._get_available_model()

            assert available != "gemini-2.5-flash-lite"

    def test_exhausted_model_recovers_after_timeout(self):
        """Test that exhausted models become available after timeout."""
        with patch("app.services.ai.genai"):
            service = AIService()
            # Mark as exhausted more than 1 hour ago
            service._exhausted_models["gemini:gemini-2.5-flash-lite"] = datetime.now() - timedelta(
                hours=2
            )

            available = service._get_available_model()

            # Model should be available again
            assert available == "gemini-2.5-flash-lite"
            assert "gemini:gemini-2.5-flash-lite" not in service._exhausted_models


class TestAIServiceProcessMessage:
    """Tests for AIService.process_message method."""

    @pytest.fixture
    def mock_model(self):
        """Create a mock AI model."""
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
                "category": "Alimentação",
                "payment_method": "Pix",
                "expense_date": "2026-04-01",
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

        with patch("app.services.ai.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            service = AIService()
            result = await service.process_message("gastei 45 reais no almoco no pix")

            assert result["intent"] == "register_expense"
            assert result["data"]["amount"] == 45.00
            assert result["data"]["category"] == "Alimentação"
            assert result["data"]["expense_date"] == "2026-04-01"

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

        with patch("app.services.ai.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            service = AIService()
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

        with patch("app.services.ai.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            service = AIService()
            result = await service.process_message("desfaz")

            assert result["intent"] == "undo_last"

    async def test_process_message_export_backup(self, mock_model):
        """Test processing a backup export message."""
        expected_response = {
            "intent": "export_backup",
            "data": {},
            "confidence": 0.95,
        }

        mock_response = MagicMock()
        mock_response.text = json.dumps(expected_response)
        mock_model.generate_content.return_value = mock_response

        with patch("app.services.ai.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            service = AIService()
            result = await service.process_message("exporta meu backup")

            assert result["intent"] == "export_backup"

    async def test_process_message_show_limits(self, mock_model):
        """Test processing a show limits message."""
        expected_response = {
            "intent": "show_limits",
            "data": {},
            "confidence": 0.95,
        }

        mock_response = MagicMock()
        mock_response.text = json.dumps(expected_response)
        mock_model.generate_content.return_value = mock_response

        with patch("app.services.ai.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            service = AIService()
            result = await service.process_message("meus limites")

            assert result["intent"] == "show_limits"

    async def test_process_message_invalid_json(self, mock_model):
        """Test handling invalid JSON response."""
        mock_response = MagicMock()
        mock_response.text = "invalid json response"
        mock_model.generate_content.return_value = mock_response

        with patch("app.services.ai.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            service = AIService()
            result = await service.process_message("test message")

            assert result["intent"] == "unknown"
            assert result["confidence"] == 0

    async def test_process_message_api_error(self, mock_model):
        """Test handling API error."""
        mock_model.generate_content.side_effect = Exception("API Error")

        with patch("app.services.ai.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            service = AIService()
            result = await service.process_message("test message")

            assert result["intent"] == "unknown"

    async def test_generate_with_fallback_uses_next_model_on_timeout(self):
        """Test timeout on one model falls back to the next available model."""
        slow_model = MagicMock()
        slow_model.generate_content.side_effect = lambda *args, **kwargs: (_ for _ in ()).throw(
            TimeoutError("timeout")
        )

        ok_response = MagicMock()
        ok_response.text = json.dumps({"intent": "query_month", "data": {}, "confidence": 0.9})
        ok_model = MagicMock()
        ok_model.generate_content.return_value = ok_response

        with patch("app.services.ai.genai") as mock_genai:
            mock_genai.GenerativeModel.side_effect = [slow_model, ok_model]

            service = AIService()
            with patch("app.services.ai.settings.ai_timeout_seconds", 5):
                result = await service.process_message("quanto gastei esse mes?")

        assert result["intent"] == "query_month"

    async def test_process_message_returns_unknown_when_all_models_timeout(self):
        """Test full timeout chain returns controlled unknown response."""
        with patch("app.services.ai.genai") as mock_genai:
            timeout_model = MagicMock()
            timeout_model.generate_content.side_effect = TimeoutError("timeout")
            mock_genai.GenerativeModel.return_value = timeout_model

            service = AIService()
            with patch("app.services.ai.settings.ai_timeout_seconds", 5):
                result = await service.process_message("test message")

        assert result["intent"] == "unknown"
        assert result["confidence"] == 0

    async def test_generate_with_fallback_uses_asyncio_to_thread(self, mock_model):
        """Test blocking AI provider call is delegated out of the event loop."""
        mock_response = MagicMock()
        mock_response.text = json.dumps({"intent": "query_month", "data": {}, "confidence": 0.9})
        mock_model.generate_content.return_value = mock_response

        async def fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)

        with (
            patch("app.services.ai.genai") as mock_genai,
            patch(
                "app.services.ai.asyncio.to_thread", side_effect=fake_to_thread
            ) as mock_to_thread,
        ):
            mock_genai.GenerativeModel.return_value = mock_model

            service = AIService()
            result = await service.process_message("quanto gastei esse mes?")

        assert result["intent"] == "query_month"
        assert mock_to_thread.await_count >= 1

    async def test_process_message_falls_back_to_groq_on_gemini_quota(self):
        """Test quota exhaustion in the primary provider falls back to Groq text generation."""
        groq_payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"intent": "query_month", "data": {}, "confidence": 0.9}
                        )
                    }
                }
            ]
        }

        quota_model = MagicMock()
        quota_model.generate_content.side_effect = Exception("429 quota exceeded")

        mock_http_response = MagicMock()
        mock_http_response.json.return_value = groq_payload
        mock_http_response.raise_for_status.return_value = None

        mock_http_client = MagicMock()
        mock_http_client.__aenter__.return_value = mock_http_client
        mock_http_client.__aexit__.return_value = False
        mock_http_client.post = AsyncMock(return_value=mock_http_response)

        with (
            patch("app.services.ai.genai") as mock_genai,
            patch("app.services.ai.httpx.AsyncClient", return_value=mock_http_client),
            patch("app.services.ai.settings.groq_api_key", "test-groq-key"),
        ):
            mock_genai.GenerativeModel.return_value = quota_model

            service = AIService()
            result = await service.process_message("quanto gastei esse mes?")

        assert result["intent"] == "query_month"

    async def test_process_image_falls_back_to_groq_on_gemini_quota(self):
        """Test quota exhaustion in the primary provider falls back to Groq vision generation."""
        groq_payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "success": True,
                                "intent": "register_expense",
                                "data": {
                                    "description": "Restaurante XYZ",
                                    "amount": 89.90,
                                    "category": "Alimentação",
                                    "payment_method": None,
                                },
                                "confidence": 0.9,
                            }
                        )
                    }
                }
            ]
        }

        quota_model = MagicMock()
        quota_model.generate_content.side_effect = Exception("quota exceeded")

        mock_http_response = MagicMock()
        mock_http_response.json.return_value = groq_payload
        mock_http_response.raise_for_status.return_value = None

        mock_http_client = MagicMock()
        mock_http_client.__aenter__.return_value = mock_http_client
        mock_http_client.__aexit__.return_value = False
        mock_http_client.post = AsyncMock(return_value=mock_http_response)

        with (
            patch("app.services.ai.genai") as mock_genai,
            patch("app.services.ai.httpx.AsyncClient", return_value=mock_http_client),
            patch("app.services.ai.settings.groq_api_key", "test-groq-key"),
        ):
            mock_genai.GenerativeModel.return_value = quota_model

            service = AIService()
            result = await service.process_image(b"fake_image_data")

        assert result["success"] is True


class TestAIServiceEvaluateConfirmation:
    """Tests for AIService.evaluate_confirmation_response method."""

    async def test_fast_path_confirm(self):
        """Test fast path for confirmation responses."""
        with patch("app.services.ai.genai"):
            service = AIService()

            # Test various confirmation phrases
            for phrase in ["sim", "ok", "confirma", "pode", "certo", "beleza"]:
                result = await service.evaluate_confirmation_response("expense summary", phrase)
                assert result["action"] == "confirm"
                assert result["confidence"] == 1.0

    async def test_fast_path_cancel(self):
        """Test fast path for cancellation responses."""
        with patch("app.services.ai.genai"):
            service = AIService()

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

        with patch("app.services.ai.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            service = AIService()
            result = await service.evaluate_confirmation_response(
                "expense summary", "muda pra 60 reais"
            )

            assert result["action"] == "adjust"
            assert result["adjustments"]["amount"] == 60.00


class TestAIServiceProcessImage:
    """Tests for AIService.process_image method."""

    async def test_process_image_success(self):
        """Test successful image processing."""
        expected_response = {
            "success": True,
            "intent": "register_expense",
            "data": {
                "description": "Restaurante XYZ",
                "amount": 89.90,
                "category": "Alimentação",
                "payment_method": None,
            },
            "confidence": 0.9,
        }

        mock_response = MagicMock()
        mock_response.text = json.dumps(expected_response)

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        with patch("app.services.ai.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            service = AIService()
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

        with patch("app.services.ai.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            service = AIService()
            result = await service.process_image(b"fake_image_data")

            assert result["success"] is False


class TestAIServiceProcessPdfText:
    """Tests for AIService.process_pdf_text method."""

    async def test_process_pdf_text_success(self):
        """Test successful PDF text processing."""
        expected_response = {
            "success": True,
            "intent": "register_expense",
            "data": {
                "description": "Restaurante ABC",
                "amount": 120.50,
                "category": "Alimentação",
                "payment_method": "Pix",
            },
            "confidence": 0.91,
        }

        mock_response = MagicMock()
        mock_response.text = json.dumps(expected_response)

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        with patch("app.services.ai.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            service = AIService()
            result = await service.process_pdf_text("COMPROVANTE PIX\nVALOR R$ 120,50")

            assert result["success"] is True
            assert result["data"]["amount"] == 120.50

    async def test_process_pdf_text_failure(self):
        """Test PDF text processing failure."""
        expected_response = {
            "success": False,
            "error": "Texto insuficiente",
        }

        mock_response = MagicMock()
        mock_response.text = json.dumps(expected_response)

        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response

        with patch("app.services.ai.genai") as mock_genai:
            mock_genai.GenerativeModel.return_value = mock_model

            service = AIService()
            result = await service.process_pdf_text("arquivo qualquer")

            assert result["success"] is False


class TestAIServiceModelStatus:
    """Tests for AIService.get_model_status method."""

    def test_get_model_status_all_available(self):
        """Test getting model status when all models are available."""
        with patch("app.services.ai.genai"):
            with patch("app.services.ai.settings.groq_api_key", "test-groq-key"):
                service = AIService()
                status = service.get_model_status()

            for model in MODEL_FALLBACK_CHAIN:
                key = f"gemini:{model}"
                assert key in status
                assert status[key]["available"] is True
            for model in GROQ_MODEL_FALLBACK_CHAIN:
                key = f"groq:{model}"
                assert key in status
                assert status[key]["available"] is True

    def test_get_model_status_some_exhausted(self):
        """Test getting model status with some exhausted models."""
        with patch("app.services.ai.genai"):
            service = AIService()
            service._exhausted_models["gemini:gemini-2.5-flash-lite"] = datetime.now()

            status = service.get_model_status()

            assert status["gemini:gemini-2.5-flash-lite"]["available"] is False
            assert "exhausted_at" in status["gemini:gemini-2.5-flash-lite"]
