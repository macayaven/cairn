"""Ordinary and newly published databases report committed events to the active logger."""

from contextlib import closing

from test_reviewer12_publication import archive, restore

from cairn.store import Store
from cairn.store.base import audit_log


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
