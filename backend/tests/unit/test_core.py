"""Settings, security primitives, URL handling, rate limiting, and the SSE encoder."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from finqon.core.config import ConfigurationError, Settings
from finqon.core.errors import AuthenticationError, RateLimitedError
from finqon.core.rate_limit import SlidingWindowRateLimiter
from finqon.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from finqon.db.urls import parse_database_url
from finqon.schemas.chat import DoneEvent, StatusEvent, TokenUsage, encode_sse
from tests.conftest import unit_settings

SECRET = "s" * 40
ISSUER = "finqon.ai"
USER = uuid.uuid4()


# ------------------------------------------------------------------------------- JWT


def token(**overrides: object) -> str:
    kwargs = {"secret": SECRET, "issuer": ISSUER, "ttl_minutes": 15}
    kwargs.update(overrides)
    return create_access_token(USER, **kwargs).token  # type: ignore[arg-type]


def test_access_token_round_trip() -> None:
    assert decode_access_token(token(), secret=SECRET, issuer=ISSUER) == USER


def test_expired_token_is_rejected() -> None:
    expired = token(now=datetime.now(UTC) - timedelta(hours=1))
    with pytest.raises(AuthenticationError) as info:
        decode_access_token(expired, secret=SECRET, issuer=ISSUER)
    assert info.value.code == "token_expired"


@pytest.mark.parametrize(
    "bad",
    [
        "not-a-jwt",
        "",
        "a.b.c",
    ],
)
def test_garbage_tokens_are_rejected(bad: str) -> None:
    with pytest.raises(AuthenticationError) as info:
        decode_access_token(bad, secret=SECRET, issuer=ISSUER)
    assert info.value.code == "invalid_token"


def test_wrong_secret_wrong_issuer_and_tampering_are_rejected() -> None:
    with pytest.raises(AuthenticationError):
        decode_access_token(token(secret="o" * 40), secret=SECRET, issuer=ISSUER)
    with pytest.raises(AuthenticationError):
        decode_access_token(token(issuer="someone.else"), secret=SECRET, issuer=ISSUER)
    header, payload, signature = token().split(".")
    with pytest.raises(AuthenticationError):
        decode_access_token(f"{header}.{payload}x.{signature}", secret=SECRET, issuer=ISSUER)


def test_unsigned_and_wrong_algorithm_tokens_are_rejected() -> None:
    claims = {"sub": str(USER), "iss": ISSUER, "exp": datetime.now(UTC) + timedelta(minutes=5)}
    unsigned = jwt.encode(claims, key="", algorithm="none")
    with pytest.raises(AuthenticationError):
        decode_access_token(unsigned, secret=SECRET, issuer=ISSUER)
    hs512 = jwt.encode(claims, SECRET * 2, algorithm="HS512")
    with pytest.raises(AuthenticationError):
        decode_access_token(hs512, secret=SECRET, issuer=ISSUER)


def test_token_without_required_claims_is_rejected() -> None:
    no_sub = jwt.encode(
        {"iss": ISSUER, "exp": datetime.now(UTC) + timedelta(minutes=5)}, SECRET, algorithm="HS256"
    )
    no_exp = jwt.encode({"sub": str(USER), "iss": ISSUER}, SECRET, algorithm="HS256")
    not_uuid = jwt.encode(
        {"sub": "nope", "iss": ISSUER, "exp": datetime.now(UTC) + timedelta(minutes=5)},
        SECRET,
        algorithm="HS256",
    )
    for bad in (no_sub, no_exp, not_uuid):
        with pytest.raises(AuthenticationError):
            decode_access_token(bad, secret=SECRET, issuer=ISSUER)


# ---------------------------------------------------------------------- passwords/tokens


def test_passwords_use_argon2id_and_verify() -> None:
    hashed = hash_password("correct horse battery")
    assert hashed.startswith("$argon2id$")
    assert verify_password("correct horse battery", hashed)
    assert not verify_password("wrong", hashed)
    assert hash_password("correct horse battery") != hashed  # salted


def test_refresh_tokens_are_random_and_hashed() -> None:
    first, second = new_refresh_token(), new_refresh_token()
    assert first != second
    assert len(first) >= 48
    digest = hash_refresh_token(first)
    assert digest != first
    assert len(digest) == 64
    assert hash_refresh_token(first) == digest


# --------------------------------------------------------------------------- settings


def test_missing_runtime_settings_lists_names_not_values(clean_env: None) -> None:
    empty = Settings(_env_file=None)
    missing = empty.missing_runtime_settings()
    assert "FINQON_JWT_SECRET" in missing
    assert "FINQON_LLM_API_KEY" in missing
    with pytest.raises(ConfigurationError, match="FINQON_API_DATABASE_URL"):
        empty.require_runtime_settings()


def test_llm_key_can_be_deferred() -> None:
    settings = unit_settings(llm_api_key="")
    with pytest.raises(ConfigurationError):
        settings.require_runtime_settings()
    settings.require_runtime_settings(require_llm=False)


def test_short_jwt_secret_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="at least 32"):
        unit_settings(jwt_secret="short").require_runtime_settings()


def test_cors_origins_parse_comma_separated(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FINQON_CORS_ORIGINS", "http://a.test, http://b.test")
    assert Settings(_env_file=None).cors_origins == ["http://a.test", "http://b.test"]


def test_unknown_timezone_is_rejected(clean_env: None) -> None:
    with pytest.raises(ValueError, match="unknown timezone"):
        Settings(_env_file=None, timezone="Mars/Olympus")


def test_secrets_are_not_in_repr() -> None:
    assert "unit-test-secret" not in repr(unit_settings())


# ---------------------------------------------------------------------------------- URLs


def test_neon_style_url_is_split_per_driver() -> None:
    url = parse_database_url(
        "postgresql://u:p@ep-x.neon.tech/db?sslmode=require&channel_binding=require"
    )
    assert url.ssl
    assert "channel_binding" not in url.asyncpg_dsn()
    assert url.sqlalchemy_async().startswith("postgresql+asyncpg://")
    assert "sslmode" not in url.sqlalchemy_async()
    assert url.sqlalchemy_sync().startswith("postgresql+psycopg://")
    assert "sslmode=require" in url.sqlalchemy_sync()


def test_plain_and_driver_prefixed_urls() -> None:
    assert not parse_database_url("postgres://u:p@localhost/db?sslmode=disable").ssl
    assert parse_database_url("postgresql+asyncpg://u:p@h/db").url.startswith("postgresql://")
    with pytest.raises(ValueError, match="postgresql"):
        parse_database_url("mysql://u:p@h/db")


# ----------------------------------------------------------------------- rate limiter


def test_rate_limiter_blocks_then_recovers() -> None:
    now = [0.0]
    limiter = SlidingWindowRateLimiter(2, 10, clock=lambda: now[0])
    limiter.check("a")
    limiter.check("a")
    with pytest.raises(RateLimitedError) as info:
        limiter.check("a")
    assert int(info.value.headers["Retry-After"]) >= 1
    limiter.check("b")  # other keys are independent
    now[0] = 11.0
    limiter.check("a")


# --------------------------------------------------------------------------------- SSE


def test_sse_frame_shape() -> None:
    frame = encode_sse(StatusEvent(stage="thinking"))
    assert frame == 'event: status\ndata: {"type":"status","stage":"thinking"}\n\n'
    done = encode_sse(
        DoneEvent(message_id=uuid.uuid4(), conversation_id=uuid.uuid4(), usage=TokenUsage())
    )
    assert done.startswith("event: done\ndata: ")
    assert done.endswith("\n\n")
