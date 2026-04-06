# Onboarding Web e Configuração por Usuário

## Objetivo

Criar uma interface web de onboarding para que cada usuário possa:

- aceitar os termos de uso;
- informar suas próprias chaves de API;
- conectar seu WhatsApp por QR Code no navegador;
- configurar preferências básicas;
- personalizar categorias;
- usar o serviço com credenciais próprias, sem depender apenas das chaves globais da instância.

## Diretrizes já definidas

- As chaves do usuário têm prioridade sobre as chaves da instância.
- Se o usuário não tiver configurado uma chave, o sistema pode usar a chave global da instância como fallback.
- `WISE_API_KEY` e `EXCHANGE_RATE_API_KEY` serão opcionais no onboarding.
- Limites de uso não entram no onboarding inicial.
- Categorias padrão do sistema continuam existindo.
- Cada usuário pode criar categorias personalizadas próprias.
- A Evolution será usada em modelo de instância compartilhada com múltiplas sessões.
- O onboarding já deve criar o acesso autenticado ao futuro painel web.
- O usuário deve definir senha durante o onboarding.

## Resolução de credenciais

Para qualquer integração externa, a ordem de resolução será:

1. Credencial do usuário
2. Credencial global da instância
3. Erro funcional claro se nenhuma credencial estiver disponível

Isso vale para:

- Gemini
- Groq
- Wise
- ExchangeRate API

## Ponto crítico de privacidade

### Decisão de produto

O onboarding deve deixar explícito que a infraestrutura é self-hosted e que o operador da instância pode ter visibilidade técnica sobre metadados e potencialmente sobre conteúdo trafegado pela stack, dependendo da configuração e do nível de acesso operacional.

### Motivo

A documentação oficial da Evolution expõe recursos para:

- salvar mensagens e chats em armazenamento persistente;
- receber eventos de mensagens por webhook;
- consultar mensagens por API.

Isso significa que não devemos prometer isolamento absoluto do operador da instância apenas pela interface do produto.

### Consequência prática

No onboarding, antes da conexão do WhatsApp, deve existir um aviso claro informando:

- que a instância é administrada por um operador;
- que o serviço depende de infraestrutura self-hosted;
- que pode existir acesso técnico/operacional aos dados;
- que o usuário só deve prosseguir se concordar com esse modelo.

### Referências oficiais

- Evolution API docs, variáveis de persistência de dados e chats/mensagens:
  https://doc.evolution-api.com/v2/en/env
- Evolution API docs, webhook e eventos de mensagens:
  https://doc.evolution-api.com/v1/en/configuration/webhooks
- Evolution API docs, endpoint de consulta de mensagens:
  https://doc.evolution-api.com/v2/api-reference/chat-controller/find-messages

## Estratégia de autenticação web

### Decisão de produto

O onboarding web deve inaugurar a conta do usuário para o painel futuro, onde a pessoa poderá revisar finanças, atualizar credenciais, gerenciar categorias e consultar preferências.

### Consequência prática

O usuário deve criar credenciais de acesso web durante o onboarding.

### Recomendação

Usar:

- email + senha como credencial principal do painel;
- telefone do WhatsApp como vínculo operacional da conta;
- sessão autenticada no navegador para proteger QR Code, credenciais e configurações.

### Requisitos mínimos

- hash forte de senha (`argon2` preferencialmente, ou `bcrypt`);
- nunca armazenar senha em texto puro;
- sessão segura no navegador;
- proteção de rotas autenticadas;
- base pronta para recuperação de senha no futuro.

## Escopo funcional do onboarding

### Obrigatório

1. Aceite dos termos
2. Criação de acesso web com senha
3. Cadastro de ao menos uma chave de IA
4. Conexão do WhatsApp por QR Code

### Opcional no onboarding

1. Chaves de câmbio
2. Perfil básico
3. Preferências de notificação
4. Categorias personalizadas iniciais

### Fora do onboarding inicial

1. Limites de uso
2. Orçamentos
3. Metas
4. Recorrências
5. Importação de backup

Esses itens podem ser configurados depois no painel.

## Fluxo de telas

### Tela 1. Boas-vindas

Objetivo:

- apresentar rapidamente o FinBot;
- mostrar o que será configurado;
- preparar o usuário para o fluxo.

Conteúdo:

- proposta de valor do produto;
- resumo das etapas;
- botão `Começar`.

### Tela 2. Termos e transparência de dados

Objetivo:

- coletar aceite;
- informar claramente o modelo self-hosted;
- informar limitação de privacidade operacional.

Conteúdo:

- versão dos termos;
- resumo objetivo da responsabilidade da instância;
- aviso sobre acesso técnico potencial do operador;
- ação `Aceitar e continuar`;
- ação `Recusar`.

Persistência:

- `accepted_terms`
- `accepted_terms_at`
- `terms_version`

### Tela 3. Criar acesso web

Objetivo:

- criar a identidade de acesso ao painel web;
- garantir sessão autenticada para proteger o restante do onboarding.

Campos:

- nome
- email
- senha
- confirmar senha

Regras:

- email deve ser único;
- senha mínima com política básica de segurança;
- senha armazenada apenas em hash;
- ao concluir esta etapa, o usuário já fica autenticado.

Persistência:

- `name`
- `email`
- hash de senha
- estado inicial de autenticação da conta

### Tela 4. Chaves de IA

Objetivo:

- permitir que o usuário configure sua própria conta de IA.

Campos:

- `GEMINI_API_KEY`
- `GROQ_API_KEY`

Regras:

- pelo menos uma das duas precisa estar preenchida;
- ambas podem ser preenchidas;
- o usuário deve poder testar/validar a chave;
- o sistema deve mostrar links de ajuda para obter cada chave.

UX:

- link para Google AI Studio;
- link para Groq Console;
- indicador visual de chave válida/inválida;
- texto explicando prioridade da chave do usuário e fallback da instância.

### Tela 5. Chaves de câmbio

Objetivo:

- habilitar recursos de conversão de moeda.

Campos:

- `WISE_API_KEY`
- `EXCHANGE_RATE_API_KEY`

Regras:

- etapa opcional;
- o usuário pode pular;
- a tela deve explicar onde conseguir as chaves.

UX:

- link para Wise;
- link para ExchangeRate API;
- texto explicando que o recurso continuará limitado ou indisponível sem essas credenciais.

### Tela 6. Preparação da conexão do WhatsApp

Objetivo:

- validar que a instância está pronta para exibir QR Code;
- evitar erro operacional antes da leitura.

Conteúdo:

- checklist visual:
  - Evolution acessível
  - webhook configurado
  - segredos mínimos da instância válidos
  - sessão pronta para conexão
- botão `Gerar QR Code`.

Observação:

- o usuário não deve precisar montar header manual;
- a própria aplicação web deve chamar o backend autenticado.

### Tela 7. QR Code do WhatsApp

Objetivo:

- conectar a sessão do usuário ao WhatsApp.

Conteúdo:

- QR Code grande;
- status da conexão em tempo real;
- botão `Atualizar QR Code`;
- botão `Tentar novamente`;
- instruções curtas de leitura.

Requisitos técnicos:

- autenticação própria da aplicação web;
- endpoint backend interno para buscar QR Code;
- vínculo entre usuário autenticado e sessão Evolution correspondente.

### Tela 8. Perfil básico

Objetivo:

- completar informações básicas do usuário.

Campos:

- nome
- nome de exibição
- email opcional
- timezone
- moeda padrão

Persistência:

- `name`
- `display_name`
- `email`
- `timezone`
- preferências futuras de moeda

### Tela 9. Preferências de notificação

Objetivo:

- configurar comportamento inicial de notificações.

Campos:

- receber alertas de orçamento
- receber lembretes de recorrência
- receber mensagens de metas

Persistência:

- `notification_preferences`

### Tela 10. Categorias personalizadas

Objetivo:

- permitir personalização inicial sem destruir o catálogo padrão.

Regras:

- categorias padrão continuam disponíveis;
- categorias personalizadas são por usuário;
- categorias padrão não devem ser removidas globalmente;
- o usuário pode desativar/ocultar categorias para seu próprio uso;
- o usuário pode criar novas categorias;
- categorias customizadas devem respeitar tipo:
  - gasto
  - entrada

Conteúdo:

- lista de categorias padrão;
- lista de categorias personalizadas;
- ação `Adicionar categoria`;
- ação `Ocultar para mim`;
- ação `Reativar`.

### Tela 11. Revisão final

Objetivo:

- resumir a configuração;
- dar confiança antes de concluir.

Conteúdo:

- termos aceitos;
- IA configurada;
- câmbio configurado ou pulado;
- WhatsApp conectado;
- perfil salvo;
- categorias personalizadas configuradas.

Ações:

- `Concluir onboarding`
- `Voltar e editar`

## Modelo de dados proposto

### 1. Credenciais por usuário

Tabela sugerida: `user_provider_credentials`

Campos mínimos:

- `id`
- `user_id`
- `provider`
- `api_key_encrypted`
- `api_key_last4`
- `is_active`
- `validated_at`
- `created_at`
- `updated_at`

Providers esperados:

- `gemini`
- `groq`
- `wise`
- `exchange_rate`

### 2. Conta web do usuário

Extensão da tabela `users` ou tabela dedicada de autenticação.

Campos mínimos:

- `email`
- `password_hash`
- `email_verified_at` no futuro
- `last_login_at`

### 3. Estado do onboarding

Tabela sugerida: `user_onboarding_state`

Campos mínimos:

- `id`
- `user_id`
- `current_step`
- `is_completed`
- `completed_at`
- `whatsapp_connected_at`
- `created_at`
- `updated_at`

### 4. Sessões de autenticação web

Tabela sugerida: `user_web_sessions`

Campos mínimos:

- `id`
- `user_id`
- `session_token_hash`
- `expires_at`
- `created_at`
- `last_seen_at`
- `revoked_at`

### 5. Sessões WhatsApp por usuário

Tabela sugerida: `user_whatsapp_sessions`

Campos mínimos:

- `id`
- `user_id`
- `evolution_instance`
- `session_key`
- `connection_status`
- `connected_at`
- `last_qrcode_at`
- `created_at`
- `updated_at`

Observação:

- como o produto usará múltiplas sessões em instância compartilhada, essa modelagem precisa existir antes do onboarding completo.

### 6. Categorias personalizadas

Tabela sugerida: `user_categories`

Campos mínimos:

- `id`
- `user_id`
- `name`
- `type`
- `is_active`
- `is_system_default`
- `base_category_id` opcional
- `created_at`
- `updated_at`

## Regras de negócio importantes

### Credenciais

- Nunca expor a chave completa depois de salva.
- Exibir apenas status e últimos 4 caracteres quando necessário.
- Armazenar criptografado em repouso.
- Permitir revogar e substituir a chave.

### Conta web

- Nunca armazenar senha reversível.
- Sempre armazenar somente hash forte.
- Sessão web deve ser invalidável.
- Rotas de onboarding e painel devem exigir autenticação apropriada após a criação da conta.

### Fallback

- A camada de serviço deve resolver credenciais por usuário primeiro.
- O fallback da instância deve ser controlado e explícito no código.
- Logs nunca devem vazar a chave.

### QR Code e sessão

- O frontend não deve depender de header manual no browser.
- O backend deve intermediar a chamada à Evolution.
- O usuário só pode ver o QR Code da própria sessão.

### Privacidade

- Não prometer confidencialidade absoluta do operador da instância.
- Expor o aviso de transparência antes da conexão do WhatsApp.
- Registrar aceite explícito desse modelo.

### Categorias

- Categorias padrão continuam no sistema.
- Categorias personalizadas não alteram o catálogo global.
- O parser/IA deve receber contexto das categorias do usuário.

## Plano de implementação

### Fase 1. Fundamentos de dados e segurança

Status: concluída

1. Estender modelo de usuário para autenticação web
2. Definir hash seguro de senha
3. Criar tabela de sessões web
4. Criar tabela de credenciais por usuário
5. Definir mecanismo de criptografia de segredos
6. Criar tabela de estado de onboarding
7. Criar tabela de sessões WhatsApp por usuário
8. Criar tabela de categorias personalizadas por usuário

### Fase 2. Autenticação web

Status: concluída

1. Criar registro, login e logout
2. Implementar sessão segura com cookie ou token de sessão
3. Proteger rotas do onboarding e do painel
4. Preparar base para recuperação de senha futura

### Fase 3. Camada de resolução de credenciais

Status: concluída

1. Criar serviço central para resolver credenciais
2. Integrar com `AIService`
3. Integrar com `CurrencyService`
4. Implementar fallback para credencial global da instância

### Fase 4. Sessão web e painel inicial

Status: concluída

1. Criar páginas do onboarding
2. Salvar progresso por etapa
3. Implementar validação visual das chaves
4. Exigir conta web já autenticada desde o início do fluxo

### Fase 5. Conexão do WhatsApp

1. Definir estratégia de múltiplas sessões na Evolution
2. Criar backend para emissão segura do QR Code
3. Criar polling ou atualização em tempo real do status da conexão
4. Vincular sessão Evolution ao usuário autenticado

### Fase 6. Categorias personalizadas

1. CRUD de categorias do usuário
2. Ocultar/reativar categorias padrão para o usuário
3. Injetar categorias personalizadas no contexto da IA
4. Ajustar validações e seleção de categoria

### Fase 7. Refinamento

1. Tela de configurações pós-onboarding
2. Gestão posterior de limites
3. Gestão posterior de backup/importação
4. Auditoria administrativa e UX de suporte

## Ordem recomendada de execução

1. Modelagem de dados
2. Autenticação web
3. Resolução de credenciais
4. Infra de QR Code por usuário
5. Telas 1 a 7 do onboarding
6. Telas 8 a 11
7. Categorias personalizadas integradas à IA

## Riscos e pontos de atenção

1. A parte mais sensível é múltiplas sessões do WhatsApp com isolamento por usuário.
2. O armazenamento de segredos exige criptografia séria, não apenas mascaramento.
3. Senha e sessão web exigem desenho correto desde o início.
4. A privacidade do operador da instância deve ser tratada como transparência contratual, não como suposição técnica.
5. O parser/IA precisará considerar categorias por usuário sem quebrar o comportamento atual.
6. O fluxo web e o fluxo WhatsApp precisarão coexistir sem inconsistência.
