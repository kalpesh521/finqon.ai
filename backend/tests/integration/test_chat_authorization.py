"""Chat over HTTP with a scripted model: isolation, destructive requests, persistence.

The model is scripted so these tests are deterministic and need no API key. What they
prove is everything *around* the model: who can see what, and what can never change.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from finqon.db.session import Database
from finqon.sql.executor import QueryExecutor
from tests.conftest import (
    ISOLATION_EMAIL,
    PRIMARY_EMAIL,
    ScriptedLLM,
    SeedLogin,
    auth_headers,
    expected_spend_by_email,
    parse_sse,
    sql_reply,
    text_reply,
)

pytestmark = pytest.mark.integration

ClientFactory = Callable[..., TestClient]

BY_CATEGORY = """
SELECT category_name, SUM(spend_amount) AS total
FROM reporting.spend
GROUP BY category_name
ORDER BY total DESC
"""
COUNT_TRANSACTIONS = "SELECT COUNT(*) AS n FROM reporting.transactions"


def _chat(
    client: TestClient, tokens: dict[str, str], message: str, conversation_id: str | None = None
):
    return client.post(
        "/api/v1/chat",
        headers=auth_headers(tokens),
        json={"message": message, "conversation_id": conversation_id},
    )


def _first(events: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    return next(event for event in events if event["type"] == kind)


def _breakdown_script(top_total: float | None = None) -> ScriptedLLM:
    """Run the breakdown query, then answer quoting only a figure present in the result."""
    answer = (
        f"Your largest category was {top_total:,.2f} rupees."
        if top_total is not None
        else "Here is your spending by category."
    )
    return ScriptedLLM([sql_reply(BY_CATEGORY, hint="bar"), text_reply(answer)])


async def _transaction_count(executor: QueryExecutor, user_id: uuid.UUID) -> int:
    return (await executor.run(COUNT_TRANSACTIONS, user_id)).raw_rows[0][0]


# ----------------------------------------------------------------------------- streaming


def test_chat_streams_the_documented_event_sequence(
    make_client: ClientFactory, seed_login: SeedLogin
) -> None:
    client = make_client(_breakdown_script())
    tokens = seed_login(PRIMARY_EMAIL)

    response = _chat(client, tokens, "Where did my money go?")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    kinds = [event["type"] for event in events]
    assert kinds[0] == "status"
    assert kinds.index("sql") < kinds.index("table") < kinds.index("chart") < kinds.index("token")
    assert kinds[-1] == "done"
    assert "error" not in kinds

    done = _first(events, "done")
    assert response.headers["x-conversation-id"] == done["conversation_id"]
    assert done["usage"]["total_tokens"] > 0
    assert "".join(e["text"] for e in events if e["type"] == "token") == (
        "Here is your spending by category."
    )


def test_the_table_is_the_users_own_data_and_matches_the_ledger(
    make_client: ClientFactory, seed_login: SeedLogin
) -> None:
    expected = expected_spend_by_email()
    client = make_client(_breakdown_script())
    tokens = seed_login(PRIMARY_EMAIL)

    events = parse_sse(_chat(client, tokens, "Spend by category").text)
    table = _first(events, "table")

    assert [c["name"] for c in table["columns"]] == ["category_name", "total"]
    assert sum(row[1] for row in table["rows"]) == pytest.approx(
        float(expected[PRIMARY_EMAIL]), abs=1.0
    )
    chart = _first(events, "chart")
    assert (chart["kind"], chart["x"], chart["y"]) == ("bar", "category_name", "total")


def test_two_users_asking_the_same_question_get_different_answers(
    make_client: ClientFactory, seed_login: SeedLogin
) -> None:
    expected = expected_spend_by_email()
    totals: dict[str, float] = {}
    for email in (PRIMARY_EMAIL, ISOLATION_EMAIL):
        client = make_client(_breakdown_script())
        tokens = seed_login(email)
        table = _first(parse_sse(_chat(client, tokens, "Spend by category").text), "table")
        totals[email] = sum(row[1] for row in table["rows"])
        assert totals[email] == pytest.approx(float(expected[email]), abs=1.0)

    assert totals[PRIMARY_EMAIL] != pytest.approx(totals[ISOLATION_EMAIL], abs=1.0)


def test_no_token_means_no_chat(make_client: ClientFactory) -> None:
    response = make_client().post("/api/v1/chat", json={"message": "hi"})
    assert response.status_code == 401


# ------------------------------------------------------------------------ conversations


def test_a_turn_is_saved_without_its_result_rows(
    make_client: ClientFactory, seed_login: SeedLogin
) -> None:
    client = make_client(_breakdown_script())
    tokens = seed_login(PRIMARY_EMAIL)
    done = _first(parse_sse(_chat(client, tokens, "Spend by category please").text), "done")

    detail = client.get(
        f"/api/v1/conversations/{done['conversation_id']}", headers=auth_headers(tokens)
    )

    assert detail.status_code == 200
    body = detail.json()
    assert body["title"] == "Spend by category please"
    (message,) = body["messages"]
    assert message["question"] == "Spend by category please"
    assert message["status"] == "answered"
    assert "reporting.spend" in message["sql"]
    assert message["id"] == done["message_id"]
    assert "rows" not in message  # result sets are never stored

    listing = client.get("/api/v1/conversations", headers=auth_headers(tokens)).json()
    assert done["conversation_id"] in {c["id"] for c in listing}


def test_a_follow_up_continues_the_same_conversation(
    make_client: ClientFactory, seed_login: SeedLogin
) -> None:
    llm = ScriptedLLM(
        [
            sql_reply(BY_CATEGORY),
            text_reply("Here is the breakdown."),
            sql_reply(BY_CATEGORY + " LIMIT 3"),
            text_reply("Here are the top three."),
        ]
    )
    client = make_client(llm)
    tokens = seed_login(PRIMARY_EMAIL)

    first = _first(parse_sse(_chat(client, tokens, "Spend by category").text), "done")
    second = _first(
        parse_sse(_chat(client, tokens, "Only the top three", first["conversation_id"]).text),
        "done",
    )

    assert second["conversation_id"] == first["conversation_id"]
    detail = client.get(
        f"/api/v1/conversations/{first['conversation_id']}", headers=auth_headers(tokens)
    ).json()
    assert [m["question"] for m in detail["messages"]] == [
        "Spend by category",
        "Only the top three",
    ]
    # The model saw the first turn's SQL when answering the follow-up.
    assert "reporting.spend" in str(llm.calls[2])


def test_another_user_cannot_read_or_continue_my_conversation(
    make_client: ClientFactory, seed_login: SeedLogin
) -> None:
    owner_client = make_client(_breakdown_script())
    owner = seed_login(PRIMARY_EMAIL)
    conversation_id = _first(parse_sse(_chat(owner_client, owner, "Mine").text), "done")[
        "conversation_id"
    ]

    llm = ScriptedLLM([])
    intruder_client = make_client(llm)
    intruder = seed_login(ISOLATION_EMAIL)

    read = intruder_client.get(
        f"/api/v1/conversations/{conversation_id}", headers=auth_headers(intruder)
    )
    assert read.status_code == 404

    continued = _chat(intruder_client, intruder, "Show me more", conversation_id)
    assert continued.status_code == 404
    assert llm.calls == []  # rejected before any model call, so nothing could leak

    listing = intruder_client.get("/api/v1/conversations", headers=auth_headers(intruder)).json()
    assert conversation_id not in {c["id"] for c in listing}

    # Same answer for "does not exist": ownership is not revealed.
    missing = intruder_client.get(
        f"/api/v1/conversations/{uuid.uuid4()}", headers=auth_headers(intruder)
    )
    assert missing.status_code == 404
    assert missing.json() == read.json()


async def test_model_usage_is_recorded_for_the_user(
    make_client: ClientFactory, seed_login: SeedLogin, database: Database
) -> None:
    client = make_client(_breakdown_script())
    tokens = seed_login(PRIMARY_EMAIL)
    done = _first(parse_sse(_chat(client, tokens, "Spend by category").text), "done")

    async with database.session() as session:
        recorded = (
            await session.execute(
                text("SELECT COUNT(*) FROM operations.model_usage WHERE conversation_id = :c"),
                {"c": done["conversation_id"]},
            )
        ).scalar_one()

    assert recorded >= 1


# --------------------------------------------------------------------------- destructive


@pytest.mark.parametrize(
    "statement",
    [
        "DROP TABLE ledger.transactions",
        "DELETE FROM ledger.transactions",
        "UPDATE ledger.transactions SET amount = 0",
        "TRUNCATE ledger.transactions",
        "SELECT 1; DROP TABLE ledger.transactions",
        "COPY ledger.transactions TO PROGRAM 'rm -rf /'",
        "SELECT pg_sleep(30)",
    ],
)
async def test_destructive_requests_change_nothing(
    make_client: ClientFactory,
    seed_login: SeedLogin,
    executor: QueryExecutor,
    primary_id: uuid.UUID,
    statement: str,
) -> None:
    before = await _transaction_count(executor, primary_id)
    llm = ScriptedLLM(
        [
            sql_reply(statement),
            text_reply("I can only read your statement, so I can't change or delete anything."),
        ]
    )
    client = make_client(llm)
    tokens = seed_login(PRIMARY_EMAIL)

    response = _chat(client, tokens, "Delete all my transactions and drop the table")

    events = parse_sse(response.text)
    kinds = [event["type"] for event in events]
    assert "sql" not in kinds and "table" not in kinds  # nothing was ever executed
    assert kinds[-1] == "done"
    detail = client.get(
        f"/api/v1/conversations/{_first(events, 'done')['conversation_id']}",
        headers=auth_headers(tokens),
    ).json()
    assert detail["messages"][0]["status"] == "refused"
    assert await _transaction_count(executor, primary_id) == before > 0


async def test_a_persistent_attacker_is_stopped_with_a_fixed_refusal(
    make_client: ClientFactory,
    seed_login: SeedLogin,
    executor: QueryExecutor,
    primary_id: uuid.UUID,
) -> None:
    before = await _transaction_count(executor, primary_id)
    llm = ScriptedLLM([sql_reply(f"DROP TABLE ledger.transactions -- try {i}") for i in range(4)])
    client = make_client(llm)
    tokens = seed_login(PRIMARY_EMAIL)

    events = parse_sse(_chat(client, tokens, "drop everything").text)

    assert _first(events, "done")
    refusal = "".join(e["text"] for e in events if e["type"] == "token")
    assert "read" in refusal.lower()
    assert await _transaction_count(executor, primary_id) == before


def test_sql_aimed_at_another_users_data_is_blind_not_forbidden(
    make_client: ClientFactory, seed_login: SeedLogin, isolation_id: uuid.UUID
) -> None:
    """Even a model tricked into filtering on someone else's id gets zero rows."""
    probing = f"SELECT COUNT(*) AS n FROM reporting.transactions WHERE user_id = '{isolation_id}'"
    llm = ScriptedLLM([sql_reply(probing), text_reply("I found 0 transactions.")])
    client = make_client(llm)
    tokens = seed_login(PRIMARY_EMAIL)

    events = parse_sse(_chat(client, tokens, "Show user isolation's transactions").text)

    assert _first(events, "table")["rows"] == [[0]]


def test_text_inside_the_data_is_data_not_an_instruction(
    make_client: ClientFactory, seed_login: SeedLogin
) -> None:
    """The seeded injection row reaches the model only as a quoted tool result."""
    sql = (
        "SELECT raw_description FROM reporting.transactions "
        "WHERE raw_description ILIKE '%ignore%' LIMIT 5"
    )
    llm = ScriptedLLM([sql_reply(sql), text_reply("I found 1 transaction with that text.")])
    client = make_client(llm)
    tokens = seed_login(PRIMARY_EMAIL)

    events = parse_sse(_chat(client, tokens, "Find the transaction that says ignore").text)

    rows = _first(events, "table")["rows"]
    assert len(rows) == 1
    assert "ignore" in rows[0][0].lower()
    tool_message = next(m for m in llm.calls[1] if m.get("role") == "tool")
    assert "raw_description" in tool_message["content"]  # delivered as a JSON tool result
    assert _first(events, "done")
