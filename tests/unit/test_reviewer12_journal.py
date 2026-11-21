"""A maintenance refusal preserves the durable database and its journal on disk."""

import fcntl
import os
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest
from cli_commands import ARGS, COMMANDS, PLANS_YAML, READS_ONLY

from cairn.store import Store
from cairn.web import cli


@pytest.mark.parametrize("argv", [["repair"], ["compact"], ["compact", "--if-due"]])
def test_busy_maintenance_preserves_the_rollback_journal_mode(tmp_path, capsys, argv):
    db = tmp_path / "db"
    Store(db).close()
    with closing(sqlite3.connect(db)) as con:
        assert con.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
    lock = tmp_path / "db.compact.lock"
    with lock.open("wb") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        before = db.read_bytes()
        assert cli.main([*argv, "--db", str(db)]) == 3
        assert "nothing was changed" in capsys.readouterr().err
        assert db.read_bytes() == before


@pytest.mark.parametrize("command", [c for c in COMMANDS if c != "serve"])
def test_just_opening_a_current_database_does_not_write(tmp_path, monkeypatch, command):
    db = tmp_path / "db"
    Store(db).close()
    with closing(sqlite3.connect(db)) as con:
        con.execute("PRAGMA journal_mode=DELETE")
    (tmp_path / "archive.zip").touch()
    (tmp_path / "plans.yaml").write_text(PLANS_YAML)
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    args = [command, "--db", str(db), *(s.format(dir=tmp_path) for s in ARGS[command])]
    if command in READS_ONLY:
        assert cli.main(args) == 0
    else:

        class Opened(Exception):
            pass

        actual = Store.__init__

        def stop_at_open(store, *args, **kwargs):
            actual(store, *args, **kwargs)
            store.close()
            raise Opened

        monkeypatch.setattr(Store, "__init__", stop_at_open)
        with pytest.raises(Opened):
            cli.main(args)
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_an_older_database_left_in_the_wal_is_refused_without_checkpointing(tmp_path, capsys):
    db = tmp_path / "db"
    Store(db).close()
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.execute("PRAGMA user_version=31")
con.execute("UPDATE oldest_build SET version=31")
con.commit()
os._exit(0)
""",
            os.fspath(db),
        ],
        check=True,
    )
    wal = tmp_path / "db-wal"
    before = db.read_bytes(), wal.read_bytes()
    assert cli.main(["repair", "--db", str(db)]) == 1
    assert "version 31" in capsys.readouterr().err
    assert wal.exists() and (db.read_bytes(), wal.read_bytes()) == before
