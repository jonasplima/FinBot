"""Google Gemini AI integration service."""

import json
import logging
import base64
from typing import Optional
from datetime import date

import google.generativeai as genai

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Configure Gemini
genai.configure(api_key=settings.gemini_api_key)


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
- unknown: nao entendi a mensagem

## Formato de resposta (JSON):
{
  "intent": "register_expense|register_recurring|cancel_recurring|query_month|export|list_recurring|unknown",
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

Entrada: "exportar meus gastos de marco"
Saida: {"intent": "export", "data": {"description": null, "amount": null, "category": null, "payment_method": null, "installments": null, "is_shared": false, "shared_percentage": null, "recurring_day": null, "month": 3, "year": null}, "confidence": 0.95}

Entrada: "gastei 200 reais no mercado dividido 60% meu"
Saida: {"intent": "register_expense", "data": {"description": "mercado", "amount": 200.00, "category": "Mercado", "payment_method": "Pix", "installments": null, "is_shared": true, "shared_percentage": 60.0, "recurring_day": null, "month": null, "year": null}, "confidence": 0.9}

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
    """Service for interacting with Google Gemini AI."""

    def __init__(self):
        # Use gemini-2.5-flash-lite for text and vision (supports both)
        self.model = genai.GenerativeModel("gemini-2.5-flash-lite")
        self.vision_model = genai.GenerativeModel("gemini-2.5-flash-lite")

    async def process_message(self, text: str) -> dict:
        """Process text message and extract intent/data."""
        try:
            # Add current date context
            today = date.today()
            context = f"Data atual: {today.strftime('%d/%m/%Y')}\n\nMensagem do usuario: {text}"

            response = self.model.generate_content(
                [SYSTEM_PROMPT, context],
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )

            # Parse JSON response
            result = json.loads(response.text)
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

            response = self.vision_model.generate_content(
                [prompt, image_part],
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )

            result = json.loads(response.text)
            logger.debug(f"Gemini vision response: {result}")

            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Gemini vision response: {e}")
            return {"success": False, "error": "Falha ao interpretar resposta"}
        except Exception as e:
            logger.error(f"Gemini vision error: {e}")
            return {"success": False, "error": str(e)}
