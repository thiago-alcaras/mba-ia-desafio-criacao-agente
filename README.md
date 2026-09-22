# Assistente do Residencial Aurora

API Python 3.12+ com Google ADK **2.2.0**, Gemini e SQLite. O modelo interpreta a conversa e chama as tools; autorização e persistência são verificadas em código. A resposta da API vem dos eventos finais do Runner, sem interpretação paralela por regex nem respostas fixas sobre o regulamento.

## Arquitetura

[`aurora/adk_agents.py`](aurora/adk_agents.py) constrói um agente principal e três especialistas:

| Agente | Responsabilidade | Tools |
| --- | --- | --- |
| `assistente_residencial_aurora` | Encaminhar a conversa por `transfer_to_agent` | Transferências ADK |
| `especialista_reservas` | Reservas, cancelamentos e disponibilidade | `reservar_area`, `cancelar_reserva`, `listar_minhas_reservas`, `consultar_disponibilidade` |
| `especialista_visitantes` | Autorizações e consultas próprias | `autorizar_visitante`, `listar_meus_visitantes` |
| `especialista_regulamento` | Responder dúvidas com evidência do arquivo | `consultar_regulamento` |

Os especialistas são `sub_agents`, com transferências ao principal e entre especialistas habilitadas. A separação mantém cada conjunto de tools com sua responsabilidade. O regulamento não faz parte das instruções de nenhum agente.

[`aurora/adk_runtime.py`](aurora/adk_runtime.py) usa `Runner`, `App` com `ResumabilityConfig(is_resumable=True)` e `SqliteSessionService`. Essa combinação permite que o Runner encaminhe uma resposta de confirmação ao especialista que a solicitou, inclusive após recriar o processo. O histórico é o histórico real do ADK: `GET /sessoes/{session_id}/eventos` devolve todos os eventos completos e em ordem, incluindo transferências, chamadas, resultados e confirmações.

Fluxo: API → Runner → principal/especialista → tool → SQLite ou arquivo → evento ADK → resposta. A API não interpreta intenções nem executa operações de domínio fora das tools. Falhas do provedor retornam `503`; não há modo de produção que simule sucesso quando falta uma chave.

## Garantias

### 1. Cobrança ou acesso só com confirmação

Em [`aurora/service.py`](aurora/service.py), `reservar_area` consulta a taxa em `dados/areas.json`; taxa positiva passa por `confirmed`. `autorizar_visitante` sempre passa por essa verificação. `confirmed` chama `ToolContext.request_confirmation`, com os detalhes reais da operação. Não grava enquanto não houver `tool_confirmation.confirmed` do ADK. Quadra gratuita e cancelamento próprio não passam por confirmação.

Em [`aurora/adk_runtime.py`](aurora/adk_runtime.py), `pending` reconstrói pendências a partir das chamadas reais `adk_request_confirmation` e das respostas persistidas. `confirm` envia `types.FunctionResponse` com o mesmo ID; o Runner retoma a chamada original, com seus argumentos originais. A rota nunca chama diretamente a função que reserva ou autoriza.

Em [`aurora/api.py`](aurora/api.py), `confirm` verifica que o ID está pendente naquela sessão; ID desconhecido, de outra sessão ou já respondido recebe `409`. Apenas essa rota constrói uma resposta de confirmação. O chat recebe somente texto; enquanto existe pendência, devolve as pendências atuais sem iniciar outra execução. Locks por sessão serializam mensagens e confirmações no processo documentado. `mutate` mantém um recibo por sessão/chamada junto do efeito na mesma transação, impedindo efeito duplicado ao reexecutar a mesma tool.

### 2. Cada sessão pertence a um apartamento

`create_session`, em [`aurora/api.py`](aurora/api.py), fixa a identidade na tabela `sessions` e no state ADK. `apartment_for`, em [`aurora/service.py`](aurora/service.py), lê o state do contexto injetado pelo ADK e o valida contra o vínculo do banco. Nenhuma tool expõe parâmetro `apartamento` ou `confirmado` ao modelo.

Listagem e cancelamento sempre filtram pelo apartamento validado. A disponibilidade consulta somente `SELECT 1`, e o conflito de reserva devolve apenas indisponibilidade, sem código, nome ou apartamento de terceiros. Alterar o texto do pedido não altera a identidade usada nas queries.

### 3. Nada se perde no reinício

[`aurora/database.py`](aurora/database.py) mantém reservas, visitantes, vínculo de sessão, códigos emitidos e recibos em `.aurora/aurora.sqlite3`. O serviço ADK persiste state e eventos completos em `.aurora/adk_sessions.sqlite3`. O startup chama `ensure_database`, nunca restaura dados existentes.

`issued_codes` preserva códigos após cancelamentos. `reservar_area` gera UUID, confere códigos emitidos e registra o código e a reserva na mesma transação. O cancelamento não apaga o código emitido.

### 4. O regulamento é consultado, não carregado

`consultar_regulamento`, em [`aurora/service.py`](aurora/service.py), lê `dados/regulamento.md`, seleciona um capítulo pelo assunto e retorna até dois artigos relevantes à consulta, com fonte e título. Capítulos de outros assuntos não são retornados. As expressões regulares existentes servem apenas para dividir o Markdown e tokenizar a busca, nunca para decidir ações da conversa.

O especialista produz a resposta usando o resultado da tool. O horário da piscina não está fixado no código da aplicação. O teste com um regulamento temporário de conteúdo diferente demonstra que o resultado acompanha o arquivo.

### 5. Dois moradores, uma reserva

[`aurora/database.py`](aurora/database.py) define `UNIQUE(area, day)`. `mutate`, em [`aurora/service.py`](aurora/service.py), usa `BEGIN IMMEDIATE`, e `reservar_area` captura o conflito de unicidade no instante do `INSERT`. A tool perdedora retorna `status=indisponivel`; o Runner produz uma resposta normal e a API retorna `200` quando o provedor está disponível. Não depende de uma consulta prévia à agenda.

## Como rodar

Pré-requisitos: Python 3.12+, [uv](https://docs.astral.sh/uv/) e uma chave do Google AI Studio com acesso ao modelo escolhido. Não é necessário Docker nem banco externo.

```bash
cp .env.example .env
uv sync --locked
```

No PowerShell, use `Copy-Item .env.example .env`. Preencha as variáveis localmente:

| Variável | Uso |
| --- | --- |
| `GOOGLE_API_KEY` | Obrigatória para conversar com o Gemini. Nunca versione a chave. |
| `GEMINI_MODEL` | Opcional; vazio usa `gemini-3.8-flash`. Escolha um modelo Gemini com suporte a function calling disponível no seu projeto. |

Confira [modelos Gemini](https://ai.google.dev/gemini-api/docs/models) e cotas do seu projeto antes da avaliação. A versão exata do ADK está fixada no `pyproject.toml` e no `uv.lock`.

O padrão foi conferido em 22/09/2026: [Gemini 3.8 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash) é estável e suporta function calling. O Google informa que o acesso aos modelos 2.5 está limitado a projetos que já os utilizavam; se o seu projeto tem esse acesso, é possível configurar `GEMINI_MODEL=gemini-2.5-flash`.

Com a API **parada**, restaure os dados iniciais:

```bash
uv run python -m aurora.reset
```

Esse comando apaga o estado de execução, incluindo sessões e confirmações, e recarrega reservas e visitantes a partir dos arquivos originais. Nunca altera `dados/`.

Suba a API com um processo:

```bash
uv run uvicorn aurora.api:app --host 127.0.0.1 --port 8000
```

A API fica em `http://localhost:8000`, com contrato interativo em `http://localhost:8000/docs`. Para reiniciar, use Ctrl+C e o mesmo comando de subida, **sem** restaurar os dados. O diretório `.aurora/` precisa permanecer no disco. Opcionalmente, `AURORA_RUNTIME_DIR` seleciona outro diretório de runtime, útil para testes isolados.

| Rota | Resultado |
| --- | --- |
| `POST /sessoes` com `{"apartamento":"101"}` | `201`, `{"session_id":"..."}` |
| `POST /sessoes/{id}/mensagens` com `{"texto":"..."}` | `200`, `resposta` e `confirmacoes_pendentes` |
| `POST /sessoes/{id}/confirmacoes` com `{"id":"...","confirmado":true}` | Mesmo formato; `409` se ID não pendente |
| `GET /sessoes/{id}/eventos` | Eventos completos do ADK em ordem |
| `GET /apartamentos/101/reservas` | Lista com `codigo`, `area`, `data` |
| `GET /apartamentos/302/visitantes` | Lista com `nome`, `data` |

Todas as rotas de sessão retornam `404` para sessão inexistente. As rotas de verificação consultam diretamente o SQLite, conforme o enunciado. Se houver `503` após uma ação, consulte eventos e rotas de verificação: a tool pode ter concluído antes de uma falha ao gerar o resumo. A mesma confirmação respondida continua inválida para reenvio.

### Testes de aceite sem chave

```bash
uv run python -m unittest discover -s tests -v
```

Os testes usam o Runner, os agentes, as transferências, a confirmação ADK, as tools e o SQLite reais. Somente a geração do modelo é roteirizada, exclusivamente dentro de `tests/`. Os dados ficam em um diretório temporário. Cobrem os cenários do avaliador: identidade, cancelamentos, reserva gratuita, cobrança aprovada/negada, reenvio, IDs inválidos, visitante, regulamento, eventos, persistência, códigos e concorrência. Também retomam uma confirmação em um novo processo Python.

### Teste de aceite com Gemini real

Depois de configurar a chave:

```bash
uv run python scripts/acceptance_live.py
```

O script inicia sua própria API numa porta livre, usa bancos temporários, percorre os cenários HTTP, reinicia o processo antes e depois de confirmações e dispara aprovações concorrentes. Consome chamadas Gemini e pode exigir cota paga. Não apaga o runtime normal. Sem chave, encerra explicitamente sem declarar sucesso.

A suíte sem chave foi executada nesta correção. O teste com Gemini real permanece pendente até configurar uma chave; testes roteirizados não comprovam a interpretação de linguagem natural do provedor.

Referências: [confirmações de tools do ADK](https://google.github.io/adk-docs/tools-custom/confirmation/) e código instalado do ADK 2.2.0 (`runners.py` e `flows/llm_flows/request_confirmation.py`). A topologia e a retomada persistida estão protegidas pelos testes de integração.
