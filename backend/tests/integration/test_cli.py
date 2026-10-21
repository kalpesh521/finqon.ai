"""The command-line entry point against the real database."""

from __future__ import annotations

import pytest

from finqon.cli import main
from tests.conftest import PRIMARY_EMAIL
from tests.reference import csv_spend, last_full_month

pytestmark = pytest.mark.integration


def test_reference_command_prints_the_independently_computed_dining_total(
    integration_settings, capsys: pytest.CaptureFixture[str]
) -> None:
    start, end = last_full_month(integration_settings)
    expected = csv_spend(PRIMARY_EMAIL, category="Dining", start=start, end=end)

    assert main(["reference", "--user", PRIMARY_EMAIL]) == 0

    assert f"{expected:,.2f}" in capsys.readouterr().out


def test_unknown_user_is_reported_not_crashed(
    integration_settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["reference", "--user", "nobody@finqon.example"]) == 1
    assert "no user" in capsys.readouterr().err
