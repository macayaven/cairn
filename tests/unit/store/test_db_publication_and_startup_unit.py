"""Database publication, key bootstrap, import staging, and startup write accounting."""

import os
import sqlite3
from contextlib import closing

import pytest

from cairn.store import DEFAULT_ACCOUNT_ID, Store, schema
from cairn.store.base import audit_log
from cairn.web import cli, cli_data
from support.store.repair_says_what_committed import at_28_file, clean_31
from support.store.store import connect, user_version
from support.web.reviewer12_publication import archive, restore

# --- test_reviewer12_audit.py ---


def test_an_open_store_uses_the_current_event_sink(tmp_path, monkeypatch):
    with closing(Store(tmp_path / "db")) as store:
        lines = []
        monkeypatch.setattr(audit_log, "info", lines.append)
        store.create_project("orchard")
        assert len(lines) == 1 and "project.created" in lines[0]


def test_a_log_failure_after_publication_leaves_a_successful_import(tmp_path, monkeypatch):
    source = archive(tmp_path)
    db = tmp_path / "db"

    def broken(line):
        raise OSError("log is closed")

    monkeypatch.setattr(audit_log, "info", broken)
    assert restore(db, source) == 0
    with closing(Store(db)) as store:
        assert store.get_project("orchard") is not None


# --- test_reviewer12_bootstrap.py ---


def test_refused_key_bootstrap_does_not_leave_a_database(tmp_path, monkeypatch):
    def refuse(self, label):
        raise ValueError("cannot mint this key")

    monkeypatch.setattr(Store, "create_key", refuse)
    db = tmp_path / "new" / "db"
    out = db.parent / "key.env"
    assert cli.main(["key-create", "--db", str(db), "--out", str(out), "--label", "demo"]) == 1
    assert not db.parent.exists()


def test_key_bootstrap_does_not_mint_into_a_concurrent_creators_database(tmp_path, monkeypatch):
    db = tmp_path / "db"
    out = tmp_path / "key.env"
    make_folder = cli_data.make_folder

    def another(path):
        result = make_folder(path)
        if not db.exists():
            Store(db).close()
        return result

    monkeypatch.setattr(cli_data, "make_folder", another)
    assert cli.main(["key-create", "--db", str(db), "--out", str(out), "--label", "demo"]) == 1
    assert not out.exists()
    store = Store(db)
    try:
        assert store.list_keys() == []
    finally:
        store.close()


# --- test_reviewer12_import.py ---


@pytest.mark.parametrize("valid", [True, False], ids=["publish", "refuse"])
def test_import_keeps_a_concurrent_creators_files(tmp_path, monkeypatch, capsys, valid):
    db = tmp_path / "db"
    archive = tmp_path / "in.zip"
    with closing(Store(":memory:")) as source, archive.open("wb") as out:
        source.create_project("orchard")
        source.export(out)
    if not valid:
        archive.write_bytes(b"bad")
    real = cli_data.make_folder
    side = db.with_name("db-journal")

    def create_first(path):
        made = real(path)
        db.write_bytes(b"first")
        side.write_bytes(b"kept")
        return made

    monkeypatch.setattr(cli_data, "make_folder", create_first)
    result = cli.main(
        ["import", "--db", str(db), "--account", DEFAULT_ACCOUNT_ID, "--in", str(archive)]
    )
    assert db.is_file() and db.read_bytes() == b"first"
    assert side.read_bytes() == b"kept"
    assert result == 1
    out, err = capsys.readouterr()
    assert out == "" and ("already exists" in err or "nothing was imported" in err)
    assert "workspace.imported" not in err
    assert set(tmp_path.iterdir()) == {archive, db, side}


def test_import_does_not_publish_over_a_leftover_sqlite_log(tmp_path, capsys):
    archive = tmp_path / "in.zip"
    with closing(Store(":memory:")) as source, archive.open("wb") as out:
        source.export(out)
    db = tmp_path / "db"
    log = tmp_path / "db-wal"
    log.write_bytes(b"kept")
    assert (
        cli.main(["import", "--db", str(db), "--account", DEFAULT_ACCOUNT_ID, "--in", str(archive)])
        == 1
    )
    assert not db.exists() and log.read_bytes() == b"kept"
    assert "db-wal" in capsys.readouterr().err


# --- test_reviewer12_publication.py ---


def test_publication_checkpoints_the_import_before_linking(tmp_path, monkeypatch):
    source = archive(tmp_path)
    db = tmp_path / "db"
    close = Store.close

    def leave_wal(store):
        store._con.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
        close(store)

    with monkeypatch.context() as patch:
        patch.setattr(Store, "close", leave_wal)
        assert restore(db, source) == 0
    with closing(Store(db, upgrade=False, create=False)) as published:
        assert published.get_project("orchard") is not None


def test_a_database_created_at_publication_wins(tmp_path, monkeypatch, capsys):
    source = archive(tmp_path)
    db = tmp_path / "db"
    link = os.link

    def first(staged, destination):
        with closing(Store(destination)) as other:
            other.create_project("first")
        link(staged, destination)

    monkeypatch.setattr(os, "link", first)
    assert restore(db, source) == 1
    assert "already exists" in capsys.readouterr().err
    with closing(Store(db, upgrade=False, create=False)) as other:
        assert [p.name for p in other.list_projects()] == ["first"]


def test_checkpoint_failure_keeps_the_import_private(tmp_path, monkeypatch, capsys):
    source = archive(tmp_path)
    db = tmp_path / "db"
    original = Store.import_export

    def deny_checkpoint(action, name, *_):
        if action == sqlite3.SQLITE_PRAGMA and name == "wal_checkpoint":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def import_then_deny(view, *args):
        result = original(view, *args)
        view._con.set_authorizer(deny_checkpoint)
        return result

    monkeypatch.setattr(Store, "import_export", import_then_deny)
    assert restore(db, source) == 1
    output = capsys.readouterr()
    assert "checkpoint" in output.err
    assert not output.out
    assert list(tmp_path.iterdir()) == [source]


# --- test_reviewer12_serve.py ---


def test_bad_model_settings_leave_the_old_database_and_lock_alone(tmp_path, monkeypatch, capsys):
    db = clean_31(tmp_path)
    before = db.read_bytes()
    monkeypatch.delenv("DEMO_MODEL", raising=False)
    locks = []
    take = cli.take_serve_lock

    def locking(path):
        locks.append(path)
        return take(path)

    monkeypatch.setattr(cli, "take_serve_lock", locking)
    assert cli.main(["serve", "--db", str(db), "--model-env", "DEMO"]) == 2
    assert "cannot use that model" in capsys.readouterr().err
    assert db.read_bytes() == before and locks == []


@pytest.mark.parametrize("start", ["new", "older", "current"])
def test_listener_failure_names_every_schema_change(tmp_path, monkeypatch, capsys, start):
    db = tmp_path / "db"
    if start == "older":
        db = clean_31(tmp_path)
    if start == "current":
        with closing(Store(db)):
            pass

    def refused(*args, **kwargs):
        raise SystemExit(3)

    monkeypatch.setattr(cli.uvicorn, "run", refused)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["serve", "--db", str(db)])
    assert stopped.value.code == 3
    err = capsys.readouterr().err
    assert user_version(db) == 32
    with connect(db) as con:
        assert con.execute("SELECT version FROM oldest_build").fetchone() == (32,)
    if start == "current":
        assert "committed schema" not in err
    else:
        source = 0 if start == "new" else 31
        assert f"committed schema {source} -> 32" in err and "floor 32" in err


def test_a_later_step_failing_names_only_the_steps_that_committed(tmp_path, monkeypatch, capsys):
    db = at_28_file(tmp_path)

    def broken(con):
        raise RuntimeError("step refused")

    monkeypatch.setitem(schema._MIGRATIONS, 30, broken)
    with pytest.raises(RuntimeError, match="step refused"):
        cli.main(["serve", "--db", str(db)])
    assert user_version(db) == 29
    err = capsys.readouterr().err
    assert "committed schema 28 -> 29" in err and "no recorded floor" in err


# --- test_reviewer12_start_writes.py ---


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
