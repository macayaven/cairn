"""Schema step 16: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

# What the disk bounds count (H6c, cairn.store.usage), per workspace: its notes' passages
# (spans of a kind the compiler makes a claim of) and the bytes of every page version it
# keeps (body and claims, as stored). Kept by triggers, so every write that adds or removes
# a span or a version counts it in its own transaction, whichever area makes it. A span is
# counted through its note's project, so spans are deleted before their notes (as every
# deletion here does).
_V16_ACCOUNT_DISK = """CREATE TABLE account_disk (
    account_id TEXT PRIMARY KEY,
    passages INTEGER NOT NULL DEFAULT 0,
    history_bytes INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID"""
_V16_PASSAGE = "new.kind IN ('paragraph', 'bullet', 'code', 'table', 'quote')"
_V16_SIZE = "LENGTH(CAST({row}.body AS BLOB)) + LENGTH(CAST({row}.claims AS BLOB))"
_V16_COUNTERS = (
    _V16_ACCOUNT_DISK,
    f"""CREATE TRIGGER passage_added AFTER INSERT ON spans WHEN {_V16_PASSAGE}
BEGIN
    INSERT INTO account_disk (account_id, passages)
    SELECT projects.account_id, 1 FROM notes JOIN projects ON projects.id = notes.project_id
    WHERE notes.id = new.note_id
    ON CONFLICT (account_id) DO UPDATE SET passages = passages + 1;
END""",
    f"""CREATE TRIGGER passage_removed AFTER DELETE ON spans
WHEN {_V16_PASSAGE.replace("new.", "old.")}
BEGIN
    UPDATE account_disk SET passages = MAX(0, passages - 1) WHERE account_id =
    (SELECT projects.account_id FROM notes JOIN projects ON projects.id = notes.project_id
     WHERE notes.id = old.note_id);
END""",
    f"""CREATE TRIGGER version_added AFTER INSERT ON page_versions
BEGIN
    INSERT INTO account_disk (account_id, history_bytes)
    SELECT account_id, {_V16_SIZE.format(row="new")} FROM projects WHERE id = new.project_id
    ON CONFLICT (account_id) DO UPDATE SET
    history_bytes = history_bytes + excluded.history_bytes;
END""",
    f"""CREATE TRIGGER version_removed AFTER DELETE ON page_versions
BEGIN
    UPDATE account_disk SET history_bytes = MAX(0, history_bytes - ({_V16_SIZE.format(row="old")}))
    WHERE account_id = (SELECT account_id FROM projects WHERE id = old.project_id);
END""",
)


def _add_disk_counters(con: sqlite3.Connection) -> None:
    """Schema 15 to _DISK_BOUNDS: each workspace's passages and page history, counted from
    the rows it holds, and the triggers that keep both counts from now on. Nothing that
    existed changes."""
    con.execute(_V16_ACCOUNT_DISK)
    con.execute(
        "INSERT INTO account_disk (account_id, passages)"
        " SELECT projects.account_id, COUNT(*) FROM spans"
        " JOIN notes ON notes.id = spans.note_id"
        " JOIN projects ON projects.id = notes.project_id"
        f" WHERE {_V16_PASSAGE.replace('new.', 'spans.')} GROUP BY projects.account_id"
    )
    con.execute(
        "INSERT INTO account_disk (account_id, history_bytes)"
        f" SELECT projects.account_id, SUM({_V16_SIZE.format(row='page_versions')})"
        " FROM page_versions JOIN projects ON projects.id = page_versions.project_id"
        " WHERE true GROUP BY projects.account_id"
        " ON CONFLICT (account_id) DO UPDATE SET history_bytes = excluded.history_bytes"
    )
    for statement in _V16_COUNTERS[1:]:
        con.execute(statement)
