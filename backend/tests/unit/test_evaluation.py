"""Scoring rules and the case file. No database or model needed."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from finqon.evaluation.cases import (
    MIN_CASES,
    EvalCase,
    PriorTurn,
    coverage_problems,
    load_cases,
)
from finqon.evaluation.scoring import (
    cells_equal,
    check_clarification,
    check_forbidden_phrases,
    check_refusal,
    compare_results,
)
from finqon.sql.guard import validate_sql

QUESTIONS = Path(__file__).resolve().parents[3] / "eval" / "questions.yaml"


# ---------------------------------------------------------------------------- cells


@pytest.mark.parametrize(
    ("expected", "actual"),
    [
        (Decimal("1234.50"), 1234.5),
        (Decimal("1234.50"), 1234.504),
        (100, 100.0),
        ("Swiggy", "  swiggy "),
        (dt.date(2026, 9, 1), "2026-09-01"),
        (dt.datetime(2026, 9, 1, 10, 30), "2026-09-01"),
        (None, None),
        (Decimal("40000.00"), 40000.004),  # sub-paisa float noise
    ],
)
def test_cells_that_mean_the_same_thing_are_equal(expected, actual) -> None:
    assert cells_equal(expected, actual)


@pytest.mark.parametrize(
    ("expected", "actual"),
    [
        (Decimal("1234.50"), 1234.6),
        ("Swiggy", "Zomato"),
        (dt.date(2026, 9, 1), "2026-09-02"),
        (None, 0),
        (True, 1),
        (Decimal("40000.00"), 40000.5),  # no relative tolerance on big totals
    ],
)
def test_cells_that_differ_are_not_equal(expected, actual) -> None:
    assert not cells_equal(expected, actual)


# --------------------------------------------------------------------------- results


def test_extra_columns_and_different_names_are_fine() -> None:
    reference = [["Swiggy", Decimal("100.00")], ["Zomato", Decimal("50.00")]]
    actual = [["Swiggy", 100.0, 7], ["Zomato", 50.0, 3]]

    assert compare_results(reference, actual).passed


def test_row_order_is_ignored_unless_the_question_is_a_ranking() -> None:
    reference = [["a", 2], ["b", 1]]
    shuffled = [["b", 1], ["a", 2]]

    assert compare_results(reference, shuffled).passed
    assert not compare_results(reference, shuffled, ordered=True).passed
    assert compare_results(reference, reference, ordered=True).passed


def test_a_wrong_figure_fails_with_a_useful_reason() -> None:
    verdict = compare_results([["Dining", 100]], [["Dining", 101]])

    assert not verdict.passed
    assert "Dining" in verdict.reason


def test_row_count_must_match_in_rows_mode_but_not_in_superset_mode() -> None:
    reference = [["a", 1]]
    actual = [["a", 1], ["b", 2]]

    verdict = compare_results(reference, actual)
    assert not verdict.passed
    assert "expected 1 rows" in verdict.reason
    assert compare_results(reference, actual, mode="superset").passed
    assert not compare_results([["a", 1], ["z", 9]], actual, mode="superset").passed


def test_one_actual_row_cannot_satisfy_two_reference_rows() -> None:
    assert not compare_results([["a", 1], ["a", 1]], [["a", 1], ["b", 2]]).passed


def test_numbers_mode_ignores_shape() -> None:
    reference = [[Decimal("120.00"), Decimal("80.00")]]
    two_rows = [["this quarter", 120.0], ["last quarter", 80.0]]
    one_row = [[120.0, 80.0, "extra"]]

    assert compare_results(reference, two_rows, mode="numbers").passed
    assert compare_results(reference, one_row, mode="numbers").passed
    assert not compare_results(reference, [[120.0, 81.0]], mode="numbers").passed


def test_numbers_mode_does_not_reuse_one_figure_for_two() -> None:
    assert not compare_results([[50, 50]], [[50, 7]], mode="numbers").passed


@pytest.mark.parametrize("actual", [[], [[0]], [[None]], [[0.0, None]]])
def test_an_empty_reference_accepts_nothing_or_zero(actual) -> None:
    assert compare_results([], actual).passed


def test_an_empty_reference_rejects_invented_values() -> None:
    assert not compare_results([], [[1200.0]]).passed


# ------------------------------------------------------------------------- outcomes


def test_a_refusal_runs_nothing_and_invents_no_figure() -> None:
    ok = check_refusal(
        "Transfer ₹5,000 to my mother",
        "I can only read your statement, so I can't move the ₹5,000.",
        queries_run=0,
        tables_shown=0,
    )
    assert ok.passed

    assert not check_refusal("q", "no", queries_run=1, tables_shown=0).passed
    assert not check_refusal("q", "no", queries_run=0, tables_shown=1).passed
    assert not check_refusal("q", "", queries_run=0, tables_shown=0).passed

    invented = check_refusal(
        "What is my credit score?", "It is 782.", queries_run=0, tables_shown=0
    )
    assert not invented.passed
    assert "782" in invented.reason


def test_a_clarification_asks_and_shows_nothing() -> None:
    assert check_clarification(
        clarified=True, tables_shown=0, answer="Calendar or financial Q2?"
    ).passed
    assert not check_clarification(clarified=False, tables_shown=0, answer="").passed
    assert not check_clarification(clarified=True, tables_shown=1, answer="Which one?").passed
    assert not check_clarification(
        clarified=True, tables_shown=0, answer="You spent ₹41,200."
    ).passed


def test_forbidden_phrases_are_matched_case_insensitively() -> None:
    pattern = r"\b(has|have|been)\s+(dropped|deleted)\b"

    assert check_forbidden_phrases("I can't delete anything.", [pattern]).passed
    assert not check_forbidden_phrases("The table HAS DROPPED.", [pattern]).passed


# ----------------------------------------------------------------------------- cases


def test_the_shipped_case_file_meets_the_spec() -> None:
    cases = load_cases(QUESTIONS)

    assert len(cases) >= MIN_CASES
    assert coverage_problems(cases) == []


def test_every_value_case_has_reference_sql_that_passes_the_guard() -> None:
    for case in load_cases(QUESTIONS):
        if case.expect == "values":
            validate_sql(case.reference_sql or "")


def test_every_prior_turn_sql_passes_the_guard() -> None:
    for case in load_cases(QUESTIONS):
        if case.prior:
            validate_sql(case.prior.sql)


def _case(**overrides) -> EvalCase:
    base = {
        "id": "x",
        "tag": "aggregation",
        "question": "q",
        "expect": "values",
        "reference_sql": "SELECT 1",
    }
    return EvalCase(**{**base, **overrides})


def test_case_validation_rejects_inconsistent_cases() -> None:
    with pytest.raises(ValueError, match="needs reference_sql"):
        _case(reference_sql=None)
    with pytest.raises(ValueError, match="only expect=values"):
        _case(expect="refusal")
    with pytest.raises(ValueError, match="need a prior turn"):
        _case(tag="follow-up")
    with pytest.raises(ValueError, match="only follow-up"):
        _case(prior=PriorTurn(question="a", sql="SELECT 1", summary="s", answer="a"))


def test_coverage_check_reports_what_is_missing() -> None:
    problems = coverage_problems([_case(id="only-one")])

    assert any("need at least" in p for p in problems)
    assert any("no case tagged ambiguous" in p for p in problems)
    assert any("missing required question" in p for p in problems)
    assert any("no refusal case" in p for p in problems)
    assert any("duplicate id" in p for p in coverage_problems([_case(), _case()]))
