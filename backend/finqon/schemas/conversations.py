from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel

MessageStatus = Literal["answered", "clarification", "refused", "error"]


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str
    created_at: dt.datetime
    updated_at: dt.datetime


class MessageRecord(BaseModel):
    """One question-and-answer turn. Result sets are never stored."""

    id: uuid.UUID
    question: str
    sql: str | None
    purpose: str | None
    result_summary: str | None
    answer: str
    status: MessageStatus
    prompt_tokens: int
    completion_tokens: int
    created_at: dt.datetime


class ConversationDetail(ConversationSummary):
    messages: list[MessageRecord]
