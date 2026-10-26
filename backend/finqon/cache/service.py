"""The semantic cache: two tiers, strict validation, and a hard latency budget.

Lookup order for a question:

1. **Exact tier.** One key read. Same user, data version, schema version, validity day and
   canonical question text. No embedding call.
2. **Semantic tier.** Embed the canonical text, then a vector search that is *pre-filtered* by
   user, data version, schema version, validity day and constraint signature
   (``analysis.py``). Only entries that already agree on every figure-changing constraint
   compete on similarity, and the best must clear ``similarity_threshold``.

Nothing here can make a turn fail or wait: every backend call has a timeout, a failure opens a
circuit breaker for ``cooldown`` seconds, and a broken or slow cache reads as a miss.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, TypeVar
from zoneinfo import ZoneInfo

from finqon.cache.analysis import Analysis, sql_is_time_relative
from finqon.cache.models import (
    STABLE_DAY,
    CachedAnswer,
    Match,
    PlanRecord,
    SearchFilter,
    day_tag,
    entry_key,
    user_tag,
)
from finqon.cache.store import CacheStore, CacheUnavailable

logger = logging.getLogger(__name__)

T = TypeVar("T")
Tier = Literal["exact", "semantic"]
Embed = Callable[[str], Awaitable[Sequence[float]]]

_SEARCH_LIMIT = 3
_EMBED_MEMO = 512
_MIN_VOLATILE_TTL = 60


@dataclass(frozen=True, slots=True)
class CacheConfig:
    similarity_threshold: float = 0.92
    ttl_seconds: int = 7 * 24 * 3600
    op_timeout: float = 0.25
    embed_timeout: float = 1.2
    breaker_cooldown: float = 30.0
    dimensions: int = 768


@dataclass(frozen=True, slots=True)
class CacheScope:
    """Everything outside the question that decides whether an entry may answer it."""

    user_id: uuid.UUID
    data_version: str
    schema_version: str
    today: date
    seconds_left_today: int

    @classmethod
    def at(
        cls,
        user_id: uuid.UUID,
        data_version: str,
        schema_version: str,
        tz: ZoneInfo,
        *,
        today: date,
        now: datetime | None = None,
    ) -> CacheScope:
        """``today`` is the date the agent plans with; ``now`` only sizes the expiry."""
        local = (now or datetime.now(tz)).astimezone(tz)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        left = 24 * 3600 - int((local - midnight).total_seconds())
        return cls(user_id, data_version, schema_version, today, left)


@dataclass(frozen=True, slots=True)
class Hit:
    entry: CachedAnswer
    tier: Tier
    similarity: float
    key: str


@dataclass(frozen=True, slots=True)
class Lookup:
    hit: Hit | None
    reason: str  # exact | semantic | cold | below_threshold | unavailable | embed_unavailable
    elapsed_ms: int
    embedding: Sequence[float] | None = None
    neighbour: Match | None = None  # best semantic candidate, even when it was rejected


class CircuitBreaker:
    """Open after consecutive failures; let one probe through once ``cooldown`` has passed."""

    def __init__(
        self,
        cooldown: float,
        *,
        threshold: int = 3,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cooldown = cooldown
        self._threshold = threshold
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None

    def allow(self) -> bool:
        if self._opened_at is None:
            return True
        if self._clock() - self._opened_at >= self._cooldown:
            self._opened_at = self._clock()  # one probe per cooldown window
            return True
        return False

    def success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = self._clock()


@dataclass(slots=True)
class CacheMetrics:
    counters: dict[str, int] = field(default_factory=dict)

    def incr(self, name: str, amount: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + amount

    def snapshot(self) -> dict[str, int]:
        return dict(self.counters)

    @property
    def hit_rate(self) -> float:
        hits = self.counters.get("hit_exact", 0) + self.counters.get("hit_semantic", 0)
        lookups = self.counters.get("lookup", 0)
        return hits / lookups if lookups else 0.0


class SemanticCache:
    def __init__(
        self,
        store: CacheStore,
        embed: Embed,
        config: CacheConfig,
        *,
        metrics: CacheMetrics | None = None,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._store = store
        self._embed = embed
        self._cfg = config
        self.metrics = metrics or CacheMetrics()
        self.breaker = breaker or CircuitBreaker(config.breaker_cooldown)
        self._ready = False
        self._ready_lock = asyncio.Lock()
        self._memo: OrderedDict[str, Sequence[float]] = OrderedDict()

    @property
    def similarity_threshold(self) -> float:
        return self._cfg.similarity_threshold

    # -- plumbing -------------------------------------------------------------------

    async def _io(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Run one backend call under the breaker and the operation timeout."""
        if not self.breaker.allow():
            self.metrics.incr("breaker_open")
            raise CacheUnavailable("circuit open")
        try:
            async with asyncio.timeout(self._cfg.op_timeout):
                await self._ensure_ready()
                result = await operation()
        except Exception as exc:
            self.breaker.failure()
            self.metrics.incr("backend_error")
            logger.warning(
                "semantic_cache_backend_error",
                extra={"error": type(exc).__name__, "breaker_open": self.breaker.is_open},
            )
            raise CacheUnavailable(str(exc)) from exc
        self.breaker.success()
        return result

    async def _ensure_ready(self) -> None:
        if self._ready:
            return
        async with self._ready_lock:
            if not self._ready:
                await self._store.ensure_ready()
                self._ready = True

    async def _embedding(self, text: str) -> Sequence[float] | None:
        """Embed with a budget. ``None`` means "no semantic tier for this question"."""
        cached = self._memo.get(text)
        if cached is not None:
            self._memo.move_to_end(text)
            return cached
        try:
            async with asyncio.timeout(self._cfg.embed_timeout):
                vector = await self._embed(text)
        except Exception as exc:
            self.metrics.incr("embed_unavailable")
            logger.info("semantic_cache_embed_skipped", extra={"error": type(exc).__name__})
            return None
        if len(vector) != self._cfg.dimensions:
            self.metrics.incr("embed_unavailable")
            logger.warning("semantic_cache_embed_wrong_size", extra={"size": len(vector)})
            return None
        self._memo[text] = vector
        if len(self._memo) > _EMBED_MEMO:
            self._memo.popitem(last=False)
        return vector

    @staticmethod
    def _days(scope: CacheScope) -> tuple[str, str]:
        return (day_tag(scope.today), STABLE_DAY)

    def _acceptable(self, entry: CachedAnswer, scope: CacheScope, analysis: Analysis) -> bool:
        """Re-check, on the entry itself, everything the store was asked to filter on."""
        return (
            entry.user == user_tag(scope.user_id)
            and entry.data_version == scope.data_version
            and entry.schema_version == scope.schema_version
            and entry.signature == analysis.signature
            and entry.day in self._days(scope)
        )

    # -- lookup ---------------------------------------------------------------------

    async def lookup(self, scope: CacheScope, analysis: Analysis) -> Lookup:
        started = time.perf_counter()
        self.metrics.incr("lookup")

        def done(
            reason: str,
            hit: Hit | None = None,
            embedding: Sequence[float] | None = None,
            neighbour: Match | None = None,
        ) -> Lookup:
            elapsed = int((time.perf_counter() - started) * 1000)
            return Lookup(hit, reason, elapsed, embedding, neighbour)

        user = user_tag(scope.user_id)
        keys = [
            entry_key(user, scope.data_version, scope.schema_version, day, analysis.text)
            for day in self._days(scope)
        ]
        try:
            found = await self._io(lambda: self._store.get_exact(user, keys))
        except CacheUnavailable:
            return done("unavailable")
        for key, entry in zip(keys, found, strict=True):
            if entry is not None and self._acceptable(entry, scope, analysis):
                self.metrics.incr("hit_exact")
                return done("exact", Hit(entry, "exact", 1.0, key))

        embedding = await self._embedding(analysis.text)
        if embedding is None:
            return done("embed_unavailable")
        flt = SearchFilter(
            user=user,
            data_version=scope.data_version,
            schema_version=scope.schema_version,
            signature=analysis.signature,
            days=self._days(scope),
        )
        try:
            matches = await self._io(lambda: self._store.search(embedding, flt, _SEARCH_LIMIT))
        except CacheUnavailable:
            return done("unavailable", embedding=embedding)
        best = matches[0] if matches else None
        for match in matches:
            if match.similarity >= self._cfg.similarity_threshold and self._acceptable(
                match.entry, scope, analysis
            ):
                self.metrics.incr("hit_semantic")
                return done(
                    "semantic",
                    Hit(match.entry, "semantic", match.similarity, match.key),
                    embedding,
                    best,
                )
        if best is not None:
            self.metrics.incr("near_miss")
        self.metrics.incr("miss")
        return done("below_threshold" if best else "cold", None, embedding, best)

    # -- writes ---------------------------------------------------------------------

    async def remember(
        self,
        scope: CacheScope,
        analysis: Analysis,
        plan: PlanRecord,
        embedding: Sequence[float] | None,
    ) -> bool:
        """Store a verified plan and answer. Returns ``False`` when it could not be stored."""
        volatile = analysis.time_relative or sql_is_time_relative(plan.sql)
        day = day_tag(scope.today) if volatile else STABLE_DAY
        ttl = (
            max(_MIN_VOLATILE_TTL, min(self._cfg.ttl_seconds, scope.seconds_left_today))
            if volatile
            else self._cfg.ttl_seconds
        )
        user = user_tag(scope.user_id)
        entry = CachedAnswer(
            user=user,
            question=analysis.text,
            signature=analysis.signature,
            sql=plan.sql,
            purpose=plan.purpose,
            chart_kind=plan.chart_kind,
            truncated=plan.truncated,
            answer=plan.answer,
            result_digest=plan.result_digest,
            row_count=plan.row_count,
            data_version=scope.data_version,
            schema_version=scope.schema_version,
            day=day,
            created_at=time.time(),
        )
        key = entry_key(user, scope.data_version, scope.schema_version, day, analysis.text)
        try:
            await self._io(lambda: self._store.put(key, entry, embedding, ttl))
        except CacheUnavailable:
            return False
        self.metrics.incr("stored")
        return True

    async def evict(self, scope: CacheScope, hit: Hit) -> None:
        """Remove an entry that failed validation so the next miss can replace it."""
        try:
            await self._io(lambda: self._store.delete(user_tag(scope.user_id), hit.key))
        except CacheUnavailable:
            return
        self.metrics.incr("evicted")

    async def purge_user(self, user_id: uuid.UUID) -> int:
        try:
            return await self._io(lambda: self._store.purge_user(user_tag(user_id)))
        except CacheUnavailable:
            return 0

    async def ping(self) -> None:
        await self._store.ping()

    def stats(self) -> dict[str, object]:
        """Aggregate counters for this process. No question text, user ids or answers."""
        return {
            "similarity_threshold": self._cfg.similarity_threshold,
            "breaker_open": self.breaker.is_open,
            "hit_rate": round(self.metrics.hit_rate, 4),
            "counters": self.metrics.snapshot(),
        }

    async def close(self) -> None:
        await self._store.close()
