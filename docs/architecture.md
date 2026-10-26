# Architecture

This is the reference for how the backend is layered and where each rule lives. The README
covers setup and usage; this file covers structure and the reasons for it.

## Repository layout

```
backend/
  finqon/            the installable package (the API and the agent)
  alembic/           migrations, run as the database owner (`finqon` in Docker)
  tests/
    unit/            no database, no network, no model key
    integration/     real roles and RLS on Postgres; skips when not configured
    conftest.py      fakes (scripted LLM) and fixtures
    reference.py     expected figures recomputed from the seed CSVs
  Dockerfile         targets: api (production), tools (setup, tests, eval)
db/init/roles.sql    the three application roles (cluster-level, so not an Alembic migration)
dataset/             synthetic ledger: generator, normaliser, loader, CSV output
eval/                question set and runner (measures answers, spends model quota)
docs/                this file
Makefile             the commands a developer or CI runs
.github/workflows/   CI: lint, types, unit tests, image build
```

`dataset/` and `eval/` are scripts that import `finqon`; they are not part of the shipped
package, and the `api` image does not contain them.

## Package layers

Dependencies point downwards only.

```
api/           HTTP: routers, auth dependency, middleware, health, OpenAPI. No SQL, no prompts.
services/      Use cases: ChatService, AuthService. Own transactions and persistence order.
agent/         The tool loop, prompts, grounding. Sees the outside world through
               agent/contracts.py (protocols), never through a database or HTTP import.
sql/           guard.py (sqlglot allow-list), executor.py (reader pool), results.py (types).
llm/           OpenAI-compatible client, retries, Gemini thought-signature replay.
repositories/  SQLAlchemy access to identity, conversation and usage tables.
db/            engine, URL parsing, role bootstrap.
schemas/       Pydantic API contracts (request, response, SSE events).
core/          config, errors, logging, security primitives, rate limiting. A leaf: imports
               nothing else from finqon.
resources.py   The composition root. Builds and closes the engine, reader pool, model client.
```

Rules that keep this honest:

* `core/` and `schemas/` import nothing from `api/`, `services/`, `agent/` or `repositories/`.
  `schemas/` depends only on `sql/results.py` for the column and cell types.
* `agent/` is testable with fakes. `tests/unit/test_orchestrator.py` runs the whole loop
  with no database; that only works while `agent/` does not import `sql/executor.py`
  internals beyond the exception type.
* Only `resources.py` and the entry points (`main.py`, `cli.py`, `eval/run_eval.py`) construct
  concrete infrastructure.

## Trust boundaries

| Boundary | Enforced by |
| --- | --- |
| Who the user is | JWT verified in `api/dependencies.py`; the id comes only from the token |
| What SQL may run | `sql/guard.py`: one SELECT, allow-listed views, row cap, function deny-list |
| Which rows are visible | RLS on the ledger and conversation tables, set per transaction from the token |
| What the database can do | Three roles: `finqon_api`, `finqon_reader` (read-only, `NOBYPASSRLS`), `finqon_loader` |
| What the prose may claim | `agent/grounding.py`: every figure and ISO date must come from a tool result |

The model never decides who the user is and never sees a full result set. A guard bug alone
cannot expose another user's rows, because RLS is enforced by the database for the reader role.

## Request flow

1. `RequestContextMiddleware` assigns `X-Request-ID` and writes the access log.
2. The router resolves the conversation before streaming, so ownership problems are real 404s.
3. `ChatService.stream_turn` builds `AgentDeps` and runs `agent.orchestrator.run_turn`.
4. The loop calls the model, runs guarded SQL as `finqon_reader`, then buffers the answer
   and checks it against the tool results (one regeneration, then a refusal).
5. Events stream as SSE: `status`, `sql`, `table`, `chart`, `token`, `done`. The turn and
   token usage are persisted before `done`.

## Operations

| Endpoint | Meaning | Use for |
| --- | --- | --- |
| `GET /health` | The process is up. Touches nothing. | Container liveness and restarts |
| `GET /health/ready` | Both database connections answer within 3 s, else `503`. | Load balancer routing |

Logs are JSON, carry the request id and (after auth) the user id, and never contain query
strings, headers, tokens or passwords.

Database: PostgreSQL 16 with the `vector` and `pg_trgm` extensions, started by Docker Compose
(`pgvector/pgvector:pg16`). Use a session connection, not a transaction-mode pooler, because
`set_config(..., true)` must live in the same session as the query. Alembic creates both
extensions. RAG stores a 768-dimension embedding on `ledger.transactions` and searches it
with pgvector cosine distance, fused with `pg_trgm`. The same SQL runs on another Postgres
by changing the four connection strings. The API never uses the owner role for chat queries.

## Known gaps

Stated plainly so they are not mistaken for finished work.

* No dependency lockfile yet. Versions are lower bounds in `pyproject.toml`.
* Integration tests are not in CI; they need a dedicated database.
* The rate limiter is in-process, so it is per replica. Fine for one instance; a shared store
  is needed before running several.
* `ChatService` takes the whole `Resources` bundle rather than the three things it uses. It is
  a small service-locator smell, acceptable at this size.
