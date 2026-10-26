"""Storage contract for the semantic cache, and the in-process implementation.

``MemoryStore`` is exact and simple (a scan over at most ``max_entries`` items). It serves
development, tests and a single replica. ``RedisStore`` (redis_store.py) is the shared one.
Both obey the same rules: a search never crosses a filter, and an expired entry is gone.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from finqon.cache.models import CachedAnswer, Match, SearchFilter


class CacheUnavailable(RuntimeError):
    """The cache backend cannot be used right now. Callers treat it as a miss."""


class CacheStore(Protocol):
    async def ensure_ready(self) -> None: ...

    async def ping(self) -> None: ...

    async def get_exact(self, user: str, keys: Sequence[str]) -> list[CachedAnswer | None]: ...

    async def put(
        self,
        key: str,
        entry: CachedAnswer,
        embedding: Sequence[float] | None,
        ttl_seconds: int,
    ) -> None: ...

    async def search(
        self, vector: Sequence[float], flt: SearchFilter, limit: int
    ) -> list[Match]: ...

    async def delete(self, user: str, key: str) -> None: ...

    async def purge_user(self, user: str) -> int: ...

    async def close(self) -> None: ...


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


@dataclass(slots=True)
class _Item:
    entry: CachedAnswer
    vector: tuple[float, ...] | None
    expires_at: float


class MemoryStore:
    """LRU + TTL store. Not shared between processes."""

    def __init__(
        self, *, max_entries: int = 2000, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._items: OrderedDict[tuple[str, str], _Item] = OrderedDict()
        self._max = max_entries
        self._clock = clock

    async def ensure_ready(self) -> None:
        return None

    async def ping(self) -> None:
        return None

    async def close(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)

    def _live(self, user: str, key: str) -> _Item | None:
        item = self._items.get((user, key))
        if item is None:
            return None
        if item.expires_at <= self._clock():
            del self._items[(user, key)]
            return None
        self._items.move_to_end((user, key))
        return item

    async def get_exact(self, user: str, keys: Sequence[str]) -> list[CachedAnswer | None]:
        found = (self._live(user, key) for key in keys)
        return [item.entry if item else None for item in found]

    async def put(
        self,
        key: str,
        entry: CachedAnswer,
        embedding: Sequence[float] | None,
        ttl_seconds: int,
    ) -> None:
        vector = tuple(embedding) if embedding is not None else None
        self._items[(entry.user, key)] = _Item(entry, vector, self._clock() + ttl_seconds)
        self._items.move_to_end((entry.user, key))
        while len(self._items) > self._max:
            self._items.popitem(last=False)

    async def search(self, vector: Sequence[float], flt: SearchFilter, limit: int) -> list[Match]:
        now = self._clock()
        matches: list[Match] = []
        for (user, key), item in list(self._items.items()):
            if item.expires_at <= now:
                del self._items[(user, key)]
                continue
            e = item.entry
            if (
                item.vector is None
                or user != flt.user
                or e.data_version != flt.data_version
                or e.schema_version != flt.schema_version
                or e.signature != flt.signature
                or e.day not in flt.days
            ):
                continue
            matches.append(Match(key, e, cosine(vector, item.vector)))
        matches.sort(key=lambda m: m.similarity, reverse=True)
        return matches[:limit]

    async def delete(self, user: str, key: str) -> None:
        self._items.pop((user, key), None)

    async def purge_user(self, user: str) -> int:
        doomed = [k for k in self._items if k[0] == user]
        for k in doomed:
            del self._items[k]
        return len(doomed)
