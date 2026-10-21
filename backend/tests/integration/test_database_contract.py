"""The semantic layer the model is allowed to query: definitions, comments and examples.

Expected figures are recomputed from the seed CSVs with plain Python, so a mistake in a
view definition cannot hide behind a test that reuses the same SQL.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from finqon.agent.few_shot_examples import DINING_LAST_MONTH, SQL_EXAMPLES
from finqon.core.config import Settings
from finqon.sql.executor import QueryExecutor
from tests.conftest import PRIMARY_EMAIL
from tests.reference import csv_spend, last_full_month

pytestmark = pytest.mark.integration

VIEWS = ("transactions", "spend", "monthly_category_spend")


# ------------------------------------------------------------------------- documentation


async def test_every_view_and_column_has_a_comment(executor: QueryExecutor) -> None:
    """The model learns the schema from these comments; an undocumented column is a bug."""
    rows = await executor.fetch_catalog_rows(
        """
        SELECT c.relname AS view, a.attname AS column,
               obj_description(c.oid, 'pg_class') AS view_comment,
               col_description(c.oid, a.attnum) AS column_comment
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        WHERE n.nspname = 'reporting' AND c.relkind = 'v'
        """
    )
    assert {r["view"] for r in rows} == set(VIEWS)
    missing = [
        f"{r['view']}.{r['column']}" for r in rows if not (r["column_comment"] or "").strip()
    ]
    assert missing == []
    assert all((r["view_comment"] or "").strip() for r in rows)


async def test_spend_view_states_what_spend_means(executor: QueryExecutor) -> None:
    (row,) = await executor.fetch_catalog_rows(
        "SELECT obj_description('reporting.spend'::regclass, 'pg_class') AS comment"
    )
    comment = row["comment"].lower()
    assert "refund" in comment
    assert "transfer" in comment


# ---------------------------------------------------------------------------- definitions


async def test_spend_excludes_income_and_transfers(
    executor: QueryExecutor, primary_id: uuid.UUID
) -> None:
    result = await executor.run(
        "SELECT COUNT(*) AS n FROM reporting.spend "
        "WHERE txn_type IN ('transfer', 'salary', 'interest', 'income')",
        primary_id,
    )
    assert result.raw_rows[0][0] == 0

    income = await executor.run(
        "SELECT COUNT(*) AS n FROM reporting.transactions WHERE txn_type = 'salary'", primary_id
    )
    assert income.raw_rows[0][0] > 0  # salary exists in the ledger, just not in spend


async def test_refunds_reduce_spend_on_the_refund_date(
    executor: QueryExecutor, primary_id: uuid.UUID
) -> None:
    refunds = await executor.run(
        "SELECT COUNT(*), MAX(spend_amount) FROM reporting.spend WHERE is_refund", primary_id
    )
    count, largest = refunds.raw_rows[0]
    assert count > 0
    assert largest < 0  # a refund is negative spend, never positive


async def test_monthly_view_agrees_with_the_spend_view(
    executor: QueryExecutor, primary_id: uuid.UUID
) -> None:
    direct = await executor.run("SELECT SUM(spend_amount) FROM reporting.spend", primary_id)
    rolled_up = await executor.run(
        "SELECT SUM(spend) FROM reporting.monthly_category_spend", primary_id
    )
    assert direct.raw_rows[0][0] == rolled_up.raw_rows[0][0]


async def test_financial_year_runs_april_to_march(
    executor: QueryExecutor, primary_id: uuid.UUID
) -> None:
    result = await executor.run(
        """
        SELECT DISTINCT month, fy, fy_quarter FROM reporting.spend
        WHERE month IN ('2026-03-01', '2026-04-01', '2026-06-01', '2026-07-01', '2026-12-01')
        ORDER BY month
        """,
        primary_id,
    )
    labels = {row[0].isoformat(): (row[1], row[2]) for row in result.raw_rows}
    expected = {
        "2026-03-01": ("FY2025-26", 4),
        "2026-04-01": ("FY2026-27", 1),
        "2026-06-01": ("FY2026-27", 1),
        "2026-07-01": ("FY2026-27", 2),
        "2026-12-01": ("FY2026-27", 3),
    }
    assert labels
    for month, label in labels.items():
        assert label == expected[month], month


async def test_dining_last_month_matches_an_independent_computation(
    executor: QueryExecutor, primary_id: uuid.UUID, integration_settings: Settings
) -> None:
    """The exit check: last month's dining total, computed two unrelated ways."""
    start, end = last_full_month(integration_settings)

    result = await executor.run(DINING_LAST_MONTH, primary_id)

    expected = csv_spend(PRIMARY_EMAIL, category="Dining", start=start, end=end)
    assert Decimal(result.raw_rows[0][0]) == expected
    assert expected > 0


# ----------------------------------------------------------------------------- examples


@pytest.mark.parametrize("example", SQL_EXAMPLES, ids=lambda e: e.question[:40])
async def test_every_few_shot_example_passes_the_guard_and_runs(
    executor: QueryExecutor, primary_id: uuid.UUID, example
) -> None:
    """A prompt that teaches SQL which fails is worse than no example at all."""
    result = await executor.run(example.sql, primary_id)

    assert result.row_count > 0, "example returned nothing on the seeded data"


async def test_unknown_merchants_exist_unaliased_for_the_resolver_to_handle(
    executor: QueryExecutor, primary_id: uuid.UUID
) -> None:
    result = await executor.run(
        "SELECT COUNT(*) FROM reporting.transactions WHERE merchant_name = 'Unknown Merchant'",
        primary_id,
    )
    assert result.raw_rows[0][0] > 0


async def test_the_injection_row_is_plain_data_in_the_primary_ledger_only(
    executor: QueryExecutor, primary_id: uuid.UUID, isolation_id: uuid.UUID
) -> None:
    sql = "SELECT COUNT(*) FROM reporting.transactions WHERE raw_description ILIKE '%ignore%'"
    assert (await executor.run(sql, primary_id)).raw_rows[0][0] == 1
    assert (await executor.run(sql, isolation_id)).raw_rows[0][0] == 0
