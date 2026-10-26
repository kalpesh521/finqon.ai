"""What the semantic cache stores, and the keys and digests that identify it.

An entry holds a *query plan and a verified answer*, never rows. Rows are re-read from
PostgreSQL on every hit; the entry's ``result_digest`` is how a hit proves that the data
under the stored answer is unchanged.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from finqon.sql.results import Cell, Column

FORMAT_VERSION = 1
STABLE_DAY = "stable"
_TAG = re.compile(r"[0-9a-z]{1,64}")


def user_tag(user_id: uuid.UUID) -> str:
    return user_id.hex


def day_tag(day: date) -> str:
    return f"d{day:%Y%m%d}"


def require_tag(value: str) -> str:
    """Tag values go into a RediSearch query string; only plain alphanumerics are allowed."""
    if not _TAG.fullmatch(value):
        raise ValueError(f"unsafe cache tag: {value!r}")
    return value


def entry_key(user: str, data_version: str, schema_version: str, day: str, text: str) -> str:
    """Exact-match identity: same user, data, schema, validity day and canonical question."""
    material = "\x1f".join((user, data_version, schema_version, day, text))
    return hashlib.sha256(material.encode()).hexdigest()[:40]


def result_digest(columns: Sequence[Column], rows: Sequence[Sequence[Cell]]) -> str:
    """Fingerprint of a result set as the client sees it (column names, types and cells)."""
    payload = json.dumps(
        [[(c.name, c.type) for c in columns], [list(r) for r in rows]],
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class CachedAnswer:
    user: str
    question: str  # canonical text
    signature: str
    sql: str  # the guarded SQL that produced the answer
    purpose: str
    chart_kind: str
    truncated: bool
    answer: str
    result_digest: str
    row_count: int
    data_version: str
    schema_version: str
    day: str  # ``STABLE_DAY`` or ``dYYYYMMDD`` for answers that depend on today's date
    created_at: float

    def to_json(self) -> str:
        return json.dumps({"v": FORMAT_VERSION, **asdict(self)}, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str | bytes) -> CachedAnswer | None:
        """Parse a stored entry. Anything unexpected is a miss, never an exception."""
        try:
            data: dict[str, Any] = json.loads(raw)
            if data.pop("v", None) != FORMAT_VERSION:
                return None
            return cls(**data)
        except (ValueError, TypeError, KeyError):
            return None


@dataclass(frozen=True, slots=True)
class PlanRecord:
    """What a finished, verified turn hands to the cache."""

    sql: str
    purpose: str
    chart_kind: str
    truncated: bool
    answer: str
    result_digest: str
    row_count: int


@dataclass(frozen=True, slots=True)
class SearchFilter:
    """Hard filters applied inside the vector search, before any similarity is considered."""

    user: str
    data_version: str
    schema_version: str
    signature: str
    days: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Match:
    key: str
    entry: CachedAnswer
    similarity: float
