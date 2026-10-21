"""Alembic environment.

Migrations run as the database owner (``FINQON_ADMIN_DATABASE_URL``) because they
create schemas, enable extensions, and grant privileges. The application roles
never run DDL. Migrations are plain SQL, so there is no ORM metadata.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context
from finqon.core.config import ConfigurationError, get_settings
from finqon.db.urls import parse_database_url

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = None


def _database_url() -> str:
    secret = get_settings().admin_database_url.get_secret_value()
    if not secret:
        raise ConfigurationError(
            "FINQON_ADMIN_DATABASE_URL is empty. Migrations must run as the database owner."
        )
    return parse_database_url(secret).sqlalchemy_sync()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported; run against a database.")
run_migrations_online()
