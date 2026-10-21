-- pgvector's type and operators live in schema public, which 10_roles.sh closed to everyone.
-- The app roles need to resolve them; they still cannot create anything there.
GRANT USAGE ON SCHEMA public TO cob_app, cob_ingest, cob_readonly;
