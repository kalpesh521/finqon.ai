"""What the agent loop needs from the outside world, and what it hands back.

Protocols for the SQL executor and schema catalog, the per-turn input and persisted
record, and the dependency bundle. ``services.chat`` builds these; the loop only sees
the interfaces, so it can be tested with fakes and has no database or HTTP imports.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from finqon.agent.catalog import SchemaCatalog
from finqon.agent.prompt_builder import HistoryTurn
from finqon.llm.client import LLMProvider
from finqon.schemas.chat import TokenUsage
from finqon.sql.guard import GuardedSql
from finqon.sql.results import QueryResult


class SqlRunner(Protocol):
    def guard(self, sql: str) -> GuardedSql: ...

    async def execute(self, guarded: GuardedSql, user_id: uuid.UUID) -> QueryResult: ...

    async def search_merchants(
        self, query: str, user_id: uuid.UUID, limit: int = 5
    ) -> list[dict[str, Any]]: ...


class CatalogProvider(Protocol):
    async def get(self) -> SchemaCatalog: ...


@dataclass(frozen=True, slots=True)
class ModelUsage:
    """One model call, as recorded by the usage repository."""

    user_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


@dataclass(frozen=True, slots=True)
class AgentLimits:
    max_model_steps: int = 6
    max_sql_executions: int = 3
    max_repairs: int = 2
    deadline_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class TurnInput:
    user_id: uuid.UUID
    conversation_id: uuid.UUID
    question: str
    today: date
    history: list[HistoryTurn] = field(default_factory=list)
    previous_sql: str | None = None
    uploaded_files: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TurnRecord:
    conversation_id: uuid.UUID
    user_id: uuid.UUID
    question: str
    sql: str | None
    purpose: str | None
    result_summary: str | None
    answer: str
    status: str
    usage: TokenUsage


@dataclass(slots=True)
class AgentDeps:
    llm: LLMProvider
    sql: SqlRunner
    catalog: CatalogProvider
    record_usage: Callable[[ModelUsage], Awaitable[None]]
    save_turn: Callable[[TurnRecord], Awaitable[uuid.UUID]]
    limits: AgentLimits = field(default_factory=AgentLimits)
    clock: Callable[[], float] = time.monotonic
