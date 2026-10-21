"""Schemas, tables, and indexes.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE SCHEMA identity",
    "CREATE SCHEMA ledger",
    "CREATE SCHEMA conversation",
    "CREATE SCHEMA operations",
    "CREATE SCHEMA reporting",
    # ---------------------------------------------------------------- identity
    """
    CREATE TABLE identity.users (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        email         text NOT NULL UNIQUE,
        password_hash text NOT NULL,
        created_at    timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT users_email_lowercase CHECK (email = lower(email))
    )
    """,
    """
    CREATE TABLE identity.refresh_tokens (
        id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id    uuid NOT NULL REFERENCES identity.users (id) ON DELETE CASCADE,
        token_hash text NOT NULL UNIQUE,
        expires_at timestamptz NOT NULL,
        revoked_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX ix_refresh_tokens_live_by_user
        ON identity.refresh_tokens (user_id) WHERE revoked_at IS NULL
    """,
    # ------------------------------------------------------------------ ledger
    # Ledger rows are loaded only from dataset/output by finqon_loader, so primary
    # keys are plain integers supplied by the CSV files.
    """
    CREATE TABLE ledger.categories (
        id        integer PRIMARY KEY,
        name      text NOT NULL UNIQUE,
        parent_id integer REFERENCES ledger.categories (id),
        kind      text NOT NULL,
        CONSTRAINT categories_kind_valid CHECK (kind IN ('expense', 'income', 'transfer'))
    )
    """,
    """
    CREATE TABLE ledger.merchants (
        id                  integer PRIMARY KEY,
        name                text NOT NULL UNIQUE,
        default_category_id integer REFERENCES ledger.categories (id)
    )
    """,
    """
    CREATE TABLE ledger.merchant_aliases (
        id          integer PRIMARY KEY,
        pattern     text NOT NULL,
        match_type  text NOT NULL,
        merchant_id integer NOT NULL REFERENCES ledger.merchants (id),
        CONSTRAINT merchant_aliases_match_type_valid
            CHECK (match_type IN ('exact', 'contains', 'regex')),
        CONSTRAINT merchant_aliases_unique UNIQUE (pattern, match_type)
    )
    """,
    """
    CREATE TABLE ledger.accounts (
        id           integer PRIMARY KEY,
        user_id      uuid NOT NULL REFERENCES identity.users (id) ON DELETE CASCADE,
        name         text NOT NULL,
        account_type text NOT NULL,
        CONSTRAINT accounts_type_valid
            CHECK (account_type IN ('savings', 'current', 'credit_card'))
    )
    """,
    "CREATE INDEX ix_accounts_user ON ledger.accounts (user_id)",
    """
    CREATE TABLE ledger.transactions (
        id               bigint PRIMARY KEY,
        account_id       integer NOT NULL REFERENCES ledger.accounts (id) ON DELETE CASCADE,
        txn_date         date NOT NULL,
        raw_description  text NOT NULL,
        merchant_id      integer REFERENCES ledger.merchants (id),
        category_id      integer REFERENCES ledger.categories (id),
        amount           numeric(14, 2) NOT NULL,
        direction        text NOT NULL,
        txn_type         text NOT NULL,
        payment_mode     text NOT NULL,
        is_recurring     boolean NOT NULL DEFAULT false,
        refund_of_txn_id bigint REFERENCES ledger.transactions (id),
        balance_after    numeric(14, 2),
        CONSTRAINT transactions_amount_positive CHECK (amount > 0),
        CONSTRAINT transactions_direction_valid CHECK (direction IN ('debit', 'credit')),
        CONSTRAINT transactions_type_valid CHECK (
            txn_type IN ('purchase', 'refund', 'salary', 'transfer',
                         'emi', 'subscription', 'atm', 'fee')
        ),
        CONSTRAINT transactions_payment_mode_valid CHECK (
            payment_mode IN ('upi', 'card', 'neft', 'imps', 'netbanking',
                             'auto_debit', 'cash', 'atm')
        ),
        CONSTRAINT transactions_credit_types CHECK (
            txn_type NOT IN ('refund', 'salary') OR direction = 'credit'
        ),
        CONSTRAINT transactions_debit_types CHECK (
            txn_type IN ('refund', 'salary', 'transfer') OR direction = 'debit'
        ),
        CONSTRAINT transactions_refund_link CHECK (
            (txn_type = 'refund') = (refund_of_txn_id IS NOT NULL)
        )
    )
    """,
    "CREATE INDEX ix_transactions_account_date ON ledger.transactions (account_id, txn_date)",
    "CREATE INDEX ix_transactions_category ON ledger.transactions (category_id)",
    "CREATE INDEX ix_transactions_merchant ON ledger.transactions (merchant_id)",
    """
    CREATE INDEX ix_transactions_refund_of
        ON ledger.transactions (refund_of_txn_id) WHERE refund_of_txn_id IS NOT NULL
    """,
    "CREATE INDEX ix_merchants_name_trgm ON ledger.merchants USING gin (name gin_trgm_ops)",
    # ------------------------------------------------------------ conversation
    """
    CREATE TABLE conversation.conversations (
        id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id    uuid NOT NULL REFERENCES identity.users (id) ON DELETE CASCADE,
        title      text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT conversations_id_user_unique UNIQUE (id, user_id)
    )
    """,
    """
    CREATE INDEX ix_conversations_user_updated
        ON conversation.conversations (user_id, updated_at DESC)
    """,
    # One row per turn. Result sets are never stored; only the SQL and a one-line summary.
    """
    CREATE TABLE conversation.messages (
        id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        conversation_id   uuid NOT NULL,
        user_id           uuid NOT NULL,
        question          text NOT NULL,
        sql               text,
        purpose           text,
        result_summary    text,
        answer            text NOT NULL,
        status            text NOT NULL,
        prompt_tokens     integer NOT NULL DEFAULT 0,
        completion_tokens integer NOT NULL DEFAULT 0,
        created_at        timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT messages_status_valid
            CHECK (status IN ('answered', 'clarification', 'refused', 'error')),
        CONSTRAINT messages_conversation_owner FOREIGN KEY (conversation_id, user_id)
            REFERENCES conversation.conversations (id, user_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX ix_messages_conversation_created
        ON conversation.messages (conversation_id, created_at)
    """,
    # -------------------------------------------------------------- operations
    """
    CREATE TABLE operations.model_usage (
        id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        user_id           uuid REFERENCES identity.users (id) ON DELETE SET NULL,
        conversation_id   uuid,
        model             text NOT NULL,
        prompt_tokens     integer NOT NULL,
        completion_tokens integer NOT NULL,
        latency_ms        integer NOT NULL,
        created_at        timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX ix_model_usage_created ON operations.model_usage (created_at)",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for schema in ("operations", "conversation", "ledger", "identity", "reporting"):
        op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
