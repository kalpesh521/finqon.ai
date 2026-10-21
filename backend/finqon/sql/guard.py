"""Static validation of model-written SQL.

The guard parses the statement with sqlglot (Postgres dialect), rejects anything
that is not a single read-only ``SELECT``, enforces the table allowlist and the
function denylist, and rewrites ``LIMIT``. The SQL that is executed is the SQL
re-generated from the validated tree, so what was checked is what runs.

This is one of several layers: the executor adds a read-only transaction, a
statement timeout and row-level security, and the database role is unprivileged.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

MAX_LIMIT = 500
MAX_SQL_LENGTH = 8_000

ALLOWED_TABLES: frozenset[tuple[str, str]] = frozenset(
    {
        ("reporting", "transactions"),
        ("reporting", "spend"),
        ("reporting", "monthly_category_spend"),
    }
)

# Statement types that must not appear anywhere in the tree (including CTEs).
_FORBIDDEN_NODE_NAMES = (
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Drop",
    "Alter",
    "AlterTable",
    "AlterColumn",
    "Create",
    "Copy",
    "Grant",
    "Revoke",
    "TruncateTable",
    "Command",
    "Set",
    "SetItem",
    "Transaction",
    "Commit",
    "Rollback",
    "Use",
    "Pragma",
    "Into",  # SELECT ... INTO creates a table
    "Lock",  # SELECT ... FOR UPDATE
    "Kill",
    "Analyze",
    "Cache",
    "Uncache",
    "LoadData",
    "Refresh",
)
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    node for name in _FORBIDDEN_NODE_NAMES if (node := getattr(exp, name, None)) is not None
)

DENIED_FUNCTIONS: frozenset[str] = frozenset(
    {
        "pg_sleep",
        "pg_read_file",
        "dblink",
        "lo_import",
        "lo_export",
        # Beyond the required five: functions that execute SQL or expose session state.
        "set_config",
        "current_setting",
        "query_to_xml",
        "query_to_xml_and_xmlschema",
        "table_to_xml",
        "cursor_to_xml",
        "schema_to_xml",
        "database_to_xml",
        "nextval",
        "setval",
        "currval",
        "lastval",
        "inet_server_addr",
        "inet_client_addr",
    }
)
DENIED_FUNCTION_PREFIXES: tuple[str, ...] = ("pg_", "lo_", "dblink")

# Set-returning table functions the model may use (for example to fill empty months).
_ALLOWED_TABLE_FUNCTIONS = ("ExplodingGenerateSeries", "GenerateSeries")


class SqlGuardError(ValueError):
    """The SQL was rejected. The message is safe to show to the model."""

    def __init__(self, message: str, *, forbidden: bool = False) -> None:
        super().__init__(message)
        # ``forbidden`` marks attempts to write or escape, which must not be "repaired".
        self.forbidden = forbidden


@dataclass(frozen=True, slots=True)
class GuardedSql:
    sql: str
    limit: int
    limit_rewritten: bool


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return str(node.sql_name()).lower()


def _is_denied_function(name: str) -> bool:
    return name in DENIED_FUNCTIONS or name.startswith(DENIED_FUNCTION_PREFIXES)


def _parse_single_statement(sql: str) -> exp.Expression:
    if not sql or not sql.strip():
        raise SqlGuardError("The query is empty.")
    if len(sql) > MAX_SQL_LENGTH:
        raise SqlGuardError("The query is too long.")
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except SqlglotError as exc:
        raise SqlGuardError(f"The query is not valid PostgreSQL: {str(exc)[:200]}") from exc
    if len(statements) != 1:
        raise SqlGuardError("Exactly one SQL statement is allowed.", forbidden=True)
    statement = statements[0]
    if not isinstance(statement, exp.Expression):  # pragma: no cover - parser contract
        raise SqlGuardError("The query is not valid PostgreSQL.")
    return statement


def _check_root(root: exp.Expression) -> None:
    if not isinstance(root, exp.Select | exp.Union):
        raise SqlGuardError("Only SELECT queries are allowed.", forbidden=True)


def _check_forbidden_nodes(root: exp.Expression) -> None:
    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise SqlGuardError(
                f"{type(node).__name__.upper()} is not allowed. "
                "Only read-only SELECT is permitted.",
                forbidden=True,
            )


def _check_tables(root: exp.Expression) -> None:
    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    for table in root.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            if type(table.this).__name__ in _ALLOWED_TABLE_FUNCTIONS:
                continue
            raise SqlGuardError("Table functions are not allowed.", forbidden=True)
        name = table.name.lower()
        schema = table.db.lower()
        if table.catalog:
            raise SqlGuardError("Cross-database references are not allowed.", forbidden=True)
        if not schema and name in cte_names:
            continue
        if (schema, name) not in ALLOWED_TABLES:
            allowed = ", ".join(f"{s}.{t}" for s, t in sorted(ALLOWED_TABLES))
            raise SqlGuardError(
                f"Table '{table.sql(dialect='postgres')}' is not allowed. "
                f"Query only: {allowed} (always schema-qualified).",
                forbidden=True,
            )


def _check_functions(root: exp.Expression) -> None:
    for func in root.find_all(exp.Func):
        name = _function_name(func)
        if _is_denied_function(name):
            raise SqlGuardError(f"Function '{name}' is not allowed.", forbidden=True)


def _literal_limit(limit: exp.Expression | None) -> int | None:
    """Return the numeric value of a plain ``LIMIT n``; ``None`` for anything else."""
    if not isinstance(limit, exp.Limit):
        return None
    expression = limit.expression
    if isinstance(expression, exp.Literal) and not expression.is_string:
        try:
            return int(expression.name)
        except ValueError:
            return None
    return None


def _enforce_limit(root: exp.Expression, max_limit: int) -> tuple[int, bool]:
    """Ensure the root has a literal ``LIMIT`` of at most ``max_limit``.

    Returns the effective limit and whether the tree was rewritten.
    """
    current = _literal_limit(root.args.get("limit"))
    if current is not None and 0 <= current <= max_limit:
        return current, False
    # Missing, ``LIMIT ALL``, ``FETCH FIRST``, an expression, or above the cap.
    root.set("limit", exp.Limit(expression=exp.Literal.number(max_limit)))
    return max_limit, True


def validate_sql(sql: str, *, max_limit: int = MAX_LIMIT) -> GuardedSql:
    """Validate ``sql`` and return the rewritten statement to execute.

    Raises ``SqlGuardError`` when any rule is violated.
    """
    root = _parse_single_statement(sql)
    _check_root(root)
    _check_forbidden_nodes(root)
    _check_tables(root)
    _check_functions(root)
    limit, rewritten = _enforce_limit(root, max_limit)
    return GuardedSql(sql=root.sql(dialect="postgres"), limit=limit, limit_rewritten=rewritten)
