-- Least-privilege PostgreSQL roles for production (run once as a database superuser).
-- * sr_owner: owns the schema and tables; used ONLY to run migrations.
-- * sr_app:   used by the running application; can read and write rows but cannot
--             ALTER/DROP tables, disable or drop the protective triggers, TRUNCATE,
--             or set session_replication_role (verified in Phase 5).
-- Replace the passwords (use a secret manager), and require TLS in pg_hba.conf.

CREATE ROLE sr_owner LOGIN PASSWORD 'CHANGE-ME-owner';
CREATE ROLE sr_app   LOGIN PASSWORD 'CHANGE-ME-app';
CREATE DATABASE sr_academy OWNER sr_owner;
\c sr_academy
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO sr_owner;
GRANT USAGE ON SCHEMA public TO sr_app;
ALTER DEFAULT PRIVILEGES FOR ROLE sr_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sr_app;
ALTER DEFAULT PRIVILEGES FOR ROLE sr_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO sr_app;
ALTER DEFAULT PRIVILEGES FOR ROLE sr_owner IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO sr_app;
