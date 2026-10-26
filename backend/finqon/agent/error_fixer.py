"""Error fixer: turn a database or guard error into a specific repair instruction.

This is the "error fixer / parser" stage of the agent pipeline. When generated SQL fails
(the diagram's example: the column was ``amount`` but the SQL used ``amt``), the model gets
the original error unchanged plus a ``hint`` that names the likely cause and the correct
column or view from the live catalog. A model repairs faster and more reliably from "use
spend_amount" than from ``column "amt" does not exist``, which saves model steps out of the
small repair budget.

It never edits SQL itself. The model rewrites the query and the guard validates the rewrite
as it would any other, so this stage cannot widen what may run. It reads only the error
text, the SQL the model wrote and the catalog; hints contain schema names, never row data.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from finqon.agent.catalog import SchemaCatalog, ViewInfo

_MAX_SUGGESTIONS = 3
_VIEW_REFERENCE = re.compile(r"\b([a-z_]+\.[a-z_]+)\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Diagnosis:
    kind: str  # stable label for logs and metrics; never contains user data
    hint: str


def diagnose(message: str, sql: str, catalog: SchemaCatalog | None) -> Diagnosis | None:
    """Explain one failure, or ``None`` when no rule recognises it."""
    text = message.strip()
    for pattern, build in _RULES:
        match = pattern.search(text)
        if match is not None:
            return build(match, sql, catalog)
    return None


# -- catalog helpers -----------------------------------------------------------------


def _views_in(sql: str, catalog: SchemaCatalog | None) -> list[ViewInfo]:
    """The views the SQL names; every view when it names none."""
    if catalog is None:
        return []
    named = {m.group(1).lower() for m in _VIEW_REFERENCE.finditer(sql)}
    used = [v for v in catalog.views if v.name.lower() in named]
    return used or list(catalog.views)


def _is_subsequence(short: str, long: str) -> bool:
    remaining = iter(long)
    return all(char in remaining for char in short)


def _closest(word: str, candidates: Iterable[str]) -> list[str]:
    """Best matches first: close spellings, then abbreviations such as ``amt`` -> ``amount``."""
    pool = list(dict.fromkeys(candidates))
    word = word.lower()
    close = difflib.get_close_matches(word, pool, n=_MAX_SUGGESTIONS, cutoff=0.6)
    abbreviations = [
        c for c in pool if c not in close and len(word) >= 3 and _is_subsequence(word, c)
    ]
    return (close + abbreviations)[:_MAX_SUGGESTIONS]


def _column_owners(views: Iterable[ViewInfo]) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for view in views:
        for column in view.columns:
            owners.setdefault(column.name, []).append(view.name)
    return owners


# -- rules ---------------------------------------------------------------------------

_Builder = Callable[[re.Match[str], str, "SchemaCatalog | None"], Diagnosis]


def _unknown_column(match: re.Match[str], sql: str, catalog: SchemaCatalog | None) -> Diagnosis:
    bad = match.group(1).split(".")[-1]
    views = _views_in(sql, catalog)
    owners = _column_owners(views)
    suggestions = _closest(bad, owners)
    if suggestions:
        shown = ", ".join(f"{c} ({owners[c][0]})" for c in suggestions)
        hint = f"There is no column '{bad}'. Closest columns in the schema: {shown}."
    elif views:
        listed = "; ".join(f"{v.name}: {', '.join(c.name for c in v.columns)}" for v in views)
        hint = f"There is no column '{bad}'. Columns available: {listed}."
    else:
        hint = f"There is no column '{bad}'. Use only the columns listed in the schema."
    return Diagnosis("unknown_column", hint)


def _unknown_relation(match: re.Match[str], sql: str, catalog: SchemaCatalog | None) -> Diagnosis:
    bad = match.group(1)
    names = [v.name for v in catalog.views] if catalog else []
    suggestions = _closest(bad, names)
    if suggestions:
        hint = f"There is no relation '{bad}'. Did you mean {', '.join(suggestions)}?"
    else:
        hint = f"There is no relation '{bad}'. Query only: {', '.join(names)}."
    return Diagnosis(
        "unknown_relation", hint + " Always schema-qualify, for example reporting.spend."
    )


def _static(kind: str, hint: str) -> _Builder:
    def build(_match: re.Match[str], _sql: str, _catalog: SchemaCatalog | None) -> Diagnosis:
        return Diagnosis(kind, hint)

    return build


def _syntax(match: re.Match[str], _sql: str, _catalog: SchemaCatalog | None) -> Diagnosis:
    near = match.group(1) if match.lastindex else ""
    where = f" near '{near}'" if near else ""
    return Diagnosis(
        "syntax",
        f"PostgreSQL could not parse the query{where}. Check commas, parentheses, quotes, "
        "and that no reserved word is used as an alias.",
    )


def _operator(match: re.Match[str], _sql: str, _catalog: SchemaCatalog | None) -> Diagnosis:
    return Diagnosis(
        "type_mismatch",
        f"The operands have incompatible types ({match.group(1)}). Cast one side, for example "
        "'2026-01-01'::date for dates or ::numeric for numbers.",
    )


_RULES: list[tuple[re.Pattern[str], _Builder]] = [
    (re.compile(r'column "?([\w.]+)"? does not exist', re.I), _unknown_column),
    (re.compile(r'(?:relation|table) "?([\w.]+)"? does not exist', re.I), _unknown_relation),
    (
        re.compile(r"permission denied", re.I),
        _static(
            "permission",
            "Only reporting.transactions, reporting.spend and reporting.monthly_category_spend "
            "can be read. Rewrite the query against one of them.",
        ),
    ),
    (
        re.compile(r"division by zero", re.I),
        _static(
            "division_by_zero", "Guard the divisor: x / NULLIF(y, 0), then COALESCE if needed."
        ),
    ),
    (
        re.compile(r"must appear in the GROUP BY clause", re.I),
        _static(
            "group_by",
            "Every selected column that is not aggregated must be in GROUP BY. Add it to GROUP "
            "BY, or wrap it in an aggregate such as MAX().",
        ),
    ),
    (
        re.compile(r"aggregate functions are not allowed in (?:WHERE|JOIN)", re.I),
        _static(
            "aggregate_in_where",
            "Filter on an aggregate with HAVING, or compute it in a subquery or CTE first.",
        ),
    ),
    (
        re.compile(r'column reference "?([\w.]+)"? is ambiguous', re.I),
        _static(
            "ambiguous_column", "Qualify the column with its table alias, for example s.txn_date."
        ),
    ),
    (
        re.compile(r'missing FROM-clause entry for table "?([\w.]+)"?', re.I),
        _static(
            "missing_alias", "Define the alias in FROM, or remove the qualifier from the column."
        ),
    ),
    (re.compile(r"operator does not exist: ([^\n]+)", re.I), _operator),
    (
        re.compile(
            r"invalid input syntax for type|out of range|invalid input value for enum", re.I
        ),
        _static(
            "bad_literal",
            "A literal has the wrong format. Write dates as 'YYYY-MM-DD' and numbers without "
            "currency symbols or commas, and use only the category and txn_type values listed.",
        ),
    ),
    (
        re.compile(r"function [\w.]+\([^)]*\) does not exist", re.I),
        _static(
            "unknown_function",
            "That function or argument type does not exist. Cast the arguments, or use "
            "date_trunc, extract, SUM, COUNT, AVG, MIN, MAX or COALESCE.",
        ),
    ),
    (
        re.compile(r"time limit|timed out|canceling statement", re.I),
        _static(
            "timeout",
            "The query was too slow. Filter by a date range, aggregate in SQL, and select "
            "only the columns needed.",
        ),
    ),
    (re.compile(r'syntax error at or near "([^"]*)"', re.I), _syntax),
    (re.compile(r"not valid PostgreSQL|syntax error", re.I), _syntax),
]
