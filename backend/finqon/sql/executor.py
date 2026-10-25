"""Read-only execution of guarded SQL on the ``finqon_reader`` pool.

Every query runs in its own ``READ ONLY`` transaction with a statement timeout, the
Asia/Kolkata time zone, and ``finqon.user_id`` set from the verified JWT. Row-level
security, not the model, decides which rows are visible.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import asyncpg

from finqon.db.urls import parse_database_url
from finqon.sql.guard import GuardedSql, SqlGuardError, validate_sql
from finqon.sql.results import (
    Column,
    QueryExecutionError,
    QueryResult,
    column_kind,
    to_json_cell,
)

logger = logging.getLogger(__name__)


class QueryExecutor:
    """Runs guarded SQL for one user at a time on a dedicated reader pool."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        timezone: str,
        statement_timeout: str = "5s",
        max_rows: int = 500,
    ) -> None:
        self._pool = pool
        self._timezone = timezone
        self._statement_timeout = statement_timeout
        self._max_rows = max_rows

    @classmethod
    async def create(
        cls,
        database_url: str,
        *,
        timezone: str,
        statement_timeout: str = "5s",
        max_rows: int = 500,
        max_size: int = 5,
    ) -> QueryExecutor:
        parsed = parse_database_url(database_url)
        pool = await asyncpg.create_pool(
            dsn=parsed.asyncpg_dsn(),
            ssl=True if parsed.ssl else None,
            min_size=0,  # connect lazily: Neon computes may be suspended
            max_size=max_size,
            max_inactive_connection_lifetime=60,
            command_timeout=15,
        )
        return cls(pool, timezone=timezone, statement_timeout=statement_timeout, max_rows=max_rows)

    async def close(self) -> None:
        await self._pool.close()

    async def ping(self) -> None:
        """Cheap connectivity check used by the readiness probe."""
        async with self._pool.acquire() as conn:
            await conn.execute("SELECT 1")

    def guard(self, sql: str) -> GuardedSql:
        return validate_sql(sql, max_limit=self._max_rows)

    async def execute(self, guarded: GuardedSql, user_id: uuid.UUID) -> QueryResult:
        started = time.perf_counter()
        try:
            async with self._pool.acquire() as conn, conn.transaction(readonly=True):
                await self._prepare_session(conn, user_id)
                statement = await conn.prepare(guarded.sql)
                columns = [
                    Column(name=attr.name, type=column_kind(attr.type.name))
                    for attr in statement.get_attributes()
                ]
                records = await statement.fetch()
        except asyncpg.PostgresError as exc:
            # Message goes back to the model so it can repair the query.
            raise QueryExecutionError(_database_error_message(exc)) from exc
        except TimeoutError as exc:
            raise QueryExecutionError("The query timed out. Make it cheaper.") from exc

        raw_rows = [tuple(record.values()) for record in records]
        return QueryResult(
            columns=columns,
            rows=[[to_json_cell(v) for v in row] for row in raw_rows],
            raw_rows=raw_rows,
            truncated=guarded.limit_rewritten and len(raw_rows) >= guarded.limit,
            sql=guarded.sql,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    async def run(self, sql: str, user_id: uuid.UUID) -> QueryResult:
        """Guard then execute. Raises ``SqlGuardError`` or ``QueryExecutionError``."""
        return await self.execute(self.guard(sql), user_id)

    async def _prepare_session(self, conn: asyncpg.Connection, user_id: uuid.UUID) -> None:
        # SET LOCAL does not take bind parameters; the timeout is a trusted setting.
        await conn.execute(
            f"SET LOCAL statement_timeout = '{_safe_interval(self._statement_timeout)}'"
        )
        await conn.execute("SELECT set_config('TimeZone', $1, true)", self._timezone)
        await conn.execute("SELECT set_config('finqon.user_id', $1, true)", str(user_id))

    async def search_merchants(
        self, query: str, user_id: uuid.UUID, limit: int = 5
    ) -> list[dict[str, Any]]:
        """pg_trgm similarity over merchants the user has actually transacted with."""
        sql = """
            SELECT m.name AS merchant_name,
                   round(similarity(m.name, $1)::numeric, 3) AS score
            FROM ledger.merchants m
            WHERE (m.name % $1 OR m.name ILIKE '%' || $1 || '%')
              AND EXISTS (SELECT 1 FROM reporting.transactions t WHERE t.merchant_name = m.name)
            ORDER BY similarity(m.name, $1) DESC, m.name
            LIMIT $2
        """
        try:
            async with self._pool.acquire() as conn, conn.transaction(readonly=True):
                await self._prepare_session(conn, user_id)
                records = await conn.fetch(sql, query, limit)
        except asyncpg.PostgresError as exc:
            raise QueryExecutionError(_database_error_message(exc)) from exc
        return [{"merchant_name": r["merchant_name"], "score": float(r["score"])} for r in records]

    async def search_transactions(
        self,
        embedding: str | None,
        text_query: str,
        user_id: uuid.UUID,
        *,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
        min_amount: float | None = None,
        max_amount: float | None = None,
        direction: str | None = None,
        limit: int = 15,
    ) -> QueryResult:
        """Hybrid search: pgvector cosine and pg_trgm / substring, fused with RRF.

        Filters (date, amount, direction) apply before ranking inside both branches. Row-level
        security limits both to the caller. ``embedding`` is a pgvector text literal.
        """
        branch_filter = """
            AND ($3::date IS NULL OR t.txn_date >= $3)
            AND ($4::date IS NULL OR t.txn_date <= $4)
            AND ($5::numeric IS NULL OR t.amount >= $5)
            AND ($6::numeric IS NULL OR t.amount <= $6)
            AND ($7::text IS NULL OR t.direction = $7)
        """
        sql = f"""
            WITH vec AS (
                SELECT t.id, row_number() OVER (ORDER BY t.embedding <=> $1::vector) AS rnk
                FROM ledger.transactions t
                WHERE t.embedding IS NOT NULL AND $1::vector IS NOT NULL {branch_filter}
                ORDER BY t.embedding <=> $1::vector
                LIMIT 40
            ), lex AS (
                SELECT t.id,
                       row_number() OVER (ORDER BY similarity(t.raw_description, $2) DESC) AS rnk
                FROM ledger.transactions t
                WHERE (t.raw_description % $2
                       OR t.raw_description ILIKE '%' || $2 || '%') {branch_filter}
                ORDER BY similarity(t.raw_description, $2) DESC
                LIMIT 40
            ), fused AS (
                SELECT id, SUM(1.0 / (60 + rnk)) AS score
                FROM (SELECT * FROM vec UNION ALL SELECT * FROM lex) ranked
                GROUP BY id
            )
            SELECT r.id, r.txn_date, r.raw_description AS description, r.merchant_name,
                   r.category_name, r.amount, r.direction
            FROM fused f JOIN reporting.transactions r ON r.id = f.id
            ORDER BY f.score DESC, r.txn_date DESC, r.id
            LIMIT $8
        """
        started = time.perf_counter()
        try:
            async with self._pool.acquire() as conn, conn.transaction(readonly=True):
                await self._prepare_session(conn, user_id)
                statement = await conn.prepare(sql)
                columns = [
                    Column(name=attr.name, type=column_kind(attr.type.name))
                    for attr in statement.get_attributes()
                ]
                records = await statement.fetch(
                    embedding, text_query, date_from, date_to, min_amount, max_amount,
                    direction, limit,
                )  # fmt: skip
        except asyncpg.PostgresError as exc:
            raise QueryExecutionError(_database_error_message(exc)) from exc
        raw_rows = [tuple(record.values()) for record in records]
        return QueryResult(
            columns=columns,
            rows=[[to_json_cell(v) for v in row] for row in raw_rows],
            raw_rows=raw_rows,
            truncated=False,
            sql="-- semantic search",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    async def fetch_catalog_rows(
        self, sql: str, args: Sequence[Any] = ()
    ) -> list[Mapping[str, Any]]:
        """Metadata queries that are not user data (comments, category names)."""
        async with self._pool.acquire() as conn, conn.transaction(readonly=True):
            return [dict(r) for r in await conn.fetch(sql, *args)]


def _database_error_message(exc: asyncpg.PostgresError) -> str:
    message = (exc.message or type(exc).__name__).strip()
    if isinstance(exc, asyncpg.QueryCanceledError):
        return "The query exceeded the time limit."
    if isinstance(exc, asyncpg.InsufficientPrivilegeError):
        return (
            "Permission denied. Query only reporting.transactions, reporting.spend, "
            "or reporting.monthly_category_spend."
        )
    return message[:300]


def _safe_interval(value: str) -> str:
    """Allow only values like ``5s`` or ``500ms`` in the interpolated timeout."""
    if not re.fullmatch(r"\d{1,6}(ms|s|min)?", value):
        raise ValueError(f"Invalid statement timeout: {value!r}")
    return value


__all__ = [
    "QueryExecutionError",
    "QueryExecutor",
    "SqlGuardError",
]
