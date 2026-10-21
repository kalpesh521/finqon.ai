"""Date ranges computed in Python so the model never guesses what "last month" means.

The financial year runs 1 April to 31 March. Its quarters start in April, July, October
and January, which are also the calendar quarter boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class DateRange:
    label: str
    start: date
    end: date  # inclusive; for the current period this is today

    def render(self) -> str:
        return f"{self.label}: {self.start.isoformat()} to {self.end.isoformat()} (inclusive)"


def today_in(tz: ZoneInfo, now: datetime | None = None) -> date:
    return (now or datetime.now(tz)).astimezone(tz).date()


def _month_start(value: date, offset: int = 0) -> date:
    index = value.year * 12 + value.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def _quarter_start(value: date) -> date:
    return date(value.year, 3 * ((value.month - 1) // 3) + 1, 1)


def _financial_year_start(value: date) -> date:
    year = value.year if value.month >= 4 else value.year - 1
    return date(year, 4, 1)


def _fy_label(start: date) -> str:
    return f"FY{start.year}-{(start.year + 1) % 100:02d}"


def compute_date_ranges(today: date) -> list[DateRange]:
    this_month = _month_start(today)
    last_month = _month_start(today, -1)
    this_quarter = _quarter_start(today)
    last_quarter = _month_start(this_quarter, -3)
    this_fy = _financial_year_start(today)
    last_fy = date(this_fy.year - 1, 4, 1)

    return [
        DateRange("today", today, today),
        DateRange("this month (to date)", this_month, today),
        DateRange("last month", last_month, this_month - timedelta(days=1)),
        DateRange("last 30 days", today - timedelta(days=29), today),
        DateRange(
            "last 3 months (three full calendar months before this month)",
            _month_start(today, -3),
            this_month - timedelta(days=1),
        ),
        DateRange("this quarter (to date)", this_quarter, today),
        DateRange("last quarter", last_quarter, this_quarter - timedelta(days=1)),
        DateRange(f"this financial year {_fy_label(this_fy)} (to date)", this_fy, today),
        DateRange(
            f"last financial year {_fy_label(last_fy)}",
            last_fy,
            this_fy - timedelta(days=1),
        ),
    ]
