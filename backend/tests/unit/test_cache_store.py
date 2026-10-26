"""Storage backends: the in-memory store, and the Redis store against a recording fake."""

from __future__ import annotations

import json
import struct
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from redis.exceptions import ResponseError

from finqon.cache.models import (
    STABLE_DAY,
    CachedAnswer,
    SearchFilter,
    entry_key,
    require_tag,
    result_digest,
)
from finqon.cache.redis_store import RedisStore, pack_vector, parse_search_reply
from finqon.cache.store import CacheUnavailable, MemoryStore, cosine
from finqon.sql.results import Column

USER = uuid.uuid4().hex
OTHER = uuid.uuid4().hex
DIM = 4


def entry(**overrides: Any) -> CachedAnswer:
    values: dict[str, Any] = {
        "user": USER,
        "question": "how much did i spend on food last month",
        "signature": "a" * 32,
        "sql": "SELECT 1",
        "purpose": "Food total.",
        "chart_kind": "none",
        "truncated": False,
        "answer": "You spent ₹100.00 on food.",
        "result_digest": "d" * 64,
        "row_count": 1,
        "data_version": "v1",
        "schema_version": "s1",
        "day": STABLE_DAY,
        "created_at": 1.0,
    }
    values.update(overrides)
    return CachedAnswer(**values)


def flt(**overrides: Any) -> SearchFilter:
    values: dict[str, Any] = {
        "user": USER,
        "data_version": "v1",
        "schema_version": "s1",
        "signature": "a" * 32,
        "days": (STABLE_DAY,),
    }
    values.update(overrides)
    return SearchFilter(**values)


# ------------------------------------------------------------------------------ models


def test_entry_round_trips_through_json() -> None:
    original = entry()
    assert CachedAnswer.from_json(original.to_json()) == original
    assert CachedAnswer.from_json(original.to_json().encode()) == original


@pytest.mark.parametrize(
    "raw",
    ["", "not json", "[]", "{}", '{"v": 99}', json.dumps({"v": 1, "user": "x"})],
)
def test_corrupt_or_foreign_entries_read_as_a_miss(raw: str) -> None:
    assert CachedAnswer.from_json(raw) is None


def test_entry_key_depends_on_every_part_of_the_identity() -> None:
    base = entry_key("u", "dv", "sv", "stable", "q")
    assert entry_key("u", "dv", "sv", "stable", "q") == base
    for changed in (
        entry_key("u2", "dv", "sv", "stable", "q"),
        entry_key("u", "dv2", "sv", "stable", "q"),
        entry_key("u", "dv", "sv2", "stable", "q"),
        entry_key("u", "dv", "sv", "d20261026", "q"),
        entry_key("u", "dv", "sv", "stable", "q2"),
    ):
        assert changed != base


def test_result_digest_tracks_values_names_and_types() -> None:
    cols = [Column(name="total", type="number")]
    base = result_digest(cols, [[100.0]])
    assert result_digest(cols, [[100.0]]) == base
    assert result_digest(cols, [[100.5]]) != base
    assert result_digest([Column(name="sum", type="number")], [[100.0]]) != base
    assert result_digest([Column(name="total", type="string")], [[100.0]]) != base
    assert result_digest(cols, [[100.0], [1.0]]) != base


@pytest.mark.parametrize("value", ["", "a b", "x}|@y", "a-b", "a:b", "*", "A" * 65, "ü"])
def test_unsafe_tag_values_are_rejected_before_they_reach_a_query(value: str) -> None:
    with pytest.raises(ValueError, match="unsafe cache tag"):
        require_tag(value)


# ------------------------------------------------------------------------- memory store


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_memory_store_exact_round_trip_and_ttl() -> None:
    clock = Clock()
    store = MemoryStore(clock=clock)
    await store.put("k1", entry(), None, ttl_seconds=10)
    assert (await store.get_exact(USER, ["k1", "missing"]))[0] == entry()
    assert (await store.get_exact(USER, ["k1", "missing"]))[1] is None
    clock.now = 11
    assert await store.get_exact(USER, ["k1"]) == [None]
    assert len(store) == 0


async def test_memory_store_never_returns_another_users_entry() -> None:
    store = MemoryStore()
    await store.put("k1", entry(), [1, 0, 0, 0], 60)
    assert await store.get_exact(OTHER, ["k1"]) == [None]
    assert await store.search([1, 0, 0, 0], flt(user=OTHER), 5) == []


async def test_memory_store_search_applies_every_filter() -> None:
    store = MemoryStore()
    await store.put("k1", entry(), [1, 0, 0, 0], 60)
    assert len(await store.search([1, 0, 0, 0], flt(), 5)) == 1
    assert await store.search([1, 0, 0, 0], flt(data_version="v2"), 5) == []
    assert await store.search([1, 0, 0, 0], flt(schema_version="s2"), 5) == []
    assert await store.search([1, 0, 0, 0], flt(signature="b" * 32), 5) == []
    assert await store.search([1, 0, 0, 0], flt(days=("d20261027",)), 5) == []


async def test_memory_store_ranks_by_similarity_and_skips_entries_without_vectors() -> None:
    store = MemoryStore()
    await store.put("near", entry(question="near"), [1, 0.1, 0, 0], 60)
    await store.put("far", entry(question="far"), [0, 1, 0, 0], 60)
    await store.put("exact-only", entry(question="exact only"), None, 60)
    found = await store.search([1, 0, 0, 0], flt(), 5)
    assert [m.key for m in found] == ["near", "far"]
    assert found[0].similarity > 0.99 > found[1].similarity


async def test_memory_store_evicts_least_recently_used() -> None:
    store = MemoryStore(max_entries=2)
    for key in ("a", "b"):
        await store.put(key, entry(question=key), None, 60)
    await store.get_exact(USER, ["a"])  # a is now the most recent
    await store.put("c", entry(question="c"), None, 60)
    assert await store.get_exact(USER, ["a", "b", "c"]) == [
        entry(question="a"),
        None,
        entry(question="c"),
    ]


async def test_memory_store_purge_and_delete() -> None:
    store = MemoryStore()
    await store.put("a", entry(), None, 60)
    await store.put("b", entry(user=OTHER), None, 60)
    await store.delete(USER, "a")
    assert await store.get_exact(USER, ["a"]) == [None]
    await store.put("a", entry(), None, 60)
    assert await store.purge_user(USER) == 1
    assert await store.get_exact(OTHER, ["b"]) == [entry(user=OTHER)]


def test_cosine() -> None:
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([0, 0], [1, 0]) == 0.0


# --------------------------------------------------------------------------- redis store


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def __aenter__(self) -> FakePipeline:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def set(self, *args: Any, **kwargs: Any) -> None:
        self._ops.append(("set", args, kwargs))

    def hset(self, *args: Any, **kwargs: Any) -> None:
        self._ops.append(("hset", args, kwargs))

    def expire(self, *args: Any, **kwargs: Any) -> None:
        self._ops.append(("expire", args, kwargs))

    async def execute(self) -> None:
        for name, args, kwargs in self._ops:
            self._redis.applied.append((name, args, kwargs))
            if name == "set":
                self._redis.strings[args[0]] = args[1]
            elif name == "hset":
                self._redis.hashes[args[0]] = kwargs["mapping"]


class FakeRedis:
    """Just enough of ``redis.asyncio.Redis`` for ``RedisStore``."""

    def __init__(self) -> None:
        self.commands: list[tuple[Any, ...]] = []
        self.strings: dict[str, Any] = {}
        self.hashes: dict[str, dict[str, Any]] = {}
        self.applied: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.index_exists = False
        self.module_missing = False
        self.search_reply: Any = [0]
        self.unlinked: list[str] = []

    async def execute_command(self, *args: Any) -> Any:
        self.commands.append(args)
        if self.module_missing:
            raise ResponseError("unknown command 'FT.INFO'")
        if args[0] == "FT.INFO":
            if not self.index_exists:
                raise ResponseError("Unknown index name")
            return []
        if args[0] == "FT.CREATE":
            self.index_exists = True
            return b"OK"
        if args[0] == "FT.SEARCH":
            return self.search_reply
        raise AssertionError(f"unexpected command {args[0]}")

    async def mget(self, keys: Sequence[str]) -> list[Any]:
        return [self.strings.get(k) for k in keys]

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        assert transaction
        return FakePipeline(self)

    async def unlink(self, *keys: str) -> int:
        self.unlinked.extend(keys)
        return len(keys)

    async def scan_iter(self, match: str, count: int) -> AsyncIterator[str]:
        prefix = match.rstrip("*")
        for key in [*self.strings, *self.hashes]:
            if key.startswith(prefix):
                yield key

    async def ping(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


def make_store(redis: FakeRedis | None = None, kind: str = "flat") -> tuple[RedisStore, FakeRedis]:
    redis = redis or FakeRedis()
    return RedisStore(redis, prefix="t:sc", dimensions=DIM, index_kind=kind), redis  # type: ignore[arg-type]


async def test_ensure_ready_creates_the_index_once_with_the_right_schema() -> None:
    store, redis = make_store()
    await store.ensure_ready()
    await store.ensure_ready()
    creates = [c for c in redis.commands if c[0] == "FT.CREATE"]
    assert len(creates) == 1
    create = list(creates[0])
    assert create[1] == "t:sc:idx:4:flat"
    assert create[create.index("PREFIX") + 2] == "t:sc:e:"
    for field in ("user", "ver", "sch", "sig", "day"):
        assert create[create.index(field) + 1] == "TAG"
    vector = create[create.index("VECTOR") :]
    assert vector[1:] == ["FLAT", "6", "TYPE", "FLOAT32", "DIM", "4", "DISTANCE_METRIC", "COSINE"]


async def test_hnsw_index_declares_its_parameters() -> None:
    store, redis = make_store(kind="hnsw")
    await store.ensure_ready()
    create = next(c for c in redis.commands if c[0] == "FT.CREATE")
    assert "HNSW" in create and create[create.index("HNSW") + 1] == "10"


async def test_missing_redisearch_is_reported_as_unavailable() -> None:
    redis = FakeRedis()
    redis.module_missing = True
    store, _ = make_store(redis)
    with pytest.raises(CacheUnavailable, match="RediSearch"):
        await store.ensure_ready()


async def test_put_writes_both_tiers_with_a_ttl_and_a_packed_vector() -> None:
    store, redis = make_store()
    await store.put("k" * 40, entry(), [1.0, 0.0, 0.5, 0.0], ttl_seconds=90)
    ops = {name: (args, kwargs) for name, args, kwargs in redis.applied}
    assert ops["set"][0][0] == f"t:sc:x:{USER}:{'k' * 40}"
    assert ops["set"][1]["ex"] == 90
    semantic_key = f"t:sc:e:{USER}:{'k' * 40}"
    assert ops["hset"][0][0] == semantic_key
    fields = ops["hset"][1]["mapping"]
    assert fields["user"] == USER and fields["ver"] == "v1" and fields["day"] == STABLE_DAY
    assert fields["vec"] == struct.pack("<4f", 1.0, 0.0, 0.5, 0.0)
    assert ops["expire"][0] == (semantic_key, 90)


async def test_put_without_an_embedding_writes_only_the_exact_tier() -> None:
    store, redis = make_store()
    await store.put("k" * 40, entry(), None, 60)
    assert [name for name, *_ in redis.applied] == ["set"]


async def test_put_rejects_a_vector_of_the_wrong_size() -> None:
    store, redis = make_store()
    with pytest.raises(ValueError, match="dimensions"):
        await store.put("k" * 40, entry(), [1.0, 0.0], 60)
    assert redis.applied == []


async def test_exact_reads_decode_entries_and_tolerate_garbage() -> None:
    store, redis = make_store()
    await store.put("a" * 40, entry(), None, 60)
    redis.strings[f"t:sc:x:{USER}:{'b' * 40}"] = b"garbage"
    found = await store.get_exact(USER, ["a" * 40, "b" * 40, "c" * 40])
    assert found == [entry(), None, None]


def test_search_query_prefilters_on_every_constraint() -> None:
    store, _ = make_store()
    query = store.build_query(flt(days=("d20261026", STABLE_DAY)), 3)
    assert query == (
        f"(@user:{{{USER}}} @ver:{{v1}} @sch:{{s1}} @sig:{{{'a' * 32}}} "
        "@day:{d20261026|stable})=>[KNN 3 @vec $q AS dist]"
    )


def test_search_query_refuses_injection_through_a_filter_value() -> None:
    store, _ = make_store()
    with pytest.raises(ValueError, match="unsafe cache tag"):
        store.build_query(flt(signature="x}) | (@user:{*"), 3)


async def test_search_sends_the_packed_query_vector_and_parses_the_reply() -> None:
    store, redis = make_store()
    good = entry()
    redis.search_reply = [
        2,
        f"t:sc:e:{USER}:{'a' * 40}".encode(),
        [b"dist", b"0.05", b"payload", good.to_json().encode()],
        f"t:sc:e:{USER}:{'b' * 40}".encode(),
        [b"dist", b"0.30", b"payload", entry(answer="other").to_json().encode()],
    ]
    matches = await store.search([1.0, 0.0, 0.0, 0.0], flt(), 3)
    command = next(c for c in redis.commands if c[0] == "FT.SEARCH")
    assert command[command.index("PARAMS") + 3] == pack_vector([1.0, 0.0, 0.0, 0.0])
    assert "DIALECT" in command and command[command.index("DIALECT") + 1] == "2"
    assert [m.key for m in matches] == ["a" * 40, "b" * 40]
    assert matches[0].similarity == pytest.approx(0.95)
    assert matches[0].entry == good


def test_reply_parser_drops_foreign_malformed_and_empty_results() -> None:
    mine = entry().to_json().encode()
    theirs = entry(user=OTHER).to_json().encode()
    reply = [
        4,
        b"k1", [b"dist", b"0.1", b"payload", theirs],  # another user's entry: dropped
        b"k2", [b"dist", b"0.1", b"payload", b"garbage"],  # corrupt: dropped
        b"k3", [b"dist", b"nan-ish", b"payload", mine],  # bad distance: dropped
        b"k4", [b"payload", mine],  # no distance: dropped
    ]  # fmt: skip
    assert parse_search_reply(reply, USER) == []
    assert parse_search_reply([0], USER) == []
    assert parse_search_reply(None, USER) == []
    assert parse_search_reply([1, b"k", "not-a-list"], USER) == []


async def test_delete_and_purge_only_touch_the_users_keys() -> None:
    store, redis = make_store()
    await store.put("a" * 40, entry(), [1.0, 0.0, 0.0, 0.0], 60)
    await store.put("b" * 40, entry(user=OTHER), [1.0, 0.0, 0.0, 0.0], 60)
    await store.delete(USER, "a" * 40)
    assert redis.unlinked == [f"t:sc:x:{USER}:{'a' * 40}", f"t:sc:e:{USER}:{'a' * 40}"]
    redis.unlinked.clear()
    assert await store.purge_user(OTHER) == 2
    assert all(OTHER in key for key in redis.unlinked)
