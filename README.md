# Assistente do Residencial Aurora

API do assistente virtual do Residencial Aurora. O Google ADK organiza o agente principal e especialistas; a API, as tools e o SQLite impõem as regras que não podem depender de uma resposta do modelo.

## Arquitetura

`aurora/adk_agents.py` declara o agente principal `assistente_residencial_aurora` e três especialistas Google ADK, todos com modelo Gemini configurado por `GEMINI_MODEL`:

- `especialista_reservas`: trata reserva e cancelamento.
- `especialista_visitantes`: trata solicitações de entrada.
- `especialista_regulamento`: trata consultas pontuais ao regulamento.

O agente principal não recebe o regulamento completo. A consulta ao regulamento é feita de forma pontual em `aurora/service.py`, e só o resultado necessário volta para a sessão.

`aurora/api.py` expõe a API FastAPI. `aurora/service.py` concentra as tools de domínio e usa o apartamento guardado na sessão, nunca uma unidade mencionada no texto. `aurora/database.py` mantém a persistência SQLite e a restrição de exclusividade. Essa separação torna o modelo responsável por orientar a conversa, mas deixa autorização, cobrança, identidade e gravação exclusivamente no código.

## Garantias

### 1. Cobrança ou acesso só com confirmação

`aurora/service.py:request_confirmation` persiste uma confirmação pendente por sessão. `answer_confirmation` aceita somente uma confirmação ainda pendente, retornando `409` para qualquer outro id; uma negação não grava nada. Reserva com taxa e autorização de visitante passam obrigatoriamente por esse caminho. A mensagem do morador nunca é uma confirmação.

### 2. Cada sessão pertence a um apartamento

`aurora/api.py:create_session` valida e fixa o apartamento uma única vez. `aurora/service.py:assistant_message` lê esse valor diretamente da tabela `sessions` e todas as queries e gravações usam essa identidade. Nenhuma tool recebe apartamento como argumento do texto do usuário, portanto uma tentativa de citar outra unidade não dá acesso aos seus dados.

### 3. Nada se perde no reinício

`aurora/database.py` grava sessões, eventos, confirmações, reservas e visitantes em `.aurora/aurora.sqlite3`. A inicialização em `aurora/api.py` apenas cria os dados quando o banco ainda não existe; ela não restaura nem apaga dados em um restart normal.

### 4. O regulamento é consultado, não carregado

`aurora/adk_agents.py` não inclui o regulamento na instrução do agente principal. Em `aurora/service.py`, a pergunta sobre piscina aos domingos usa uma consulta específica e registra somente `consulta pontual: horario da piscina aos domingos` no evento; nenhum capítulo inteiro entra na sessão.

### 5. Dois moradores, uma reserva

`aurora/database.py` declara `UNIQUE(area, day)` na tabela `reservations`. `aurora/service.py:create_reservation` e `answer_confirmation` usam transações SQLite com `BEGIN IMMEDIATE` e inserção protegida pela mesma restrição. Em duas aprovações simultâneas, uma cria a reserva e a outra recebe uma resposta normal de indisponibilidade.

## Como rodar

Pré-requisitos: Python 3.12+, [uv](https://docs.astral.sh/uv/) e uma chave do Google AI Studio.

```bash
cp .env.example .env
```

Preencha `GOOGLE_API_KEY` no `.env`. Opcionalmente ajuste `GEMINI_MODEL`; o padrão é `gemini-2.5-flash`.

Instale as dependências, com versões travadas em `uv.lock`:

```bash
uv sync
```

Restaure o estado inicial do condomínio. Esse comando recria somente o banco de runtime e nunca altera `dados/`:

```bash
uv run python -m aurora.reset
```

Suba a API em `http://localhost:8000`:

```bash
uv run uvicorn aurora.api:app --host 0.0.0.0 --port 8000
```

Rotas principais:

- `POST /sessoes`
- `POST /sessoes/{session_id}/mensagens`
- `POST /sessoes/{session_id}/confirmacoes`
- `GET /sessoes/{session_id}/eventos`
- `GET /apartamentos/{apartamento}/reservas`
- `GET /apartamentos/{apartamento}/visitantes`

## Verificação local

Foi validado localmente com Python 3.13, `uv sync`, Google ADK `2.2.0`, FastAPI `0.141.1` e SQLite. Os fluxos cobertos incluem isolamento entre apartamentos, cancelamento próprio, reserva gratuita, confirmação negada/aprovada/repetida, autorização de visitante, consulta da piscina, persistência após restart e duas aprovações concorrentes para a mesma área/data.
