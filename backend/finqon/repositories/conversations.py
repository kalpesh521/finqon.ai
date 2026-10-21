"""Conversation storage. Must be used inside ``Database.user_scope`` so RLS applies."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from finqon.schemas.conversations import ConversationSummary, MessageRecord

_MESSAGE_COLUMNS = (
    "id, question, sql, purpose, result_summary, answer, status, "
    "prompt_tokens, completion_tokens, created_at"
)


@dataclass(frozen=True, slots=True)
class NewMessage:
    conversation_id: uuid.UUID
    user_id: uuid.UUID
    question: str
    sql: str | None
    purpose: str | None
    result_summary: str | None
    answer: str
    status: str
    prompt_tokens: int
    completion_tokens: int


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, user_id: uuid.UUID, title: str) -> ConversationSummary:
        result = await self._session.execute(
            text(
                """
                INSERT INTO conversation.conversations (user_id, title)
                VALUES (:user_id, :title)
                RETURNING id, title, created_at, updated_at
                """
            ),
            {"user_id": user_id, "title": title[:120]},
        )
        return ConversationSummary.model_validate(result.mappings().one())

    async def get(self, conversation_id: uuid.UUID) -> ConversationSummary | None:
        result = await self._session.execute(
            text(
                """
                SELECT id, title, created_at, updated_at
                FROM conversation.conversations WHERE id = :id
                """
            ),
            {"id": conversation_id},
        )
        row = result.mappings().one_or_none()
        return ConversationSummary.model_validate(row) if row else None

    async def list_recent(self, limit: int, offset: int) -> list[ConversationSummary]:
        result = await self._session.execute(
            text(
                """
                SELECT id, title, created_at, updated_at
                FROM conversation.conversations
                ORDER BY updated_at DESC, id
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": limit, "offset": offset},
        )
        return [ConversationSummary.model_validate(r) for r in result.mappings()]

    async def messages(self, conversation_id: uuid.UUID) -> list[MessageRecord]:
        result = await self._session.execute(
            text(
                f"""
                SELECT {_MESSAGE_COLUMNS}
                FROM conversation.messages
                WHERE conversation_id = :conversation_id
                ORDER BY created_at, id
                """
            ),
            {"conversation_id": conversation_id},
        )
        return [MessageRecord.model_validate(r) for r in result.mappings()]

    async def recent_messages(self, conversation_id: uuid.UUID, limit: int) -> list[MessageRecord]:
        """The latest ``limit`` turns, oldest first."""
        result = await self._session.execute(
            text(
                f"""
                SELECT * FROM (
                    SELECT {_MESSAGE_COLUMNS}
                    FROM conversation.messages
                    WHERE conversation_id = :conversation_id
                    ORDER BY created_at DESC, id DESC
                    LIMIT :limit
                ) latest
                ORDER BY created_at, id
                """
            ),
            {"conversation_id": conversation_id, "limit": limit},
        )
        return [MessageRecord.model_validate(r) for r in result.mappings()]

    async def add_message(self, message: NewMessage) -> uuid.UUID:
        result = await self._session.execute(
            text(
                """
                INSERT INTO conversation.messages
                    (conversation_id, user_id, question, sql, purpose, result_summary,
                     answer, status, prompt_tokens, completion_tokens)
                VALUES
                    (:conversation_id, :user_id, :question, :sql, :purpose, :result_summary,
                     :answer, :status, :prompt_tokens, :completion_tokens)
                RETURNING id
                """
            ),
            {
                "conversation_id": message.conversation_id,
                "user_id": message.user_id,
                "question": message.question,
                "sql": message.sql,
                "purpose": message.purpose,
                "result_summary": message.result_summary,
                "answer": message.answer,
                "status": message.status,
                "prompt_tokens": message.prompt_tokens,
                "completion_tokens": message.completion_tokens,
            },
        )
        message_id: uuid.UUID = result.scalar_one()
        await self._session.execute(
            text("UPDATE conversation.conversations SET updated_at = now() WHERE id = :id"),
            {"id": message.conversation_id},
        )
        return message_id
