"""Process-wide resources: database engine, reader pool, model client.

Created once in the FastAPI lifespan (and by the CLI and the eval runner), and closed on
shutdown. Connections are opened lazily, so start-up does not need the database.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import text

from finqon.agent.catalog import CachedCatalog
from finqon.cache.runner import CachedTurnRunner
from finqon.cache.service import CacheConfig, SemanticCache
from finqon.cache.store import CacheStore, MemoryStore
from finqon.core.config import Settings
from finqon.core.rate_limit import SlidingWindowRateLimiter
from finqon.db.session import Database, create_engine
from finqon.llm.client import LLMProvider, OpenAICompatibleProvider
from finqon.llm.embeddings import Embedder, OpenAICompatibleEmbedder
from finqon.sql.executor import QueryExecutor

logger = logging.getLogger(__name__)
_READY_TIMEOUT_SECONDS = 3.0


@dataclass(slots=True)
class Resources:
    settings: Settings
    database: Database
    executor: QueryExecutor
    llm: LLMProvider
    catalog: CachedCatalog
    auth_limiter: SlidingWindowRateLimiter
    embedder: Embedder
    cache: SemanticCache | None = None
    turn_runner: CachedTurnRunner | None = None

    @classmethod
    async def create(cls, settings: Settings, *, llm: LLMProvider | None = None) -> Resources:
        database = Database(create_engine(settings.api_database_url.get_secret_value()))
        executor = await QueryExecutor.create(
            settings.reader_database_url.get_secret_value(),
            timezone=settings.timezone,
            statement_timeout=settings.sql_statement_timeout,
            max_rows=settings.sql_max_rows,
            max_size=settings.reader_pool_max_size,
        )
        provider = llm or OpenAICompatibleProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
        embedder = OpenAICompatibleEmbedder(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            timeout=settings.llm_timeout_seconds,
            requests_per_minute=settings.embedding_requests_per_minute,
        )
        cache = _build_cache(settings, embedder)
        return cls(
            settings=settings,
            database=database,
            executor=executor,
            llm=provider,
            catalog=CachedCatalog(executor),
            auth_limiter=SlidingWindowRateLimiter(
                settings.auth_rate_limit_attempts, settings.auth_rate_limit_window_seconds
            ),
            embedder=embedder,
            cache=cache,
            turn_runner=(
                CachedTurnRunner(cache, mode=settings.cache_mode, tz=settings.tz)
                if cache is not None
                else None
            ),
        )

    async def forget_cached_answers(self, user_id: uuid.UUID) -> None:
        """Drop a user's cached answers after their statements change. Best effort.

        Correctness does not depend on this: entries are keyed by a ledger fingerprint and
        every hit is re-verified against the database. It frees memory and keeps answers
        derived from a deleted statement from lingering until their TTL.
        """
        if self.cache is None:
            return
        try:
            await self.cache.purge_user(user_id)
        except Exception:
            logger.warning("semantic_cache_purge_failed", exc_info=True)

    async def check_ready(self) -> dict[str, bool]:
        """Ping both database connections. Never raises; failures read as ``False``."""
        return {
            "api_database": await _ping(self._ping_api),
            "reader_database": await _ping(self.executor.ping),
        }

    async def cache_status(self) -> dict[str, object]:
        """What the semantic cache is doing in this process. Never raises."""
        status: dict[str, object] = {"mode": self.settings.cache_mode}
        if self.cache is None:
            return {**status, "enabled": False}
        return {
            **status,
            "enabled": True,
            "backend": self.settings.cache_backend,
            "store_reachable": await _ping(self.cache.ping),
            **self.cache.stats(),
        }

    async def _ping_api(self) -> None:
        async with self.database.session() as session:
            await session.execute(text("SELECT 1"))

    async def close(self) -> None:
        aclose = getattr(self.llm, "aclose", None)
        if aclose is not None:
            await aclose()
        aclose_embedder = getattr(self.embedder, "aclose", None)
        if aclose_embedder is not None:
            await aclose_embedder()
        if self.cache is not None:
            await self.cache.close()
        await self.executor.close()
        await self.database.dispose()


async def _ping(probe: Callable[[], Awaitable[None]]) -> bool:
    try:
        await asyncio.wait_for(probe(), timeout=_READY_TIMEOUT_SECONDS)
    except Exception:
        logger.warning("readiness_probe_failed", exc_info=True)
        return False
    return True


def _build_cache(settings: Settings, embedder: Embedder) -> SemanticCache | None:
    """The semantic cache, or ``None`` when ``FINQON_CACHE_MODE`` is ``off``."""
    if settings.cache_mode == "off":
        return None
    store: CacheStore
    if settings.cache_backend == "redis":
        from finqon.cache.redis_store import RedisStore  # noqa: PLC0415 - optional at runtime

        store = RedisStore.from_url(
            settings.redis_url.get_secret_value(),
            prefix=settings.cache_key_prefix,
            dimensions=settings.embedding_dimensions,
            index_kind=settings.cache_vector_index,
            timeout_seconds=max(0.1, settings.cache_op_timeout_ms / 1000),
        )
    else:
        store = MemoryStore(max_entries=settings.cache_memory_max_entries)

    async def embed(text: str) -> list[float]:
        return (await embedder.embed([text]))[0]

    logger.info(
        "semantic_cache_configured",
        extra={"mode": settings.cache_mode, "backend": settings.cache_backend},
    )
    return SemanticCache(
        store,
        embed,
        CacheConfig(
            similarity_threshold=settings.cache_similarity_threshold,
            ttl_seconds=settings.cache_ttl_seconds,
            op_timeout=settings.cache_op_timeout_ms / 1000,
            embed_timeout=settings.cache_embed_timeout_ms / 1000,
            breaker_cooldown=settings.cache_breaker_cooldown_seconds,
            dimensions=settings.embedding_dimensions,
        ),
    )
