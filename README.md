<div align="center">
  <img src="assets/logo.svg" alt="FinBot Logo" width="150" height="150">
  <h1>FinBot</h1>
  <p><b>Assistente Financeiro Inteligente Integrado ao WhatsApp</b></p>
  
  [![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)](#)
  [![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](#)
  [![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)](#)
  [![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?style=for-the-badge&logo=postgresql&logoColor=white)](#)
  [![Gemini](https://img.shields.io/badge/Google_Gemini-8E75B2?style=for-the-badge&logo=google&logoColor=white)](#)
</div>

<br/>

O **FinBot** é um assistente financeiro pessoal automatizado que opera diretamente no WhatsApp. Desenvolvido para simplificar a gestão de despesas, o projeto permite que os usuários registrem seus gastos ou receitas através de mensagens de texto ou áudio de forma natural. 

Através da integração da **Evolution API** com a inteligência artificial do **Google Gemini**, o FinBot é capaz de interpretar o contexto das conversas e organizar todas as suas informações financeiras de maneira eficiente e autônoma. Diferente das planilhas tradicionais ou aplicativos complexos, a interação ocorre exatamente onde você já se comunica no seu dia a dia.

---

## 🌟 Principais Funcionalidades

- **Processamento em Linguagem Natural:** Envie mensagens como faria com qualquer contato. O modelo do Google Gemini interpreta o contexto, categoriza a despesa e extrai os valores automaticamente.
- **Conexão Simplificada:** Interface web intuitiva para conexão do celular via QR Code (`/admin/qrcode`), oferecendo a mesma experiência do WhatsApp Web.
- **Segurança e Privacidade:** O assistente foi configurado para responder exclusivamente ao número de telefone do administrador (`OWNER_PHONE`) e aos números previamente configurados na sua estrutura de permissões. 
- **Desempenho Assíncrono:** Utilizando `asyncio` e FastAPI, o processamento e a comunicação com as APIs externas ocorrem de forma paralela, ágil e sem interrupções.
- **Persistência Confiável:** Armazenamento estruturado das interações e finanças em um banco de dados relacional PostgreSQL, somado à utilização do Redis como controle de cache em memória.
- **Exportação de Dados:** Possibilidade de geração de relatórios simples e visualização contábil (suporte à exportação para Excel via bibliotecas Python nativas).

---

## 🛠️ Arquitetura e Tecnologias

A infraestrutura do FinBot foi construída visando estabilidade, flexibilidade, facilidade de implantação e segurança. 

- **FastAPI (Python):** Framework sólido responsável pela alta performance no roteamento e gerenciamento dos webhooks.
- **Evolution API:** Interface robusta para interagir nativamente com a camada de mensagens da Meta (WhatsApp).
- **Google Gemini AI:** Modelo fundacional em nuvem encarregado da interpretação cognitiva, raciocínio lógico e classificação das entradas textuais não formatadas.
- **PostgreSQL e Redis:** Banco de dados relacional e banco de chave-valor base da persistência e controle de estado transacional.
- **Docker e Docker Compose:** Plataforma total de containerização do seu projeto. Garante que os microsserviços (API, Banco de dados, WhatsApp) estejam imutáveis e prontos para escalar.

---

## 🚀 Como Executar Localmente

### 1. Pré-requisitos
Certifique-se de ter o [Docker](https://www.docker.com/) e a extensão do [Docker Compose](https://docs.docker.com/compose/) devidamente instalados e operacionais no seu ambiente.

### 2. Configuração Básica do Repositório
Realize a clonagem do projeto para um diretório local e navegue até ele:
```bash
git clone https://github.com/jonasplima/FinBot.git
cd FinBot
```

A partir do diretório raiz, crie o arquivo definitivo de variáveis de ambiente baseando-se no modelo de propriedades que preparamos:
```bash
cp .env.example .env
```

Configure cautelosamente as credenciais no escopo do arquivo `.env`:
- `EVOLUTION_API_KEY`: Uma chave alfanumérica customizada por você para a autenticação segura do webhook.
- `OWNER_PHONE`: O seu telefone de operação principal (insira em formato internacional contínuo, sem formatação. Ex: `5511999999999`).
- `GEMINI_API_KEY`: Sua chave privada do [Google AI Studio](https://aistudio.google.com/apikey).
- `ADMIN_SECRET`: Chave secreta de proteção aos módulos remotos e painel do QR Code.

### 3. Orquestração e Inicialização
Com as chaves e propriedades devidamente validadas, emita o comando de orquestração geral para os contêineres e a base de dados:
```bash
docker-compose up -d
```
Este comando realizará o download e a compilação isolada das instâncias do FinBot, do agente local da Evolution API, do PostgreSQL e da extensão de cache.

### 4. Autenticação e Sincronização
Para vincular de fato a sua conta e conectar o aparelho em uso, acesse o painel seguro pelo navegador web:
```
http://localhost:3003/admin/qrcode?secret=SUA_SENHA_ADMIN_SECRETA
```
*(Não esqueça de substituir a `SUA_SENHA_ADMIN_SECRETA` pelo valor literal configurado na credencial do `.env`)*

Basta escanear o QR Code exibido na tela, semelhante ao processo de autenticação padrão desktop/web. Feita a leitura rápida, envie seu primeiro registro financeiro no WhatsApp, em texto livre, para que o robô faça seu trabalho.

---

## 📁 Organização de Diretórios Estruturais

Abaixo está o layout das pastas e dependências, em respeito ao paradigma de arquitetura limpa:

```text
FinBot/
├── app/                  # Código-fonte e diretórios primários da API
│   ├── database/         # Controles de inicialização, sessões com DB e migrations
│   ├── handlers/         # Processamento de fluxos dinâmicos dos webhooks
│   ├── services/         # Integrações HTTP com serviços (Evolution e Gemini)
│   ├── utils/            # Ferramentas independentes, tipagens e processadores utilitários
│   ├── config.py         # Arquitetura de mapeamento do Pydantic atrelada ao '.env'
│   └── main.py           # Entrypoint da aplicação e roteamento principal (App FastAPI)
├── docker-compose.yml    # Manifesto formalizando dependências dos Nodes, Redes e Imagens
├── Dockerfile            # Construção, Build OS e isolamento Python Runtime
├── init-db.sh            # Script Shell inicializando o PostgreSQL na primeira subida
└── .env.example          # Esqueletos das configurações de ambiente necessárias
```

**Nota sobre Segurança de Acessos:** 
A integridade dos endpoints administrativos (em especial o módulo de apresentação e geração de QR code) é garantida localmente pela injeção da query param `secret`. Qualquer tráfego suspeito será imediatamente rejeitado pela rota principal com os status `HTTPException: 401 | Unauthorized` caso essas chaves de conferência apresentem inconformidades com suas devidas variáveis de ambiente nativas.