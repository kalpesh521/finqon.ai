# FinQon.ai Agent Pattern Diagram

## Architecture Overview: single-agent tool-calling loop with a verifier gate

> The model uses native function calling (not text-based "Thought/Action" ReAct), and the
> loop is written by hand. Example values and payloads below are illustrative.

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                        FINQON.AI AGENT ARCHITECTURE                          ║
║                (Hand-rolled tool-calling loop + verifier)                    ║
╚═══════════════════════════════════════════════════════════════════════════════╝


                                USER QUESTION
                                     │
                                     ▼
        ┌────────────────────────────────────────────────────┐
        │         TURN INPUT (TurnInput)                     │
        ├────────────────────────────────────────────────────┤
        │ • question: "How much did I spend on dining?"      │
        │ • user_id: UUID (from JWT token)                  │
        │ • conversation_id: UUID                            │
        │ • today: date (Asia/Kolkata)                      │
        │ • history: [previous_turns]                        │
        │ • previous_sql: string | None                     │
        └────────────────────────────────────────────────────┘
                                     │
                                     ▼
        ╔════════════════════════════════════════════════════╗
        ║  STEP 1: BUILD SYSTEM PROMPT                       ║
        ║  (prompt_builder.py)                               ║
        ╚════════════════════════════════════════════════════╝
                                     │
              ┌──────────────────────┼──────────────────────┐
              ▼                      ▼                      ▼
        ┌─────────────┐      ┌──────────────┐      ┌────────────┐
        │ INSTRUCTIONS│      │ SCHEMA DOCS  │      │ FEW-SHOT   │
        │ (role,      │      │ (from DB     │      │ EXAMPLES   │
        │  task,      │      │  catalog)    │      │ (Q&A       │
        │  safety)    │      │ • columns    │      │  pairs)    │
        │             │      │ • views      │      │            │
        │ "You are an │      │ • comments   │      │ "Q: Total  │
        │ analyst...  │      │              │      │ spend for  │
        │ Use only    │      │ reporting.   │      │ August?    │
        │ these 3     │      │ transactions │      │            │
        │ views..."   │      │ reporting.   │      │ A: 45,000  │
        │             │      │ spend        │      │ (SQL...)"  │
        │             │      │ reporting.   │      │            │
        │             │      │ monthly...   │      │            │
        └─────────────┘      └──────────────┘      └────────────┘
              │                      │                      │
              │              ┌────────┴────────┐            │
              │              ▼                 ▼            │
              │         ┌────────────────────────┐          │
              │         │ DATE RANGES            │          │
              │         │ (date_ranges.py)       │          │
              │         ├────────────────────────┤          │
              │         │ "last month" =         │          │
              │         │  July 1-31, 2026       │          │
              │         │ "this quarter" =       │          │
              │         │  Jul 1 - Sep 30        │          │
              │         └────────────────────────┘          │
              │                      │                      │
              └──────────────────────┬──────────────────────┘
                                     ▼
        ╔════════════════════════════════════════════════════╗
        ║  STEP 2: CALL LLM (llm/client.py)                 ║
        ║  OpenAI-compatible endpoint (Gemini, Claude, etc)  ║
        ║  Response: tool_choice + tool_calls                ║
        ╚════════════════════════════════════════════════════╝
                                     │
                    ┌────────────────┼────────────────┐
                    │                │                │
                    ▼                ▼                ▼
              ┌──────────────┐ ┌──────────────┐ ┌─────────────┐
              │   TOOL CALL  │ │  TOOL CALL   │ │   MODEL     │
              │   run_sql    │ │  search_     │ │   OUTPUT    │
              │   _query     │ │  merchants   │ │   (plain    │
              │              │ │              │ │   text)     │
              │ {            │ │ {            │ │             │
              │  sql: string │ │  query:      │ │ "I'll help  │
              │  purpose:    │ │  string      │ │ you..."     │
              │  string      │ │ }            │ │             │
              │  chart_hint  │ │              │ │             │
              │ }            │ └──────────────┘ └─────────────┘
              └──────────────┘
                    │
                    ▼
        ╔════════════════════════════════════════════════════╗
        ║  STEP 3: ROUTE TO CORRECT HANDLER                 ║
        ║  (orchestrator.py - run_turn)                      ║
        ╚════════════════════════════════════════════════════╝
                    │
            ┌───────┼───────┐
            ▼       ▼       ▼
         RUN_SQL  SEARCH   ASK_
         _QUERY   MERCHANT CLARIF
                  S        ICATION
            │       │       │
            ▼       ▼       ▼
    ┌─────────────────────────────────────────────┐
    │          STEP 4: SAFETY LAYER               │
    │          (sql/guard.py + RLS)               │
    └─────────────────────────────────────────────┘
            │
            ▼
    ╔═════════════════════════════════════════════╗
    ║    SQL GUARD (sqlglot parser)               ║
    ╠═════════════════════════════════════════════╣
    ║ ✓ Parse SQL into AST (not regex)            ║
    ║ ✓ Verify root node is SELECT (not INSERT)   ║
    ║ ✓ Allow only 3 views:                       ║
    ║   - reporting.transactions                  ║
    ║   - reporting.spend                         ║
    ║   - reporting.monthly_category_spend        ║
    ║ ✓ Rewrite LIMIT to ≤ 500                    ║
    ║ ✓ Deny functions: drop, copy, set_config    ║
    ║ ✓ No LOCK, FOR UPDATE, INTO, SET            ║
    ║ ✓ Union/intersection allowed (with caveats) ║
    ╚═════════════════════════════════════════════╝
            │
            ▼ (if guarded passes)
    ╔═════════════════════════════════════════════╗
    ║    EXECUTE on finqon_reader role            ║
    ║    (sql/executor.py)                        ║
    ╠═════════════════════════════════════════════╣
    ║ • READ ONLY transaction                     ║
    ║ • Statement timeout: 5s                     ║
    ║ • SET finqon.user_id = <JWT.sub>            ║
    ║   (Row-Level Security enforced by DB)       ║
    ║ • Fetch first 20 rows + stats               ║
    ╚═════════════════════════════════════════════╝
            │
            ▼
    ┌─────────────────────────────────────────┐
    │  SUMMARIZE RESULT (results.py)          │
    ├─────────────────────────────────────────┤
    │  {                                      │
    │    columns: [name, type],               │
    │    rows: 20,            ← first 20      │
    │    truncated: true,     ← if > 20       │
    │    stats: {             ← for numbers   │
    │      min, max, sum      ← min/max/sum   │
    │    }                                    │
    │  }                                      │
    └─────────────────────────────────────────┘
            │
            ▼
    ┌─────────────────────────────────────────┐
    │  LOOP CONTROL (Constraints Check)       │
    ├─────────────────────────────────────────┤
    │  Model steps executed: 1 of 6  ✓        │
    │  SQL executions: 1 of 3  ✓              │
    │  Repairs done: 0 of 2  ✓                │
    │  Time elapsed: 2.5s of 30s  ✓           │
    └─────────────────────────────────────────┘
            │
            ├─ SQL budget used up? ──► tool error tells the model to answer
            │                          with what it already has
            ├─ Step limit / deadline hit? ──► `error` event (step_limit, timeout)
            │
            └─ Model returned tool calls? ──► LOOP BACK TO STEP 2
                                (model sees all previous
                                 results + new summary)
               Model returned plain text? ──► go to the grounding check


        ╔════════════════════════════════════════════════════╗
        ║  STEP 5: BUFFER FINAL ANSWER                       ║
        ║  (No streaming yet; collect full answer)           ║
        ╚════════════════════════════════════════════════════╝
                                     │
                                     ▼
        ╔════════════════════════════════════════════════════╗
        ║  STEP 6: GROUNDING CHECK (grounding.py)            ║
        ║  Extract & verify numbers & dates                  ║
        ╠════════════════════════════════════════════════════╣
        ║  Answer: "You spent ₹45,000 on dining"             ║
        ║  Extract: ["45000"]                                ║
        ║  Check: 45000 ∈ {rows from SQL}?  ✓ YES            ║
        ║                                                    ║
        ║  Answer: "Last month (Aug 2026)"                   ║
        ║  Extract: ["2026-08"]                              ║
        ║  Check: 2026-08-01 ∈ {date range}?  ✓ YES          ║
        ║                                                    ║
        ║  VERDICT: ✓ VERIFIED ──► STREAM TO CLIENT         ║
        ║           ✗ MISMATCH ──► REGENERATE ONCE           ║
        ║                          ELSE REFUSE               ║
        ╚════════════════════════════════════════════════════╝
                                     │
                                     ▼
        ╔════════════════════════════════════════════════════╗
        ║  STEP 7: STREAM EVENTS (SSE)                       ║
        ║  (services/chat.py)                                ║
        ╚════════════════════════════════════════════════════╝
                    │
        ┌───────────┼───────────┬──────────┬────────┐
        ▼           ▼           ▼          ▼        ▼
    status:     sql:        table:     chart:    token:
    "thinking" {sql,      {columns,   {kind,    "You"
               purpose}   rows,       x, y}     "spent"
               
    status:              truncated}
    "querying"
    
    status:
    "summarizing"
                                                done:
                                            {message_id,
                                             conversation
                                             _id,
                                             usage,
                                             status}


        ╔════════════════════════════════════════════════════╗
        ║  STEP 8: PERSIST (repositories/)                   ║
        ║  Save to conversation table before "done" event     ║
        ╚════════════════════════════════════════════════════╝


═══════════════════════════════════════════════════════════════════════════════
                        THREE INDEPENDENT SAFETY LAYERS
═══════════════════════════════════════════════════════════════════════════════

LAYER 1: GUARD (sqlglot parser)               LAYER 2: ROLE (Postgres)
┌──────────────────────────────┐              ┌──────────────────────────────┐
│ Rejects malicious SQL BEFORE │              │ finqon_reader role:          │
│ database sees it:            │              │ • NOSUPERUSER                │
│                              │              │ • NOBYPASSRLS                │
│ • DROP TABLE                 │              │ • READ ONLY transaction      │
│ • INSERT/UPDATE/DELETE       │              │ • Statement timeout: 5s      │
│ • COPY                       │              │ • Can't access identity or   │
│ • Non-whitelisted views      │              │   conversation tables        │
│ • Functions (set_config)     │              │ • Max 5 connections          │
│ • LIMIT > 500                │              │                              │
└──────────────────────────────┘              └──────────────────────────────┘
         ↓                                              ↓
    Model can't                                   Database policy
    break out                                     stops data leak


LAYER 3: RLS (Row-Level Security)
┌──────────────────────────────┐
│ Every query runs with:       │
│ SET finqon.user_id = <JWT>   │
│                              │
│ Policies on tables:          │
│ • ledger.transactions        │
│ • ledger.accounts            │
│ • conversation.conversations │
│ • conversation.messages      │
│                              │
│ WHERE user_id = CURRENT_USER │
│ (enforced by PostgreSQL)      │
└──────────────────────────────┘
         ↓
    Even if guard/role
    fail, RLS stops
    cross-user leakage


═══════════════════════════════════════════════════════════════════════════════
                              TOOL DEFINITIONS
═══════════════════════════════════════════════════════════════════════════════

TOOL 1: run_sql_query
┌────────────────────────────────────────────────────────────┐
│ {                                                          │
│   "type": "function",                                      │
│   "function": {                                            │
│     "name": "run_sql_query",                               │
│     "description": "Execute SELECT on 3 reporting views",  │
│     "parameters": {                                        │
│       "sql": "SELECT ... FROM reporting.spend ...",        │
│       "purpose": "Total spend for August",                 │
│       "chart_hint": "none" | "bar" | "pie" | "line"       │
│     }                                                      │
│   }                                                        │
│ }                                                          │
└────────────────────────────────────────────────────────────┘

TOOL 2: search_merchants
┌────────────────────────────────────────────────────────────┐
│ {                                                          │
│   "name": "search_merchants",                              │
│   "parameters": {                                          │
│     "query": "Swiggy" ← fuzzy match against merchant names │
│   },                                                       │
│   "returns": [                                             │
│     {"merchant_name": "Swiggy", "score": 0.97},           │
│     {"merchant_name": "Swiggy Instamart", "score": 0.91}  │
│   ]                                                        │
│ }                                                          │
└────────────────────────────────────────────────────────────┘

TOOL 3: ask_clarification
┌────────────────────────────────────────────────────────────┐
│ {                                                          │
│   "name": "ask_clarification",                             │
│   "parameters": {                                          │
│     "question": "Do you mean August or September?",        │
│     "options": [                                           │
│       "August 2026",                                       │
│       "September 2026"                                     │
│     ]                                                      │
│   }                                                        │
│ }                                                          │
│                                                            │
│ → Ends the turn (status = "clarification")                │
│ → The UI shows the options as buttons                     │
│ → The click arrives as the NEXT user message, with the    │
│   question stored in conversation history                 │
└────────────────────────────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════════════
                           LOOP CONSTRAINTS
═══════════════════════════════════════════════════════════════════════════════

MAX_MODEL_STEPS = 6        (more than 6 model calls -> `step_limit` error event)
MAX_SQL_EXECUTIONS = 3     (a 4th query is refused with a tool error)
MAX_REPAIRS = 2            (tool errors: bad arguments, guard rejection, SQL error;
                            beyond 2 the turn ends as "refused")
GROUNDING RETRY = 1        (separate from repairs: an answer with unverified figures
                            is regenerated once, then replaced by a fixed refusal)
DEADLINE_SECONDS = 30      (hard cutoff at 30s wall clock)

Example: User asks "Break down my spending by merchant for the last 3 months"
┌─────────────────────────────────────────────────────────────┐
│ Step 1: Model calls run_sql_query (execution #1)            │
│ Step 2: Model calls run_sql_query again (execution #2)      │
│ Step 3: Draft answer fails grounding → one regeneration     │
│ Step 4: Grounding passes, emit answer                       │
│                                                             │
│ Result: 2 executions, 0 repairs, 4 model steps ✓ ALLOWED   │
└─────────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════════════════════════════════════
                        DATA FLOW EXAMPLE
═══════════════════════════════════════════════════════════════════════════════

USER INPUT:
  "How much did I spend on dining in August?"

PROMPT BUILT:
  System: [instructions + schema docs]
  Context: [date ranges: last_month = Aug 1-31]
  History: []
  User question: "How much did I spend on dining in August?"

LLM RESPONSE:
  {
    "tool_calls": [
      {
        "id": "call_1",
        "function": {
          "name": "run_sql_query",
          "arguments": {
            "sql": "SELECT COALESCE(SUM(spend_amount),0) as total
                    FROM reporting.spend
                    WHERE category_name='Dining'
                    AND txn_date >= '2026-08-01'
                    AND txn_date <= '2026-08-31'",
            "purpose": "Total dining spend for August 2026",
            "chart_hint": "none"
          }
        }
      }
    ]
  }

GUARD CHECK:
  ✓ Single SELECT
  ✓ Only reporting.spend (whitelisted)
  ✓ No LIMIT provided (implicit ≤500)
  ✓ No banned functions
  → PASS

EXECUTE:
  User ID: 033d35f4-2967-42df-aa04-6e28c26c5d29 (from JWT)
  SET finqon.user_id = '033d35f4...' (RLS activated)
  
  Result: [{total: 12757.42}]

SUMMARIZE:
  {
    "columns": [{"name": "total", "type": "numeric"}],
    "rows": [[12757.42]],
    "truncated": false,
    "elapsed_ms": 245
  }

MODEL SEES:
  "Your query returned: Column 'total' (number). Row 1: 12757.42"

MODEL DRAFTS ANSWER:
  "You spent ₹12,757.42 on dining in August 2026."

GROUNDING CHECK:
  Extract: [12757.42]
  Found in result? [12757.42] ∈ [[12757.42]] ✓ YES
  → VERIFIED

STREAM EVENTS:
  event: status
  data: {"type":"status","stage":"thinking"}
  
  event: sql
  data: {"type":"sql","sql":"SELECT ... ","purpose":"..."}
  
  event: table
  data: {"type":"table","columns":[...],"rows":[[12757.42]],...}
  
  event: chart
  data: {"type":"chart","kind":"none","x":"","y":""}
  
  event: token
  data: {"type":"token","text":"You"}
  
  event: token
  data: {"type":"token","text":" spent"}
  
  event: token
  data: {"type":"token","text":" ₹12,757.42"}
  
  [... more tokens ...]
  
  event: done
  data: {...,"status":"answered"}

═══════════════════════════════════════════════════════════════════════════════
```

## Key Design Principles

1. **No Framework Overhead** — ~500 lines of hand-rolled Python vs. multi-MB LangChain
2. **Fail Closed** — Every safety layer is independent; losing one doesn't break others
3. **Testable** — `agent/` has zero DB imports; unit tests use scripted LLM
4. **Transparent** — Every decision is code, not hidden in a library black box
5. **Grounded** — No number reaches the user unless it came from a query result
6. **Bounded** — Hard limits on steps, executions, repairs, wall-clock time

## Files Implementing This Pattern

```
backend/finqon/agent/
├── orchestrator.py          ← Main loop (run_turn generator)
├── contracts.py             ← Protocol definitions (testable)
├── prompt_builder.py        ← System prompt + history
├── few_shot_examples.py     ← Example Q&A pairs
├── tools.py                 ← Tool definitions (JSON schema)
├── grounding.py             ← Number & date verification
├── date_ranges.py           ← "Last month" logic
└── catalog.py               ← Schema docs from DB

backend/finqon/sql/
├── guard.py                 ← sqlglot safety checks
├── executor.py              ← finqon_reader role, RLS
└── results.py               ← Result summarization

backend/finqon/llm/
└── client.py                ← OpenAI-compatible API calls
```
