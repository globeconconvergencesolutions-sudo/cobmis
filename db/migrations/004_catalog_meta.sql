-- Catalog / meta route support: ingest timestamps, OCR page counts, approved about blurb,
-- and a minimal users + collection_access stub for ACL-scoped catalog queries (Phase 7 will
-- grow login on top of these tables).

-- Catalog reads metric/fact/validation counts; cob_app already has table SELECTs from 001
-- but lacked schema USAGE (only cob_ingest / cob_readonly had it).
GRANT USAGE ON SCHEMA public_data TO cob_app;

ALTER TABLE content.documents
    ADD COLUMN IF NOT EXISTS ingested_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS needs_ocr_pages integer NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS app.dataset_about (
    about_id     serial PRIMARY KEY,
    body         text NOT NULL,
    approved_at  timestamptz,
    approved_by  text
);

-- Draft blurb for owner review. approved_at NULL means "pending approval"; the catalog still
-- surfaces it as the dataset description until a reviewed paragraph replaces it.
INSERT INTO app.dataset_about (body, approved_at, approved_by)
SELECT
    'This assistant answers questions from the Controller of Budget consolidated County '
    'Governments Budget Implementation Review Reports. Narrative answers cite PDF pages; '
    'numeric answers (when enabled) draw from chapter-2 tables loaded into SQL. Coverage is '
    'exactly the reports that have been downloaded and ingested on this machine - never the '
    'full COB archive unless every file has been scraped. Per-county chapter-3 tables are '
    'searchable as text chunks but are not yet normalised into SQL. A small number of '
    'image-only pages (covers and key-highlights infographics) are flagged and skipped until '
    'OCR is enabled.',
    NULL,
    NULL
WHERE NOT EXISTS (SELECT 1 FROM app.dataset_about);

CREATE TABLE IF NOT EXISTS app.users (
    user_id       serial PRIMARY KEY,
    email         text NOT NULL UNIQUE,
    display_name  text NOT NULL,
    role          text NOT NULL CHECK (role IN ('admin', 'analyst', 'viewer')),
    is_active     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.collection_access (
    user_id        integer NOT NULL REFERENCES app.users ON DELETE CASCADE,
    collection_id  integer NOT NULL REFERENCES app.collections ON DELETE CASCADE,
    can_read       boolean NOT NULL DEFAULT true,
    can_write      boolean NOT NULL DEFAULT false,
    PRIMARY KEY (user_id, collection_id)
);

GRANT SELECT ON app.dataset_about TO cob_app, cob_ingest;
GRANT SELECT, INSERT, UPDATE, DELETE ON app.dataset_about TO cob_ingest;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app TO cob_ingest;

GRANT SELECT ON app.users, app.collection_access TO cob_app, cob_ingest;
GRANT SELECT, INSERT, UPDATE, DELETE ON app.users, app.collection_access TO cob_ingest;
GRANT USAGE, SELECT ON SEQUENCE app.users_user_id_seq TO cob_ingest;
