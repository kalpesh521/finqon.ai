"""Context retriever: choose the schema slice and the term mapping for one question.

This is the "context retriever" stage of the agent pipeline. Before the SQL model sees the
question it receives:

* the most relevant views, best first, limited to ``top_k`` (so a large catalog never floods
  the prompt), and
* a mapping from the user's own words to schema entities ("eating out" -> category_name =
  'Dining'), the same job as mapping "Apple" to a company-name column.

It is deterministic and has no I/O: the same question and catalog always give the same
context, it adds no latency, and it is testable without a model. Every hint is checked
against the live catalog before it is emitted, so a renamed category or column silently
drops its hint instead of misleading the model.

Extension point: ``retrieve_context`` is the only function the agent calls. A vector index
over view and column documentation can replace the ranking in ``_rank_views`` when the
catalog grows beyond what keyword scoring separates well; the output type does not change.
Transaction-level semantic search is separate and stays a model tool
(``search_transactions``).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from finqon.agent.catalog import SchemaCatalog, ViewInfo

DEFAULT_TOP_VIEWS = 5
_MAX_MAPPINGS = 8
_WORD = re.compile(r"[a-z0-9]+")

# What each reporting view is for, in the words users use. Used to rank views; a view that is
# not listed here is still ranked by its own name, comments and column names.
_VIEW_INTENT: dict[str, tuple[str, ...]] = {
    "spend": (
        "spend",
        "spent",
        "spending",
        "expense",
        "cost",
        "total",
        "much",
        "breakdown",
        "category",
        "merchant",
    ),
    "transactions": (
        "transaction",
        "list",
        "show",
        "salary",
        "income",
        "earn",
        "credit",
        "debit",
        "transfer",
        "refund",
        "largest",
        "biggest",
        "latest",
        "recent",
        "statement",
        "upload",
    ),
    "monthly_category_spend": (
        "month",
        "monthly",
        "trend",
        "compare",
        "versus",
        "highest",
        "lowest",
        "ranking",
        "rank",
        "over",
        "time",
        "each",
    ),
}
_INTENT_WEIGHT = 3.0

# User wording -> category name. Applied only when the category exists in the catalog.
_CATEGORY_TERMS: dict[str, tuple[str, ...]] = {
    "Dining": (
        "dining",
        "eating out",
        "restaurant",
        "restaurants",
        "food delivery",
        "cafe",
        "coffee",
        "lunch",
        "dinner",
        "takeaway",
    ),
    "Groceries": ("groceries", "grocery", "supermarket", "vegetables", "kirana"),
    "Transport": ("transport", "cab", "cabs", "taxi", "commute", "metro", "ride hailing"),
    "Fuel": ("fuel", "petrol", "diesel", "gas station"),
    "Utilities": ("utilities", "electricity", "broadband", "internet", "wifi", "water bill"),
    "Housing": ("housing", "rent", "mortgage", "maintenance"),
    "Healthcare": ("healthcare", "doctor", "hospital", "medicine", "pharmacy", "medical"),
    "Education": ("education", "tuition", "school", "college", "course"),
    "Entertainment": ("entertainment", "movie", "movies", "streaming", "netflix", "concert"),
    "Travel": ("travel", "flight", "flights", "hotel", "hotels", "vacation", "holiday"),
    "Shopping": ("shopping", "clothes", "clothing", "online shopping"),
    "Fees & Charges": ("bank charges", "bank fees", "penalty", "penalties", "late fee"),
    "Cash Withdrawal": ("atm", "cash withdrawal", "cash withdrawals"),
    "Salary": ("salary", "paycheck", "payday"),
    "Insurance": ("insurance", "premium", "premiums"),
    "Personal Care": ("personal care", "salon", "spa", "grooming"),
    "Kids": ("kids", "children", "child"),
    "Family Support": ("family support", "sent to family", "parents"),
}

# User wording -> column. Applied only when some retrieved view has the column.
_COLUMN_TERMS: dict[str, tuple[str, ...]] = {
    "merchant_name": ("merchant", "merchants", "payee", "shop", "store", "vendor"),
    "category_name": ("category", "categories"),
    "txn_date": ("date", "dates"),
    "fy": ("financial year", "fiscal year", "fy"),
    "account_name": ("account", "file", "statement", "upload"),
    "raw_description": ("description", "narration", "remarks", "reference"),
    "spend_amount": ("spent", "spend", "spending", "expenditure"),
}


@dataclass(frozen=True, slots=True)
class TermMapping:
    phrase: str
    target: str


@dataclass(frozen=True, slots=True)
class RetrievedContext:
    views: tuple[ViewInfo, ...]  # most relevant first, at most ``top_k``
    mappings: tuple[TermMapping, ...] = ()

    def render(self) -> str:
        """The prompt section. Empty when there is nothing question-specific to say."""
        lines: list[str] = []
        if len(self.views) > 1:
            names = ", ".join(v.name for v in self.views)
            lines.append(f"Views, most relevant first: {names}.")
        if self.mappings:
            lines.append("The user's words, mapped to the schema:")
            lines.extend(f'- "{m.phrase}" -> {m.target}' for m in self.mappings)
        if not lines:
            return ""
        lines.append("These are hints from the wording. The schema above is authoritative.")
        return "## Retrieved context for this question\n" + "\n".join(lines)


def retrieve_context(
    question: str, catalog: SchemaCatalog, *, top_k: int = DEFAULT_TOP_VIEWS
) -> RetrievedContext:
    """Rank the views for the question and map its wording to categories and columns."""
    tokens = {_stem(w) for w in _WORD.findall(question.lower())}
    views = _rank_views(tokens, catalog.views)[: max(1, top_k)]
    lowered = f" {' '.join(_WORD.findall(question.lower()))} "
    mappings = (*_category_mappings(lowered, catalog), *_column_mappings(lowered, views))
    return RetrievedContext(views=views, mappings=mappings[:_MAX_MAPPINGS])


# -- ranking ------------------------------------------------------------------------


def _stem(word: str) -> str:
    for suffix in ("ing", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _view_tokens(view: ViewInfo) -> set[str]:
    words = _WORD.findall(view.name.split(".")[-1].replace("_", " ").lower())
    words += _WORD.findall(view.comment.lower())
    for column in view.columns:
        words += _WORD.findall(column.name.replace("_", " ").lower())
        words += _WORD.findall(column.comment.lower())
    return {_stem(w) for w in words if len(w) > 2}


def _rank_views(tokens: set[str], views: Sequence[ViewInfo]) -> tuple[ViewInfo, ...]:
    def score(view: ViewInfo) -> float:
        short = view.name.split(".")[-1]
        intent = {_stem(w) for w in _VIEW_INTENT.get(short, ())}
        return len(tokens & _view_tokens(view)) + _INTENT_WEIGHT * bool(tokens & intent)

    # ``sorted`` is stable, so equal scores keep the catalog order.
    return tuple(sorted(views, key=score, reverse=True))


# -- term mapping -------------------------------------------------------------------


def _mentions(lowered: str, phrase: str) -> bool:
    return f" {phrase} " in lowered


def _category_mappings(lowered: str, catalog: SchemaCatalog) -> list[TermMapping]:
    kinds = {c.name.lower(): c for c in catalog.categories}
    found: list[TermMapping] = []
    for name, phrases in _CATEGORY_TERMS.items():
        category = kinds.get(name.lower())
        if category is None:
            continue
        phrase = next((p for p in phrases if _mentions(lowered, p)), None)
        if phrase is None:
            continue
        target = f"category_name = '{category.name}'"
        if category.kind != "expense":
            target += f" ({category.kind}: not in reporting.spend, use reporting.transactions)"
        found.append(TermMapping(phrase, target))
    return found


def _column_mappings(lowered: str, views: Sequence[ViewInfo]) -> list[TermMapping]:
    found: list[TermMapping] = []
    for column, phrases in _COLUMN_TERMS.items():
        owner = next((v for v in views if any(c.name == column for c in v.columns)), None)
        phrase = next((p for p in phrases if _mentions(lowered, p)), None)
        if owner is not None and phrase is not None:
            found.append(TermMapping(phrase, f"{owner.name}.{column}"))
    return found
