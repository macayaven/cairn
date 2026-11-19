"""Reads as audit events (serve --audit-reads), and an export as one always: what Memory
records when a read ends, under the same door as the access log (Memory.access)."""

import io
import logging
import sqlite3
from datetime import UTC, datetime

import pytest

from cairn.gate import lint_gate
from cairn.memory import Memory, NotFound, Unavailable
from cairn.store import Claim, Store
from cairn.store.base import CannotWrite

CLOCK = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)
TEXT = "# Deploy\n\nThe zebrafinch job restarts at 09:00.\n"
CLAIM = "cl_000000000001"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db", clock=lambda: CLOCK)
    yield s
    s.close()


@pytest.fixture
def world(store):
    ops = store.create_project("ops")
    note, _ = store.add_note(ops.id, TEXT)
    span = next(s.id for s in store.note_spans(note.id) if s.kind == "paragraph")
    claim = Claim(id=CLAIM, text="The job restarts at 09:00.", span_ids=(span,))
    store.save_page(ops.id, "deploy", "Deploy", "# Deploy\n\nThe job restarts.\n", [claim], {})
    store.reads_audited = True
    view = store.acting_as("key", "laptop", "key_1")
    return ops, note, Memory(view, lint_gate)


def reads(store) -> list:
    return [e for e in store.list_audit(1000) if e.action.startswith("read.")]


def test_reads_are_not_events_unless_the_server_says(store):
    assert store.reads_audited is False
    ops = store.create_project("ops")
    memory = Memory(store, lint_gate)
    with memory.access("recall"):
        memory.recall("deploy")
    with memory.access("overview"):
        memory.overview("ops")
    assert reads(store) == [] and ops


def test_reading_a_page_is_an_event_naming_who_the_project_and_the_page(store, world):
    _, _, memory = world
    with memory.access("page"):
        memory.page("ops", "deploy")
    (event,) = reads(store)
    assert (event.action, event.project, event.detail) == ("read.page", "ops", {"page": "deploy"})
    assert (event.actor_kind, event.actor, event.actor_id) == ("key", "laptop", "key_1")


def test_reading_a_claims_evidence_names_the_page_and_the_claim(store, world):
    _, _, memory = world
    with memory.access("evidence"):
        memory.evidence("ops", "deploy", CLAIM)
    (event,) = reads(store)
    assert (event.action, event.detail) == ("read.evidence", {"claim": CLAIM, "page": "deploy"})


def test_a_search_is_an_event_without_its_words(store, world, tmp_path):
    _, _, memory = world
    with memory.access("recall"):
        memory.recall("zebrafinch restarts", "ops")
    (event,) = reads(store)
    assert (event.action, event.project, event.detail) == ("read.search", "ops", {})
    with memory.access("recall"):
        memory.recall("quokka")  # every project: no project named
    assert reads(store)[0].project is None
    with sqlite3.connect(tmp_path / "cairn.db") as con:
        rows = "\n".join(str(r) for r in con.execute("SELECT * FROM audit_events"))
    assert "quokka" not in rows and "zebrafinch" not in rows


def test_the_page_that_shows_a_note_names_the_note(store, world):
    _, note, memory = world
    with memory.access("ask_forget"):
        memory.forget_preview("ops", note.id)
    (event,) = reads(store)
    assert (event.action, event.project, event.detail) == ("read.note", "ops", {"note": note.id})


def test_a_graph_is_an_event_of_its_project(store, world):
    _, _, memory = world
    with memory.access("graph"):
        memory._touched = "ops"  # as the graph's routes set it
    (event,) = reads(store)
    assert (event.action, event.project, event.detail) == ("read.graph", "ops", {})


def test_a_read_that_fails_and_one_that_is_no_read_are_no_events(store, world):
    _, _, memory = world
    with pytest.raises(NotFound), memory.access("page"):
        memory.page("ops", "nowhere")
    with memory.access("overview"):
        memory.overview("ops")
    with memory.access("projects"):
        memory.projects()
    with memory.access("recall"):
        pass  # the search page with no words searched nothing
    assert reads(store) == []


def test_a_read_only_key_limited_to_one_project_is_recorded_too(store, world):
    ops, _, _ = world
    narrow = store.acting_as("key", "reader", "key_2").narrow(projects=(ops.id,), access="read")
    memory = Memory(narrow, lint_gate)
    with memory.access("page"):
        memory.page("ops", "deploy")
    (event,) = reads(store)
    assert (event.action, event.actor) == ("read.page", "reader")


def test_a_read_whose_event_cannot_be_written_is_unavailable(store, world, monkeypatch, caplog):
    _, _, memory = world

    def full(*args, **kwargs):
        raise CannotWrite(sqlite3.OperationalError("database or disk is full"))

    monkeypatch.setattr(type(memory.store), "_record_read", full)
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        with pytest.raises(Unavailable), memory.access("page"):
            memory.page("ops", "deploy")
    (record,) = [r for r in caplog.records if r.name == "cairn.access"]
    assert record.status == 503


def test_a_deleted_project_takes_the_page_names_out_of_its_read_events(store, world):
    _, _, memory = world
    with memory.access("page"):
        memory.page("ops", "deploy")
    store.delete_project(store.get_project("ops").id)
    (event,) = reads(store)
    assert "page" not in event.detail


def test_an_export_is_one_event_with_reads_on_or_off(store, world):
    _, _, memory = world
    exporter = Memory(store.acting_as("session", "Ada", "usr_1"), lint_gate)
    for on in (True, False):
        store.reads_audited = on
        with exporter.access("workspace.exported"):
            exporter.export(io.BytesIO())
    exported = [e for e in store.list_audit(1000) if e.action == "workspace.exported"]
    assert [(e.actor_kind, e.actor, e.project, e.detail) for e in exported] == [
        ("session", "Ada", None, {})
    ] * 2
    assert reads(store) == []


def test_an_export_refused_is_no_event(store, world):
    ops, _, _ = world
    narrow = Memory(store.narrow(projects=(ops.id,)), lint_gate)
    with pytest.raises(Exception):  # noqa: B017  Forbidden
        narrow.export(io.BytesIO())
    assert [e for e in store.list_audit(1000) if e.action == "workspace.exported"] == []
