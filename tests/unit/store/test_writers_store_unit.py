"""A1, part A in the store: every note records who wrote it, taken from the view that stores
it (never from what the note says), and a person is named without an address."""

import io
import json
import sqlite3
import zipfile
from datetime import UTC, datetime

import pytest

from cairn.store import DEFAULT_ACCOUNT_ID, Store
from cairn.store.audit import WRITER_KINDS, person_name

CLOCK = datetime(2099, 2, 3, 4, 5, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db", clock=lambda: CLOCK)
    yield s
    s.close()


def note(view, project_id: str, text: str, via: str | None = None) -> str:
    note, created = view.add_note(project_id, f"# Note\n\n{text}\n", via=via)
    assert created
    return note.id


# A person's name


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        ("Ada", "Ada"),
        ("Ada Lovelace", "Ada Lovelace"),
        ("cy@example.test", "cy"),  # a provider gave no name: the store's name is the address
        ("Cy <cy@example.test>", "Cy"),
        ("<cy@example.test>", "cy"),
        ("Dee (dee@example.test) Moss", "Dee Moss"),
        ("[ed@example.test]", "ed"),
        ("Fay @fay", "Fay fay"),  # an "@" that is no address is dropped all the same
    ],
)
def test_a_persons_name_is_their_name_and_never_an_address(stored, shown):
    assert person_name(stored) == shown
    assert "@" not in person_name(stored)


# Who a note records


def test_each_kind_of_view_records_its_own_kind_and_name(store):
    project = store.create_project("ops")
    bare = note(store, project.id, "The operator wrote this.")
    key = note(
        store.acting_as("key", "laptop", "key_000000000001"), project.id, "A key wrote this."
    )
    agent = note(store.acting_as("token", "probe", "grant_000000000001"), project.id, "An agent.")
    person = note(store.acting_as("session", "Cy <cy@example.test>"), project.id, "A person.")
    source = note(
        store.acting_as("system", None).writing_as("source", "team-mail"), project.id, "Mail."
    )
    via_key = note(
        store.acting_as("session", "Ada", "usr_000000000001").writing_as(
            "key", "ada-desk", "key_000000000002"
        ),
        project.id,
        "A session opened with a key.",
    )
    writers = store.note_writers([bare, key, agent, person, source, via_key])
    assert {i: (kind, name) for i, (kind, name, _) in writers.items()} == {
        bare: ("operator", None),
        key: ("key", "laptop"),
        agent: ("agent", "probe"),
        person: ("person", "Cy"),
        source: ("source", "team-mail"),
        via_key: ("key", "ada-desk"),
    }


def test_the_writer_is_the_views_whatever_via_the_note_names(store):
    project = store.create_project("ops")
    view = store.acting_as("key", "laptop", "key_000000000001")
    written = note(view, project.id, "It says it came from the web.", via="web")
    assert store.note_writers([written])[written] == ("key", "laptop", "web")


def test_a_note_sent_again_keeps_its_first_writer(store):
    project = store.create_project("ops")
    first = note(store.acting_as("key", "laptop", "key_000000000001"), project.id, "Once.")
    again, created = store.acting_as("token", "probe", "grant_1").add_note(
        project.id, "# Note\n\nOnce.\n"
    )
    assert (again.id, created) == (first, False)
    assert store.note_writers([first])[first][:2] == ("key", "laptop")


def test_a_note_with_no_recorded_writer_is_unknown(store, tmp_path):
    project = store.create_project("ops")
    written = note(store, project.id, "Stored before writers were recorded.", via="gmail")
    con = sqlite3.connect(tmp_path / "cairn.db")
    con.execute("DELETE FROM note_writers")
    con.commit()
    con.close()
    assert store.note_writers([written]) == {written: ("unknown", None, "gmail")}


def test_writers_are_read_only_where_the_view_reaches(store):
    ops, hr = store.create_project("ops"), store.create_project("hr")
    mine, theirs = note(store, ops.id, "Ops."), note(store, hr.id, "People.")
    narrowed = store.narrow([ops.id], "read")
    assert set(narrowed.note_writers([mine, theirs, "cap_000000000000"])) == {mine}
    other = store.for_account(store.create_account("other").id)
    assert other.note_writers([mine, theirs]) == {}


def test_a_view_takes_only_the_kinds_of_writer_there_are(store):
    assert WRITER_KINDS == ("person", "key", "agent", "source", "operator")
    with pytest.raises(ValueError):
        store.writing_as("unknown", "x")
    with pytest.raises(ValueError):
        store.writing_as("system", None)


def test_writing_as_keeps_the_views_narrowing(store):
    ops, hr = store.create_project("ops"), store.create_project("hr")
    view = store.narrow([ops.id], "write").writing_as("key", "laptop", "key_000000000001")
    assert [b.name for b in view.list_projects()] == ["ops"]
    with pytest.raises(KeyError):
        view.add_note(hr.id, "# Note\n\nOut of reach.\n")


# Erasure takes a note's writer with it


def writer_rows(tmp_path) -> int:
    con = sqlite3.connect(tmp_path / "cairn.db")
    try:
        return con.execute("SELECT COUNT(*) FROM note_writers").fetchone()[0]
    finally:
        con.close()


def test_forgetting_a_note_deleting_its_project_or_its_workspace_removes_its_writer(
    store, tmp_path
):
    ops, hr = store.create_project("ops"), store.create_project("hr")
    kept = note(store.acting_as("key", "laptop", "key_1"), ops.id, "Kept.")
    gone = note(store.acting_as("key", "laptop", "key_1"), ops.id, "Forgotten.")
    note(store, hr.id, "In a project deleted.")
    assert writer_rows(tmp_path) == 3
    store.forget_note(gone)
    assert writer_rows(tmp_path) == 2 and gone not in store.note_writers([kept, gone])
    store.delete_project(hr.id)
    assert writer_rows(tmp_path) == 1
    other = store.for_account(store.create_account("other").id)
    note(other, other.create_project("ops").id, "Elsewhere.")
    store.delete_account(other.account_id)
    assert writer_rows(tmp_path) == 1


# Export and import


def lines_of(archive: bytes, project: str) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(archive)) as held:
        data = held.read(f"projects/{project}/notes.jsonl").decode()
    return [json.loads(line) for line in data.splitlines()]


def test_an_export_names_each_writer_but_a_sources_name(store, tmp_path):
    ops = store.create_project("ops")
    note(store.acting_as("key", "laptop", "key_1"), ops.id, "By a key.")
    note(store.acting_as("system", None).writing_as("source", "team-mail"), ops.id, "Mail.")
    unknown = note(store, ops.id, "Before.")
    con = sqlite3.connect(tmp_path / "cairn.db")
    con.execute("DELETE FROM note_writers WHERE note_id = ?", (unknown,))
    con.commit()
    con.close()
    out = io.BytesIO()
    store.export(out)
    assert [line["writer"] for line in lines_of(out.getvalue(), "ops")] == [
        {"kind": "key", "name": "laptop"},
        {"kind": "source", "name": None},  # an export never holds a source's name
        None,  # stored before writers were recorded: none
    ]
    assert "team-mail" not in out.getvalue().decode("latin-1")


def test_an_import_restores_writers_and_writes_its_own_for_lines_that_name_none(store, tmp_path):
    ops = store.create_project("ops")
    ids = [
        note(store.acting_as("session", "Ada"), ops.id, "By Ada."),
        note(store, ops.id, "Unknown.", via="gmail"),
        note(store, ops.id, "Older archive."),
    ]
    out = io.BytesIO()
    store.export(out)
    lines = lines_of(out.getvalue(), "ops")
    lines[0]["writer"]["name"] = "Ada <ada@example.test>"  # an archive's name holds no address
    lines[1]["writer"] = None  # an export of a note stored before writers were recorded
    del lines[2]["writer"]  # an archive written before writers were exported
    rebuilt = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(out.getvalue())) as source,
        zipfile.ZipFile(rebuilt, "w") as target,
    ):
        for info in source.infolist():
            data = source.read(info)
            if info.filename == "projects/ops/notes.jsonl":
                data = "".join(json.dumps(line) + "\n" for line in lines).encode()
            target.writestr(info, data)
    other = Store(tmp_path / "other.db", clock=lambda: CLOCK)
    try:
        other.import_export(io.BytesIO(rebuilt.getvalue()))
        assert other.note_writers(ids) == {
            ids[0]: ("person", "Ada", None),
            ids[1]: ("unknown", None, "gmail"),
            ids[2]: ("operator", None, None),  # the command line wrote it, from the file
        }
    finally:
        other.close()


@pytest.mark.parametrize(
    "writer",
    [
        {"kind": "system", "name": None},
        {"kind": "key"},
        {"kind": "key", "name": "x" * 201},
        {"kind": "key", "name": 3},
        "key",
    ],
)
def test_an_import_refuses_a_writer_it_would_not_store(store, tmp_path, writer):
    ops = store.create_project("ops")
    note(store, ops.id, "A note.")
    out = io.BytesIO()
    store.export(out)
    (line,) = lines_of(out.getvalue(), "ops")
    line["writer"] = writer
    rebuilt = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(out.getvalue())) as source,
        zipfile.ZipFile(rebuilt, "w") as target,
    ):
        for info in source.infolist():
            data = source.read(info)
            if info.filename == "projects/ops/notes.jsonl":
                data = (json.dumps(line) + "\n").encode()
            target.writestr(info, data)
    other = Store(tmp_path / "other.db")
    try:
        with pytest.raises(ValueError, match="writer"):
            other.import_export(io.BytesIO(rebuilt.getvalue()))
        assert other.for_account(DEFAULT_ACCOUNT_ID).list_projects() == []
    finally:
        other.close()
