"""Semantic search over transaction descriptions (pgvector) and the ``info`` message status.

Revision ID: 0009
Revises: 0008

Embeddings live on the transaction row, so the existing row-level security policy covers
them. Only the (already masked) description is embedded, never amounts or dates; those
are filtered in SQL. Per-user data is small, so an exact scan inside the user's rows is
used; add an HNSW index when a single user's volume makes that slow (filtered ANN can
return too few rows, so test it on real data first).
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    "ALTER TABLE ledger.transactions ADD COLUMN embedding vector(768)",
    "COMMENT ON COLUMN ledger.transactions.embedding IS "
    "'Embedding of raw_description (masked). Model: gemini-embedding-001, 768 dimensions.'",
    "CREATE INDEX ix_transactions_description_trgm ON ledger.transactions "
    "USING gin (raw_description gin_trgm_ops)",
    "GRANT UPDATE (embedding) ON ledger.transactions TO finqon_api",
    "ALTER TABLE conversation.messages DROP CONSTRAINT messages_status_valid",
    "ALTER TABLE conversation.messages ADD CONSTRAINT messages_status_valid "
    "CHECK (status IN ('answered', 'clarification', 'refused', 'error', 'info'))",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute("UPDATE conversation.messages SET status = 'answered' WHERE status = 'info'")
    op.execute("ALTER TABLE conversation.messages DROP CONSTRAINT messages_status_valid")
    op.execute(
        "ALTER TABLE conversation.messages ADD CONSTRAINT messages_status_valid "
        "CHECK (status IN ('answered', 'clarification', 'refused', 'error'))"
    )
    op.execute("REVOKE UPDATE (embedding) ON ledger.transactions FROM finqon_api")
    op.execute("DROP INDEX ledger.ix_transactions_description_trgm")
    op.execute("ALTER TABLE ledger.transactions DROP COLUMN embedding")
