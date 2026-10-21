from decimal import Decimal

import pytest

from finqon.agent.grounding import dates_in, extract_claims, numbers_in, verify_answer

D = Decimal


def ok(answer: str, *allowed: str, evidence: str = "") -> bool:
    return verify_answer(answer, [D(a) for a in allowed], evidence=evidence).ok


def test_exact_and_rounded_amounts_match() -> None:
    assert ok("You spent ₹12,345.67 on dining.", "12345.67")
    assert ok("You spent ₹12,346 on dining.", "12345.67")
    assert ok("About ₹12,345.7 on dining.", "12345.67")
    assert ok("Rs. 1,23,456.00 in total.", "123456")


def test_units_are_scaled() -> None:
    assert ok("Roughly ₹1.2 lakh.", "120000")
    assert ok("Around 12.3k.", "12345")
    assert not ok("About ₹2 lakh.", "120000")


def test_wrong_figure_is_flagged() -> None:
    result = verify_answer("Dining cost ₹9,999.", [D("12345.67")])
    assert not result.ok
    assert result.ungrounded == ["₹9,999"]


def test_percentages_must_come_from_results() -> None:
    assert ok("Groceries were 23.4% of spend.", "23.4")
    assert not ok("Groceries were 23.4% of spend.", "23.5")


def test_negative_values_match_by_magnitude() -> None:
    assert ok("Spend fell by ₹1,200.50.", "-1200.50")


def test_years_dates_and_small_counts_are_not_numeric_claims() -> None:
    answer = "In September 2026 (FY2026-27, 2026-09-14) your top 5 merchants, 3 of them food."
    assert extract_claims(answer) == []
    assert ok("On 14 September you paid for 2 items.")
    assert ok("In the 1st quarter of FY2026-27.")


def test_iso_dates_must_appear_in_the_tool_result() -> None:
    evidence = "2026-05-25 POS 1378 AVENUE SUPERMARTS PUNE 909.39"
    assert dates_in("purchase on 2026-05-25 for ₹909.39") == ["2026-05-25"]
    assert ok("POS on 2026-05-25 for ₹909.39 mentioned Pune.", "909.39", evidence=evidence)
    wrong = verify_answer(
        "POS on 2025-05-25 for ₹909.39 mentioned Pune.",
        [D("909.39")],
        evidence=evidence,
    )
    assert not wrong.ok
    assert "2025-05-25" in wrong.ungrounded


def test_counts_above_ten_are_checked() -> None:
    assert not ok("You made 42 transactions.", "41")
    assert ok("You made 42 transactions.", "42")


def test_numbers_from_question_and_sql_can_be_quoted() -> None:
    allowed = numbers_in("Show transactions above ₹5,000") + numbers_in("WHERE amount > 5000")
    assert verify_answer("Transactions above ₹5,000:", allowed).ok


def test_answer_without_numbers_is_always_grounded() -> None:
    assert ok("I can only read your statement, so nothing was changed.")


@pytest.mark.parametrize("text", ["₹0", "₹0.00"])
def test_zero_requires_a_zero_in_results(text: str) -> None:
    assert not ok(f"You spent {text}.", "5")
    assert ok(f"You spent {text}.", "0")
