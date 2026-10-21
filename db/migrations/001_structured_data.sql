-- Structured COB data (public_data schema). Designed from the tables actually extracted from
-- the FY 2025/26 H1, 9M and annual reports (see DECISIONS.md D-015). Applied by
-- backend/scripts/migrate.py as the owner role.

CREATE TABLE IF NOT EXISTS public_data.dim_county (
    county_id   smallint PRIMARY KEY,             -- official county code 1..47
    name        text NOT NULL UNIQUE,
    region      text NOT NULL                     -- former province
);

CREATE TABLE IF NOT EXISTS public_data.dim_period (
    period_id    serial PRIMARY KEY,
    fiscal_year  text NOT NULL CHECK (fiscal_year ~ '^\d{4}/\d{2}$'),
    period_type  text NOT NULL CHECK (period_type IN ('Q1', 'H1', '9M', 'FY')),
    months       smallint NOT NULL CHECK (months IN (3, 6, 9, 12)),  -- cumulative months covered
    period_end   date NOT NULL,
    UNIQUE (fiscal_year, period_type)
);

-- Metric dictionary: one row per metric the loader can produce. Used in the SQL prompt and
-- for the cross-period consistency check (cumulative flows must not fall within a year).
CREATE TABLE IF NOT EXISTS public_data.dim_metric (
    metric       text PRIMARY KEY,
    description  text NOT NULL,
    unit         text NOT NULL CHECK (unit IN ('KES', 'percent', 'count')),
    cumulative   boolean NOT NULL       -- true: year-to-date flow (expenditure, revenue collected)
);

CREATE TABLE IF NOT EXISTS public_data.source_document (
    doc_id        serial PRIMARY KEY,
    title         text NOT NULL,
    fiscal_year   text NOT NULL,
    period_type   text NOT NULL,
    source_url    text,
    file_name     text NOT NULL,
    sha256        text NOT NULL UNIQUE,
    page_count    integer NOT NULL,
    parse_version integer NOT NULL
);

CREATE TABLE IF NOT EXISTS public_data.fact_county_budget (
    fact_id         bigserial PRIMARY KEY,
    county_id       smallint NOT NULL REFERENCES public_data.dim_county,
    period_id       integer NOT NULL REFERENCES public_data.dim_period,
    metric          text NOT NULL REFERENCES public_data.dim_metric,
    value           numeric(20, 2) NOT NULL,   -- KES (converted from the printed unit) or percent
    -- provenance: every number can be traced to a page of a document
    source_doc_id   integer NOT NULL REFERENCES public_data.source_document ON DELETE CASCADE,
    source_page     integer NOT NULL,          -- 1-based PDF page
    source_table    text NOT NULL,             -- e.g. "2.4 County Budget Allocation, Expenditure, and Absorption Rate ..."
    source_row      text NOT NULL,             -- row label as printed
    printed_value   text NOT NULL,             -- the cell text exactly as extracted
    printed_unit    text NOT NULL,             -- e.g. 'Kshs. Million'
    UNIQUE (county_id, period_id, metric, source_doc_id)
);
CREATE INDEX IF NOT EXISTS fact_county_budget_lookup ON public_data.fact_county_budget (metric, period_id, county_id);

-- Rows that failed validation: kept with the reason, never loaded into the fact table.
CREATE TABLE IF NOT EXISTS public_data.quarantine_rows (
    quarantine_id  bigserial PRIMARY KEY,
    source_doc_id  integer NOT NULL REFERENCES public_data.source_document ON DELETE CASCADE,
    source_page    integer NOT NULL,
    source_table   text NOT NULL,
    row_label      text NOT NULL,
    raw_row        jsonb NOT NULL,
    check_name     text NOT NULL,
    reason         text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- Table-level and cross-period validation outcomes (one row per check per scope).
CREATE TABLE IF NOT EXISTS public_data.validation_results (
    result_id      bigserial PRIMARY KEY,
    check_name     text NOT NULL,
    scope          text NOT NULL,             -- e.g. "doc 3 / 2.4 ..." or "2025/26 expenditure_total Baringo"
    status         text NOT NULL CHECK (status IN ('pass', 'fail', 'warn')),
    details        jsonb NOT NULL DEFAULT '{}',
    source_doc_id  integer REFERENCES public_data.source_document ON DELETE CASCADE,
    source_page    integer,
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- Friendly view for the text-to-SQL agent: names instead of ids, provenance included.
CREATE OR REPLACE VIEW public_data.v_county_metrics AS
SELECT c.name AS county, c.region, p.fiscal_year, p.period_type, p.months,
       f.metric, m.unit, f.value,
       d.title AS source_title, f.source_page, f.source_table, d.source_url
FROM public_data.fact_county_budget f
JOIN public_data.dim_county c USING (county_id)
JOIN public_data.dim_period p USING (period_id)
JOIN public_data.dim_metric m USING (metric)
JOIN public_data.source_document d ON d.doc_id = f.source_doc_id;

GRANT SELECT ON ALL TABLES IN SCHEMA public_data TO cob_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public_data TO cob_ingest;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public_data TO cob_ingest;
-- The SQL agent reads facts and dimensions, not the quarantine or validation logs.
GRANT SELECT ON public_data.dim_county, public_data.dim_period, public_data.dim_metric,
               public_data.source_document, public_data.fact_county_budget,
               public_data.v_county_metrics TO cob_readonly;
