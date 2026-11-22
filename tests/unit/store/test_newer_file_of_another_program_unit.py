"""A file above this cairn's schema that holds none of cairn's tables is another program's
file that carries a high user_version, not a database a newer cairn wrote: every cairn of a
later version keeps tables this version's steps make. Every command answers it as not a
cairn database and changes nothing. A newer cairn's database, which holds one of cairn's
tables, is still answered "run the cairn that wrote it". Made-up tables."""

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from cairn.store import SCHEMA_VERSION
from cairn.web import cli
from cairn.web.cli import CREATES, NEWER_DATABASE, NOT_A_DATABASE, NOT_A_DATABASE_YET
from support.cli_commands import ARGS, COMMANDS, PLANS_YAML


@pytest.fixture(autouse=True)
def no_listener(monkeypatch):
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **kw: pytest.fail("server started"))


def newer(tmp_path: Path, *tables: str) -> Path:
    """A file at SCHEMA_VERSION + 1 holding `tables`, each with one made-up column."""
    db = tmp_path / "cairn.db"
    with closing(sqlite3.connect(db)) as con:
        for table in tables:
            con.execute(f"CREATE TABLE {table} (n INTEGER)")
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    (tmp_path / "archive.zip").write_bytes(b"not read: the database is refused first")
    (tmp_path / "plans.yaml").write_text(PLANS_YAML, encoding="utf-8")
    return db


def files(tmp_path: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}


def run(tmp_path: Path, db: Path, command: str) -> int:
    argv = [command, "--db", str(db), *(arg.format(dir=tmp_path) for arg in ARGS[command])]
    return cli.main(argv)


@pytest.mark.parametrize("command", COMMANDS)
def test_a_newer_file_with_none_of_cairns_tables_is_not_a_cairn_database(tmp_path, capsys, command):
    db = newer(tmp_path, "receipts")
    before = files(tmp_path)
    assert run(tmp_path, db, command) == 1
    out, err = capsys.readouterr()
    line = NOT_A_DATABASE_YET if command in CREATES else NOT_A_DATABASE
    assert out == "" and err == line.format(path=db) + "\n"
    assert files(tmp_path) == before


@pytest.mark.parametrize("command", COMMANDS)
def test_a_newer_cairns_database_still_says_run_the_cairn_that_wrote_it(tmp_path, capsys, command):
    db = newer(tmp_path, "receipts", "accounts")
    before = files(tmp_path)
    assert run(tmp_path, db, command) == 1
    out, err = capsys.readouterr()
    head, tail = NEWER_DATABASE.split("{refusal}")
    assert out == "" and err.startswith(head) and err.endswith(tail + "\n")
    assert f"schema version {SCHEMA_VERSION + 1}" in err and len(err.splitlines()) == 1
    assert files(tmp_path) == before
