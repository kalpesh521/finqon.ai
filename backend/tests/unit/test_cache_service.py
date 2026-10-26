"""The cache service: tiers, validation, expiry, and failing safe when the backend does not."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from finqon.cache.analysis import Analysis, analyse
from finqon.cache.models import PlanRecord, day_tag, user_tag
from finqon.cache.service import CacheConfig, CacheScope, CircuitBreaker, SemanticCache
from finqon.cache.store import CacheUnavailable, MemoryStore

DIM = 4
USER = uuid.uuid4()
OTHER_USER = uuid.uuid4()
TODAY = date(2026, 10, 21)
QUESTION = "How much did I spend on food last month?"
PARAPHRASE = "What was my total food spending last month?"


def scope(**overrides: Any) -> CacheScope:
    values: dict[str, Any] = {
        "user_id": USER,
        "data_version": "v1",
        "schema_version": "s1",
        "today": TODAY,
        "seconds_left_today": 3600,
    }
    values.update(overrides)
    return CacheScope(**values)


def plan(**overrides: Any) -> PlanRecord:
    values: dict[str, Any] = {
        "sql": "SELECT SUM(spend_amount) FROM reporting.spend LIMIT 500",
        "purpose": "Food total.",
        "chart_kind": "none",
        "truncated": False,
        "answer": "You spent ₹100.00 on food last month.",
        "result_digest": "d" * 64,
        "row_count": 1,
    }
    values.update(overrides)
    return PlanRecord(**values)


class FakeEmbed:
    """Vectors from a table; anything unlisted gets an orthogonal one."""

    def __init__(self, table: dict[str, Sequence[float]] | None = None) -> None:
        self.table = table or {}
        self.calls: list[str] = []
        self.delay = 0.0
        self.fail: Exception | None = None
        self.size = DIM

    async def __call__(self, text: str) -> Sequence[float]:
        self.calls.append(text)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise self.fail
        vector = self.table.get(text)
        if vector is None:
            return [0.0, 0.0, 0.0, 1.0][: self.size] + [0.0] * (self.size - 4)
        return list(vector)


def build(
    embed: FakeEmbed | None = None,
    store: Any = None,
    threshold: float = 0.9,
    **config: Any,
) -> SemanticCache:
    cfg = CacheConfig(similarity_threshold=threshold, dimensions=DIM, **config)
    return SemanticCache(store if store is not None else MemoryStore(), embed or FakeEmbed(), cfg)


def vectors_for(*texts: str) -> FakeEmbed:
    """All listed texts embed almost identically."""
    return FakeEmbed({t: [1.0, 0.01 * i, 0.0, 0.0] for i, t in enumerate(texts)})


async def seed(
    cache: SemanticCache,
    question: str = QUESTION,
    *,
    sc: CacheScope | None = None,
    embedding: Sequence[float] | None = (1.0, 0.0, 0.0, 0.0),
    **plan_overrides: Any,
) -> Analysis:
    analysis = analyse(question)
    assert await cache.remember(sc or scope(), analysis, plan(**plan_overrides), embedding)
    return analysis


# ------------------------------------------------------------------------- exact tier


async def test_exact_hit_needs_no_embedding_call() -> None:
    embed = FakeEmbed()
    cache = build(embed)
    analysis = await seed(cache)
    found = await cache.lookup(scope(), analysis)
    assert found.hit is not None and found.hit.tier == "exact" and found.hit.similarity == 1.0
    assert embed.calls == []


async def test_exact_tier_matches_regardless_of_punctuation_and_case() -> None:
    cache = build()
    await seed(cache, "How much did I spend on food last month?")
    found = await cache.lookup(scope(), analyse("how much did i spend on FOOD last month"))
    assert found.hit is not None and found.hit.tier == "exact"


@pytest.mark.parametrize(
    "other",
    [
        scope(user_id=OTHER_USER),
        scope(data_version="v2"),
        scope(schema_version="s2"),
    ],
    ids=["other user", "ledger changed", "schema or prompt changed"],
)
async def test_entries_are_not_offered_across_users_data_or_schema(other: CacheScope) -> None:
    cache = build(vectors_for(QUESTION))
    analysis = await seed(cache)
    found = await cache.lookup(other, analysis)
    assert found.hit is None


async def test_time_relative_answers_are_only_valid_on_the_day_they_were_written() -> None:
    cache = build(vectors_for(QUESTION))
    analysis = await seed(cache)  # "last month" depends on today
    assert (await cache.lookup(scope(), analysis)).hit is not None
    tomorrow = scope(today=date(2026, 10, 22))
    assert (await cache.lookup(tomorrow, analysis)).hit is None


async def test_absolute_answers_are_valid_on_any_day() -> None:
    cache = build(vectors_for("how much did i spend on food in september 2026"))
    analysis = await seed(cache, "How much did I spend on food in September 2026?")
    assert not analysis.time_relative
    later = scope(today=date(2027, 3, 1))
    assert (await cache.lookup(later, analysis)).hit is not None


async def test_sql_that_reads_the_clock_makes_an_absolute_looking_question_day_bound() -> None:
    store = MemoryStore()
    cache = build(store=store)
    analysis = await seed(
        cache,
        "Top 5 merchants by spend",
        sql="SELECT 1 FROM reporting.spend WHERE d >= current_date - 30 LIMIT 500",
    )
    assert not analysis.time_relative
    assert (await cache.lookup(scope(), analysis)).hit is not None
    assert (await cache.lookup(scope(today=date(2026, 10, 22)), analysis)).hit is None


async def test_time_relative_entries_expire_at_the_end_of_the_local_day() -> None:
    clock = [0.0]
    store = MemoryStore(clock=lambda: clock[0])
    cache = build(store=store)
    analysis = await seed(cache, sc=scope(seconds_left_today=120))
    clock[0] = 119
    assert (await cache.lookup(scope(), analysis)).hit is not None
    clock[0] = 121
    assert (await cache.lookup(scope(), analysis)).hit is None


def test_scope_counts_seconds_to_local_midnight() -> None:
    tz = ZoneInfo("Asia/Kolkata")
    now = datetime(2026, 10, 21, 23, 0, 0, tzinfo=tz)
    made = CacheScope.at(USER, "v", "s", tz, today=TODAY, now=now)
    assert made.seconds_left_today == 3600 and made.today == TODAY


# ---------------------------------------------------------------------- semantic tier


async def test_a_paraphrase_hits_the_semantic_tier() -> None:
    embed = vectors_for(analyse(QUESTION).text, analyse(PARAPHRASE).text)
    cache = build(embed)
    await seed(cache, embedding=await embed(analyse(QUESTION).text))
    found = await cache.lookup(scope(), analyse(PARAPHRASE))
    assert found.hit is not None and found.hit.tier == "semantic"
    assert found.hit.similarity > 0.99
    assert found.reason == "semantic"
    assert cache.metrics.counters["hit_semantic"] == 1


async def test_similarity_below_the_threshold_is_a_miss_with_a_logged_neighbour() -> None:
    embed = FakeEmbed({analyse(PARAPHRASE).text: [0.8, 0.6, 0.0, 0.0]})  # cosine 0.8 to seed
    cache = build(embed, threshold=0.9)
    await seed(cache, embedding=[1.0, 0.0, 0.0, 0.0])
    found = await cache.lookup(scope(), analyse(PARAPHRASE))
    assert found.hit is None and found.reason == "below_threshold"
    assert found.neighbour is not None and found.neighbour.similarity == pytest.approx(0.8)
    assert cache.metrics.counters["near_miss"] == 1


@pytest.mark.parametrize(
    "different",
    [
        "How much did I spend on food in October?",
        "How much did I spend on groceries last month?",
        "How much did I earn last month?",
        "Top 5 food transactions last month",
    ],
)
async def test_identical_embeddings_still_cannot_cross_a_constraint(different: str) -> None:
    """The embedding says "same" (cosine 1.0); the signature says "different"; signature wins."""
    same_vector = [1.0, 0.0, 0.0, 0.0]
    embed = FakeEmbed({analyse(different).text: same_vector, analyse(QUESTION).text: same_vector})
    cache = build(embed, threshold=0.8)
    await seed(cache, embedding=same_vector)
    found = await cache.lookup(scope(), analyse(different))
    assert found.hit is None and found.reason == "cold"


async def test_semantic_tier_never_returns_another_users_entry() -> None:
    embed = vectors_for(analyse(QUESTION).text, analyse(PARAPHRASE).text)
    cache = build(embed)
    await seed(cache, embedding=[1.0, 0.0, 0.0, 0.0])
    assert (await cache.lookup(scope(user_id=OTHER_USER), analyse(PARAPHRASE))).hit is None


async def test_slow_embedding_degrades_to_the_exact_tier_instead_of_waiting() -> None:
    embed = FakeEmbed()
    embed.delay = 5.0
    cache = build(embed, embed_timeout=0.05)
    started = asyncio.get_running_loop().time()
    found = await cache.lookup(scope(), analyse(PARAPHRASE))
    assert asyncio.get_running_loop().time() - started < 1.0
    assert found.hit is None and found.reason == "embed_unavailable"


async def test_failing_or_mis_sized_embeddings_are_a_miss_not_an_error() -> None:
    embed = FakeEmbed()
    embed.fail = RuntimeError("quota")
    cache = build(embed)
    assert (await cache.lookup(scope(), analyse(PARAPHRASE))).reason == "embed_unavailable"
    embed.fail = None
    embed.size = 2
    assert (await cache.lookup(scope(), analyse(PARAPHRASE))).reason == "embed_unavailable"
    assert cache.breaker.is_open is False  # an embedding outage is not a cache outage


async def test_embeddings_are_memoised() -> None:
    embed = FakeEmbed()
    cache = build(embed)
    await cache.lookup(scope(), analyse(PARAPHRASE))
    await cache.lookup(scope(data_version="v2"), analyse(PARAPHRASE))
    assert embed.calls == [analyse(PARAPHRASE).text]


# ------------------------------------------------------------------- failing safe


class BrokenStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.broken = True

    async def get_exact(self, user: str, keys: Sequence[str]) -> Any:
        self.calls += 1
        if self.broken:
            raise ConnectionError("redis down")
        return await super().get_exact(user, keys)


async def test_backend_failure_reads_as_a_miss_and_opens_the_breaker() -> None:
    store = BrokenStore()
    cache = build(store=store)
    for _ in range(3):
        found = await cache.lookup(scope(), analyse(QUESTION))
        assert found.hit is None and found.reason == "unavailable"
    assert cache.breaker.is_open
    assert store.calls == 3
    await cache.lookup(scope(), analyse(QUESTION))  # skipped: the backend is not touched
    assert store.calls == 3
    assert cache.metrics.counters["breaker_open"] >= 1


async def test_breaker_lets_a_probe_through_after_the_cooldown_and_recovers() -> None:
    clock = [0.0]
    breaker = CircuitBreaker(cooldown=30, clock=lambda: clock[0])
    store = BrokenStore()
    cache = SemanticCache(store, FakeEmbed(), CacheConfig(dimensions=DIM), breaker=breaker)
    for _ in range(3):
        await cache.lookup(scope(), analyse(QUESTION))
    assert breaker.is_open
    clock[0] = 31
    store.broken = False
    await cache.lookup(scope(), analyse(QUESTION))
    assert not breaker.is_open and store.calls == 4


async def test_a_slow_backend_is_cut_off_by_the_operation_timeout() -> None:
    class SlowStore(MemoryStore):
        async def get_exact(self, user: str, keys: Sequence[str]) -> Any:
            await asyncio.sleep(5)

    cache = build(store=SlowStore(), op_timeout=0.05)
    started = asyncio.get_running_loop().time()
    found = await cache.lookup(scope(), analyse(QUESTION))
    assert asyncio.get_running_loop().time() - started < 1.0
    assert found.reason == "unavailable"


async def test_remember_reports_failure_instead_of_raising() -> None:
    class RejectingStore(MemoryStore):
        async def put(self, *args: Any, **kwargs: Any) -> None:
            raise CacheUnavailable("read-only replica")

    cache = build(store=RejectingStore())
    assert await cache.remember(scope(), analyse(QUESTION), plan(), None) is False


async def test_index_is_prepared_lazily_and_retried_after_a_failure() -> None:
    class FlakyReady(MemoryStore):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        async def ensure_ready(self) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise CacheUnavailable("redis starting")

    store = FlakyReady()
    cache = build(store=store)
    assert (await cache.lookup(scope(), analyse(QUESTION))).reason == "unavailable"
    assert (await cache.lookup(scope(), analyse(QUESTION))).reason != "unavailable"
    assert store.attempts == 2


# ---------------------------------------------------------------------- maintenance


async def test_evict_and_purge() -> None:
    cache = build()
    analysis = await seed(cache)
    hit = (await cache.lookup(scope(), analysis)).hit
    assert hit is not None
    await cache.evict(scope(), hit)
    assert (await cache.lookup(scope(), analysis)).hit is None
    await seed(cache)
    await seed(cache, "top 5 merchants", sc=scope(user_id=OTHER_USER))
    assert await cache.purge_user(USER) == 1
    assert (await cache.lookup(scope(user_id=OTHER_USER), analyse("top 5 merchants"))).hit


def test_tags_are_plain_alphanumerics() -> None:
    assert user_tag(USER) == USER.hex
    assert day_tag(date(2026, 1, 5)) == "d20260105"
