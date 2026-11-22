"""A database at this code's schema number that lacks a table this code makes was not made by
this version: the store refuses to open it before it writes anything, in one sentence, and
every command answers it with exit 1 and that sentence on one line of stderr, nothing on
stdout, nothing written, and `serve` never listens. /healthz never answers for it, since no
store is opened on it."""

import sqlite3
from pathlib import Path

import pytest

from cairn.store import SCHEMA_VERSION, Store, UnknownSchema
from cairn.web import cli
from support.cli_commands import ARGS, COMMANDS, PLANS_YAML

ASIDE = "kept_aside"


def without(directory: Path, table: str) -> Path:
    """A database made by this code, at its schema number, with `table` under another name."""
    db = directory / "cairn.db"
    Store(db).close()
    con = sqlite3.connect(db)
    try:
        con.execute(f"ALTER TABLE {table} RENAME TO {ASIDE}")
        con.commit()
    finally:
        con.close()
    (directory / "archive.zip").write_bytes(b"not read: the database is refused first")
    (directory / "plans.yaml").write_text(PLANS_YAML, encoding="utf-8")
    return db


def sentence(table: str) -> str:
    return (
        f"the database was not made by this version of cairn: it has schema version"
        f" {SCHEMA_VERSION} but no table {table}"
    )


@pytest.mark.parametrize("table", ["notes", "projects", "spans", "page_heads"])
def test_the_store_refuses_a_database_that_lacks_a_table_and_writes_nothing(tmp_path, table):
    db = without(tmp_path, table)
    before, files = db.read_bytes(), sorted(tmp_path.iterdir())
    with pytest.raises(UnknownSchema) as refused:
        Store(db)
    assert str(refused.value) == sentence(table)
    assert db.read_bytes() == before, "the database was written"
    assert sorted(tmp_path.iterdir()) == files, "a file was made or left beside it"


def test_a_database_this_code_made_opens_as_before(tmp_path):
    db = tmp_path / "cairn.db"
    Store(db).close()
    Store(db).close()


@pytest.fixture
def listened(monkeypatch) -> list:
    """uvicorn.run as `serve` would call it, recorded and never run."""
    calls: list = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    return calls


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_refuses_it_in_one_line(tmp_path, capsys, listened, command):
    db = without(tmp_path, "notes")
    before, files = db.read_bytes(), sorted(tmp_path.iterdir())
    argv = [command, "--db", str(db), *(arg.format(dir=tmp_path) for arg in ARGS[command])]
    code = cli.main(argv)
    out, err = capsys.readouterr()
    assert (code, out) == (1, ""), argv
    assert err == sentence("notes") + "\n"
    assert db.read_bytes() == before, "the database was written"
    assert sorted(tmp_path.iterdir()) == files, "a file was made or removed beside it"
    assert listened == [], "serve started listening"
