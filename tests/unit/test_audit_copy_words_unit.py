"""What the copy of the audit trail on the `cairn.audit` logger is, as the guides now say it
(second review, should-fix 15): `serve` writes each event it stores as one line once the
event's write has committed, so an event committed just before the process dies has no
line; and a command of the command line, such as `key-create` or `import`, stores its
events and writes no such line. Made-up data; nothing sleeps."""

import io
import json
import logging
import sqlite3
from pathlib import Path

import pytest

from cairn.store import DEFAULT_ACCOUNT_ID, Store, base
from cairn.store.base import AUDIT_LOGGER
from cairn.web import cli


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


def stored_actions(db: Path) -> list[str]:
    con = sqlite3.connect(db)
    try:
        return [a for (a,) in con.execute("SELECT action FROM audit_events ORDER BY seq")]
    finally:
        con.close()


class SeesTheRow(logging.Handler):
    """At each line, whether another connection already reads the event it carries."""

    def __init__(self, db: Path) -> None:
        super().__init__()
        self.db, self.seen = db, []

    def emit(self, record: logging.LogRecord) -> None:
        line = json.loads(record.getMessage())
        con = sqlite3.connect(self.db)
        try:
            found = con.execute(
                "SELECT 1 FROM audit_events WHERE account_id = ? AND n = ?",
                (line["account_id"], line["n"]),
            ).fetchone()
        finally:
            con.close()
        self.seen.append((line["action"], found is not None))


def test_each_line_is_written_once_its_event_has_committed(db):
    store = Store(db)
    handler = SeesTheRow(db)
    logger = logging.getLogger(AUDIT_LOGGER)
    level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        store.create_project("ops")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
        store.close()
    assert handler.seen == [("project.created", True)]


class Died(BaseException):
    """The process stops between the commit and the line: nothing after it runs."""


def test_an_event_committed_just_before_the_process_dies_has_no_line(db, monkeypatch, caplog):
    store = Store(db)

    def dies(line):
        raise Died

    monkeypatch.setattr(base.audit_log, "info", dies)
    try:
        with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER), pytest.raises(Died):
            store.create_project("ops")
    finally:
        store.close()
    assert "project.created" in stored_actions(db)  # stored
    assert not [r for r in caplog.records if r.name == AUDIT_LOGGER]  # and no line


@pytest.mark.parametrize("command", ["key-create", "import"])
def test_a_command_of_the_command_line_stores_its_events_and_writes_no_line(
    db, tmp_path, capsys, command
):
    if command == "key-create":
        argv = ["key-create", "--db", str(db), "--label", "laptop"]
        argv += ["--out", str(tmp_path / "laptop.env")]
        expected = "key.created"
    else:
        source = Store(tmp_path / "source.db")
        try:
            source.create_project("ops")
            archive = io.BytesIO()
            source.export(archive)
        finally:
            source.close()
        (tmp_path / "export.zip").write_bytes(archive.getvalue())
        argv = ["import", "--db", str(db), "--account", DEFAULT_ACCOUNT_ID]
        argv += ["--in", str(tmp_path / "export.zip")]
        expected = "workspace.imported"
    capsys.readouterr()
    assert cli.main(argv) == 0
    printed = capsys.readouterr()
    assert expected in stored_actions(db)
    assert AUDIT_LOGGER not in printed.out + printed.err
