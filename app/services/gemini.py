"""Google Gemini AI integration service."""

import base64
import json
import logging
from datetime import date, datetime, timedelta

import google.generativeai as genai

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Configure Gemini
genai.configure(api_key=settings.gemini_api_key)

# Model fallback chain - ordered by priority (best to fallback)
MODEL_FALLBACK_CHAIN = [
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemma-3-27b-it",
    "gemma-3-12b-it",
    "gemma-3-4b-it",
    "gemma-3-1b-it",
]

# Models that support vision (image processing)
VISION_CAPABLE_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
]


# System prompt with all categories and payment methods
SYSTEM_PROMPT = """Voce e um assistente financeiro que ajuda usuarios a registrar gastos via WhatsApp.

## Categorias Disponiveis (Negativo = gasto, Positivo = entrada):

### Gastos (Negativo):
- Alimentacao
- Assinatura
- Imprevistos
- Despesa Fixa
- Educacao
- Emprestimo
- Lazer
- Mercado
- Moradia
- Outros
- Parcelamento de Fatura
- Presente
- Saude e Beleza
- Servicos
- Transferencia
- Transporte
- Vestuario
- Viagem
- Reserva de Emergencia
- Investimento

### Entradas (Positivo):
- Salario - Adiantamento
- Salario
- Salario - 13o
- Reembolso - Aluguel + Condominio
- Bonus
- PLR
- VR (Flash)
- VR (Flash - Auxilio)
- Outros (entrada)

## Meios de Pagamento:
- Cartao de Credito
- Cartao de Debito
- Dinheiro
- VR
- Pix

## Sua tarefa:
Analise a mensagem do usuario e retorne um JSON com a intencao e dados extraidos.

## Intencoes possiveis:
- register_expense: registrar gasto ou entrada unica
- register_recurring: registrar despesa recorrente (assinatura, conta mensal)
- cancel_recurring: cancelar despesa recorrente
- query_month: consultar resumo do mes
- export: exportar gastos para planilha
- list_recurring: listar despesas recorrentes
- undo_last: desfazer/apagar o ultimo registro
- unknown: nao entendi a mensagem

## Formato de resposta (JSON):
{
  "intent": "register_expense|register_recurring|cancel_recurring|query_month|export|list_recurring|undo_last|unknown",
  "data": {
    "description": "descricao do gasto",
    "amount": 0.00,
    "category": "categoria exata da lista",
    "payment_method": "metodo exato da lista",
    "installments": null ou numero de parcelas,
    "is_shared": false ou true,
    "shared_percentage": null ou percentual do usuario,
    "recurring_day": null ou dia do mes (1-31),
    "month": null ou numero do mes (1-12),
    "year": null ou ano (ex: 2024)
  },
  "confidence": 0.0 a 1.0
}

## Regras:
1. Sempre escolha categoria e metodo de pagamento das listas acima
2. Se o usuario mencionar "parcelado" ou "Xx", extraia o numero de parcelas
3. Se mencionar "dividido" ou "compartilhado", marque is_shared e calcule o percentual
4. Para recorrentes, extraia o dia do mes (ex: "todo dia 15" -> recurring_day: 15)
5. Para consultas, extraia mes e ano se mencionados
6. Se nao conseguir identificar algo, use null
7. Inferir categoria quando nao especificada (ex: "almoco" -> Alimentacao)
8. Inferir metodo de pagamento pelo contexto (ex: "no pix" -> Pix)

## Exemplos:

Entrada: "gastei 45 reais no almoco no pix"
Saida: {"intent": "register_expense", "data": {"description": "almoco", "amount": 45.00, "category": "Alimentacao", "payment_method": "Pix", "installments": null, "is_shared": false, "shared_percentage": null, "recurring_day": null, "month": null, "year": null}, "confidence": 0.95}

Entrada: "comprei um tenis de 300 reais em 3x no cartao"
Saida: {"intent": "register_expense", "data": {"description": "tenis", "amount": 300.00, "category": "Vestuario", "payment_method": "Cartao de Credito", "installments": 3, "is_shared": false, "shared_percentage": null, "recurring_day": null, "month": null, "year": null}, "confidence": 0.9}

Entrada: "netflix 55 reais todo mes dia 15"
Saida: {"intent": "register_recurring", "data": {"description": "netflix", "amount": 55.00, "category": "Assinatura", "payment_method": "Cartao de Credito", "installments": null, "is_shared": false, "shared_percentage": null, "recurring_day": 15, "month": null, "year": null}, "confidence": 0.95}

Entrada: "cancelar netflix"
Saida: {"intent": "cancel_recurring", "data": {"description": "netflix", "amount": null, "category": null, "payment_method": null, "installments": null, "is_shared": false, "shared_percentage": null, "recurring_day": null, "month": null, "year": null}, "confidence": 0.9}

Entrada: "quanto gastei esse mes?"
Saida: {"intent": "query_month", "data": {"description": null, "amount": null, "category": null, "payment_method": null, "installments": null, "is_shared": false, "shared_percentage": null, "recurring_day": null, "month": null, "year": null}, "confidence": 0.95}

Entrada: "quais foram minhas despesas esse mes"
Saida: {"intent": "query_month", "data": {"description": null, "amount": null, "category": null, "payment_method": null, "installments": null, "is_shared": false, "shared_percentage": null, "recurring_day": null, "month": null, "year": null}, "confidence": 0.95}

Entrada: "resumo de gastos"
Saida: {"intent": "query_month", "data": {"description": null, "amount": null, "category": null, "payment_method": null, "installments": null, "is_shared": false, "shared_percentage": null, "recurring_day": null, "month": null, "year": null}, "confidence": 0.95}

Entrada: "o que eu gastei esse mes"
Saida: {"intent": "query_month", "data": {"description": null, "amount": null, "category": null, "payment_method": null, "installments": null, "is_shared": false, "shared_percentage": null, "recurring_day": null, "month": null, "year": null}, "confidence": 0.95}

Entrada: "me mostra meus gastos"
Saida: {"intent": "query_month", "data": {"description": null, "amount": null, "category": null, "payment_method": null, "installments": null, "is_shared": false, "shared_percentage": null, "recurring_day": null, "month": null, "year": null}, "confidence": 0.95}

Entrada: "exportar meus gastos de marco"
Saida: {"intent": "export", "data": {"description": null, "amount": null, "category": null, "payment_method": null, "installments": null, "is_shared": false, "shared_percentage": null, "recurring_day": null, "month": 3, "year": null}, "confidence": 0.95}

Entrada: "gastei 200 reais no mercado dividido 60% meu"
Saida: {"intent": "register_expense", "data": {"description": "mercado", "amount": 200.00, "category": "Mercado", "payment_method": "Pix", "installments": null, "is_shared": true, "shared_percentage": 60.0, "recurring_day": null, "month": null, "year": null}, "confidence": 0.9}

Entrada: "desfaz"
Saida: {"intent": "undo_last", "data": {}, "confidence": 0.95}

Entrada: "apaga o ultimo"
Saida: {"intent": "undo_last", "data": {}, "confidence": 0.95}

Entrada: "cancela o ultimo gasto"
Saida: {"intent": "undo_last", "data": {}, "confidence": 0.95}

Entrada: "errei, remove"
Saida: {"intent": "undo_last", "data": {}, "confidence": 0.9}

Responda APENAS com o JSON, sem texto adicional.
"""

CONFIRMATION_PROMPT = """Avalie a resposta do usuario a uma confirmacao de despesa/entrada.

## Contexto da despesa pendente:
{expense_summary}

## Resposta do usuario:
{user_response}

## Sua tarefa:
Classifique a intencao da resposta do usuario:

1. "confirm" - usuario confirma que esta correto
   Exemplos: sim, s, ok, isso, pode salvar, ta certo, isso mesmo, confirma, perfeito, exato, correto, certo, beleza, show, bora, valeu, tudo certo, pode ser, confirmar, esta correto, esta certo

2. "cancel" - usuario quer cancelar/desistir
   Exemplos: nao, n, cancela, esquece, deixa pra la, nao quero mais, desisto

3. "adjust" - usuario quer ajustar/corrigir algum campo
   Exemplos: muda pra 60 reais, na verdade foi no cartao, era lazer nao alimentacao, descricao errada

4. "list_categories" - usuario quer ver categorias disponiveis
   Exemplos: quais categorias, lista categorias, categorias disponiveis, que categorias tem

5. "list_payment_methods" - usuario quer ver formas de pagamento
   Exemplos: quais formas de pagamento, metodos de pagamento, como posso pagar

## Formato de resposta (JSON):
{{
  "action": "confirm|cancel|adjust|list_categories|list_payment_methods",
  "adjustments": {{
    "amount": null ou novo valor numerico,
    "description": null ou nova descricao,
    "category": null ou nova categoria (usar nome exato da lista),
    "payment_method": null ou novo metodo (usar nome exato)
  }},
  "confidence": 0.0 a 1.0
}}

## Categorias validas para ajuste:
Gastos: Alimentacao, Assinatura, Imprevistos, Despesa Fixa, Educacao, Emprestimo, Lazer, Mercado, Moradia, Outros, Parcelamento de Fatura, Presente, Saude e Beleza, Servicos, Transferencia, Transporte, Vestuario, Viagem, Reserva de Emergencia, Investimento
Entradas: Salario - Adiantamento, Salario, Salario - 13o, Reembolso - Aluguel + Condominio, Bonus, PLR, VR (Flash), VR (Flash - Auxilio), Outros (entrada)

## Metodos de pagamento validos:
Cartao de Credito, Cartao de Debito, Dinheiro, VR, Pix

Responda APENAS com o JSON, sem texto adicional.
"""

IMAGE_PROMPT = """Analise esta imagem de nota fiscal/cupom e extraia as informacoes.

## Categorias Disponiveis (use EXATAMENTE um destes nomes):
### Gastos: Alimentacao, Assinatura, Imprevistos, Despesa Fixa, Educacao, Emprestimo, Lazer, Mercado, Moradia, Outros, Parcelamento de Fatura, Presente, Saude e Beleza, Servicos, Transferencia, Transporte, Vestuario, Viagem, Reserva de Emergencia, Investimento
### Entradas: Salario - Adiantamento, Salario, Salario - 13o, Reembolso - Aluguel + Condominio, Bonus, PLR, VR (Flash), VR (Flash - Auxilio), Outros (entrada)

## Meios de Pagamento (use EXATAMENTE um destes - SEM ACENTOS):
- Cartao de Credito
- Cartao de Debito
- Dinheiro
- VR
- Pix

## Regras para inferir categoria pelo nome do estabelecimento:
- Bar, Pub, Fliperama, Cinema, Teatro, Boliche -> Lazer
- Restaurante, Lanchonete, Padaria, Cafe -> Alimentacao
- Supermercado, Mercado, Hortifruti -> Mercado
- Farmacia, Drogaria, Clinica, Hospital -> Saude e Beleza
- Posto de Gasolina, Uber, 99, Estacionamento -> Transporte
- Loja de Roupas, Calcados, Moda -> Vestuario
- Hotel, Pousada, Airbnb -> Viagem
- Livraria, Curso, Escola -> Educacao

Retorne um JSON no formato:
{
  "success": true,
  "intent": "register_expense",
  "data": {
    "description": "nome do estabelecimento",
    "amount": valor_numerico,
    "category": "categoria da lista acima",
    "payment_method": null,
    "installments": null,
    "is_shared": false,
    "shared_percentage": null,
    "recurring_day": null,
    "month": null,
    "year": null
  },
  "confidence": 0.0 a 1.0
}

IMPORTANTE: payment_method deve ser null quando nao identificavel na imagem. O sistema perguntara ao usuario.

Se nao conseguir ler a imagem, retorne:
{"success": false, "error": "motivo"}

Responda APENAS com o JSON.
"""


class GeminiService:
    """Service for interacting with Google Gemini AI with automatic model fallback."""

    # Class-level tracking of exhausted models (shared across instances)
    _exhausted_models: dict[str, datetime] = {}
    _exhausted_timeout = timedelta(hours=1)  # Retry exhausted models after 1 hour

    def __init__(self):
        self.models = MODEL_FALLBACK_CHAIN
        self.vision_models = VISION_CAPABLE_MODELS
        self._current_model_index = 0
        self._current_vision_model_index = 0

    def _is_quota_error(self, error: Exception) -> bool:
        """Check if the error is a quota/rate limit error."""
        error_str = str(error).lower()
        quota_indicators = [
            "quota",
            "rate limit",
            "resource exhausted",
            "429",
            "too many requests",
            "resourceexhausted",
        ]
        return any(indicator in error_str for indicator in quota_indicators)

    def _get_available_model(self, vision_only: bool = False) -> str | None:
        """Get the next available model, skipping exhausted ones."""
        model_list = self.vision_models if vision_only else self.models
        now = datetime.now()

        # Clean up expired exhausted models
        expired = [
            model
            for model, exhausted_at in self._exhausted_models.items()
            if now - exhausted_at > self._exhausted_timeout
        ]
        for model in expired:
            del self._exhausted_models[model]
            logger.info(f"Model {model} is available again after timeout")

        # Find first available model
        for model in model_list:
            if model not in self._exhausted_models:
                return model

        # All models exhausted - try the first one anyway (might have reset)
        logger.warning("All models exhausted, trying first model in chain")
        return model_list[0] if model_list else None

    def _mark_model_exhausted(self, model_name: str) -> None:
        """Mark a model as exhausted."""
        self._exhausted_models[model_name] = datetime.now()
        logger.warning(f"Model {model_name} marked as exhausted (quota exceeded)")

    async def _generate_with_fallback(
        self,
        contents: list,
        generation_config: genai.GenerationConfig,
        vision_only: bool = False,
    ) -> str:
        """
        Generate content with automatic model fallback on quota errors.

        Args:
            contents: Content to send to the model
            generation_config: Generation configuration
            vision_only: If True, only use vision-capable models

        Returns:
            Generated text response

        Raises:
            Exception: If all models fail
        """
        model_list = self.vision_models if vision_only else self.models
        last_error = None

        for model_name in model_list:
            # Skip exhausted models
            if model_name in self._exhausted_models:
                continue

            try:
                model = genai.GenerativeModel(model_name)
                logger.debug(f"Trying model: {model_name}")

                response = model.generate_content(
                    contents,
                    generation_config=generation_config,
                )

                logger.info(f"Successfully used model: {model_name}")
                return response.text

            except Exception as e:
                last_error = e
                if self._is_quota_error(e):
                    self._mark_model_exhausted(model_name)
                    logger.warning(f"Quota exceeded for {model_name}, trying next model")
                    continue
                else:
                    # Non-quota error, re-raise
                    raise

        # All models failed
        if last_error:
            raise last_error
        raise Exception("No models available")

    async def process_message(self, text: str) -> dict:
        """Process text message and extract intent/data."""
        try:
            # Add current date context
            today = date.today()
            context = f"Data atual: {today.strftime('%d/%m/%Y')}\n\nMensagem do usuario: {text}"

            response_text = await self._generate_with_fallback(
                contents=[SYSTEM_PROMPT, context],
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )

            # Parse JSON response
            result = json.loads(response_text)
            logger.debug(f"Gemini response: {result}")

            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Gemini response: {e}")
            return {"intent": "unknown", "data": {}, "confidence": 0}
        except Exception as e:
            logger.error(f"Gemini error: {e}")
            return {"intent": "unknown", "data": {}, "confidence": 0}

    async def process_image(
        self,
        image_data: bytes,
        additional_text: str = "",
    ) -> dict:
        """Process image (receipt/invoice) and extract data."""
        try:
            # Encode image to base64
            image_b64 = base64.b64encode(image_data).decode("utf-8")

            # Create image part
            image_part = {
                "mime_type": "image/jpeg",
                "data": image_b64,
            }

            # Add context if there's additional text
            prompt = IMAGE_PROMPT
            if additional_text:
                prompt += f"\n\nTexto adicional do usuario: {additional_text}"

            # Use vision-only models for image processing
            response_text = await self._generate_with_fallback(
                contents=[prompt, image_part],
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
                vision_only=True,
            )

            result = json.loads(response_text)
            logger.debug(f"Gemini vision response: {result}")

            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Gemini vision response: {e}")
            return {"success": False, "error": "Falha ao interpretar resposta"}
        except Exception as e:
            logger.error(f"Gemini vision error: {e}")
            return {"success": False, "error": str(e)}

    async def evaluate_confirmation_response(
        self,
        expense_summary: str,
        user_response: str,
    ) -> dict:
        """
        Evaluate user response to expense confirmation.

        Returns dict with action (confirm/cancel/adjust/list_categories/list_payment_methods)
        and any adjustments requested.
        """
        try:
            # Check for common fast responses first (avoid LLM call for simple cases)
            response_lower = user_response.lower().strip()
            # Remove punctuation for comparison
            response_clean = response_lower.rstrip("!.,?")
            fast_confirm = (
                "sim",
                "s",
                "yes",
                "y",
                "ok",
                "confirmo",
                "isso",
                "pode",
                "salvar",
                "correto",
                "certo",
                "perfeito",
                "exato",
                "isso mesmo",
                "ta certo",
                "esta certo",
                "esta correto",
                "tá certo",
                "tudo certo",
                "pode ser",
                "confirma",
                "confirmar",
                "beleza",
                "show",
                "bora",
                "valeu",
            )
            fast_cancel = (
                "nao",
                "não",
                "n",
                "no",
                "cancela",
                "cancelar",
                "desisto",
                "esquece",
                "deixa",
                "nope",
                "para",
                "parar",
            )

            if response_clean in fast_confirm:
                logger.info(f"Fast-path confirmation: '{response_clean}'")
                return {"action": "confirm", "adjustments": {}, "confidence": 1.0}
            if response_clean in fast_cancel:
                logger.info(f"Fast-path cancellation: '{response_clean}'")
                return {"action": "cancel", "adjustments": {}, "confidence": 1.0}

            # Use LLM for more complex responses
            logger.info(f"Using LLM for confirmation evaluation: '{user_response}'")
            prompt = CONFIRMATION_PROMPT.format(
                expense_summary=expense_summary,
                user_response=user_response,
            )

            response_text = await self._generate_with_fallback(
                contents=[prompt],
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )

            logger.debug(f"Gemini confirmation raw response: {response_text}")
            result = json.loads(response_text)
            logger.info(f"Gemini confirmation evaluation: action={result.get('action')}")

            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Gemini confirmation response: {e}")
            # Default to asking for clarification
            return {"action": "unknown", "adjustments": {}, "confidence": 0}
        except Exception as e:
            logger.error(f"Gemini confirmation error: {e}")
            return {"action": "unknown", "adjustments": {}, "confidence": 0}

    def get_model_status(self) -> dict:
        """Get current status of all models (for debugging/monitoring)."""
        now = datetime.now()
        status = {}
        for model in self.models:
            if model in self._exhausted_models:
                exhausted_at = self._exhausted_models[model]
                time_remaining = self._exhausted_timeout - (now - exhausted_at)
                status[model] = {
                    "available": False,
                    "exhausted_at": exhausted_at.isoformat(),
                    "available_in": str(time_remaining)
                    if time_remaining.total_seconds() > 0
                    else "soon",
                }
            else:
                status[model] = {"available": True}
        return status
