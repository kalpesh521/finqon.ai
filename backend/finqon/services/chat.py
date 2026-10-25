"""Chat orchestration glue: conversations, history, persistence, and the SSE stream."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from finqon.agent.contracts import AgentDeps, AgentLimits, ModelUsage, TurnInput, TurnRecord
from finqon.agent.date_ranges import today_in
from finqon.agent.orchestrator import run_canned, run_turn
from finqon.agent.prompt_builder import HistoryTurn
from finqon.agent.smalltalk import smalltalk_reply
from finqon.core.errors import NotFoundError
from finqon.llm.embeddings import to_pgvector
from finqon.privacy.pii import REFUSAL_MESSAGE, screen
from finqon.repositories.conversations import ConversationRepository, NewMessage
from finqon.repositories.statements import StatementRepository
from finqon.repositories.usage import UsageRepository
from finqon.resources import Resources
from finqon.schemas.chat import ChatEvent, DoneStatus, ErrorEvent
from finqon.schemas.conversations import ConversationDetail, ConversationSummary, ConversationUpdate

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Gate:
    """What the pre-model checks decided about a user message."""

    text: str  # safe to store and to send to a model: sensitive values are already masked
    reply: str | None = None  # a fixed reply; the model is not called
    status: DoneStatus = "info"


def gate_message(message: str) -> Gate:
    """Mask PII, refuse secrets professionally, and answer small talk without the model."""
    screened = screen(message.strip())
    if screened.blocked:
        return Gate(screened.text, REFUSAL_MESSAGE, "info")
    reply = smalltalk_reply(screened.text)
    return Gate(screened.text, reply, "info") if reply else Gate(screened.text)


@dataclass(frozen=True, slots=True)
class OpenedConversation:
    id: uuid.UUID
    history: list[HistoryTurn]
    previous_sql: str | None
    uploaded_files: list[str] = field(default_factory=list)


class ChatService:
    def __init__(self, resources: Resources) -> None:
        self._r = resources

    # -- conversations ----------------------------------------------------------------

    async def open_conversation(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID | None, first_message: str
    ) -> OpenedConversation:
        """Create a conversation or load one the user owns. Raises ``NotFoundError``."""
        settings = self._r.settings
        async with self._r.database.user_scope(user_id) as session:
            repo = ConversationRepository(session)
            if conversation_id is None:
                created = await repo.create(user_id, first_message.strip())
                files = await StatementRepository(session).account_names()
                return OpenedConversation(created.id, [], None, files)
            if await repo.get(conversation_id) is None:
                # Not found and not owned look identical, on purpose.
                raise NotFoundError("Conversation not found.")
            recent = await repo.recent_messages(conversation_id, settings.agent_history_turns)
            files = await StatementRepository(session).account_names()

        history = [HistoryTurn(m.question, m.sql, m.result_summary, m.answer) for m in recent]
        previous_sql = next((m.sql for m in reversed(recent) if m.sql), None)
        return OpenedConversation(conversation_id, history, previous_sql, files)

    async def list_conversations(
        self, user_id: uuid.UUID, limit: int, offset: int
    ) -> list[ConversationSummary]:
        async with self._r.database.user_scope(user_id) as session:
            return await ConversationRepository(session).list_recent(limit, offset)

    async def get_conversation(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> ConversationDetail:
        async with self._r.database.user_scope(user_id) as session:
            repo = ConversationRepository(session)
            summary = await repo.get(conversation_id)
            if summary is None:
                raise NotFoundError("Conversation not found.")
            messages = await repo.messages(conversation_id)
        return ConversationDetail(**summary.model_dump(), messages=messages)

    async def update_conversation(
        self, user_id: uuid.UUID, conversation_id: uuid.UUID, change: ConversationUpdate
    ) -> ConversationSummary:
        async with self._r.database.user_scope(user_id) as session:
            updated = await ConversationRepository(session).update(
                conversation_id, title=change.title, pinned=change.pinned
            )
        if updated is None:
            raise NotFoundError("Conversation not found.")
        return updated

    async def delete_conversation(self, user_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
        async with self._r.database.user_scope(user_id) as session:
            deleted = await ConversationRepository(session).delete(conversation_id)
        if not deleted:
            raise NotFoundError("Conversation not found.")

    # -- the turn ----------------------------------------------------------------------

    async def stream_turn(
        self,
        user_id: uuid.UUID,
        conversation: OpenedConversation,
        question: str,
        gate: Gate | None = None,
    ) -> AsyncIterator[ChatEvent]:
        settings = self._r.settings
        turn = TurnInput(
            user_id=user_id,
            conversation_id=conversation.id,
            question=question,
            today=today_in(settings.tz),
            history=conversation.history,
            previous_sql=conversation.previous_sql,
            uploaded_files=conversation.uploaded_files,
        )
        deps = AgentDeps(
            llm=self._r.llm,
            sql=self._r.executor,
            catalog=self._r.catalog,
            record_usage=self._record_usage,
            save_turn=self._save_turn,
            embed=self._embed_query,
            limits=AgentLimits(
                max_model_steps=settings.agent_max_model_steps,
                max_sql_executions=settings.agent_max_sql_executions,
                max_repairs=settings.agent_max_repairs,
                deadline_seconds=settings.agent_deadline_seconds,
            ),
        )
        try:
            if gate is not None and gate.reply is not None:
                async for event in run_canned(turn, deps, gate.reply, gate.status):
                    yield event
                return
            async for event in run_turn(turn, deps):
                yield event
        except Exception:
            logger.exception("chat_turn_failed")
            yield ErrorEvent(code="internal_error", message="Something went wrong. Please retry.")

    async def _embed_query(self, text: str) -> str:
        vectors = await self._r.embedder.embed([text])
        return to_pgvector(vectors[0])

    async def _record_usage(self, usage: ModelUsage) -> None:
        async with self._r.database.session() as session:
            await UsageRepository(session).record(usage)

    async def _save_turn(self, record: TurnRecord) -> uuid.UUID:
        async with self._r.database.user_scope(record.user_id) as session:
            return await ConversationRepository(session).add_message(
                NewMessage(
                    conversation_id=record.conversation_id,
                    user_id=record.user_id,
                    question=record.question,
                    sql=record.sql,
                    purpose=record.purpose,
                    result_summary=record.result_summary,
                    answer=record.answer,
                    status=record.status,
                    prompt_tokens=record.usage.prompt_tokens,
                    completion_tokens=record.usage.completion_tokens,
                )
            )
