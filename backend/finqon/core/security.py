"""Password hashing, access JWTs, and opaque refresh tokens."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from finqon.core.errors import AuthenticationError

_ALGORITHM = "HS256"
_password_hash = PasswordHash((Argon2Hasher(),))  # argon2id


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hash.verify(password, password_hash)


# Used to spend the same time on unknown emails as on wrong passwords.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(16))


@dataclass(frozen=True, slots=True)
class AccessToken:
    token: str
    expires_in: int


def create_access_token(
    user_id: uuid.UUID,
    *,
    secret: str,
    issuer: str,
    ttl_minutes: int,
    now: datetime | None = None,
) -> AccessToken:
    issued = now or datetime.now(UTC)
    expires = issued + timedelta(minutes=ttl_minutes)
    payload = {"sub": str(user_id), "iss": issuer, "iat": issued, "exp": expires}
    return AccessToken(
        token=jwt.encode(payload, secret, algorithm=_ALGORITHM),
        expires_in=ttl_minutes * 60,
    )


def decode_access_token(token: str, *, secret: str, issuer: str) -> uuid.UUID:
    """Return the user id from a valid token or raise ``AuthenticationError``."""
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[_ALGORITHM],
            issuer=issuer,
            options={"require": ["exp", "sub", "iss"]},
        )
        return uuid.UUID(str(claims["sub"]))
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Access token expired.", code="token_expired") from exc
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise AuthenticationError("Invalid access token.", code="invalid_token") from exc


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Refresh tokens are high-entropy random strings, so a fast hash is sufficient."""
    return hashlib.sha256(token.encode()).hexdigest()
