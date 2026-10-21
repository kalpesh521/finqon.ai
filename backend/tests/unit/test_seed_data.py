"""Invariants of the generated ledger. Reads the CSVs only, so no database is needed."""

from __future__ import annotations

import csv
from collections import Counter
from decimal import Decimal

import pytest

from tests.conftest import ISOLATION_EMAIL, OUTPUT_DIR, PRIMARY_EMAIL

pytestmark = pytest.mark.skipif(
    not (OUTPUT_DIR / "transactions.csv").exists(),
    reason="run dataset/generate_ledger.py first",
)


def _read(name: str) -> list[dict[str, str]]:
    with (OUTPUT_DIR / name).open(newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def ledger() -> dict[str, list[dict[str, str]]]:
    owner = {row["id"]: row["user_email"] for row in _read("accounts.csv")}
    by_user: dict[str, list[dict[str, str]]] = {}
    for row in _read("transactions.csv"):
        by_user.setdefault(owner[row["account_id"]], []).append(row)
    return by_user


def test_the_primary_user_has_enough_history(ledger) -> None:
    rows = ledger[PRIMARY_EMAIL]
    assert len(rows) >= 500
    dates = sorted(row["txn_date"] for row in rows)
    assert (dates[-1] > dates[0]) and dates[0][:7] == "2026-04"


def test_both_users_exist_and_are_different_in_size(ledger) -> None:
    assert set(ledger) == {PRIMARY_EMAIL, ISOLATION_EMAIL}
    assert len(ledger[PRIMARY_EMAIL]) != len(ledger[ISOLATION_EMAIL])


@pytest.mark.parametrize("email", [PRIMARY_EMAIL, ISOLATION_EMAIL])
def test_the_running_balance_is_continuous_and_never_negative(ledger, email: str) -> None:
    """A savings account cannot go below zero; each balance follows from the last."""
    rows = sorted(ledger[email], key=lambda r: int(r["id"]))
    previous = None
    for row in rows:
        balance = Decimal(row["balance_after"])
        assert balance >= 0, f"{email} overdrawn on {row['txn_date']} (txn {row['id']})"
        if previous is not None:
            amount = Decimal(row["amount"])
            delta = amount if row["direction"] == "credit" else -amount
            assert balance - previous == delta, f"balance jumps at txn {row['id']}"
        previous = balance


def test_amounts_are_unsigned_and_direction_carries_the_sign(ledger) -> None:
    for rows in ledger.values():
        assert all(Decimal(row["amount"]) > 0 for row in rows)
        assert {row["direction"] for row in rows} == {"debit", "credit"}


def test_every_refund_points_at_an_earlier_purchase_it_does_not_exceed(ledger) -> None:
    for rows in ledger.values():
        by_id = {row["id"]: row for row in rows}
        refunds = [row for row in rows if row["txn_type"] == "refund"]
        assert refunds
        for refund in refunds:
            original = by_id[refund["refund_of_txn_id"]]
            assert original["direction"] == "debit"
            assert original["txn_date"] <= refund["txn_date"]
            assert Decimal(refund["amount"]) <= Decimal(original["amount"])
            assert original["merchant_id"] == refund["merchant_id"]


def test_the_injection_description_exists_once_and_only_for_the_primary_user(ledger) -> None:
    hits = {
        email: [row for row in rows if "IGNORE ALL PREVIOUS INSTRUCTIONS" in row["raw_description"]]
        for email, rows in ledger.items()
    }
    assert len(hits[PRIMARY_EMAIL]) == 1
    assert hits[ISOLATION_EMAIL] == []
    assert "DROP TABLE" in hits[PRIMARY_EMAIL][0]["raw_description"]


def test_every_cleaned_transaction_has_a_merchant_and_category(ledger) -> None:
    for rows in ledger.values():
        assert all(row["merchant_id"] and row["category_id"] for row in rows)


def test_transfers_and_salary_are_present_so_the_spend_rules_matter(ledger) -> None:
    types = Counter(row["txn_type"] for row in ledger[PRIMARY_EMAIL])
    assert types["transfer"] >= 6
    assert types["salary"] >= 6
    assert types["emi"] >= 6
