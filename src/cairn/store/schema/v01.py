"""The original unstamped schema, used to identify the shapes left by later steps."""

DDL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
    digest TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS api_keys_by_label ON api_keys (label);
CREATE TABLE IF NOT EXISTS sessions (
    digest TEXT PRIMARY KEY,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    text TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    title TEXT NOT NULL,
    topic TEXT,
    label TEXT,
    source_ref TEXT,
    via TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_by_text ON notes (project_id, text_sha256);
CREATE TABLE IF NOT EXISTS spans (
    id TEXT PRIMARY KEY,
    note_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    kind TEXT NOT NULL,
    heading_path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    text TEXT NOT NULL,
    UNIQUE (note_id, ordinal)
);
CREATE TABLE IF NOT EXISTS page_versions (
    project_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    claims TEXT NOT NULL,
    metrics TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, slug, version)
);
CREATE VIRTUAL TABLE IF NOT EXISTS claim_search USING fts5 (
    project_id UNINDEXED, slug UNINDEXED, claim_id UNINDEXED, span_ids UNINDEXED, title, text
);
"""
