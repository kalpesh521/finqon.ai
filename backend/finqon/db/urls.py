"""Connection URL helpers.

Neon hands out libpq-style URLs (``postgresql://...?sslmode=require&channel_binding=require``).
asyncpg and SQLAlchemy's asyncpg dialect do not understand those query options, so
they are translated here. The same URLs keep working on self-hosted PostgreSQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SSL_DISABLED = {"disable", "allow", "prefer"}
_LIBPQ_ONLY_OPTIONS = {"sslmode", "channel_binding", "sslrootcert", "sslcert", "sslkey"}


@dataclass(frozen=True, slots=True)
class DatabaseUrl:
    """A parsed libpq-style URL split into a driver-neutral part and SSL intent."""

    url: str
    ssl: bool

    def asyncpg_dsn(self) -> str:
        """DSN for ``asyncpg``. Unknown query options would become server settings."""
        return _strip_libpq_options(self.url)

    def sqlalchemy_async(self) -> str:
        return _with_scheme(_strip_libpq_options(self.url), "postgresql+asyncpg")

    def sqlalchemy_sync(self) -> str:
        """psycopg (v3) URL for Alembic and the seed loader; libpq options are native here."""
        return _with_scheme(self.url, "postgresql+psycopg")


def _with_scheme(url: str, scheme: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((scheme, parts.netloc, parts.path, parts.query, parts.fragment))


def _strip_libpq_options(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in _LIBPQ_ONLY_OPTIONS]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def parse_database_url(raw: str) -> DatabaseUrl:
    """Normalise ``postgres://`` and ``postgresql+driver://`` inputs to ``postgresql://``."""
    parts = urlsplit(raw.strip())
    scheme = parts.scheme.split("+", 1)[0]
    if scheme not in {"postgres", "postgresql"}:
        raise ValueError("Database URL must start with postgresql://")
    options = dict(parse_qsl(parts.query))
    ssl = options.get("sslmode", "prefer") not in _SSL_DISABLED
    url = urlunsplit(("postgresql", parts.netloc, parts.path, parts.query, parts.fragment))
    return DatabaseUrl(url=url, ssl=ssl)


def async_engine_connect_args(db_url: DatabaseUrl) -> dict[str, object]:
    return {"ssl": True} if db_url.ssl else {}
