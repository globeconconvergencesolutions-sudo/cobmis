#!/bin/bash
# Runs once, on first initialisation of the Postgres volume.
# Creates extensions, schemas and three least-privilege login roles:
#   cob_app       - API runtime (app schema read/write; reads documents/chunks)
#   cob_ingest    - ingestion worker (writes public_data/private/content)
#   cob_readonly  - text-to-SQL executor (SELECT only, short statement_timeout)
# Per-table grants are added by Alembic migrations as tables are created.
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_pw="$COB_APP_PASSWORD" \
  -v ingest_pw="$COB_INGEST_PASSWORD" \
  -v ro_pw="$COB_READONLY_PASSWORD" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS content;      -- documents + chunks (all collections)
CREATE SCHEMA IF NOT EXISTS public_data;  -- normalised COB tables
CREATE SCHEMA IF NOT EXISTS private;      -- uploaded sensitive tables

CREATE ROLE cob_app      LOGIN PASSWORD :'app_pw';
CREATE ROLE cob_ingest   LOGIN PASSWORD :'ingest_pw';
CREATE ROLE cob_readonly LOGIN PASSWORD :'ro_pw';

-- Nobody gets anything by default.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

GRANT USAGE ON SCHEMA app, content TO cob_app;
GRANT USAGE ON SCHEMA app, content, public_data, private TO cob_ingest;
-- Read-only role sees public_data only; private tables are granted per collection later.
GRANT USAGE ON SCHEMA public_data TO cob_readonly;

ALTER ROLE cob_readonly SET statement_timeout = '10s';
ALTER ROLE cob_readonly SET default_transaction_read_only = on;
ALTER ROLE cob_readonly SET idle_in_transaction_session_timeout = '30s';
SQL
