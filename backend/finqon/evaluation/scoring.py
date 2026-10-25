"""Scoring rules for the evaluation set. Pure functions: no database, no model.

Answers are judged by the values the user would see, never by the SQL text. Two result
shapes can differ in column names, column order and extra columns and still agree.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from finqon.agent.grounding import numbers_in, verify_answer
from finqon.sql.results import summarize_rows

CompareMode = Literal["rows", "superset", "numbers"]

# Values are exact two-decimal amounts that only pass through float for JSON, so the only
# difference worth forgiving is one paisa of rounding. A relative tolerance would excuse
# real errors on large totals.
ABSOLUTE_TOLERANCE = 0.01


@dataclass(frozen=True, slots=True)
class Verdict:
    passed: bool
    reason: str = ""

    @classmethod
    def ok(cls) -> Verdict:
        return cls(True)

    @classmethod
    def fail(cls, reason: str) -> Verdict:
        return cls(False, reason)


# --------------------------------------------------------------------------- values


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float | Decimal) and not isinstance(value, bool)


def _canonical(value: Any) -> Any:
    """Make cells from the database and from the JSON event stream comparable."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()[:10]
    return str(value).strip().casefold()


def cells_equal(expected: Any, actual: Any) -> bool:
    left, right = _canonical(expected), _canonical(actual)
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right  # True == 1 in Python, but a flag is not a count
    if _is_number(left) and _is_number(right):
        return bool(abs(left - right) <= ABSOLUTE_TOLERANCE)
    return bool(left == right)


def _row_contains(reference_row: Sequence[Any], actual_row: Sequence[Any]) -> bool:
    """Every reference cell appears in the actual row, each actual cell used once."""
    remaining = list(actual_row)
    for cell in reference_row:
        index = next((i for i, other in enumerate(remaining) if cells_equal(cell, other)), None)
        if index is None:
            return False
        remaining.pop(index)
    return True


def _is_empty_answer(rows: Sequence[Sequence[Any]]) -> bool:
    """No rows, or only zeros and NULLs: both mean "nothing was spent"."""
    return all(
        cell is None or (_is_number(cell) and float(cell) == 0.0) for row in rows for cell in row
    )


def compare_results(
    reference: Sequence[Sequence[Any]],
    actual: Sequence[Sequence[Any]],
    *,
    mode: CompareMode = "rows",
    ordered: bool = False,
) -> Verdict:
    """Compare the agent's rows with the reference rows.

    ``rows``      same number of rows; each reference row is contained in one actual row.
    ``superset``  the agent may return extra rows (for "why" questions with context).
    ``numbers``   shape-agnostic: every numeric reference cell appears among the numeric
                  cells of the answer (a comparison may be one row or two).
    """
    if not reference:
        if _is_empty_answer(actual):
            return Verdict.ok()
        return Verdict.fail("reference is empty but the agent returned values")

    if mode == "numbers":
        return _compare_numbers(reference, actual)

    if mode == "rows" and len(actual) != len(reference):
        return Verdict.fail(f"expected {len(reference)} rows, agent returned {len(actual)}")
    if len(actual) < len(reference):
        return Verdict.fail(
            f"expected at least {len(reference)} rows, agent returned {len(actual)}"
        )

    if ordered:
        for position, row in enumerate(reference):
            if not _row_contains(row, actual[position]):
                got = list(actual[position])
                return Verdict.fail(f"row {position + 1} differs: expected {list(row)}, got {got}")
        return Verdict.ok()

    unused = list(range(len(actual)))
    for row in reference:
        match = next((i for i in unused if _row_contains(row, actual[i])), None)
        if match is None:
            return Verdict.fail(f"no agent row matches expected row {list(row)}")
        unused.remove(match)
    return Verdict.ok()


def _compare_numbers(
    reference: Sequence[Sequence[Any]], actual: Sequence[Sequence[Any]]
) -> Verdict:
    pool = [cell for row in actual for cell in row if _is_number(cell)]
    for cell in (cell for row in reference for cell in row if _is_number(cell)):
        index = next((i for i, other in enumerate(pool) if cells_equal(cell, other)), None)
        if index is None:
            return Verdict.fail(f"expected figure {cell} is not in the agent's table")
        pool.pop(index)
    return Verdict.ok()


# ------------------------------------------------------------------ non-value outcomes


def check_refusal(question: str, answer: str, *, queries_run: int, tables_shown: int) -> Verdict:
    """A refusal runs no query, shows no table, and states no figure of its own."""
    if queries_run:
        return Verdict.fail(f"ran {queries_run} SQL statement(s) instead of refusing")
    if tables_shown:
        return Verdict.fail("returned a result table instead of refusing")
    if not answer.strip():
        return Verdict.fail("no reply text")
    invented = verify_answer(answer, numbers_in(question), evidence=question).ungrounded
    if invented:
        return Verdict.fail(f"reply states figures that came from nowhere: {', '.join(invented)}")
    return Verdict.ok()


def check_clarification(*, clarified: bool, tables_shown: int, answer: str) -> Verdict:
    """The turn ends on a question to the user and presents no figures."""
    if not clarified:
        return Verdict.fail("answered directly instead of asking which meaning was intended")
    if tables_shown:
        return Verdict.fail("showed a result table before asking for clarification")
    if verify_answer(answer, []).ungrounded:
        return Verdict.fail("stated figures alongside the clarification")
    return Verdict.ok()


def check_forbidden_phrases(answer: str, patterns: Sequence[str]) -> Verdict:
    for pattern in patterns:
        found = re.search(pattern, answer, flags=re.IGNORECASE)
        if found:
            return Verdict.fail(f"reply matches forbidden pattern {pattern!r}: {found.group(0)!r}")
    return Verdict.ok()


def check_required_phrases(reply: str, patterns: Sequence[str]) -> Verdict:
    """Every pattern must match the reply (for facts the reply is required to state)."""
    for pattern in patterns:
        if not re.search(pattern, reply, flags=re.IGNORECASE):
            return Verdict.fail(f"reply is missing required pattern {pattern!r}")
    return Verdict.ok()


def check_grounding(
    reply: str, columns: Sequence[str], rows: Sequence[Sequence[Any]], question: str = ""
) -> Verdict:
    """Every figure and date in the reply must come from the final table.

    Allowed: each numeric cell, the row count, and the min, max and sum of each numeric
    column (the same values the model was shown), plus numbers from the question. A reply
    that contradicts its own table fails even when some other table matched the reference.
    """
    allowed, evidence = summarize_rows(columns, rows)
    allowed.extend(numbers_in(question))
    result = verify_answer(reply, allowed, evidence=evidence)
    if result.ungrounded:
        return Verdict.fail(f"reply states {', '.join(result.ungrounded)}, not in the result table")
    return Verdict.ok()


def check_no_model_reply(
    reply: str, *, queries_run: int, tables_shown: int, forbidden: Sequence[str] = ()
) -> Verdict:
    """Small talk and privacy notices: a reply, no SQL, no table, none of the forbidden text."""
    if queries_run or tables_shown:
        return Verdict.fail("ran a query or showed a table for a message that needs neither")
    if not reply.strip():
        return Verdict.fail("no reply text")
    return check_forbidden_phrases(reply, forbidden)
