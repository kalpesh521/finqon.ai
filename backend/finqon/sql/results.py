"""Result types for guarded queries: what the database returned, and what the model may see.

Pure data and pure functions, no database access, so the API schemas and the agent can use
them without importing the executor.
"""

from __future__ import annotations

import datetime as dt
import decimal
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

Cell = str | int | float | bool | None

_NUMERIC_TYPES = {"int2", "int4", "int8", "numeric", "float4", "float8"}
_DATE_TYPES = {"date", "timestamp", "timestamptz"}
FIRST_ROWS_FOR_MODEL = 20


class QueryExecutionError(RuntimeError):
    """The database rejected or failed the query. Message is safe for the model."""


class Column(BaseModel):
    name: str
    type: str  # "number" | "string" | "date" | "boolean" | "other"


@dataclass(frozen=True, slots=True)
class QueryResult:
    columns: list[Column]
    rows: list[list[Cell]]  # JSON-safe values for the API
    raw_rows: list[tuple[Any, ...]] = field(repr=False)  # exact values for statistics
    truncated: bool
    sql: str
    elapsed_ms: int

    @property
    def row_count(self) -> int:
        return len(self.rows)


def column_kind(type_name: str) -> str:
    if type_name in _NUMERIC_TYPES:
        return "number"
    if type_name in _DATE_TYPES:
        return "date"
    if type_name == "bool":
        return "boolean"
    if type_name in {"text", "varchar", "bpchar", "name", "uuid"}:
        return "string"
    return "other"


def to_json_cell(value: Any) -> Cell:
    """Convert a database value into a JSON-safe scalar."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, float):
        return value
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


@dataclass(frozen=True, slots=True)
class NumericStats:
    minimum: decimal.Decimal
    maximum: decimal.Decimal
    total: decimal.Decimal


@dataclass(frozen=True, slots=True)
class ResultSummary:
    """What the model is allowed to see about a result set."""

    columns: list[Column]
    row_count: int
    truncated: bool
    first_rows: list[list[Cell]]
    numeric_stats: dict[str, NumericStats]

    def to_model_payload(self) -> dict[str, Any]:
        return {
            "columns": [c.name for c in self.columns],
            "row_count": self.row_count,
            "truncated": self.truncated,
            "first_rows": self.first_rows,
            "numeric_stats": {
                name: {
                    "min": str(stats.minimum),
                    "max": str(stats.maximum),
                    "sum": str(stats.total),
                }
                for name, stats in self.numeric_stats.items()
            },
        }

    def grounding_values(self) -> list[decimal.Decimal]:
        """Every number the model has been shown for this result."""
        values: list[decimal.Decimal] = [decimal.Decimal(self.row_count)]
        for row in self.first_rows:
            for cell in row:
                if isinstance(cell, bool) or not isinstance(cell, int | float):
                    continue
                values.append(decimal.Decimal(str(cell)))
        for stats in self.numeric_stats.values():
            values.extend((stats.minimum, stats.maximum, stats.total))
        return values


def summarize(result: QueryResult) -> ResultSummary:
    stats: dict[str, NumericStats] = {}
    for index, column in enumerate(result.columns):
        if column.type != "number":
            continue
        numbers = [
            decimal.Decimal(str(row[index]))
            for row in result.raw_rows
            if row[index] is not None and not isinstance(row[index], bool)
        ]
        if numbers:
            total = sum(numbers, decimal.Decimal(0))
            stats[column.name] = NumericStats(min(numbers), max(numbers), total)
    return ResultSummary(
        columns=result.columns,
        row_count=result.row_count,
        truncated=result.truncated,
        first_rows=result.rows[:FIRST_ROWS_FOR_MODEL],
        numeric_stats=stats,
    )


def summarize_rows(
    columns: Sequence[str], rows: Sequence[Sequence[Any]]
) -> tuple[list[decimal.Decimal], str]:
    """Numbers a reply may quote from a table, and the table text that dates must appear in.

    Used by the evaluation: the same values ``ResultSummary`` shows the model, computed from
    the rows the user saw.
    """
    allowed: list[decimal.Decimal] = [decimal.Decimal(len(rows))]
    for index in range(len(columns)):
        numbers = [
            decimal.Decimal(str(row[index]))
            for row in rows
            if index < len(row)
            and isinstance(row[index], int | float | decimal.Decimal)
            and not isinstance(row[index], bool)
        ]
        if numbers:
            allowed.extend(numbers)
            allowed.extend((min(numbers), max(numbers), sum(numbers, decimal.Decimal(0))))
    evidence = " ".join(str(cell) for row in rows for cell in row)
    return allowed, evidence


__all__ = [
    "FIRST_ROWS_FOR_MODEL",
    "Cell",
    "Column",
    "NumericStats",
    "QueryExecutionError",
    "QueryResult",
    "ResultSummary",
    "column_kind",
    "summarize",
    "summarize_rows",
    "to_json_cell",
]
