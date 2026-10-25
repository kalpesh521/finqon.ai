"""Text embeddings through the same OpenAI-compatible gateway as chat.

Providers meter embeddings per input text, not per request (Gemini's free tier allows 100
texts a minute), so the client paces itself to ``requests_per_minute`` and, if the provider
still answers 429, waits for the delay it asks for and retries.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Sequence
from typing import Protocol

import openai
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)
_BATCH = 50
_MAX_ATTEMPTS = 6


class Embedder(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAICompatibleEmbedder:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dimensions: int,
        timeout: float = 30.0,
        requests_per_minute: int = 90,
    ) -> None:
        self._client = AsyncOpenAI(
            base_url=base_url, api_key=api_key, timeout=timeout, max_retries=0
        )
        self._model = model
        self._dimensions = dimensions
        self._rpm = requests_per_minute
        self._next_ok = 0.0
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.close()

    async def _pace(self, count: int) -> None:
        if self._rpm <= 0:
            return
        async with self._lock:
            wait = self._next_ok - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_ok = time.monotonic() + count * 60.0 / self._rpm

    async def _batch(self, batch: list[str]) -> list[list[float]]:
        for attempt in range(_MAX_ATTEMPTS):
            await self._pace(len(batch))
            try:
                response = await self._client.embeddings.create(
                    model=self._model, input=batch, dimensions=self._dimensions
                )
            except openai.RateLimitError as exc:
                if attempt == _MAX_ATTEMPTS - 1:
                    raise
                delay = _retry_delay(exc)
                logger.warning("embedding_rate_limited", extra={"delay_s": delay})
                await asyncio.sleep(delay)
                continue
            # Gemini's compatibility layer returns ``index=None``; order is preserved.
            return [item.embedding for item in response.data]
        raise RuntimeError("unreachable")  # pragma: no cover

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _BATCH):
            vectors.extend(await self._batch(list(texts[start : start + _BATCH])))
        if len(vectors) != len(texts):
            raise RuntimeError("The embedding service returned the wrong number of vectors.")
        return vectors


def _retry_delay(exc: Exception) -> float:
    match = re.search(r"retry in ([\d.]+)s", str(exc))
    return min(float(match.group(1)) + 1.0, 60.0) if match else 20.0


def to_pgvector(vector: Sequence[float]) -> str:
    """pgvector text literal: ``[0.1,0.2,...]`` (cast with ``::vector`` in SQL)."""
    return "[" + ",".join(f"{v:.6f}" for v in vector) + "]"
