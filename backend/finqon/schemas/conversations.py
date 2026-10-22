from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

MessageStatus = Literal["answered", "clarification", "refused", "error"]


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str
    pinned: bool = False
    pinned_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    pinned: bool | None = None

    @field_validator("title")
    @classmethod
    def _clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Title must not be empty.")
        return cleaned

    @model_validator(mode="after")
    def _something_to_change(self) -> ConversationUpdate:
        if self.title is None and self.pinned is None:
            raise ValueError("Provide a title or a pinned value.")
        return self


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
