"""A cairn database that holds more than cairn's steps made is still cairn's: a database
replicated by Litestream holds the two tables Litestream makes in it, an operator may add an
index of their own, and ANALYZE adds SQLite's statistics tables. The recognition asks that
everything cairn's steps made at that version is there, compared as sets; more is allowed.
`serve` upgrades such a file and leaves what is not cairn's as it was; every other command
answers it as an older cairn database, never as another program's. Made-up rows."""

import gc
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from cli_commands import ARGS, COMMANDS, PLANS_YAML
from test_repair_says_what_committed_unit import at_28_file, at_31
from test_store_unit import user_version

from cairn.store import SCHEMA_VERSION
from cairn.web import cli, cli_data

BUILDS = {"28": at_28_file, "31": at_31}
EXTRAS = {
    "litestream": [],
    "litestream and an operator's index": [
        "CREATE INDEX operator_notes_by_time ON notes (created_at)"
    ],
    "litestream after ANALYZE": ["ANALYZE"],
}


def replicated(tmp_path: Path, build: str, extra: list[str]) -> Path:
    """An older cairn database with Litestream's two tables in it (one row in the first),
    and `extra` run on it."""
    db = BUILDS[build](tmp_path)
    gc.collect()  # connections the builders leave open, closed before the file is compared
    with closing(sqlite3.connect(db)) as con:
        con.execute("CREATE TABLE _litestream_seq (id INTEGER PRIMARY KEY, seq INTEGER)")
        con.execute("INSERT INTO _litestream_seq (id, seq) VALUES (1, 7)")
        con.execute("CREATE TABLE _litestream_lock (id INTEGER)")
        for statement in extra:
            con.execute(statement)
        con.commit()
    (tmp_path / "archive.zip").write_bytes(b"not read: the database is refused first")
    (tmp_path / "plans.yaml").write_text(PLANS_YAML, encoding="utf-8")
    return db


@pytest.fixture
def listened(monkeypatch) -> list:
    """uvicorn.run as `serve` would call it, recorded and never run."""
    calls: list = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    return calls


@pytest.mark.parametrize("extra", EXTRAS)
@pytest.mark.parametrize("build", BUILDS)
def test_serve_upgrades_a_cairn_database_that_holds_another_programs_tables(
    tmp_path, listened, build, extra
):
    db = replicated(tmp_path, build, EXTRAS[extra])
    assert cli.main(["serve", "--db", str(db)]) == 0
    assert user_version(db) == SCHEMA_VERSION and len(listened) == 1
    with closing(sqlite3.connect(db)) as con:
        assert con.execute("SELECT id, seq FROM _litestream_seq").fetchall() == [(1, 7)]
        assert con.execute("SELECT count(*) FROM _litestream_lock").fetchone() == (0,)


def test_serve_upgrades_an_older_file_whose_table_holds_a_column_more(tmp_path, listened):
    """Columns are compared as sets too: one more on one of cairn's tables is allowed. (The
    reviewer's case that pinned this file's refusal pinned equality, and moved here.)"""
    db = at_28_file(tmp_path)
    gc.collect()
    with closing(sqlite3.connect(db)) as con:
        con.execute("ALTER TABLE sessions ADD COLUMN spare TEXT")
    assert cli.main(["serve", "--db", str(db)]) == 0
    assert user_version(db) == SCHEMA_VERSION and len(listened) == 1


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("command", [c for c in COMMANDS if c != "serve"])
def test_every_other_command_answers_it_as_an_older_cairn_database(
    tmp_path, capsys, listened, command
):
    db = replicated(tmp_path, "31", EXTRAS["litestream"])
    before = sha(db)
    argv = [command, "--db", str(db), *(arg.format(dir=tmp_path) for arg in ARGS[command])]
    assert cli.main(argv) == 1
    out, err = capsys.readouterr()
    line = cli_data.OLDER_SCHEMA.format(db=db, found=31, known=SCHEMA_VERSION, command=command)
    assert out == "" and err == line + "\n"
    assert sha(db) == before
