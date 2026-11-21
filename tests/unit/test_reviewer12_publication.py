"""A published main file holds the entire import, including the last committed WAL frame."""

import os
import sqlite3
from contextlib import closing

from cairn.store import DEFAULT_ACCOUNT_ID, Store
from cairn.web import cli


def archive(tmp_path):
    source = tmp_path / "in.zip"
    with closing(Store(":memory:")) as store, source.open("wb") as out:
        store.create_project("orchard")
        store.export(out)
    return source


def restore(db, source):
    return cli.main(
        ["import", "--db", str(db), "--account", DEFAULT_ACCOUNT_ID, "--in", str(source)]
    )


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
