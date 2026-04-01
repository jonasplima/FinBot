# Próximos Passos e Melhorias (Roadmap)

Este documento descreve as futuras evoluções mapeadas para o **FinBot**. Cada etapa detalha seu nível de prioridade, complexidade e instruções de alto nível (high level) para implementação.

---

## 1. Alertas e Limites de Orçamento (Budgets)
- **Prioridade:** Alta 🔴
- **Complexidade:** Média 🟡
- **Orientações (High Level):**
  - Permitir que o usuário defina limites mensais de gastos por categoria (ex: "Alimentação: R$ 500").
  - Criar um gatilho no fluxo de persistência da despesa para calcular o somatório daquele mês.
  - Integrar um prompt adicional ao Gemini para avaliar o limite e, via Evolution API, enviar alertas proativos caso o usuário atinja 80% ou 100% de sua cota estipulada.

## 2. Geração de Relatórios Visuais (Gráficos)
- **Prioridade:** Alta 🔴
- **Complexidade:** Média 🟡
- **Orientações (High Level):**
  - Adicionar ferramentas de plotagem ao backend abstrato (ex: `matplotlib`, `seaborn` ou `plotly`).
  - Identificar intenções analíticas do usuário. Quando ele perguntar "mostre meus gastos deste mês", em vez de devolver só o texto, montar um gráfico de pizza (por categoria) ou barras e transformá-lo em bytes.
  - Adaptar o handler para enviar a imagem pronta e formatada de volta para o chat do WhatsApp.

## 3. Gestão de Contas Recorrentes
- **Prioridade:** Média 🟡
- **Complexidade:** Média 🟡
- **Orientações (High Level):**
  - Criar uma abstração na modelagem (tabela no PostgreSQL) de despesas recorrentes (ex: Netflix, Aluguel, Condomínio), com dias certos de lançamento.
  - Implementar um job de background cronometrado (como o `APScheduler` ou `Celery`).
  - O job verifica diariamente as contas que vencem e realiza a inserção do gasto automaticamente, ou envia uma mensagem questionando o bot se a conta já foi paga.

## 4. Exportação de Extratos Completos (PDF/Excel)
- **Prioridade:** Média 🟡
- **Complexidade:** Baixa 🟢
- **Orientações (High Level):**
  - Atender a prompts de extração (ex: "baixe o relatório de abril pra mim").
  - Integrar bibliotecas como `pandas` para transformar as queries do banco diretamente em um `.xlsx` ou formatar em `.pdf` (com `FPDF`/`ReportLab`).
  - Interagir diretamente com o endpoint de midia/documentos da Evolution API para enviar a planilha construída no chat.

## 5. Suporte a Multi-Usuários e Segurança de Dados Isolados
- **Prioridade:** Baixa 🟢
- **Complexidade:** Alta 🔴
- **Orientações (High Level):**
  - Quebrar o bloqueio atual baseado na constante `OWNER_PHONE` no `.env`.
  - Criar uma etapa de onboarding, registrando o número que envia mensagens em uma entidade `User` (Customer) e confirmando termos de uso por WhatsApp.
  - Modificar todos os relacionamentos no banco para adicionar a segurança das chaves estrangeiras, forçando com que todas as leituras e escritas cruzem com o número identificador na requisição webhook.

## 6. Criação de Rotina de Testes e CI/CD
- **Prioridade:** Baixa 🟢
- **Complexidade:** Baixa 🟢
- **Orientações (High Level):**
  - Desenvolver testes unitários básicos e de integração para o pipeline de transação (`pytest`).
  - "Mockar" (simular) respostas do `Google Gemini` e chamadas externas para a `Evolution API`.
  - Preparar Actions (ex: GitHub Actions) que garantam a saúde da arquitetura impedindo commits defeituosos e organizando linting de código no ambiente (mypy, ruff ou flake8).
