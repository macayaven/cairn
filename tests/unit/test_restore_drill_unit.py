"""scripts/restore_drill.py --db-file: what passes is a cairn database, not any SQLite file."""

import sqlite3
import subprocess
import sys
from pathlib import Path

from cairn.store import Store

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "restore_drill.py"


def drill(db: Path, *more: str) -> subprocess.CompletedProcess:
    command = [sys.executable, str(SCRIPT), "--db-file", str(db), *more]
    return subprocess.run(command, capture_output=True, text=True, timeout=60)


def test_a_database_cairn_made_passes(tmp_path):
    db = tmp_path / "cairn.db"
    store = Store(db)
    try:
        project = store.create_project("ops")
        store.add_note(project.id, "# Note\n\nQuill retries 3 times.\n")
    finally:
        store.close()
    done = drill(db, "--min-notes", "1")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Quill" not in done.stdout + done.stderr  # counts and times, never text


def test_another_sqlite_file_fails_whatever_it_holds(tmp_path):
    other = tmp_path / "other.db"
    con = sqlite3.connect(other)
    con.execute("CREATE TABLE things (id INTEGER PRIMARY KEY, name TEXT)")
    con.execute("INSERT INTO things (name) VALUES ('one')")
    con.commit()
    con.close()
    done = drill(other)
    assert done.returncode == 1 and "projects" in done.stdout


def test_a_file_with_only_a_notes_table_fails(tmp_path):
    partial = tmp_path / "partial.db"
    con = sqlite3.connect(partial)
    con.execute("CREATE TABLE notes (seq INTEGER PRIMARY KEY, id TEXT, created_at TEXT)")
    con.execute("INSERT INTO notes (id, created_at) VALUES ('cap_1', '2026-11-10T00:00:00+00:00')")
    con.commit()
    con.close()
    done = drill(partial, "--min-notes", "1")
    assert done.returncode == 1 and "FAIL" in done.stdout


def test_cairns_tables_without_a_schema_version_fail(tmp_path):
    db = tmp_path / "cairn.db"
    Store(db).close()
    con = sqlite3.connect(db)
    con.execute("PRAGMA user_version = 0")
    con.commit()
    con.close()
    done = drill(db)
    assert done.returncode == 1 and "version" in done.stdout


def test_the_right_table_names_with_the_wrong_columns_fail(tmp_path):
    """Five tables named like cairn's, a notes.created_at and a plausible version: the
    file a review built to pass the drill without being a cairn database."""
    fake = tmp_path / "fake.db"
    con = sqlite3.connect(fake)
    for table in ("accounts", "projects", "spans", "page_versions"):
        con.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY)")
    con.execute("CREATE TABLE notes (seq INTEGER PRIMARY KEY, id TEXT, created_at TEXT)")
    con.execute("INSERT INTO notes (id, created_at) VALUES ('cap_1', '2026-11-10T00:00:00+00:00')")
    con.execute("PRAGMA user_version = 19")
    con.commit()
    con.close()
    done = drill(fake, "--min-notes", "1")
    assert done.returncode == 1 and "column" in done.stdout
