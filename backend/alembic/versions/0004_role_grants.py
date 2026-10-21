"""Privileges for the three application roles.

Revision ID: 0004
Revises: 0003

Roles are created by ``db/init/roles.sql`` (they are cluster-level, so they are not
part of a migration). This migration checks them and grants privileges.

About ``finqon_reader`` and base tables: the views use ``security_invoker = on``, and
PostgreSQL then checks base-table privileges as the calling role. The reader therefore
needs SELECT on the ledger tables the views read. Row-level security still restricts
every one of those tables to the signed-in user, and the SQL guard only lets the model
name the three reporting views.
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

ASSERT_ROLES = """
DO $$
DECLARE
    role_name text;
    role_row  pg_roles%ROWTYPE;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['finqon_api', 'finqon_reader', 'finqon_loader'] LOOP
        SELECT * INTO role_row FROM pg_roles WHERE rolname = role_name;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Role % does not exist. Run db/init/roles.sql first.', role_name;
        END IF;
        IF role_row.rolsuper OR role_row.rolbypassrls THEN
            RAISE EXCEPTION 'Role % must be NOSUPERUSER and NOBYPASSRLS.', role_name;
        END IF;
    END LOOP;
END
$$
"""

GRANTS = [
    # ---------------------------------------------------------------- reader
    "GRANT USAGE ON SCHEMA reporting, ledger TO finqon_reader",
    """
    GRANT SELECT ON
        reporting.transactions, reporting.spend, reporting.monthly_category_spend
    TO finqon_reader
    """,
    # Needed because the views are security_invoker. Both user tables are protected by RLS;
    # categories and merchants are shared lookups.
    """
    GRANT SELECT ON
        ledger.accounts, ledger.transactions, ledger.categories, ledger.merchants
    TO finqon_reader
    """,
    # ------------------------------------------------------------------- api
    "GRANT USAGE ON SCHEMA identity, conversation, operations TO finqon_api",
    "GRANT SELECT, INSERT, UPDATE ON identity.users, identity.refresh_tokens TO finqon_api",
    "GRANT SELECT, INSERT, UPDATE ON conversation.conversations, conversation.messages TO finqon_api",
    "GRANT SELECT, INSERT ON operations.model_usage TO finqon_api",
    # ---------------------------------------------------------------- loader
    "GRANT USAGE ON SCHEMA ledger, identity TO finqon_loader",
    """
    GRANT SELECT, INSERT, UPDATE, DELETE ON
        ledger.categories, ledger.merchants, ledger.merchant_aliases,
        ledger.accounts, ledger.transactions
    TO finqon_loader
    """,
    "GRANT SELECT, INSERT, UPDATE ON identity.users TO finqon_loader",
]


def upgrade() -> None:
    op.execute(ASSERT_ROLES)
    for statement in GRANTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute(
        "REVOKE ALL ON ALL TABLES IN SCHEMA reporting, ledger, identity, conversation, operations "
        "FROM finqon_reader, finqon_api, finqon_loader"
    )
    op.execute(
        "REVOKE ALL ON SCHEMA reporting, ledger, identity, conversation, operations "
        "FROM finqon_reader, finqon_api, finqon_loader"
    )
