"""SQLite header and floor metadata must be meaningful before the schema can be used."""

import sqlite3
from contextlib import closing

import pytest

from cairn.web import cli


@pytest.mark.parametrize("command", ["serve", "key-list", "stats", "audit-verify"])
@pytest.mark.parametrize(
    "metadata",
    [
        ["PRAGMA application_id=7"],
        ["CREATE TABLE oldest_build (n)", "PRAGMA user_version=33"],
        [
            "CREATE TABLE oldest_build (one, version)",
            "INSERT INTO oldest_build VALUES (1, 'x')",
            "PRAGMA user_version=33",
        ],
    ],
)
def test_another_application_or_an_invalid_floor_is_a_clean_refusal(
    tmp_path, capsys, monkeypatch, command, metadata
):
    db = tmp_path / "db"
    with closing(sqlite3.connect(db)) as con:
        for sql in metadata:
            con.execute(sql)
        con.commit()
    before = db.read_bytes()
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **kw: pytest.fail("server started"))
    assert cli.main([command, "--db", str(db)]) == 1
    out, err = capsys.readouterr()
    assert out == "" and len(err.splitlines()) == 1 and "cairn database" in err
    assert "serve --db" not in err and db.read_bytes() == before
