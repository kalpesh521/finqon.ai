"""Uploaded statement files: the shape the UI polls while a job runs."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, Field

FileStatus = Literal["queued", "processing", "ready", "failed", "quarantined"]


class FileRecord(BaseModel):
    id: uuid.UUID
    filename: str
    size_bytes: int
    status: FileStatus
    stage: str = Field(description="Human-readable step of the ingestion job.")
    progress: int = Field(ge=0, le=100)
    rows_imported: int
    rows_skipped: int
    date_from: dt.date | None
    date_to: dt.date | None
    warnings: list[str]
    error: str | None
    created_at: dt.datetime
    completed_at: dt.datetime | None
