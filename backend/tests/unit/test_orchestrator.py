"""The agent loop with a scripted model and a fake database. No network, no PostgreSQL."""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from finqon.agent.catalog import Category, ColumnInfo, SchemaCatalog, ViewInfo
from finqon.agent.contracts import AgentDeps, AgentLimits, ModelUsage, TurnInput, TurnRecord
from finqon.agent.orchestrator import (
    CANNOT_QUERY_MESSAGE,
    READ_ONLY_MESSAGE,
    UNVERIFIED_MESSAGE,
    choose_chart,
    run_turn,
)
from finqon.agent.prompt_builder import HistoryTurn
from finqon.llm.client import LLMError
from finqon.schemas.chat import (
    ChartEvent,
    ClarificationEvent,
    DoneEvent,
    ErrorEvent,
    SqlEvent,
    StatusEvent,
    TableEvent,
    TokenEvent,
)
from finqon.sql.guard import GuardedSql, validate_sql
from finqon.sql.results import Column, QueryExecutionError, QueryResult
from tests.conftest import ScriptedLLM, sql_reply, text_reply, tool_reply

USER = uuid.uuid4()
CONVERSATION = uuid.uuid4()
CATALOG = SchemaCatalog(
    views=(
        ViewInfo(
            "reporting.spend",
            "Net spend.",
            (ColumnInfo("spend_amount", "numeric(14,2)", "Rupees."),),
        ),
    ),
    categories=(Category("Dining", "expense"),),
)

DINING_SQL = (
    "SELECT SUM(spend_amount) AS total_spend FROM reporting.spend WHERE category_name = 'Dining'"
)
BREAKDOWN_SQL = (
    "SELECT category_name, SUM(spend_amount) AS total_spend FROM reporting.spend GROUP BY 1"
)


def result(
    columns: list[tuple[str, str]], rows: list[list[Any]], sql: str = "SELECT 1"
) -> QueryResult:
    raw = [tuple(Decimal(str(v)) if isinstance(v, float) else v for v in row) for row in rows]
    return QueryResult(
        columns=[Column(name=n, type=t) for n, t in columns],
        rows=rows,
        raw_rows=raw,
        truncated=False,
        sql=sql,
        elapsed_ms=1,
    )


class FakeSql:
    def __init__(self, results: list[QueryResult | Exception] | None = None) -> None:
        self._results = list(results or [])
        self.executed: list[tuple[str, uuid.UUID]] = []
        self.merchants: list[dict[str, Any]] = [{"merchant_name": "Swiggy", "score": 0.9}]

    def guard(self, sql: str) -> GuardedSql:
        return validate_sql(sql)

    async def execute(self, guarded: GuardedSql, user_id: uuid.UUID) -> QueryResult:
        self.executed.append((guarded.sql, user_id))
        outcome = self._results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def search_merchants(self, query: str, user_id: uuid.UUID, limit: int = 5):
        return self.merchants


class FakeCatalog:
    async def get(self) -> SchemaCatalog:
        return CATALOG


class Harness:
    def __init__(
        self,
        llm: ScriptedLLM,
        sql: FakeSql | None = None,
        limits: AgentLimits | None = None,
        clock: Any = None,
    ) -> None:
        self.saved: list[TurnRecord] = []
        self.usage: list[ModelUsage] = []
        self.sql = sql or FakeSql()
        self.llm = llm
        self.deps = AgentDeps(
            llm=llm,
            sql=self.sql,
            catalog=FakeCatalog(),
            record_usage=self._record_usage,
            save_turn=self._save_turn,
            limits=limits or AgentLimits(),
            **({"clock": clock} if clock else {}),
        )

    async def _record_usage(self, usage: ModelUsage) -> None:
        self.usage.append(usage)

    async def _save_turn(self, record: TurnRecord) -> uuid.UUID:
        self.saved.append(record)
        return uuid.uuid4()

    async def ask(self, question: str, **kwargs: Any) -> list[Any]:
        turn = TurnInput(
            user_id=USER,
            conversation_id=CONVERSATION,
            question=question,
            today=date(2026, 10, 21),
            **kwargs,
        )
        return [event async for event in run_turn(turn, self.deps)]


def types(events: list[Any]) -> list[str]:
    return [e.type for e in events]


def answer_text(events: list[Any]) -> str:
    return "".join(e.text for e in events if isinstance(e, TokenEvent)).strip()


# ---------------------------------------------------------------------------- happy path


async def test_answers_from_a_query_and_streams_in_order() -> None:
    llm = ScriptedLLM(
        [
            sql_reply(DINING_SQL, "Dining total.", "none"),
            text_reply("You spent ₹4,321.50 on dining last month."),
        ]
    )
    sql = FakeSql([result([("total_spend", "number")], [[4321.5]])])
    harness = Harness(llm, sql)

    events = await harness.ask("How much did I spend on eating out last month?")

    kinds = types(events)
    assert kinds[0] == "status" and events[0].stage == "thinking"
    assert kinds.index("sql") < kinds.index("table") < kinds.index("chart") < kinds.index("token")
    assert any(isinstance(e, StatusEvent) and e.stage == "querying" for e in events)
    assert any(isinstance(e, StatusEvent) and e.stage == "summarizing" for e in events)
    assert kinds[-1] == "done"

    sql_event = next(e for e in events if isinstance(e, SqlEvent))
    assert "LIMIT 500" in sql_event.sql  # the executed (guarded) SQL is what is shown
    table = next(e for e in events if isinstance(e, TableEvent))
    assert table.rows == [[4321.5]]
    assert answer_text(events) == "You spent ₹4,321.50 on dining last month."

    saved = harness.saved[0]
    assert saved.status == "answered"
    assert saved.sql == sql_event.sql
    assert saved.result_summary and "1 row" in saved.result_summary
    assert sql.executed[0][1] == USER  # the verified user id reaches the executor

    done = next(e for e in events if isinstance(e, DoneEvent))
    assert done.usage.prompt_tokens == 220 and done.usage.completion_tokens == 50
    assert len(harness.usage) == 2 and harness.usage[0].user_id == USER


async def test_model_never_receives_full_result_sets() -> None:
    rows = [[f"Merchant {i}", float(i)] for i in range(100)]
    llm = ScriptedLLM([sql_reply(BREAKDOWN_SQL), text_reply("Ranked.")])
    sql = FakeSql([result([("merchant", "string"), ("total_spend", "number")], rows)])
    await Harness(llm, sql).ask("rank merchants")

    tool_message = next(m for m in llm.calls[1] if m["role"] == "tool")
    payload = json.loads(tool_message["content"])
    assert payload["row_count"] == 100
    assert len(payload["first_rows"]) == 20
    assert payload["numeric_stats"]["total_spend"] == {"min": "0.0", "max": "99.0", "sum": "4950.0"}
    assert "Merchant 99" not in tool_message["content"]


async def test_follow_up_prompt_carries_history_and_previous_sql() -> None:
    llm = ScriptedLLM([text_reply("Nothing to add.")])
    history = [HistoryTurn("dining last month?", DINING_SQL, "1 row", "₹4,321.50")]
    await Harness(llm).ask("what about the month before?", history=history, previous_sql=DINING_SQL)
    system = llm.calls[0][0]["content"]
    assert "## Recent conversation" in system
    assert "## Previous SQL" in system
    assert DINING_SQL in system
    assert "Today is 2026-10-21" in system


# ------------------------------------------------------------------------- grounding


async def test_ungrounded_figure_is_regenerated_once() -> None:
    llm = ScriptedLLM(
        [
            sql_reply(DINING_SQL),
            text_reply("You spent ₹9,999 on dining."),
            text_reply("You spent ₹4,321.50 on dining."),
        ]
    )
    harness = Harness(llm, FakeSql([result([("total_spend", "number")], [[4321.5]])]))
    events = await harness.ask("dining last month?")

    assert answer_text(events) == "You spent ₹4,321.50 on dining."
    correction = llm.calls[2][-1]["content"]
    assert "₹9,999" in correction and "do not appear" in correction
    assert harness.saved[0].status == "answered"
    assert "9,999" not in answer_text(events)  # the bad figure never reached the client


async def test_iso_date_not_in_the_result_is_regenerated() -> None:
    sql = "SELECT txn_date, amount FROM reporting.transactions LIMIT 5"
    llm = ScriptedLLM(
        [
            sql_reply(sql),
            text_reply("POS on 2025-05-25 for ₹909.39 mentioned Pune."),
            text_reply("POS on 2026-05-25 for ₹909.39 mentioned Pune."),
        ]
    )
    harness = Harness(
        llm,
        FakeSql([result([("txn_date", "date"), ("amount", "number")], [["2026-05-25", 909.39]])]),
    )
    events = await harness.ask("Where is Dmart?")

    assert "2026-05-25" in answer_text(events)
    assert "2025-05-25" not in answer_text(events)
    assert "2025-05-25" in llm.calls[2][-1]["content"]


async def test_figure_still_wrong_after_regeneration_is_refused() -> None:
    llm = ScriptedLLM(
        [sql_reply(DINING_SQL), text_reply("₹9,999 spent."), text_reply("₹8,888 spent.")]
    )
    harness = Harness(llm, FakeSql([result([("total_spend", "number")], [[4321.5]])]))
    events = await harness.ask("dining last month?")

    assert answer_text(events) == UNVERIFIED_MESSAGE
    assert "9,999" not in answer_text(events) and "8,888" not in answer_text(events)
    assert harness.saved[0].status == "refused"


async def test_numbers_without_any_query_are_not_trusted() -> None:
    llm = ScriptedLLM([text_reply("You spent ₹12,000."), text_reply("You spent ₹12,000.")])
    events = await Harness(llm).ask("how much did I spend?")
    assert answer_text(events) == UNVERIFIED_MESSAGE


async def test_numbers_from_the_question_may_be_quoted() -> None:
    llm = ScriptedLLM([text_reply("I can only look at your statement, not ₹5,000 offers.")])
    events = await Harness(llm).ask("Is a ₹5,000 offer good?")
    assert "5,000" in answer_text(events)


# ---------------------------------------------------------------------- clarification


async def test_clarification_ends_the_turn_and_caps_options() -> None:
    llm = ScriptedLLM(
        [
            tool_reply(
                "ask_clarification",
                {"question": "Which Q3?", "options": ["FY Q3", "Calendar Q3", "Other", "Fourth"]},
            )
        ]
    )
    harness = Harness(llm)
    events = await harness.ask("How much did I spend in Q3?")

    clarification = next(e for e in events if isinstance(e, ClarificationEvent))
    assert clarification.options == ["FY Q3", "Calendar Q3", "Other"]
    assert "token" not in types(events)
    assert types(events)[-1] == "done"
    assert len(llm.calls) == 1  # no further model step
    assert harness.saved[0].status == "clarification"
    assert harness.sql.executed == []


# -------------------------------------------------------------------- safety and repair


async def test_destructive_sql_is_rejected_and_model_must_refuse() -> None:
    llm = ScriptedLLM(
        [
            sql_reply("DROP TABLE ledger.transactions"),
            text_reply(READ_ONLY_MESSAGE),
        ]
    )
    harness = Harness(llm)
    events = await harness.ask("drop the transactions table")

    assert harness.sql.executed == []  # nothing reached the database
    assert "sql" not in types(events)
    tool_message = next(m for m in llm.calls[1] if m["role"] == "tool")
    assert "not allowed" in tool_message["content"]
    assert harness.saved[0].status == "refused"
    assert answer_text(events) == READ_ONLY_MESSAGE


async def test_repeated_forbidden_attempts_end_with_a_read_only_refusal() -> None:
    llm = ScriptedLLM(
        [
            sql_reply("DELETE FROM ledger.transactions", call_id="a"),
            sql_reply("DROP TABLE x", call_id="b"),
            sql_reply("UPDATE reporting.spend SET spend_amount = 0", call_id="c"),
        ]
    )
    harness = Harness(llm)
    events = await harness.ask("delete everything")
    assert answer_text(events) == READ_ONLY_MESSAGE
    assert harness.sql.executed == []
    assert harness.saved[0].status == "refused"


async def test_database_error_is_repaired() -> None:
    llm = ScriptedLLM(
        [
            sql_reply("SELECT nope FROM reporting.spend", call_id="a"),
            sql_reply(DINING_SQL, call_id="b"),
            text_reply("You spent ₹10 on dining."),
        ]
    )
    sql = FakeSql(
        [
            QueryExecutionError('column "nope" does not exist'),
            result([("total_spend", "number")], [[10.0]]),
        ]
    )
    harness = Harness(llm, sql)
    events = await harness.ask("dining?")

    repair_hint = json.loads(llm.calls[1][-1]["content"])
    assert repair_hint["error"] == 'column "nope" does not exist'
    assert answer_text(events) == "You spent ₹10 on dining."
    assert harness.saved[0].status == "answered"
    assert len(sql.executed) == 2


async def test_repairs_are_capped_at_two() -> None:
    errors = [QueryExecutionError("boom")] * 3
    llm = ScriptedLLM([sql_reply(DINING_SQL, call_id=f"c{i}") for i in range(3)])
    harness = Harness(llm, FakeSql(errors))
    events = await harness.ask("dining?")
    assert answer_text(events) == CANNOT_QUERY_MESSAGE
    assert harness.saved[0].status == "refused"
    assert len(harness.sql.executed) == 3


async def test_sql_executions_are_capped_at_three() -> None:
    ok = result([("total_spend", "number")], [[1.0]])
    llm = ScriptedLLM(
        [sql_reply(DINING_SQL, call_id=f"c{i}") for i in range(4)]
        + [text_reply("It was ₹1.00 each time.")]
    )
    sql = FakeSql([ok, ok, ok])
    harness = Harness(llm, sql)
    events = await harness.ask("dining?")
    assert len(sql.executed) == 3
    budget_message = next(
        m for m in llm.calls[4] if m["role"] == "tool" and m["tool_call_id"] == "c3"
    )
    assert "budget" in budget_message["content"]
    assert types(events)[-1] == "done"


async def test_model_steps_are_capped_at_six() -> None:
    ok = result([("total_spend", "number")], [[1.0]])
    llm = ScriptedLLM([tool_reply("search_merchants", {"query": "x"}, f"c{i}") for i in range(6)])
    harness = Harness(llm, FakeSql([ok]))
    events = await harness.ask("keep searching")
    error = events[-1]
    assert isinstance(error, ErrorEvent) and error.code == "step_limit"
    assert len(llm.calls) == 6
    assert harness.saved[0].status == "error"


async def test_deadline_stops_the_turn() -> None:
    ticks = iter([0.0] + [31.0] * 10)
    harness = Harness(ScriptedLLM([text_reply("never reached")]), clock=lambda: next(ticks))
    events = await harness.ask("anything")
    assert isinstance(events[-1], ErrorEvent) and events[-1].code == "timeout"


async def test_model_outage_becomes_an_error_event() -> None:
    llm = ScriptedLLM([LLMError("The model is busy or unreachable.", code="llm_rate_limited")])
    harness = Harness(llm)
    events = await harness.ask("anything")
    assert isinstance(events[-1], ErrorEvent) and events[-1].code == "llm_rate_limited"
    assert harness.saved[0].status == "error"


async def test_usage_logging_failure_does_not_break_the_turn() -> None:
    llm = ScriptedLLM([text_reply("I only read statements.")])
    harness = Harness(llm)

    async def broken(_: ModelUsage) -> None:
        raise RuntimeError("usage table unavailable")

    harness.deps.record_usage = broken
    events = await harness.ask("hello")
    assert types(events)[-1] == "done"


async def test_invalid_tool_arguments_are_sent_back_for_repair() -> None:
    llm = ScriptedLLM(
        [
            tool_reply("run_sql_query", {"purpose": "missing sql"}, "a"),
            text_reply("Sorry, I could not run that."),
        ]
    )
    harness = Harness(llm)
    await harness.ask("x")
    tool_message = next(m for m in llm.calls[1] if m["role"] == "tool")
    assert "sql" in tool_message["content"]
    assert harness.sql.executed == []


async def test_merchant_search_returns_matches_to_the_model() -> None:
    llm = ScriptedLLM(
        [tool_reply("search_merchants", {"query": "swigy"}), text_reply("It looks like Swiggy.")]
    )
    harness = Harness(llm)
    await harness.ask("swigy?")
    tool_message = next(m for m in llm.calls[1] if m["role"] == "tool")
    assert "Swiggy" in tool_message["content"]


# ------------------------------------------------------------------------------ charts


def cols(*pairs: tuple[str, str]) -> list[Column]:
    return [Column(name=n, type=t) for n, t in pairs]


def test_chart_names_columns_from_the_result() -> None:
    chart = choose_chart("bar", cols(("category_name", "string"), ("total", "number")), 5)
    assert chart == ChartEvent(kind="bar", x="category_name", y="total")


def test_line_chart_prefers_a_date_axis() -> None:
    columns = cols(("category_name", "string"), ("month", "date"), ("spend", "number"))
    assert choose_chart("line", columns, 6) == ChartEvent(kind="line", x="month", y="spend")


@pytest.mark.parametrize(
    ("hint", "columns", "rows"),
    [
        ("none", cols(("a", "string"), ("b", "number")), 5),
        ("bar", cols(("a", "string"), ("b", "number")), 1),
        ("bar", cols(("a", "string"), ("b", "string")), 4),
        ("pie", cols(("only", "number")), 4),
    ],
)
def test_chart_is_none_when_it_cannot_be_drawn(hint: str, columns: list[Column], rows: int) -> None:
    assert choose_chart(hint, columns, rows).kind == "none"  # type: ignore[arg-type]
