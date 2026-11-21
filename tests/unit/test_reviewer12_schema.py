"""A version number alone does not identify a cairn database."""

import gc
import sqlite3
from contextlib import closing

import pytest
from cli_commands import ARGS, COMMANDS
from test_repair_says_what_committed_unit import at_28_file

from cairn.web import cli


@pytest.fixture(autouse=True)
def no_listener(monkeypatch):
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **kw: pytest.fail("server started"))


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("version", [0, 28, -1])
def test_foreign_database_is_never_advised_onto_serve(tmp_path, capsys, command, version):
    db = tmp_path / "db"
    with closing(sqlite3.connect(db)) as con:
        con.execute("CREATE TABLE receipts (n INTEGER)")
        con.execute(f"PRAGMA user_version = {version}")
    (tmp_path / "archive.zip").touch()
    argv = [command, "--db", str(db), *(s.format(dir=tmp_path) for s in ARGS[command])]
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    assert cli.main(argv) == 1
    out, err = capsys.readouterr()
    assert out == "" and len(err.splitlines()) == 1
    assert "cairn database" in err and "start the server" not in err
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize(
    "change",
    [
        "ALTER TABLE sessions DROP COLUMN expires_at",
        "ALTER TABLE sessions ADD COLUMN spare TEXT",
        "CREATE TABLE slack_notes (n INTEGER)",
    ],
)
def test_serve_refuses_a_damaged_older_shape_before_the_first_step(tmp_path, capsys, change):
    db = at_28_file(tmp_path)
    gc.collect()  # close the historical fixture's connections before comparing disk bytes
    with closing(sqlite3.connect(db)) as con:
        con.execute(change)
    before = db.read_bytes()
    assert cli.main(["serve", "--db", str(db)]) == 1
    assert "cairn database" in capsys.readouterr().err
    assert db.read_bytes() == before
