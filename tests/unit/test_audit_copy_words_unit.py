"""What the copy of the audit trail on the `cairn.audit` logger is, as the guides now say it
(second review, should-fix 15): `serve` writes each event it stores as one line once the
event's write has committed, so an event committed just before the process dies has no
line; and a command of the command line, such as `key-create` or `import`, writes its lines
on its own stderr, as serve does, and leaves stdout, which scripts read, as it was. Made-up
data; nothing sleeps."""

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
def test_a_command_of_the_command_line_writes_its_audit_lines_on_its_stderr(
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
    assert AUDIT_LOGGER not in printed.out  # stdout as it was
    lines = [
        json.loads(line.removeprefix(f"{AUDIT_LOGGER}: "))
        for line in printed.err.splitlines()
        if line.startswith(f"{AUDIT_LOGGER}: ")
    ]
    assert expected in [line["action"] for line in lines]
    # as serve writes them: the stored event's own fields
    assert {"account_id", "n", "action", "chain"} <= set(lines[-1])


def test_the_logger_is_put_back_after_a_command(db, tmp_path, capsys):
    logger = logging.getLogger(AUDIT_LOGGER)
    before = (list(logger.handlers), logger.level, logger.propagate)
    argv = ["key-create", "--db", str(db), "--label", "laptop", "--out", str(tmp_path / "k.env")]
    assert cli.main(argv) == 0
    assert (list(logger.handlers), logger.level, logger.propagate) == before


SECURITY_CHAIN = (
    "Someone who can write the database file can rewrite the whole chain, which is why the"
    " server writes a copy of the events it stores on its output (above, with the events that"
    " copy can miss)."
)
SECURITY_AGAINST = "Against the copy, a change shows only for an event the copy holds."


def test_the_security_guide_says_the_copy_is_of_the_events_it_holds():
    from guide_source import guide_source

    text = " ".join(guide_source("security").split())
    for sentence in (SECURITY_CHAIN, SECURITY_AGAINST):
        assert text.count(sentence) == 1, sentence
    assert "the copy of each event on the server's output" not in text


SPEC_ROW_SAYS = (
    "Each event is chained to the one before it; `serve` copies each event it stores to a"
    " logger after its write commits (an event committed just before the process dies can be"
    " missing; other commands write theirs on their own stderr)."
)


def test_the_spec_row_on_the_audit_trail_says_what_the_copy_is():
    """docs/spec-v1.md's Design row on the audit trail says what the tests above run: the
    copy is written once the event's write commits (one committed just before the process
    dies has no line), and a command of the command line writes its lines on its own
    stderr. It fails until the row says it (the coordinator's sentence)."""
    spec = (Path(__file__).resolve().parents[2] / "docs" / "spec-v1.md").read_text("utf-8")
    (row,) = [line for line in spec.splitlines() if line.startswith("| Audit trail |")]
    row = " ".join(row.split())
    assert row.count(SPEC_ROW_SAYS) == 1
    assert "and copied to a logger, so" not in row
