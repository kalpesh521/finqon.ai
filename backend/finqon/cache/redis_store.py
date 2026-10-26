"""Redis Stack implementation of ``CacheStore``.

Layout (``prefix`` is ``FINQON_CACHE_KEY_PREFIX``):

* ``{prefix}:x:{user}:{key}``  string, the JSON entry. The exact tier: one ``MGET``.
* ``{prefix}:e:{user}:{key}``  hash, the semantic tier: ``user ver sch sig day`` TAG fields,
  a ``vec`` FLOAT32 vector and the JSON ``payload``. Indexed by ``{prefix}:idx:{dim}:{kind}``.

A semantic search is one ``FT.SEARCH`` with the user, data version, schema version,
constraint signature and validity day as *pre-filters* on the KNN, so the vector index only
ranks entries that are already allowed to answer. Every key carries the user id, and every
entry is checked again against it after the read.

Both keys have a TTL. Run Redis with a ``volatile-*`` or ``allkeys-*`` eviction policy and a
``maxmemory`` so the cache cannot grow without bound.
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Sequence
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError

from finqon.cache.models import CachedAnswer, Match, SearchFilter, require_tag
from finqon.cache.store import CacheUnavailable

logger = logging.getLogger(__name__)

_SCAN_BATCH = 500


def pack_vector(vector: Sequence[float]) -> bytes:
    """FLOAT32, little-endian: the layout RediSearch expects for a vector field."""
    return struct.pack(f"<{len(vector)}f", *vector)


class RedisStore:
    def __init__(
        self,
        client: Redis,
        *,
        prefix: str,
        dimensions: int,
        index_kind: str = "flat",
    ) -> None:
        self._r = client
        self._prefix = prefix
        self._dim = dimensions
        self._kind = index_kind
        self._index = f"{prefix}:idx:{dimensions}:{index_kind}"

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        prefix: str,
        dimensions: int,
        index_kind: str = "flat",
        timeout_seconds: float = 0.5,
    ) -> RedisStore:
        client = Redis.from_url(
            url,
            protocol=2,
            decode_responses=False,
            socket_timeout=timeout_seconds,
            socket_connect_timeout=timeout_seconds,
            health_check_interval=30,
            retry_on_timeout=False,
        )
        return cls(client, prefix=prefix, dimensions=dimensions, index_kind=index_kind)

    # -- naming ---------------------------------------------------------------------

    def _x(self, user: str, key: str) -> str:
        return f"{self._prefix}:x:{require_tag(user)}:{require_tag(key)}"

    def _e(self, user: str, key: str) -> str:
        return f"{self._prefix}:e:{require_tag(user)}:{require_tag(key)}"

    # -- lifecycle ------------------------------------------------------------------

    async def ensure_ready(self) -> None:
        """Create the vector index if it is missing. Safe to call from several replicas."""
        try:
            await self._r.execute_command("FT.INFO", self._index)
            return
        except ResponseError as exc:
            message = str(exc).lower()
            if "unknown command" in message:
                raise CacheUnavailable(
                    "Redis has no RediSearch module; use Redis Stack (redis/redis-stack-server)."
                ) from exc
            if "unknown" not in message and "no such index" not in message:
                raise CacheUnavailable(f"FT.INFO failed: {exc}") from exc
        except RedisError as exc:
            raise CacheUnavailable(f"Redis is unreachable: {exc}") from exc

        if self._kind == "hnsw":
            vector = ["HNSW", "10", "TYPE", "FLOAT32", "DIM", str(self._dim)]
            vector += ["DISTANCE_METRIC", "COSINE", "M", "16", "EF_CONSTRUCTION", "200"]
        else:
            vector = ["FLAT", "6", "TYPE", "FLOAT32", "DIM", str(self._dim)]
            vector += ["DISTANCE_METRIC", "COSINE"]
        command = ["FT.CREATE", self._index, "ON", "HASH", "PREFIX", "1", f"{self._prefix}:e:"]
        command += ["SCHEMA"]
        for field in ("user", "ver", "sch", "sig", "day"):
            command += [field, "TAG"]
        command += ["vec", "VECTOR", *vector]
        try:
            await self._r.execute_command(*command)
        except ResponseError as exc:
            if "already exists" not in str(exc).lower():
                raise CacheUnavailable(f"FT.CREATE failed: {exc}") from exc
        except RedisError as exc:
            raise CacheUnavailable(f"Redis is unreachable: {exc}") from exc
        logger.info("semantic_cache_index_ready", extra={"index": self._index})

    async def ping(self) -> None:
        await self._r.ping()

    async def close(self) -> None:
        await self._r.aclose()

    # -- exact tier -----------------------------------------------------------------

    async def get_exact(self, user: str, keys: Sequence[str]) -> list[CachedAnswer | None]:
        if not keys:
            return []
        raws = await self._r.mget([self._x(user, key) for key in keys])
        return [CachedAnswer.from_json(raw) if raw else None for raw in raws]

    # -- writes ---------------------------------------------------------------------

    async def put(
        self,
        key: str,
        entry: CachedAnswer,
        embedding: Sequence[float] | None,
        ttl_seconds: int,
    ) -> None:
        payload = entry.to_json()
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.set(self._x(entry.user, key), payload, ex=ttl_seconds)
            if embedding is not None:
                if len(embedding) != self._dim:
                    raise ValueError(f"embedding has {len(embedding)} dimensions, want {self._dim}")
                semantic = self._e(entry.user, key)
                pipe.hset(
                    semantic,
                    mapping={
                        "user": require_tag(entry.user),
                        "ver": require_tag(entry.data_version),
                        "sch": require_tag(entry.schema_version),
                        "sig": require_tag(entry.signature),
                        "day": require_tag(entry.day),
                        "vec": pack_vector(embedding),
                        "payload": payload,
                    },
                )
                pipe.expire(semantic, ttl_seconds)
            await pipe.execute()

    async def delete(self, user: str, key: str) -> None:
        await self._r.unlink(self._x(user, key), self._e(user, key))

    async def purge_user(self, user: str) -> int:
        removed = 0
        for kind in ("x", "e"):
            pattern = f"{self._prefix}:{kind}:{require_tag(user)}:*"
            batch: list[bytes] = []
            async for key in self._r.scan_iter(match=pattern, count=_SCAN_BATCH):
                batch.append(key)
                if len(batch) >= _SCAN_BATCH:
                    removed += await self._r.unlink(*batch)
                    batch.clear()
            if batch:
                removed += await self._r.unlink(*batch)
        return removed

    # -- semantic tier --------------------------------------------------------------

    def build_query(self, flt: SearchFilter, limit: int) -> str:
        days = "|".join(require_tag(d) for d in flt.days)
        return (
            f"(@user:{{{require_tag(flt.user)}}} @ver:{{{require_tag(flt.data_version)}}} "
            f"@sch:{{{require_tag(flt.schema_version)}}} @sig:{{{require_tag(flt.signature)}}} "
            f"@day:{{{days}}})=>[KNN {int(limit)} @vec $q AS dist]"
        )

    async def search(self, vector: Sequence[float], flt: SearchFilter, limit: int) -> list[Match]:
        reply = await self._r.execute_command(
            "FT.SEARCH", self._index, self.build_query(flt, limit),
            "PARAMS", "2", "q", pack_vector(vector),
            "SORTBY", "dist", "ASC",
            "RETURN", "2", "dist", "payload",
            "LIMIT", "0", str(int(limit)),
            "DIALECT", "2",
        )  # fmt: skip
        return parse_search_reply(reply, flt.user)


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def parse_search_reply(reply: Any, user: str) -> list[Match]:
    """RESP2 ``FT.SEARCH`` reply: ``[total, key, [field, value, ...], key, [...], ...]``.

    Entries that fail to parse, or that belong to another user, are dropped.
    """
    if not isinstance(reply, list | tuple) or len(reply) < 1:
        return []
    matches: list[Match] = []
    for position in range(1, len(reply) - 1, 2):
        redis_key = _text(reply[position])
        fields = reply[position + 1]
        if not isinstance(fields, list | tuple):
            continue
        values = {_text(fields[i]): fields[i + 1] for i in range(0, len(fields) - 1, 2)}
        if "dist" not in values or "payload" not in values:
            continue
        entry = CachedAnswer.from_json(values["payload"])
        if entry is None or entry.user != user:
            continue
        try:
            similarity = 1.0 - float(_text(values["dist"]))
        except ValueError:
            continue
        matches.append(Match(redis_key.rsplit(":", 1)[-1], entry, similarity))
    return matches
