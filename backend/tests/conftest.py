"""Shared fixtures and fakes.

Unit tests need nothing. Tests marked ``integration`` need the ``FINQON_*`` database URLs
(from ``.env`` or the environment), a migrated database, and the seed data. They skip
themselves with a clear reason when that is not available.
"""

from __future__ import annotations

import csv
import json
import os
import uuid
import warnings
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from finqon.core.config import Settings, get_settings
from finqon.core.security import create_access_token
from finqon.db.session import Database, create_engine
from finqon.db.urls import parse_database_url
from finqon.llm.client import ChatResult, LLMError, Message, ToolCall, ToolSpec
from finqon.main import create_app
from finqon.repositories.users import UserRepository
from finqon.sql.executor import QueryExecutor

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / "dataset" / "output"
PRIMARY_EMAIL = "kalpeshpawar@gmail.com"
ISOLATION_EMAIL = "seed.isolation@finqon.example"
TEST_EMAIL_DOMAIN = "tests.finqon.example"


def unit_settings(**overrides: Any) -> Settings:
    """Settings that never touch a real database or model."""
    values: dict[str, Any] = {
        "api_database_url": "postgresql://api:pw@127.0.0.1:1/none?sslmode=disable",
        "reader_database_url": "postgresql://reader:pw@127.0.0.1:1/none?sslmode=disable",
        "jwt_secret": "unit-test-secret-" + "x" * 32,
        "llm_api_key": "not-a-real-key",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings() -> Settings:
    return unit_settings()


# ----------------------------------------------------------------------------- fakes


class ScriptedLLM:
    """Replays a fixed list of model replies and records what it was sent."""

    def __init__(self, replies: Sequence[ChatResult | Exception]) -> None:
        self._replies = list(replies)
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None,
        stream: bool = False,
        on_text: Any = None,
    ) -> ChatResult:
        self.calls.append([dict(m) for m in messages])
        if not self._replies:
            raise AssertionError("ScriptedLLM ran out of replies")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if on_text is not None and reply.content and not reply.tool_calls:
            # Deliver the text in small pieces, like a real stream.
            words = reply.content.split(" ")
            for index, word in enumerate(words):
                await on_text(word + (" " if index < len(words) - 1 else ""))
        return reply


def tool_reply(name: str, arguments: dict[str, Any], call_id: str = "call_1") -> ChatResult:
    return ChatResult(
        content=None,
        tool_calls=[ToolCall(call_id, name, json.dumps(arguments))],
        model="fake-model",
        prompt_tokens=100,
        completion_tokens=20,
        latency_ms=5,
    )


def text_reply(text: str) -> ChatResult:
    return ChatResult(
        content=text, model="fake-model", prompt_tokens=120, completion_tokens=30, latency_ms=5
    )


def sql_reply(sql: str, purpose: str = "test", hint: str = "none", call_id: str = "call_1"):
    return tool_reply(
        "run_sql_query", {"sql": sql, "purpose": purpose, "chart_hint": hint}, call_id
    )


__all__ = ["LLMError", "ScriptedLLM", "sql_reply", "text_reply", "tool_reply", "unit_settings"]


# -------------------------------------------------------------------------- integration


def _integration_settings() -> Settings:
    get_settings.cache_clear()
    settings = get_settings()
    missing = [
        name
        for name in ("FINQON_API_DATABASE_URL", "FINQON_READER_DATABASE_URL", "FINQON_JWT_SECRET")
        if name in settings.missing_runtime_settings()
    ]
    if missing:
        pytest.skip(f"integration tests need {', '.join(missing)} (see .env.example)")
    return settings


@pytest.fixture(scope="session")
def integration_settings(cleanup_test_users: None) -> Settings:
    return _integration_settings()


SeedLogin = Callable[[str], dict[str, str]]


@pytest.fixture(scope="session")
def seed_user_ids(integration_settings: Settings) -> dict[str, uuid.UUID]:
    """Ids of the two seed users. Skips if the seed data is not loaded."""
    emails = [PRIMARY_EMAIL, ISOLATION_EMAIL]
    url = parse_database_url(integration_settings.api_database_url.get_secret_value()).url
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT email, id FROM identity.users WHERE email = ANY(%s)", (emails,)
        ).fetchall()
    found = {email: user_id for email, user_id in rows}
    missing = [email for email in emails if email not in found]
    if missing:
        pytest.skip(f"seed users not loaded: {', '.join(missing)}; run dataset/load_ledger.py")
    return found


@pytest.fixture
def seed_login(integration_settings: Settings, seed_user_ids: dict[str, uuid.UUID]) -> SeedLogin:
    """Access tokens for the seed users, signed with the app's own secret.

    Tests of the ledger and chat do not depend on any human's password. The API still
    verifies the token exactly as it would a real login; ``test_auth.py`` covers the
    password flow with throwaway users.
    """

    def mint(email: str) -> dict[str, str]:
        token = create_access_token(
            seed_user_ids[email],
            secret=integration_settings.jwt_secret.get_secret_value(),
            issuer=integration_settings.jwt_issuer,
            ttl_minutes=integration_settings.jwt_access_ttl_minutes,
        )
        return {"access_token": token.token}

    return mint


def expected_spend_by_email() -> dict[str, Decimal]:
    """Net spend per seed user, recomputed from the CSV files with the documented rule.

    Spend = debits that are not transfers, minus refunds. Independent of the SQL views.
    """
    with (OUTPUT_DIR / "accounts.csv").open(newline="") as handle:
        owner = {row["id"]: row["user_email"] for row in csv.DictReader(handle)}
    totals: dict[str, Decimal] = {}
    with (OUTPUT_DIR / "transactions.csv").open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row["txn_type"] == "transfer":
                continue
            amount = Decimal(row["amount"])
            email = owner[row["account_id"]]
            if row["direction"] == "debit":
                totals[email] = totals.get(email, Decimal(0)) + amount
            elif row["txn_type"] == "refund":
                totals[email] = totals.get(email, Decimal(0)) - amount
    return totals


@pytest.fixture(scope="session")
def cleanup_test_users() -> Iterator[None]:
    """Remove users created by tests when an owner URL is available."""
    yield
    # Settings, not os.environ: the owner URL normally lives in .env.
    admin_url = get_settings().admin_database_url.get_secret_value()
    if not admin_url:
        return
    try:
        with psycopg.connect(parse_database_url(admin_url).url, autocommit=True) as conn:
            conn.execute(
                "DELETE FROM identity.users WHERE email LIKE %s", (f"%@{TEST_EMAIL_DOMAIN}",)
            )
    except psycopg.Error as exc:
        warnings.warn(f"could not clean up test users: {exc}", stacklevel=1)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests of settings must not see the developer's FINQON_* environment."""
    for name in list(os.environ):
        if name.startswith("FINQON_"):
            monkeypatch.delenv(name)


def new_test_email() -> str:
    return f"user-{uuid.uuid4().hex[:10]}@{TEST_EMAIL_DOMAIN}"


# ------------------------------------------------------------ integration fixtures


def parse_sse(body: str) -> list[dict[str, Any]]:
    """Parse a Server-Sent Events body into the JSON payload of each frame."""
    events: list[dict[str, Any]] = []
    for frame in body.strip().split("\n\n"):
        data = [line[5:].strip() for line in frame.splitlines() if line.startswith("data:")]
        if data:
            events.append(json.loads("".join(data)))
    return events


@pytest.fixture
async def executor(integration_settings: Settings) -> AsyncIterator[QueryExecutor]:
    instance = await QueryExecutor.create(
        integration_settings.reader_database_url.get_secret_value(),
        timezone=integration_settings.timezone,
    )
    yield instance
    await instance.close()


@pytest.fixture
async def database(integration_settings: Settings) -> AsyncIterator[Database]:
    db = Database(create_engine(integration_settings.api_database_url.get_secret_value()))
    yield db
    await db.dispose()


async def user_id_by_email(database: Database, email: str) -> uuid.UUID:
    async with database.session() as session:
        found = await UserRepository(session).get_by_email(email)
    if found is None:
        pytest.skip(f"seed user {email} is not loaded; run dataset/load_ledger.py")
    return found.id


@pytest.fixture
async def primary_id(database: Database) -> uuid.UUID:
    return await user_id_by_email(database, PRIMARY_EMAIL)


@pytest.fixture
async def isolation_id(database: Database) -> uuid.UUID:
    return await user_id_by_email(database, ISOLATION_EMAIL)


@pytest.fixture
def make_client(integration_settings: Settings) -> Iterator[Callable[..., TestClient]]:
    """Build an app against the real database with a scripted model."""
    opened: list[TestClient] = []

    def factory(llm: ScriptedLLM | None = None, **overrides: Any) -> TestClient:
        settings = integration_settings.model_copy(update=overrides)
        app = create_app(settings, llm=llm or ScriptedLLM([]))
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        opened.append(client)
        return client

    yield factory
    for client in opened:
        client.__exit__(None, None, None)


def login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def auth_headers(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}
