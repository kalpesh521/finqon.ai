"""User-uploaded statements.

Revision ID: 0008
Revises: 0007

Each upload becomes one ``ledger.statements`` row (job state and audit) and one
``ledger.accounts`` row named after the file, so the existing reporting views, the SQL
guard and row-level security cover uploaded data with no change. Deleting a statement
cascades to its account and transactions.

Uploaded ids come from sequences in a high range so they never collide with the seed
data, whose integer ids are supplied by the CSV files.
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

CURRENT_USER = "NULLIF(current_setting('finqon.user_id', true), '')::uuid"

STATEMENTS = [
    """
    CREATE TABLE ledger.statements (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id       uuid NOT NULL REFERENCES identity.users (id) ON DELETE CASCADE,
        filename      text NOT NULL,
        sha256        text NOT NULL,
        size_bytes    integer NOT NULL,
        status        text NOT NULL DEFAULT 'queued',
        stage         text NOT NULL DEFAULT 'queued',
        progress      smallint NOT NULL DEFAULT 0,
        rows_total    integer NOT NULL DEFAULT 0,
        rows_imported integer NOT NULL DEFAULT 0,
        rows_skipped  integer NOT NULL DEFAULT 0,
        date_from     date,
        date_to       date,
        warnings      jsonb NOT NULL DEFAULT '[]'::jsonb,
        error         text,
        created_at    timestamptz NOT NULL DEFAULT now(),
        completed_at  timestamptz,
        CONSTRAINT statements_status_valid
            CHECK (status IN ('queued', 'processing', 'ready', 'failed', 'quarantined')),
        CONSTRAINT statements_progress_valid CHECK (progress BETWEEN 0 AND 100),
        CONSTRAINT statements_user_hash_unique UNIQUE (user_id, sha256)
    )
    """,
    "CREATE INDEX ix_statements_user_created ON ledger.statements (user_id, created_at DESC)",
    "ALTER TABLE ledger.accounts ADD COLUMN statement_id uuid "
    "REFERENCES ledger.statements (id) ON DELETE CASCADE",
    "CREATE UNIQUE INDEX ix_accounts_statement ON ledger.accounts (statement_id) "
    "WHERE statement_id IS NOT NULL",
    "CREATE SEQUENCE ledger.upload_account_id_seq START 1000000",
    "CREATE SEQUENCE ledger.upload_txn_id_seq START 1000000000",
    "ALTER TABLE ledger.statements ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE ledger.statements FORCE ROW LEVEL SECURITY",
    f"""
    CREATE POLICY statements_owner_only ON ledger.statements
        USING (user_id = {CURRENT_USER}) WITH CHECK (user_id = {CURRENT_USER})
    """,
    # The API role writes only through user_scope, so the forced RLS policies on
    # accounts, transactions and statements confine every write to the caller.
    "GRANT USAGE ON SCHEMA ledger TO finqon_api",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ledger.statements TO finqon_api",
    "GRANT SELECT, INSERT, DELETE ON ledger.accounts, ledger.transactions TO finqon_api",
    "GRANT SELECT ON ledger.categories, ledger.merchants, ledger.merchant_aliases TO finqon_api",
    "GRANT USAGE ON SEQUENCE ledger.upload_account_id_seq, ledger.upload_txn_id_seq TO finqon_api",
    "GRANT SELECT ON ledger.statements TO finqon_reader",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DELETE FROM ledger.accounts WHERE statement_id IS NOT NULL")
    op.execute("DROP POLICY IF EXISTS statements_owner_only ON ledger.statements")
    op.execute("ALTER TABLE ledger.accounts DROP COLUMN statement_id")
    op.execute("DROP TABLE ledger.statements")
    op.execute("DROP SEQUENCE ledger.upload_account_id_seq, ledger.upload_txn_id_seq")
    op.execute("REVOKE INSERT, DELETE ON ledger.accounts, ledger.transactions FROM finqon_api")
