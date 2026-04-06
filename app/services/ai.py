"""Generic AI service with support for multiple providers."""

import asyncio
import base64
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

import google.generativeai as genai
import httpx

from app.config import get_settings
from app.database.connection import async_session
from app.database.models import User
from app.services.category import CategoryService
from app.services.credentials import CredentialService

logger = logging.getLogger(__name__)
settings = get_settings()

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

GROQ_MODEL_FALLBACK_CHAIN = [
    "openai/gpt-oss-20b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]

GROQ_VISION_MODEL_FALLBACK_CHAIN = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
]


# System prompt with all categories and payment methods
SYSTEM_PROMPT = """Voce e um assistente financeiro que ajuda usuarios a registrar gastos via WhatsApp.

## Categorias Disponiveis (Negativo = gasto, Positivo = entrada):

### Gastos (Negativo):
- Alimentação
- Assinatura
- Imprevistos
- Despesa Fixa
- Educação
- Emprestimo
- Lazer
- Mercado
- Moradia
- Outros
- Parcelamento de Fatura
- Presente
- Saúde e Beleza
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
- Reembolso
- Bonus
- PLR
- Vale Refeição
- Vale Alimentação
- Outros (entrada)

## Meios de Pagamento:
- Cartão de Crédito
- Cartão de Débito
- Dinheiro
- Vale Alimentação
- Vale Refeição
- Pix

## Sua tarefa:
Analise a mensagem do usuario e retorne um JSON com a intencao e dados extraidos.

## Intencoes possiveis:
- register_expense: registrar gasto ou entrada unica
- register_recurring: registrar despesa recorrente (assinatura, conta mensal)
- cancel_recurring: cancelar despesa recorrente
- query_month: consultar resumo do mes
- export: exportar gastos para arquivo (xlsx por padrao, pdf quando solicitado)
- list_recurring: listar despesas recorrentes
- undo_last: desfazer/apagar o ultimo registro
- set_budget: definir limite de orcamento para uma categoria
- check_budget: verificar status do orcamento
- list_budgets: listar todos os orcamentos
- remove_budget: remover orcamento de uma categoria
- show_chart: mostrar grafico dos gastos (pizza, barras ou linha)
- create_goal: criar meta de economia
- check_goal: verificar progresso de uma meta
- list_goals: listar todas as metas
- remove_goal: remover/cancelar uma meta
- add_to_goal: adicionar valor a uma meta (deposito manual)
- convert_currency: converter valor entre moedas (sem registrar gasto)
- export_backup: exportar backup completo dos dados do usuario
- import_backup: orientar/importar um backup enviado como JSON
- show_limits: mostrar limites diarios configurados para o usuario
- set_user_limit: ajustar um limite diario do usuario
- unknown: nao entendi a mensagem

## Formato de resposta (JSON):
{
  "intent": "register_expense|register_recurring|cancel_recurring|query_month|export|list_recurring|undo_last|set_budget|check_budget|list_budgets|remove_budget|show_chart|create_goal|check_goal|list_goals|remove_goal|add_to_goal|convert_currency|export_backup|import_backup|show_limits|set_user_limit|unknown",
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
    "year": null ou ano (ex: 2024),
    "budget_limit": null ou limite de orcamento (para set_budget),
    "export_format": null ou "xlsx" ou "pdf" (para export),
    "chart_type": null ou "pie" ou "bars" ou "line" (para show_chart),
    "goal_description": null ou descricao da meta (para create_goal, check_goal, remove_goal, add_to_goal),
    "goal_amount": null ou valor alvo em reais (para create_goal),
    "goal_deadline": null ou data limite formato "YYYY-MM-DD" (para create_goal),
    "goal_deposit": null ou valor a depositar na meta (para add_to_goal),
    "currency": null ou codigo ISO da moeda estrangeira (USD, EUR, GBP, KRW, HUF, etc.),
    "target_currency": null ou codigo ISO da moeda destino (para convert_currency, default BRL),
    "limit_type": null ou "daily_text_limit" ou "daily_media_limit" ou "daily_ai_limit",
    "daily_limit": null ou numero inteiro positivo
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
7. Inferir categoria quando nao especificada (ex: "almoco" -> Alimentação)
8. Inferir metodo de pagamento pelo contexto (ex: "no pix" -> Pix)
9. Para orcamentos: extraia categoria e limite (budget_limit) em reais
10. Frases como "definir limite", "orcamento de X reais", "limite de X para Y" indicam set_budget
11. Para export: se o usuario mencionar PDF, use export_format="pdf"; caso contrario use export_format="xlsx"
12. Para graficos: "grafico", "visualmente", "evolucao" indicam show_chart. Tipos: pie (pizza), bars (barras), line (linha/evolucao)
13. Para metas: "quero economizar", "meta de", "guardar X ate" indicam create_goal. Extraia descricao, valor e prazo
14. Para consultar meta: "como esta minha meta", "progresso da meta" indicam check_goal
15. Para depositar na meta: "depositar na meta", "guardar na meta", "adicionar a meta" indicam add_to_goal
16. Moedas estrangeiras: detecte dolares (USD), euros (EUR), libras (GBP), won coreano (KRW), florim hungaro (HUF), etc.
17. Se o usuario registrar gasto em moeda estrangeira ("gastei 50 dolares"), use register_expense com currency preenchido
18. Para conversao sem gasto ("quanto e 100 dolares", "converter 50 euros pra reais"), use convert_currency
19. Frases como "exporta meu backup", "fazer backup dos meus dados" indicam export_backup
20. Frases como "importar backup", "restaurar backup", "recuperar backup" indicam import_backup
21. Frases como "meus limites", "mostrar limites", "ver limites" indicam show_limits
22. Frases como "ajustar limite de ia para 30 por dia" indicam set_user_limit
23. Mapeamento de limites:
   - texto/mensagem -> daily_text_limit
   - midia/media/arquivo -> daily_media_limit
   - ia/ai -> daily_ai_limit

## Exemplos:

Entrada: "gastei 45 reais no almoco no pix"
Saida: {"intent": "register_expense", "data": {"description": "almoco", "amount": 45.00, "category": "Alimentação", "payment_method": "Pix", "installments": null, "is_shared": false, "shared_percentage": null, "recurring_day": null, "month": null, "year": null}, "confidence": 0.95}

Entrada: "comprei um tenis de 300 reais em 3x no cartao"
Saida: {"intent": "register_expense", "data": {"description": "tenis", "amount": 300.00, "category": "Vestuario", "payment_method": "Cartão de Crédito", "installments": 3, "is_shared": false, "shared_percentage": null, "recurring_day": null, "month": null, "year": null}, "confidence": 0.9}

Entrada: "netflix 55 reais todo mes dia 15"
Saida: {"intent": "register_recurring", "data": {"description": "netflix", "amount": 55.00, "category": "Assinatura", "payment_method": "Cartão de Crédito", "installments": null, "is_shared": false, "shared_percentage": null, "recurring_day": 15, "month": null, "year": null}, "confidence": 0.95}

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
Saida: {"intent": "export", "data": {"description": null, "amount": null, "category": null, "payment_method": null, "installments": null, "is_shared": false, "shared_percentage": null, "recurring_day": null, "month": 3, "year": null, "export_format": "xlsx"}, "confidence": 0.95}

Entrada: "exporta pdf de marco"
Saida: {"intent": "export", "data": {"description": null, "amount": null, "category": null, "payment_method": null, "installments": null, "is_shared": false, "shared_percentage": null, "recurring_day": null, "month": 3, "year": null, "export_format": "pdf"}, "confidence": 0.95}

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

Entrada: "definir limite alimentacao 500 reais"
Saida: {"intent": "set_budget", "data": {"category": "Alimentação", "budget_limit": 500.00}, "confidence": 0.95}

Entrada: "quero um orcamento de 1000 para lazer"
Saida: {"intent": "set_budget", "data": {"category": "Lazer", "budget_limit": 1000.00}, "confidence": 0.95}

Entrada: "limite de 2000 reais pra mercado"
Saida: {"intent": "set_budget", "data": {"category": "Mercado", "budget_limit": 2000.00}, "confidence": 0.95}

Entrada: "quanto tenho de orcamento?"
Saida: {"intent": "list_budgets", "data": {}, "confidence": 0.95}

Entrada: "como esta meu orcamento de alimentacao"
Saida: {"intent": "check_budget", "data": {"category": "Alimentação"}, "confidence": 0.95}

Entrada: "quais sao meus orcamentos"
Saida: {"intent": "list_budgets", "data": {}, "confidence": 0.95}

Entrada: "meus limites de gasto"
Saida: {"intent": "list_budgets", "data": {}, "confidence": 0.95}

Entrada: "remover orcamento de lazer"
Saida: {"intent": "remove_budget", "data": {"category": "Lazer"}, "confidence": 0.95}

Entrada: "tirar limite de alimentacao"
Saida: {"intent": "remove_budget", "data": {"category": "Alimentação"}, "confidence": 0.95}

Entrada: "mostra grafico de pizza"
Saida: {"intent": "show_chart", "data": {"chart_type": "pie", "month": null, "year": null}, "confidence": 0.95}

Entrada: "quero ver visualmente meus gastos"
Saida: {"intent": "show_chart", "data": {"chart_type": "pie", "month": null, "year": null}, "confidence": 0.95}

Entrada: "grafico dos maiores gastos"
Saida: {"intent": "show_chart", "data": {"chart_type": "bars", "month": null, "year": null}, "confidence": 0.95}

Entrada: "mostra grafico de barras"
Saida: {"intent": "show_chart", "data": {"chart_type": "bars", "month": null, "year": null}, "confidence": 0.95}

Entrada: "evolucao dos gastos"
Saida: {"intent": "show_chart", "data": {"chart_type": "line", "month": null, "year": null}, "confidence": 0.95}

Entrada: "grafico de linha dos gastos"
Saida: {"intent": "show_chart", "data": {"chart_type": "line", "month": null, "year": null}, "confidence": 0.95}

Entrada: "mostra meus gastos em grafico de marco"
Saida: {"intent": "show_chart", "data": {"chart_type": "pie", "month": 3, "year": null}, "confidence": 0.95}

Entrada: "quero economizar 1000 reais ate dezembro"
Saida: {"intent": "create_goal", "data": {"goal_description": "economia", "goal_amount": 1000.00, "goal_deadline": "2024-12-31"}, "confidence": 0.95}

Entrada: "criar meta de 5000 reais para viagem ate junho"
Saida: {"intent": "create_goal", "data": {"goal_description": "viagem", "goal_amount": 5000.00, "goal_deadline": "2025-06-30"}, "confidence": 0.95}

Entrada: "meta de guardar 2000 para o natal"
Saida: {"intent": "create_goal", "data": {"goal_description": "natal", "goal_amount": 2000.00, "goal_deadline": "2024-12-25"}, "confidence": 0.95}

Entrada: "como esta minha meta de viagem"
Saida: {"intent": "check_goal", "data": {"goal_description": "viagem"}, "confidence": 0.95}

Entrada: "quanto falta para minha meta"
Saida: {"intent": "check_goal", "data": {"goal_description": null}, "confidence": 0.90}

Entrada: "progresso da meta de economia"
Saida: {"intent": "check_goal", "data": {"goal_description": "economia"}, "confidence": 0.95}

Entrada: "quais sao minhas metas"
Saida: {"intent": "list_goals", "data": {}, "confidence": 0.95}

Entrada: "minhas metas de economia"
Saida: {"intent": "list_goals", "data": {}, "confidence": 0.95}

Entrada: "listar metas"
Saida: {"intent": "list_goals", "data": {}, "confidence": 0.95}

Entrada: "cancelar meta de viagem"
Saida: {"intent": "remove_goal", "data": {"goal_description": "viagem"}, "confidence": 0.95}

Entrada: "remover meta de carro"
Saida: {"intent": "remove_goal", "data": {"goal_description": "carro"}, "confidence": 0.95}

Entrada: "depositar 200 reais na meta de viagem"
Saida: {"intent": "add_to_goal", "data": {"goal_description": "viagem", "goal_deposit": 200.00}, "confidence": 0.95}

Entrada: "guardar 500 reais na economia"
Saida: {"intent": "add_to_goal", "data": {"goal_description": "economia", "goal_deposit": 500.00}, "confidence": 0.95}

Entrada: "adicionar 100 na meta"
Saida: {"intent": "add_to_goal", "data": {"goal_description": null, "goal_deposit": 100.00}, "confidence": 0.90}

Entrada: "gastei 50 dolares no uber"
Saida: {"intent": "register_expense", "data": {"description": "uber", "amount": 50.00, "category": "Transporte", "payment_method": "Pix", "installments": null, "is_shared": false, "shared_percentage": null, "recurring_day": null, "month": null, "year": null, "currency": "USD"}, "confidence": 0.95}

Entrada: "almoco de 30 euros"
Saida: {"intent": "register_expense", "data": {"description": "almoco", "amount": 30.00, "category": "Alimentação", "payment_method": "Pix", "installments": null, "is_shared": false, "shared_percentage": null, "recurring_day": null, "month": null, "year": null, "currency": "EUR"}, "confidence": 0.95}

Entrada: "quanto e 100 dolares em reais"
Saida: {"intent": "convert_currency", "data": {"amount": 100.00, "currency": "USD", "target_currency": "BRL"}, "confidence": 0.95}

Entrada: "converter 50 euros"
Saida: {"intent": "convert_currency", "data": {"amount": 50.00, "currency": "EUR", "target_currency": "BRL"}, "confidence": 0.95}

Entrada: "quanto vale 1000 won coreano"
Saida: {"intent": "convert_currency", "data": {"amount": 1000.00, "currency": "KRW", "target_currency": "BRL"}, "confidence": 0.95}

Entrada: "cotacao do dolar"
Saida: {"intent": "convert_currency", "data": {"amount": 1.00, "currency": "USD", "target_currency": "BRL"}, "confidence": 0.90}

Entrada: "exporta meu backup"
Saida: {"intent": "export_backup", "data": {}, "confidence": 0.95}

Entrada: "quero restaurar backup"
Saida: {"intent": "import_backup", "data": {}, "confidence": 0.95}

Entrada: "meus limites"
Saida: {"intent": "show_limits", "data": {}, "confidence": 0.95}

Entrada: "ajustar limite de ia para 30 por dia"
Saida: {"intent": "set_user_limit", "data": {"limit_type": "daily_ai_limit", "daily_limit": 30}, "confidence": 0.95}

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
Gastos: Alimentação, Assinatura, Imprevistos, Despesa Fixa, Educação, Emprestimo, Lazer, Mercado, Moradia, Outros, Parcelamento de Fatura, Presente, Saúde e Beleza, Servicos, Transferencia, Transporte, Vestuario, Viagem, Reserva de Emergencia, Investimento
Entradas: Salario - Adiantamento, Salario, Salario - 13o, Reembolso, Bonus, PLR, Vale Refeição, Vale Alimentação, Outros (entrada)

## Metodos de pagamento validos:
Cartão de Crédito, Cartão de Débito, Dinheiro, Pix, Vale Alimentação, Vale Refeição

Responda APENAS com o JSON, sem texto adicional.
"""

IMAGE_PROMPT = """Analise esta imagem de nota fiscal/cupom e extraia as informacoes.

## Categorias Disponiveis (use EXATAMENTE um destes nomes):
### Gastos: Alimentação, Assinatura, Imprevistos, Despesa Fixa, Educação, Emprestimo, Lazer, Mercado, Moradia, Outros, Parcelamento de Fatura, Presente, Saúde e Beleza, Servicos, Transferencia, Transporte, Vestuario, Viagem, Reserva de Emergencia, Investimento
### Entradas: Salario - Adiantamento, Salario, Salario - 13o, Reembolso, Bonus, PLR, Vale Refeição, Vale Alimentação, Outros (entrada)

## Meios de Pagamento (use EXATAMENTE um destes nomes):
- Cartão de Crédito
- Cartão de Débito
- Dinheiro
- Pix
- Vale Alimentação
- Vale Refeição

## Regras para inferir categoria pelo nome do estabelecimento:
- Bar, Pub, Fliperama, Cinema, Teatro, Boliche -> Lazer
- Restaurante, Lanchonete, Padaria, Cafe -> Alimentação
- Supermercado, Mercado, Hortifruti -> Mercado
- Farmacia, Drogaria, Clinica, Hospital -> Saúde e Beleza
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

PDF_PROMPT = """Analise o texto extraido de um comprovante ou nota fiscal em PDF e extraia as informacoes.

## Categorias Disponiveis (use EXATAMENTE um destes nomes):
### Gastos: Alimentação, Assinatura, Imprevistos, Despesa Fixa, Educação, Emprestimo, Lazer, Mercado, Moradia, Outros, Parcelamento de Fatura, Presente, Saúde e Beleza, Servicos, Transferencia, Transporte, Vestuario, Viagem, Reserva de Emergencia, Investimento
### Entradas: Salario - Adiantamento, Salario, Salario - 13o, Reembolso, Bonus, PLR, Vale Refeição, Vale Alimentação, Outros (entrada)

## Meios de Pagamento (use EXATAMENTE um destes nomes):
- Cartão de Crédito
- Cartão de Débito
- Dinheiro
- Pix
- Vale Alimentação
- Vale Refeição

## Sua tarefa:
- Identifique estabelecimento, valor total, categoria e meio de pagamento quando possivel
- Priorize o valor total pago no comprovante, nao subtotais intermediarios
- Se houver ambiguidade, faca a melhor inferencia a partir do texto

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

Se o texto nao for suficiente para identificar um gasto, retorne:
{"success": false, "error": "motivo"}

Responda APENAS com o JSON.
"""


class AIService:
    """Service for interacting with multiple AI providers with fallback support."""

    # Class-level tracking of exhausted models (shared across instances)
    _exhausted_models: dict[str, datetime] = {}
    _exhausted_timeout = timedelta(hours=1)  # Retry exhausted models after 1 hour

    def __init__(self) -> None:
        self.models = MODEL_FALLBACK_CHAIN
        self.vision_models = VISION_CAPABLE_MODELS
        self.groq_models = GROQ_MODEL_FALLBACK_CHAIN
        self.groq_vision_models = GROQ_VISION_MODEL_FALLBACK_CHAIN
        self.primary_provider = settings.normalized_ai_primary_provider
        self.gemini_api_key = settings.gemini_api_key
        self.groq_api_key = settings.groq_api_key
        self.credential_service = CredentialService()
        self.category_service = CategoryService()
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
            "credits",
            "rate_limit_exceeded",
        ]
        return any(indicator in error_str for indicator in quota_indicators)

    def _get_available_model(
        self,
        vision_only: bool = False,
        provider: str | None = None,
    ) -> str | None:
        """Get the next available model, skipping exhausted ones."""
        provider_chains = self._build_provider_chains(vision_only)
        if provider:
            model_list = [
                model_name
                for provider_name, model_name in provider_chains
                if provider_name == provider
            ]
        else:
            model_list = [model_name for _, model_name in provider_chains]
        now = datetime.now()

        # Clean up expired exhausted models
        expired = []
        for model_key, exhausted_at in self._exhausted_models.items():
            if now - exhausted_at > self._exhausted_timeout:
                expired.append(model_key)
        for model_key in expired:
            del self._exhausted_models[model_key]
            logger.info("Model %s is available again after timeout", model_key)

        # Find first available model
        for model in model_list:
            if (
                self._get_model_key(provider or self.primary_provider, model)
                not in self._exhausted_models
            ):
                return model

        # All models exhausted - try the first one anyway (might have reset)
        logger.warning("All models exhausted, trying first model in chain")
        return model_list[0] if model_list else None

    def _mark_model_exhausted(self, model_name: str, provider: str = "gemini") -> None:
        """Mark a model as exhausted."""
        model_key = self._get_model_key(provider, model_name)
        self._exhausted_models[model_key] = datetime.now()
        logger.warning("Model %s marked as exhausted (quota exceeded)", model_key)

    def _has_gemini(self, credentials: dict[str, str] | None = None) -> bool:
        """Whether the Gemini provider is configured."""
        if credentials is not None:
            return bool(credentials.get("gemini"))
        return bool(self.gemini_api_key)

    def _has_groq(self, credentials: dict[str, str] | None = None) -> bool:
        """Whether Groq is configured."""
        if credentials is not None:
            return bool(credentials.get("groq"))
        return bool(self.groq_api_key)

    def _get_model_key(self, provider: str, model_name: str) -> str:
        """Build a unique key for per-provider model exhaustion tracking."""
        return f"{provider}:{model_name}"

    def _build_provider_chains(
        self,
        vision_only: bool = False,
        credentials: dict[str, str] | None = None,
    ) -> list[tuple[str, str]]:
        """Build the ordered provider/model fallback chain."""
        gemini_models = self.vision_models if vision_only else self.models
        groq_models = self.groq_vision_models if vision_only else self.groq_models

        ordered_providers = (
            ["groq", "gemini"] if self.primary_provider == "groq" else ["gemini", "groq"]
        )

        provider_chains: list[tuple[str, str]] = []
        for provider in ordered_providers:
            if provider == "gemini" and self._has_gemini(credentials):
                provider_chains.extend(("gemini", model_name) for model_name in gemini_models)
            if provider == "groq" and self._has_groq(credentials):
                provider_chains.extend(("groq", model_name) for model_name in groq_models)
        return provider_chains

    def _generate_content_sync(
        self,
        model_name: str,
        contents: list,
        generation_config: genai.GenerationConfig,
        api_key: str,
    ) -> Any:
        """Run the blocking Gemini SDK call synchronously."""
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        return model.generate_content(
            contents,
            generation_config=generation_config,
        )

    async def _generate_content_groq(
        self,
        model_name: str,
        contents: list[Any],
        generation_config: genai.GenerationConfig,
        api_key: str,
    ) -> str:
        """Generate content using Groq's OpenAI-compatible chat API."""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": self._build_groq_messages(contents),
            "temperature": getattr(generation_config, "temperature", 0.1),
        }
        if getattr(generation_config, "response_mime_type", "") == "application/json":
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=settings.effective_ai_timeout_seconds) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        return str(data["choices"][0]["message"]["content"])

    def _build_groq_messages(self, contents: list[Any]) -> list[dict[str, Any]]:
        """Convert the existing content shape into Groq chat-completions messages."""
        if not contents:
            return []

        messages: list[dict[str, Any]] = []
        content_parts = list(contents)

        if len(content_parts) > 1 and isinstance(content_parts[0], str):
            messages.append({"role": "system", "content": content_parts[0]})
            content_parts = content_parts[1:]

        user_content: list[dict[str, Any]] = []
        for item in content_parts:
            if isinstance(item, str):
                user_content.append({"type": "text", "text": item})
                continue

            if isinstance(item, dict) and {"mime_type", "data"} <= set(item):
                data_url = f"data:{item['mime_type']};base64,{item['data']}"
                user_content.append({"type": "image_url", "image_url": {"url": data_url}})

        if user_content:
            messages.append({"role": "user", "content": user_content})

        return messages

    async def _generate_with_fallback(
        self,
        contents: list[Any],
        generation_config: genai.GenerationConfig,
        vision_only: bool = False,
        credentials: dict[str, str] | None = None,
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
        provider_chain = self._build_provider_chains(vision_only, credentials)
        last_error = None

        for provider_name, model_name in provider_chain:
            # Skip exhausted models
            if self._get_model_key(provider_name, model_name) in self._exhausted_models:
                continue

            try:
                logger.debug("Trying model %s via provider %s", model_name, provider_name)

                if provider_name == "gemini":
                    gemini_key = (credentials or {}).get("gemini", self.gemini_api_key)
                    response = await asyncio.wait_for(
                        asyncio.to_thread(
                            self._generate_content_sync,
                            model_name,
                            contents,
                            generation_config,
                            gemini_key,
                        ),
                        timeout=settings.effective_ai_timeout_seconds,
                    )
                    logger.info("Successfully used model: %s via Gemini", model_name)
                    return response.text

                groq_key = (credentials or {}).get("groq", self.groq_api_key)
                response_text = await self._generate_content_groq(
                    model_name,
                    contents,
                    generation_config,
                    groq_key,
                )
                logger.info("Successfully used model: %s via Groq", model_name)
                return response_text

            except TimeoutError as e:
                last_error = e
                logger.warning(
                    "Model %s via %s timed out after %ss, trying next model",
                    model_name,
                    provider_name,
                    settings.effective_ai_timeout_seconds,
                )
                continue
            except Exception as e:
                last_error = e
                if self._is_quota_error(e):
                    self._mark_model_exhausted(model_name, provider=provider_name)
                    logger.warning(
                        "Quota exceeded for %s via %s, trying next model",
                        model_name,
                        provider_name,
                    )
                    continue
                else:
                    # Non-quota error, re-raise
                    raise

        # All models failed
        if last_error:
            raise last_error
        raise Exception("No AI models available")

    async def _resolve_provider_credentials(self, user: User | None = None) -> dict[str, str]:
        """Resolve effective provider credentials for the current user."""
        return await self.credential_service.resolve_many(["gemini", "groq"], user=user)

    async def _build_dynamic_category_context(self, user: User | None = None) -> str:
        """Build the active category catalog section for the current user."""
        if user is None:
            return ""

        try:
            async with async_session() as session:
                grouped = await self.category_service.get_active_category_names(session, user)
        except Exception as exc:
            logger.warning("Could not load user category context for AI prompts: %s", exc)
            return ""

        negatives = grouped.get("Negativo", [])
        positives = grouped.get("Positivo", [])

        lines = [
            "## Catalogo ativo deste usuario (priorize APENAS estas categorias):",
            "### Gastos (Negativo):",
        ]
        lines.extend(f"- {name}" for name in negatives)
        lines.append("\n### Entradas (Positivo):")
        lines.extend(f"- {name}" for name in positives)
        lines.append(
            "\nUse somente categorias desta secao ao classificar, ajustar ou listar categorias."
        )
        return "\n".join(lines)

    async def process_message(self, text: str, user: User | None = None) -> dict:
        """Process text message and extract intent/data."""
        try:
            provider_credentials = await self._resolve_provider_credentials(user)
            dynamic_category_context = await self._build_dynamic_category_context(user)
            # Add current date context
            today = date.today()
            context = f"Data atual: {today.strftime('%d/%m/%Y')}\n\nMensagem do usuario: {text}"
            prompt = SYSTEM_PROMPT
            if dynamic_category_context:
                prompt = f"{SYSTEM_PROMPT}\n\n{dynamic_category_context}"

            response_text = await self._generate_with_fallback(
                contents=[prompt, context],
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
                credentials=provider_credentials,
            )

            # Parse JSON response
            result = json.loads(response_text)
            logger.debug(f"AI response: {result}")

            if result.get("intent") == "export":
                export_data = result.setdefault("data", {})
                export_format = export_data.get("export_format")
                if export_format not in {"xlsx", "pdf"}:
                    export_data["export_format"] = "xlsx"

            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response: {e}")
            return {"intent": "unknown", "data": {}, "confidence": 0}
        except Exception as e:
            logger.error(f"AI provider error: {e}")
            return {"intent": "unknown", "data": {}, "confidence": 0}

    async def process_image(
        self,
        image_data: bytes,
        additional_text: str = "",
        user: User | None = None,
    ) -> dict:
        """Process image (receipt/invoice) and extract data."""
        try:
            provider_credentials = await self._resolve_provider_credentials(user)
            dynamic_category_context = await self._build_dynamic_category_context(user)
            # Encode image to base64
            image_b64 = base64.b64encode(image_data).decode("utf-8")

            # Create image part
            image_part = {
                "mime_type": "image/jpeg",
                "data": image_b64,
            }

            # Add context if there's additional text
            prompt = IMAGE_PROMPT
            if dynamic_category_context:
                prompt += f"\n\n{dynamic_category_context}"
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
                credentials=provider_credentials,
            )

            result = json.loads(response_text)
            logger.debug(f"AI vision response: {result}")

            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI vision response: {e}")
            return {"success": False, "error": "Falha ao interpretar resposta"}
        except Exception as e:
            logger.error(f"AI vision error: {e}")
            return {"success": False, "error": str(e)}

    async def process_pdf_text(
        self,
        pdf_text: str,
        additional_text: str = "",
        user: User | None = None,
    ) -> dict:
        """Process extracted PDF text and infer expense data."""
        try:
            provider_credentials = await self._resolve_provider_credentials(user)
            dynamic_category_context = await self._build_dynamic_category_context(user)
            text_excerpt = pdf_text[:12000]
            prompt = PDF_PROMPT + f"\n\nTexto extraido do PDF:\n{text_excerpt}"
            if dynamic_category_context:
                prompt += f"\n\n{dynamic_category_context}"
            if additional_text:
                prompt += f"\n\nTexto adicional do usuario: {additional_text}"

            response_text = await self._generate_with_fallback(
                contents=[prompt],
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
                credentials=provider_credentials,
            )

            result = json.loads(response_text)
            logger.debug(f"AI PDF response: {result}")

            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI PDF response: {e}")
            return {"success": False, "error": "Falha ao interpretar comprovante em PDF"}
        except Exception as e:
            logger.error(f"AI PDF error: {e}")
            return {"success": False, "error": str(e)}

    async def evaluate_confirmation_response(
        self,
        expense_summary: str,
        user_response: str,
        user: User | None = None,
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
            logger.info(f"Using AI provider for confirmation evaluation: '{user_response}'")
            dynamic_category_context = await self._build_dynamic_category_context(user)
            prompt = CONFIRMATION_PROMPT.format(
                expense_summary=expense_summary,
                user_response=user_response,
            )
            if dynamic_category_context:
                prompt = f"{prompt}\n\n{dynamic_category_context}"
            provider_credentials = await self._resolve_provider_credentials(user)

            response_text = await self._generate_with_fallback(
                contents=[prompt],
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
                credentials=provider_credentials,
            )

            logger.debug(f"AI confirmation raw response: {response_text}")
            result = json.loads(response_text)
            logger.info(f"AI confirmation evaluation: action={result.get('action')}")

            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI confirmation response: {e}")
            # Default to asking for clarification
            return {"action": "unknown", "adjustments": {}, "confidence": 0}
        except Exception as e:
            logger.error(f"AI confirmation error: {e}")
            return {"action": "unknown", "adjustments": {}, "confidence": 0}

    def get_model_status(self) -> dict:
        """Get current status of all models (for debugging/monitoring)."""
        now = datetime.now()
        status = {}
        all_models = [("gemini", model) for model in self.models] + [
            ("groq", model) for model in self.groq_models
        ]
        for provider_name, model in all_models:
            model_key = self._get_model_key(provider_name, model)
            status_key = f"{provider_name}:{model}"
            if model_key in self._exhausted_models:
                exhausted_at = self._exhausted_models[model_key]
                time_remaining = self._exhausted_timeout - (now - exhausted_at)
                status[status_key] = {
                    "available": False,
                    "exhausted_at": exhausted_at.isoformat(),
                    "available_in": str(time_remaining)
                    if time_remaining.total_seconds() > 0
                    else "soon",
                }
            else:
                status[status_key] = {"available": True}
        return status

    def format_budget_alert(self, alert: dict) -> str:
        """
        Format a budget alert message in a friendly way.

        Args:
            alert: Dict with threshold, category, spent, limit, percentage, exceeded

        Returns:
            Formatted alert message
        """
        threshold = alert["threshold"]
        category = alert["category"]
        spent = alert["spent"]
        limit = alert["limit"]
        percentage = alert["percentage"]
        exceeded = alert.get("exceeded", False)

        if exceeded:
            excess = spent - limit
            return (
                f"🚨 *Limite atingido!*\n\n"
                f"Voce excedeu o orcamento de *{category}* em R$ {excess:.2f}.\n"
                f"Limite: R$ {limit:.2f}\n"
                f"Gasto: R$ {spent:.2f} ({percentage:.0f}%)"
            )
        elif threshold == 80:
            return (
                f"⚠️ *Cuidado!*\n\n"
                f"Voce ja gastou *{percentage:.0f}%* do orcamento de *{category}*.\n"
                f"Limite: R$ {limit:.2f}\n"
                f"Gasto: R$ {spent:.2f}\n"
                f"Restante: R$ {(limit - spent):.2f}"
            )
        else:  # 50%
            return (
                f"📊 *Aviso de orcamento*\n\n"
                f"Voce ja gastou *{percentage:.0f}%* do orcamento de *{category}*.\n"
                f"Limite: R$ {limit:.2f}\n"
                f"Gasto: R$ {spent:.2f}\n"
                f"Restante: R$ {(limit - spent):.2f}"
            )

    def format_goal_motivation(self, progress: dict) -> str:
        """
        Format a motivational message for goal progress.

        Args:
            progress: Dict with goal progress details containing:
                - description: goal description
                - target_amount: target amount
                - current_progress: current progress amount
                - percentage: progress percentage
                - remaining_amount: amount remaining
                - remaining_days: days until deadline
                - daily_rate_needed: daily savings needed
                - is_on_track: whether user is on track
                - is_achieved: whether goal is achieved

        Returns:
            Formatted motivational message
        """
        percentage = progress["percentage"]
        description = progress["description"]
        remaining = progress["remaining_amount"]
        remaining_days = progress["remaining_days"]
        is_on_track = progress.get("is_on_track", True)
        is_achieved = progress.get("is_achieved", False)
        daily_rate = progress.get("daily_rate_needed", 0)

        if is_achieved or percentage >= 100:
            return (
                f"🎉 *Parabens!* Voce atingiu sua meta de *{description}*!\n\n"
                f"Meta: R$ {progress['target_amount']:.2f}\n"
                f"Economizado: R$ {progress['current_progress']:.2f}\n\n"
                f"Continue assim! 💪"
            )
        elif percentage >= 75:
            return (
                f"🌟 *Quase la!* Voce esta com {percentage:.0f}% da meta de *{description}*!\n\n"
                f"Faltam apenas R$ {remaining:.2f}\n"
                f"Restam {remaining_days} dias\n\n"
                f"Voce esta muito perto do objetivo! 🚀"
            )
        elif percentage >= 50:
            return (
                f"💪 *Metade do caminho!* Voce esta com {percentage:.0f}% da meta de *{description}*.\n\n"
                f"Economizado: R$ {progress['current_progress']:.2f}\n"
                f"Faltam: R$ {remaining:.2f}\n"
                f"Restam {remaining_days} dias\n\n"
                f"Continue firme! 🎯"
            )
        elif percentage >= 25:
            emoji = "✅" if is_on_track else "⚠️"
            track_msg = (
                "Voce esta no caminho certo!"
                if is_on_track
                else f"Tente economizar R$ {daily_rate:.2f} por dia."
            )
            return (
                f"{emoji} *Progresso da meta {description}:* {percentage:.0f}%\n\n"
                f"Economizado: R$ {progress['current_progress']:.2f}\n"
                f"Meta: R$ {progress['target_amount']:.2f}\n"
                f"Restam {remaining_days} dias\n\n"
                f"{track_msg}"
            )
        else:
            return (
                f"📊 *Meta {description}:* {percentage:.0f}% concluido\n\n"
                f"Economizado: R$ {progress['current_progress']:.2f}\n"
                f"Meta: R$ {progress['target_amount']:.2f}\n"
                f"Restam {remaining_days} dias\n\n"
                f"Cada real conta! Tente economizar R$ {daily_rate:.2f} por dia. 💰"
            )
