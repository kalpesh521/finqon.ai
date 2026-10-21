from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: uuid.UUID
    email: str
    password_hash: str
    created_at: dt.datetime


_COLUMNS = "id, email, password_hash, created_at"


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, email: str, password_hash: str) -> UserRecord | None:
        """Insert a user. Returns ``None`` when the email is already registered."""
        result = await self._session.execute(
            text(
                f"""
                INSERT INTO identity.users (email, password_hash)
                VALUES (:email, :password_hash)
                ON CONFLICT (email) DO NOTHING
                RETURNING {_COLUMNS}
                """
            ),
            {"email": email, "password_hash": password_hash},
        )
        row = result.one_or_none()
        return UserRecord(*row) if row else None

    async def get_by_email(self, email: str) -> UserRecord | None:
        result = await self._session.execute(
            text(f"SELECT {_COLUMNS} FROM identity.users WHERE email = :email"),
            {"email": email},
        )
        row = result.one_or_none()
        return UserRecord(*row) if row else None

    async def get_by_id(self, user_id: uuid.UUID) -> UserRecord | None:
        result = await self._session.execute(
            text(f"SELECT {_COLUMNS} FROM identity.users WHERE id = :id"),
            {"id": user_id},
        )
        row = result.one_or_none()
        return UserRecord(*row) if row else None
