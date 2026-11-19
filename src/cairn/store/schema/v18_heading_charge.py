"""Schema step 18: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

# From _HEADING_CHARGE, what the passages counter counts for a span is its charge
# (cairn.store.usage.charge_of): one if it is a passage, and one for every full 40 bytes
# (BYTES_PER_PASSAGE) of its heading path as stored, less JSON's brackets, quotes and
# separators (4 a heading, as json.dumps writes a list; 2 for an empty path's brackets,
# which the division drops).
_V18_CHARGE = (
    "(({row}.kind IN ('paragraph', 'bullet', 'code', 'table', 'quote'))"
    " + MAX(0, LENGTH(CAST({row}.heading_path AS BLOB))"
    " - 4 * json_array_length({row}.heading_path)) / 40)"
)
_V18_PASSAGES = (
    f"""CREATE TRIGGER passage_added AFTER INSERT ON spans WHEN {_V18_CHARGE.format(row="new")} > 0
BEGIN
    INSERT INTO account_disk (account_id, passages)
    SELECT projects.account_id, {_V18_CHARGE.format(row="new")}
    FROM notes JOIN projects ON projects.id = notes.project_id
    WHERE notes.id = new.note_id
    ON CONFLICT (account_id) DO UPDATE SET passages = passages + excluded.passages;
END""",
    f"""CREATE TRIGGER passage_removed AFTER DELETE ON spans
WHEN {_V18_CHARGE.format(row="old")} > 0
BEGIN
    UPDATE account_disk SET passages = MAX(0, passages - {_V18_CHARGE.format(row="old")})
    WHERE account_id = (SELECT projects.account_id FROM notes
    JOIN projects ON projects.id = notes.project_id WHERE notes.id = old.note_id);
END""",
)


def _charge_headings(con: sqlite3.Connection) -> None:
    """Schema _KEY_SESSIONS to _HEADING_CHARGE: the passages counter charges the heading
    path stored with each span (_V18_CHARGE), from the rows each workspace holds, and its
    triggers count so from now on. A path stored with JSON's \\u escapes, as cairn wrote
    them before paths were stored as UTF-8, is charged as stored. Nothing else changes, but
    a workspace's count rises and can pass its plan's cap: then its new notes are refused
    until some are removed or the plan changes."""
    con.execute("DROP TRIGGER passage_added")
    con.execute("DROP TRIGGER passage_removed")
    con.execute("UPDATE account_disk SET passages = 0")
    con.execute(
        "INSERT INTO account_disk (account_id, passages)"
        f" SELECT projects.account_id, SUM({_V18_CHARGE.format(row='spans')}) FROM spans"
        " JOIN notes ON notes.id = spans.note_id"
        " JOIN projects ON projects.id = notes.project_id"
        " WHERE true GROUP BY projects.account_id"
        " ON CONFLICT (account_id) DO UPDATE SET passages = excluded.passages"
    )
    for statement in _V18_PASSAGES:
        con.execute(statement)
