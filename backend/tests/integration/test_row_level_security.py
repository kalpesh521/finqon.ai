"""Row-level security and role privileges, exercised through the real roles.

Every test here uses the same connections the application uses (``finqon_reader`` and
``finqon_api``), never the owner, so a passing test means the policy holds for the app.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import asyncpg
import pytest
from sqlalchemy import text

from finqon.db.session import Database
from finqon.repositories.conversations import ConversationRepository, NewMessage
from finqon.sql.executor import QueryExecutor
from tests.conftest import ISOLATION_EMAIL, PRIMARY_EMAIL, expected_spend_by_email

pytestmark = pytest.mark.integration

TOTAL_SPEND = "SELECT SUM(spend_amount) AS total FROM reporting.spend"


def _message(conversation_id: uuid.UUID, user_id: uuid.UUID, question: str) -> NewMessage:
    return NewMessage(
        conversation_id=conversation_id,
        user_id=user_id,
        question=question,
        sql=None,
        purpose=None,
        result_summary=None,
        answer="probe answer",
        status="answered",
        prompt_tokens=0,
        completion_tokens=0,
    )


async def _scalar(executor: QueryExecutor, sql: str, user_id: uuid.UUID):
    result = await executor.run(sql, user_id)
    return result.raw_rows[0][0]


# ----------------------------------------------------------------------- ledger / reader


async def test_each_user_sees_exactly_their_own_spend(
    executor: QueryExecutor, primary_id: uuid.UUID, isolation_id: uuid.UUID
) -> None:
    expected = expected_spend_by_email()
    primary = Decimal(await _scalar(executor, TOTAL_SPEND, primary_id))
    isolated = Decimal(await _scalar(executor, TOTAL_SPEND, isolation_id))

    assert primary == expected[PRIMARY_EMAIL]
    assert isolated == expected[ISOLATION_EMAIL]
    assert primary != isolated


async def test_asking_for_another_users_rows_by_id_returns_nothing(
    executor: QueryExecutor, primary_id: uuid.UUID, isolation_id: uuid.UUID
) -> None:
    sql = f"SELECT COUNT(*) AS n FROM reporting.transactions WHERE user_id = '{primary_id}'"

    assert await _scalar(executor, sql, isolation_id) == 0
    assert await _scalar(executor, sql, primary_id) > 0


async def test_every_ledger_view_is_empty_without_a_user(executor: QueryExecutor) -> None:
    """RLS must default to deny: no ``finqon.user_id`` means no rows, not all rows."""
    async with executor._pool.acquire() as conn:
        for view in ("transactions", "spend", "monthly_category_spend"):
            assert await conn.fetchval(f"SELECT COUNT(*) FROM reporting.{view}") == 0
        for table in ("accounts", "transactions"):
            assert await conn.fetchval(f"SELECT COUNT(*) FROM ledger.{table}") == 0


async def test_unknown_user_id_sees_nothing(executor: QueryExecutor) -> None:
    assert await _scalar(executor, TOTAL_SPEND, uuid.uuid4()) is None


async def test_user_scope_does_not_leak_between_pooled_connections(
    executor: QueryExecutor, primary_id: uuid.UUID, isolation_id: uuid.UUID
) -> None:
    """SET LOCAL is transaction-scoped: alternating users on a shared pool stay separate."""
    totals = [
        Decimal(await _scalar(executor, TOTAL_SPEND, user))
        for user in (primary_id, isolation_id, primary_id, isolation_id)
    ]
    assert totals[0] == totals[2]
    assert totals[1] == totals[3]
    assert totals[0] != totals[1]


async def test_merchant_search_only_returns_the_users_own_merchants(
    executor: QueryExecutor, primary_id: uuid.UUID, isolation_id: uuid.UUID
) -> None:
    mine = await executor.search_merchants("swiggy", primary_id)
    assert mine and mine[0]["merchant_name"].lower().startswith("swiggy")

    # Tanishq appears only in the primary persona's ledger (the festival spike).
    assert await executor.search_merchants("tanishq", primary_id)
    assert await executor.search_merchants("tanishq", isolation_id) == []


async def test_reader_cannot_write(executor: QueryExecutor, primary_id: uuid.UUID) -> None:
    async with executor._pool.acquire() as conn:
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("UPDATE ledger.transactions SET amount = 1")
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("CREATE TABLE reporting.x (a int)")
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("DROP VIEW reporting.spend")


async def test_reader_cannot_read_identity_or_conversations(executor: QueryExecutor) -> None:
    async with executor._pool.acquire() as conn:
        for table in ("identity.users", "identity.refresh_tokens", "conversation.messages"):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetchval(f"SELECT COUNT(*) FROM {table}")


async def test_reader_session_is_read_only_by_default(executor: QueryExecutor) -> None:
    async with executor._pool.acquire() as conn:
        assert await conn.fetchval("SHOW default_transaction_read_only") == "on"
        with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
            await conn.execute("CREATE TEMP TABLE t (a int)")


async def test_application_roles_hold_no_dangerous_attributes(executor: QueryExecutor) -> None:
    async with executor._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT rolname, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
            "FROM pg_roles WHERE rolname IN ('finqon_api', 'finqon_reader', 'finqon_loader')"
        )
    assert {row["rolname"] for row in rows} == {"finqon_api", "finqon_reader", "finqon_loader"}
    for row in rows:
        assert not (row["rolsuper"] or row["rolbypassrls"] or row["rolcreatedb"])
        assert not row["rolcreaterole"]


async def test_every_ledger_and_conversation_table_forces_rls(executor: QueryExecutor) -> None:
    async with executor._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT n.nspname || '.' || c.relname AS name, c.relrowsecurity, c.relforcerowsecurity "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relname IN ('accounts', 'transactions', 'conversations', 'messages') "
            "AND n.nspname IN ('ledger', 'conversation')"
        )
    assert len(rows) == 4
    assert all(row["relrowsecurity"] and row["relforcerowsecurity"] for row in rows), rows


async def test_reporting_views_run_with_the_callers_privileges(executor: QueryExecutor) -> None:
    async with executor._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT c.relname, c.reloptions FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'reporting' AND c.relkind = 'v'"
        )
    assert {row["relname"] for row in rows} == {
        "transactions",
        "spend",
        "monthly_category_spend",
    }
    for row in rows:
        assert "security_invoker=on" in (row["reloptions"] or []), row["relname"]


# ------------------------------------------------------------------ conversations / API role


async def test_api_role_cannot_touch_the_ledger(database: Database) -> None:
    for table in ("ledger.transactions", "ledger.accounts"):
        with pytest.raises(Exception, match="permission denied"):
            async with database.session() as session:
                await session.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))


async def test_conversations_are_invisible_to_other_users(
    database: Database, primary_id: uuid.UUID, isolation_id: uuid.UUID
) -> None:
    async with database.user_scope(primary_id) as session:
        repo = ConversationRepository(session)
        conversation = await repo.create(primary_id, "RLS probe")
        await repo.add_message(_message(conversation.id, primary_id, "probe question"))

    async with database.user_scope(isolation_id) as session:
        repo = ConversationRepository(session)
        assert await repo.get(conversation.id) is None
        assert conversation.id not in {c.id for c in await repo.list_recent(100, 0)}
        assert await repo.messages(conversation.id) == []

    async with database.user_scope(primary_id) as session:
        assert await ConversationRepository(session).get(conversation.id) is not None


async def test_a_user_cannot_write_into_someone_elses_conversation(
    database: Database, primary_id: uuid.UUID, isolation_id: uuid.UUID
) -> None:
    async with database.user_scope(primary_id) as session:
        conversation = await ConversationRepository(session).create(primary_id, "owned by A")

    with pytest.raises(Exception, match=r"row-level security|foreign key|violates"):
        async with database.user_scope(isolation_id) as session:
            await ConversationRepository(session).add_message(
                _message(conversation.id, isolation_id, "injected")
            )


async def test_api_session_without_a_user_sees_no_conversations(
    database: Database, primary_id: uuid.UUID
) -> None:
    async with database.user_scope(primary_id) as session:
        await ConversationRepository(session).create(primary_id, "exists")

    async with database.session() as session:  # no finqon.user_id set
        count = (
            await session.execute(text("SELECT COUNT(*) FROM conversation.conversations"))
        ).scalar()
    assert count == 0
