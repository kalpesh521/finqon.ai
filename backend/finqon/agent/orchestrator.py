"""The agent loop: one async function, no LLM framework.

``run_turn`` takes a question and yields ``ChatEvent`` objects. Caps: 6 model steps, 3 SQL
executions, 2 repairs, about 30 seconds (see docs/architecture.md).

Safety properties kept here:

* The model sees only a summary of each result (columns, row count, first 20 rows,
  numeric min/max/sum), never a full result set.
* The final prose is buffered, then checked against the tool results. A figure that
  was not shown to the model is regenerated once, then refused. Nothing unchecked is
  streamed to the client.
* Guard rejections of writes are not "repaired": the model is told to refuse.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from finqon.agent.contracts import AgentDeps, ModelUsage, TurnInput, TurnRecord
from finqon.agent.grounding import numbers_in, verify_answer
from finqon.agent.prompt_builder import build_system_prompt
from finqon.agent.tools import (
    ASK_CLARIFICATION,
    RUN_SQL,
    SEARCH_MERCHANTS,
    TOOL_DEFINITIONS,
    AskClarificationArgs,
    RunSqlArgs,
    SearchMerchantsArgs,
)
from finqon.llm.client import ChatResult, LLMError, ToolCall
from finqon.schemas.chat import (
    ChartEvent,
    ChartKind,
    ChatEvent,
    ClarificationEvent,
    DoneEvent,
    ErrorEvent,
    SqlEvent,
    StatusEvent,
    TableEvent,
    TokenEvent,
    TokenUsage,
)
from finqon.sql.guard import SqlGuardError
from finqon.sql.results import Column, QueryExecutionError, QueryResult, summarize

logger = logging.getLogger(__name__)

UNVERIFIED_MESSAGE = (
    "I couldn't verify the figures in my answer against your statement, so I won't state "
    "them. Please try rephrasing the question."
)
CANNOT_QUERY_MESSAGE = (
    "I wasn't able to build a valid query for that request, so I haven't answered. "
    "Try asking about your spending, merchants, categories or transactions."
)
READ_ONLY_MESSAGE = (
    "I can only read your statement. I can't change, delete or move anything, so nothing "
    "was changed."
)
_CHUNK_CHARS = 32


class _TurnFailure(Exception):
    """The turn cannot continue; surfaced to the client as an ``error`` event."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class _Final:
    status: str  # answered | clarification | refused
    text: str
    stream: bool = True


@dataclass(slots=True)
class _State:
    deadline: float
    allowed: list[Decimal]
    evidence: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    executions: int = 0
    repairs: int = 0
    successes: int = 0
    forbidden_attempts: int = 0
    sql: str | None = None
    purpose: str | None = None
    result_summary: str | None = None
    final: _Final | None = None
    awaiting_summary: bool = False


# ------------------------------------------------------------------------------ helpers


def choose_chart(hint: ChartKind, columns: Sequence[Column], row_count: int) -> ChartEvent:
    """Name the columns to plot. Never fabricates points; the UI plots ``table.rows``."""
    none = ChartEvent(kind="none", x="", y="")
    if hint == "none" or row_count < 2:
        return none
    numeric = [c for c in columns if c.type == "number"]
    if not numeric:
        return none
    preferred = "date" if hint == "line" else "string"
    x = next((c for c in columns if c.type == preferred), None) or next(
        (c for c in columns if c.type in {"string", "date"}), None
    )
    if x is None:
        return none
    y = next((c for c in numeric if c.name != x.name), None)
    if y is None:
        return none
    return ChartEvent(kind=hint, x=x.name, y=y.name)


def one_line_summary(result: QueryResult) -> str:
    summary = summarize(result)
    parts = [
        f"{result.row_count} row{'s' if result.row_count != 1 else ''}",
        "columns: " + ", ".join(c.name for c in result.columns),
    ]
    for name, stats in summary.numeric_stats.items():
        parts.append(f"sum({name})={stats.total}")
        break
    if result.truncated:
        parts.append("truncated")
    return "; ".join(parts)


def _tool_message(call: ToolCall, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "name": call.name,
        "content": json.dumps(payload, default=str, ensure_ascii=False),
    }


def _chunks(text: str) -> list[str]:
    pieces: list[str] = []
    current = ""
    for word in re.findall(r"\S+\s*", text):
        if current and len(current) + len(word) > _CHUNK_CHARS:
            pieces.append(current)
            current = ""
        current += word
    if current:
        pieces.append(current)
    return pieces or [text]


def _remaining(deps: AgentDeps, state: _State) -> float:
    remaining = state.deadline - deps.clock()
    if remaining <= 0:
        raise _TurnFailure("timeout", "That took too long. Please try again.")
    return remaining


# ---------------------------------------------------------------------------- the loop


async def run_turn(turn: TurnInput, deps: AgentDeps) -> AsyncIterator[ChatEvent]:
    state = _State(
        deadline=deps.clock() + deps.limits.deadline_seconds,
        allowed=numbers_in(turn.question),
        evidence=turn.question,
    )
    try:
        catalog = await deps.catalog.get()
        system_prompt = build_system_prompt(catalog, turn.today, turn.history, turn.previous_sql)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": turn.question},
        ]
        yield StatusEvent(stage="thinking")
        async for event in _loop(turn, deps, state, messages):
            yield event

        final = state.final
        if final is None:  # pragma: no cover - _loop either sets it or raises
            raise _TurnFailure("internal_error", "The assistant did not produce an answer.")
        if final.stream:
            for piece in _chunks(final.text):
                yield TokenEvent(text=piece)
        message_id = await deps.save_turn(_record(turn, state, final.text, final.status))
        yield DoneEvent(
            message_id=message_id, conversation_id=turn.conversation_id, usage=state.usage
        )
    except _TurnFailure as failure:
        await _save_failure(turn, deps, state, failure)
        yield ErrorEvent(code=failure.code, message=failure.message)


def _record(turn: TurnInput, state: _State, answer: str, status: str) -> TurnRecord:
    return TurnRecord(
        conversation_id=turn.conversation_id,
        user_id=turn.user_id,
        question=turn.question,
        sql=state.sql,
        purpose=state.purpose,
        result_summary=state.result_summary,
        answer=answer,
        status=status,
        usage=state.usage,
    )


async def _save_failure(
    turn: TurnInput, deps: AgentDeps, state: _State, failure: _TurnFailure
) -> None:
    try:
        await deps.save_turn(_record(turn, state, failure.message, "error"))
    except Exception:
        logger.exception("save_failed_turn_failed")


async def _loop(
    turn: TurnInput, deps: AgentDeps, state: _State, messages: list[dict[str, Any]]
) -> AsyncIterator[ChatEvent]:
    limits = deps.limits
    regenerated = False

    for _ in range(limits.max_model_steps):
        if state.awaiting_summary:
            yield StatusEvent(stage="summarizing")
            state.awaiting_summary = False

        result = await _call_model(turn, deps, state, messages)

        if not result.tool_calls:
            answer = (result.content or "").strip()
            verdict = (
                verify_answer(answer, state.allowed, evidence=state.evidence) if answer else None
            )
            if answer and verdict is not None and verdict.ok:
                status = "answered" if state.successes else "refused"
                state.final = _Final(status, answer)
                return
            if regenerated:
                state.final = _Final("refused", UNVERIFIED_MESSAGE)
                return
            regenerated = True
            ungrounded = verdict.ungrounded if verdict else []
            messages.append({"role": "assistant", "content": answer})
            messages.append({"role": "user", "content": _correction(ungrounded, bool(answer))})
            continue

        messages.append(result.assistant_message())
        for call in result.tool_calls:
            async for event in _dispatch(call, turn, deps, state, messages):
                yield event
            if state.final is not None:
                return

        if state.repairs > limits.max_repairs:
            message = READ_ONLY_MESSAGE if state.forbidden_attempts else CANNOT_QUERY_MESSAGE
            state.final = _Final("refused", message)
            return

    raise _TurnFailure(
        "step_limit", "I couldn't finish that within my step limit. Try a simpler question."
    )


def _correction(ungrounded: list[str], had_answer: bool) -> str:
    if not had_answer:
        return "Reply with the answer for the user, using only the tool results."
    return (
        "Your reply contained figures that do not appear in the tool results: "
        f"{', '.join(ungrounded)}. Rewrite it using only figures from the tool results. "
        "If the data you have cannot support the answer, run another query or say you could "
        "not determine it. Do not mention this correction."
    )


async def _call_model(
    turn: TurnInput, deps: AgentDeps, state: _State, messages: list[dict[str, Any]]
) -> ChatResult:
    remaining = _remaining(deps, state)
    try:
        async with asyncio.timeout(remaining):
            result = await deps.llm.chat(messages, TOOL_DEFINITIONS, stream=False)
    except TimeoutError as exc:
        raise _TurnFailure("timeout", "The model took too long. Please try again.") from exc
    except LLMError as exc:
        raise _TurnFailure(exc.code, str(exc)) from exc

    state.usage.add(result.prompt_tokens, result.completion_tokens)
    try:
        await deps.record_usage(
            ModelUsage(
                user_id=turn.user_id,
                conversation_id=turn.conversation_id,
                model=result.model,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                latency_ms=result.latency_ms,
            )
        )
    except Exception:
        logger.exception("model_usage_log_failed")
    return result


# ----------------------------------------------------------------------------- tools


async def _dispatch(
    call: ToolCall,
    turn: TurnInput,
    deps: AgentDeps,
    state: _State,
    messages: list[dict[str, Any]],
) -> AsyncIterator[ChatEvent]:
    if call.name == ASK_CLARIFICATION:
        async for event in _ask_clarification(call, state, messages):
            yield event
    elif call.name == SEARCH_MERCHANTS:
        await _search_merchants(call, turn, deps, state, messages)
    elif call.name == RUN_SQL:
        async for event in _run_sql(call, turn, deps, state, messages):
            yield event
    else:
        state.repairs += 1
        messages.append(_tool_message(call, {"error": f"Unknown tool '{call.name}'."}))


async def _ask_clarification(
    call: ToolCall, state: _State, messages: list[dict[str, Any]]
) -> AsyncIterator[ChatEvent]:
    try:
        args = AskClarificationArgs.model_validate_json(call.arguments)
    except ValidationError as exc:
        state.repairs += 1
        messages.append(_tool_message(call, {"error": _validation_message(exc)}))
        return
    options = args.clipped_options()
    yield ClarificationEvent(question=args.question, options=options)
    stored = args.question + (f" ({' / '.join(options)})" if options else "")
    state.final = _Final("clarification", stored, stream=False)


async def _search_merchants(
    call: ToolCall,
    turn: TurnInput,
    deps: AgentDeps,
    state: _State,
    messages: list[dict[str, Any]],
) -> None:
    try:
        args = SearchMerchantsArgs.model_validate_json(call.arguments)
    except ValidationError as exc:
        state.repairs += 1
        messages.append(_tool_message(call, {"error": _validation_message(exc)}))
        return
    try:
        async with asyncio.timeout(_remaining(deps, state)):
            matches = await deps.sql.search_merchants(args.query, turn.user_id)
    except QueryExecutionError as exc:
        state.repairs += 1
        messages.append(_tool_message(call, {"error": str(exc)}))
        return
    except TimeoutError as exc:
        raise _TurnFailure("timeout", "That took too long. Please try again.") from exc
    payload = {"matches": matches}
    state.evidence += "\n" + json.dumps(payload, default=str)
    messages.append(_tool_message(call, payload))


async def _run_sql(
    call: ToolCall,
    turn: TurnInput,
    deps: AgentDeps,
    state: _State,
    messages: list[dict[str, Any]],
) -> AsyncIterator[ChatEvent]:
    try:
        args = RunSqlArgs.model_validate_json(call.arguments)
    except ValidationError as exc:
        state.repairs += 1
        messages.append(_tool_message(call, {"error": _validation_message(exc)}))
        return

    try:
        guarded = deps.sql.guard(args.sql)
    except SqlGuardError as exc:
        state.repairs += 1
        if exc.forbidden:
            state.forbidden_attempts += 1
            payload = {
                "error": str(exc),
                "instruction": (
                    "This request is not allowed and will not be retried. If the user asked "
                    "to change data, tell them you can only read their statement."
                ),
            }
        else:
            payload = {"error": str(exc), "instruction": "Fix the SQL and call the tool again."}
        messages.append(_tool_message(call, payload))
        return

    if state.executions >= deps.limits.max_sql_executions:
        messages.append(
            _tool_message(
                call,
                {
                    "error": "The query budget for this question is used up. Answer with the "
                    "results you already have, or say you could not determine it."
                },
            )
        )
        return

    state.executions += 1
    yield StatusEvent(stage="querying")
    yield SqlEvent(sql=guarded.sql, purpose=args.purpose)
    try:
        async with asyncio.timeout(_remaining(deps, state)):
            result = await deps.sql.execute(guarded, turn.user_id)
    except QueryExecutionError as exc:
        state.repairs += 1
        messages.append(
            _tool_message(call, {"error": str(exc), "instruction": "Fix the SQL and try again."})
        )
        return
    except TimeoutError as exc:
        raise _TurnFailure("timeout", "That took too long. Please try again.") from exc

    summary = summarize(result)
    state.successes += 1
    state.sql, state.purpose = guarded.sql, args.purpose
    state.result_summary = one_line_summary(result)
    state.allowed.extend(summary.grounding_values())
    state.allowed.extend(numbers_in(guarded.sql))
    payload = summary.to_model_payload()
    state.evidence += "\n" + guarded.sql + "\n" + json.dumps(payload, default=str)
    state.awaiting_summary = True

    yield TableEvent(columns=result.columns, rows=result.rows, truncated=result.truncated)
    yield choose_chart(args.chart_hint, result.columns, result.row_count)
    payload["note"] = "Quote only figures and dates that appear in this result."
    messages.append(_tool_message(call, payload))


def _validation_message(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())[:300]
