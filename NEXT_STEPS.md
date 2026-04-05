# Roadmap FinBot - Próximas Evoluções

Este documento descreve as futuras evoluções do **FinBot**, organizadas em fases de implementação com dependências claras.

---

## Estado Atual (Já Implementado)

Antes de planejar novos recursos, é importante reconhecer o que já existe:

| Funcionalidade | Status | Arquivo |
|----------------|--------|---------|
| Exportação XLSX | ✅ Completo | `app/services/export.py` |
| Modelo de despesas recorrentes | ✅ Completo | `app/database/models.py` |
| Serviço de recorrentes | ✅ Completo | `app/services/recurring.py` |
| Scheduler de recorrentes | ✅ Completo | `app/services/scheduler.py` |
| Suporte multi-usuário | ⚠️ Parcial (estrutura existe) | Campo `user_phone` nos models |
| Testes unitários | ✅ Completo (148 testes) | `tests/` |
| Gráficos no WhatsApp | ✅ Completo | `app/services/chart.py` |
| Desfazer última ação | ✅ Completo | `app/services/expense.py` |
| Pipeline CI/CD | ✅ Completo | `.github/workflows/ci.yml` |
| Alertas e Limites de Orçamento | ✅ Completo | `app/services/budget.py` |

---

## Fase 1: Fundação e Qualidade (Prioridade Crítica)

### 1.1 Pipeline de CI/CD e Testes ✅
- **Complexidade:** Baixa 🟢
- **Valor:** Alto (previne regressões nas próximas fases)
- **Status:** Implementado
- **Implementação:**
  - ✅ 112 testes cobrindo `ExpenseService`, `GeminiService`, `ExportService`, `WebhookHandler` e `BudgetService`
  - ✅ Mocks para APIs externas (Gemini, Evolution API)
  - ✅ GitHub Actions com: `pytest`, `ruff`, `mypy`
  - ✅ Testes de integração para webhook
  - ✅ Cobertura de 57% (expense: 81%, export: 99%, gemini: 83%)
- **Arquivos:**
  - `.github/workflows/ci.yml` - Pipeline GitHub Actions
  - `pyproject.toml` - Configurações ruff, mypy, pytest
  - `tests/conftest.py` - Fixtures compartilhadas
  - `tests/test_expense.py` - Testes ExpenseService
  - `tests/test_gemini.py` - Testes GeminiService
  - `tests/test_export.py` - Testes ExportService
  - `tests/test_webhook.py` - Testes integração

### 1.2 Desfazer Última Ação ✅
- **Complexidade:** Baixa 🟢
- **Valor:** Alto (UX crítica - erros são comuns)
- **Status:** Implementado
- **Implementação:**
  - Hard-delete da última transação (verifica `created_at` no banco)
  - Comandos: "desfaz", "apaga o último", "cancela o último gasto", "errei, remove"
  - Limite de tempo: 5 minutos
  - Arquivos: `app/services/expense.py`, `app/handlers/webhook.py`, `app/services/gemini.py`

---

## Fase 2: Inteligência Financeira (Prioridade Alta)

### 2.1 Alertas e Limites de Orçamento ✅
- **Complexidade:** Média 🟡
- **Valor:** Muito Alto (diferencial competitivo)
- **Status:** Implementado
- **Implementação:**
  - ✅ Novas tabelas: `budgets` e `budget_alerts` para rastrear alertas enviados
  - ✅ `BudgetService` com: create, remove, list, check_status, check_and_send_alerts
  - ✅ Gatilhos de alerta em 50%, 80%, 100% do limite (sem duplicatas no mesmo mês)
  - ✅ Alertas automáticos após confirmação de gasto
  - ✅ Integração com Gemini para novos intents: set_budget, check_budget, list_budgets, remove_budget
  - ✅ 17 testes específicos para funcionalidade de orçamento
- **Comandos:**
  - "definir limite alimentação 500 reais" → cria/atualiza orçamento
  - "quanto tenho de orçamento?" → lista todos os orçamentos
  - "como está meu orçamento de alimentação" → status específico
  - "remover orçamento de lazer" → desativa orçamento
- **Arquivos:**
  - `app/database/models.py` - Models Budget e BudgetAlert
  - `app/services/budget.py` - BudgetService completo
  - `app/services/gemini.py` - Novos intents e format_budget_alert()
  - `app/handlers/webhook.py` - Handlers de orçamento e integração com alertas
  - `tests/test_budget.py` - Testes unitários

### 2.2 Ativação do Scheduler de Recorrentes ✅
- **Complexidade:** Baixa 🟢
- **Valor:** Alto (infraestrutura já existe)
- **Status:** Implementado
- **Implementação:**
  - ✅ `APScheduler` integrado ao FastAPI (job diário configurável)
  - ✅ `SchedulerService` com: start, shutdown, process_recurring_job
  - ✅ Modo de confirmação: pergunta ao usuário antes de lançar
  - ✅ Mensagem: "Despesas recorrentes de hoje: ... Já pagou? Responda sim ou não"
  - ✅ Handler de confirmação no webhook para processar respostas
  - ✅ Integração com alertas de orçamento após lançar despesas
  - ✅ 12 testes específicos para funcionalidade de scheduler
- **Configurações (`.env`):**
  - `SCHEDULER_ENABLED=true` - Habilita/desabilita
  - `SCHEDULER_TIMEZONE=America/Sao_Paulo` - Fuso horário
  - `SCHEDULER_HOUR=8` / `SCHEDULER_MINUTE=0` - Horário do job
- **Arquivos:**
  - `app/services/scheduler.py` - SchedulerService completo
  - `app/handlers/webhook.py` - Handler _handle_recurring_confirmation
  - `app/main.py` - Integração no lifespan (start/shutdown)
  - `app/config.py` - Configurações do scheduler
  - `tests/test_scheduler.py` - Testes unitários

---

## Fase 3: Visualização e Relatórios (Prioridade Alta)

### 3.1 Gráficos no WhatsApp ✅
- **Complexidade:** Média 🟡
- **Valor:** Alto (impacto visual)
- **Status:** Implementado
- **Implementação:**
  - ✅ `matplotlib` com backend `Agg` (sem GUI)
  - ✅ `ChartService` com 3 tipos de gráficos:
    - Pizza: distribuição por categoria
    - Barras horizontais: top 10 gastos do mês
    - Linha: evolução diária com acumulado
  - ✅ Envio via Evolution API (media message com PNG)
  - ✅ Novo intent `show_chart` no GeminiService
  - ✅ 24 testes unitários para ChartService
- **Comandos:**
  - "mostra gráfico de pizza" → distribuição por categoria
  - "gráfico de barras" → maiores gastos
  - "evolução dos gastos" → gráfico de linha
  - "gráfico de março" → gráficos de mês específico
- **Arquivos:**
  - `app/services/chart.py` - ChartService completo
  - `app/services/evolution.py` - método send_image()
  - `app/services/expense.py` - métodos get_expenses_by_category, get_top_expenses, get_daily_totals
  - `app/services/gemini.py` - intent show_chart
  - `app/handlers/webhook.py` - handler handle_show_chart
  - `tests/test_chart.py` - testes unitários

### 3.2 Exportação PDF
- **Complexidade:** Baixa 🟢
- **Valor:** Médio (complementa XLSX existente)
- **Orientações:**
  - Usar `ReportLab` ou `WeasyPrint`
  - Reaproveitar dados de `ExportService.export_month()`
  - Incluir gráfico de pizza no PDF
  - Comando: "exporta pdf de março"

---

## Fase 4: Funcionalidades Avançadas (Prioridade Média)

### 4.1 Metas de Economia ✅
- **Complexidade:** Média 🟡
- **Valor:** Médio-Alto
- **Status:** Implementado
- **Implementação:**
  - ✅ Novas tabelas: `goals` e `goal_updates` para rastrear progresso
  - ✅ `GoalService` com: create, remove, list, check_progress, add_to_goal
  - ✅ Cálculo de progresso baseado em (entradas - gastos) do período + depósitos manuais
  - ✅ Fluxo de confirmação antes de criar meta
  - ✅ Integração com Gemini para novos intents: create_goal, check_goal, list_goals, remove_goal, add_to_goal
  - ✅ Job semanal (domingo 10h) para enviar mensagens motivacionais
  - ✅ 24 testes específicos para funcionalidade de metas
- **Comandos:**
  - "quero economizar 1000 reais ate dezembro" → cria meta
  - "como esta minha meta de viagem" → verifica progresso
  - "minhas metas" → lista todas as metas
  - "depositar 200 reais na meta de viagem" → depósito manual
  - "cancelar meta de viagem" → remove meta
- **Arquivos:**
  - `app/database/models.py` - Models Goal e GoalUpdate
  - `app/services/goal.py` - GoalService completo
  - `app/services/gemini.py` - Novos intents e format_goal_motivation()
  - `app/services/scheduler.py` - Job semanal de motivação
  - `app/handlers/webhook.py` - Handlers de metas e confirmação
  - `tests/test_goal.py` - Testes unitários

### 4.2 Conversão de Moeda
- **Complexidade:** Baixa 🟢
- **Valor:** Médio (nicho: viajantes)
- **Orientações:**
  - Integrar API gratuita de câmbio (exchangerate-api.com)
  - Detectar moeda na mensagem: "gastei 50 dólares"
  - Converter para BRL e armazenar ambos valores
  - Cache de cotação por 1 hora

---

## Fase 5: Escalabilidade (Prioridade Baixa)

### 5.1 Onboarding Multi-Usuários
- **Complexidade:** Alta 🔴
- **Valor:** Alto (mas estrutura já suporta)
- **Orientações:**
  - Remover `OWNER_PHONE` do `.env`
  - Criar tabela `users` com: phone, name, created_at, accepted_terms
  - Fluxo de primeiro contato: "Olá! Aceita os termos de uso?"
  - Rate limiting por usuário (evitar abuso)
  - **Nota:** Modelo atual já usa `user_phone` em todas as queries

### 5.2 Backup e Restauração
- **Complexidade:** Média 🟡
- **Valor:** Médio
- **Orientações:**
  - Exportar todos os dados do usuário em JSON
  - Comando: "exporta meu backup"
  - Permitir importação (com validação rigorosa)
  - Útil para migração de número de telefone

---

## Matriz de Priorização

| Item | Valor | Complexidade | Dependências | Score |
|------|-------|--------------|--------------|-------|
| ~~CI/CD e Testes~~ | ✅ | 🟢 | Nenhuma | ⭐⭐⭐⭐⭐ |
| ~~Desfazer Ação~~ | ✅ | 🟢 | Nenhuma | ⭐⭐⭐⭐⭐ |
| ~~Alertas/Limites~~ | ✅ | 🟡 | ~~Testes~~ ✅ | ⭐⭐⭐⭐ |
| ~~Scheduler Recorrentes~~ | ✅ | 🟢 | Nenhuma | ⭐⭐⭐⭐ |
| ~~Gráficos~~ | ✅ | 🟡 | Nenhuma | ⭐⭐⭐⭐ |
| PDF Export | 🟡 | 🟢 | XLSX (existe) | ⭐⭐⭐ |
| ~~Metas~~ | ✅ | 🟡 | ~~Alertas~~ ✅ | ⭐⭐⭐ |
| Conversão Moeda | 🟡 | 🟢 | Nenhuma | ⭐⭐⭐ |
| Multi-Usuários | 🔴 | 🔴 | ~~Testes, CI~~ ✅ | ⭐⭐ |
| Backup | 🟡 | 🟡 | Multi-usuários | ⭐⭐ |

---

## Ordem de Implementação Sugerida

1. **Sprint 1:** ~~CI/CD + Testes~~ ✅ + ~~Desfazer Ação~~ ✅
2. **Sprint 2:** ~~Alertas/Limites~~ ✅ + ~~Scheduler Recorrentes~~ ✅
3. **Sprint 3:** ~~Gráficos~~ ✅ + ~~Metas~~ ✅
4. **Sprint 4:** PDF + Conversão Moeda ⬅️ **PRÓXIMO**
5. **Sprint 5:** Multi-Usuários + Backup
