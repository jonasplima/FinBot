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

### 3.2 Exportação PDF ✅
- **Complexidade:** Baixa 🟢
- **Valor:** Médio (complementa XLSX existente)
- **Status:** Implementado
- **Implementação:**
  - ✅ `ReportLab` integrado para geração de PDFs no backend
  - ✅ Reaproveitamento dos dados de `ExportService.export_month()`
  - ✅ Inclusão de gráfico de pizza no PDF via `ChartService`
  - ✅ Exportação padrão continua em XLSX; PDF apenas quando solicitado
  - ✅ Integração com Gemini para extrair `export_format` (`xlsx` ou `pdf`)
  - ✅ Envio via Evolution API com `mimetype` correto para PDF
  - ✅ 5 testes adicionais cobrindo PDF e roteamento no webhook
- **Comandos:**
  - "exportar meus gastos de março" → envia XLSX
  - "exporta pdf de março" → envia PDF
- **Arquivos:**
  - `app/services/export.py` - geração de XLSX e PDF
  - `app/services/gemini.py` - extração de `export_format`
  - `app/handlers/webhook.py` - roteamento por formato de exportação
  - `tests/test_export.py` - testes da exportação PDF
  - `tests/test_webhook.py` - testes do fluxo de envio

### 3.3 Leitura de Comprovantes em PDF ✅
- **Complexidade:** Média 🟡
- **Valor:** Alto (expande captura de gastos para um formato comum no WhatsApp)
- **Status:** Implementado
- **Implementação:**
  - ✅ Detecção de `documentMessage` no webhook da Evolution API
  - ✅ Identificação de `mimetype` e roteamento específico para `application/pdf`
  - ✅ Reaproveitamento de `download_media()` para baixar o documento recebido
  - ✅ Extração de texto de PDFs com `pypdf`
  - ✅ Novo método no Gemini para interpretar texto extraído de comprovantes em PDF
  - ✅ Reaproveitamento do fluxo existente de confirmação em `handle_register_expense`
  - ✅ Mensagens amigáveis para PDF sem texto extraível ou não interpretado
  - ✅ 7 testes adicionais cobrindo Gemini, webhook e extração de metadados da Evolution API
- **Critérios de Aceite:**
  - Usuário envia PDF com comprovante e o sistema extrai dados básicos do gasto
  - O fluxo segue para confirmação antes de persistir no banco
  - PDF sem texto útil retorna orientação amigável
  - Imagens continuam funcionando como hoje
- **Arquivos:**
  - `app/services/evolution.py` - detecção de documentos e metadados do anexo
  - `app/handlers/webhook.py` - fluxo de processamento de PDF recebido e extração de texto
  - `app/services/gemini.py` - método de interpretação para texto extraído de PDF
  - `tests/test_evolution.py` - testes da extração de documentos no webhook
  - `tests/test_webhook.py` - testes de roteamento e processamento de PDF
  - `tests/test_gemini.py` - testes do fluxo de interpretação

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

### 4.2 Conversão de Moeda ✅
- **Complexidade:** Baixa 🟢
- **Valor:** Médio (nicho: viajantes)
- **Status:** Implementado
- **Implementação:**
  - ✅ `CurrencyService` com cache de 1 hora para cotações
  - ✅ **API Wise como fonte primária** (cotação comercial + valor real com taxas)
    - GET /v1/rates - Cotação comercial (mid-market)
    - POST /v3/quotes - Valor real que chega após IOF e taxas Wise
  - ✅ Fallback para exchangerate-api.com
  - ✅ Fallback final para arquivo JSON (atualizado semanalmente)
  - ✅ Detecção automática de moeda na mensagem: "gastei 50 dólares"
  - ✅ Conversão para BRL e armazenamento de ambos valores no banco
  - ✅ Serviço standalone para consultas: "quanto é 100 dólares em reais"
  - ✅ 12 moedas suportadas: USD, EUR, GBP, KRW, HUF, ARS, JPY, CAD, AUD, CHF, CNY, MXN
  - ✅ 31 testes unitários para funcionalidade de conversão
- **Comandos:**
  - "gastei 50 dolares no uber" → registra gasto convertendo para BRL
  - "almoco de 30 euros" → registra gasto com conversão
  - "quanto e 100 dolares em reais" → apenas consulta cotação (mostra cotação comercial + valor Wise)
  - "converter 50 euros" → consulta cotação
  - "cotacao do dolar" → mostra cotação atual
- **Configurações (`.env`):**
  - `WISE_API_KEY` - Chave da API Wise (primária, criar em https://wise.com/your-account/integrations-and-tools/api-tokens)
  - `EXCHANGE_RATE_API_KEY` - Chave da API ExchangeRate (fallback)
  - `EXCHANGE_RATE_CACHE_TTL=3600` - Tempo de cache em segundos
  - `FALLBACK_RATES_FILE=data/fallback_rates.json` - Arquivo de taxas de fallback
  - `FALLBACK_RATES_UPDATE_DAYS=7` - Intervalo de atualização do fallback (dias)
- **Arquivos:**
  - `app/config.py` - Configurações das APIs de câmbio (Wise + ExchangeRate)
  - `app/database/models.py` - Campos original_currency, original_amount, exchange_rate
  - `app/services/currency.py` - CurrencyService com Wise API + fallbacks
  - `app/services/gemini.py` - Intent convert_currency e detecção de moeda
  - `app/handlers/webhook.py` - Handlers de conversão e integração com registro
  - `data/fallback_rates.json` - Taxas de fallback (gerado automaticamente)
  - `tests/test_currency.py` - Testes unitários

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
- **Status:** Implementado
- **Implementação:**
  - ✅ `BackupService` dedicado para exportação e restauração de dados do usuário
  - ✅ Schema JSON versionado com `metadata`, `schema_version`, `exported_at` e `source_phone`
  - ✅ Exportação de `expenses`, `budgets`, `budget_alerts`, `goals` e `goal_updates`
  - ✅ Serialização segura de `Decimal`, `date` e `datetime`
  - ✅ Envio do backup como arquivo `.json` via WhatsApp
  - ✅ Importação de backup JSON recebido como documento
  - ✅ Validação rigorosa antes da restauração
  - ✅ Restauração em modo `append` com transação única e rollback total em caso de erro
  - ✅ Recriação de referências por nomes de categoria e meio de pagamento
  - ✅ Confirmação antes de iniciar a restauração
  - ✅ Deduplicação básica para evitar reimportação acidental dos mesmos registros
  - ✅ 11 testes adicionais cobrindo exportação, parsing, restauração, rollback e fluxo de webhook
- **Decisões da V1:**
  - `append` como estratégia padrão de restauração
  - `categories` e `payment_methods` continuam sendo catálogos do sistema, não parte do backup
  - `pending_confirmations` ficam fora da restauração inicial por serem dados transitórios
- **Critérios de Aceite:**
  - Usuário consegue exportar seus dados em JSON via WhatsApp
  - Usuário consegue importar um backup JSON válido com confirmação prévia
  - Restore inválido falha com mensagem clara e sem gravar dados parciais
  - Relações entre metas, atualizações, orçamentos e alertas permanecem consistentes após restauração
- **Arquivos:**
  - `app/services/backup.py` - serviço de exportação e restauração
  - `app/services/gemini.py` - intents de exportar/importar backup
  - `app/handlers/webhook.py` - fluxo de envio e recebimento de backup JSON
  - `app/services/evolution.py` - reaproveitamento do tratamento de documentos recebidos
  - `tests/test_backup.py` - testes do serviço de backup
  - `tests/test_webhook.py` - testes do fluxo de import/export via WhatsApp
  - `tests/test_gemini.py` - testes das novas intents

---

## Matriz de Priorização

| Item | Valor | Complexidade | Dependências | Score |
|------|-------|--------------|--------------|-------|
| ~~CI/CD e Testes~~ | ✅ | 🟢 | Nenhuma | ⭐⭐⭐⭐⭐ |
| ~~Desfazer Ação~~ | ✅ | 🟢 | Nenhuma | ⭐⭐⭐⭐⭐ |
| ~~Alertas/Limites~~ | ✅ | 🟡 | ~~Testes~~ ✅ | ⭐⭐⭐⭐ |
| ~~Scheduler Recorrentes~~ | ✅ | 🟢 | Nenhuma | ⭐⭐⭐⭐ |
| ~~Gráficos~~ | ✅ | 🟡 | Nenhuma | ⭐⭐⭐⭐ |
| ~~PDF Export~~ | ✅ | 🟢 | XLSX (existe) | ⭐⭐⭐ |
| ~~Leitura PDF~~ | ✅ | 🟡 | Webhook de mídia (existe) | ⭐⭐⭐ |
| ~~Metas~~ | ✅ | 🟡 | ~~Alertas~~ ✅ | ⭐⭐⭐ |
| ~~Conversão Moeda~~ | ✅ | 🟢 | Nenhuma | ⭐⭐⭐ |
| Multi-Usuários | 🔴 | 🔴 | ~~Testes, CI~~ ✅ | ⭐⭐ |
| ~~Backup~~ | ✅ | 🟡 | Multi-usuários | ⭐⭐ |

---

## Ordem de Implementação Sugerida

1. **Sprint 1:** ~~CI/CD + Testes~~ ✅ + ~~Desfazer Ação~~ ✅
2. **Sprint 2:** ~~Alertas/Limites~~ ✅ + ~~Scheduler Recorrentes~~ ✅
3. **Sprint 3:** ~~Gráficos~~ ✅ + ~~Metas~~ ✅
4. **Sprint 4:** ~~PDF~~ ✅ + ~~Conversão Moeda~~ ✅
5. **Sprint 5:** ~~Leitura de PDF~~ ✅ + Multi-Usuários ⬅️ **PRÓXIMO**
6. **Sprint 6:** ~~Backup~~ ✅
