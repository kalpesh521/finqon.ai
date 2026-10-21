"""Expected figures recomputed from the seed CSVs with plain Python.

Shared by the integration tests and the CLI check so a mistake in a view definition cannot
hide behind a test that reuses the same SQL.
"""

from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

from finqon.core.config import Settings
from tests.conftest import OUTPUT_DIR


def rows(name: str) -> list[dict[str, str]]:
    with (OUTPUT_DIR / name).open(newline="") as handle:
        return list(csv.DictReader(handle))


def csv_spend(email: str, *, category: str | None = None, start=None, end=None) -> Decimal:
    owner = {r["id"]: r["user_email"] for r in rows("accounts.csv")}
    names = {r["id"]: r["name"] for r in rows("categories.csv")}
    total = Decimal(0)
    for row in rows("transactions.csv"):
        if owner[row["account_id"]] != email or row["txn_type"] == "transfer":
            continue
        if category and names.get(row["category_id"]) != category:
            continue
        day = dt.date.fromisoformat(row["txn_date"])
        if (start and day < start) or (end and day >= end):
            continue
        amount = Decimal(row["amount"])
        if row["direction"] == "debit":
            total += amount
        elif row["txn_type"] == "refund":
            total -= amount
    return total


def last_full_month(settings: Settings) -> tuple[dt.date, dt.date]:
    today = dt.datetime.now(ZoneInfo(settings.timezone)).date()
    end = today.replace(day=1)
    return (end - dt.timedelta(days=1)).replace(day=1), end
