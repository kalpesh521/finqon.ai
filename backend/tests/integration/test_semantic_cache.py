"""The semantic cache against the real database (and, optionally, a real Redis Stack).

The model is scripted. A cache hit must be served with the script *exhausted*: an unexpected
model call raises, which surfaces as an ``error`` event and fails the assertion.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from finqon.db.session import Database
from finqon.repositories.statements import StatementRepository
from tests.conftest import (
    ISOLATION_EMAIL,
    PRIMARY_EMAIL,
    ScriptedLLM,
    SeedLogin,
    auth_headers,
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
QUESTION = "Show my spending by category"
ANSWER = "Here is your spending by category."
# The exact tier needs no embedding; give the embedder no time so tests never wait on it.
CACHE_ON: dict[str, Any] = {
    "cache_mode": "on",
    "cache_backend": "memory",
    "cache_embed_timeout_ms": 1,
}


def _script() -> ScriptedLLM:
    return ScriptedLLM([sql_reply(BY_CATEGORY, hint="bar"), text_reply(ANSWER)])


def _chat(client: TestClient, tokens: dict[str, str], message: str) -> list[dict[str, Any]]:
    response = client.post("/api/v1/chat", headers=auth_headers(tokens), json={"message": message})
    assert response.status_code == 200, response.text
    return parse_sse(response.text)


def _answer(events: list[dict[str, Any]]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "token").strip()


# ------------------------------------------------------------------------ ledger version


async def _version(database: Database, user_id: uuid.UUID) -> str:
    async with database.user_scope(user_id) as session:
        return await StatementRepository(session).data_version()


async def test_data_version_is_stable_and_private_to_each_user(
    database: Database, primary_id: uuid.UUID, isolation_id: uuid.UUID
) -> None:
    first = await _version(database, primary_id)
    assert first == await _version(database, primary_id)
    assert first != await _version(database, isolation_id)


# ---------------------------------------------------------------------------- over HTTP


def test_a_repeated_question_is_served_without_the_model(
    make_client: ClientFactory, seed_login: SeedLogin
) -> None:
    llm = _script()
    client = make_client(llm, **CACHE_ON)
    tokens = seed_login(PRIMARY_EMAIL)

    first = _chat(client, tokens, QUESTION)
    assert first[-1]["type"] == "done" and first[-1]["usage"]["total_tokens"] > 0
    assert len(llm.calls) == 2

    second = _chat(client, tokens, QUESTION)
    assert second[-1]["type"] == "done"
    assert second[-1]["usage"]["total_tokens"] == 0
    assert len(llm.calls) == 2  # unchanged: the model was not called
    assert _answer(second) == ANSWER
    tables = [e for e in second if e["type"] == "table"]
    assert tables and tables[0]["rows"] == next(e for e in first if e["type"] == "table")["rows"]


def test_another_user_asking_the_same_question_never_sees_the_cached_answer(
    make_client: ClientFactory, seed_login: SeedLogin
) -> None:
    llm = _script()
    client = make_client(llm, **CACHE_ON)
    _chat(client, seed_login(PRIMARY_EMAIL), QUESTION)
    assert len(llm.calls) == 2

    llm_for_other = ScriptedLLM([sql_reply(BY_CATEGORY, hint="bar"), text_reply(ANSWER)])
    other_client = make_client(llm_for_other, **CACHE_ON)  # separate process: separate cache
    _chat(other_client, seed_login(ISOLATION_EMAIL), QUESTION)
    assert len(llm_for_other.calls) == 2

    # Same process, different user: the entry exists but belongs to someone else.
    again = ScriptedLLM([sql_reply(BY_CATEGORY, hint="bar"), text_reply(ANSWER)])
    shared = make_client(again, **CACHE_ON)
    _chat(shared, seed_login(PRIMARY_EMAIL), QUESTION)
    _chat(shared, seed_login(ISOLATION_EMAIL), QUESTION)
    assert len(again.calls) == 4  # the second user paid for their own answer


def test_cache_off_changes_nothing(make_client: ClientFactory, seed_login: SeedLogin) -> None:
    llm = ScriptedLLM(
        [sql_reply(BY_CATEGORY), text_reply(ANSWER), sql_reply(BY_CATEGORY), text_reply(ANSWER)]
    )
    client = make_client(llm)
    tokens = seed_login(PRIMARY_EMAIL)
    _chat(client, tokens, QUESTION)
    _chat(client, tokens, QUESTION)
    assert len(llm.calls) == 4


# ------------------------------------------------------------------------- real Redis

REDIS_URL = os.environ.get("FINQON_TEST_REDIS_URL", "")


@pytest.mark.skipif(not REDIS_URL, reason="set FINQON_TEST_REDIS_URL to a Redis Stack server")
async def test_redis_store_round_trip_against_a_real_server() -> None:
    """Index creation, both tiers, filters, purge. Needs Redis Stack (RediSearch)."""
    from finqon.cache.models import STABLE_DAY, CachedAnswer, SearchFilter  # noqa: PLC0415
    from finqon.cache.redis_store import RedisStore  # noqa: PLC0415

    prefix = f"finqon:test:{uuid.uuid4().hex[:8]}"
    store = RedisStore.from_url(REDIS_URL, prefix=prefix, dimensions=8)
    user, other = uuid.uuid4().hex, uuid.uuid4().hex
    entry = CachedAnswer(
        user=user, question="q", signature="a" * 32, sql="SELECT 1", purpose="p",
        chart_kind="none", truncated=False, answer="a", result_digest="d" * 64, row_count=1,
        data_version="v1", schema_version="s1", day=STABLE_DAY, created_at=1.0,
    )  # fmt: skip
    vector = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    flt = SearchFilter(user, "v1", "s1", "a" * 32, (STABLE_DAY,))
    try:
        await store.ensure_ready()
        await store.ensure_ready()  # idempotent
        await store.put("k" * 40, entry, vector, ttl_seconds=60)

        assert (await store.get_exact(user, ["k" * 40]))[0] == entry
        found = await store.search(vector, flt, 3)
        assert len(found) == 1 and found[0].similarity == pytest.approx(1.0, abs=1e-4)
        assert (
            await store.search(vector, SearchFilter(other, "v1", "s1", "a" * 32, (STABLE_DAY,)), 3)
            == []
        )
        assert (
            await store.search(vector, SearchFilter(user, "v2", "s1", "a" * 32, (STABLE_DAY,)), 3)
            == []
        )
        assert await store.purge_user(user) == 2
        assert await store.search(vector, flt, 3) == []
    finally:
        await store.purge_user(user)
        await store.close()
