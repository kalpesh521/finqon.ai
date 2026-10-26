"""End to end: the agent loop wrapped by the semantic cache, with a scripted model.

The properties that matter, each tested below:

* a hit costs zero model calls and still reads the database;
* a hit is served only if the fresh result matches the one the answer was written for;
* a question that differs in any figure-changing way is a miss, however similar it sounds;
* anything that cannot be verified falls back to the normal agent, never to a wrong answer.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from finqon.agent.catalog import Category
from finqon.agent.contracts import AgentDeps, ModelUsage, TurnInput, TurnRecord
from finqon.agent.orchestrator import run_turn
from finqon.cache.analysis import analyse
from finqon.cache.runner import CachedTurnRunner, Mode, schema_version
from finqon.cache.service import CacheConfig, SemanticCache
from finqon.cache.store import MemoryStore
from finqon.llm.client import LLMError
from finqon.schemas.chat import (
    DoneEvent,
    ErrorEvent,
    RetractEvent,
    SqlEvent,
    TableEvent,
    TokenEvent,
)
from finqon.sql.results import QueryExecutionError
from tests.conftest import ScriptedLLM, sql_reply, text_reply, tool_reply
from tests.unit.test_orchestrator import (
    CATALOG,
    CONVERSATION,
    DINING_SQL,
    USER,
    FakeCatalog,
    FakeSql,
    result,
)

DIM = 4
TODAY = date(2026, 10, 21)
QUESTION = "How much did I spend on dining last month?"
PARAPHRASE = "What was my total dining spending last month?"
ANSWER = "You spent ₹4,321.50 on dining last month."
ROWS = result([("total_spend", "number")], [[4321.5]])


def rows(value: float) -> Any:
    return result([("total_spend", "number")], [[value]])


class Embed:
    """Questions about dining embed identically; everything else is orthogonal."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, text: str) -> list[float]:
        self.calls += 1
        return [1.0, 0.0, 0.0, 0.0] if "dining" in text else [0.0, 0.0, 1.0, 0.0]


class World:
    """One runner, one cache, and a way to ask questions as different users."""

    def __init__(self, mode: Mode = "on") -> None:
        self.embed = Embed()
        self.store = MemoryStore()
        self.cache = SemanticCache(
            self.store, self.embed, CacheConfig(similarity_threshold=0.9, dimensions=DIM)
        )
        self.runner = CachedTurnRunner(self.cache, mode=mode, tz=ZoneInfo("Asia/Kolkata"))
        self.saved: list[TurnRecord] = []
        self.llm_calls_total = 0

    async def _save_turn(self, record: TurnRecord) -> uuid.UUID:
        self.saved.append(record)
        return uuid.uuid4()

    async def _record_usage(self, usage: ModelUsage) -> None:
        return None

    async def ask(
        self,
        question: str,
        llm: ScriptedLLM,
        sql: FakeSql,
        *,
        user: uuid.UUID = USER,
        data_version: str = "ledger-1",
        today: date = TODAY,
    ) -> list[Any]:
        deps = AgentDeps(
            llm=llm,
            sql=sql,
            catalog=FakeCatalog(),
            record_usage=self._record_usage,
            save_turn=self._save_turn,
        )
        turn = TurnInput(user_id=user, conversation_id=CONVERSATION, question=question, today=today)
        events = [e async for e in self.runner.run(turn, deps, data_version=data_version)]
        self.llm_calls_total += len(llm.calls)
        return events


def shown(events: list[Any]) -> str:
    out: list[str] = []
    for event in events:
        if isinstance(event, RetractEvent):
            out.clear()
        elif isinstance(event, TokenEvent):
            out.append(event.text)
    return "".join(out).strip()


def kinds(events: list[Any]) -> list[str]:
    return [e.type for e in events]


def fresh_llm(*replies: Any) -> ScriptedLLM:
    return ScriptedLLM(list(replies))


def miss_script() -> ScriptedLLM:
    return fresh_llm(sql_reply(DINING_SQL, "Dining total.", "none"), text_reply(ANSWER))


async def warm(world: World) -> list[Any]:
    """Run QUESTION once through the real agent so the cache has an entry."""
    return await world.ask(QUESTION, miss_script(), FakeSql([ROWS]))


# -------------------------------------------------------------------------- a hit


async def test_exact_repeat_costs_no_model_call_and_reads_the_database() -> None:
    world = World()
    first = await warm(world)
    assert world.cache.metrics.counters["stored"] == 1

    sql = FakeSql([ROWS])
    llm = fresh_llm()  # any model call would raise
    second = await world.ask(QUESTION, llm, sql)

    assert llm.calls == []
    assert len(sql.executed) == 1 and sql.executed[0][1] == USER
    assert shown(second) == ANSWER
    assert kinds(second) == [
        "status", "sql", "table", "chart", *["token"] * kinds(second).count("token"), "done",
    ]  # fmt: skip
    # The UI receives the same evidence as on the first, uncached turn.
    assert next(e for e in second if isinstance(e, SqlEvent)) == next(
        e for e in first if isinstance(e, SqlEvent)
    )
    assert next(e for e in second if isinstance(e, TableEvent)).rows == [[4321.5]]
    done = second[-1]
    assert isinstance(done, DoneEvent) and done.usage.total_tokens == 0
    assert done.status == "answered"
    assert world.embed.calls == 1  # embedded once, on the first turn's lookup; memoised after


async def test_a_paraphrase_is_served_from_the_semantic_tier() -> None:
    world = World()
    await warm(world)
    llm = fresh_llm()
    events = await world.ask(PARAPHRASE, llm, FakeSql([ROWS]))
    assert llm.calls == [] and shown(events) == ANSWER
    assert world.cache.metrics.counters["hit_semantic"] == 1


async def test_a_served_turn_is_saved_to_the_conversation_like_any_other() -> None:
    world = World()
    await warm(world)
    await world.ask(PARAPHRASE, fresh_llm(), FakeSql([ROWS]))
    saved = world.saved[-1]
    assert saved.question == PARAPHRASE  # what the user asked, not what was cached
    assert saved.answer == ANSWER and saved.status == "answered"
    assert saved.sql and "LIMIT 500" in saved.sql
    assert saved.result_summary and "1 row" in saved.result_summary
    assert saved.usage.total_tokens == 0


# ---------------------------------------------------- hits that must be refused


async def test_changed_data_is_never_answered_with_the_old_figure() -> None:
    world = World()
    await warm(world)
    # Same ledger version, but the rows moved (a change the version did not capture).
    llm = fresh_llm(
        sql_reply(DINING_SQL, "Dining total.", "none"),
        text_reply("You spent ₹5,000.25 on dining last month."),
    )
    sql = FakeSql([rows(5000.25), rows(5000.25)])
    events = await world.ask(QUESTION, llm, sql)
    assert "5,000.25" in shown(events) and "4,321.50" not in shown(events)
    assert len(llm.calls) == 2  # the agent ran
    assert world.cache.metrics.counters["replay_stale"] == 1


async def test_after_a_stale_hit_the_next_ask_is_served_from_the_refreshed_entry() -> None:
    world = World()
    await warm(world)
    llm = fresh_llm(sql_reply(DINING_SQL), text_reply("You spent ₹5,000.25 on dining last month."))
    await world.ask(QUESTION, llm, FakeSql([rows(5000.25), rows(5000.25)]))
    repeat = fresh_llm()
    events = await world.ask(QUESTION, repeat, FakeSql([rows(5000.25)]))
    assert repeat.calls == [] and "5,000.25" in shown(events)


async def test_new_ledger_version_misses() -> None:
    world = World()
    await warm(world)
    llm = miss_script()
    await world.ask(QUESTION, llm, FakeSql([ROWS]), data_version="ledger-2")
    assert len(llm.calls) == 2


async def test_another_user_never_shares_an_entry() -> None:
    world = World()
    await warm(world)
    other = uuid.uuid4()
    llm = miss_script()
    sql = FakeSql([ROWS])
    await world.ask(QUESTION, llm, sql, user=other)
    assert len(llm.calls) == 2 and sql.executed[0][1] == other


async def test_next_day_misses_for_time_relative_questions() -> None:
    world = World()
    await warm(world)
    llm = miss_script()
    await world.ask(QUESTION, llm, FakeSql([ROWS]), today=date(2026, 10, 22))
    assert len(llm.calls) == 2


async def test_a_different_month_is_a_miss_even_though_the_embedding_agrees() -> None:
    world = World()
    await warm(world)
    llm = fresh_llm(sql_reply(DINING_SQL), text_reply(ANSWER))
    await world.ask("How much did I spend on dining in September?", llm, FakeSql([ROWS]))
    assert len(llm.calls) == 2
    assert world.cache.metrics.counters.get("hit_semantic", 0) == 0


async def test_a_cached_sql_the_guard_now_rejects_is_evicted_and_the_agent_runs() -> None:
    world = World()
    await warm(world)
    entry_key = next(iter(world.store._items))
    item = world.store._items[entry_key]

    world.store._items[entry_key] = replace(
        item, entry=replace(item.entry, sql="DELETE FROM ledger.transactions")
    )
    llm = miss_script()
    sql = FakeSql([ROWS])
    events = await world.ask(QUESTION, llm, sql)
    assert len(llm.calls) == 2 and shown(events) == ANSWER
    assert world.cache.metrics.counters["replay_query_failed"] == 1
    assert [s for s, _ in sql.executed] == [sql.executed[0][0]]  # only the agent's query ran


async def test_a_tampered_answer_fails_grounding_against_the_fresh_rows() -> None:
    world = World()
    await warm(world)

    key = next(iter(world.store._items))
    item = world.store._items[key]
    forged = replace(item.entry, answer="You spent ₹9,999.00 on dining last month.")
    world.store._items[key] = replace(item, entry=forged)
    llm = miss_script()
    events = await world.ask(QUESTION, llm, FakeSql([ROWS, ROWS]))  # replay, then the agent
    assert shown(events) == ANSWER and "9,999" not in shown(events)
    assert world.cache.metrics.counters["replay_ungrounded"] == 1


async def test_a_database_error_during_replay_falls_back_to_the_agent() -> None:

    world = World()
    await warm(world)
    llm = miss_script()
    events = await world.ask(QUESTION, llm, FakeSql([QueryExecutionError("timeout"), ROWS]))
    assert shown(events) == ANSWER and len(llm.calls) == 2


# ----------------------------------------------------------- what is never cached


async def test_follow_up_questions_bypass_the_cache_entirely() -> None:
    world = World()
    llm = fresh_llm(text_reply("Nothing to add."))
    await world.ask("and what about the month before?", llm, FakeSql())
    assert world.cache.metrics.counters.get("lookup", 0) == 0
    assert world.cache.metrics.counters["bypass"] == 1
    assert len(world.store) == 0


async def test_clarifications_and_refusals_are_not_stored() -> None:
    world = World()
    await world.ask(
        "How much did I spend in Q3?",
        fresh_llm(
            tool_reply("ask_clarification", {"question": "Which Q3?", "options": ["FY", "CY"]})
        ),
        FakeSql(),
    )
    await world.ask(
        "Delete all my transactions please",
        fresh_llm(sql_reply("DROP TABLE x"), text_reply("I can only read your statement.")),
        FakeSql(),
    )
    assert len(world.store) == 0


async def test_errors_are_not_stored() -> None:
    world = World()
    outage = LLMError("The model is busy or unreachable.", code="llm_rate_limited")
    events = await world.ask(QUESTION, fresh_llm(outage), FakeSql())
    assert isinstance(events[-1], ErrorEvent)
    assert len(world.store) == 0


async def test_turns_with_two_queries_are_not_stored() -> None:
    world = World()
    llm = fresh_llm(
        sql_reply(DINING_SQL, call_id="a"),
        sql_reply(DINING_SQL, call_id="b"),
        text_reply("You spent ₹4,321.50 on dining last month."),
    )
    await world.ask(QUESTION, llm, FakeSql([ROWS, ROWS]))
    assert len(world.store) == 0


async def test_semantic_transaction_search_turns_are_not_stored() -> None:
    world = World()
    search = tool_reply("search_transactions", {"query": "dining"})
    sql = FakeSql()

    async def search_transactions(*_: Any, **__: Any) -> Any:
        return result([("id", "number"), ("amount", "number")], [[1, 450.0], [2, 600.0]])

    sql.search_transactions = search_transactions  # type: ignore[attr-defined]
    await world.ask(
        "Find my dining transactions last month",
        fresh_llm(search, text_reply("I found 2 dining transactions totalling ₹1,050.00.")),
        sql,
    )
    assert len(world.store) == 0


async def test_a_retracted_answer_is_not_what_gets_cached() -> None:
    world = World()
    llm = fresh_llm(sql_reply(DINING_SQL), text_reply("You spent ₹12,000."), text_reply(ANSWER))
    events = await world.ask(QUESTION, llm, FakeSql([ROWS]))
    assert "retract" in kinds(events)
    entry = next(iter(world.store._items.values())).entry
    assert entry.answer == ANSWER


async def test_no_ledger_version_means_no_cache() -> None:
    world = World()
    await world.ask(QUESTION, miss_script(), FakeSql([ROWS]), data_version="")
    assert len(world.store) == 0 and world.cache.metrics.counters.get("lookup", 0) == 0


# ------------------------------------------------------------------ modes and safety


async def test_off_mode_is_a_pure_passthrough() -> None:
    world = World(mode="off")
    await warm(world)
    assert len(world.store) == 0


async def test_shadow_mode_never_serves_but_records_and_compares() -> None:
    world = World(mode="shadow")
    await warm(world)
    assert len(world.store) == 1  # entries are written so lookups can be evaluated
    llm = miss_script()
    sql = FakeSql([ROWS])
    events = await world.ask(PARAPHRASE, llm, sql)
    assert len(llm.calls) == 2  # the agent really ran
    assert shown(events) == ANSWER
    assert world.cache.metrics.counters["hit_semantic"] == 1  # the candidate was found
    assert "served" not in world.cache.metrics.counters


async def test_shadow_logs_whether_the_candidate_would_have_been_right(caplog: Any) -> None:

    world = World(mode="shadow")
    await warm(world)
    with caplog.at_level(logging.INFO, logger="finqon.cache.runner"):
        await world.ask(PARAPHRASE, miss_script(), FakeSql([ROWS]))
    records = [r for r in caplog.records if r.message == "semantic_cache_shadow"]
    record = records[-1]  # the second turn; the first had nothing cached to compare
    assert record.shadow_sql_match is True and record.shadow_result_match is True
    assert record.shadow_answer_match is True and record.cache_tier == "semantic"


async def test_a_broken_cache_cannot_break_a_turn() -> None:
    class Exploding(MemoryStore):
        async def get_exact(self, *_: Any, **__: Any) -> Any:
            raise ConnectionError("redis is on fire")

        async def put(self, *_: Any, **__: Any) -> None:
            raise ConnectionError("redis is on fire")

    world = World()
    world.cache = SemanticCache(Exploding(), Embed(), CacheConfig(dimensions=DIM))
    world.runner = CachedTurnRunner(world.cache, mode="on", tz=ZoneInfo("Asia/Kolkata"))
    events = await world.ask(QUESTION, miss_script(), FakeSql([ROWS]))
    assert shown(events) == ANSWER and isinstance(events[-1], DoneEvent)


async def test_the_runner_emits_exactly_what_the_agent_emits() -> None:
    """On a miss the cache is invisible: same events as calling the agent directly."""
    world = World()
    cached = await world.ask(QUESTION, miss_script(), FakeSql([ROWS]), data_version="")

    plain_world = World()
    deps = AgentDeps(
        llm=miss_script(),
        sql=FakeSql([ROWS]),
        catalog=FakeCatalog(),
        record_usage=plain_world._record_usage,
        save_turn=plain_world._save_turn,
    )
    turn = TurnInput(user_id=USER, conversation_id=CONVERSATION, question=QUESTION, today=TODAY)
    plain = [e async for e in run_turn(turn, deps)]
    assert [type(e) for e in cached] == [type(e) for e in plain]


def test_schema_version_changes_with_the_catalog() -> None:

    base = schema_version(CATALOG)
    assert schema_version(CATALOG) == base
    other = replace(CATALOG, categories=(*CATALOG.categories,))
    assert schema_version(other) == base  # equal catalogs, equal version

    changed = replace(CATALOG, categories=(*CATALOG.categories, Category("Fuel", "expense")))
    assert schema_version(changed) != base


def test_the_analyser_agrees_the_test_questions_are_equivalent() -> None:
    assert analyse(QUESTION).signature == analyse(PARAPHRASE).signature
