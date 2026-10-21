"""Row-level security keyed by ``finqon.user_id``.

Revision ID: 0002
Revises: 0001

Policies are forced so they apply to the table owner as well. The executor and the
API set ``finqon.user_id`` with ``set_config(..., true)`` from the verified JWT.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

CURRENT_USER = "NULLIF(current_setting('finqon.user_id', true), '')::uuid"

OWNED_BY_USER = {
    "ledger.accounts": "user_id",
    "conversation.conversations": "user_id",
    "conversation.messages": "user_id",
}

TRANSACTIONS_POLICY = f"""
    EXISTS (
        SELECT 1
        FROM ledger.accounts a
        WHERE a.id = transactions.account_id
          AND a.user_id = {CURRENT_USER}
    )
"""


def _enable(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    for table, column in OWNED_BY_USER.items():
        _enable(table)
        policy = f"{table.split('.')[1]}_owner_only"
        condition = f"{column} = {CURRENT_USER}"
        op.execute(
            f"CREATE POLICY {policy} ON {table} USING ({condition}) WITH CHECK ({condition})"
        )

    _enable("ledger.transactions")
    op.execute(
        "CREATE POLICY transactions_owner_only ON ledger.transactions "
        f"USING ({TRANSACTIONS_POLICY}) WITH CHECK ({TRANSACTIONS_POLICY})"
    )


def downgrade() -> None:
    for table in ("ledger.transactions", *OWNED_BY_USER):
        policy = f"{table.split('.')[1]}_owner_only"
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
