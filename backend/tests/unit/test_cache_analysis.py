"""The constraint signature: paraphrases may share an entry, different figures may not.

A false hit returns a wrong financial figure, so most of these tests are about questions that
*sound* alike and must not match.
"""

from __future__ import annotations

import pytest

from finqon.cache.analysis import analyse, sql_is_time_relative


def sig(question: str) -> str:
    return analyse(question).signature


@pytest.mark.parametrize(
    "paraphrase",
    [
        "How much did I spend on food last month?",
        "What was my total food spending last month?",
        "Show my food expenses from last month.",
        "how much did i spend on food in the previous month",
        "Total spent on Food last month, please",
        "What did I spend on food last month",
    ],
)
def test_paraphrases_share_a_signature(paraphrase: str) -> None:
    assert sig(paraphrase) == sig("How much did I spend on food last month?")


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # a different month, year, or relative period
        ("How much did I spend on food in September?", "How much did I spend on food in October?"),
        ("food spend in September 2025", "food spend in September 2026"),
        ("food spend last month", "food spend this month"),
        ("food spend last month", "food spend last 3 months"),
        ("food spend last 3 months", "food spend last 6 months"),
        ("food spend in Q2", "food spend in Q3"),
        ("food spend in FY2025-26", "food spend in FY2024-25"),
        ("spend on 2026-09-01", "spend on 2026-09-02"),
        ("spend since January", "spend until January"),
        # a different category or merchant
        ("How much on food last month", "How much on groceries last month"),
        ("spend at swiggy last month", "spend at zomato last month"),
        ("spend by category", "spend by merchant"),
        # a different direction of money
        ("how much did I spend in September", "how much did I earn in September"),
        ("salary in September", "refunds in September"),
        ("how much did I spend", "how much did I withdraw"),
        # a different calculation
        ("total food spend", "average food spend"),
        ("total food spend", "how many food transactions"),
        ("total food spend", "list food transactions"),
        ("top merchants", "bottom merchants"),
        ("top 5 merchants", "top 10 merchants"),
        ("top 5 merchants", "top five merchants by spend"),
        ("monthly food spend", "total food spend"),
        # thresholds and negation
        ("transactions above 5000", "transactions below 5000"),
        ("transactions above 5000", "transactions above 6000"),
        ("food spend excluding swiggy", "food spend including swiggy"),
        ("everything except food", "everything food"),
        ("spend not on food", "spend on food"),
    ],
)
def test_different_constraints_never_share_a_signature(a: str, b: str) -> None:
    assert sig(a) != sig(b), f"{a!r} and {b!r} must not be interchangeable"


def test_direction_of_a_comparison_matters() -> None:
    more = sig("Did I spend more on swiggy than zomato?")
    reverse = sig("Did I spend more on zomato than swiggy?")
    assert more != reverse


def test_amounts_are_canonical() -> None:
    assert sig("transactions above 5k") == sig("transactions above 5000")
    assert sig("transactions above ₹5,000") == sig("transactions above 5000")
    assert sig("transactions above 1 lakh") == sig("transactions above 100000")
    assert sig("top five merchants") == sig("top 5 merchants")


def test_case_punctuation_and_whitespace_do_not_matter() -> None:
    assert sig("  HOW MUCH did I SPEND on Food,   last month ??? ") == sig(
        "how much did i spend on food last month"
    )


def test_unknown_words_are_entities_not_noise() -> None:
    # "mother" is not in any vocabulary, so it must change the signature.
    assert sig("how much did my mother spend on food last month") != sig(
        "how much did I spend on food last month"
    )


def test_canonical_text_is_stable_and_punctuation_free() -> None:
    assert analyse("How much did I spend on Food, last month?").text == (
        "how much did i spend on food last month"
    )


# ------------------------------------------------------------------ time dependence


@pytest.mark.parametrize(
    "question",
    [
        "food spend last month",
        "what did I spend this week",
        "spend yesterday",
        "recent transactions",
        "food spend in September",  # which year? resolved from today
        "spend in Q3",
        "spend in the financial year",
        "year to date spend on food",
        "spend in the last 30 days",
    ],
)
def test_questions_resolved_from_todays_date_are_time_relative(question: str) -> None:
    assert analyse(question).time_relative


@pytest.mark.parametrize(
    "question",
    [
        "top 5 merchants by spend",
        "food spend in September 2026",
        "spend on 2026-09-01",
        "spend in FY2025-26",
        "total salary received",
    ],
)
def test_questions_with_absolute_scope_are_not_time_relative(question: str) -> None:
    assert not analyse(question).time_relative


@pytest.mark.parametrize(
    ("sql", "relative"),
    [
        ("SELECT 1 WHERE d >= date_trunc('month', current_date)", True),
        ("SELECT 1 WHERE d >= now() - interval '7 days'", True),
        ("SELECT 1 WHERE d >= CURRENT_TIMESTAMP", True),
        ("SELECT 1 WHERE d >= DATE '2026-09-01' AND d < DATE '2026-10-01'", False),
        ("SELECT 1 FROM reporting.spend WHERE category_name = 'current_dates'", False),
    ],
)
def test_sql_that_reads_the_clock_is_time_relative(sql: str, relative: bool) -> None:
    assert sql_is_time_relative(sql) is relative


# --------------------------------------------------------- conversation dependence


@pytest.mark.parametrize(
    "question",
    [
        "and what about the month before?",
        "What about December?",
        "how about swiggy",
        "same for last year",
        "show me more of them",
        "what was that merchant again",
        "December",
        "and November?",
        "why is it so high",
        "break that down by category",
        "what was the previous question",
    ],
)
def test_follow_ups_are_never_cached(question: str) -> None:
    assert analyse(question).context_dependent
    assert not analyse(question).cacheable


@pytest.mark.parametrize(
    "question",
    [
        "How much did I spend on food last month?",
        "top 5 merchants by spend",
        "transactions above 5000 in September 2026",
    ],
)
def test_standalone_questions_are_cacheable(question: str) -> None:
    assert analyse(question).cacheable
