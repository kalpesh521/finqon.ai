"""Chat request and the streamed event contract.

The events are defined once here, exposed in OpenAPI, and mirrored by the future
frontend. ``table.rows`` are the query result; ``chart`` only names the kind and the
columns to plot. No event carries invented points.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from finqon.sql.results import Cell, Column

ChartKind = Literal["bar", "pie", "line", "none"]
Stage = Literal["thinking", "querying", "summarizing"]


class ChatRequest(BaseModel):
    conversation_id: uuid.UUID | None = Field(
        default=None, description="Continue this conversation, or null to start a new one."
    )
    message: Annotated[str, Field(min_length=1, max_length=2000)]


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens = self.prompt_tokens + self.completion_tokens


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True)


class StatusEvent(_Event):
    type: Literal["status"] = "status"
    stage: Stage


class SqlEvent(_Event):
    type: Literal["sql"] = "sql"
    sql: str
    purpose: str


class TableEvent(_Event):
    type: Literal["table"] = "table"
    columns: list[Column]
    rows: list[list[Cell]]
    truncated: bool
    elapsed_ms: int = Field(ge=0, description="Database execution time for this query.")


class ChartEvent(_Event):
    type: Literal["chart"] = "chart"
    kind: ChartKind
    x: str
    y: str


class ClarificationEvent(_Event):
    type: Literal["clarification"] = "clarification"
    question: str
    options: list[str] = Field(max_length=3)


class TokenEvent(_Event):
    type: Literal["token"] = "token"
    text: str


class RetractEvent(_Event):
    """Discard the answer text streamed so far; a verified replacement follows.

    Live tokens go out before the grounding check finishes. If the check fails, or the
    model turns out to be calling a tool, the client clears what it showed.
    """

    type: Literal["retract"] = "retract"


DoneStatus = Literal["answered", "clarification", "refused", "info"]


class DoneEvent(_Event):
    type: Literal["done"] = "done"
    message_id: uuid.UUID
    conversation_id: uuid.UUID
    usage: TokenUsage
    status: DoneStatus = "answered"


class ErrorEvent(_Event):
    type: Literal["error"] = "error"
    code: str
    message: str


ChatEvent = Annotated[
    StatusEvent
    | SqlEvent
    | TableEvent
    | ChartEvent
    | ClarificationEvent
    | TokenEvent
    | RetractEvent
    | DoneEvent
    | ErrorEvent,
    Field(discriminator="type"),
]

chat_event_adapter: TypeAdapter[ChatEvent] = TypeAdapter(ChatEvent)


def encode_sse(event: ChatEvent) -> str:
    """Serialise one event as a Server-Sent Events frame."""
    payload = chat_event_adapter.dump_json(event).decode()
    return f"event: {event.type}\ndata: {payload}\n\n"
