"""Process-wide resources: database engine, reader pool, model client.

Created once in the FastAPI lifespan (and by the CLI and the eval runner), and closed on
shutdown. Connections are opened lazily, so start-up does not need the database.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import text

from finqon.agent.catalog import CachedCatalog
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
        )

    async def check_ready(self) -> dict[str, bool]:
        """Ping both database connections. Never raises; failures read as ``False``."""
        return {
            "api_database": await _ping(self._ping_api),
            "reader_database": await _ping(self.executor.ping),
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
        await self.executor.close()
        await self.database.dispose()


async def _ping(probe: Callable[[], Awaitable[None]]) -> bool:
    try:
        await asyncio.wait_for(probe(), timeout=_READY_TIMEOUT_SECONDS)
    except Exception:
        logger.warning("readiness_probe_failed", exc_info=True)
        return False
    return True
