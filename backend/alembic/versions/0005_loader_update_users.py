"""Allow the loader to reset seed-user passwords on re-run.

Revision ID: 0005
Revises: 0004

``dataset/load_ledger.py`` upserts ``identity.users``. INSERT ... ON CONFLICT DO UPDATE
needs UPDATE on that table; 0004 only granted SELECT and INSERT.
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT, INSERT, UPDATE ON identity.users TO finqon_loader")


def downgrade() -> None:
    op.execute("REVOKE UPDATE ON identity.users FROM finqon_loader")
    op.execute("GRANT SELECT, INSERT ON identity.users TO finqon_loader")
