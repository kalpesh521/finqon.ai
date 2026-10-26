"""Ambiguity gate: decide, without a model, whether to ask before answering.

This is the "is the query ambiguous?" decision of the agent pipeline. It runs after a cache
miss and before any model call, so the two cases below cost no tokens and cannot be
forgotten by the model:

* A quarter is named without saying financial or calendar year. The two never agree here
  (the financial year starts in April), so any default is wrong half the time.
* A follow-up fragment ("and last month?") arrives with nothing to follow up on.

Everything else stays with the model, which can still call ``ask_clarification`` under the
prompt rules. The gate is deliberately narrow: asking when a sensible default exists makes
the product worse, so each rule fires only when the answer would otherwise be a guess.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

# Months covered by each quarter, indexed by quarter number - 1.
_FY_MONTHS = ("Apr-Jun", "Jul-Sep", "Oct-Dec", "Jan-Mar")
_CALENDAR_MONTHS = ("Jan-Mar", "Apr-Jun", "Jul-Sep", "Oct-Dec")
_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3}
_ORDINALS |= {"fourth": 4, "4th": 4}

_QUARTER = re.compile(
    r"\bq\s?([1-4])\b|\b(first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter\b|"
    r"\bquarter\s+([1-4])\b",
    re.IGNORECASE,
)
# Any wording that settles which kind of year is meant.
_YEAR_KIND = re.compile(
    r"\b(?:financial|fiscal|calendar|fy|cy)\b|\bfy\s?\d|\bf\.y\b|\bc\.y\b", re.IGNORECASE
)

_DANGLING_START = re.compile(r"^\s*(?:and|also|what about|how about|same for|now)\b", re.IGNORECASE)
_MAX_DANGLING_WORDS = 7
_WORD = re.compile(r"[a-z']+")
# Words that give a fragment a subject of its own, so it is a question and not a dangling
# follow-up: "what about my dining spend?" can be answered with no earlier turn.
_SUBJECT = re.compile(
    r"\b(spend|spent|spending|expense|expenses|transaction|transactions|merchant|merchants|"
    r"categor\w*|salary|income|earn\w*|emi|paid|pay|payment|payments|balance|total|average|"
    r"rent|bill|bills|refund|refunds|fee|fees|cash|statement|account|upload\w*|file)\b",
    re.IGNORECASE,
)

DANGLING_OPTIONS = ("Total spending", "Spending by category", "Top merchants")


@dataclass(frozen=True, slots=True)
class Clarification:
    question: str
    options: tuple[str, ...]
    reason: str  # stable label for logs and metrics


class _Turn(Protocol):
    @property
    def question(self) -> str: ...

    @property
    def answer(self) -> str: ...


def check_ambiguity(
    question: str,
    *,
    history: Sequence[_Turn] = (),
    previous_sql: str | None = None,
    category_names: Iterable[str] = (),
) -> Clarification | None:
    """Return the question to ask, or ``None`` when the agent should just answer."""
    return _ambiguous_quarter(question, history) or _dangling_follow_up(
        question, history, previous_sql, tuple(category_names)
    )


def _ambiguous_quarter(question: str, history: Sequence[_Turn]) -> Clarification | None:
    match = _QUARTER.search(question)
    if match is None or _YEAR_KIND.search(question):
        return None
    # A quarter chosen earlier in this conversation carries over to "and Q4?".
    if any(_YEAR_KIND.search(f"{turn.question} {turn.answer}") for turn in history):
        return None
    number = _quarter_number(match)
    return Clarification(
        question=f"Which Q{number} do you mean?",
        options=(
            f"Financial-year Q{number} ({_FY_MONTHS[number - 1]})",
            f"Calendar Q{number} ({_CALENDAR_MONTHS[number - 1]})",
        ),
        reason="quarter_year_kind",
    )


def _quarter_number(match: re.Match[str]) -> int:
    digit = match.group(1) or match.group(3)
    return int(digit) if digit else _ORDINALS[match.group(2).lower()]


def _dangling_follow_up(
    question: str,
    history: Sequence[_Turn],
    previous_sql: str | None,
    category_names: tuple[str, ...],
) -> Clarification | None:
    if history or previous_sql or not _DANGLING_START.match(question):
        return None
    if len(_WORD.findall(question.lower())) > _MAX_DANGLING_WORDS or _SUBJECT.search(question):
        return None
    lowered = question.lower()
    if any(name.lower() in lowered for name in category_names):
        return None
    return Clarification(
        question="I don't have an earlier question to build on. What would you like to see?",
        options=DANGLING_OPTIONS,
        reason="dangling_follow_up",
    )
