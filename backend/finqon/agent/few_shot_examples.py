"""Hand-written question and SQL pairs shown to the model.

Every SQL string here is validated by the guard in the test suite, and executed against
a seeded database in the integration tests, so the examples cannot drift out of date.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SqlExample:
    question: str
    purpose: str
    chart_hint: str
    sql: str
    previous_sql: str | None = None  # present for follow-ups


@dataclass(frozen=True, slots=True)
class ClarificationExample:
    question: str
    ask: str
    options: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RefusalExample:
    question: str
    reply: str


DINING_LAST_MONTH = """\
SELECT COALESCE(SUM(spend_amount), 0) AS total_spend
FROM reporting.spend
WHERE category_name = 'Dining'
  AND txn_date >= date_trunc('month', current_date - interval '1 month')::date
  AND txn_date < date_trunc('month', current_date)::date"""

SQL_EXAMPLES: tuple[SqlExample, ...] = (
    SqlExample(
        "How much did I spend on eating out last month?",
        "Total dining spend last month.",
        "none",
        DINING_LAST_MONTH,
    ),
    SqlExample(
        "Give me a category-wise spending breakdown for the last 3 months.",
        "Net spend by category over the last three full months.",
        "bar",
        """\
SELECT category_name, SUM(spend_amount) AS total_spend
FROM reporting.spend
WHERE txn_date >= date_trunc('month', current_date - interval '3 months')::date
  AND txn_date < date_trunc('month', current_date)::date
GROUP BY category_name
HAVING SUM(spend_amount) <> 0
ORDER BY total_spend DESC""",
    ),
    SqlExample(
        "What are my top 5 merchants by spend?",
        "Top five merchants by net spend across all available data.",
        "bar",
        """\
SELECT merchant_name,
       SUM(spend_amount) AS total_spend,
       COUNT(*) FILTER (WHERE NOT is_refund) AS txn_count
FROM reporting.spend
WHERE merchant_name <> 'Unknown Merchant'
GROUP BY merchant_name
ORDER BY total_spend DESC
LIMIT 5""",
    ),
    SqlExample(
        "Which month did I spend the most, and why?",
        "The highest-spend month and the categories that drove it.",
        "bar",
        """\
WITH monthly AS (
    SELECT month, SUM(spend) AS month_total
    FROM reporting.monthly_category_spend
    GROUP BY month
),
top_month AS (
    SELECT month, month_total FROM monthly ORDER BY month_total DESC LIMIT 1
)
SELECT t.month, t.month_total, m.category_name, m.spend AS category_spend
FROM top_month t
JOIN reporting.monthly_category_spend m ON m.month = t.month
WHERE m.spend > 0
ORDER BY m.spend DESC""",
    ),
    SqlExample(
        "Show me all transactions above ₹5,000 in the last 30 days.",
        "Transactions over 5,000 rupees in the last 30 days.",
        "none",
        """\
SELECT txn_date, merchant_name, category_name, amount, direction, txn_type
FROM reporting.transactions
WHERE amount > 5000
  AND txn_date >= current_date - 29
  AND txn_date <= current_date
ORDER BY amount DESC, txn_date DESC""",
    ),
    SqlExample(
        "Compare my grocery spend this quarter vs last quarter.",
        "Grocery spend this quarter to date against last quarter.",
        "bar",
        """\
WITH quarterly AS (
    SELECT date_trunc('quarter', txn_date)::date AS quarter_start,
           SUM(spend_amount) AS grocery_spend
    FROM reporting.spend
    WHERE category_name = 'Groceries'
      AND txn_date >= date_trunc('quarter', current_date - interval '3 months')::date
      AND txn_date <= current_date
    GROUP BY 1
)
SELECT quarter_start,
       grocery_spend,
       grocery_spend - LAG(grocery_spend) OVER (ORDER BY quarter_start) AS change_vs_previous
FROM quarterly
ORDER BY quarter_start""",
    ),
    SqlExample(
        "What about the month before?",
        "Dining spend two months ago (follow-up: shift the window back one month).",
        "none",
        """\
SELECT COALESCE(SUM(spend_amount), 0) AS total_spend
FROM reporting.spend
WHERE category_name = 'Dining'
  AND txn_date >= date_trunc('month', current_date - interval '2 months')::date
  AND txn_date < date_trunc('month', current_date - interval '1 month')::date""",
        previous_sql=DINING_LAST_MONTH,
    ),
    SqlExample(
        "Break that down by merchant.",
        "Dining spend last month by merchant (follow-up: same filters, add grouping).",
        "bar",
        """\
SELECT merchant_name, SUM(spend_amount) AS total_spend
FROM reporting.spend
WHERE category_name = 'Dining'
  AND txn_date >= date_trunc('month', current_date - interval '1 month')::date
  AND txn_date < date_trunc('month', current_date)::date
GROUP BY merchant_name
HAVING SUM(spend_amount) <> 0
ORDER BY total_spend DESC""",
        previous_sql=DINING_LAST_MONTH,
    ),
)

CLARIFICATION_EXAMPLE = ClarificationExample(
    question="How much did I spend in Q3?",
    ask="Which Q3 do you mean?",
    options=(
        "Financial-year Q3 (Oct-Dec)",
        "Calendar Q3 (Jul-Sep)",
    ),
)

REFUSAL_EXAMPLE = RefusalExample(
    "Delete all my transactions from last month.",
    "I can only read your statement. I can't delete, change or move anything, "
    "so nothing was changed.",
)
