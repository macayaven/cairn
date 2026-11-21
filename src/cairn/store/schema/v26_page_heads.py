"""Schema step 26: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

# Page heads (Q1). One row per page: its latest version's number, title, claim count and
# time, kept by two triggers on page_versions in the transaction of each insert and delete,
# so a list of a project's pages reads these short rows, never a version (each version holds
# the whole page, and its body's overflow pages lie between its title and its claims).
# Measured on 2,000 pages of 50 KB bodies: listing every page read 24,698 database pages
# without this table and 50 with it; the first 100 by slug, 1,258 and 12. A save inserts the
# new latest; removing an older version changes nothing; removing the latest takes the next
# one's row, and removing a page's last version removes its head. Rows are not exported:
# an import's versions make them, and they go with their page, project and workspace.
_V26_PAGE_HEADS = """CREATE TABLE page_heads (
    project_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    version INTEGER NOT NULL,  -- the page's latest version
    title TEXT NOT NULL,
    claims INTEGER NOT NULL,  -- how many claims that version has
    updated_at TEXT NOT NULL,  -- when that version was saved
    PRIMARY KEY (project_id, slug)
) WITHOUT ROWID"""
_V26_HEAD_SAVED = """CREATE TRIGGER head_saved AFTER INSERT ON page_versions
BEGIN
    INSERT INTO page_heads (project_id, slug, version, title, claims, updated_at)
    VALUES (new.project_id, new.slug, new.version, new.title, json_array_length(new.claims),
    new.created_at)
    ON CONFLICT (project_id, slug) DO UPDATE SET version = excluded.version,
    title = excluded.title, claims = excluded.claims, updated_at = excluded.updated_at
    WHERE excluded.version >= page_heads.version;
END"""
_V26_HEAD_REMOVED = """CREATE TRIGGER head_removed AFTER DELETE ON page_versions
WHEN old.version = (SELECT version FROM page_heads
    WHERE project_id = old.project_id AND slug = old.slug)
BEGIN
    DELETE FROM page_heads WHERE project_id = old.project_id AND slug = old.slug;
    INSERT INTO page_heads (project_id, slug, version, title, claims, updated_at)
    SELECT project_id, slug, version, title, json_array_length(claims), created_at
    FROM page_versions WHERE project_id = old.project_id AND slug = old.slug
    ORDER BY version DESC LIMIT 1;
END"""
_V26_HEADS = (_V26_PAGE_HEADS, _V26_HEAD_SAVED, _V26_HEAD_REMOVED)


def _add_page_heads(con: sqlite3.Connection) -> None:
    """Schema _WRITERS to _PAGE_HEADS (Q1): page_heads and its triggers, filled with every
    page's latest version as it is. Nothing that existed changes."""
    for statement in _V26_HEADS:
        con.execute(statement)
    con.execute(
        "INSERT INTO page_heads (project_id, slug, version, title, claims, updated_at)"
        " SELECT project_id, slug, version, title, json_array_length(claims), created_at"
        " FROM page_versions AS v WHERE version = (SELECT MAX(version) FROM page_versions"
        " WHERE project_id = v.project_id AND slug = v.slug)"
    )
