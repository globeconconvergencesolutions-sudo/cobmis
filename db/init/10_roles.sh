#!/bin/bash
# Runs once, on first initialisation of the Postgres volume.
# Creates the three least-privilege login roles (cluster-wide):
#   cob_app       - API runtime (app schema read/write; reads documents/chunks)
#   cob_ingest    - ingestion worker (writes public_data/private/content)
#   cob_readonly  - text-to-SQL executor (SELECT only, short statement_timeout)
# Extensions, schemas and all grants are in db/migrations (python -m scripts.migrate).
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_pw="$COB_APP_PASSWORD" \
  -v ingest_pw="$COB_INGEST_PASSWORD" \
  -v ro_pw="$COB_READONLY_PASSWORD" <<'SQL'
CREATE ROLE cob_app      LOGIN PASSWORD :'app_pw';
CREATE ROLE cob_ingest   LOGIN PASSWORD :'ingest_pw';
CREATE ROLE cob_readonly LOGIN PASSWORD :'ro_pw';

ALTER ROLE cob_readonly SET statement_timeout = '10s';
ALTER ROLE cob_readonly SET default_transaction_read_only = on;
ALTER ROLE cob_readonly SET idle_in_transaction_session_timeout = '30s';
SQL
