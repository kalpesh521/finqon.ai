"""Load ``dataset/output/*.csv`` into PostgreSQL as ``finqon_loader``.

    FINQON_SEED_PASSWORD=... python dataset/load_ledger.py

* Creates the two seed users (password from ``FINQON_SEED_PASSWORD`` in ``.env``).
  Re-running updates that password to match ``.env``.
* Upserts the shared lookup tables, then replaces each seed user's accounts and
  transactions. Row-level security is forced, so the loader sets ``finqon.user_id``
  for each user before touching their rows. Safe to re-run.
"""

from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

import psycopg

from finqon.core.config import ConfigurationError, get_settings
from finqon.core.security import hash_password
from finqon.db.urls import parse_database_url

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
BATCH = 500


def read(name: str) -> list[dict[str, str]]:
    with (OUTPUT_DIR / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def ensure_users(cur: psycopg.Cursor, emails: list[str]) -> dict[str, str]:
    password = get_settings().seed_password.get_secret_value() or os.environ.get(
        "FINQON_SEED_PASSWORD", ""
    )
    if len(password) < 10:
        raise ConfigurationError("Set FINQON_SEED_PASSWORD (at least 10 characters).")
    password_hash = hash_password(password)
    ids: dict[str, str] = {}
    for email in emails:
        cur.execute(
            """
            INSERT INTO identity.users (email, password_hash) VALUES (%s, %s)
            ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash
            """,
            (email, password_hash),
        )
        cur.execute("SELECT id FROM identity.users WHERE email = %s", (email,))
        ids[email] = str(cur.fetchone()[0])
    return ids


def upsert_lookups(cur: psycopg.Cursor) -> None:
    categories = sorted(read("categories.csv"), key=lambda r: (r["parent_id"] != "", int(r["id"])))
    cur.executemany(
        """
        INSERT INTO ledger.categories (id, name, parent_id, kind) VALUES (%s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE
            SET name = EXCLUDED.name, parent_id = EXCLUDED.parent_id, kind = EXCLUDED.kind
        """,
        [
            (int(r["id"]), r["name"], int(r["parent_id"]) if r["parent_id"] else None, r["kind"])
            for r in categories
        ],
    )
    cur.executemany(
        """
        INSERT INTO ledger.merchants (id, name, default_category_id) VALUES (%s, %s, %s)
        ON CONFLICT (id) DO UPDATE
            SET name = EXCLUDED.name, default_category_id = EXCLUDED.default_category_id
        """,
        [(int(r["id"]), r["name"], int(r["default_category_id"])) for r in read("merchants.csv")],
    )
    cur.execute("DELETE FROM ledger.merchant_aliases")
    cur.executemany(
        "INSERT INTO ledger.merchant_aliases (id, pattern, match_type, merchant_id) "
        "VALUES (%s, %s, %s, %s)",
        [
            (int(r["id"]), r["pattern"], r["match_type"], int(r["merchant_id"]))
            for r in read("merchant_aliases.csv")
        ],
    )


def replace_user_ledger(
    cur: psycopg.Cursor,
    user_id: str,
    accounts: list[dict[str, str]],
    transactions: list[dict[str, str]],
) -> None:
    cur.execute("SELECT set_config('finqon.user_id', %s, true)", (user_id,))
    cur.execute(
        "DELETE FROM ledger.transactions WHERE account_id IN "
        "(SELECT id FROM ledger.accounts WHERE user_id = %s)",
        (user_id,),
    )
    cur.execute("DELETE FROM ledger.accounts WHERE user_id = %s", (user_id,))
    cur.executemany(
        "INSERT INTO ledger.accounts (id, user_id, name, account_type) VALUES (%s, %s, %s, %s)",
        [(int(a["id"]), user_id, a["name"], a["account_type"]) for a in accounts],
    )
    rows = [
        (
            int(t["id"]),
            int(t["account_id"]),
            t["txn_date"],
            t["raw_description"],
            int(t["merchant_id"]) if t["merchant_id"] else None,
            int(t["category_id"]) if t["category_id"] else None,
            t["amount"],
            t["direction"],
            t["txn_type"],
            t["payment_mode"],
            t["is_recurring"] == "true",
            int(t["refund_of_txn_id"]) if t["refund_of_txn_id"] else None,
            t["balance_after"] or None,
        )
        for t in transactions
    ]
    # One statement per user: the self-referencing refund FK is checked at statement end.
    row_placeholder = "(" + ", ".join(["%s"] * 13) + ")"
    placeholders = ", ".join([row_placeholder] * len(rows))
    cur.execute(
        "INSERT INTO ledger.transactions (id, account_id, txn_date, raw_description, merchant_id, "
        "category_id, amount, direction, txn_type, payment_mode, is_recurring, refund_of_txn_id, "
        f"balance_after) VALUES {placeholders}",
        [value for row in rows for value in row],
    )


def main() -> int:
    url = get_settings().loader_database_url.get_secret_value()
    if not url:
        raise ConfigurationError("FINQON_LOADER_DATABASE_URL is empty.")

    accounts = read("accounts.csv")
    transactions = read("transactions.csv")
    accounts_by_email: dict[str, list[dict[str, str]]] = defaultdict(list)
    for account in accounts:
        accounts_by_email[account["user_email"]].append(account)
    account_owner = {a["id"]: a["user_email"] for a in accounts}

    with psycopg.connect(parse_database_url(url).url) as conn, conn.cursor() as cur:
        user_ids = ensure_users(cur, sorted(accounts_by_email))
        upsert_lookups(cur)
        for email, user_accounts in accounts_by_email.items():
            rows = [t for t in transactions if account_owner[t["account_id"]] == email]
            replace_user_ledger(cur, user_ids[email], user_accounts, rows)
            print(f"{email}: {len(rows)} transactions loaded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
