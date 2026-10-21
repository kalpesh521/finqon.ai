from datetime import date, datetime
from zoneinfo import ZoneInfo

from finqon.agent.date_ranges import compute_date_ranges, today_in


def ranges(today: date) -> dict[str, tuple[date, date]]:
    return {r.label: (r.start, r.end) for r in compute_date_ranges(today)}


def find(table: dict[str, tuple[date, date]], prefix: str) -> tuple[date, date]:
    return next(v for k, v in table.items() if k.startswith(prefix))


def test_mid_october() -> None:
    table = ranges(date(2026, 10, 21))
    assert find(table, "this month") == (date(2026, 10, 1), date(2026, 10, 21))
    assert find(table, "last month") == (date(2026, 9, 1), date(2026, 9, 30))
    assert find(table, "last 30 days") == (date(2026, 9, 22), date(2026, 10, 21))
    assert find(table, "last 3 months") == (date(2026, 7, 1), date(2026, 9, 30))
    assert find(table, "this quarter") == (date(2026, 10, 1), date(2026, 10, 21))
    assert find(table, "last quarter") == (date(2026, 7, 1), date(2026, 9, 30))
    assert find(table, "this financial year") == (date(2026, 4, 1), date(2026, 10, 21))
    assert find(table, "last financial year") == (date(2025, 4, 1), date(2026, 3, 31))


def test_january_belongs_to_the_previous_financial_year() -> None:
    table = ranges(date(2027, 1, 15))
    assert find(table, "last month") == (date(2026, 12, 1), date(2026, 12, 31))
    assert find(table, "this quarter") == (date(2027, 1, 1), date(2027, 1, 15))
    assert find(table, "last quarter") == (date(2026, 10, 1), date(2026, 12, 31))
    assert find(table, "this financial year") == (date(2026, 4, 1), date(2027, 1, 15))


def test_first_of_april_starts_a_new_year() -> None:
    table = ranges(date(2027, 4, 1))
    assert find(table, "this financial year") == (date(2027, 4, 1), date(2027, 4, 1))
    assert find(table, "last financial year") == (date(2026, 4, 1), date(2027, 3, 31))
    assert find(table, "last quarter") == (date(2027, 1, 1), date(2027, 3, 31))


def test_financial_year_label_in_description() -> None:
    labels = [r.label for r in compute_date_ranges(date(2026, 10, 21))]
    assert any("FY2026-27" in label for label in labels)
    assert any("FY2025-26" in label for label in labels)


def test_today_uses_the_given_time_zone() -> None:
    kolkata = ZoneInfo("Asia/Kolkata")
    # 20:00 UTC on 20 Oct is already 21 Oct in Kolkata.
    now = datetime(2026, 10, 20, 20, 0, tzinfo=ZoneInfo("UTC"))
    assert today_in(kolkata, now) == date(2026, 10, 21)
