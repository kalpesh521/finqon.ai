"""Registration, login, refresh-token rotation, and logout."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from finqon.core.config import Settings
from finqon.core.errors import AuthenticationError, ConflictError
from finqon.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from finqon.db.session import Database
from finqon.repositories.refresh_tokens import RefreshTokenRepository
from finqon.repositories.users import UserRecord, UserRepository
from finqon.schemas.auth import TokenPair

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self._db = database
        self._settings = settings

    async def register(
        self, email: str, password: str, display_name: str | None = None
    ) -> UserRecord:
        password_hash = hash_password(password)
        async with self._db.session() as session:
            user = await UserRepository(session).create(email, password_hash, display_name)
        if user is None:
            raise ConflictError("An account with this email already exists.")
        logger.info("user_registered", extra={"user_id": str(user.id)})
        return user

    async def login(self, email: str, password: str) -> TokenPair:
        async with self._db.session() as session:
            user = await UserRepository(session).get_by_email(email)
            # Always verify a hash so unknown emails cost the same as wrong passwords.
            password_ok = verify_password(
                password, user.password_hash if user else DUMMY_PASSWORD_HASH
            )
            if user is None or not password_ok:
                raise AuthenticationError("Invalid email or password.", code="invalid_credentials")
            pair = await self._issue_pair(RefreshTokenRepository(session), user.id)
        logger.info("user_logged_in", extra={"user_id": str(user.id)})
        return pair

    async def refresh(self, refresh_token: str) -> TokenPair:
        token_hash = hash_refresh_token(refresh_token)
        reuse_of_user: uuid.UUID | None = None
        pair: TokenPair | None = None

        async with self._db.session() as session:
            tokens = RefreshTokenRepository(session)
            record = await tokens.get_by_hash(token_hash)
            if record is None:
                raise AuthenticationError("Invalid refresh token.", code="invalid_refresh_token")
            if record.revoked_at is not None:
                reuse_of_user = record.user_id
                await tokens.revoke_all_for_user(record.user_id)
            elif record.expires_at <= datetime.now(UTC):
                raise AuthenticationError("Refresh token expired.", code="refresh_token_expired")
            elif not await tokens.revoke_if_live(record.id):
                # Lost a race with another request using the same token: treat as reuse.
                reuse_of_user = record.user_id
                await tokens.revoke_all_for_user(record.user_id)
            else:
                pair = await self._issue_pair(tokens, record.user_id)

        # Raised after the transaction commits so the revocations persist.
        if reuse_of_user is not None or pair is None:
            logger.warning("refresh_token_reuse", extra={"user_id": str(reuse_of_user)})
            raise AuthenticationError(
                "Refresh token already used. Sign in again.", code="refresh_token_reused"
            )
        return pair

    async def logout(self, refresh_token: str) -> None:
        async with self._db.session() as session:
            await RefreshTokenRepository(session).revoke_by_hash(hash_refresh_token(refresh_token))

    async def get_user(self, user_id: uuid.UUID) -> UserRecord:
        async with self._db.session() as session:
            user = await UserRepository(session).get_by_id(user_id)
        if user is None:
            raise AuthenticationError("Account no longer exists.", code="invalid_token")
        return user

    async def _issue_pair(self, tokens: RefreshTokenRepository, user_id: uuid.UUID) -> TokenPair:
        settings = self._settings
        access = create_access_token(
            user_id,
            secret=settings.jwt_secret.get_secret_value(),
            issuer=settings.jwt_issuer,
            ttl_minutes=settings.jwt_access_ttl_minutes,
        )
        refresh_token = new_refresh_token()
        await tokens.add(
            user_id,
            hash_refresh_token(refresh_token),
            datetime.now(UTC) + timedelta(days=settings.jwt_refresh_ttl_days),
        )
        return TokenPair(
            access_token=access.token,
            refresh_token=refresh_token,
            expires_in=access.expires_in,
        )
