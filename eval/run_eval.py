"""Run the FinQon evaluation set and write ``eval/reports/latest.md``.

    python eval/run_eval.py                    # full run (needs the model key)
    python eval/run_eval.py --reference-only   # validate the reference SQL, no model
    python eval/run_eval.py --only dining-last-month,top-5-merchants

Each case runs as the seeded primary user through the same ``ChatService`` the API uses.
Answers are judged by the values in the result table, never by SQL text. The report lists
every failure; nothing is hidden or retried until it passes.

Exit code: 0 when every case passed, 1 when any failed, 2 on configuration problems.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from finqon.core.config import ConfigurationError, Settings, get_settings
from finqon.core.logging import configure_logging
from finqon.evaluation.cases import EvalCase, load_cases
from finqon.evaluation.scoring import (
    Verdict,
    check_clarification,
    check_forbidden_phrases,
    check_refusal,
    compare_results,
)
from finqon.llm.client import ChatResult
from finqon.repositories.conversations import ConversationRepository, NewMessage
from finqon.repositories.users import UserRepository
from finqon.resources import Resources
from finqon.schemas.chat import (
    ClarificationEvent,
    DoneEvent,
    ErrorEvent,
    SqlEvent,
    TableEvent,
    TokenEvent,
)
from finqon.services.chat import ChatService

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_CASES = EVAL_DIR / "questions.yaml"
DEFAULT_REPORT = EVAL_DIR / "reports" / "latest.md"
DEFAULT_USER = "kalpeshpawar@gmail.com"
COUNT_TRANSACTIONS = "SELECT COUNT(*) AS n FROM reporting.transactions"
PREVIEW_ROWS = 5

SPEND_DEFINITION = (
    "Spend = every debit that is not a transfer, minus linked refunds (a refund is negative "
    "spend on the day it arrives). Salary and other credits are not spend. Transfers, such as "
    "money sent to parents, are not spend. This is `reporting.spend`; the reference SQL uses it."
)


@dataclass(slots=True)
class CaseResult:
    case: EvalCase
    verdict: Verdict
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    answer: str = ""
    agent_sql: str | None = None
    expected: list[list[Any]] = field(default_factory=list)
    actual: list[list[Any]] = field(default_factory=list)


class _NoModel:
    """Stands in for the model when only the reference SQL is being checked."""

    async def chat(self, *_: object, **__: object) -> ChatResult:
        raise RuntimeError("--reference-only makes no model calls")


# ------------------------------------------------------------------------------ entry


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = get_settings()
        settings.require_runtime_settings(require_llm=not args.reference_only)
        cases = _select(load_cases(args.cases), args.only)
    except (ConfigurationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    configure_logging("WARNING", stream=sys.stderr)
    return asyncio.run(_run(args, settings, cases))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--user", default=DEFAULT_USER, help="Seed user to run as.")
    parser.add_argument("--only", help="Comma-separated case ids to run.")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between model calls.")
    parser.add_argument(
        "--reference-only", action="store_true", help="Only execute the reference SQL."
    )
    return parser


def _select(cases: list[EvalCase], only: str | None) -> list[EvalCase]:
    if not only:
        return cases
    wanted = {part.strip() for part in only.split(",") if part.strip()}
    unknown = wanted - {case.id for case in cases}
    if unknown:
        raise ValueError(f"unknown case ids: {', '.join(sorted(unknown))}")
    return [case for case in cases if case.id in wanted]


async def _run(args: argparse.Namespace, settings: Settings, cases: list[EvalCase]) -> int:
    resources = await Resources.create(settings, llm=_NoModel() if args.reference_only else None)
    try:
        async with resources.database.session() as session:
            user = await UserRepository(session).get_by_email(args.user.lower())
        if user is None:
            print(f"error: no user {args.user!r}; run dataset/load_ledger.py", file=sys.stderr)
            return 2

        if args.reference_only:
            return await _check_references(resources, user.id, cases)

        results = await _evaluate(resources, user.id, cases, args.delay)
        report = render_report(results, settings, args.user)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
        passed = sum(r.verdict.passed for r in results)
        print(f"{passed}/{len(results)} passed. Report: {args.report}")
        return 0 if passed == len(results) else 1
    finally:
        await resources.close()


# --------------------------------------------------------------------------- references


async def _check_references(resources: Resources, user_id: uuid.UUID, cases: list[EvalCase]) -> int:
    broken = 0
    for case in cases:
        if case.expect != "values" or case.reference_sql is None:
            print(f"  skip   {case.id} ({case.expect})")
            continue
        try:
            result = await resources.executor.run(case.reference_sql, user_id)
        except Exception as exc:  # reported per case so one bad reference does not hide the rest
            broken += 1
            print(f"  BROKEN {case.id}: {exc}")
            continue
        first = [_plain(cell) for cell in result.raw_rows[0]] if result.raw_rows else "no rows"
        print(f"  ok     {case.id}: {result.row_count} row(s), first = {first}")
    print(f"{broken} broken reference(s)")
    return 1 if broken else 0


# --------------------------------------------------------------------------- evaluation


async def _evaluate(
    resources: Resources, user_id: uuid.UUID, cases: list[EvalCase], delay: float
) -> list[CaseResult]:
    service = ChatService(resources)
    baseline = await _transaction_count(resources, user_id)
    results: list[CaseResult] = []

    for position, case in enumerate(cases, start=1):
        print(f"[{position}/{len(cases)}] {case.id} ...", end=" ", flush=True)
        result = await _run_case(resources, service, user_id, case)

        if await _transaction_count(resources, user_id) != baseline:
            result.verdict = Verdict.fail("DATA CHANGED: the user's ledger differs after this case")
        print("PASS" if result.verdict.passed else f"FAIL - {result.verdict.reason}")
        results.append(result)
        if delay and position < len(cases):
            await asyncio.sleep(delay)
    return results


async def _transaction_count(resources: Resources, user_id: uuid.UUID) -> int:
    result = await resources.executor.run(COUNT_TRANSACTIONS, user_id)
    return int(result.raw_rows[0][0])


async def _run_case(
    resources: Resources, service: ChatService, user_id: uuid.UUID, case: EvalCase
) -> CaseResult:
    result = CaseResult(case=case, verdict=Verdict.fail("not run"))

    reference_rows: list[list[Any]] = []
    if case.expect == "values" and case.reference_sql:
        try:
            executed = await resources.executor.run(case.reference_sql, user_id)
        except Exception as exc:  # a broken reference is a defect in the eval, not the agent
            result.verdict = Verdict.fail(f"reference SQL failed: {exc}")
            return result
        reference_rows = [list(row) for row in executed.raw_rows]
        result.expected = reference_rows

    started = time.perf_counter()
    try:
        conversation = await _open(resources, service, user_id, case)
        events = [e async for e in service.stream_turn(user_id, conversation, case.question)]
    except Exception as exc:  # the harness must finish the run and report the crash
        result.verdict = Verdict.fail(f"crashed: {type(exc).__name__}: {exc}")
        return result
    result.latency_ms = int((time.perf_counter() - started) * 1000)

    tables = [e for e in events if isinstance(e, TableEvent)]
    clarifications = [e for e in events if isinstance(e, ClarificationEvent)]
    queries = [e for e in events if isinstance(e, SqlEvent)]
    errors = [e for e in events if isinstance(e, ErrorEvent)]
    done = next((e for e in events if isinstance(e, DoneEvent)), None)

    reply = "".join(e.text for e in events if isinstance(e, TokenEvent))
    asked = " ".join(c.question for c in clarifications)
    result.answer = (reply or asked).strip()
    result.agent_sql = queries[-1].sql if queries else None
    result.actual = tables[-1].rows[:PREVIEW_ROWS] if tables else []
    if done is not None:
        result.prompt_tokens = done.usage.prompt_tokens
        result.completion_tokens = done.usage.completion_tokens

    if errors:
        result.verdict = Verdict.fail(f"error [{errors[0].code}]: {errors[0].message}")
        return result
    result.verdict = _judge(
        case, reference_rows, tables, clarifications, len(queries), reply, asked
    )
    return result


async def _open(resources: Resources, service: ChatService, user_id: uuid.UUID, case: EvalCase):
    """Open a conversation; for follow-ups, store the prior turn as real history first."""
    if case.prior is None:
        return await service.open_conversation(user_id, None, case.question)

    seed = await service.open_conversation(user_id, None, case.prior.question)
    async with resources.database.user_scope(user_id) as session:
        await ConversationRepository(session).add_message(
            NewMessage(
                conversation_id=seed.id,
                user_id=user_id,
                question=case.prior.question,
                sql=case.prior.sql.strip(),
                purpose=None,
                result_summary=case.prior.summary,
                answer=case.prior.answer,
                status="answered",
                prompt_tokens=0,
                completion_tokens=0,
            )
        )
    return await service.open_conversation(user_id, seed.id, case.question)


def _judge(
    case: EvalCase,
    reference_rows: list[list[Any]],
    tables: list[TableEvent],
    clarifications: list[ClarificationEvent],
    queries_run: int,
    reply: str,
    asked: str,
) -> Verdict:
    if case.expect == "clarification":
        verdict = check_clarification(
            clarified=bool(clarifications), tables_shown=len(tables), answer=asked
        )
    elif case.expect == "refusal":
        verdict = check_refusal(
            case.question, reply, queries_run=queries_run, tables_shown=len(tables)
        )
    else:
        verdict = _judge_values(case, reference_rows, tables)

    if verdict.passed and case.must_not_match:
        verdict = check_forbidden_phrases(reply, case.must_not_match)
    return verdict


def _judge_values(
    case: EvalCase, reference_rows: list[list[Any]], tables: list[TableEvent]
) -> Verdict:
    """Pass when any table shown during the turn agrees with the reference."""
    if not tables:
        return Verdict.fail("no result table: the agent did not run a query")
    reasons: list[str] = []
    for table in tables:
        verdict = compare_results(
            reference_rows, table.rows, mode=case.compare, ordered=case.ordered
        )
        if verdict.passed:
            return verdict
        reasons.append(verdict.reason)
    return Verdict.fail(reasons[-1])


# ------------------------------------------------------------------------------ report


def _plain(value: Any) -> Any:
    return float(value) if hasattr(value, "as_tuple") else value


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _preview(rows: list[list[Any]]) -> str:
    if not rows:
        return "(no rows)"
    return "\n".join(str([_plain(c) for c in row]) for row in rows[:PREVIEW_ROWS])


def render_report(results: list[CaseResult], settings: Settings, user: str) -> str:
    total = len(results)
    passed = sum(r.verdict.passed for r in results)
    latencies = sorted(r.latency_ms for r in results if r.latency_ms)
    tokens = sum(r.prompt_tokens + r.completion_tokens for r in results)

    lines = [
        "# FinQon evaluation report",
        "",
        f"- **Result: {passed} of {total} passed** ({passed / total:.0%})"
        if total
        else "- No cases were run",
        f"- Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        f"- Model: `{settings.llm_model}`",
        f"- Run as: `{user}` (synthetic seed data, fixed seed)",
        f"- Total tokens: {tokens:,}",
    ]
    if latencies:
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
        lines.append(f"- Latency: median {int(statistics.median(latencies)):,} ms, p95 {p95:,} ms")
    lines += [
        "",
        "## Definition of spend",
        "",
        SPEND_DEFINITION,
        "",
        "Answers are judged by the values in the result table, not by SQL text. A turn passes "
        "when any table it showed agrees with the reference result (numbers within 0.01).",
        "",
        "## By tag",
        "",
        "| Tag | Passed | Total |",
        "| --- | ---: | ---: |",
    ]
    by_tag: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        by_tag[result.case.tag].append(result)
    for tag in sorted(by_tag):
        group = by_tag[tag]
        lines.append(f"| {tag} | {sum(r.verdict.passed for r in group)} | {len(group)} |")

    lines += [
        "",
        "## Cases",
        "",
        "| Case | Tag | Result | Latency | Tokens | Question | Mismatch |",
        "| --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for r in results:
        lines.append(
            f"| `{r.case.id}` | {r.case.tag} | {'PASS' if r.verdict.passed else '**FAIL**'} "
            f"| {r.latency_ms:,} ms | {r.prompt_tokens + r.completion_tokens:,} "
            f"| {_cell(r.case.question)} | {_cell(r.verdict.reason)} |"
        )

    failures = [r for r in results if not r.verdict.passed]
    lines += ["", "## Failures", ""]
    if not failures:
        lines.append("None.")
    for r in failures:
        lines += [
            f"### `{r.case.id}` ({r.case.tag})",
            "",
            f"- Question: {r.case.question}",
            f"- Expected: {r.case.expect}",
            f"- Mismatch: {r.verdict.reason}",
        ]
        if r.case.note:
            lines.append(f"- Note: {r.case.note}")
        if r.agent_sql:
            lines += ["", "Agent SQL:", "", "```sql", r.agent_sql.strip(), "```"]
        if r.case.expect == "values":
            lines += [
                "",
                "Expected rows:",
                "",
                "```",
                _preview(r.expected),
                "```",
                "",
                "Agent rows:",
                "",
                "```",
                _preview(r.actual),
                "```",
            ]
        if r.answer:
            lines += ["", f"Reply: {r.answer}"]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
