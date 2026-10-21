from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class RefreshTokenRecord:
    id: uuid.UUID
    user_id: uuid.UUID
    expires_at: dt.datetime
    revoked_at: dt.datetime | None


class RefreshTokenRepository:
    """Only token hashes are stored; the opaque token is never persisted."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user_id: uuid.UUID, token_hash: str, expires_at: dt.datetime) -> None:
        await self._session.execute(
            text(
                """
                INSERT INTO identity.refresh_tokens (user_id, token_hash, expires_at)
                VALUES (:user_id, :token_hash, :expires_at)
                """
            ),
            {"user_id": user_id, "token_hash": token_hash, "expires_at": expires_at},
        )

    async def get_by_hash(self, token_hash: str) -> RefreshTokenRecord | None:
        result = await self._session.execute(
            text(
                """
                SELECT id, user_id, expires_at, revoked_at
                FROM identity.refresh_tokens
                WHERE token_hash = :token_hash
                """
            ),
            {"token_hash": token_hash},
        )
        row = result.one_or_none()
        return RefreshTokenRecord(*row) if row else None

    async def revoke_if_live(self, token_id: uuid.UUID) -> bool:
        """Atomically revoke a live token. ``False`` means someone else already did."""
        result = await self._session.execute(
            text(
                """
                UPDATE identity.refresh_tokens
                SET revoked_at = now()
                WHERE id = :id AND revoked_at IS NULL
                RETURNING id
                """
            ),
            {"id": token_id},
        )
        return result.one_or_none() is not None

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        result = await self._session.execute(
            text(
                """
                UPDATE identity.refresh_tokens
                SET revoked_at = now()
                WHERE user_id = :user_id AND revoked_at IS NULL
                RETURNING id
                """
            ),
            {"user_id": user_id},
        )
        return len(result.all())

    async def revoke_by_hash(self, token_hash: str) -> None:
        await self._session.execute(
            text(
                """
                UPDATE identity.refresh_tokens
                SET revoked_at = now()
                WHERE token_hash = :token_hash AND revoked_at IS NULL
                """
            ),
            {"token_hash": token_hash},
        )
