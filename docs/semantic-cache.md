# Semantic cache

A cache in front of the agent loop. When a user repeats a question, or asks one that means
the same thing, the stored plan (SQL, result, answer) is **replayed against the live
database and verified**. The model is called only when verification fails.

It is **off by default**. Nothing in this document applies until `FINQON_CACHE_MODE` is set.

## The rule

> A cached financial answer is served only if the user, the data, the dates, the filters and
> the meaning of the question all match.

A wrong hit shows someone the wrong money, so the design leans toward misses. Every
mechanism below exists to turn a doubtful hit into a miss.

## Where it sits

```
ChatService.stream_turn
   └─ CachedTurnRunner.run            (finqon/cache/runner.py)
        ├─ analyse(question)          deterministic: bypass, or compute a constraint signature
        ├─ SemanticCache.lookup       exact tier, then semantic tier
        ├─ replay + verify            on a candidate hit
        └─ run_turn  (the agent)      on a miss, a rejected replay, or in shadow mode
              └─ _after_turn          store the plan if the turn was clean
```

`agent/` does not know the cache exists. The runner wraps `run_turn` and yields the same
`ChatEvent` stream, so the API, the UI and persistence do not change. Canned replies
(greetings, out-of-scope) never reach the cache.

## What is stored

A **plan record**, not only prose: the SQL, its purpose and chart hint, a digest of the
result (columns and rows), the grounded answer, and the fingerprint it was made under. The
database stays the source of truth. A hit re-runs the SQL and compares digests.

A turn is stored only if it was clean: one table, a real query (not a search probe), no
error, and a grounded answer.

## Lookup

1. **Analyse.** `cache/analysis.py` canonicalizes the question without a model: operators
   (`over`, `under`, `between`), periods (`last month`, `Q2`, `FY25`, ISO and `dd/mm`
   dates), amounts (`5k`, `2 lakh`, `1 crore`) and entities. It returns a *signature*, a hash
   of those constraints. Words outside the closed vocabulary become entities, so an unknown
   merchant changes the signature instead of being ignored. Word order counts when the
   question has comparatives, negations, several entities or numbers, or from/to.
   Follow-ups ("and last month?", "what about that one") depend on the conversation and
   bypass the cache.
2. **Exact tier.** One key lookup on
   `sha256(user | data_version | schema_version | day | canonical text)`. No embedding.
3. **Semantic tier.** Embed the question, then a KNN search **pre-filtered** by the tags
   `user`, `ver`, `sch`, `sig` and `day`, then a cosine threshold
   (`FINQON_CACHE_SIMILARITY_THRESHOLD`). Embedding similarity alone never selects an entry:
   "spend over 5000" and "spend under 5000" are close vectors with different signatures.
4. **Time.** Questions that mention relative time, and SQL that uses `current_date` or
   `now()`, produce entries valid for the same local day only (`FINQON_TIMEZONE`), with a
   TTL that ends at midnight. Everything else lives for `FINQON_CACHE_TTL_SECONDS`.

## Fingerprint

| Field | What it is | Changes when |
| --- | --- | --- |
| `user` | the authenticated user id | never shared across users |
| `data_version` | hash of the count and highest id of accounts and transactions, read under RLS | a statement is imported or deleted |
| `schema_version` | hash of the catalog, rules, SQL examples and `FORMAT_VERSION` | prompts, views or the cache format change |
| `day` | local date, or `stable` | midnight, for time-relative entries |

When any of them changes, old entries stop matching and expire on their own. A user's entries
are also purged when a statement upload finishes or a statement is deleted.

## Verification on a hit

Before a hit is served, the runner:

1. re-checks that the entry belongs to this user,
2. re-runs the SQL through the same guard as the agent,
3. executes it as `finqon_reader` under the user's RLS,
4. compares the `result_digest` (columns and rows) with the stored one,
5. re-runs grounding on the stored answer against the fresh result.

If any step fails, the entry is evicted and the agent answers normally. The user sees a
slightly slower normal answer, never a stale one. A served hit costs one SQL query and no
model call; its `done` event reports zero token usage.

## Protection between users

Three independent layers:

1. keys and search filters carry the user id,
2. every returned entry is checked against the requesting user,
3. the replay runs under RLS, and the digest would differ if rows belonged to someone else.

## Failure behavior

The cache is an optimization and must never take chat down.

* Every store call has a timeout (`FINQON_CACHE_OP_TIMEOUT_MS`); the embedding has its own
  (`FINQON_CACHE_EMBED_TIMEOUT_MS`).
* A circuit breaker opens after repeated failures and skips the cache for
  `FINQON_CACHE_BREAKER_COOLDOWN_SECONDS`.
* Any cache error is read as a miss. The agent runs as if the cache did not exist.
* A failed `data_version` query yields an empty version, which disables caching for that turn.

## Backends

| Backend | Use | Notes |
| --- | --- | --- |
| `memory` | development, tests, one replica | LRU with TTL, `FINQON_CACHE_MEMORY_MAX_ENTRIES`. Lost on restart. |
| `redis` | shared, several replicas | Needs **Redis Stack** (RediSearch). Plain Redis fails fast with a clear error and the cache stays disabled. |

Redis layout, under `FINQON_CACHE_KEY_PREFIX` (default `finqon:sc`):

* `{prefix}:x:{user}:{key}`: exact-tier keys.
* `{prefix}:e:{user}:{key}`: hashes with `user`, `ver`, `sch`, `sig`, `day`, `vec`, `payload`,
  indexed by one RediSearch index. `FINQON_CACHE_VECTOR_INDEX=flat` is exact and fine to about
  100k entries; use `hnsw` above that.

The Compose service `redis` (profile `cache`) caps memory with `allkeys-lru` and has no
persistence. The cache is disposable.

## Optimizations

What the cache changes, and why each piece is there.

| Optimization | Effect |
| --- | --- |
| Exact tier before the semantic tier | A repeated question costs one key lookup. No embedding call, no vector search. |
| Hit replays stored SQL, no model call | Removes both model round trips of a normal turn (SQL generation and the answer). A hit reports `usage.total_tokens = 0`. |
| Pre-filtered KNN (user, versions, signature, day) | The vector search only sees candidates that could be valid, so it is small, fast and cannot return another user's entry. |
| Constraint signature | Turns "similar enough" into "means the same", so the threshold can stay permissive for paraphrases without risking wrong periods, amounts or merchants. |
| Embedding timeout and memo | The semantic tier never delays an answer by more than `FINQON_CACHE_EMBED_TIMEOUT_MS`; a repeated text is embedded once. |
| Day-bound TTL for relative dates | "Last month" answers are reused all day and never across midnight, instead of being skipped entirely. |
| Ledger fingerprint in the key | New or deleted statements invalidate stale entries by construction, with no invalidation protocol. |
| Purge on import and delete | Memory is released and deleted-statement answers do not linger. |
| Circuit breaker and per-call timeouts | A slow or dead Redis costs at most one timed-out call per cooldown window, not one per request. |
| Redis `allkeys-lru`, no persistence | Bounded memory; a restart simply empties a disposable cache. |
| Shadow mode | Measures hit and false-hit rates on real traffic before anything is served. |

Per served hit you save two model calls and the model latency; you pay one database query
(milliseconds, with the statement timeout still applying) and one Redis round trip. A miss
adds one lookup and, when the semantic tier runs, one embedding bounded by its timeout.
Measure the real numbers with `semantic_cache_turn` (`cache_lookup_ms`, `replay_ms`,
`cache_hit_rate`) rather than trusting estimates: they depend on how often your users repeat
themselves.

## Is it production grade?

Assessed against the code as written, not as hoped.

| Property | Status | Evidence |
| --- | --- | --- |
| Cannot show another user's data | Met | user in key, filter and entry re-check; replay under RLS; unit tests in `test_cache_service.py`, `test_cache_runner.py`; integration test `test_another_user_...` (needs Postgres) |
| Cannot serve stale numbers | Met | ledger fingerprint plus result digest on every hit; `replay_stale` evicts |
| Cannot serve an ungrounded claim | Met | stored answer re-verified against the fresh result |
| Cannot take chat down | Met | breaker, timeouts, every error reads as a miss; tests for outage, timeout, bad payloads |
| Safe default | Met | mode `off`; `shadow` before `on`; one setting to roll back |
| Bounded resources | Met | LRU and `FINQON_CACHE_MEMORY_MAX_ENTRIES` in memory; `allkeys-lru` and `maxmemory` in Redis |
| Observable | Met | structured logs, `GET /health/cache`, `eval/verify_cache.py` |
| Static quality | Met | `ruff`, `ruff format`, `mypy` clean; 485 unit tests pass |
| Works against a real Redis Stack | **Not proven here** | Unit tests use a recording fake. A live test exists (`FINQON_TEST_REDIS_URL`) but has not been run in this environment. |
| Works against the real database | **Not proven here** | `data_version` SQL and the end-to-end integration tests need Postgres, which was unavailable. |
| Latency and hit-rate gains | **Not measured** | Depends on real traffic. Use shadow mode to measure. |
| Fleet-wide metrics | Partial | Counters are per replica. Use the logs for aggregates, or add an exporter. |
| Cache poisoning by a user | Contained | Entries are per user, so a user can only affect their own answers, and every hit is re-grounded. |

Before enabling `on` in production, do these three things: run the live Redis test, run the
integration tests (`docker compose --profile test run test`), and run a week of `shadow`.

## How to verify the cache is working

### 1. Is it on, and is the store reachable?

```bash
curl -s localhost:8000/health/cache | jq
```

```json
{"mode": "on", "enabled": true, "backend": "redis", "store_reachable": true,
 "similarity_threshold": 0.92, "breaker_open": false, "hit_rate": 0.31,
 "counters": {"lookup": 120, "hit_exact": 30, "hit_semantic": 7, "miss": 83, "stored": 80,
              "served": 37, "bypass": 14, "near_miss": 12, "evicted": 1}}
```

* `"enabled": false` means `FINQON_CACHE_MODE=off` or the process was not restarted.
* `store_reachable: false` or `breaker_open: true` means Redis is the problem. Chat still works.
* `bypass` counts follow-ups and questions the cache refuses by design.
* `near_miss` is a candidate below the threshold or rejected by a filter.
* `replay_stale` and `replay_ungrounded` should stay near zero. A steady rise means the data or
  prompts change faster than entries are reused, or the threshold is too low.

### 2. One scripted check against a running API

```bash
cd finqon.ai
backend/.venv/bin/python eval/verify_cache.py \
  --email kalpeshpawar@gmail.com --password "$FINQON_SEED_PASSWORD" \
  --other-email seed.isolation@finqon.example --other-password "$FINQON_SEED_PASSWORD"
```

It asks a question, repeats it, rewords it, changes the period, and asks as the other user.
It prints timings and token counts and exits non-zero on any failed check:

| Step | Expected with mode `on` |
| --- | --- |
| Repeat | `tokens=0`, same SQL and answer, faster |
| Reworded | `tokens=0` if the embedding is close enough, otherwise a normal answer |
| Different period | Normal answer (`tokens>0`). If this is `0`, that is a bug. |
| Other user | Normal answer (`tokens>0`). If this is `0`, that is a security bug. |

It spends a little model quota on the first ask. It needs the model key and a seeded database.

### 3. By hand in the UI

Ask a question, then ask it again in a new conversation. The second answer should start
immediately, show the same SQL and table, and the conversation's token usage should not grow.

### 4. From the logs

```bash
docker compose logs api | grep semantic_cache_turn      # served, with tier, similarity, ms
docker compose logs api | grep semantic_cache_shadow    # shadow comparisons
docker compose logs api | grep -E 'semantic_cache_(backend_error|replay_)'   # problems
```

### 5. In Redis

```bash
redis-cli -p 6379 FT.INFO finqon:sc:idx:768:flat | head   # the index exists (prefix:idx:dimensions:kind)
redis-cli -p 6379 --scan --pattern 'finqon:sc:*' | head
redis-cli -p 6379 DBSIZE
```

### 6. Automated

```bash
make test-unit                                        # 485 tests, no services needed
docker compose --profile test run test                # adds the database integration tests
FINQON_TEST_REDIS_URL=redis://localhost:6379/0 \
  backend/.venv/bin/pytest backend/tests/integration/test_semantic_cache.py -q
```

## Rollout

1. **Deploy with `off`.** No behavior change.
2. **`shadow`.** Every turn runs the agent as before. In addition the cache is looked up,
   and the candidate is compared with what the agent really produced. Nothing is served.
   Run it for a week of real traffic.
3. **Read the logs** (below). Raise the threshold if `shadow_result_match` is ever false for a
   candidate. Continue only with no false hits.
4. **`on`.** Keep watching `semantic_cache_turn`.

Rollback is `FINQON_CACHE_MODE=off` and a restart.

## Logs and metrics

All are structured JSON log events (no PII beyond the conversation id).

| Event | Meaning |
| --- | --- |
| `semantic_cache_turn` | `cache_outcome` (`served` or `replay_rejected`), `cache_tier`, `cache_similarity`, `cache_lookup_ms`, `cache_hit_rate`, `replay_ms` |
| `semantic_cache_shadow` | `cache_reason`, `cache_tier`, `cache_similarity` (of the candidate or nearest neighbour), and when a candidate existed: `shadow_sql_match`, `shadow_result_match`, `shadow_answer_match` |
| `semantic_cache_backend_error` | a store call failed or timed out; includes `breaker_open` |
| `semantic_cache_replay_query_failed` / `_replay_error` / `_replay_save_failed` | a replay did not complete |
| `semantic_cache_store_failed` | storing a new plan failed |
| `semantic_cache_embed_skipped` / `_embed_wrong_size` | the embedding was unavailable or malformed |

Targets before `on`: false-hit rate 0 (any `shadow_result_match=false` with a candidate at or
above the threshold is a bug to investigate), no cross-user entries, p95 lookup under 50 ms
on Redis.

## Calibrating the threshold

Start at `0.92`. In shadow logs, group candidates by `cache_similarity`. The lowest value at
which every candidate still had `shadow_result_match=true`, plus a margin, is the floor. The
signature filter already blocks changes of operator, period, number and entity, so the
threshold mainly handles paraphrase; it can usually be lowered a little to raise the hit
rate, but only on evidence from shadow mode.

## Limitations and next steps

* **No partial hit.** If the digest differs (new transactions) or the day rolled over, the
  whole turn runs again. A cheaper path would reuse the cached SQL and call the model only
  to write the summary.
* The signature vocabulary is closed and English-focused. New phrasings fall to the exact
  tier or miss; they never produce a wrong hit.
* `data_version` is per user. Any new transaction invalidates all of that user's entries,
  which is safe and simple, but coarse for users who import often.
* The Redis store is covered by unit tests against a recording fake. The live-server test in
  `tests/integration/test_semantic_cache.py` runs when `FINQON_TEST_REDIS_URL` points to
  Redis Stack; run it once before enabling the `redis` backend.
