from __future__ import annotations

from decimal import Decimal

import pytest

from finqon.ingest.classify import Alias, Classifier
from finqon.ingest.parser import ParseError, parse_amount, parse_date, parse_statement
from finqon.services.ingestion import Incoming, validate_incoming

SPLIT = (
    b"Date,Narration,Withdrawal Amt,Deposit Amt,Closing Balance\n"
    b"01/04/2026,UPI/SWIGGY/1234,500.00,,9500.00\n"
    b"02/04/2026,SALARY ACME LTD,,50000.00,59500.00\n"
    b"03/04/2026,ATM WDL MUMBAI,2000.00,,57500.00\n"
    b"04/04/2026,NETFLIX,649.00,,56851.00\n"
)


def test_split_columns_parse_and_reconcile() -> None:
    parsed = parse_statement("a.csv", SPLIT)
    assert [r.direction for r in parsed.rows] == ["debit", "credit", "debit", "debit"]
    assert parsed.rows[0].amount == Decimal("500.00")
    assert parsed.reconciled is True


def test_newest_first_still_reconciles() -> None:
    lines = SPLIT.splitlines()
    flipped = b"\n".join([lines[0], *reversed(lines[1:])])
    assert parse_statement("a.csv", flipped).reconciled is True


def test_bad_balances_are_flagged() -> None:
    broken = SPLIT.replace(b"57500.00", b"1.00").replace(b"59500.00", b"2.00")
    assert parse_statement("a.csv", broken).reconciled is False


def test_signed_amount_column_and_semicolons() -> None:
    data = b"Transaction Date;Description;Amount\n2026-04-01;Coffee;-120.50\n2026-04-02;Refund;80\n"
    rows = parse_statement("a.csv", data).rows
    assert (rows[0].direction, rows[0].amount) == ("debit", Decimal("120.50"))
    assert rows[1].direction == "credit"


def test_unknown_layout_is_rejected_with_message() -> None:
    with pytest.raises(ParseError, match="transaction columns"):
        parse_statement("a.csv", b"foo,bar\n1,2\n")


def test_junk_rows_are_skipped_not_fatal() -> None:
    data = SPLIT + b"TOTAL,,,,\n,,,,\n"
    parsed = parse_statement("a.csv", data)
    assert len(parsed.rows) == 4 and parsed.skipped == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("₹1,234.50", "1234.50"), ("(50)", "-50"), ("500 Dr", "-500"), ("1e999", None), ("abc", None)],
)
def test_parse_amount(raw: str, expected: str | None) -> None:
    got = parse_amount(raw)
    assert (str(got) if got is not None else None) == expected


def test_parse_date_formats_and_bounds() -> None:
    assert parse_date("05-Apr-2026").isoformat() == "2026-04-05"  # type: ignore[union-attr]
    assert parse_date("1850-01-01") is None


def _classifier() -> Classifier:
    return Classifier(
        [Alias("SWIGGY", "contains", 7)],
        {7: 3},
        {"Dining": 3, "Salary": 18, "Cash Withdrawal": 15},
        99,
    )


def test_alias_beats_keywords_and_unknown_falls_back() -> None:
    c = _classifier()
    swiggy = c.classify("UPI/SWIGGY/1234", "debit")
    assert (swiggy.merchant_id, swiggy.category_id, swiggy.payment_mode) == (7, 3, "upi")
    other = c.classify("RANDOM SHOP", "debit")
    assert other.merchant_id == 99 and other.category_id is None


def test_credits_never_violate_schema_types() -> None:
    c = _classifier()
    assert c.classify("SALARY ACME", "credit").txn_type == "salary"
    assert c.classify("Refund from shop", "credit").txn_type == "transfer"
    assert c.classify("SALARY REVERSAL", "debit").txn_type == "transfer"


def test_upload_validation() -> None:
    assert validate_incoming(Incoming("a.exe", b"x")) is not None
    assert validate_incoming(Incoming("a.pdf", b"not a pdf")) is not None
    assert validate_incoming(Incoming("a.csv", b"")) is not None
    assert validate_incoming(Incoming("a.csv", b"x")) is None
