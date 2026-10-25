from decimal import Decimal

from finqon.evaluation.judge import parse_judgement
from finqon.evaluation.scoring import (
    check_grounding,
    check_no_model_reply,
    check_required_phrases,
)


def test_grounding_accepts_figures_from_the_table() -> None:
    rows = [["Netflix", Decimal("1448.00")], ["Spotify", Decimal("119.00")]]
    verdict = check_grounding("Netflix cost ₹1,448 and Spotify ₹119.", ["m", "t"], rows)
    assert verdict.passed


def test_grounding_accepts_sum_and_row_count() -> None:
    rows = [["a", Decimal("100")], ["b", Decimal("250")]]
    assert check_grounding("Across 2 rows the total is ₹350.", ["m", "t"], rows).passed


def test_grounding_rejects_invented_figures() -> None:
    rows = [["a", Decimal("100")]]
    assert not check_grounding("You spent ₹9,999.", ["m", "t"], rows).passed


def test_required_phrases() -> None:
    assert check_required_phrases("This is quarter-to-date", [r"quarter.to.date"]).passed
    assert not check_required_phrases("Done", [r"quarter"]).passed


def test_no_model_reply_rejects_sql_and_leaks() -> None:
    assert check_no_model_reply("Hello!", queries_run=0, tables_shown=0).passed
    assert not check_no_model_reply("Hello!", queries_run=1, tables_shown=0).passed
    assert not check_no_model_reply(
        "4111", queries_run=0, tables_shown=0, forbidden=["4111"]
    ).passed


def test_judgement_parsing() -> None:
    ok = '{"items":[{"criterion":"a","evidence":"x","met":true}]}'
    assert parse_judgement(ok, ["a"]).passed
    bad = '{"items":[{"criterion":"a","evidence":"x","met":false}]}'
    assert not parse_judgement(bad, ["a"]).passed
    assert not parse_judgement("nonsense", ["a"]).passed
    assert not parse_judgement(ok, ["a", "b"]).passed
