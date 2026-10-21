-- Extensions, schemas and base privileges. Idempotent. The login roles themselves are
-- cluster-wide and created once by db/init/10_roles.sh.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS content;      -- documents + chunks (all collections)
CREATE SCHEMA IF NOT EXISTS public_data;  -- normalised COB tables
CREATE SCHEMA IF NOT EXISTS private;      -- uploaded sensitive tables

-- Nobody gets anything by default.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

GRANT USAGE ON SCHEMA app, content TO cob_app;
GRANT USAGE ON SCHEMA app, content, public_data, private TO cob_ingest;
-- Read-only role sees public_data only; private tables are granted per collection later.
GRANT USAGE ON SCHEMA public_data TO cob_readonly;
