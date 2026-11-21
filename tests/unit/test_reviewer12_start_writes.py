"""Failed startup accounts for its file, journal mode and startup writes as well as schema."""

import sqlite3
from contextlib import closing

import pytest

from cairn.store import Store, schema
from cairn.web import cli


def test_listener_failure_reports_journal_and_retention_settings(tmp_path, monkeypatch, capsys):
    db = tmp_path / "db"
    Store(db).close()
    with closing(sqlite3.connect(db)) as con:
        con.execute("PRAGMA journal_mode=DELETE")

    def stop(*a, **kw):
        raise SystemExit(3)

    monkeypatch.setattr(cli.uvicorn, "run", stop)
    with pytest.raises(SystemExit):
        cli.main(["serve", "--db", str(db), "--audit-keep", "12", "--keep-versions", "3"])
    err = capsys.readouterr().err
    with closing(sqlite3.connect(db)) as con:
        assert con.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    assert "journal mode delete -> wal" in err
    assert "audit retention" in err and "page history bound" in err
    assert "committed schema" not in err


def test_file_created_before_a_failed_schema_step_is_reported(tmp_path, monkeypatch, capsys):
    db = tmp_path / "db"

    def stop(con):
        raise RuntimeError("step stopped")

    monkeypatch.setattr(schema, "_create", stop)
    with pytest.raises(RuntimeError, match="step stopped"):
        cli.main(["serve", "--db", str(db)])
    assert db.exists() and "created the database file" in capsys.readouterr().err
