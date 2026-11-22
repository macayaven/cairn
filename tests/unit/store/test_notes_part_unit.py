"""A project's notes can be listed (review: UI and UX, note 8): Store.notes_part gives one part
of a project's notes, newest first, without their text, and where the next part starts, in one
statement whatever the project holds. Fixtures are synthetic."""

from pathlib import Path

import pytest

from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.store import NoteHead, Store
from cairn.store.audit import UNKNOWN_WRITER


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


def note(store, project, n: int, **fields):
    made, _ = store.add_note(project.id, f"# Topic {n}\n\nQuill line {n}.\n", **fields)
    return made


def test_a_part_is_newest_first_with_where_the_next_starts(store):
    project = store.create_project("ops")
    notes = [note(store, project, n) for n in range(5)]
    first, after = store.notes_part(project.id, 2)
    assert [h.id for h in first] == [notes[4].id, notes[3].id]
    assert after == notes[3].id
    second, after = store.notes_part(project.id, 2, before=after)
    assert [h.id for h in second] == [notes[2].id, notes[1].id]
    last, after = store.notes_part(project.id, 2, before=after)
    assert [h.id for h in last] == [notes[0].id] and after is None


def test_a_full_last_part_says_a_next_that_is_empty(store):
    project = store.create_project("ops")
    notes = [note(store, project, n) for n in range(2)]
    part, after = store.notes_part(project.id, 2)
    assert len(part) == 2 and after == notes[0].id
    assert store.notes_part(project.id, 2, before=after) == ([], None)


def test_each_row_says_who_wrote_it_and_where_it_is_without_its_text(store):
    project = store.create_project("ops")
    waiting = note(store, project, 1, source_ref="meeting:standup", via="cli")
    filed = note(store, project, 2)
    assert compile_note(store, filed, gate=lint_gate).status == "saved"
    heads = {h.id: h for h in store.notes_part(project.id, 10)[0]}
    assert set(NoteHead.model_fields) == {
        "id", "title", "created_at", "source_ref", "via", "writer", "writer_name", "waiting",
        "page",
    }  # fmt: skip
    one = heads[waiting.id]
    assert (one.title, one.source_ref, one.via) == ("Topic 1", "meeting:standup", "cli")
    assert one.created_at == waiting.created_at
    assert (one.waiting, one.page) == (True, None)
    assert (one.writer, one.writer_name) == ("operator", None)
    two = heads[filed.id]
    assert (two.waiting, two.page) == (False, "topic-2")
    assert "Quill" not in repr(heads)


def test_a_note_stored_before_writers_were_recorded_is_by_an_unknown_writer(store, db):
    import sqlite3

    project = store.create_project("ops")
    made = note(store, project, 1)
    con = sqlite3.connect(db)
    with con:
        con.execute("DELETE FROM note_writers WHERE note_id = ?", (made.id,))
    con.close()
    (head,) = store.notes_part(project.id, 10)[0]
    assert (head.writer, head.writer_name) == (UNKNOWN_WRITER, None)


def test_a_part_is_scoped_as_list_notes_is(store):
    ops, other = store.create_project("ops"), store.create_project("other")
    note(store, ops, 1)
    theirs = note(store, other, 2)
    narrowed = store.narrow([other.id])
    assert narrowed.notes_part(ops.id, 10) == ([], None) and narrowed.list_notes(ops.id) == []
    assert [h.id for h in narrowed.notes_part(other.id, 10)[0]] == [theirs.id]
    elsewhere = store.for_account(store.create_account("Elsewhere").id)
    assert elsewhere.notes_part(ops.id, 10) == ([], None)
    # a cursor from another project starts nothing
    assert store.notes_part(ops.id, 10, before=theirs.id) == ([], None)
    assert store.notes_part(ops.id, 0) == ([], None)


def test_a_part_is_one_statement_and_at_most_limit_rows_whatever_the_project_holds(store):
    project = store.create_project("ops")
    for n in range(40):
        note(store, project, n)
    seen: list[str] = []
    rows = [0]

    def counted(cursor, values):
        rows[0] += 1
        return values

    store.notes_part(project.id, 5)  # a reader opened before the observers are attached
    store._observe(trace=seen.append, rows=counted)
    try:
        part, after = store.notes_part(project.id, 5)
        store.notes_part(project.id, 5, before=after)
    finally:
        store._observe(trace=None, rows=None)
    selects = [s for s in seen if s.lstrip().upper().startswith("SELECT")]
    assert len(part) == 5 and len(selects) == 2  # one for each part
    assert rows[0] == 10
