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
eval/                question set and runner (measures answers, spends model quota),
                     verify_cache.py (checks the semantic cache on a running API)
docs/                this file, semantic-cache.md
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
agent/         The pipeline stages and the tool loop: ambiguity gate, context retriever,
               prompts, SQL repair hints (error fixer), grounding. Sees the outside world
               through agent/contracts.py (protocols), never through a database or HTTP
               import. Stage by stage in docs/AGENT_PATTERN.md.
cache/         Optional semantic cache around the agent loop (off by default). Wraps run_turn,
               replays and verifies stored plans. See docs/semantic-cache.md.
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

The semantic cache adds no new trust boundary. A cached answer is served only after its SQL is
re-guarded, re-run as `finqon_reader` under the user's RLS, matched by result digest and
re-grounded; entries are also keyed and filtered by user id. Details in `docs/semantic-cache.md`.

The model never decides who the user is and never sees a full result set. A guard bug alone
cannot expose another user's rows, because RLS is enforced by the database for the reader role.

## Request flow

1. `RequestContextMiddleware` assigns `X-Request-ID` and writes the access log.
2. The router resolves the conversation before streaming, so ownership problems are real 404s.
   `ChatService.open_conversation` also reads the user's ledger fingerprint (`data_version`)
   when the cache is on.
3. `ChatService.stream_turn` builds `AgentDeps`. Canned replies (greetings, out of scope) are
   answered directly. Everything else goes to `CachedTurnRunner.run`, or straight to
   `agent.orchestrator.run_turn` when the cache is off.
4. `CachedTurnRunner` analyses the question and looks it up (exact, then semantic).
   * **Hit, mode `on`:** the stored SQL is re-guarded and re-run as `finqon_reader`, the result
     digest is compared, the stored answer is re-grounded, the turn is saved, and the events
     are replayed with zero token usage. Any failed check evicts the entry and falls through.
   * **Miss, bypass, rejected replay or mode `shadow`:** `run_turn` runs.
5. `run_turn` runs the agent pipeline (diagram and stage contracts in `docs/AGENT_PATTERN.md`):
   1. **Ambiguity gate** (no model): a bare quarter or a follow-up with nothing to follow ends
      the turn with a `clarification` event.
   2. **Context retriever** (no model): ranks the views and maps the user's words to categories
      and columns; the result is a section of the system prompt.
   3. **SQL agent** (model): the tool loop calls the model, which writes SQL.
   4. **Guardrails**: `sql/guard.py` parses and allow-lists the SQL; the database enforces RLS
      for `finqon_reader`. Forbidden SQL is refused, never repaired.
   5. **Error fixer** (no model): a failed query returns to the model with its original error
      and a `hint` naming the likely fix (for example `amt` -> `spend_amount`), within the
      same repair budget.
   6. **Response node** (model): the answer is buffered and checked against the tool results
      (one regeneration, then a refusal).
6. After a clean, grounded, single-query turn the runner stores the plan before `done` is sent.
7. Events stream as SSE: `status`, `sql`, `table`, `chart`, `token`, `done`. The turn and
   token usage are persisted before `done`. A cache hit produces the same event sequence.

```
                       ┌────────────── CachedTurnRunner ──────────────┐
 question ─ analyse ─► │ lookup: exact key ─► KNN (tag pre-filtered) │
                       │          │ hit                  │ miss      │
                       │   replay + verify ──fail──────► │           │
                       └──────────┬──────────────────────┼───────────┘
                                  │ ok                   ▼
                           events, 0 tokens        run_turn (agent) ─► store plan
                                                   gate ► retrieve ► SQL ► guard ► run
                                                              ▲ error fixer ◄─┘ (failures)
                                                                       │
                       MemoryStore | RedisStore (Redis Stack) ◄────────┘
```

## Semantic cache

Optional, off by default, in `finqon/cache/`. Full design in `docs/semantic-cache.md`.

| Module | Responsibility |
| --- | --- |
| `analysis.py` | Deterministic question analysis: bypass rules, relative-time detection, constraint signature |
| `models.py` | Entry, plan and filter types; key and digest functions; `FORMAT_VERSION` |
| `store.py` | `CacheStore` protocol and the in-process `MemoryStore` (LRU + TTL) |
| `redis_store.py` | `RedisStore`: exact keys plus a RediSearch index with tag pre-filters and KNN |
| `service.py` | `SemanticCache`: lookup, store, evict, purge; circuit breaker, timeouts, embedding memo, metrics |
| `runner.py` | `CachedTurnRunner`: wraps `run_turn`, replay and verify, shadow comparison, telemetry |

Dependency direction: `services/` and `resources.py` use `cache/`; `cache/` uses `agent/`
contracts, `sql/` and `schemas/`; `agent/` never imports `cache/`. The only change inside
`agent/` is that `chunk_text` became public so a replay streams like a live answer.

Changes outside `cache/`:

| File | Change |
| --- | --- |
| `core/config.py` | `cache_*`, `redis_url` and `agent_clarify_before_model`, `agent_context_top_views` settings with validators; startup check for the redis backend |
| `resources.py` | Builds the cache and runner, `forget_cached_answers`, `cache_status`, closes the store |
| `services/chat.py` | Computes `data_version`, routes turns through the runner |
| `services/ingestion.py` | Purges a user's entries after a statement import or delete |
| `repositories/statements.py` | `data_version()`: fingerprint of the user's ledger, read under RLS |
| `api/health.py` | `GET /health/cache` |
| `docker-compose.yml` | `redis` service (Redis Stack, profile `cache`, memory capped, no persistence) |

Failure model: the cache can only make an answer faster. Every cache error, timeout or open
circuit reads as a miss and the agent answers as it would without a cache.

## Operations

| Endpoint | Meaning | Use for |
| --- | --- | --- |
| `GET /health` | The process is up. Touches nothing. | Container liveness and restarts |
| `GET /health/ready` | Both database connections answer within 3 s, else `503`. | Load balancer routing |
| `GET /health/cache` | Cache mode, store reachability, hit rate and counters for this replica. Always `200`. | Dashboards, rollout checks. Not a readiness gate |

Logs are JSON, carry the request id and (after auth) the user id, and never contain query
strings, headers, tokens or passwords.

Cache store: with `FINQON_CACHE_BACKEND=redis`, Redis Stack Server (RediSearch is required; plain
Redis is refused at first use and the cache stays off). Memory is capped with `allkeys-lru` and
nothing is persisted, because the cache is disposable. Counters are per replica.

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
* The semantic cache has no partial hit (reusing cached SQL with a fresh summary), and its Redis
  store has not yet been exercised against a live Redis Stack in CI.
* The rate limiter is in-process, so it is per replica. Fine for one instance; a shared store
  is needed before running several.
* `ChatService` takes the whole `Resources` bundle rather than the three things it uses. It is
  a small service-locator smell, acceptable at this size.
