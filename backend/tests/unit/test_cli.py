"""The command-line entry point: configuration errors, reference output, formatting."""

from __future__ import annotations

import pytest

from finqon.cli import _format_table, main
from tests.conftest import unit_settings


def test_table_formatting_aligns_columns_and_formats_money() -> None:
    table = _format_table(["category", "total"], [["Dining", 1234.5], ["Rent", 20000.0]])

    assert table.splitlines() == [
        "category  total",
        "--------  ---------",
        "Dining    1,234.50",
        "Rent      20,000.00",
    ]


def test_table_formatting_truncates_long_results() -> None:
    table = _format_table(["n"], [[i] for i in range(25)])

    assert table.splitlines()[-1] == "... 5 more rows"


def test_ask_without_a_model_key_exits_with_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Settings are built without reading the developer's real .env.
    monkeypatch.setattr("finqon.cli.get_settings", lambda: unit_settings(llm_api_key=""))

    assert main(["ask", "hello", "--user", "someone@example.com"]) == 2
    assert "FINQON_LLM_API_KEY" in capsys.readouterr().err
