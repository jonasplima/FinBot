<div align="center">
  <img src="assets/logo.svg" alt="FinBot Logo" width="140" height="140">
  <h1>FinBot</h1>
  <p><b>Assistente financeiro via WhatsApp com IA multi-provider</b></p>

  [![CI](https://github.com/jonasplima/FinBot/actions/workflows/ci.yml/badge.svg)](https://github.com/jonasplima/FinBot/actions/workflows/ci.yml)
  ![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)
  ![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
  ![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?style=for-the-badge&logo=postgresql&logoColor=white)
  ![Redis](https://img.shields.io/badge/Redis-DC382D?style=for-the-badge&logo=redis&logoColor=white)
  ![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)
  ![LLM](https://img.shields.io/badge/LLM-Gemini%20%2B%20Groq-0F766E?style=for-the-badge)
</div>

<p align="center">
  Registre gastos, acompanhe orçamentos, metas, recorrências, gráficos, exportações e backups completos usando linguagem natural no WhatsApp.
</p>

---

## Visão Geral

O **FinBot** é uma aplicação FastAPI conectada à **Evolution API** para operar pelo WhatsApp. O usuário envia mensagens de texto, imagens ou documentos, a camada de IA interpreta a intenção, e o sistema transforma isso em operações financeiras rastreáveis no banco.

O projeto foi desenhado para uso real: possui **idempotência de webhook**, **health/readiness**, **scheduler com trava distribuída**, **limites defensivos para arquivos**, **backup/restauração com auditoria**, **lockfile com hashes**, **rate limit administrativo** e **fallback entre provedores de IA**.

## O Que Ele Faz

- Registra despesas e entradas por linguagem natural.
- Entende parcelamento, despesa compartilhada e recorrência mensal.
- Lê imagens e PDFs para extrair dados financeiros.
- Gera resumos mensais, gráficos e exportações.
- Controla orçamentos por categoria com alertas.
- Cria e acompanha metas de economia.
- Faz conversão de moeda.
- Exporta e restaura backup completo do usuário.
- Suporta migração de backup entre números com confirmação reforçada e auditoria.

## Exemplo de Uso

```text
gastei 42 no almoço no pix
comprei um tênis de 300 em 3x no cartão
netflix 55 reais todo mês dia 15
quanto gastei esse mês?
limite de 800 para alimentação
quero economizar 5000 até 2026-12-31 para viagem
exportar meus gastos de março em pdf
```

## Arquitetura

```text
WhatsApp
   |
   v
Evolution API
   |
   v
FinBot (FastAPI)
   |
   +--> AI Service (Gemini / Groq)
   +--> PostgreSQL
   +--> Redis
   +--> APScheduler
```

### Componentes principais

- **FastAPI**: API principal, webhooks, healthchecks e endpoints administrativos.
- **Evolution API**: integração com WhatsApp.
- **AIService**: interpretação de texto, imagem e PDF com suporte a múltiplos provedores.
- **PostgreSQL**: persistência de usuários, despesas, metas, orçamentos, auditorias e confirmações pendentes.
- **Redis**: idempotência, rate limits, locks distribuídos e storage temporário.
- **APScheduler**: lembretes de recorrência, motivação semanal de metas e atualização de câmbio.

---

## Funcionalidades

### Registro financeiro

- Despesas e entradas
- Parcelamento
- Despesa compartilhada
- Desfazer última operação
- Categorização automática
- Suporte a moeda estrangeira

### Automação

- Despesas recorrentes com lembrete diário
- Confirmação explícita antes de lançar recorrências
- Scheduler protegido contra execução duplicada em `multi_instance`

### Gestão financeira

- Orçamentos por categoria
- Alertas de orçamento
- Metas de economia
- Conversão de moeda
- Resumo mensal
- Gráficos

### Portabilidade e recuperação

- Exportação em XLSX e PDF
- Backup JSON completo
- Restore com validação estrutural
- Migração entre números com confirmação reforçada
- Auditoria persistida de restore

---

## Stack Técnica

| Camada | Tecnologia |
| --- | --- |
| API | FastAPI |
| IA | Gemini e Groq |
| Banco | PostgreSQL |
| Cache / Coordenação | Redis |
| Scheduler | APScheduler |
| ORM | SQLAlchemy async |
| Testes | Pytest |
| Lint / Format | Ruff |
| Type checking | MyPy |
| Auditoria de dependências | pip-audit |
| Infra local | Docker Compose |

---

## Quick Start

### 1. Clonar o projeto

```bash
git clone https://github.com/jonasplima/FinBot.git
cd FinBot
```

### 2. Criar o `.env`

```bash
cp .env.example .env
```

Preencha ao menos:

```env
DATABASE_URL=postgresql+asyncpg://finbot:finbot_secure_password@postgres:5432/finbot
REDIS_URL=redis://:redis_secure_password@redis:6379

EVOLUTION_API_URL=http://evolution-api:8080
EVOLUTION_API_KEY=sua_chave_evolution
EVOLUTION_INSTANCE=FinBot

GEMINI_API_KEY=sua_chave_gemini
GROQ_API_KEY=sua_chave_groq
AI_PRIMARY_PROVIDER=gemini
AI_TIMEOUT_SECONDS=25

ADMIN_SECRET=uma_senha_forte
WEBHOOK_SECRET=um_segredo_forte

OWNER_PHONE=5511999999999
```

### 3. Subir o ambiente

```bash
docker compose up -d --build
```

### 4. Conectar o WhatsApp

Acesse:

```text
http://localhost:3003/admin/qrcode
```

Envie o header:

```text
Authorization: Bearer SUA_SENHA_ADMIN
```

### 5. Testar

Envie no WhatsApp:

```text
gastei 10 reais no café
```

---

## Endpoints

| Endpoint | Método | Descrição |
| --- | --- | --- |
| `/health` | `GET` | Readiness com checagem de dependências |
| `/health/live` | `GET` | Liveness do processo |
| `/health/ready` | `GET` | Readiness explícito |
| `/admin/qrcode` | `GET` | QR Code de conexão do WhatsApp |
| `/admin/status` | `GET` | Status da instância Evolution |
| `/webhook/evolution` | `POST` | Webhook principal de mensagens |

### Endpoints administrativos

- Exigem `Authorization: Bearer <ADMIN_SECRET>`.
- Possuem rate limit por IP e rota.
- Retornam erro sanitizado em vez de detalhes internos.

### Webhook

- Pode exigir `Authorization: Bearer <WEBHOOK_SECRET>`.
- Usa idempotência por `message_id`.
- Evita reprocessamento perigoso após efeitos já persistidos.

---

## IA Multi-Provider

O projeto possui uma camada de IA genérica em [`app/services/ai.py`](app/services/ai.py).

### Estratégia atual

- `Gemini` e `Groq` são suportados.
- `AI_PRIMARY_PROVIDER` define quem responde primeiro.
- Se o provedor principal falhar por quota, rate limit ou indisponibilidade, o sistema tenta fallback.
- O nome da camada foi generalizado para permitir novos provedores no futuro sem acoplar o projeto a `Gemini`.

### Variáveis relacionadas

| Variável | Descrição |
| --- | --- |
| `GEMINI_API_KEY` | chave do Gemini |
| `GROQ_API_KEY` | chave do Groq |
| `AI_PRIMARY_PROVIDER` | `gemini` ou `groq` |
| `AI_TIMEOUT_SECONDS` | timeout por chamada |

---

## Segurança e Resiliência

### Segurança aplicada

- Bearer token com comparação em tempo constante para endpoints sensíveis.
- Rate limit administrativo.
- Webhook autenticado.
- Sanitização de erros HTTP.
- `.env` ignorado no Git e excluído da imagem.
- Containers com endurecimento adicional no `finbot`:
  - `read_only: true`
  - `tmpfs` em `/tmp`
  - `cap_drop: [ALL]`
  - `no-new-privileges`
- Portas expostas em `127.0.0.1` por padrão.
- Lockfile com hashes em [`requirements.lock`](requirements.lock).
- CI com `pip check` e `pip-audit`.

### Resiliência operacional

- Healthchecks reais de banco, Redis e Evolution.
- Scheduler com lock distribuído quando em `multi_instance`.
- Fallbacks locais restritos ao modo `single_instance`.
- Eventos operacionais recentes expostos nos health endpoints.
- Backup temporário fora do banco com TTL.
- Auditoria persistida para restore e migração entre números.

---

## Configuração

O projeto possui muitas variáveis, mas a maioria já está documentada em [`.env.example`](.env.example). Abaixo estão os grupos mais importantes.

### Aplicação

| Variável | Default |
| --- | --- |
| `PORT` | `3003` |
| `LOG_LEVEL` | `INFO` |

### WhatsApp / Evolution

| Variável | Descrição |
| --- | --- |
| `EVOLUTION_API_URL` | URL base da Evolution |
| `EVOLUTION_API_KEY` | chave de autenticação |
| `EVOLUTION_INSTANCE` | nome da instância |
| `OWNER_PHONE` | número principal para bootstrap |
| `ALLOWED_NUMBERS` | rollout controlado opcional |

### Segurança

| Variável | Descrição |
| --- | --- |
| `ADMIN_SECRET` | acesso aos endpoints admin |
| `WEBHOOK_SECRET` | autenticação do webhook |
| `ADMIN_RATE_LIMIT_MAX_ATTEMPTS` | tentativas por janela |
| `ADMIN_RATE_LIMIT_WINDOW_SECONDS` | tamanho da janela |

### Deploy / Scheduler

| Variável | Descrição |
| --- | --- |
| `SCHEDULER_ENABLED` | ativa jobs agendados |
| `SCHEDULER_TIMEZONE` | timezone do scheduler |
| `SCHEDULER_HOUR` | hora do job diário |
| `SCHEDULER_MINUTE` | minuto do job diário |
| `DEPLOYMENT_MODE` | `single_instance` ou `multi_instance` |
| `SCHEDULER_LOCK_TTL_SECONDS` | TTL da trava distribuída |
| `INSTANCE_ID` | identificador da instância |

### Limites defensivos

| Variável | Descrição |
| --- | --- |
| `MAX_PDF_SIZE_BYTES` | tamanho máximo de PDF |
| `MAX_PDF_PAGES` | páginas máximas |
| `MAX_PDF_TEXT_CHARS` | texto máximo extraído |
| `MAX_BACKUP_SIZE_BYTES` | tamanho máximo de backup |
| `BACKUP_TEMP_TTL_SECONDS` | TTL do backup temporário |
| `WEBHOOK_IDEMPOTENCY_TTL_SECONDS` | retenção da chave de idempotência |

---

## Backup e Migração de Número

O FinBot exporta backup completo em JSON com metadata e validação de estrutura.

### O que o backup cobre

- despesas
- orçamentos
- alertas de orçamento
- metas
- atualizações de metas
- metadata de origem

### Proteções do fluxo

- limite máximo de tamanho
- validação de schema
- whitelist de campos aceitos
- restore sob confirmação
- confirmação especial quando o backup vem de outro número
- auditoria persistida da restauração
- identidade estável de backup para reduzir dependência exclusiva do telefone

---

## Desenvolvimento

### Estrutura do projeto

```text
FinBot/
├── app/
│   ├── database/
│   ├── handlers/
│   ├── services/
│   ├── utils/
│   ├── config.py
│   └── main.py
├── tests/
├── assets/
├── .github/workflows/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── requirements.lock
├── NEXT_STEPS.md
└── .env.example
```

### Serviços principais

- [`app/services/ai.py`](app/services/ai.py)
- [`app/services/backup.py`](app/services/backup.py)
- [`app/services/budget.py`](app/services/budget.py)
- [`app/services/currency.py`](app/services/currency.py)
- [`app/services/evolution.py`](app/services/evolution.py)
- [`app/services/expense.py`](app/services/expense.py)
- [`app/services/export.py`](app/services/export.py)
- [`app/services/goal.py`](app/services/goal.py)
- [`app/services/rate_limit.py`](app/services/rate_limit.py)
- [`app/services/scheduler.py`](app/services/scheduler.py)
- [`app/services/webhook_idempotency.py`](app/services/webhook_idempotency.py)

### Rodando localmente sem Docker

```bash
python -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.lock
pip install pytest pytest-asyncio pytest-cov aiosqlite mypy types-python-dateutil
uvicorn app.main:app --reload --port 3003
```

---

## Qualidade e CI

Atualmente o repositório possui **14 arquivos de teste** cobrindo IA, webhook, scheduler, backup, exportação, metas, orçamento, câmbio e utilitários.

### Comandos úteis

```bash
ruff check .
ruff format --check .
mypy app --ignore-missing-imports
pytest -q
pip-audit -r requirements.lock
docker compose config
```

### Pipeline de CI

O GitHub Actions valida:

- lockfile com hashes
- lint
- formatação
- type checking
- testes com cobertura
- consistência de dependências com `pip check`
- auditoria de vulnerabilidades com `pip-audit`

Arquivo: [`.github/workflows/ci.yml`](.github/workflows/ci.yml)

---

## Supply Chain e Imagens

O projeto já está preparado para travar imagens por digest no deploy:

```env
POSTGRES_IMAGE=postgres:16-alpine@sha256:...
REDIS_IMAGE=redis:7-alpine@sha256:...
EVOLUTION_IMAGE=evoapicloud/evolution-api:v2.3.7@sha256:...
PYTHON_BUILDER_IMAGE=python:3.12-slim@sha256:...
PYTHON_RUNTIME_IMAGE=python:3.12-slim@sha256:...
```

As dependências Python podem ser instaladas com integridade verificada via:

```bash
pip install --require-hashes -r requirements.lock
```

---

## Roadmap

O roadmap e as próximas entregas estão em [`NEXT_STEPS.md`](NEXT_STEPS.md).

---

## Licença

Projeto privado para uso pessoal.

---

<div align="center">
  <sub>FinBot • WhatsApp + FastAPI + PostgreSQL + Redis + IA multi-provider</sub>
</div>
