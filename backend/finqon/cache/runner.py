"""Runs a turn through the semantic cache, around the unchanged agent loop.

``CachedTurnRunner.run`` has the same contract as ``agent.orchestrator.run_turn``: it yields
the same ``ChatEvent`` objects, so the API, the stream and the UI do not change.

* **Hit (mode ``on``):** the cached SQL is re-validated by the guard and re-run against the
  database *as the asking user* (row-level security applies). The result is fingerprinted. Only
  if the fingerprint equals the one stored with the answer, and the answer still passes the
  grounding check against the fresh rows, is the stored answer replayed. That costs one SQL
  round trip and zero model calls. Any doubt (guard, error, timeout, changed data, failed
  grounding) evicts the entry and runs the normal agent: a cache can only ever make a turn
  cheaper, never different.
* **Miss:** the agent runs as before while a recorder watches its events. If the turn ended
  as a verified, single-query answer, it is stored before ``done`` is sent.
* **Shadow mode:** lookups run and are logged, nothing is served, and the real answer is
  compared with the candidate: that measures the false-hit rate before anything is trusted.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol, cast
from zoneinfo import ZoneInfo

from finqon.agent.catalog import SchemaCatalog
from finqon.agent.contracts import AgentDeps, TurnInput, TurnRecord
from finqon.agent.few_shot_examples import CLARIFICATION_EXAMPLE, REFUSAL_EXAMPLE, SQL_EXAMPLES
from finqon.agent.grounding import numbers_in, verify_answer
from finqon.agent.orchestrator import choose_chart, chunk_text, one_line_summary, run_turn
from finqon.agent.prompt_builder import RULES
from finqon.cache.analysis import Analysis, analyse
from finqon.cache.models import FORMAT_VERSION, PlanRecord, result_digest
from finqon.cache.service import CacheScope, Hit, Lookup, SemanticCache
from finqon.schemas.chat import (
    ChartEvent,
    ChartKind,
    ChatEvent,
    DoneEvent,
    ErrorEvent,
    RetractEvent,
    SqlEvent,
    StatusEvent,
    TableEvent,
    TokenEvent,
    TokenUsage,
)
from finqon.sql.guard import SqlGuardError
from finqon.sql.results import QueryExecutionError, summarize

logger = logging.getLogger(__name__)

Mode = Literal["off", "shadow", "on"]
_CHART_KINDS = {"bar", "pie", "line", "none"}
_STORE_BUDGET_SECONDS = 1.0


class TurnRunner(Protocol):
    def __call__(self, turn: TurnInput, deps: AgentDeps) -> AsyncIterator[ChatEvent]: ...


@functools.lru_cache(maxsize=8)
def schema_version(catalog: SchemaCatalog) -> str:
    """Changes whenever the views, categories, rules or examples the model plans from change.

    A plan written for yesterday's schema or prompt is not reused after either moves on.
    """
    material = repr((FORMAT_VERSION, catalog, RULES, SQL_EXAMPLES, CLARIFICATION_EXAMPLE))
    return hashlib.sha256((material + repr(REFUSAL_EXAMPLE)).encode()).hexdigest()[:16]


@dataclass(slots=True)
class _Recorder:
    """Watches the event stream of one turn and decides whether it is worth caching."""

    pending: SqlEvent | None = None
    queries: list[tuple[SqlEvent, TableEvent]] = field(default_factory=list)
    chart: ChartEvent | None = None
    text: list[str] = field(default_factory=list)
    disqualified: bool = False

    def observe(self, event: ChatEvent) -> None:
        if isinstance(event, SqlEvent):
            if event.sql.startswith("--"):  # semantic transaction search: not a replayable plan
                self.disqualified = True
            self.pending = event
        elif isinstance(event, TableEvent):
            if self.pending is None:
                self.disqualified = True
            else:
                self.queries.append((self.pending, event))
            self.pending = None
        elif isinstance(event, ChartEvent):
            self.chart = event
        elif isinstance(event, TokenEvent):
            self.text.append(event.text)
        elif isinstance(event, RetractEvent):
            self.text.clear()
        elif isinstance(event, ErrorEvent):
            self.disqualified = True

    @property
    def answer(self) -> str:
        return "".join(self.text).strip()

    def plan(self, status: str) -> PlanRecord | None:
        """A plan exists only for a verified answer built from exactly one query."""
        if self.disqualified or status != "answered" or len(self.queries) != 1:
            return None
        answer = self.answer
        if not answer:
            return None
        sql_event, table = self.queries[0]
        return PlanRecord(
            sql=sql_event.sql,
            purpose=sql_event.purpose,
            chart_kind=self.chart.kind if self.chart else "none",
            truncated=table.truncated,
            answer=answer,
            result_digest=result_digest(table.columns, table.rows),
            row_count=len(table.rows),
        )


class CachedTurnRunner:
    def __init__(
        self,
        cache: SemanticCache,
        *,
        mode: Mode,
        tz: ZoneInfo,
        inner: TurnRunner = run_turn,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._cache = cache
        self._mode = mode
        self._tz = tz
        self._inner = inner
        self._clock = clock

    async def run(
        self, turn: TurnInput, deps: AgentDeps, *, data_version: str
    ) -> AsyncIterator[ChatEvent]:
        analysis = analyse(turn.question)
        if self._mode == "off" or not data_version or not analysis.cacheable:
            self._cache.metrics.incr("bypass")
            async for event in self._inner(turn, deps):
                yield event
            return

        scope = CacheScope.at(
            turn.user_id,
            data_version,
            schema_version(await deps.catalog.get()),
            self._tz,
            today=turn.today,
        )
        lookup = await self._cache.lookup(scope, analysis)

        if lookup.hit is not None and self._mode == "on":
            started = self._clock()
            replay = await self._replay(turn, deps, scope, lookup.hit)
            if replay is not None:
                self._log(turn, "served", lookup, replay_ms=_ms(self._clock() - started))
                for event in replay:
                    yield event
                return
            self._log(turn, "replay_rejected", lookup)

        recorder = _Recorder()
        async for event in self._inner(turn, deps):
            recorder.observe(event)
            if isinstance(event, DoneEvent):
                await self._after_turn(
                    turn,
                    scope=scope,
                    analysis=analysis,
                    lookup=lookup,
                    plan=recorder.plan(event.status),
                )
            yield event

    # -- hit ------------------------------------------------------------------------

    async def _replay(
        self, turn: TurnInput, deps: AgentDeps, scope: CacheScope, hit: Hit
    ) -> list[ChatEvent] | None:
        """Build the full event list for a hit, or ``None`` if anything fails to verify."""
        entry = hit.entry
        try:
            guarded = deps.sql.guard(entry.sql)
            async with asyncio.timeout(deps.limits.deadline_seconds):
                result = await deps.sql.execute(guarded, turn.user_id)
        except (SqlGuardError, QueryExecutionError, TimeoutError):
            logger.info("semantic_cache_replay_query_failed", exc_info=True)
            await self._cache.evict(scope, hit)
            self._cache.metrics.incr("replay_query_failed")
            return None
        except Exception:
            logger.warning("semantic_cache_replay_error", exc_info=True)
            return None

        table = TableEvent(
            columns=result.columns,
            rows=result.rows,
            truncated=entry.truncated and result.row_count >= guarded.limit,
            elapsed_ms=result.elapsed_ms,
        )
        if result_digest(table.columns, table.rows) != entry.result_digest:
            # The data under the stored answer is not the data the answer was written for.
            await self._cache.evict(scope, hit)
            self._cache.metrics.incr("replay_stale")
            return None

        summary = summarize(result)
        allowed = [*numbers_in(turn.question), *summary.grounding_values()]
        allowed.extend(numbers_in(guarded.sql))
        evidence = "\n".join(
            (turn.question, guarded.sql, json.dumps(summary.to_model_payload(), default=str))
        )
        if not verify_answer(entry.answer, allowed, evidence=evidence).ok:
            await self._cache.evict(scope, hit)
            self._cache.metrics.incr("replay_ungrounded")
            return None

        kind = cast(ChartKind, entry.chart_kind if entry.chart_kind in _CHART_KINDS else "none")
        record = TurnRecord(
            conversation_id=turn.conversation_id,
            user_id=turn.user_id,
            question=turn.question,
            sql=guarded.sql,
            purpose=entry.purpose,
            result_summary=one_line_summary(result),
            answer=entry.answer,
            status="answered",
            usage=TokenUsage(),
        )
        try:
            message_id = await deps.save_turn(record)
        except Exception:
            logger.warning("semantic_cache_replay_save_failed", exc_info=True)
            return None

        events: list[ChatEvent] = [
            StatusEvent(stage="querying"),
            SqlEvent(sql=guarded.sql, purpose=entry.purpose),
            table,
            choose_chart(kind, result.columns, result.row_count),
            *(TokenEvent(text=piece) for piece in chunk_text(entry.answer)),
            DoneEvent(
                message_id=message_id,
                conversation_id=turn.conversation_id,
                usage=TokenUsage(),
                status="answered",
            ),
        ]
        self._cache.metrics.incr("served")
        return events

    # -- miss -----------------------------------------------------------------------

    async def _after_turn(
        self,
        turn: TurnInput,
        *,
        scope: CacheScope,
        analysis: Analysis,
        lookup: Lookup,
        plan: PlanRecord | None,
    ) -> None:
        """Store or compare. Runs before ``done`` is sent and can never raise."""
        try:
            if self._mode == "shadow":
                self._log_shadow(turn, lookup, plan)
            if plan is None:
                return
            hit = lookup.hit
            if hit is not None and self._mode == "shadow":
                same = hit.entry.sql == plan.sql and hit.entry.result_digest == plan.result_digest
                if same:
                    return  # an equivalent entry is already stored
            async with asyncio.timeout(_STORE_BUDGET_SECONDS):
                await self._cache.remember(scope, analysis, plan, lookup.embedding)
        except Exception:
            logger.warning("semantic_cache_store_failed", exc_info=True)

    # -- telemetry ------------------------------------------------------------------

    def _log(self, turn: TurnInput, outcome: str, lookup: Lookup, **extra: object) -> None:
        hit = lookup.hit
        logger.info(
            "semantic_cache_turn",
            extra={
                "cache_outcome": outcome,
                "cache_tier": hit.tier if hit else None,
                "cache_similarity": round(hit.similarity, 4) if hit else None,
                "cache_lookup_ms": lookup.elapsed_ms,
                "cache_hit_rate": round(self._cache.metrics.hit_rate, 4),
                "conversation_id": str(turn.conversation_id),
                **extra,
            },
        )

    def _log_shadow(self, turn: TurnInput, lookup: Lookup, plan: PlanRecord | None) -> None:
        candidate = lookup.hit.entry if lookup.hit else None
        neighbour = lookup.neighbour
        logger.info(
            "semantic_cache_shadow",
            extra={
                "cache_reason": lookup.reason,
                "cache_tier": lookup.hit.tier if lookup.hit else None,
                "cache_similarity": round(lookup.hit.similarity, 4)
                if lookup.hit
                else (round(neighbour.similarity, 4) if neighbour else None),
                "cache_lookup_ms": lookup.elapsed_ms,
                # When a candidate existed: would serving it have been right?
                "shadow_sql_match": (candidate.sql == plan.sql) if candidate and plan else None,
                "shadow_result_match": (candidate.result_digest == plan.result_digest)
                if candidate and plan
                else None,
                "shadow_answer_match": (candidate.answer == plan.answer)
                if candidate and plan
                else None,
                "conversation_id": str(turn.conversation_id),
            },
        )


def _ms(seconds: float) -> int:
    return int(seconds * 1000)
