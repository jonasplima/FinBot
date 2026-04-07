# Plano de Ação - Integração das Páginas Web do FinBot

**Data de criação:** 2026-04-06
**Objetivo:** Alinhar as páginas HTML em `web/` com o fluxo e dados existentes em `app/main.py`

---

## Resumo Executivo

As páginas HTML criadas na pasta `web/` precisam de ajustes para refletir corretamente:
1. APIs configuráveis (faltando ExchangeRate API)
2. Categorias corretas do sistema
3. Métodos de pagamento
4. Fluxo de navegação entre páginas
5. Etapas do wizard de onboarding

---

## 1. Onboarding (`web/onboarding.html`)

### 1.1 ExchangeRate API - FALTANDO

**Localização no main.py:** Linha 1354
```html
<div class="stat"><strong>ExchangeRate API</strong><span>Token opcional em <a class="inline-link mono" href="https://www.exchangerate-api.com/" target="_blank" rel="noopener noreferrer">https://www.exchangerate-api.com/</a></span></div>
```

**Ação:** Adicionar campo para ExchangeRate API na seção de chaves de API (Step 2).

**Código a adicionar após Wise (linha ~835):**
```html
<div class="card">
  <div class="card-header">
    <div class="card-icon">📊</div>
    <div>
      <div class="card-title">ExchangeRate API</div>
      <div class="card-subtitle">Fallback para conversão de moedas</div>
    </div>
  </div>
  <div class="input-group">
    <label for="exchange-rate-key">API Key</label>
    <input type="password" id="exchange-rate-key" placeholder="...">
    <div class="input-hint">Obtenha em <a href="https://www.exchangerate-api.com/" target="_blank">ExchangeRate-API</a></div>
  </div>
</div>
```

### 1.2 Categorias - INCORRETAS

**Categorias atuais no HTML (ERRADAS):**
```javascript
const defaultCategories = [
  { id: 'alimentacao', name: 'Alimentação', icon: '🍽️' },
  { id: 'transporte', name: 'Transporte', icon: '🚗' },
  { id: 'moradia', name: 'Moradia', icon: '🏠' },
  { id: 'saude', name: 'Saúde', icon: '💊' },
  { id: 'educacao', name: 'Educação', icon: '📚' },
  { id: 'lazer', name: 'Lazer', icon: '🎮' },
  { id: 'compras', name: 'Compras', icon: '🛍️' },  // NÃO EXISTE
  { id: 'servicos', name: 'Serviços', icon: '🔧' },
  { id: 'salario', name: 'Salário', icon: '💰' },
  { id: 'investimentos', name: 'Investimentos', icon: '📈' },
  { id: 'freelance', name: 'Freelance', icon: '💼' },  // NÃO EXISTE
  { id: 'outros', name: 'Outros', icon: '📌' },
];
```

**Categorias corretas (de `app/database/seed.py`):**

**Despesas (Negativo):**
| Nome | Ícone Sugerido |
|------|----------------|
| Alimentação | 🍽️ |
| Assinatura | 📱 |
| Imprevistos | ⚠️ |
| Despesa Fixa | 📋 |
| Educação | 📚 |
| Emprestimo | 💳 |
| Lazer | 🎮 |
| Mercado | 🛒 |
| Moradia | 🏠 |
| Outros | 📌 |
| Parcelamento de Fatura | 💰 |
| Presente | 🎁 |
| Saúde e Beleza | 💊 |
| Servicos | 🔧 |
| Transferencia | ↔️ |
| Transporte | 🚗 |
| Vestuario | 👕 |
| Viagem | ✈️ |
| Reserva de Emergencia | 🚨 |
| Investimento | 📈 |
| Metas | 🎯 |

**Receitas (Positivo):**
| Nome | Ícone Sugerido |
|------|----------------|
| Salario - Adiantamento | 💵 |
| Salario | 💰 |
| Salario - 13o | 🎉 |
| Reembolso | 🔄 |
| Bonus | ⭐ |
| PLR | 🏆 |
| Vale Refeição | 🍴 |
| Vale Alimentação | 🛒 |
| Outros (entrada) | ➕ |

**Ação:** Substituir a lista `defaultCategories` pelas categorias corretas, agrupadas por tipo (Negativo/Positivo).

### 1.3 Funcionalidade de Categorias Customizadas - FALTANDO

**No main.py existe a possibilidade de criar categorias customizadas (linhas 1431-1442):**
```html
<form id="category-form">
    <label>Nome da categoria
        <input name="name" placeholder="Ex.: Pets, Freelance, Academia" required>
    </label>
    <label>Tipo
        <select name="type" required>
            <option value="Negativo">Despesa</option>
            <option value="Positivo">Entrada</option>
        </select>
    </label>
    <button class="primary" type="submit">Adicionar categoria</button>
</form>
```

**Funcionalidades necessárias:**
1. Formulário para adicionar nova categoria customizada
2. Seleção de tipo (Despesa/Entrada)
3. Lista de categorias ativas e ocultas
4. Capacidade de ocultar/mostrar categorias existentes
5. Endpoint: `POST /onboarding/categories` para criar
6. Endpoint: `POST /onboarding/categories/visibility` para alterar visibilidade

**Ação:** Adicionar seção no Step 5 (Categorias) com:
- Grid de categorias do sistema (com toggle para ocultar)
- Formulário para criar categorias customizadas
- Separação visual entre Despesas e Receitas

### 1.4 Etapa de Revisão (Step 6) - FALTANDO

**No main.py existe a etapa `review` (linhas 1461-1478):**
```html
<div id="step-review" class="wizard-step">
    <h2>6. Revise e conclua</h2>
    <p>Você já passou pelo essencial. Agora é só revisar rapidamente e encerrar o onboarding.</p>
    <div class="grid">
        <div class="stat"><strong id="review-terms">-</strong><span>Termos</span></div>
        <div class="stat"><strong id="review-whatsapp">-</strong><span>WhatsApp</span></div>
        <div class="stat"><strong id="review-display-name">-</strong><span>Nome de exibição</span></div>
        <div class="stat"><strong id="review-timezone">-</strong><span>Timezone</span></div>
        <div class="stat"><strong id="review-providers">-</strong><span>Chaves de API</span></div>
        <div class="stat"><strong id="review-custom-categories">-</strong><span>Categorias criadas</span></div>
        <div class="stat"><strong id="review-hidden-categories">-</strong><span>Categorias ocultadas</span></div>
    </div>
    ...
</div>
```

**Ação:** Adicionar Step 6 (Revisão) ao wizard de onboarding.

### 1.5 Fluxo de Steps

**Fluxo atual no HTML:** 5 steps (Terms → API Keys → WhatsApp → Profile → Categories)
**Fluxo correto (main.py):** 7 steps (Terms → API Keys → WhatsApp → Profile → Categories → Review → Completed)

**Mudanças necessárias:**
1. Atualizar sidebar para mostrar 7 steps
2. Adicionar panel para Step 6 (Revisão)
3. Adicionar panel para Step 7 (Concluído)
4. Ajustar navegação entre steps

---

## 2. Settings (`web/settings.html`)

### 2.1 ExchangeRate API - FALTANDO

**Localização no HTML atual (linha ~804-830):** Apenas Gemini, Groq e Wise

**Ação:** Adicionar campo para ExchangeRate API:
```html
<div class="input-group">
  <label for="api-exchange-rate">ExchangeRate API</label>
  <input type="password" id="api-exchange-rate" placeholder="..." value="">
</div>
```

### 2.2 Seções do Settings

**Seções no main.py:**
- [x] Termos aceitos
- [x] Perfil (nome, display_name, email, timezone, moeda base, separadores)
- [x] Números autorizados
- [x] Notificações
- [x] Limites diários
- [ ] **Chaves de API** (falta ExchangeRate)
- [x] Backup
- [x] Zona de perigo

### 2.3 Notificações

**Opções atuais no main.py:**
- budget_alerts (Alertas de orçamento)
- recurring_reminders (Lembretes de recorrência)
- goal_updates (Mensagens sobre metas)

**Opções atuais no HTML:**
- Alertas de orçamento ✅
- Lembretes de despesas recorrentes ✅
- Atualizações de metas ✅
- Resumo semanal ⚠️ (Não implementado no backend)

**Ação:** Implementar "Resumo semanal" corretamente:

1. **Backend (main.py):** Adicionar campo `weekly_summary` no modelo de notificações
2. **Endpoint `/settings/notifications`:** Incluir `weekly_summary` no payload
3. **Endpoint `/settings/state`:** Retornar estado do `weekly_summary`
4. **Job agendado:** Criar tarefa para enviar resumo semanal via WhatsApp

**Dados do Resumo Semanal:**
- Total de despesas da semana
- Comparação com semana anterior (%)
- Categorias com maior gasto
- Progresso das metas
- Status dos orçamentos (dentro/acima do limite)
- Próximas despesas recorrentes

**Exemplo de implementação no backend:**
```python
# Em app/database/models.py - UserPreferences
weekly_summary = Column(Boolean, default=False)

# Em app/main.py - POST /settings/notifications
@app.post("/settings/notifications")
async def update_notifications(...):
    # Incluir weekly_summary no payload
    notifications = {
        "budget_alerts": payload.budget_alerts,
        "recurring_reminders": payload.recurring_reminders,
        "goal_updates": payload.goal_updates,
        "weekly_summary": payload.weekly_summary,  # NOVO
    }
```

---

## 3. Dashboard (`web/dashboard.html`)

### 3.1 Categorias nos formulários

As categorias devem ser carregadas dinamicamente da API `/dashboard/state`, mas os exemplos hardcoded devem refletir as categorias corretas.

### 3.2 Métodos de Pagamento

**Métodos corretos (de `app/database/seed.py`):**
- Cartão de Crédito
- Cartão de Débito
- Dinheiro
- Pix
- Vale Alimentação
- Vale Refeição

**Ação:** Verificar se os métodos de pagamento estão corretos no dashboard.

### 3.3 Seções do Dashboard

**Seções no main.py:**
- [x] Hero com seletor de período
- [x] Navegação rápida
- [x] Stats de resumo
- [x] Lançamentos (expense form + receipt dropzone + table)
- [x] Orçamentos (budget form + table + charts)
- [x] Metas (goal form + contribution + withdrawal + list)
- [x] Histórico de alterações (audits)
- [x] Conversão de moeda
- [x] Exportação

### 3.4 Campos do formulário de despesas

**Campos no main.py:**
- expense_id (hidden)
- description
- amount
- category
- payment_method
- expense_date
- currency
- is_shared (checkbox)
- shared_percentage

**Ação:** Verificar se todos os campos estão presentes no dashboard.html

---

## 4. Login (`web/login.html`)

### 4.1 Navegação

**Ação:** Verificar links de navegação:
- Após login bem-sucedido → `/web/onboarding` (se não completou) ou `/web/dashboard`
- Link para criar conta → aba de registro

---

## 5. Navegação entre Páginas

### 5.1 Links corretos

| Origem | Destino | Condição |
|--------|---------|----------|
| login.html | /web/onboarding | Após login, se onboarding não completado |
| login.html | /web/dashboard | Após login, se onboarding completado |
| onboarding.html | /web/settings | Após concluir onboarding |
| dashboard.html | /web/settings | Botão "Configurações" |
| dashboard.html | /web/login | Botão "Sair" |
| settings.html | /web/dashboard | Botão "Painel" |
| settings.html | /web/login | Botão "Sair" |

---

## 6. Endpoints da API

### 6.1 Endpoints usados pelo Onboarding

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| /onboarding/state | GET | Estado atual do onboarding |
| /onboarding/step | POST | Atualizar step atual |
| /onboarding/terms/accept | POST | Aceitar termos |
| /onboarding/terms/reject | POST | Rejeitar termos |
| /onboarding/profile | POST | Salvar perfil |
| /onboarding/credentials | GET | Listar credenciais |
| /onboarding/credentials | POST | Salvar credencial |
| /onboarding/categories | GET | Listar categorias |
| /onboarding/categories | POST | Criar categoria customizada |
| /onboarding/categories/visibility | POST | Alterar visibilidade |
| /onboarding/complete | POST | Concluir onboarding |
| /onboarding/whatsapp/prepare | POST | Preparar sessão WhatsApp |
| /onboarding/whatsapp/status | GET | Status da conexão |
| /onboarding/whatsapp/qrcode | POST | Gerar QR Code |
| /onboarding/whatsapp/refresh | POST | Atualizar status |

### 6.2 Endpoints usados pelo Settings

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| /settings/state | GET | Estado das configurações |
| /settings/profile | POST | Atualizar perfil |
| /settings/notifications | POST | Atualizar notificações |
| /settings/limits | POST | Atualizar limites |
| /settings/authorized-phones | POST | Adicionar número |
| /settings/authorized-phones | DELETE | Remover número |
| /settings/backup/export | POST | Exportar backup |
| /settings/backup/import/preview | POST | Preview do backup |
| /settings/backup/import/apply | POST | Aplicar backup |

### 6.3 Endpoints usados pelo Dashboard

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| /dashboard/state | GET | Estado do dashboard |
| /dashboard/profile/base-currency | POST | Alterar moeda base |
| /dashboard/expenses | POST | Criar/editar despesa |
| /dashboard/expenses/recognize | POST | Reconhecer recibo |
| /dashboard/budgets | POST | Criar/atualizar orçamento |
| /dashboard/goals | POST | Criar meta |
| /dashboard/goals/contribute | POST | Aportar em meta |
| /dashboard/goals/withdraw | POST | Usar valor da meta |
| /dashboard/export | POST | Exportar dados |
| /dashboard/currency/convert | POST | Converter moeda |

---

## 7. Checklist de Implementação

### 7.1 Prioridade Alta (Funcionalidade)
- [ ] Adicionar ExchangeRate API no onboarding.html
- [ ] Adicionar ExchangeRate API no settings.html
- [ ] Corrigir lista de categorias no onboarding.html (30 categorias do sistema)
- [ ] Adicionar funcionalidade de categorias customizadas no onboarding.html
- [ ] Adicionar step de revisão no onboarding.html
- [ ] Implementar "Resumo semanal" com dados reais (frontend + backend)

### 7.2 Prioridade Média (Integrações)
- [ ] Conectar formulários aos endpoints corretos
- [ ] Implementar carregamento dinâmico de categorias via API
- [ ] Implementar carregamento de métodos de pagamento via API
- [ ] Ajustar fluxo de navegação entre páginas

### 7.3 Prioridade Baixa (Polish)
- [ ] Verificar consistência visual entre páginas
- [ ] Testar responsividade em todos os breakpoints
- [ ] Adicionar estados de loading nos formulários
- [ ] Adicionar tratamento de erros amigável

---

## 8. Próximos Passos

1. **Imediato:** Aplicar correções de Prioridade Alta
2. **Curto prazo:** Conectar formulários aos endpoints
3. **Médio prazo:** Testes de integração completos
4. **Longo prazo:** Mover HTML de main.py para arquivos em web/

---

## Anexo: Providers de API Suportados

De `app/services/credentials.py`:
```python
PROVIDER_LABELS = {
    "gemini": "Google Gemini",
    "groq": "Groq",
    "wise": "Wise",
    "exchange_rate": "ExchangeRate API",
}

PROVIDER_DOCS = {
    "gemini": "https://aistudio.google.com/apikey",
    "groq": "https://console.groq.com/keys",
    "wise": "https://wise.com/your-account/integrations-and-tools/api-tokens",
    "exchange_rate": "https://www.exchangerate-api.com/",
}
```
