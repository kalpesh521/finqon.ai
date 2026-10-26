-- FinQon.ai application roles. Run once as the database owner, before `alembic upgrade head`.
--
--   psql "$FINQON_ADMIN_DATABASE_URL" \
--        -v api_password=... -v reader_password=... -v loader_password=... \
--        -f db/init/roles.sql
--
-- or let the helper read the passwords from the three role URLs in .env:
--
--   python -m finqon.db.bootstrap_roles
--
-- Safe to re-run: existing roles keep their identity and get the new password.
-- None of the roles can bypass row-level security.

\set ON_ERROR_STOP on

SELECT format(
    'CREATE ROLE %I LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT',
    r.name)
FROM (VALUES ('finqon_api'), ('finqon_reader'), ('finqon_loader')) AS r(name)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r.name)
\gexec

SELECT format('ALTER ROLE finqon_api    PASSWORD %L', :'api_password')    \gexec
SELECT format('ALTER ROLE finqon_reader PASSWORD %L', :'reader_password') \gexec
SELECT format('ALTER ROLE finqon_loader PASSWORD %L', :'loader_password') \gexec

-- Enforce the non-superuser attributes even if a role already existed.
-- Do not mention SUPERUSER. CREATE ROLE already set NOSUPERUSER, and a hosted owner
-- cannot change that attribute. The local Docker user `finqon` is a superuser and
-- does not need this script to alter itself.
ALTER ROLE finqon_api    NOBYPASSRLS NOCREATEDB NOCREATEROLE;
ALTER ROLE finqon_reader NOBYPASSRLS NOCREATEDB NOCREATEROLE;
ALTER ROLE finqon_loader NOBYPASSRLS NOCREATEDB NOCREATEROLE;

-- The reader can never write, whatever the application asks for.
ALTER ROLE finqon_reader SET default_transaction_read_only = on;
ALTER ROLE finqon_reader SET statement_timeout = '5s';
ALTER ROLE finqon_reader SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE finqon_reader CONNECTION LIMIT 20;
