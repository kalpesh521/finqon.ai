"""HTTP behaviour that needs no database: authentication failures and the contract."""

import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from finqon.api.statement import STATEMENT_SQL, _count, _date
from finqon.core.config import ConfigurationError
from finqon.core.security import create_access_token
from finqon.main import create_app
from finqon.resources import Resources
from finqon.schemas.conversations import ConversationUpdate
from finqon.sql.guard import validate_sql
from tests.conftest import ScriptedLLM, unit_settings

SETTINGS = unit_settings()
SECRET = SETTINGS.jwt_secret.get_secret_value()


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(SETTINGS, llm=ScriptedLLM([]))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def access_token(**overrides: object) -> str:
    kwargs = {"secret": SECRET, "issuer": SETTINGS.jwt_issuer, "ttl_minutes": 15}
    kwargs.update(overrides)
    return create_access_token(uuid4(), **kwargs).token  # type: ignore[arg-type]


PROTECTED = [
    ("GET", "/api/v1/auth/me", None),
    ("POST", "/api/v1/chat", {"message": "hi"}),
    ("GET", "/api/v1/conversations", None),
    ("GET", f"/api/v1/conversations/{uuid4()}", None),
    ("PATCH", f"/api/v1/conversations/{uuid4()}", {"title": "Rent"}),
    ("DELETE", f"/api/v1/conversations/{uuid4()}", None),
]


def test_conversation_update_rejects_an_empty_change() -> None:
    with pytest.raises(ValidationError):
        ConversationUpdate()
    with pytest.raises(ValidationError):
        ConversationUpdate(title="   ")
    assert ConversationUpdate(title="  Rent   ").title == "Rent"
    assert ConversationUpdate(pinned=False).pinned is False


def test_health_is_public(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"]


def test_ready_is_503_when_databases_are_unreachable(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"api_database": False, "reader_database": False},
    }
    assert client.get("/health").status_code == 200  # liveness does not depend on them


def test_ready_is_200_when_databases_answer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def healthy(_self: object) -> dict[str, bool]:
        return {"api_database": True, "reader_database": True}

    monkeypatch.setattr(Resources, "check_ready", healthy)
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED)
def test_missing_token_is_401(
    client: TestClient, method: str, path: str, body: dict | None
) -> None:
    response = client.request(method, path, json=body)
    assert response.status_code == 401
    assert response.json()["code"] == "missing_token"
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED)
def test_bad_jwt_is_401(client: TestClient, method: str, path: str, body: dict | None) -> None:
    response = client.request(method, path, json=body, headers=bearer("not.a.jwt"))
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED)
def test_expired_jwt_is_401(client: TestClient, method: str, path: str, body: dict | None) -> None:
    expired = access_token(now=datetime.now(UTC) - timedelta(hours=2))
    response = client.request(method, path, json=body, headers=bearer(expired))
    assert response.status_code == 401
    assert response.json()["code"] == "token_expired"


def test_token_signed_with_another_secret_is_401(client: TestClient) -> None:
    forged = access_token(secret="a-different-secret-" + "z" * 32)
    response = client.get("/api/v1/auth/me", headers=bearer(forged))
    assert response.status_code == 401


def test_request_id_is_echoed_when_valid_and_replaced_when_not(client: TestClient) -> None:
    assert (
        client.get("/health", headers={"X-Request-ID": "trace-12345678"}).headers["x-request-id"]
        == "trace-12345678"
    )
    assert (
        client.get("/health", headers={"X-Request-ID": "bad id!"}).headers["x-request-id"]
        != "bad id!"
    )


def test_registration_validates_input_before_touching_the_database(client: TestClient) -> None:
    assert (
        client.post(
            "/api/v1/auth/register", json={"email": "nope", "password": "x" * 12}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/auth/register", json={"email": "a@b.co", "password": "short"}
        ).status_code
        == 422
    )


def test_statement_summary_is_a_guarded_read(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert "/api/v1/statement" in schema["paths"]
    guarded = validate_sql(STATEMENT_SQL)
    assert "reporting.transactions" in guarded.sql
    assert _count(538) == 538
    assert _count(None) == 0
    assert _date("2026-04-01") == date(2026, 4, 1)
    assert _date(None) is None


def test_openapi_exposes_the_chat_event_contract(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    event = schema["components"]["schemas"]["ChatEvent"]
    assert event["discriminator"]["propertyName"] == "type"
    names = {ref["$ref"].rsplit("/", 1)[1] for ref in event["oneOf"]}
    assert names == {
        "StatusEvent",
        "SqlEvent",
        "TableEvent",
        "ChartEvent",
        "ClarificationEvent",
        "TokenEvent",
        "RetractEvent",
        "DoneEvent",
        "ErrorEvent",
    }
    chat = schema["paths"]["/api/v1/chat"]["post"]["responses"]["200"]["content"]
    assert list(chat) == ["text/event-stream"]
    assert chat["text/event-stream"]["schema"]["$ref"].endswith("/ChatEvent")
    for name, model in schema["components"]["schemas"].items():
        for field in model.get("properties", {}):
            assert re.fullmatch(r"[a-z][a-z0-9_]*", field), f"{name}.{field} is not snake_case"
    done = schema["components"]["schemas"]["DoneEvent"]["properties"]
    assert {"message_id", "usage"} <= set(done)


def test_app_refuses_to_start_without_required_settings() -> None:
    app = create_app(unit_settings(jwt_secret=""), llm=ScriptedLLM([]))
    with pytest.raises(ConfigurationError, match="FINQON_JWT_SECRET"), TestClient(app):
        pass
