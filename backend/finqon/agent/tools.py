"""Tool definitions the model may call, and validated argument models."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

RUN_SQL = "run_sql_query"
SEARCH_MERCHANTS = "search_merchants"
SEARCH_TRANSACTIONS = "search_transactions"
ASK_CLARIFICATION = "ask_clarification"

ChartHint = Literal["bar", "pie", "line", "none"]
MAX_CLARIFICATION_OPTIONS = 3


class RunSqlArgs(BaseModel):
    sql: Annotated[str, Field(min_length=1, max_length=8000)]
    purpose: Annotated[str, Field(min_length=1, max_length=300)]
    chart_hint: ChartHint = "none"


class SearchMerchantsArgs(BaseModel):
    query: Annotated[str, Field(min_length=1, max_length=100)]


class SearchTransactionsArgs(BaseModel):
    query: Annotated[str, Field(min_length=1, max_length=200)]
    date_from: str | None = None
    date_to: str | None = None
    min_amount: Annotated[float, Field(ge=0)] | None = None
    max_amount: Annotated[float, Field(ge=0)] | None = None
    direction: Literal["debit", "credit"] | None = None
    limit: Annotated[int, Field(ge=1, le=30)] = 15


class AskClarificationArgs(BaseModel):
    question: Annotated[str, Field(min_length=1, max_length=300)]
    options: Annotated[list[str], Field(default_factory=list)]

    def clipped_options(self) -> list[str]:
        cleaned = [o.strip()[:80] for o in self.options if o.strip()]
        return cleaned[:MAX_CLARIFICATION_OPTIONS]


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": RUN_SQL,
            "description": (
                "Run ONE read-only PostgreSQL SELECT against reporting.transactions, "
                "reporting.spend or reporting.monthly_category_spend (always schema-qualified). "
                "Returns column names, row count, the first 20 rows, and min/max/sum of numeric "
                "columns. Do all arithmetic in SQL."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "A single SELECT statement."},
                    "purpose": {
                        "type": "string",
                        "description": "One short sentence: what this query shows the user.",
                    },
                    "chart_hint": {
                        "type": "string",
                        "enum": ["bar", "pie", "line", "none"],
                        "description": (
                            "bar: compare categories or merchants. pie: share of a total "
                            "(at most 8 slices). line: trend over time. none: single values "
                            "or plain lists."
                        ),
                    },
                },
                "required": ["sql", "purpose", "chart_hint"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": SEARCH_MERCHANTS,
            "description": (
                "Fuzzy-match a merchant name the user typed against merchants they have "
                "transacted with. Use it before filtering by merchant_name when the spelling "
                "is uncertain."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": SEARCH_TRANSACTIONS,
            "description": (
                "Semantic search over transaction descriptions: finds transactions by meaning "
                "('coffee shops', 'ride hailing', 'electricity') even when the exact merchant "
                "name or category is unknown or mis-spelled. Returns matching rows with id, "
                "date, description, merchant, category and amount. Use it to FIND transactions "
                "by what they were for; use run_sql_query for totals, rankings and trends. "
                "Optional filters are applied before ranking."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What the user is looking for."},
                    "date_from": {"type": "string", "description": "YYYY-MM-DD, inclusive."},
                    "date_to": {"type": "string", "description": "YYYY-MM-DD, inclusive."},
                    "min_amount": {"type": "number"},
                    "max_amount": {"type": "number"},
                    "direction": {"type": "string", "enum": ["debit", "credit"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 30},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": ASK_CLARIFICATION,
            "description": (
                "Ask the user ONE question when the request is ambiguous in a way that changes "
                "the answer. Ends the turn. Offer at most three short options."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": MAX_CLARIFICATION_OPTIONS,
                    },
                },
                "required": ["question"],
            },
        },
    },
]
