"""Create the application roles using the passwords already present in ``.env``.

    python -m finqon.db.bootstrap_roles

Connects as the owner (``FINQON_ADMIN_DATABASE_URL``), then runs ``db/init/roles.sql``
through ``psql``. Passwords are read from the three role URLs and passed on stdin so
they never appear in the process list.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit

from finqon.core.config import REPO_ROOT, ConfigurationError, get_settings
from finqon.db.urls import parse_database_url

ROLES_SQL = REPO_ROOT / "db" / "init" / "roles.sql"

_ROLE_FOR_VARIABLE = {
    "api_password": "finqon_api",
    "reader_password": "finqon_reader",
    "loader_password": "finqon_loader",
}


def _password_and_user(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    if not parts.password or not parts.username:
        raise ConfigurationError("Role URLs must contain a username and password.")
    return unquote(parts.username), unquote(parts.password)


def _psql_literal(value: str) -> str:
    """Quote for psql's ``\\set`` (backslashes and quotes are escaped)."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def build_script() -> str:
    settings = get_settings()
    urls = {
        "api_password": settings.api_database_url.get_secret_value(),
        "reader_password": settings.reader_database_url.get_secret_value(),
        "loader_password": settings.loader_database_url.get_secret_value(),
    }
    lines: list[str] = []
    for variable, url in urls.items():
        if not url:
            raise ConfigurationError(f"The URL for {_ROLE_FOR_VARIABLE[variable]} is empty.")
        user, password = _password_and_user(url)
        if user != _ROLE_FOR_VARIABLE[variable]:
            raise ConfigurationError(
                f"Expected user {_ROLE_FOR_VARIABLE[variable]} in its URL, found {user}."
            )
        lines.append(f"\\set {variable} {_psql_literal(password)}")
    lines.append(f"\\i {ROLES_SQL}")
    return "\n".join(lines) + "\n"


def main() -> int:
    settings = get_settings()
    admin_url = settings.admin_database_url.get_secret_value()
    if not admin_url:
        raise ConfigurationError("FINQON_ADMIN_DATABASE_URL is empty.")
    psql = shutil.which("psql")
    if psql is None:
        raise ConfigurationError("psql is required. Install the PostgreSQL client.")

    # psql wants a plain libpq URL; drop any SQLAlchemy driver suffix.
    dsn = parse_database_url(admin_url).url
    result = subprocess.run(
        [psql, dsn, "--no-psqlrc", "--quiet", "-v", "ON_ERROR_STOP=1"],
        input=build_script(),
        text=True,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
