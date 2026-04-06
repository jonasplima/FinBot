<div align="center">
  <img src="assets/logo.svg" alt="FinBot Logo" width="140" height="140">
  <h1>FinBot</h1>
  <p><b>Assistente financeiro via WhatsApp com FastAPI, Evolution API e IA multi-provider</b></p>

  [![CI](https://github.com/jonasplima/FinBot/actions/workflows/ci.yml/badge.svg)](https://github.com/jonasplima/FinBot/actions/workflows/ci.yml)
  ![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)
  ![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
  ![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?style=for-the-badge&logo=postgresql&logoColor=white)
  ![Redis](https://img.shields.io/badge/Redis-DC382D?style=for-the-badge&logo=redis&logoColor=white)
  ![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)
  ![LLM](https://img.shields.io/badge/LLM-Gemini%20%2B%20Groq-0F766E?style=for-the-badge)
</div>

<p align="center">
  Registre gastos, acompanhe metas e orçamentos, exporte relatórios e opere tudo por linguagem natural no WhatsApp.
</p>

---

## Visão Geral

O **FinBot** é uma aplicação FastAPI integrada à **Evolution API** para operar pelo WhatsApp. Ele recebe mensagens, imagens e PDFs, usa uma camada de IA para interpretar a intenção do usuário e transforma isso em operações financeiras persistidas no banco.

Hoje o projeto já inclui:

- registro de despesas e entradas por linguagem natural;
- metas, orçamentos e recorrências;
- exportação em PDF/XLSX e backup JSON;
- restore com auditoria e migração entre números;
- onboarding web com autenticação, QR Code no navegador e painel de configurações;
- health/readiness reais, scheduler com lock distribuído e hardening básico de infra.

## Funcionalidades

- Despesas e entradas com categorização automática
- Parcelamento e despesa compartilhada
- Despesas recorrentes com confirmação
- Metas financeiras
- Orçamentos por categoria e alertas
- Conversão de moeda
- Leitura de imagens e PDFs
- Exportação em PDF e XLSX
- Backup e restauração de dados
- Onboarding web e configurações pós-onboarding

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
   +--> AIService (Gemini / Groq)
   +--> PostgreSQL
   +--> Redis
   +--> APScheduler
```

## Stack

| Camada | Tecnologia |
| --- | --- |
| API | FastAPI |
| IA | Gemini e Groq |
| Banco | PostgreSQL |
| Cache / coordenação | Redis |
| Scheduler | APScheduler |
| ORM | SQLAlchemy async |
| Testes | Pytest |
| Lint / format | Ruff |
| Type checking | MyPy |
| Supply chain | pip-tools + pip-audit |
| Infra local | Docker Compose |

---

## Configuração Passo a Passo

Esta é a forma recomendada de subir o projeto hoje.

### 1. Clonar o repositório

```bash
git clone https://github.com/jonasplima/FinBot.git
cd FinBot
```

### 2. Criar o arquivo de ambiente

```bash
cp .env.example .env
```

### 3. Preencher o `.env`

O mínimo para o projeto iniciar com Docker é:

```env
POSTGRES_USER=finbot
POSTGRES_PASSWORD=finbot_secure_password
POSTGRES_DB=finbot
DATABASE_URL=postgresql+asyncpg://finbot:finbot_secure_password@postgres:5432/finbot

REDIS_PASSWORD=redis_secure_password
REDIS_URL=redis://:redis_secure_password@redis:6379

EVOLUTION_API_URL=http://evolution-api:8080
EVOLUTION_API_KEY=sua_chave_evolution
EVOLUTION_INSTANCE=FinBot

GEMINI_API_KEY=sua_chave_gemini
GROQ_API_KEY=
AI_PRIMARY_PROVIDER=gemini
AI_TIMEOUT_SECONDS=25

ADMIN_SECRET=sua_senha_admin_secreta
WEBHOOK_SECRET=sua_chave_webhook_secreta
APP_ENCRYPTION_KEY=
```

### 4. Entender o que cada grupo configura

#### Infra obrigatória

Sem isso o app não sobe corretamente:

- `DATABASE_URL`
- `REDIS_URL`
- `EVOLUTION_API_URL`
- `EVOLUTION_API_KEY`
- `EVOLUTION_INSTANCE`
- `ADMIN_SECRET`

#### IA

Hoje o projeto usa chaves globais da instância via `.env`.

- `GEMINI_API_KEY`
- `GROQ_API_KEY`
- `AI_PRIMARY_PROVIDER`
- `AI_TIMEOUT_SECONDS`

Regras atuais:

- `AI_PRIMARY_PROVIDER=gemini`: Gemini primeiro, Groq como fallback se configurado
- `AI_PRIMARY_PROVIDER=groq`: Groq primeiro, Gemini como fallback se configurado

#### Segurança

- `ADMIN_SECRET`: protege endpoints administrativos
- `WEBHOOK_SECRET`: autentica o webhook da Evolution
- `APP_ENCRYPTION_KEY`: material de chave para criptografia interna

Se `APP_ENCRYPTION_KEY` ficar vazio, o sistema tenta derivar material a partir dos segredos da instância. Em produção, o ideal é definir explicitamente.

#### WhatsApp / rollout

- `OWNER_PHONE`: número opcional para bootstrap inicial
- `ALLOWED_NUMBERS`: lista opcional para rollout controlado

#### Scheduler e modo de deploy

- `SCHEDULER_ENABLED`
- `SCHEDULER_TIMEZONE`
- `SCHEDULER_HOUR`
- `SCHEDULER_MINUTE`
- `DEPLOYMENT_MODE`
- `SCHEDULER_LOCK_TTL_SECONDS`
- `INSTANCE_ID`

Use:

- `DEPLOYMENT_MODE=single_instance` para ambiente simples
- `DEPLOYMENT_MODE=multi_instance` quando houver múltiplas réplicas e Redis obrigatório para coordenação

#### Conversão de moeda

Opcionais:

- `WISE_API_KEY`
- `EXCHANGE_RATE_API_KEY`
- `EXCHANGE_RATE_CACHE_TTL`
- `FALLBACK_RATES_UPDATE_DAYS`

#### Limites defensivos

Já vêm com defaults seguros em [`.env.example`](/Users/jonas.lima/github/FinBot/.env.example):

- limites de PDF
- limites de backup
- TTL de backup temporário
- TTL de idempotência de webhook

### 5. Subir a stack com Docker

```bash
docker compose up -d --build
```

### 6. Conferir se os serviços subiram

```bash
docker compose ps
```

Healthchecks esperados:

- `postgres`: healthy
- `redis`: healthy
- `finbot`: healthy
- `evolution`: running

### 7. Validar a API

Abra:

- `http://localhost:3003/health/live`
- `http://localhost:3003/health/ready`

O `/health/ready` deve refletir o estado de banco, Redis e Evolution.

### 8. Criar o acesso web

Abra no navegador:

```text
http://localhost:3003/web/login
```

Nessa tela você pode:

- criar uma conta web com `nome + email + senha + telefone`
- ou entrar em uma conta já criada

### 9. Fazer o onboarding web

Depois do login, o fluxo segue para:

```text
http://localhost:3003/web/onboarding
```

Atualmente o onboarding web permite:

- aceitar os termos
- salvar perfil básico
- preparar a sessão WhatsApp
- gerar QR Code no navegador
- acompanhar status da conexão
- personalizar categorias

### 10. Conectar o WhatsApp

No onboarding:

1. clique em `Preparar sessão`
2. clique em `Gerar QR Code`
3. escaneie o QR Code com o WhatsApp

Esse é o fluxo recomendado hoje. Você não precisa montar header manual para obter o QR Code pelo browser.

### 11. Acessar configurações pós-onboarding

Depois do onboarding, use:

```text
http://localhost:3003/web/settings
```

No painel de configurações você consegue:

- editar perfil
- ajustar notificações
- configurar limites diários
- exportar backup
- fazer preview e aplicar restore de backup

### 12. Testar no WhatsApp

Envie algo como:

```text
gastei 42 reais no almoço no pix
```

Ou:

```text
comprei um tênis de 300 em 3x no cartão
```

---

## Setup Alternativo sem Docker

Se quiser rodar localmente sem containers:

```bash
python -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.lock
pip install pytest pytest-asyncio pytest-cov aiosqlite mypy types-python-dateutil
uvicorn app.main:app --reload --port 3003
```

Nesse caso você precisa fornecer PostgreSQL, Redis e Evolution manualmente.

---

## Endpoints Principais

| Endpoint | Método | Descrição |
| --- | --- | --- |
| `/health` | `GET` | Readiness com dependências |
| `/health/live` | `GET` | Liveness |
| `/health/ready` | `GET` | Readiness explícito |
| `/auth/register` | `POST` | Registro de acesso web |
| `/auth/login` | `POST` | Login web |
| `/auth/logout` | `POST` | Logout web |
| `/auth/me` | `GET` | Usuário autenticado |
| `/web/login` | `GET` | Tela web de acesso |
| `/web/onboarding` | `GET` | Tela de onboarding |
| `/web/settings` | `GET` | Painel de configurações |
| `/webhook/evolution` | `POST` | Webhook principal |
| `/admin/qrcode` | `GET` | QR Code administrativo de fallback |
| `/admin/status` | `GET` | Status administrativo da Evolution |

### Observação sobre `/admin/qrcode`

O endpoint administrativo continua existindo para operação e troubleshooting, mas o fluxo normal do produto hoje deve passar pelo onboarding web em `/web/onboarding`.

---

## IA Multi-Provider

A camada principal está em [`app/services/ai.py`](/Users/jonas.lima/github/FinBot/app/services/ai.py).

Comportamento atual:

- suporta `Gemini` e `Groq`
- usa `AI_PRIMARY_PROVIDER` para decidir o provedor principal
- faz fallback automático quando o provedor principal falha por quota, rate limit ou indisponibilidade

Variáveis relacionadas:

| Variável | Descrição |
| --- | --- |
| `GEMINI_API_KEY` | chave do Gemini |
| `GROQ_API_KEY` | chave do Groq |
| `AI_PRIMARY_PROVIDER` | `gemini` ou `groq` |
| `AI_TIMEOUT_SECONDS` | timeout por chamada |

---

## Segurança e Resiliência

### Segurança

- comparação em tempo constante para bearer tokens
- rate limit administrativo
- webhook com autenticação
- sanitização de erros HTTP
- `.env` ignorado no Git
- criptografia para segredos internos usando `APP_ENCRYPTION_KEY`

### Resiliência

- healthchecks reais de banco, Redis e Evolution
- idempotência no webhook
- scheduler com lock distribuído
- fallbacks locais restritos ao modo `single_instance`
- auditoria de restore de backup
- eventos operacionais recentes expostos nos health endpoints

### Hardening de containers

No serviço `finbot`, o Compose já usa:

- `read_only: true`
- `tmpfs` em `/tmp`
- `cap_drop: [ALL]`
- `no-new-privileges`
- portas publicadas em `127.0.0.1`

---

## Backup e Migração de Número

O backup JSON cobre:

- despesas
- orçamentos
- alertas de orçamento
- metas
- atualizações de metas
- metadata de origem

Proteções do fluxo:

- limite máximo de tamanho
- validação estrutural
- whitelist de campos aceitos
- preview antes do restore
- confirmação explícita para migração entre números/perfis
- auditoria persistida da restauração
- `backup_owner_id` estável para reduzir dependência exclusiva do telefone

---

## Estrutura do Projeto

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
├── docs/
├── .github/workflows/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── requirements.lock
├── NEXT_STEPS.md
└── .env.example
```

Serviços centrais:

- [`app/services/ai.py`](/Users/jonas.lima/github/FinBot/app/services/ai.py)
- [`app/services/backup.py`](/Users/jonas.lima/github/FinBot/app/services/backup.py)
- [`app/services/evolution.py`](/Users/jonas.lima/github/FinBot/app/services/evolution.py)
- [`app/services/expense.py`](/Users/jonas.lima/github/FinBot/app/services/expense.py)
- [`app/services/scheduler.py`](/Users/jonas.lima/github/FinBot/app/services/scheduler.py)
- [`app/services/rate_limit.py`](/Users/jonas.lima/github/FinBot/app/services/rate_limit.py)
- [`app/services/auth.py`](/Users/jonas.lima/github/FinBot/app/services/auth.py)
- [`app/services/onboarding.py`](/Users/jonas.lima/github/FinBot/app/services/onboarding.py)

---

## Desenvolvimento e Qualidade

Comandos úteis:

```bash
ruff check .
ruff format --check .
mypy app --ignore-missing-imports
pytest -q
pip-audit -r requirements.lock
docker compose config
```

A CI valida:

- lockfile com hashes
- lint
- formatação
- type checking
- testes
- `pip check`
- `pip-audit`

Arquivo da pipeline:

- [`.github/workflows/ci.yml`](/Users/jonas.lima/github/FinBot/.github/workflows/ci.yml)

---

## Supply Chain

O projeto já está preparado para travar imagens por digest no deploy:

```env
POSTGRES_IMAGE=postgres:16-alpine@sha256:...
REDIS_IMAGE=redis:7-alpine@sha256:...
EVOLUTION_IMAGE=evoapicloud/evolution-api:v2.3.7@sha256:...
PYTHON_BUILDER_IMAGE=python:3.12-slim@sha256:...
PYTHON_RUNTIME_IMAGE=python:3.12-slim@sha256:...
```

As dependências Python podem ser instaladas com verificação de integridade:

```bash
pip install --require-hashes -r requirements.lock
```

---

## Roadmap

Planejamento e próximas entregas:

- [`NEXT_STEPS.md`](/Users/jonas.lima/github/FinBot/NEXT_STEPS.md)
- [`docs/onboarding-plan.md`](/Users/jonas.lima/github/FinBot/docs/onboarding-plan.md)

---

## Licença

Projeto privado para uso pessoal.

---

<div align="center">
  <sub>FinBot • WhatsApp + FastAPI + PostgreSQL + Redis + IA multi-provider</sub>
</div>
