"""Optional display name for the signed-in greeting.

Revision ID: 0007
Revises: 0006

Existing accounts keep a null name and the UI falls back to the email.
The primary seed account is labeled so the desk can greet him by name.
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE identity.users ADD COLUMN display_name text")
    op.execute(
        """
        UPDATE identity.users
        SET display_name = 'Kalpesh Pawar'
        WHERE email = 'kalpeshpawar@gmail.com' AND display_name IS NULL
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE identity.users DROP COLUMN display_name")
