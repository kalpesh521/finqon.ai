"""Schema description for the prompt, read from PostgreSQL ``COMMENT``s.

There is no second hand-written schema description: the views and columns document
themselves in the migration, and the prompt is built from those comments.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

TXN_TYPES = ("purchase", "refund", "salary", "transfer", "emi", "subscription", "atm", "fee")

_COMMENTS_SQL = """
SELECT c.relname AS view_name,
       obj_description(c.oid, 'pg_class') AS view_comment,
       a.attname AS column_name,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       col_description(c.oid, a.attnum) AS column_comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = 'reporting' AND c.relkind = 'v'
ORDER BY c.relname, a.attnum
"""
_CATEGORIES_SQL = "SELECT name, kind FROM ledger.categories ORDER BY kind, name"


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    name: str
    data_type: str
    comment: str


@dataclass(frozen=True, slots=True)
class ViewInfo:
    name: str
    comment: str
    columns: tuple[ColumnInfo, ...]


@dataclass(frozen=True, slots=True)
class Category:
    name: str
    kind: str


@dataclass(frozen=True, slots=True)
class SchemaCatalog:
    views: tuple[ViewInfo, ...]
    categories: tuple[Category, ...]
    txn_types: tuple[str, ...] = TXN_TYPES


class CatalogSource(Protocol):
    async def fetch_catalog_rows(
        self, sql: str, args: Sequence[Any] = ()
    ) -> list[Mapping[str, Any]]: ...


async def load_catalog(source: CatalogSource) -> SchemaCatalog:
    comment_rows = await source.fetch_catalog_rows(_COMMENTS_SQL)
    views: dict[str, tuple[str, list[ColumnInfo]]] = {}
    for row in comment_rows:
        name = f"reporting.{row['view_name']}"
        _, columns = views.setdefault(name, (row["view_comment"] or "", []))
        columns.append(
            ColumnInfo(row["column_name"], row["data_type"], row["column_comment"] or "")
        )
    category_rows = await source.fetch_catalog_rows(_CATEGORIES_SQL)
    return SchemaCatalog(
        views=tuple(
            ViewInfo(name, comment, tuple(columns)) for name, (comment, columns) in views.items()
        ),
        categories=tuple(Category(r["name"], r["kind"]) for r in category_rows),
    )


class CachedCatalog:
    """Loads the catalog on first use and refreshes it after ``ttl_seconds``."""

    def __init__(self, source: CatalogSource, ttl_seconds: float = 3600.0) -> None:
        self._source = source
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()
        self._catalog: SchemaCatalog | None = None
        self._loaded_at = 0.0

    async def get(self) -> SchemaCatalog:
        async with self._lock:
            if self._catalog is None or time.monotonic() - self._loaded_at > self._ttl:
                self._catalog = await load_catalog(self._source)
                self._loaded_at = time.monotonic()
            return self._catalog
