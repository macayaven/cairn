"""Unit tests for the audit trail in the store: reading it a part at a time with filters, how
many events a workspace keeps, and the copy of each event on the logger "cairn.audit"."""

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

import cairn.store.base
from cairn.store import Store
from cairn.store.audit import AUDIT_LOGGER

ISSUER = "https://id.example.test/pool-1"
NOW = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db, clock=lambda: NOW)
    yield s
    s.close()


def count_events(db: Path, account_id: str) -> int:
    con = sqlite3.connect(db)  # closed here: `with` on a connection only ends a transaction
    try:
        (n,) = con.execute(
            "SELECT COUNT(*) FROM audit_events WHERE account_id = ?", (account_id,)
        ).fetchone()
    finally:
        con.close()
    return n


def stored_chain(store) -> str:
    with sqlite3.connect(store.database_file()) as con:
        (chain,) = con.execute("SELECT chain FROM audit_events ORDER BY seq DESC").fetchone()
    return chain


def admin_without(store, project_name: str):
    """Ada owns the workspace; Brook is an admin who does not reach `project_name`."""
    people = [
        store.sign_in("github", str(n), f"p{n}@example.test", name, issuer=ISSUER, join="default")
        for n, name in enumerate(("Ada", "Brook"))
    ]
    ada, brook = (store.for_account(p.account_id, p.user.id) for p in people)
    ada.set_role(people[1].user.id, "admin")
    hidden = ada.create_project(project_name, everyone="none")
    return ada, brook, hidden


# Reading a part at a time


def test_each_event_carries_its_number_in_its_workspace(store):
    for name in ("a", "b", "c"):
        store.create_project(name)
    assert [e.n for e in store.list_audit()] == [3, 2, 1]


def test_before_gives_the_events_numbered_below_it(store):
    for n in range(6):
        store.create_project(f"b{n}")
    first = store.list_audit(limit=2)
    assert [e.project for e in first] == ["b5", "b4"]
    older = store.list_audit(limit=2, before=first[-1].n)
    assert [e.project for e in older] == ["b3", "b2"]
    assert [e.project for e in store.list_audit(limit=10, before=older[-1].n)] == ["b1", "b0"]
    assert store.list_audit(limit=10, before=1) == []


def test_a_part_says_how_many_events_match_and_how_many_are_newer(store):
    for n in range(6):
        store.create_project(f"b{n}")
    first = store.list_audit(limit=2)
    assert (first.total, first.newer) == (6, 0)
    older = store.list_audit(limit=2, before=first[-1].n)
    assert (older.total, older.newer) == (6, 2)


def test_filters_by_action_project_and_who(store):
    ops = store.create_project("ops")
    store.acting_as("key", "laptop", "key_1").create_project("web")
    store.acting_as("key", "laptop", "key_1").record_note(ops.id, "unchanged", {"page": "deploy"})
    store.acting_as("token", "Quill", "grt_9").record_note(ops.id, "rejected", {"page": "x"})
    notes = ["note.rejected", "note.unchanged"]
    assert [e.action for e in store.list_audit(action="note")] == notes
    assert [e.action for e in store.list_audit(action="note.rejected")] == ["note.rejected"]
    assert [e.action for e in store.list_audit(action="not")] == []  # a family is whole words
    assert [e.project for e in store.list_audit(project="OPS")] == ["ops", "ops", "ops"]
    by_laptop = ["note.unchanged", "project.created"]
    assert [e.action for e in store.list_audit(who="laptop")] == by_laptop
    assert [e.action for e in store.list_audit(who="grt_9")] == ["note.rejected"]  # by its id too
    both = store.list_audit(action="note", who="Laptop", project="ops")
    assert [e.action for e in both] == ["note.unchanged"] and both.total == 1
    assert store.list_audit(project="nowhere").total == 0


def test_a_filter_keeps_every_rule_of_who_sees_what(store):
    """An admin who does not reach a private project sees none of its events, filtered or
    not, and the count counts only what they see."""
    ada, brook, hidden = admin_without(store, "hiring")
    ada.record_note(hidden.id, "unchanged", {"page": "offer"})
    assert [e.project for e in brook.list_audit(project="hiring")] == []
    assert brook.list_audit(project="hiring").total == 0
    assert all(e.project != "hiring" for e in brook.list_audit(limit=1000))
    seen = brook.list_audit(limit=1000)
    assert seen.total == len(seen)
    assert ada.list_audit(project="hiring").total == 2


def test_a_narrowed_view_reads_none_of_the_log_filtered_or_not(store):
    project = store.create_project("ops")
    assert store.narrow(projects=(project.id,)).list_audit(action="project") == []
    assert store.narrow(access="read").list_audit(before=10, who="system") == []


def test_reading_a_part_writes_nothing(store, db):
    store.create_project("ops")
    con = sqlite3.connect(db)
    before = "\n".join(con.iterdump())
    store.list_audit(limit=1, before=5, action="project", project="ops", who="system")
    assert "\n".join(con.iterdump()) == before
    con.close()


# How many events a workspace keeps


def test_a_workspace_keeps_100000_events_unless_told(store):
    assert store.audit_keep == 100_000


def test_the_number_kept_is_set_for_every_view_and_all_keeps_every_event(store, db):
    other = store.for_account(store.create_account("Fable Works").id)
    store.audit_keep = 5
    assert other.audit_keep == 5
    ops = store.create_project("ops")
    for n in range(20):
        store.record_note(ops.id, "unchanged", {"page": f"p{n}"})
    assert count_events(db, store.account_id) <= 5
    newest = [e.detail["page"] for e in store.list_audit(1000)]
    assert newest[0] == "p19"
    store.audit_keep = None  # all
    assert store.audit_keep is None
    for n in range(20, 40):
        store.record_note(ops.id, "unchanged", {"page": f"p{n}"})
    assert count_events(db, store.account_id) >= 20


def test_setting_the_number_the_file_already_reads_writes_nothing(store, db):
    """A server started again with the same number, or with the default on a file that
    records none, leaves the file as it was: a refused or unchanged request writes nothing,
    however often the server restarts."""
    con = sqlite3.connect(db)
    try:
        before = "\n".join(con.iterdump())
        store.audit_keep = 100_000  # the default, on a file that records none
        assert "\n".join(con.iterdump()) == before
        store.audit_keep = 5
        recorded = "\n".join(con.iterdump())
        assert recorded != before
        again = Store(db, clock=lambda: datetime(2026, 11, 3, tzinfo=UTC))
        try:
            again.audit_keep = 5  # a restart with the same number, at a later time
        finally:
            again.close()
        assert "\n".join(con.iterdump()) == recorded
        store.audit_keep = 100_000  # back to the default: recorded, so other processes read it
        reader = Store(db)
        try:
            assert reader.audit_keep == 100_000
        finally:
            reader.close()
    finally:
        con.close()


@pytest.mark.parametrize("keep", [0, -1, 1.5, "10", True])
def test_the_number_kept_is_a_whole_number_of_at_least_one(store, keep):
    with pytest.raises(ValueError):
        store.audit_keep = keep


@pytest.mark.parametrize("keep", [1, 2, 5, 10, 999])
def test_a_small_number_kept_never_drops_below_keep_with_the_real_batch(store, db, keep):
    """Pruning waits until a batch can be removed without dropping stored events below
    `keep`, so a workspace told to keep 5 never dips to 4 right after a prune."""
    assert cairn.store.base.AUDIT_PRUNE_BATCH == 1_000
    store.audit_keep = keep
    ops = store.create_project("ops")
    sizes = []
    for n in range(keep * 3 + 3):
        store.record_note(ops.id, "unchanged", {"page": f"p{n}"})
        sizes.append(count_events(db, store.account_id))
    batch = min(cairn.store.base.AUDIT_PRUNE_BATCH, max(1, keep // 2))
    assert max(sizes) <= keep + batch - 1
    assert min(sizes[keep:]) >= keep


# The copy on the logger "cairn.audit"


def test_every_stored_event_is_one_json_line_on_the_audit_logger(store, caplog):
    with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
        store.acting_as("key", "laptop", "key_1").create_project("ops")
    (record,) = [r for r in caplog.records if r.name == "cairn.audit"]
    line = json.loads(record.getMessage())
    (event,) = store.list_audit()
    assert line == {
        "account_id": store.account_id,
        "n": event.n,
        "at": event.at,
        "actor_kind": "key",
        "actor": "laptop",
        "actor_id": "key_1",
        "action": "project.created",
        "project": "ops",
        "detail": event.detail,
        "chain": stored_chain(store),  # schema step 31: the value that chains it
    }
    assert "\n" not in record.getMessage()


def test_a_write_that_rolls_back_sends_no_line(store, caplog, monkeypatch):
    ops = store.create_project("ops")
    with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
        with pytest.raises(RuntimeError), store._write() as con:
            store._audit(con, "note.unchanged", project="ops", detail={"page": "x"})
            raise RuntimeError("the write fails after its event")
        assert [r for r in caplog.records if r.name == "cairn.audit"] == []
        store.record_note(ops.id, "unchanged", {"page": "y"})
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "cairn.audit"]
    assert [line["detail"]["page"] for line in lines] == ["y"]


def test_a_logger_that_raises_does_not_fail_the_write(store):
    class Broken(logging.Handler):
        def emit(self, record):
            raise OSError("the log is full")

        def handleError(self, record):
            raise OSError("and says so")

    audit = logging.getLogger(AUDIT_LOGGER)
    broken = Broken()
    audit.addHandler(broken)
    try:
        store.create_project("ops")
    finally:
        audit.removeHandler(broken)
    assert [e.action for e in store.list_audit()] == ["project.created"]


def test_the_line_holds_no_note_text(store, caplog):
    ops = store.create_project("ops")
    text = "# Deploy\n\nThe zebrafinch job restarts at 09:00.\n"
    with caplog.at_level(logging.INFO, logger=AUDIT_LOGGER):
        store.add_note(ops.id, text, title="Deploy")
    said = "\n".join(r.getMessage() for r in caplog.records if r.name == "cairn.audit")
    assert "zebrafinch" not in said


def test_the_number_kept_holds_for_another_process_on_the_same_file(db, monkeypatch):
    """A command-line write through its own Store, while a server keeps every event, prunes
    nothing: the number the server was told is recorded in the file."""
    monkeypatch.setattr(cairn.store.base, "AUDIT_KEEP", 10)
    server = Store(db, clock=lambda: NOW)
    try:
        server.audit_keep = None
        ops = server.create_project("ops")
        for n in range(30):
            server.record_note(ops.id, "unchanged", {"page": f"p{n}"})
        command_line = Store(db, clock=lambda: NOW)
        try:
            assert command_line.audit_keep is None
            command_line.record_note(ops.id, "unchanged", {"page": "late"})
        finally:
            command_line.close()
        assert count_events(db, server.account_id) == 32
        server.audit_keep = 12
        later = Store(db, clock=lambda: NOW)
        try:
            later.record_note(ops.id, "unchanged", {"page": "later"})
            assert count_events(db, server.account_id) <= 12
        finally:
            later.close()
    finally:
        server.close()


def test_a_bound_view_cannot_change_how_many_events_are_kept_or_whether_reads_are(store):
    ops = store.create_project("ops")
    people = store.sign_in("github", "1", "p1@example.test", "Ada", issuer=ISSUER, join="default")
    for view in (
        store.narrow(projects=(ops.id,)),
        store.narrow(access="read"),
        store.for_account(people.account_id, people.user.id),
    ):
        with pytest.raises(PermissionError):
            view.audit_keep = 1
        with pytest.raises(PermissionError):
            view.reads_audited = True
    assert store.audit_keep == 100_000 and store.reads_audited is False


def test_who_system_finds_the_events_of_no_actor(store):
    store.create_project("ops")
    store.acting_as("key", "laptop", "key_1").create_project("web")
    assert [e.project for e in store.list_audit(who="System")] == ["ops"]
