-- ============================================================================
-- Campus AI - non-bypass application role for Row-Level Security (TEMPLATE)
-- ----------------------------------------------------------------------------
-- STATUS: UNVERIFIED DRAFT written in Phase 3. It has never been executed.
-- Review it against your actual database before applying anything.
--
-- It replaces the old role script, which was deliberately NOT restored because
-- it embedded a literal password. This file contains no credential: the
-- password is supplied at run time and never written to disk.
--
-- WHY: RLS policies are ignored by superusers, by roles with BYPASSRLS, and
-- (unless FORCE ROW LEVEL SECURITY is set) by the table owner. The application
-- must therefore connect as a separate, unprivileged role. Migrations keep
-- using the owner role (MIGRATION_DATABASE_URL) because they run DDL.
--
-- HOW TO RUN (interactive psql only - uses psql variables, \gexec and
-- \password; a web SQL editor will not understand it). Connect as the
-- owner/admin role; psql prompts for the new role's password:
--
--   psql "$OWNER_DATABASE_URL" -v ON_ERROR_STOP=1 -f app_role.template.sql
--
-- Optional overrides:  -v app_role=campus_app  -v owner_role=postgres
-- The connection string for the app (DATABASE_URL) then uses this role and the
-- password you entered; store it only in your secret manager.
-- ============================================================================

\set ON_ERROR_STOP on

\if :{?app_role}
\else
  \set app_role app_user
\endif
\if :{?owner_role}
\else
  \set owner_role postgres
\endif

-- 1) Create the role if missing, WITHOUT a password (it cannot log in with
--    password auth until step 2). A new role is NOSUPERUSER / NOBYPASSRLS /
--    NOCREATEDB / NOCREATEROLE by default; they are not spelled out because
--    some managed hosts do not let a non-superuser admin set those attributes.
--    Step 8 proves the result instead of assuming it.
SELECT format('CREATE ROLE %I LOGIN', :'app_role')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_role')
\gexec

-- 2) Set the password interactively. \password prompts twice without echo and
--    sends only the hashed form to the server, so the plaintext never appears
--    in this file, in shell history, in argv or in server logs. Re-running
--    this file is how you rotate the password. Use a NEW random value.
\echo 'Set a NEW, randomly generated password for the application role:'
\password :app_role

-- 3) Connect to the current database.
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'app_role')
\gexec

-- 4) Schema usage: public = application tables, app = the RLS helper function.
GRANT USAGE ON SCHEMA public TO :"app_role";
GRANT USAGE ON SCHEMA app    TO :"app_role";

-- 5) DML on existing tables + sequences. No DDL, no TRUNCATE, no ownership.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA public TO :"app_role";
GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA public TO :"app_role";

--    The app has no reason to modify migration bookkeeping.
REVOKE INSERT, UPDATE, DELETE ON TABLE public.alembic_version FROM :"app_role";

-- 6) The helper the tenant policies call.
GRANT EXECUTE ON FUNCTION app.current_tenant_id() TO :"app_role";

-- 7) Tables/sequences the owner creates later (future migrations).
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner_role" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner_role" IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO :"app_role";

-- 8) Verification - every value in this row MUST be false.
SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb
FROM pg_roles WHERE rolname = :'app_role';

--    The app role must not own any table (owners bypass non-FORCEd RLS).
--    Expect zero rows:
SELECT schemaname, tablename FROM pg_tables
WHERE schemaname = 'public' AND tableowner = :'app_role';

--    Tables that have a tenant_id column but RLS is not enabled AND forced.
--    Review every row returned (expect zero once all policies are in place):
SELECT c.relname, c.relrowsecurity AS rls_enabled, c.relforcerowsecurity AS rls_forced
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
WHERE c.relkind = 'r' AND NOT (c.relrowsecurity AND c.relforcerowsecurity)
ORDER BY 1;
