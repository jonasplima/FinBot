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

## Fase 0: Hardening, Segurança e Confiabilidade (Prioridade Máxima)

### 0.1 Autenticação do Webhook da Evolution API ✅
- **Complexidade:** Média 🟡
- **Valor:** Muito Alto
- **Status:** Implementado
- **Problema identificado:**
  - O endpoint `/webhook/evolution` aceita requisições sem autenticação forte
  - Hoje é possível forjar payloads com `remoteJid` e simular mensagens de outros números caso a porta esteja acessível
  - A publicação da porta do `finbot` no `docker-compose` amplia a superfície de ataque
- **Implementação:**
  - ✅ Inclusão de `WEBHOOK_SECRET` nas configurações da aplicação
  - ✅ Configuração do webhook da Evolution com header customizado `Authorization: Bearer <WEBHOOK_SECRET>`
  - ✅ Validação fail-closed do header no endpoint `/webhook/evolution`
  - ✅ Rejeição explícita quando o segredo não está configurado
  - ✅ Atualização do `docker-compose` e `.env.example` para propagar a nova variável
  - ✅ Testes cobrindo credencial válida, inválida, ausente e payload enviado no `setup_webhook()`
- **Critérios de aceite:**
  - Webhooks sem credencial válida são rejeitados antes de qualquer processamento
  - Requisições legítimas da Evolution continuam funcionando normalmente
  - Não é mais possível acionar o fluxo com payloads forjados apenas chamando a rota HTTP
- **Arquivos impactados:**
  - `app/main.py`
  - `app/services/evolution.py`
  - `docker-compose.yml`
  - `tests/test_webhook.py`

### 0.2 Proteção da Superfície Admin e Redução de Logs Sensíveis ✅
- **Complexidade:** Média 🟡
- **Valor:** Muito Alto
- **Status:** Implementado
- **Problema identificado:**
  - `ADMIN_SECRET` é enviado por query string em `/admin/qrcode` e `/admin/status`
  - Logs atuais podem registrar QR code, pairing code, telefone, conteúdo da mensagem e metadados sensíveis
  - O `logging.basicConfig()` está fixando `INFO`, reduzindo o controle operacional do que vaza
- **Implementação:**
  - ✅ Migração dos endpoints admin para autenticação via header `Authorization: Bearer <ADMIN_SECRET>`
  - ✅ Remoção da dependência de `secret` na query string dos endpoints administrativos
  - ✅ `logging.basicConfig()` passando a respeitar `LOG_LEVEL`
  - ✅ Inclusão de helpers para mascarar telefone e sanitizar texto em logs
  - ✅ Redução de logs sensíveis no webhook e na integração com Evolution API
  - ✅ Atualização da documentação e testes cobrindo autenticação admin e helpers de segurança
- **Critérios de aceite:**
  - URLs administrativas deixam de carregar segredo em texto puro
  - Logs não exibem QR code, pairing code, payload bruto nem mensagem financeira do usuário
  - O nível de log configurado no ambiente é respeitado pela aplicação
- **Arquivos impactados:**
  - `app/main.py`
  - `app/services/evolution.py`
  - `app/handlers/webhook.py`
  - `app/config.py`

### 0.3 Blindagem do Build Docker Contra Vazamento de `.env`
- **Complexidade:** Baixa 🟢
- **Valor:** Muito Alto
- **Status:** Implementado
- **Problema identificado:**
  - O `Dockerfile` usa `COPY . .` sem `.dockerignore`
  - O arquivo `.env` local pode ir para dentro da imagem e ficar preservado nas layers
  - Parte das variáveis em runtime parece depender desse vazamento implícito
- **Implementação:**
  - ✅ Criação de `.dockerignore` excluindo `.env`, `.git`, caches, cobertura e artefatos locais de desenvolvimento
  - ✅ Configuração do serviço `finbot` para carregar variáveis em runtime via `env_file` e `environment`, sem depender de arquivos copiados para a imagem
  - ✅ Explicitação das variáveis da aplicação no `docker-compose`, incluindo scheduler, limites padrão e integrações opcionais
  - ✅ Inclusão de defaults seguros no Compose para variáveis opcionais com fallback já previsto na aplicação
  - ✅ Atualização da documentação para deixar claro que segredos entram no container em runtime, não no build
  - ✅ Validação estrutural com `docker compose config --services` sem warnings
- **Critérios de aceite:**
  - O build não inclui `.env` nem outros arquivos locais sensíveis
  - O container sobe com as variáveis declaradas explicitamente
  - O comportamento da aplicação não depende mais do conteúdo acidental da imagem
- **Arquivos impactados:**
  - `docker-compose.yml`
  - `.dockerignore`
  - `README.md`

### 0.4 Sanitização de Dados na Exportação XLSX
- **Complexidade:** Baixa 🟢
- **Valor:** Alto
- **Status:** Implementado
- **Problema identificado:**
  - Campos livres como descrição são exportados para Excel sem sanitização
  - Valores iniciados por `=`, `+`, `-` ou `@` podem ser interpretados como fórmula por planilhas
  - Isso abre espaço para formula injection em arquivos exportados
- **Implementação:**
  - ✅ Inclusão de `sanitize_for_spreadsheet()` para neutralizar conteúdo textual iniciado por `=`, `+`, `-` ou `@`
  - ✅ Aplicação da sanitização apenas nas colunas textuais da exportação XLSX
  - ✅ Preservação de colunas numéricas, como `Valor`, para não quebrar formatação e cálculos
  - ✅ Testes cobrindo descrições maliciosas, casos normais e garantia de que valores monetários continuam numéricos
- **Critérios de aceite:**
  - Dados livres continuam legíveis no arquivo exportado
  - Células potencialmente executáveis deixam de ser interpretadas como fórmula
  - Exportações existentes permanecem compatíveis
- **Arquivos impactados:**
  - `app/services/export.py`
  - `app/utils/validators.py`
  - `tests/test_export.py`

### 0.5 Limites Defensivos para PDFs e Backups
- **Complexidade:** Média 🟡
- **Valor:** Alto
- **Status:** Implementado
- **Problema identificado:**
  - PDFs e JSONs são carregados inteiros em memória
  - Não há limite de tamanho, páginas, caracteres extraídos ou profundidade do backup
  - O backup completo é salvo em `pending_confirmations.data`, aumentando uso de banco e risco operacional
  - A validação do backup ainda é estrutural, mas pouco restritiva em enums, ranges e campos permitidos
- **Implementação:**
  - ✅ Inclusão de limites configuráveis com teto seguro para tamanho de PDF, quantidade de páginas, caracteres extraídos e tamanho/cardinalidade de backups
  - ✅ Rejeição antecipada de PDFs grandes ou inválidos antes do processamento pelo Gemini
  - ✅ Endurecimento do schema de backup com validação explícita de campos permitidos, enums, datas e limites por coleção
  - ✅ Substituição do blob completo em `pending_confirmations` por referência temporária com TTL e hash
  - ✅ Armazenamento temporário do backup via Redis com fallback em memória
  - ✅ Testes cobrindo arquivo acima do limite, schema inválido, restore por referência e expiração do backup temporário
- **Critérios de aceite:**
  - Arquivos excessivos são recusados sem derrubar a aplicação
  - Backups inválidos falham antes da restauração com mensagens claras
  - O banco não armazena blobs grandes desnecessariamente em confirmações pendentes
- **Arquivos impactados:**
  - `app/config.py`
  - `app/handlers/webhook.py`
  - `app/services/backup.py`
  - `tests/test_backup.py`
  - `tests/test_webhook.py`
  - `docker-compose.yml`
  - `.env.example`

### 0.6 Idempotência, Retry e Confiabilidade do Webhook
- **Complexidade:** Média/Alta 🔴
- **Valor:** Alto
- **Status:** Implementado
- **Problema identificado:**
  - Em caso de erro a API responde `200`, o que pode mascarar falhas e impedir retry da origem
  - A deduplicação de mensagens está só em memória, perdendo eficácia em restart ou múltiplas instâncias
  - O fluxo de confirmações pendentes depende de replace por usuário e pode sofrer com corridas
- **Implementação:**
  - ✅ Criação de um serviço dedicado de idempotência do webhook com Redis e fallback em memória
  - ✅ Reserva do `message_id` na borda HTTP antes de qualquer efeito colateral
  - ✅ Resposta explícita para duplicatas com `duplicate_ignored`
  - ✅ Retorno `500` em falha real, com liberação da reserva para permitir retry
  - ✅ Remoção da deduplicação em memória dentro da extração da Evolution API
  - ✅ Configuração de TTL da idempotência via ambiente
  - ✅ Testes cobrindo evento novo, duplicado, mensagem sem `message_id` e liberação da reserva em erro
- **Critérios de aceite:**
  - O mesmo evento não é processado duas vezes após retry ou reinício
  - Falhas reais não retornam sucesso enganoso para a origem
  - O fluxo de confirmação continua consistente sob concorrência
- **Arquivos impactados:**
  - `app/main.py`
  - `app/services/evolution.py`
  - `app/services/webhook_idempotency.py`
  - `tests/test_webhook.py`
  - `tests/test_evolution.py`
  - `docker-compose.yml`
  - `.env.example`

### 0.7 Isolamento das Chamadas do Gemini do Event Loop
- **Complexidade:** Média 🟡
- **Valor:** Alto
- **Status:** Implementado
- **Problema identificado:**
  - `generate_content()` do SDK atual é síncrono e está sendo chamado dentro do fluxo assíncrono
  - Chamadas lentas podem bloquear o event loop, atrasando webhooks, scheduler e respostas HTTP
- **Implementação:**
  - ✅ Isolamento das chamadas bloqueantes do SDK do Gemini com `asyncio.to_thread(...)`
  - ✅ Adição de timeout configurável por ambiente para chamadas ao Gemini
  - ✅ Tratamento de timeout como falha recuperável dentro da cadeia de fallback de modelos
  - ✅ Preservação do fallback automático entre modelos já existente
  - ✅ Testes cobrindo sucesso, timeout com fallback, timeout total e execução fora do event loop
- **Critérios de aceite:**
  - Uma chamada lenta de IA não degrada o processamento de outros eventos
  - Timeouts geram resposta amigável sem travar a aplicação
  - O fallback de modelos continua funcionando
- **Arquivos impactados:**
  - `app/config.py`
  - `app/services/gemini.py`
  - `tests/test_gemini.py`
  - `tests/conftest.py`
  - `docker-compose.yml`
  - `.env.example`

### 0.8 Correções de Lógica Financeira e Recorrência
- **Complexidade:** Baixa/Média 🟡
- **Valor:** Médio/Alto
- **Status:** Implementado
- **Problema identificado:**
  - Parcelamentos podem perder centavos por arredondar todas as parcelas igualmente
  - O preview de recorrências futuras simplifica datas com `min(..., 28)`, gerando resultados incorretos no fim do mês
  - Alguns campos aceitos em restore e confirmação ainda dependem demais de inferência livre
- **Implementação:**
  - ✅ Distribuição do resíduo de arredondamento na última parcela para fechar exatamente o total original
  - ✅ Validações adicionais de consistência financeira no fluxo de criação de despesas
  - ✅ Reescrita do preview de recorrências futuras com datas reais, incluindo virada de mês
  - ✅ Endurecimento de combinações financeiras inválidas no restore de backup
  - ✅ Testes cobrindo centavos residuais, limites de parcelamento, percentual compartilhado inválido, conversão incompleta e recorrência em fim de mês
- **Critérios de aceite:**
  - A soma das parcelas sempre fecha exatamente com o valor original
  - Recorrências futuras aparecem com dias corretos ao longo dos meses
  - Persistência rejeita combinações inconsistentes de campos críticos
- **Arquivos impactados:**
  - `app/services/expense.py`
  - `app/services/recurring.py`
  - `app/services/backup.py`
  - `tests/test_expense.py`
  - `tests/test_scheduler.py`

### 0.9 Auditoria Complementar: Riscos Residuais e Hardening Adicional
- **Complexidade:** Média/Alta 🔴
- **Valor:** Muito Alto
- **Status:** Em andamento
- **Contexto:**
  - O projeto já recebeu uma boa rodada de hardening, mas ainda existem riscos residuais de duplicidade, exposição de erro, fragilidade operacional e decisões de produto que precisam virar regra explícita
  - Esta etapa consolida os achados da revisão completa do projeto para evitar que eles se percam fora do roadmap

#### 0.9.1 Idempotência real após efeitos colaterais
- **Status:** Implementado
- **Problema identificado:**
  - O webhook reserva o `message_id`, mas em caso de exceção posterior a reserva pode ser liberada mesmo depois de persistência em banco
  - Isso abre espaço para reprocessamento do mesmo evento e duplicidade de lançamentos em cenários de retry parcial
  - O risco aumenta quando o registro no banco acontece antes do envio das mensagens finais ao usuário
- **Implementação sugerida:**
  - Persistir uma trilha de processamento por `message_id` com estados explícitos (`received`, `processing`, `committed`, `failed`)
  - Evitar liberar a idempotência quando já houve commit de efeito de negócio
  - Garantir que mensagens de saída e alertas sejam tratadas como side effects secundários, sem reabrir a operação principal
  - Adicionar testes cobrindo falha depois do commit e retry da mesma mensagem
- **Critérios de aceite:**
  - O mesmo evento não gera duas despesas mesmo com falha parcial e retry da origem
  - O sistema diferencia falha antes do commit e falha depois do commit
  - O retry fica seguro sem depender de timing entre banco, Redis e Evolution API
- **Implementação realizada:**
  - ✅ Sinalização explícita de `processing_committed` no handler
  - ✅ Preservação da reserva de idempotência quando a falha acontece após persistência
  - ✅ Resposta controlada `ok_committed_with_warnings` para falhas pós-commit
  - ✅ Testes cobrindo falha antes e depois do commit

#### 0.9.2 Sanitização de erros HTTP e respostas internas
- **Status:** Implementado
- **Problema identificado:**
  - Endpoints administrativos e webhook ainda devolvem `str(e)` em alguns fluxos
  - Isso pode expor detalhes internos, mensagens de biblioteca, estrutura de integração e pistas úteis para diagnóstico por terceiros
- **Implementação sugerida:**
  - Substituir mensagens técnicas por erros genéricos para o cliente
  - Manter detalhes completos apenas em log estruturado
  - Introduzir um padrão único de tratamento de exceção para rotas FastAPI
- **Critérios de aceite:**
  - Nenhum endpoint expõe stack, exceções brutas ou mensagens internas sensíveis ao cliente
  - Logs continuam suficientes para troubleshooting operacional
- **Implementação realizada:**
  - ✅ Remoção de `str(e)` das respostas HTTP do webhook e endpoints admin
  - ✅ Mensagens genéricas para cliente com log detalhado apenas no servidor
  - ✅ Ajustes de testes cobrindo sanitização

#### 0.9.3 Healthcheck real e startup degradado
- **Status:** Implementado
- **Problema identificado:**
  - O endpoint `/health` sempre responde saudável mesmo quando dependências críticas podem estar indisponíveis
  - O startup continua em modo degradado se Evolution falhar, mas sem sinalização operacional forte
- **Implementação sugerida:**
  - Criar healthchecks separados: `liveness` e `readiness`
  - Validar conectividade com banco, Redis e Evolution API no readiness
  - Expor estado degradado quando integrações opcionais falharem
  - Adicionar `healthcheck` ao serviço `finbot` no `docker-compose`
- **Critérios de aceite:**
  - Orquestradores conseguem distinguir instância viva de instância pronta
  - Falhas de dependência aparecem claramente para operação
  - O comportamento degradado é explícito e observável
- **Implementação realizada:**
  - ✅ Endpoints `/health/live` e `/health/ready`
  - ✅ `/health` convertido para readiness com checagem de banco, Redis e Evolution
  - ✅ `healthcheck` do serviço `finbot` no `docker-compose`

#### 0.9.4 Redis como ponto único de consistência distribuída
- **Status:** Implementado
- **Problema identificado:**
  - Idempotência, rate limit e backup temporário têm fallback em memória local
  - Isso funciona em ambiente simples, mas quebra consistência em múltiplos processos ou múltiplas réplicas
- **Implementação sugerida:**
  - Definir formalmente se o produto opera apenas em instância única ou se suportará horizontal scaling
  - Se suportar múltiplas instâncias, tornar Redis obrigatório para os fluxos que dependem de consistência compartilhada
  - Adicionar métricas e alertas para fallback em memória, tratando-o como estado degradado
- **Critérios de aceite:**
  - O comportamento em multi-instância é previsível e documentado
  - Queda do Redis não produz duplicidade silenciosa nem limites inconsistentes
- **Implementação realizada:**
  - ✅ Scheduler protegido com lock distribuído por job
  - ✅ `RateLimitService` passa a falhar de forma explícita em `multi_instance` quando Redis está indisponível
  - ✅ Armazenamento temporário de backup deixa de usar fallback silencioso em `multi_instance`
  - ✅ Webhook agora responde com mensagens claras quando o storage compartilhado está indisponível
  - ✅ Eventos operacionais recentes expostos nos health endpoints para tornar fallback/degradação observáveis
  - ✅ Serviços críticos registram eventos operacionais quando entram em modo degradado

#### 0.9.5 Scheduler com trava distribuída
- **Status:** Implementado
- **Problema identificado:**
  - Cada instância do `finbot` sobe o scheduler localmente
  - Em múltiplas réplicas isso pode gerar lembretes duplicados, atualizações repetidas e concorrência entre jobs
- **Implementação sugerida:**
  - Adotar lock distribuído para jobs agendados ou mover scheduler para um worker dedicado
  - Documentar claramente o modo suportado de deploy
  - Adicionar testes ou validações de execução exclusiva por janela
- **Critérios de aceite:**
  - Jobs recorrentes rodam uma única vez por janela esperada
  - O deploy com mais de uma réplica não duplica notificações
- **Implementação realizada:**
  - ✅ Lock distribuído por job com Redis
  - ✅ Política explícita de `DEPLOYMENT_MODE` (`single_instance` vs `multi_instance`)
  - ✅ Bloqueio de execução quando Redis está indisponível em `multi_instance`
  - ✅ Fallback controlado para execução local apenas em `single_instance`
  - ✅ Testes cobrindo lock adquirido, lock ocupado e indisponibilidade de Redis

#### 0.9.6 Política de restore de backup com migração de número
- **Status:** Parcialmente implementado
- **Problema identificado:**
  - Hoje o restore aceita backup de outra origem sem uma política explícita
  - Bloquear rigidamente por `source_phone == target_phone` parece seguro, mas quebra um caso legítimo: usuário que trocou de número e precisa migrar o histórico
  - Liberar irrestritamente também aumenta risco de restauração indevida para o número errado
- **Diretriz de produto:**
  - O sistema deve permitir migração entre números quando isso representar continuidade legítima do mesmo usuário
  - A política não deve ser “sempre bloquear número diferente” nem “sempre aceitar qualquer origem”
- **Implementação sugerida:**
  - Exigir confirmação explícita reforçada quando `source_phone` for diferente do número atual
  - Exibir com clareza origem e destino da restauração antes do aceite final
  - Registrar auditoria da migração de backup entre números
  - Avaliar uso de um identificador estável de usuário no backup, reduzindo dependência exclusiva do telefone
  - Considerar flag/configuração para permitir restore cruzado apenas em modo assistido ou com confirmação adicional
- **Critérios de aceite:**
  - Um usuário consegue migrar backup do número antigo para o novo com segurança
  - O fluxo reduz o risco de restaurar dados de terceiros por engano
  - A decisão fica documentada como regra de negócio, não como comportamento implícito
- **Implementação realizada:**
  - ✅ Confirmação reforçada `sim migrar` quando origem e destino do backup diferem
  - ✅ Exibição explícita do contexto de migração antes do restore
  - ✅ Auditoria persistida em banco para restores, com origem, destino, status e confirmação explícita de migração
  - ⏳ Ainda falta avaliar identificador estável de usuário para reduzir dependência exclusiva do telefone

#### 0.9.7 Hardening adicional de Docker e supply chain
- **Status:** Parcialmente implementado
- **Problema identificado:**
  - Imagens base e de terceiros não estão fixadas por digest
  - Dependências Python não usam hashes de integridade
  - Portas administrativas e da Evolution estão publicadas para o host por padrão
- **Implementação sugerida:**
  - Fixar imagens críticas por digest em produção
  - Avaliar travamento de dependências com hashes
  - Rever exposição de portas e preferir publicação apenas quando necessário
  - Adicionar uma rotina de auditoria de dependências (`pip-audit` ou equivalente) na CI
- **Critérios de aceite:**
  - A cadeia de build fica mais reprodutível e auditável
  - A superfície de exposição padrão do ambiente sobe mais fechada
- **Implementação realizada:**
  - ✅ `Dockerfile` convertido para multi-stage build, removendo toolchain de compilação da imagem final
  - ✅ Runtime com `PYTHONDONTWRITEBYTECODE` e `PYTHONUNBUFFERED`
  - ✅ Serviço `finbot` endurecido com `read_only`, `tmpfs`, `cap_drop: [ALL]` e `no-new-privileges`
  - ✅ Portas publicadas em `localhost` por padrão no Compose
  - ✅ Job de `pip-audit` adicionado à CI em modo não-bloqueante
  - ⏳ Ainda faltam pin por digest das imagens e, se desejado, hashes de integridade para dependências Python

#### 0.9.8 Proteção adicional dos segredos estáticos
- **Status:** Implementado
- **Problema identificado:**
  - A autenticação de admin e webhook depende de bearer secrets estáticos
  - A validação atual usa comparação simples de string
- **Implementação sugerida:**
  - Trocar para `secrets.compare_digest`
  - Adicionar rate limit ou proteção adicional aos endpoints administrativos
  - Documentar rotação periódica de segredos
- **Critérios de aceite:**
  - Comparações sensíveis passam a usar comparação em tempo constante
  - Endpoints administrativos ficam menos suscetíveis a abuso por força bruta ou enumeração
- **Implementação realizada:**
  - ✅ Migração para `secrets.compare_digest`
  - ✅ Rate limit por IP/janela para endpoints administrativos
  - ✅ Falha fechada da proteção administrativa em `multi_instance` quando o storage compartilhado estiver indisponível

#### 0.9.9 Observações de validação
- **Resultado da revisão local:**
  - Suíte de testes local executada com sucesso: 292 testes passando
  - `ruff check .` sem achados
  - `python -m compileall app tests` sem erros
  - Não foi executada auditoria online de CVEs de dependências durante a revisão

#### 0.9.10 Ordem sugerida de execução
1. Avaliar pin por digest das imagens e hashes de dependências Python
2. Avaliar identificador estável de usuário para migração de backup entre números

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

### 5.1 Onboarding Multi-Usuários ✅
- **Complexidade:** Alta 🔴
- **Valor:** Alto (mas estrutura já suporta)
- **Status:** Implementado
- **Implementação:**
  - ✅ Tabela `users` criada com identidade, aceite de termos, metadados de uso e limites configuráveis
  - ✅ Remoção do gate funcional de usuário único baseado em `OWNER_PHONE`
  - ✅ Allowlist operacional opcional via `allowed_numbers`, sem bloquear o modelo multi-usuário
  - ✅ `UserService` para criação automática de usuário, aceite de termos e atualização de limites
  - ✅ Onboarding com aceite obrigatório antes de registrar dados, processar mídia ou chamar Gemini
  - ✅ Reaproveitamento de `PendingConfirmation` para fluxo de `user_onboarding`
  - ✅ `RateLimitService` com Redis e fallback em memória
  - ✅ Limites diários por usuário para texto, mídia e chamadas de IA
  - ✅ Comandos locais para consultar e ajustar limites sem depender de IA
  - ✅ Intents adicionais no Gemini para limites por usuário
  - ✅ Base preparada para futura interface web com campos persistidos de perfil e preferências
  - ✅ 7 testes adicionais cobrindo usuários, rate limit, onboarding e comandos de limites
- **Termos de Uso - Diretriz Inicial:**
  - O texto deve deixar claro que o FinBot é operado em ambiente `self-hosted`
  - Os dados permanecem sob a infraestrutura administrada pelo operador da instância
  - A guarda, segurança, backup e disponibilidade dependem da configuração e da operação desse ambiente
  - Formulação recomendada:
    - "Como o FinBot opera em ambiente self-hosted, a guarda e a segurança dos dados dependem da infraestrutura administrada pelo operador da instância."
- **Decisões Iniciais:**
  - O controle de acesso deixa de depender de um único telefone
  - O aceite de termos passa a ser obrigatório antes do uso efetivo
  - Limites de uso serão configuráveis por usuário
  - A modelagem já deve nascer preparada para a futura interface web
- **Critérios de Aceite:**
  - Um número novo consegue iniciar conversa sem pré-cadastro manual no `.env`
  - Antes do aceite, o usuário não consegue registrar dados nem consumir IA
  - Após o aceite, o usuário usa normalmente o sistema com isolamento por `user_phone`
  - Cada usuário consegue consultar e ajustar seus limites diários
  - A estrutura criada permite reaproveitamento em futura interface web sem refatoração profunda
- **Arquivos:**
  - `app/database/models.py` - tabela `users` e campos de preferências/limites
  - `app/services/user.py` - gestão de usuários e onboarding
  - `app/services/rate_limit.py` - limites de uso por usuário
  - `app/handlers/webhook.py` - gate de onboarding e integração com rate limit
  - `app/services/gemini.py` - intents para consultar/ajustar limites
  - `app/config.py` - defaults globais de limites e configuração inicial
  - `tests/test_user.py` - testes do fluxo de usuários
  - `tests/test_rate_limit.py` - testes de limites configuráveis
  - `tests/test_webhook.py` - testes do onboarding e bloqueios antes do aceite

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
| Hardening do webhook | Muito Alto | 🟡 | Nenhuma | ⭐⭐⭐⭐⭐ |
| Proteção admin/logs | Muito Alto | 🟡 | Hardening do webhook | ⭐⭐⭐⭐⭐ |
| Blindagem do build Docker | Muito Alto | 🟢 | Nenhuma | ⭐⭐⭐⭐⭐ |
| Limites defensivos PDF/backup | Alto | 🟡 | Backup existente | ⭐⭐⭐⭐ |
| Idempotência e retry do webhook | Alto | 🔴 | Hardening do webhook | ⭐⭐⭐⭐ |
| Isolamento do Gemini | Alto | 🟡 | Nenhuma | ⭐⭐⭐⭐ |
| Sanitização XLSX | Alto | 🟢 | XLSX existente | ⭐⭐⭐⭐ |
| Correções financeiras/recorrência | Médio-Alto | 🟡 | Funcionalidades existentes | ⭐⭐⭐ |
| ~~CI/CD e Testes~~ | ✅ | 🟢 | Nenhuma | ⭐⭐⭐⭐⭐ |
| ~~Desfazer Ação~~ | ✅ | 🟢 | Nenhuma | ⭐⭐⭐⭐⭐ |
| ~~Alertas/Limites~~ | ✅ | 🟡 | ~~Testes~~ ✅ | ⭐⭐⭐⭐ |
| ~~Scheduler Recorrentes~~ | ✅ | 🟢 | Nenhuma | ⭐⭐⭐⭐ |
| ~~Gráficos~~ | ✅ | 🟡 | Nenhuma | ⭐⭐⭐⭐ |
| ~~PDF Export~~ | ✅ | 🟢 | XLSX (existe) | ⭐⭐⭐ |
| ~~Leitura PDF~~ | ✅ | 🟡 | Webhook de mídia (existe) | ⭐⭐⭐ |
| ~~Metas~~ | ✅ | 🟡 | ~~Alertas~~ ✅ | ⭐⭐⭐ |
| ~~Conversão Moeda~~ | ✅ | 🟢 | Nenhuma | ⭐⭐⭐ |
| ~~Multi-Usuários~~ | ✅ | 🔴 | ~~Testes, CI~~ ✅ | ⭐⭐ |
| ~~Backup~~ | ✅ | 🟡 | Multi-usuários | ⭐⭐ |

---

## Ordem de Implementação Sugerida

1. **Sprint 0:** Hardening do webhook + blindagem do build Docker + proteção da superfície admin
2. **Sprint 0.1:** Idempotência/retry do webhook + isolamento do Gemini
3. **Sprint 0.2:** Limites defensivos para PDFs/backups + sanitização XLSX
4. **Sprint 0.3:** Correções de lógica financeira e recorrência
5. **Sprint 1:** ~~CI/CD + Testes~~ ✅ + ~~Desfazer Ação~~ ✅
6. **Sprint 2:** ~~Alertas/Limites~~ ✅ + ~~Scheduler Recorrentes~~ ✅
7. **Sprint 3:** ~~Gráficos~~ ✅ + ~~Metas~~ ✅
8. **Sprint 4:** ~~PDF~~ ✅ + ~~Conversão Moeda~~ ✅
9. **Sprint 5:** ~~Leitura de PDF~~ ✅ + ~~Multi-Usuários~~ ✅
10. **Sprint 6:** ~~Backup~~ ✅
