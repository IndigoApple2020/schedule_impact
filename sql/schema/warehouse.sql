-- Canonical analytics schema (SQLite / DuckDB compatible)
-- See docs/architecture.md for field semantics

CREATE TABLE IF NOT EXISTS programme (
    programme_id   TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    timezone       TEXT
);

CREATE TABLE IF NOT EXISTS project_row (
    project_row_id TEXT PRIMARY KEY,
    programme_id   TEXT NOT NULL REFERENCES programme(programme_id),
    p6_proj_id     TEXT,
    display_name   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedule_snapshot (
    snapshot_id        TEXT PRIMARY KEY,
    project_row_id     TEXT NOT NULL REFERENCES project_row(project_row_id),
    reporting_period   TEXT NOT NULL,  -- YYYY-MM
    previous_period    TEXT,
    xer_path           TEXT,
    xer_file_hash      TEXT NOT NULL,
    data_date          TEXT,
    project_end_date   TEXT,
    ingested_at        TEXT NOT NULL,
    UNIQUE (project_row_id, reporting_period, xer_file_hash)
);

CREATE TABLE IF NOT EXISTS task_monthly_fact (
    project_row_id     TEXT NOT NULL,
    reporting_period   TEXT NOT NULL,
    task_id            TEXT NOT NULL,
    task_code          TEXT,
    task_name          TEXT,
    wbs_id             TEXT,
    wbs_name           TEXT,
    early_finish       TEXT,
    late_finish        TEXT,
    actual_finish      TEXT,
    prev_early_finish  TEXT,
    finish_slip_days   REAL,
    total_float_days   REAL,
    is_critical        INTEGER,
    on_longest_path    INTEGER,
    status_code        TEXT,
    match_quality      TEXT,
    PRIMARY KEY (project_row_id, reporting_period, task_id)
);

CREATE TABLE IF NOT EXISTS incident (
    incident_id        TEXT PRIMARY KEY,
    incident_type        TEXT NOT NULL CHECK (incident_type IN ('impact', 'float')),
    project_row_id     TEXT NOT NULL,
    reporting_period   TEXT NOT NULL,
    previous_period    TEXT,
    primary_task_id    TEXT,
    related_task_ids   TEXT,  -- JSON array
    delay_days         REAL NOT NULL,
    delay_metric       TEXT NOT NULL,
    cp_effect_days     REAL,
    severity           TEXT,
    detected_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS narrative_chunk (
    chunk_id           TEXT PRIMARY KEY,
    document_id        TEXT NOT NULL,
    project_row_id     TEXT,
    reporting_period   TEXT NOT NULL,
    text               TEXT NOT NULL,
    page_start         INTEGER,
    page_end           INTEGER,
    section_title      TEXT,
    extracted_refs     TEXT,  -- JSON
    ocr_used           INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS incident_narrative_link (
    incident_id        TEXT NOT NULL REFERENCES incident(incident_id),
    chunk_id           TEXT NOT NULL REFERENCES narrative_chunk(chunk_id),
    link_method        TEXT NOT NULL,
    confidence         REAL NOT NULL,
    rationale          TEXT,
    PRIMARY KEY (incident_id, chunk_id, link_method)
);

CREATE TABLE IF NOT EXISTS quality_assessment (
    incident_id          TEXT PRIMARY KEY REFERENCES incident(incident_id),
    is_quality_related   INTEGER NOT NULL,
    quality_confidence   REAL NOT NULL,
    quality_signals      TEXT,
    classifier           TEXT NOT NULL,
    review_status        TEXT DEFAULT 'auto'
);

CREATE INDEX IF NOT EXISTS idx_incident_period
    ON incident (reporting_period, project_row_id);

CREATE INDEX IF NOT EXISTS idx_task_slip
    ON task_monthly_fact (reporting_period, finish_slip_days);
