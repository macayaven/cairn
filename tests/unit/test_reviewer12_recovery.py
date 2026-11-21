"""A cold command must not recover an abandoned rollback transaction in order to refuse it."""

import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest

from cairn.store import Store
from cairn.web import cli


@pytest.mark.parametrize(
    "command", ["serve", "repair", "compact", "key-list", "stats", "audit-verify"]
)
def test_recovery_is_left_to_the_database_owner(tmp_path, capsys, command, monkeypatch):
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **kw: pytest.fail("server started"))
    db = tmp_path / "db"
    Store(db).close()
    with closing(sqlite3.connect(db)) as con:
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("CREATE TABLE scratch (v BLOB)")
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.execute("PRAGMA cache_size=2")
con.execute("BEGIN IMMEDIATE")
con.execute("PRAGMA user_version=31")
con.executemany("INSERT INTO scratch VALUES (zeroblob(4000))", [()] * 80)
os._exit(0)
""",
            str(db),
        ],
        check=True,
    )
    journal = tmp_path / "db-journal"
    before = db.read_bytes(), journal.read_bytes()
    assert before[1][:8] != bytes(8)
    assert cli.main([command, "--db", str(db)]) == 1
    out, err = capsys.readouterr()
    assert out == "" and "rollback journal" in err and "start the server" not in err
    assert journal.exists() and (db.read_bytes(), journal.read_bytes()) == before


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
@pytest.mark.parametrize("empty", [False, True])
def test_serve_leaves_an_unattached_companion_alone(tmp_path, capsys, monkeypatch, suffix, empty):
    db = tmp_path / "db"
    if empty:
        db.touch()
    companion = tmp_path / ("db" + suffix)
    companion.write_bytes(b"previous")
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **kw: pytest.fail("server started"))
    assert cli.main(["serve", "--db", str(db)]) == 1
    assert str(companion) in capsys.readouterr().err
    assert db.exists() == empty and (not empty or db.read_bytes() == b"")
    assert companion.read_bytes() == b"previous"
