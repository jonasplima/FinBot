<div align="center">
  <img src="assets/logo.svg" alt="FinBot Logo" width="150" height="150">
  <h1>FinBot</h1>
  <p><b>Assistente Financeiro Inteligente via WhatsApp</b></p>

  [![CI](https://github.com/jonasplima/FinBot/actions/workflows/ci.yml/badge.svg)](https://github.com/jonasplima/FinBot/actions/workflows/ci.yml)
  [![Python](https://img.shields.io/badge/Python_3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](#)
  [![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](#)
  [![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)](#)
  [![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?style=for-the-badge&logo=postgresql&logoColor=white)](#)
  [![Gemini](https://img.shields.io/badge/Google_Gemini-8E75B2?style=for-the-badge&logo=google&logoColor=white)](#)
</div>

<br/>

O **FinBot** é um assistente financeiro pessoal que opera diretamente no WhatsApp. Registre gastos, receitas e controle seu orçamento através de mensagens de texto naturais ou fotos de notas fiscais.

Através da integração da **Evolution API** com o **Google Gemini**, o FinBot interpreta suas mensagens, categoriza despesas automaticamente e mantém seu controle financeiro organizado.

---

## Funcionalidades

### Registro de Despesas
- **Linguagem natural**: "gastei 50 reais no almoço no pix"
- **Parcelamento**: "comprei um tênis de 300 reais em 3x no cartão"
- **Despesas compartilhadas**: "mercado 200 reais dividido 60% meu"
- **Leitura de notas fiscais**: envie uma foto e o FinBot extrai os dados

### Despesas Recorrentes
- **Cadastro**: "netflix 55 reais todo mês dia 15"
- **Scheduler automático**: às 08:00 pergunta se você já pagou as contas do dia
- **Confirmação**: responda "sim" para lançar ou "não" para ignorar

### Orçamentos e Alertas
- **Definir limite**: "definir limite alimentação 500 reais"
- **Alertas automáticos**: notificações em 50%, 80% e 100% do limite
- **Consultar status**: "como está meu orçamento de alimentação?"

### Consultas e Exportação
- **Resumo mensal**: "quanto gastei esse mês?"
- **Exportar Excel**: "exportar meus gastos de março"
- **Desfazer**: "desfaz" ou "apaga o último"

---

## Comandos Disponíveis

| Comando | Exemplo |
|---------|---------|
| Registrar gasto | "gastei 45 reais no almoço no pix" |
| Gasto parcelado | "comprei TV de 2000 em 10x no cartão" |
| Gasto compartilhado | "mercado 300 reais dividido 50%" |
| Despesa recorrente | "spotify 21.90 todo mês dia 5" |
| Cancelar recorrente | "cancelar spotify" |
| Listar recorrentes | "minhas despesas recorrentes" |
| Resumo do mês | "quanto gastei esse mês?" |
| Exportar Excel | "exportar março" |
| Desfazer último | "desfaz" ou "apaga o último" |
| Definir orçamento | "limite de 500 para alimentação" |
| Ver orçamentos | "meus limites de gasto" |
| Status orçamento | "como está meu orçamento?" |
| Remover orçamento | "remover limite de lazer" |

---

## Arquitetura

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  WhatsApp   │────▶│ Evolution API │────▶│   FinBot    │
│   (User)    │◀────│   (Webhook)   │◀────│  (FastAPI)  │
└─────────────┘     └──────────────┘     └──────┬──────┘
                                                │
                    ┌───────────────────────────┼───────────────────────────┐
                    │                           │                           │
              ┌─────▼─────┐             ┌───────▼───────┐           ┌───────▼───────┐
              │ PostgreSQL │             │ Google Gemini │           │     Redis     │
              │    (DB)    │             │     (AI)      │           │    (Cache)    │
              └───────────┘             └───────────────┘           └───────────────┘
```

### Tecnologias

- **FastAPI** - Framework web assíncrono de alta performance
- **Evolution API** - Integração nativa com WhatsApp
- **Google Gemini** - IA para interpretação de mensagens e imagens
- **PostgreSQL** - Banco de dados relacional
- **Redis** - Cache e controle de estado
- **APScheduler** - Agendamento de tarefas (despesas recorrentes)
- **Docker** - Containerização completa

---

## Instalação

### Pré-requisitos

- [Docker](https://www.docker.com/) e [Docker Compose](https://docs.docker.com/compose/)
- Conta no [Google AI Studio](https://aistudio.google.com/) para chave da API Gemini

### 1. Clone o repositório

```bash
git clone https://github.com/jonasplima/FinBot.git
cd FinBot
```

### 2. Configure as variáveis de ambiente

```bash
cp .env.example .env
```

Edite o arquivo `.env`:

```env
# Obrigatórios
OWNER_PHONE=5511999999999        # Seu número (formato internacional)
GEMINI_API_KEY=sua_chave_gemini  # Do Google AI Studio
ADMIN_SECRET=senha_segura        # Para acessar endpoints admin via Authorization
EVOLUTION_API_KEY=chave_aleatoria # Gere com: openssl rand -hex 32
WEBHOOK_SECRET=chave_webhook     # Autenticação do webhook da Evolution

# Opcionais
ALLOWED_NUMBERS=5511988888888    # Números adicionais (separados por vírgula)
SCHEDULER_ENABLED=true           # Ativar scheduler de recorrentes
SCHEDULER_HOUR=8                 # Hora do lembrete diário
```

### 3. Inicie os containers

```bash
docker-compose up -d
```

Observação:
- O build Docker ignora `.env` e outros arquivos locais sensíveis via `.dockerignore`
- As credenciais são injetadas no container em runtime pelo `docker-compose`, não copiadas para a imagem
- As portas publicadas por padrão ficam presas em `127.0.0.1`, reduzindo exposição acidental na rede local
- O serviço `finbot` roda com filesystem somente leitura, `tmpfs` em `/tmp` e `no-new-privileges`
- As imagens do Compose e do `Dockerfile` podem ser travadas por digest via `.env`, sem editar o YAML

### 4. Conecte o WhatsApp

Acesse no navegador:
```
http://localhost:3003/admin/qrcode
```

Envie o header:
```text
Authorization: Bearer SUA_SENHA_ADMIN
```

Escaneie o QR Code com o WhatsApp (como no WhatsApp Web).

### 5. Teste

Envie uma mensagem para o número conectado:
```
gastei 10 reais no café
```

---

## Desenvolvimento

### Estrutura do Projeto

```
FinBot/
├── app/
│   ├── database/        # Models, conexão e seeds
│   ├── handlers/        # Webhook handlers
│   ├── services/        # Serviços (Gemini, Evolution, Budget, etc)
│   ├── utils/           # Utilitários e validadores
│   ├── config.py        # Configurações (Pydantic Settings)
│   └── main.py          # Aplicação FastAPI
├── tests/               # Testes unitários (124 testes)
├── .github/workflows/   # CI/CD (GitHub Actions)
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

### Executar Testes

```bash
# Instalar dependências de desenvolvimento
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-cov aiosqlite

# Rodar todos os testes
pytest tests/ -v

# Com cobertura
pytest tests/ --cov=app --cov-report=term-missing
```

### Linting e Type Checking

```bash
# Lint
ruff check .

# Formatação
ruff format .

# Type checking
mypy app/
```

### Auditoria de Dependências

```bash
# Auditoria local de dependências conhecidas
pip install pip-audit
pip-audit -r requirements.txt

# Verificação adicional de consistência do ambiente instalado
pip check
```

### Pin por Digest em Produção

Para produção, prefira travar imagens por digest em vez de depender apenas de tags mutáveis. O `docker-compose.yml` e o `Dockerfile` já aceitam isso via variáveis de ambiente:

```env
POSTGRES_IMAGE=postgres:16-alpine@sha256:...
REDIS_IMAGE=redis:7-alpine@sha256:...
EVOLUTION_IMAGE=evoapicloud/evolution-api:v2.3.7@sha256:...
PYTHON_BUILDER_IMAGE=python:3.12-slim@sha256:...
PYTHON_RUNTIME_IMAGE=python:3.12-slim@sha256:...
```

Assim o deploy continua igual, mas com origem de imagem mais reprodutível e auditável.

---

## Configurações do Scheduler

O scheduler de despesas recorrentes pode ser configurado via variáveis de ambiente:

| Variável | Default | Descrição |
|----------|---------|-----------|
| `SCHEDULER_ENABLED` | `true` | Ativar/desativar o scheduler |
| `SCHEDULER_TIMEZONE` | `America/Sao_Paulo` | Fuso horário |
| `SCHEDULER_HOUR` | `8` | Hora do lembrete (0-23) |
| `SCHEDULER_MINUTE` | `0` | Minuto do lembrete (0-59) |

---

## Categorias Disponíveis

### Gastos (Negativo)
Alimentação, Assinatura, Imprevistos, Despesa Fixa, Educação, Empréstimo, Lazer, Mercado, Moradia, Outros, Parcelamento de Fatura, Presente, Saúde e Beleza, Serviços, Transferência, Transporte, Vestuário, Viagem, Reserva de Emergência, Investimento

### Entradas (Positivo)
Salário, Salário - Adiantamento, Salário - 13º, Reembolso, Bônus, PLR, VR (Flash), Outros

### Meios de Pagamento
Cartão de Crédito, Cartão de Débito, Pix, Dinheiro, VR

---

## Roadmap

Veja o arquivo [NEXT_STEPS.md](NEXT_STEPS.md) para o roadmap completo.

### Concluído
- [x] Registro de despesas via linguagem natural
- [x] Leitura de notas fiscais (imagem)
- [x] Despesas parceladas e compartilhadas
- [x] Despesas recorrentes com scheduler
- [x] Alertas e limites de orçamento
- [x] Exportação para Excel
- [x] Desfazer última ação
- [x] Pipeline CI/CD
- [x] 124 testes unitários

### Próximos
- [ ] Gráficos no WhatsApp
- [ ] Exportação PDF
- [ ] Metas de economia
- [ ] Conversão de moeda
- [ ] Multi-usuários

---

## Licença

Este projeto é privado e de uso pessoal.

---

<div align="center">
  <sub>Desenvolvido com FastAPI + Google Gemini</sub>
</div>
