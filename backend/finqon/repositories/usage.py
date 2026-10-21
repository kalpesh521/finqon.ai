from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from finqon.agent.contracts import ModelUsage


class UsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, usage: ModelUsage) -> None:
        await self._session.execute(
            text(
                """
                INSERT INTO operations.model_usage
                    (user_id, conversation_id, model, prompt_tokens, completion_tokens, latency_ms)
                VALUES
                    (:user_id, :conversation_id, :model, :prompt_tokens, :completion_tokens,
                     :latency_ms)
                """
            ),
            {
                "user_id": usage.user_id,
                "conversation_id": usage.conversation_id,
                "model": usage.model,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "latency_ms": usage.latency_ms,
            },
        )
