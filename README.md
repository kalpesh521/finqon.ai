# FinQon.ai

Ask questions about your bank statement in plain English and get an answer, the SQL that
produced it, and the rows behind it. The React UI is in `frontend/` (see [Frontend](#frontend)).

It is a text-to-SQL agent on Neon Postgres, written by hand: a FastAPI service, a tool loop
of about 500 lines, a SQL guard built on `sqlglot`, a read-only database role, and
row-level security that keeps each user inside their own rows. No LangChain, LangGraph,
LlamaIndex, PydanticAI, Redis, or Postgres container.

## Status

| Part | State |
| --- | --- |
| API, auth, chat (SSE), conversations, statement summary | Built |
| React UI (chat desk, evidence charts, sign-in) | Built |
| Unit tests (guard, grounding, agent loop, API contract, scoring, seed data) | Pass, need no database |
| Integration tests (RLS, roles, auth flows, chat authorization, views) | Passed against a local Postgres 16. Against Neon, `test_auth`, `test_row_level_security` and `test_database_contract` passed (49 passed). The chat-authorization and CLI tests were reworked to mint seed-user tokens and have **not been re-run on Neon yet** |
| Real model calls (Gemini) | Made through the running API during manual checks. Every automated test still uses a scripted model |
| Evaluation | Written (29 cases) but **not run**, so there is no pass count yet. See [Evaluation](#evaluation) |
| Docker images | Written and `docker compose config` validates. **Not built**: no Docker daemon was available |

Nothing above is a claim about answer quality. That is what the eval measures, and it needs
your model key.

## How it works

```
client ──Bearer JWT──▶ FastAPI ──▶ ChatService ──▶ agent loop ◀──▶ LLM (OpenAI-compatible)
                          │                          │
                  finqon_api role               run_sql_query
                  (users, chats)                     │
                                          sqlglot guard (SELECT only, allow-listed views,
                                          LIMIT ≤ 500)
                                                     │
                                          finqon_reader role: read-only transaction,
                                          5 s timeout, finqon.user_id set ──▶ RLS
```

- **Tools:** `run_sql_query`, `search_merchants` (`pg_trgm`, for "starbuks"), and
  `ask_clarification`. The loop is capped at 6 model steps, 3 SQL executions, 2 repairs and
  30 seconds.
- **The model only sees summaries.** After a query it receives the columns, the row count,
  the first 20 rows and min/max/sum of numeric columns, never the full result.
- **Grounding check.** Every number in the drafted answer must match a value the model was
  shown (or appear in the question or the SQL). If one does not, the model regenerates once;
  if it still fails, the user gets a fixed refusal instead of an unverified figure.
- **Dates are computed in Python,** not guessed: the prompt carries today's date and the
  resolved ranges for "last month", "last 30 days", "this quarter" and so on.
- **Streaming:** a turn is `status`, `sql`, `table`, `chart`, then `token` events and a final
  `done`. The `chart` event names a kind and columns only; the UI plots `table.rows`.

## Quick start

You need Python 3.12+, a Neon project, `psql`, and (for real answers) a Gemini API key.

```bash
cp .env.example .env            # fill it in; see "Environment" below

cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

python -m finqon.db.bootstrap_roles      # create the three roles (uses the Neon owner URL)
alembic upgrade head                     # schemas, tables, RLS, views, grants
python ../dataset/load_ledger.py         # two seed users and their statements

uvicorn finqon.main:app_factory --factory --reload
```

The API is on `http://localhost:8000`, with interactive docs at `/docs` and the full event
contract in `/openapi.json`. `GET /health` returns `{"status":"ok"}`.

With Docker (the database stays on Neon; there is no Postgres service):

```bash
docker compose --profile setup run --rm setup     # once: roles, migrations, seed
docker compose up --build api
```

### Try the exit check without a model

```bash
finqon-cli reference --user kalpeshpawar@gmail.com
finqon-cli ask "How much did I spend on eating out last month?" --user kalpeshpawar@gmail.com
```

`reference` runs the known-good dining query directly and needs no model key; `ask` runs
the full agent (needs `FINQON_LLM_API_KEY`) and prints the SQL, the table and the answer, so
the two figures can be compared. On 21 October 2026, "last month" is September 2026, and the
reference figure is ₹32,862.95 for the primary user and ₹5,580.32 for the isolation user.
"Last month" moves with the calendar, so on another date the figures change; the seed
transactions do not.

## Environment

Settings come only from `pydantic-settings` with the `FINQON_` prefix, read from the
environment or `.env` at the repository root. `.env` is gitignored; commit only
`.env.example`. A missing required value stops the API at startup, naming the variable but
never printing a value.

| Variable | Needed by | Notes |
| --- | --- | --- |
| `FINQON_API_DATABASE_URL` | API | Role `finqon_api`. Neon **direct** (session) URL, not the pooler |
| `FINQON_READER_DATABASE_URL` | API | Role `finqon_reader`. Runs model-written SQL |
| `FINQON_LOADER_DATABASE_URL` | seed loader | Role `finqon_loader` |
| `FINQON_ADMIN_DATABASE_URL` | setup only | Neon owner. Used by role bootstrap and Alembic. The running API never needs it |
| `FINQON_SEED_PASSWORD` | seed loader, tests | Password for the two seed users |
| `FINQON_JWT_SECRET` | API | At least 32 characters |
| `FINQON_LLM_API_KEY` | API, `ask`, eval | Any OpenAI-compatible endpoint |
| `FINQON_LLM_BASE_URL`, `FINQON_LLM_MODEL` | optional | Default to Gemini's OpenAI-compatible endpoint and `gemini-flash-latest` |
| `FINQON_JWT_ACCESS_TTL_MINUTES`, `FINQON_JWT_REFRESH_TTL_DAYS`, `FINQON_JWT_ISSUER` | optional | 15, 14, `finqon.ai` |
| `FINQON_CORS_ORIGINS` | optional | Comma-separated. Default `http://localhost:5173` |
| `FINQON_TIMEZONE` | optional | Default `Asia/Kolkata` |

Passwords for the three application roles are taken from the passwords inside their URLs,
so there is nothing else to configure. Connection options such as `sslmode` and
`channel_binding` in a Neon URL are handled for both asyncpg and SQLAlchemy.

## Schema decisions

Five schemas keep ownership obvious: `identity` (users, refresh tokens), `ledger`
(categories, merchants, aliases, accounts, transactions), `conversation` (conversations,
messages), `operations` (model usage), and `reporting` (the only thing the model may read).
Alembic is the only way the schema changes.

- **The model reads views, not tables.** `reporting.transactions`, `reporting.spend` and
  `reporting.monthly_category_spend` already join merchants and categories, expose
  `fy`, `fy_quarter` and `month`, and carry a `COMMENT` on the view and on every column. The
  prompt is built from those comments (read from the database catalog on first use and cached
  for an hour), so the documentation the model sees is the documentation in the database.
- **`amount` is unsigned** and `direction` (`debit`/`credit`) carries the sign, the way a
  bank statement does. The one signed column is `spend_amount`, described below.
- **Raw descriptions are kept.** Messy strings stay in `raw_description`; the model gets the
  cleaned merchant and category. Merchants no alias matches become `Unknown Merchant` rather
  than a guess, and rankings exclude them.
- **Financial year** runs 1 April to 31 March (`FY2026-27`), quarters start in April, July,
  October and January, and every date is read in `Asia/Kolkata`.
- **Merchant matching** uses `pg_trgm`, restricted to merchants the signed-in user has
  actually transacted with.
- **Conversations store** the question, the SQL, a one-line result summary, the answer and
  token counts. Result sets are never stored, so history sent back to the model contains no
  rows.

## What "spend" means

> Spend is every debit that is not a transfer, minus linked refunds. A refund is negative
> spend on the day it arrives. Salary and other credits are not spend, and transfers (for
> example money sent to parents) are not spend.

This is `reporting.spend`, and `spend_amount` is its signed amount. Consequences worth
knowing:

- Refunds reduce the month they **arrive** in, not the month of the purchase. A month can
  therefore be lower than the sum of its purchases.
- A transfer of ₹15,000 to parents does not appear in spend, but it does appear in
  `reporting.transactions`, so "how much did I send home?" is answerable.
- The evaluation's reference SQL uses exactly this definition. See
  [dataset/README.md](dataset/README.md) for the seed data.

## Authentication

Register, log in, and use the access token. Refresh tokens rotate: each use returns a new
pair and revokes the old one. Replaying a used token revokes the whole family, because the
original may have been stolen. Logout is idempotent. Passwords are hashed with argon2id,
refresh tokens are stored only as SHA-256 hashes, and register and login are rate limited in
process (10 attempts per minute by default, per address and per email).

```bash
BASE=http://localhost:8000/api/v1

curl -s $BASE/auth/register -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"a long passphrase"}'

TOKENS=$(curl -s $BASE/auth/login -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"a long passphrase"}')
ACCESS=$(echo "$TOKENS"  | jq -r .access_token)
REFRESH=$(echo "$TOKENS" | jq -r .refresh_token)

curl -s $BASE/auth/me -H "authorization: Bearer $ACCESS"

# Rotate: use the NEW refresh token from this response from now on.
curl -s $BASE/auth/refresh -H 'content-type: application/json' \
  -d "{\"refresh_token\":\"$REFRESH\"}"

curl -s -X POST $BASE/auth/logout -H 'content-type: application/json' \
  -d "{\"refresh_token\":\"$REFRESH\"}"        # 204
```

Application errors are `{"code": "...", "message": "..."}` with stable codes such as
`invalid_credentials`, `refresh_token_reused`, `refresh_token_expired` and `rate_limited`
(which also sets `Retry-After`). Malformed requests return FastAPI's standard `422` body.

## Chat

```bash
# Seed users have their own passwords (FINQON_SEED_PASSWORD).
ACCESS=$(curl -s $BASE/auth/login -H 'content-type: application/json' \
  -d '{"email":"kalpeshpawar@gmail.com","password":"'"$FINQON_SEED_PASSWORD"'"}' \
  | jq -r .access_token)

curl -N $BASE/chat -H "authorization: Bearer $ACCESS" -H 'content-type: application/json' \
  -d '{"message":"Give me a category-wise spending breakdown for the last 3 months."}'
```

The response is Server-Sent Events (`event: <type>` / `data: <json>`) and carries an
`X-Conversation-Id` header. Pass `conversation_id` in the next request to continue; "What
about the month before?" only makes sense there, because the model is given the previous
turn's SQL and summary.

```
status        stage: thinking | querying | summarizing
sql           sql, purpose
table         columns[{name,type}], rows, truncated, elapsed_ms
chart         kind: bar | pie | line | none, x, y
clarification question, options (at most 3)
token         text
done          message_id, conversation_id, usage, status (answered | clarification | refused)
error         code, message
```

`GET /api/v1/conversations` lists your conversations and
`GET /api/v1/conversations/{id}` returns one with its messages. `PATCH` renames a chat
(`title`) or pins it (`pinned`); pinned chats stay at the top of the list.
`DELETE` removes the chat and its messages. Someone else's conversation
and one that does not exist return the same `404`. `GET /api/v1/statement` returns the
signed-in user's transaction count and the first and last transaction dates. Result rows are
not stored with the conversation, so a refreshed page shows the questions and answers, and
the chart fills in again on the next question.

## How row-level security is set from the token

Isolation does not depend on the model writing `WHERE user_id = ...`. The prompt even says
not to.

1. The bearer token is verified (signature, expiry, issuer). Its `sub` is the user id; no
   database lookup decides who you are.
2. Every database transaction that touches user data begins with
   `SELECT set_config('finqon.user_id', <id>, true)`. The third argument makes it
   transaction-local, so a pooled connection cannot carry one user's id into the next request.
   The API role does this through `Database.user_scope(user_id)`; the reader does it inside a
   read-only transaction in `QueryExecutor`.
3. The policies on `ledger.accounts`, `ledger.transactions`, `conversation.conversations` and
   `conversation.messages` compare rows to `NULLIF(current_setting('finqon.user_id', true), '')::uuid`.
   RLS is **enabled and forced**, and the views are `security_invoker = on`, so they run with
   the caller's rights and inherit the policies.
4. If the setting is missing the policy yields no rows. Forgetting to set it fails closed.

The guard also rejects `set_config`, `current_setting` and `pg_*` functions, so model-written
SQL cannot change or even read the setting.

## Safety layers

A request to delete data, an injection in a transaction description, and a hand-written
`DROP TABLE` each have to get past several independent layers:

1. **Prompt:** descriptions are data; refuse writes. A suggestion, and the weakest layer.
2. **Guard:** one statement, a root `SELECT`/`UNION`, no write, DDL, `COPY`, `INTO`, `LOCK` or
   `SET` nodes, only the three reporting views (plus the query's own CTEs), a function
   deny-list, and `LIMIT` rewritten to at most 500. Parsed with `sqlglot` in the Postgres
   dialect, never matched with regexes.
3. **Database role:** `finqon_reader` is `NOSUPERUSER NOBYPASSRLS`, defaults to read-only
   transactions, has a statement timeout and a small connection limit, and has no access to
   `identity` or `conversation`.
4. **RLS:** even a query that got through can only see its own user's rows.
5. **Grounding:** no number reaches the user unless it came from a tool result.

## Testing

```bash
make check                   # lint, types, unit tests (what CI runs)
make test                    # everything; integration tests skip without a database
# or: cd backend && pytest tests/unit   |   pytest tests/integration
```

Integration tests are marked `integration` and need the three role URLs, the seed users and
`FINQON_JWT_SECRET` (and `FINQON_SEED_PASSWORD` for the seed-user tests). They use the real
application roles, never the owner, so a pass means the policy holds for the app. They cover:

- **Row-level security and roles** (`tests/integration/test_row_level_security.py`): per-user spend equals a
  recomputation from the CSVs; asking for another user's rows by id returns nothing; no user
  set means no rows; the reader cannot write, read `identity`, or run DDL; conversations are
  invisible across users.
- **Auth** (`test_auth.py`): duplicate email, wrong password and unknown email being
  indistinguishable, rotation and reuse revocation, expiry, logout, rate limiting, secrets
  stored hashed and absent from logs.
- **Chat** (`test_chat_authorization.py`): the SSE sequence, per-user tables, history and
  follow-ups, cross-user conversation `404`s with no model call made, and seven
  delete/drop/update/copy/sleep attempts that leave the ledger unchanged.
- **Views** (`test_database_contract.py`): every view and column is documented, the spend
  rules hold, the financial-year labels are right, and every few-shot example runs on the
  seeded data.

Lint with Ruff, from `backend/`:

```bash
ruff check . ../dataset ../eval && mypy   # or: make lint typecheck
```

## Evaluation

`eval/questions.yaml` holds 29 cases, each judged by **result values, not SQL text**: the
runner executes a reference query as `finqon_reader` for the seed user, runs the agent, and
compares the rows (numbers within ₹0.01, order ignored unless the question is a ranking).
It covers the six questions from the brief, "What about the month before?" and "Break that
down by merchant", ambiguous questions that should be clarified, questions a bank statement
cannot answer, and attacks, including the seeded injection row that must be listed as an
ordinary transaction.

```bash
python eval/run_eval.py --reference-only   # validate the reference SQL; no model needed
python eval/run_eval.py                    # full run; writes eval/reports/latest.md
```

**Latest result: not run yet.** It needs `FINQON_LLM_API_KEY`. What has been checked without
a model is that all 21 value cases' reference SQL executes on the seed data, that the case
file meets the spec (at least 20 cases, every tag, the required questions), and that the
scoring rules behave. After a run, the report at [eval/reports/latest.md](eval/reports/latest.md)
lists every case with its mismatch, latency and tokens. Failures are reported, not retried
or hidden; if cases fail, the fix is to the views, the date preamble or the few-shot
examples, never to the question text.

## Deviations from the brief, and why

- **The reader can `SELECT` the base ledger tables.** The brief asks for views with
  `security_invoker = on` *and* a reader with no base-table privileges. Postgres cannot do
  both: an invoker view runs with the caller's rights, so the caller needs the underlying
  access. I kept `security_invoker` and granted the reader `SELECT` on the four base tables
  it needs. RLS still limits it to the user's rows, and the guard still limits the model to
  the three views. The reader cannot see `identity` or `conversation` at all.
- **Model calls inside the loop are not streamed.** The answer is buffered and verified
  against the tool results first, then emitted as `token` events. Streaming raw model output
  would put unverified numbers on screen before the check could reject them.
- **Two extra variables:** `FINQON_ADMIN_DATABASE_URL` and `FINQON_SEED_PASSWORD`. Creating
  roles and running migrations needs the Neon owner, and the seed users need a password.
- **The rate limiter is in process.** It is correct for one API instance; running several
  needs a shared store, which this project deliberately does not add.

## More

Layering, trust boundaries, probes and known gaps: [docs/architecture.md](docs/architecture.md).
`GET /health` is liveness; `GET /health/ready` checks both database connections.

## Frontend

The UI is a Vite + React + TypeScript app in `frontend/`. It signs in against `/api/v1/auth`,
streams `POST /api/v1/chat`, and plots `table.rows` (never numbers copied out of the answer
text). Event types in `frontend/src/types/events.ts` are generated from `frontend/openapi.json`;
each event is checked with Zod before the chat reducer sees it.

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173, proxied to the API on :8000
```

`npm run typecheck` and `npm test` cover the stream reducer. With Docker, `docker compose up --build`
also starts the UI on port 5173 and proxies `/api` to the API container. Regenerate the
contract after an API change with the backend virtualenv:

```bash
cd backend
.venv/bin/python -c "import json; from finqon.main import create_app; from tests.conftest import ScriptedLLM, unit_settings; app = create_app(unit_settings(), llm=ScriptedLLM([]), require_llm_key=False); open('../frontend/openapi.json','w').write(json.dumps(app.openapi(), indent=2)+'\n')"
cd ../frontend && npm run generate:api
```

No CSV or PDF upload, budgets, anomaly detection, or pgvector are included by design.
