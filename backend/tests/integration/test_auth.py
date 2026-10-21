"""Registration, login, refresh rotation, logout and abuse limits against a real database."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from finqon.core.security import hash_refresh_token
from finqon.db.session import Database
from tests.conftest import auth_headers, new_test_email

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery"
ClientFactory = Callable[..., TestClient]


def _register(client: TestClient, email: str | None = None, password: str = PASSWORD) -> str:
    email = email or new_test_email()
    response = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return email


def _login(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_register_then_login_then_me(make_client: ClientFactory) -> None:
    client = make_client()
    email = _register(client)

    response = _login(client, email)
    assert response.status_code == 200
    tokens = response.json()
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] > 0

    me = client.get("/api/v1/auth/me", headers=auth_headers(tokens))
    assert me.status_code == 200
    assert me.json()["email"] == email


def test_register_response_never_contains_the_password_hash(make_client: ClientFactory) -> None:
    client = make_client()
    response = client.post(
        "/api/v1/auth/register", json={"email": new_test_email(), "password": PASSWORD}
    )
    assert set(response.json()) == {"id", "email", "created_at"}


def test_duplicate_email_is_a_conflict_regardless_of_case(make_client: ClientFactory) -> None:
    client = make_client()
    email = _register(client)

    again = client.post(
        "/api/v1/auth/register", json={"email": email.upper(), "password": PASSWORD}
    )
    assert again.status_code == 409
    assert again.json()["code"] == "conflict"


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "password": PASSWORD},
        {"email": "ok@tests.finqon.example", "password": "short"},
        {"email": "ok@tests.finqon.example"},
    ],
)
def test_invalid_registration_is_rejected(make_client: ClientFactory, payload: dict) -> None:
    assert make_client().post("/api/v1/auth/register", json=payload).status_code == 422


def test_wrong_password_and_unknown_email_look_identical(make_client: ClientFactory) -> None:
    client = make_client()
    email = _register(client)

    wrong = _login(client, email, "not the password")
    unknown = _login(client, new_test_email())

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.headers["www-authenticate"] == "Bearer"


def test_refresh_rotates_and_a_replayed_token_revokes_the_whole_family(
    make_client: ClientFactory,
) -> None:
    client = make_client()
    email = _register(client)
    first = _login(client, email).json()

    second = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert second.status_code == 200
    assert second.json()["refresh_token"] != first["refresh_token"]

    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401
    assert replay.json()["code"] == "refresh_token_reused"

    # The legitimate successor was revoked too: the attacker may have it.
    successor = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": second.json()["refresh_token"]}
    )
    assert successor.status_code == 401
    assert successor.json()["code"] == "refresh_token_reused"


def test_unknown_refresh_token_is_rejected(make_client: ClientFactory) -> None:
    response = make_client().post("/api/v1/auth/refresh", json={"refresh_token": "x" * 43})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_refresh_token"


def test_expired_refresh_token_is_rejected(make_client: ClientFactory) -> None:
    client = make_client(jwt_refresh_ttl_days=0)
    email = _register(client)
    tokens = _login(client, email).json()

    response = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 401
    assert response.json()["code"] == "refresh_token_expired"


def test_logout_revokes_the_refresh_token_and_is_idempotent(make_client: ClientFactory) -> None:
    client = make_client()
    email = _register(client)
    tokens = _login(client, email).json()

    body = {"refresh_token": tokens["refresh_token"]}
    assert client.post("/api/v1/auth/logout", json=body).status_code == 204
    assert client.post("/api/v1/auth/logout", json=body).status_code == 204
    assert client.post("/api/v1/auth/logout", json={"refresh_token": "u" * 43}).status_code == 204

    assert client.post("/api/v1/auth/refresh", json=body).status_code == 401


def test_login_is_rate_limited_per_email(make_client: ClientFactory) -> None:
    client = make_client(auth_rate_limit_attempts=3, auth_rate_limit_window_seconds=60)
    email = _register(client)

    statuses = [_login(client, email, "wrong password").status_code for _ in range(4)]

    assert statuses[:3] == [401, 401, 401]
    assert statuses[3] == 429
    limited = _login(client, email)
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1
    assert limited.json()["code"] == "rate_limited"


async def test_secrets_are_stored_hashed_and_never_logged(
    make_client: ClientFactory, database: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    client = make_client()
    email = _register(client)
    tokens = _login(client, email).json()
    client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    _login(client, email, "wrong password attempt")

    async with database.session() as session:
        plain = (
            await session.execute(
                text("SELECT COUNT(*) FROM identity.refresh_tokens WHERE token_hash = :t"),
                {"t": tokens["refresh_token"]},
            )
        ).scalar()
        hashed = (
            await session.execute(
                text("SELECT COUNT(*) FROM identity.refresh_tokens WHERE token_hash = :t"),
                {"t": hash_refresh_token(tokens["refresh_token"])},
            )
        ).scalar()
        password_hash = (
            await session.execute(
                text("SELECT password_hash FROM identity.users WHERE email = :e"), {"e": email}
            )
        ).scalar()

    assert plain == 0
    assert hashed == 1
    assert password_hash.startswith("$argon2id$")
    assert PASSWORD not in password_hash

    logged = capsys.readouterr().out
    # Guard against a vacuous pass: the auth events must really have been captured.
    assert '"user_registered"' in logged
    assert '"user_logged_in"' in logged

    for secret in (PASSWORD, "wrong password attempt", tokens["refresh_token"]):
        assert secret not in logged
    assert tokens["access_token"] not in logged
