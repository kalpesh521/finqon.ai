"""Pin flag on conversations, and permission to delete a chat.

Revision ID: 0006
Revises: 0005

Pinning is a property of the conversation, not of a message. Deleting a conversation
removes its messages through the existing ON DELETE CASCADE. The API role previously
could not DELETE those tables.
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE conversation.conversations
            ADD COLUMN pinned boolean NOT NULL DEFAULT false,
            ADD COLUMN pinned_at timestamptz
        """
    )
    op.execute(
        "GRANT DELETE ON conversation.conversations, conversation.messages TO finqon_api"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE DELETE ON conversation.conversations, conversation.messages FROM finqon_api"
    )
    op.execute("ALTER TABLE conversation.conversations DROP COLUMN pinned_at")
    op.execute("ALTER TABLE conversation.conversations DROP COLUMN pinned")
