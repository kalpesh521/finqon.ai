"""Async SQLAlchemy engine for the ``finqon_api`` role.

Tables with row-level security (``conversation.*``) are only touched through
``user_scope``, which sets ``finqon.user_id`` for the transaction. Identity tables
have no RLS because authentication happens before the user is known.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from finqon.db.urls import async_engine_connect_args, parse_database_url

SET_USER_SQL = text("SELECT set_config('finqon.user_id', :user_id, true)")


def create_engine(database_url: str) -> AsyncEngine:
    parsed = parse_database_url(database_url)
    return create_async_engine(
        parsed.sqlalchemy_async(),
        connect_args=async_engine_connect_args(parsed),
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,  # Neon suspends idle computes and drops connections.
        pool_recycle=300,
    )


class Database:
    """Owns the engine and hands out transactional sessions."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self._factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """A transaction without a user scope (identity and usage tables)."""
        async with self._factory() as session, session.begin():
            yield session

    @asynccontextmanager
    async def user_scope(self, user_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
        """A transaction in which row-level security sees only ``user_id``'s rows."""
        async with self._factory() as session, session.begin():
            await session.execute(SET_USER_SQL, {"user_id": str(user_id)})
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
