"""Fixed read of how much of the statement is loaded. Not a model-written query."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter

from finqon.api.dependencies import CurrentUserDep, ResourcesDep
from finqon.core.errors import AppError
from finqon.schemas.statement import StatementSummary
from finqon.sql.results import Cell, QueryExecutionError

router = APIRouter(tags=["statement"])

# Kept here so the guard test and the route cannot drift. The guard appends LIMIT.
STATEMENT_SQL = """
SELECT
    COUNT(*) AS transaction_count,
    MIN(txn_date) AS covered_from,
    MAX(txn_date) AS covered_to
FROM reporting.transactions
"""


@router.get("/statement", response_model=StatementSummary)
async def statement_summary(user: CurrentUserDep, resources: ResourcesDep) -> StatementSummary:
    try:
        result = await resources.executor.run(STATEMENT_SQL, user.id)
    except QueryExecutionError as exc:
        raise AppError("The statement summary is unavailable.") from exc
    row = result.rows[0] if result.rows else None
    if row is None or len(row) < 3:
        return StatementSummary(transaction_count=0, covered_from=None, covered_to=None)
    return StatementSummary(
        transaction_count=_count(row[0]),
        covered_from=_date(row[1]),
        covered_to=_date(row[2]),
    )


def _count(value: Cell) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return int(value)


def _date(value: Cell) -> dt.date | None:
    # The executor serialises dates to ISO strings before they reach this route.
    if not isinstance(value, str) or not value:
        return None
    return dt.date.fromisoformat(value[:10])
