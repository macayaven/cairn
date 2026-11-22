"""The audit trail's sentences of the security and privacy guides, each pinned to what the code
does: the guide's sentence is in the test, and the test shows it true over a real store."""

import io
import json
import logging
from importlib.resources import files

import pytest

from cairn.gate import lint_gate
from cairn.memory import EXPORTED_EVENT, READ_EVENTS, Memory
from cairn.store import Claim, Store
from cairn.store.audit import AUDIT_LOGGER
from cairn.web import cli


def guide(name: str) -> str:
    return " ".join(files("cairn.guides").joinpath(f"{name}.md").read_text().split())


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


def a_page(store):
    ops = store.create_project("ops")
    note, _ = store.add_note(ops.id, "# Heron count\n\nThe heron count is 12.\n")
    span = next(s.id for s in store.note_spans(note.id) if s.kind == "paragraph")
    claim = Claim(id="cl_000000000001", text="The heron count is 12.", span_ids=(span,))
    store.save_page(ops.id, "heron-count", "Heron count", "# Heron count\n\n12.\n", [claim], {})
    return ops, note


SECURITY_KEEPS = (
    "It keeps the newest 100,000 events of each workspace, unless the operator starts the server"
    " with `--audit-keep N`, or `--audit-keep all` for every event; the page says the number in"
    " force."
)


def test_security_how_many_events_are_kept(store):
    assert SECURITY_KEEPS in guide("security")
    assert store.audit_keep == 100_000
    args = cli._parser().parse_args(["serve", "--db", "x.db", "--audit-keep", "all"])
    assert cli._audit_kept(args) is None
    args = cli._parser().parse_args(["serve", "--db", "x.db", "--audit-keep", "7"])
    assert cli._audit_kept(args) == 7


SECURITY_WHO_READS = (
    "Owners and admins read it under Workspace, then Audit log, a part at a time, filtered by"
    " what was done, by project and by who, and download what they see as CSV or JSON; a key"
    " that manages the workspace reads it at `GET /api/audit`. Each export of the workspace's"
    " notes and pages, and each download of the log, is an event of its own, with who and when."
)


def test_security_who_reads_the_log_and_what_is_recorded_of_taking_it(store):
    assert SECURITY_WHO_READS in guide("security")
    a_page(store)
    Memory(store, lint_gate).export(io.BytesIO())
    assert store.list_audit(1)[0].action == EXPORTED_EVENT


SECURITY_READS = (
    "When the operator starts the server with `--audit-reads`, reading a page, a note, a claim's"
    " evidence, a search and a graph are each an event of the workspace's audit log too: who"
    " read, what kind of read, the project, and the page or note read, never the words searched"
    " or any text. It is off unless given, since each read then costs one more write."
)


def test_security_reads_are_events_when_the_operator_says(store):
    assert SECURITY_READS in guide("security")
    assert set(READ_EVENTS.values()) == {
        "read.page",
        "read.note",
        "read.evidence",
        "read.search",
        "read.graph",
        "read.notes",  # UI and UX 8: a part of a project's list of notes, which no sentence names
    }
    a_page(store)
    memory = Memory(store, lint_gate)
    with memory.access("recall"):
        memory.recall("heron")
    assert not [e for e in store.list_audit() if e.action.startswith("read.")]  # off
    store.reads_audited = True
    with memory.access("recall"):
        memory.recall("heron")
    (event,) = [e for e in store.list_audit() if e.action.startswith("read.")]
    assert "heron" not in json.dumps(event.model_dump())


SECURITY_COPY = (
    "The running server writes each event it stores as one line on its output, once the"
    " event's write has committed: a JSON object with the same fields as the stored event, on"
    " a logger of its own, and never a note's text. The copy can miss an event: one committed"
    " just before the server's process dies, and one a command of the command line stores,"
    " such as `key-create` or `import`, which writes its line on its own standard error"
    " instead, kept only where the operator keeps that output. Keeping those lines where"
    " nobody can change them, such as a bucket locked against deletion, is the operator's"
    " set-up, and such a place keeps what it received: the log in the database is rewritten"
    " when a note is forgotten or a project deleted, which takes out the page names made from"
    " notes, and loses its oldest events past the number kept."
)


def test_security_each_event_is_a_line_the_operator_keeps(store, caplog):
    assert SECURITY_COPY in guide("security")
    with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
        _, note = a_page(store)
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == AUDIT_LOGGER]
    assert len(lines) == len(store.list_audit(100))
    assert "heron count is" not in json.dumps(lines).lower()
    page_names = [line["detail"].get("page") for line in lines]
    store.forget_note(note.id)
    stored = [e.detail.get("page") for e in store.list_audit(100)]
    assert "heron-count" in page_names and "heron-count" not in stored


PRIVACY_LOG = (
    "It records each export of the workspace's notes and pages and each download of the log,"
    " and, when the operator turns it on, each read: who read, the kind of read, the project and"
    " the page or note read, never the words searched."
)
PRIVACY_NOT_KEPT = (
    "In the database, the words anyone searched for, or, unless the operator starts the server"
    " with `--audit-reads`, a record of what anyone read. With it, each read is an event of the"
    " audit log, as above, without the words searched."
)
PRIVACY_LINES = (
    "Each audit event the server stores is also one line there, written once it is stored; an"
    " event stored just before the server's process dies has none, and one a command of the"
    " command line stores is a line on that command's standard error instead. Those lines are"
    " not"
    " rewritten when a note is forgotten or a project deleted, and the oldest are not deleted"
    " with the database's: they keep the names of pages made from a note's title for as long as"
    " the operator keeps them."
)
PRIVACY_ERASED = (
    "The lines the server wrote for those events as they were stored (Logs, above) are not"
    " changed: they keep the page names."
)
PRIVACY_TABLE = (
    "| The audit log | The newest 100,000 events of each workspace, or as many as the operator"
    " set with `serve --audit-keep` (every one with `all`), and up to 1,000 older ones: the"
    " oldest are removed a batch at a time (1,000, or half the number kept when that is less) |"
)
PRIVACY_EXPORT = (
    "Exporting writes one thing to the database: an event of the audit log saying who exported"
    " and when."
)


def test_privacy_the_audit_log_records_exports_downloads_and_reads_when_on(store):
    text = guide("privacy")
    said = (PRIVACY_LOG, PRIVACY_NOT_KEPT, PRIVACY_LINES, PRIVACY_ERASED, PRIVACY_TABLE)
    for sentence in (*said, PRIVACY_EXPORT):
        assert sentence in text, sentence
    assert "Reading writes nothing there." not in text
    assert "Exporting writes nothing to the database." not in text
    a_page(store)
    before = len(store.list_audit(100))
    Memory(store, lint_gate).export(io.BytesIO())
    events = store.list_audit(100)
    assert len(events) == before + 1 and events[0].detail == {}


def test_the_guides_name_no_product_in_these_sentences():
    for sentence in (SECURITY_KEEPS, SECURITY_WHO_READS, SECURITY_READS, SECURITY_COPY,
                     PRIVACY_LOG, PRIVACY_NOT_KEPT, PRIVACY_LINES, PRIVACY_ERASED, PRIVACY_TABLE,
                     PRIVACY_EXPORT):  # fmt: skip
        assert "cairn" not in sentence.lower()


RUN_OPTIONS = (
    "`serve --audit-reads` records reads in the audit log; `--audit-keep N` or `all` sets how"
    " many events each workspace keeps; `serve` writes each event it stores as one JSON line on"
    " the `cairn.audit` logger, once the event's write has committed. That copy can miss an"
    " event: one committed just before the server's process dies, and one a command of the"
    " command line stores, such as `key-create` or `import`, which writes its line on its own"
    " standard error instead, kept only where the operator keeps that output."
)


def test_run_names_the_audit_options_and_the_logger(store, caplog):
    assert RUN_OPTIONS in guide("run")
    args = cli._parser().parse_args(["serve", "--db", "x.db", "--audit-reads"])
    assert args.audit_reads is True
    args = cli._parser().parse_args(["serve", "--db", "x.db", "--audit-keep", "all"])
    assert cli._audit_kept(args) is None
    assert AUDIT_LOGGER == "cairn.audit"
    with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
        store.create_project("ops")
    (line,) = [r.getMessage() for r in caplog.records if r.name == AUDIT_LOGGER]
    assert json.loads(line)["action"] == store.list_audit(1)[0].action
