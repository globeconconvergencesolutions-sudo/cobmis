-- Documents and retrievable chunks (content schema), with embeddings and full-text search.
-- The vector size is fixed at 1024 (bge-m3, the configured small-profile model). Switching
-- to an embedding model with another size needs a new migration and a re-embed.

CREATE TABLE IF NOT EXISTS app.collections (
    collection_id  serial PRIMARY KEY,
    name           text NOT NULL UNIQUE,
    visibility     text NOT NULL CHECK (visibility IN ('public', 'restricted'))
);
INSERT INTO app.collections (name, visibility) VALUES ('cob-public', 'public') ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS content.documents (
    doc_id         serial PRIMARY KEY,
    collection_id  integer NOT NULL REFERENCES app.collections,
    title          text NOT NULL,
    fiscal_year    text,
    period_type    text,
    source_url     text,
    file_name      text NOT NULL,
    sha256         text NOT NULL UNIQUE,
    page_count     integer NOT NULL,
    parse_version  integer NOT NULL
);

CREATE TABLE IF NOT EXISTS content.chunks (
    chunk_id       bigserial PRIMARY KEY,
    doc_id         integer NOT NULL REFERENCES content.documents ON DELETE CASCADE,
    collection_id  integer NOT NULL REFERENCES app.collections,
    kind           text NOT NULL CHECK (kind IN ('narrative', 'table')),
    page_start     integer NOT NULL,
    page_end       integer NOT NULL,
    section_title  text,
    county_id      smallint,
    table_ref      text,                -- "3.6" etc. for table chunks
    context_header text NOT NULL,       -- prepended before embedding
    text           text NOT NULL,
    token_estimate integer NOT NULL,
    content_hash   text NOT NULL,       -- sha256 of context_header + text
    embedding      vector(1024),
    embed_model    text,
    tsv            tsvector GENERATED ALWAYS AS (to_tsvector('english', context_header || ' ' || text)) STORED,
    UNIQUE (doc_id, content_hash)
);
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON content.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON content.chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_filter ON content.chunks (collection_id, county_id, doc_id);
CREATE INDEX IF NOT EXISTS chunks_unembedded ON content.chunks (chunk_id) WHERE embedding IS NULL;

GRANT SELECT ON app.collections TO cob_app, cob_ingest;
GRANT SELECT ON ALL TABLES IN SCHEMA content TO cob_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA content TO cob_ingest;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA content TO cob_ingest;
