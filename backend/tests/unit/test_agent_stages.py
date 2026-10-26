"""The pipeline stages around the model: ambiguity gate, context retriever, error fixer."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from finqon.agent.ambiguity import DANGLING_OPTIONS, check_ambiguity
from finqon.agent.catalog import Category, ColumnInfo, SchemaCatalog, ViewInfo
from finqon.agent.context import retrieve_context
from finqon.agent.error_fixer import diagnose
from finqon.agent.prompt_builder import HistoryTurn, build_system_prompt
from finqon.sql.results import QueryExecutionError
from tests.conftest import ScriptedLLM, sql_reply, text_reply
from tests.unit.test_orchestrator import (
    DINING_SQL,
    FakeSql,
    Harness,
    answer_text,
    result,
    types,
)


def cols(*names: str) -> tuple[ColumnInfo, ...]:
    return tuple(ColumnInfo(n, "text", f"{n.replace('_', ' ')}.") for n in names)


CATALOG = SchemaCatalog(
    views=(
        ViewInfo(
            "reporting.transactions",
            "Every transaction, credits and debits.",
            cols("account_name", "txn_date", "month", "fy", "merchant_name", "category_name")
            + cols("amount", "raw_description"),
        ),
        ViewInfo(
            "reporting.spend",
            "The only definition of spend.",
            cols("txn_date", "merchant_name", "category_name", "spend_amount", "account_name"),
        ),
        ViewInfo(
            "reporting.monthly_category_spend",
            "Spend per month and category.",
            cols("month", "category_name", "total_spend"),
        ),
    ),
    categories=(
        Category("Dining", "expense"),
        Category("Groceries", "expense"),
        Category("Salary", "income"),
    ),
)


def turn(question: str, answer: str = "") -> HistoryTurn:
    return HistoryTurn(question, None, None, answer)


# ---------------------------------------------------------------------- ambiguity gate


@pytest.mark.parametrize(
    ("question", "number", "fy_months", "cal_months"),
    [
        ("How much did I spend in Q3?", 3, "Oct-Dec", "Jul-Sep"),
        ("spending in q1", 1, "Apr-Jun", "Jan-Mar"),
        ("what did I spend in the second quarter", 2, "Jul-Sep", "Apr-Jun"),
        ("total for quarter 4", 4, "Jan-Mar", "Oct-Dec"),
    ],
)
def test_a_bare_quarter_is_ambiguous(
    question: str, number: int, fy_months: str, cal_months: str
) -> None:
    found = check_ambiguity(question)
    assert found is not None and found.reason == "quarter_year_kind"
    assert found.question == f"Which Q{number} do you mean?"
    assert found.options == (
        f"Financial-year Q{number} ({fy_months})",
        f"Calendar Q{number} ({cal_months})",
    )


@pytest.mark.parametrize(
    "question",
    [
        "Spend in financial year Q3",
        "spend in FY26 Q2",
        "spend in fy 2026 q2",
        "calendar Q3 total",
        "fiscal Q1 spend",
        "Financial-year Q3 (Oct-Dec)",  # the user's own answer to the question
        "How much did I spend last quarter?",  # computed range, not a named quarter
        "spend in q5",
    ],
)
def test_a_quarter_with_its_year_kind_is_not_ambiguous(question: str) -> None:
    assert check_ambiguity(question) is None


def test_a_quarter_chosen_earlier_carries_over() -> None:
    history = [turn("spend in Q3", "Which Q3 do you mean? (Financial-year Q3 / Calendar Q3)")]
    assert check_ambiguity("and Q4?", history=history) is None
    unrelated = [turn("top merchants", "Here they are.")]
    assert check_ambiguity("and Q4?", history=unrelated) is not None


@pytest.mark.parametrize("question", ["and last month?", "What about last month", "same for May?"])
def test_a_follow_up_with_nothing_to_follow_is_ambiguous(question: str) -> None:
    found = check_ambiguity(question)
    assert found is not None and found.reason == "dangling_follow_up"
    assert found.options == DANGLING_OPTIONS


@pytest.mark.parametrize(
    "question",
    [
        "What about my dining spend?",  # has a subject of its own
        "and groceries?",  # a category name is a subject
        "how much did I spend last month",  # not a follow-up form
        "and what about last month's average spending on everything I bought?",  # long
    ],
)
def test_self_contained_questions_are_not_dangling(question: str) -> None:
    assert check_ambiguity(question, category_names=["Dining", "Groceries"]) is None


def test_a_follow_up_with_history_or_previous_sql_is_not_dangling() -> None:
    assert check_ambiguity("and last month?", history=[turn("spend?", "₹1")]) is None
    assert check_ambiguity("and last month?", previous_sql="SELECT 1") is None


async def test_the_gate_asks_without_calling_the_model_or_the_database() -> None:
    llm = ScriptedLLM([])  # any model call would raise
    harness = Harness(llm)
    events = await harness.ask("How much did I spend in Q3?")

    assert types(events) == ["clarification", "done"]
    assert events[0].options == [
        "Financial-year Q3 (Oct-Dec)",
        "Calendar Q3 (Jul-Sep)",
    ]
    assert llm.calls == [] and harness.sql.executed == []
    assert harness.saved[0].status == "clarification"
    assert "Which Q3" in harness.saved[0].answer and harness.saved[0].usage.total_tokens == 0


async def test_the_gate_can_be_turned_off() -> None:
    llm = ScriptedLLM([text_reply("I only read statements.")])
    harness = Harness(llm, clarify_before_model=False)
    events = await harness.ask("How much did I spend in Q3?")
    assert "clarification" not in types(events) and len(llm.calls) == 1


async def test_a_resolved_quarter_goes_to_the_model() -> None:
    llm = ScriptedLLM([text_reply("I only read statements.")])
    harness = Harness(llm)
    events = await harness.ask("How much did I spend in financial-year Q3?")
    assert "clarification" not in types(events) and len(llm.calls) == 1


# -------------------------------------------------------------------- context retriever


def test_views_are_ranked_for_the_question() -> None:
    def first(question: str) -> str:
        return retrieve_context(question, CATALOG).views[0].name

    assert first("How much did I spend on dining?") == "reporting.spend"
    assert first("Show my monthly spending trend") == "reporting.monthly_category_spend"
    assert first("List my largest transactions and my salary") == "reporting.transactions"


def test_top_k_limits_the_schema_in_the_prompt() -> None:
    narrow = retrieve_context("How much did I spend on dining?", CATALOG, top_k=1)
    assert [v.name for v in narrow.views] == ["reporting.spend"]
    prompt = build_system_prompt(CATALOG, date(2026, 10, 21), [], None, context=narrow)
    assert "reporting.spend:" in prompt
    assert "reporting.monthly_category_spend:" not in prompt
    assert retrieve_context("x", CATALOG, top_k=0).views  # never empty


def test_the_users_words_are_mapped_to_categories() -> None:
    context = retrieve_context("what did I spend on eating out and the supermarket?", CATALOG)
    targets = {m.phrase: m.target for m in context.mappings}
    assert targets["eating out"] == "category_name = 'Dining'"
    assert targets["supermarket"] == "category_name = 'Groceries'"


def test_income_categories_say_they_are_not_spend() -> None:
    context = retrieve_context("what was my salary last month", CATALOG)
    target = next(m.target for m in context.mappings if m.phrase == "salary")
    assert "category_name = 'Salary'" in target and "reporting.transactions" in target


def test_a_category_missing_from_the_catalog_gives_no_hint() -> None:
    catalog = SchemaCatalog(views=CATALOG.views, categories=(Category("Groceries", "expense"),))
    context = retrieve_context("spend on restaurants", catalog)
    assert not [m for m in context.mappings if "Dining" in m.target]


def test_words_are_mapped_to_columns_that_exist() -> None:
    context = retrieve_context("which merchant and which account?", CATALOG)
    targets = {m.phrase: m.target for m in context.mappings}
    # reporting.spend ranks first for "merchant", and it has both columns.
    assert targets["merchant"] == "reporting.spend.merchant_name"
    assert targets["account"] == "reporting.spend.account_name"
    bare = SchemaCatalog(
        views=(ViewInfo("reporting.spend", "s", cols("spend_amount")),), categories=()
    )
    assert retrieve_context("which merchant?", bare).mappings == ()


def test_context_is_deterministic_and_has_a_prompt_section() -> None:
    question = "spend on eating out by merchant"
    assert retrieve_context(question, CATALOG) == retrieve_context(question, CATALOG)
    section = retrieve_context(question, CATALOG).render()
    assert section.startswith("## Retrieved context for this question")
    assert "\"eating out\" -> category_name = 'Dining'" in section
    assert (
        retrieve_context("hello", SchemaCatalog(views=CATALOG.views[:1], categories=())).render()
        == ""
    )


async def test_the_model_receives_the_retrieved_context() -> None:
    llm = ScriptedLLM([text_reply("I only read statements.")])
    await Harness(llm).ask("what did I spend on eating out?")
    system = llm.calls[0][0]["content"]
    assert "## Retrieved context for this question" in system
    assert "category_name = 'Dining'" in system


# ------------------------------------------------------------------------ error fixer


@pytest.mark.parametrize(
    ("message", "expected_kind", "expected_text"),
    [
        ('column "amt" does not exist', "unknown_column", "spend_amount"),
        ('column "amoun" does not exist', "unknown_column", "amount"),
        ("column s.categroy_name does not exist", "unknown_column", "category_name"),
        ('relation "reporting.spnd" does not exist', "unknown_relation", "reporting.spend"),
        ('relation "accounts" does not exist', "unknown_relation", "Query only"),
        ("division by zero", "division_by_zero", "NULLIF"),
        (
            'column "x" must appear in the GROUP BY clause or be used in an aggregate function',
            "group_by",
            "GROUP BY",
        ),
        ("aggregate functions are not allowed in WHERE", "aggregate_in_where", "HAVING"),
        ('column reference "txn_date" is ambiguous', "ambiguous_column", "alias"),
        ("operator does not exist: date >= text", "type_mismatch", "::date"),
        ('invalid input syntax for type date: "last month"', "bad_literal", "YYYY-MM-DD"),
        ("The query exceeded the time limit.", "timeout", "date range"),
        ('syntax error at or near ","', "syntax", "near ','"),
        ("The query is not valid PostgreSQL: Expecting )", "syntax", "parentheses"),
        ("Permission denied. Query only reporting.spend", "permission", "reporting.transactions"),
    ],
)
def test_known_errors_get_a_specific_hint(
    message: str, expected_kind: str, expected_text: str
) -> None:
    diagnosis = diagnose(message, "SELECT amt FROM reporting.spend", CATALOG)
    assert diagnosis is not None
    assert diagnosis.kind == expected_kind
    assert expected_text in diagnosis.hint


def test_the_hint_looks_only_at_views_the_sql_names() -> None:
    sql = "SELECT total FROM reporting.monthly_category_spend"
    hint = diagnose('column "totl" does not exist', sql, CATALOG)
    assert hint is not None and "total_spend (reporting.monthly_category_spend)" in hint.hint


def test_an_unmatchable_column_lists_what_exists() -> None:
    hint = diagnose('column "zzzz" does not exist', "SELECT zzzz FROM reporting.spend", CATALOG)
    assert hint is not None
    assert "spend_amount" in hint.hint and "reporting.monthly_category_spend" not in hint.hint


def test_unknown_errors_and_missing_catalog_are_handled() -> None:
    assert diagnose("something nobody has seen", "SELECT 1", CATALOG) is None
    hint = diagnose('column "amt" does not exist', "SELECT amt FROM reporting.spend", None)
    assert hint is not None and "listed in the schema" in hint.hint


async def test_the_repair_message_keeps_the_error_and_adds_the_hint() -> None:
    llm = ScriptedLLM(
        [
            sql_reply("SELECT amt FROM reporting.spend", call_id="a"),
            sql_reply(DINING_SQL, call_id="b"),
            text_reply("You spent ₹10 on dining."),
        ]
    )
    sql = FakeSql(
        [QueryExecutionError('column "amt" does not exist'), result([("t", "number")], [[10.0]])]
    )
    harness = Harness(llm, sql)
    events = await harness.ask("dining?")

    payload: dict[str, Any] = json.loads(llm.calls[1][-1]["content"])
    assert payload["error"] == 'column "amt" does not exist'  # unchanged
    assert "spend_amount" in payload["hint"]
    assert payload["instruction"]
    assert answer_text(events) == "You spent ₹10 on dining."
    assert harness.saved[0].status == "answered"


async def test_syntax_rejections_get_hints_and_forbidden_ones_do_not() -> None:
    llm = ScriptedLLM(
        [sql_reply("SELECT sum( FROM reporting.spend", call_id="a"), text_reply("Sorry.")]
    )
    harness = Harness(llm)
    await harness.ask("dining?")
    payload = json.loads(llm.calls[1][-1]["content"])
    assert "not valid PostgreSQL" in payload["error"] and "parentheses" in payload["hint"]
    assert harness.sql.executed == []

    # An attempt to write or escape is never "repaired", so it gets no help either.
    llm = ScriptedLLM([sql_reply("DROP TABLE ledger.transactions"), text_reply("Read only.")])
    await Harness(llm).ask("drop it")
    forbidden = json.loads(llm.calls[1][-1]["content"])
    assert "hint" not in forbidden and "will not be retried" in forbidden["instruction"]
