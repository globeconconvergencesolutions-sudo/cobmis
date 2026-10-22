-- Phase 6 Evidence Terminal: auth sessions, conversations, feedback, jobs, audit.
-- Extends stub app.users with password hashes for cookie sessions.

ALTER TABLE app.users
    ADD COLUMN IF NOT EXISTS password_hash text;

CREATE TABLE IF NOT EXISTS app.sessions (
    session_id   text PRIMARY KEY,
    user_id      integer NOT NULL REFERENCES app.users ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    csrf_token   text NOT NULL
);

CREATE INDEX IF NOT EXISTS sessions_user_idx ON app.sessions (user_id);
CREATE INDEX IF NOT EXISTS sessions_expires_idx ON app.sessions (expires_at);

CREATE TABLE IF NOT EXISTS app.conversations (
    conversation_id  serial PRIMARY KEY,
    user_id          integer NOT NULL REFERENCES app.users ON DELETE CASCADE,
    title            text NOT NULL DEFAULT 'New chat',
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS conversations_user_idx ON app.conversations (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS app.messages (
    message_id       bigserial PRIMARY KEY,
    conversation_id  integer NOT NULL REFERENCES app.conversations ON DELETE CASCADE,
    role             text NOT NULL CHECK (role IN ('user', 'assistant')),
    content          text NOT NULL,
    route            text,
    citations_json   jsonb,
    sql_json         jsonb,
    unsupported_numbers text[],
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS messages_conv_idx ON app.messages (conversation_id, message_id);

CREATE TABLE IF NOT EXISTS app.feedback (
    feedback_id      serial PRIMARY KEY,
    message_id       bigint NOT NULL REFERENCES app.messages ON DELETE CASCADE,
    user_id          integer NOT NULL REFERENCES app.users ON DELETE CASCADE,
    rating           smallint NOT NULL CHECK (rating IN (-1, 1)),
    note             text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (message_id, user_id)
);

CREATE TABLE IF NOT EXISTS app.jobs (
    job_id       serial PRIMARY KEY,
    kind         text NOT NULL,
    status       text NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    created_by   integer REFERENCES app.users ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    started_at   timestamptz,
    finished_at  timestamptz,
    detail       text,
    log_tail     text
);

CREATE TABLE IF NOT EXISTS app.audit_events (
    event_id     bigserial PRIMARY KEY,
    user_id      integer REFERENCES app.users ON DELETE SET NULL,
    question     text NOT NULL,
    route        text,
    sql_text     text,
    sources_json jsonb,
    conversation_id integer REFERENCES app.conversations ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS audit_created_idx ON app.audit_events (created_at DESC);

-- API role needs write access for sessions / chat / admin (Phase 6).
GRANT SELECT, INSERT, UPDATE, DELETE ON app.sessions TO cob_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON app.conversations TO cob_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON app.messages TO cob_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON app.feedback TO cob_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON app.jobs TO cob_app;
GRANT SELECT, INSERT ON app.audit_events TO cob_app;
GRANT SELECT, INSERT, UPDATE ON app.users TO cob_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON app.collection_access TO cob_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app TO cob_app;

GRANT SELECT, INSERT, UPDATE, DELETE ON app.sessions, app.conversations, app.messages,
    app.feedback, app.jobs, app.audit_events TO cob_ingest;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app TO cob_ingest;
