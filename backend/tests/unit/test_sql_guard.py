import pytest

from finqon.sql.guard import MAX_LIMIT, SqlGuardError, validate_sql


def _reject(sql: str, *, forbidden: bool | None = None) -> SqlGuardError:
    with pytest.raises(SqlGuardError) as info:
        validate_sql(sql)
    if forbidden is not None:
        assert info.value.forbidden is forbidden
    return info.value


# --- Required rejections -------------------------------------------------------------


def test_drop_table_is_rejected() -> None:
    _reject("DROP TABLE reporting.spend", forbidden=True)


def test_stacked_statements_are_rejected() -> None:
    error = _reject("SELECT 1 FROM reporting.spend; DROP TABLE ledger.transactions", forbidden=True)
    assert "one SQL statement" in str(error)


def test_disallowed_table_is_rejected() -> None:
    error = _reject("SELECT * FROM ledger.transactions", forbidden=True)
    assert "reporting.spend" in str(error)  # tells the model what it may query


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_sleep(10)",
        "SELECT pg_catalog.pg_sleep(10)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT * FROM dblink('host=x', 'select 1') AS t(a int)",
        "SELECT lo_import('/etc/passwd')",
        "SELECT lo_export(1, '/tmp/x')",
        "SELECT set_config('finqon.user_id', 'someone-else', false)",
        "SELECT current_setting('finqon.user_id')",
        "SELECT query_to_xml('select * from identity.users', true, true, '')",
        "SELECT pg_terminate_backend(1)",
    ],
)
def test_denied_functions_are_rejected(sql: str) -> None:
    _reject(sql, forbidden=True)


def test_missing_limit_is_added() -> None:
    guarded = validate_sql("SELECT merchant_name FROM reporting.spend")
    assert guarded.limit == MAX_LIMIT
    assert guarded.limit_rewritten
    assert guarded.sql.endswith(f"LIMIT {MAX_LIMIT}")


# --- LIMIT rewriting -----------------------------------------------------------------


def test_excessive_limit_is_lowered() -> None:
    guarded = validate_sql("SELECT * FROM reporting.spend LIMIT 100000")
    assert guarded.limit == MAX_LIMIT
    assert "100000" not in guarded.sql


def test_small_limit_is_kept() -> None:
    guarded = validate_sql("SELECT * FROM reporting.spend LIMIT 5")
    assert guarded.limit == 5
    assert not guarded.limit_rewritten


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM reporting.spend LIMIT ALL",
        "SELECT * FROM reporting.spend FETCH FIRST 100000 ROWS ONLY",
    ],
)
def test_limit_all_and_fetch_are_capped(sql: str) -> None:
    guarded = validate_sql(sql)
    assert guarded.limit == MAX_LIMIT
    assert guarded.sql.endswith(f"LIMIT {MAX_LIMIT}")


def test_union_gets_a_limit_on_the_whole_statement() -> None:
    guarded = validate_sql(
        "SELECT merchant_name FROM reporting.spend UNION SELECT merchant_name FROM reporting.transactions"
    )
    assert guarded.sql.endswith(f"LIMIT {MAX_LIMIT}")


# --- Other write and escape attempts -------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO reporting.spend VALUES (1)",
        "UPDATE reporting.spend SET spend_amount = 0",
        "DELETE FROM reporting.spend",
        "ALTER TABLE ledger.transactions ADD COLUMN x int",
        "CREATE TABLE t AS SELECT 1",
        "COPY reporting.spend TO STDOUT",
        "GRANT ALL ON reporting.spend TO public",
        "TRUNCATE ledger.transactions",
        "SET ROLE finqon_loader",
        "SELECT * INTO new_table FROM reporting.spend",
        "SELECT * FROM reporting.spend FOR UPDATE",
        "WITH gone AS (DELETE FROM ledger.transactions RETURNING *) SELECT * FROM gone",
        "WITH ins AS (INSERT INTO ledger.accounts DEFAULT VALUES RETURNING *) SELECT * FROM ins",
    ],
)
def test_write_and_escape_statements_are_rejected(sql: str) -> None:
    _reject(sql, forbidden=True)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM identity.users",
        "SELECT * FROM spend",  # not schema-qualified
        "SELECT * FROM otherdb.reporting.spend",
        "SELECT * FROM reporting.spend s JOIN identity.refresh_tokens r ON true",
        "SELECT (SELECT count(*) FROM ledger.accounts)",
        "SELECT * FROM pg_sleep(1)",
    ],
)
def test_tables_outside_the_allowlist_are_rejected(sql: str) -> None:
    _reject(sql, forbidden=True)


@pytest.mark.parametrize(
    "sql", ["", "   ", "SELEC * FROM", "SELECT * FROM reporting.spend WHERE ("]
)
def test_invalid_or_empty_sql_is_rejected(sql: str) -> None:
    _reject(sql)


def test_overlong_sql_is_rejected() -> None:
    _reject("SELECT " + "1," * 10_000 + "1")


# --- Valid queries -------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SUM(spend_amount) FROM reporting.spend WHERE category_name = 'Dining'",
        "SELECT * FROM reporting.transactions WHERE amount > 5000 ORDER BY amount DESC",
        "SELECT month, category_name, spend FROM reporting.monthly_category_spend",
        "WITH m AS (SELECT month, SUM(spend) AS t FROM reporting.monthly_category_spend GROUP BY 1) "
        "SELECT * FROM m ORDER BY t DESC LIMIT 1",
        "SELECT merchant_name FROM reporting.spend UNION ALL SELECT merchant_name FROM reporting.transactions",
        "SELECT date_trunc('month', current_date - interval '1 month')::date",
        "SELECT g::date FROM generate_series(date '2026-01-01', date '2026-03-01', interval '1 month') g",
    ],
)
def test_valid_read_queries_pass(sql: str) -> None:
    guarded = validate_sql(sql)
    assert guarded.sql.upper().startswith(("SELECT", "WITH"))
    assert guarded.limit <= MAX_LIMIT


def test_cte_name_may_shadow_nothing_dangerous() -> None:
    # A CTE alias is allowed as a table, but its body is still checked.
    _reject("WITH t AS (SELECT * FROM ledger.accounts) SELECT * FROM t", forbidden=True)


def test_comments_cannot_hide_a_second_statement() -> None:
    _reject("SELECT 1 FROM reporting.spend; /* ok */ DROP TABLE x", forbidden=True)


def test_executed_sql_is_the_regenerated_statement() -> None:
    guarded = validate_sql("select  *   from reporting.spend  -- trailing comment")
    assert "--" not in guarded.sql
