"""An allocated SQLite file without a schema still belongs to its owner."""

import sqlite3
from contextlib import closing

import pytest

from cairn.web import cli


@pytest.mark.parametrize("statement", ["VACUUM", "CREATE TABLE receipt(n); DROP TABLE receipt"])
@pytest.mark.parametrize("command", ["serve", "key-list", "stats", "audit-verify"])
def test_allocated_sqlite_file_is_never_an_initialization_target(
    tmp_path, capsys, monkeypatch, command, statement
):
    db = tmp_path / "db"
    with closing(sqlite3.connect(db)) as other:
        other.executescript(statement)
        assert other.execute("PRAGMA page_count").fetchone()[0] > 0
        assert not other.execute("SELECT name FROM sqlite_schema").fetchall()
    before = db.read_bytes()
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **kw: None)
    result = cli.main([command, "--db", str(db)])
    out, err = capsys.readouterr()
    assert db.read_bytes() == before
    assert result == 1 and out == ""
    assert len(err.splitlines()) == 1 and "not a cairn database" in err
