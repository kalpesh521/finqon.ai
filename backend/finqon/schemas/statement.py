"""Coverage of the signed-in user's statement. Dates are calendar days, not instants."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class StatementSummary(BaseModel):
    transaction_count: int = Field(ge=0)
    covered_from: dt.date | None
    covered_to: dt.date | None
