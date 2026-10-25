"""The evaluation case file: typed, validated, and checked for coverage."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from finqon.evaluation.scoring import CompareMode

Tag = Literal[
    "aggregation",
    "date",
    "ranking",
    "comparison",
    "follow-up",
    "ambiguous",
    "unanswerable",
    "injection",
    "count",
    "share",
    "recurring",
    "empty",
    "smalltalk",
    "privacy",
]
Expect = Literal["values", "clarification", "refusal", "smalltalk", "privacy"]

REQUIRED_TAGS: frozenset[str] = frozenset(Tag.__args__)  # type: ignore[attr-defined]
MIN_CASES = 20

# Questions the brief says must be represented, matched case-insensitively.
REQUIRED_QUESTIONS: tuple[str, ...] = (
    "How much did I spend on eating out last month?",
    "Give me a category-wise spending breakdown for the last 3 months.",
    "What are my top 5 merchants by spend?",
    "Which month did I spend the most, and why?",
    "Show me all transactions above ₹5,000 in the last 30 days.",
    "Compare my grocery spend this quarter vs last quarter.",
    "What about the month before?",
    "Break that down by merchant",
)


class PriorTurn(BaseModel):
    """The turn a follow-up continues. Stored as real history before the question is asked."""

    model_config = ConfigDict(extra="forbid")

    question: str
    sql: str
    summary: str
    answer: str


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    tag: Tag
    question: str
    expect: Expect
    reference_sql: str | None = None
    compare: CompareMode = "rows"
    ordered: bool = False
    prior: PriorTurn | None = None
    must_not_match: list[str] = Field(default_factory=list)
    must_match: list[str] = Field(default_factory=list)  # every regex must match the reply
    rubric: list[str] = Field(default_factory=list)  # judged by the LLM judge, only these cases
    note: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.expect == "values" and not (self.reference_sql or "").strip():
            raise ValueError(f"{self.id}: expect=values needs reference_sql")
        if self.expect != "values" and self.reference_sql:
            raise ValueError(f"{self.id}: only expect=values cases carry reference_sql")
        for pattern in (*self.must_match, *self.must_not_match):
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"{self.id}: bad regex {pattern!r}: {exc}") from exc
        if self.tag == "follow-up" and self.prior is None:
            raise ValueError(f"{self.id}: follow-up cases need a prior turn")
        if self.prior is not None and self.tag != "follow-up":
            raise ValueError(f"{self.id}: only follow-up cases carry a prior turn")
        return self


def load_cases(path: Path) -> list[EvalCase]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path} must contain a list of cases")
    cases = [EvalCase.model_validate(item) for item in raw]
    problems = coverage_problems(cases)
    if problems:
        raise ValueError("invalid evaluation set: " + "; ".join(problems))
    return cases


def coverage_problems(cases: Sequence[EvalCase]) -> list[str]:
    """Everything the evaluation spec requires of the set as a whole."""
    problems: list[str] = []
    ids = [case.id for case in cases]
    problems += [f"duplicate id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    if len(cases) < MIN_CASES:
        problems.append(f"{len(cases)} cases, need at least {MIN_CASES}")
    missing_tags = REQUIRED_TAGS - {case.tag for case in cases}
    problems += [f"no case tagged {tag}" for tag in sorted(missing_tags)]
    asked = {case.question.strip().casefold() for case in cases}
    problems += [
        f"missing required question: {question}"
        for question in REQUIRED_QUESTIONS
        if question.casefold() not in asked
    ]
    if not any(case.expect == "clarification" for case in cases):
        problems.append("no clarification case")
    if not any(case.expect == "refusal" for case in cases):
        problems.append("no refusal case")
    return problems
