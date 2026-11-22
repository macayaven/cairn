"""Unit tests for the note guard where it is enforced: `Store.add_note`, the one place
a note is written. Whatever calls it (the server, a source, a script) gets the workspace's
policy; nothing reaches the database unscreened. Secret-shaped strings are built at run
time, so this file holds nothing that looks like a live credential."""

import sqlite3

import pytest

import cairn.guard
from cairn.compiler import provenance_gate
from cairn.guard import Finding, PatternGuard
from cairn.memory import Memory, Refused
from cairn.store import NoteRefused, Store

TOKEN = "ghp_" + "Zq7xR2" * 6
EMAIL = "grace" + "@example.test"
NOTE = f"# Deploy\n\nThe job restarts at 09:00.\n\nIt uses {TOKEN} for now.\n"
FIELDS = ("title", "topic", "label", "source_ref", "via")


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


def dump(tmp_path) -> str:
    con = sqlite3.connect(tmp_path / "cairn.db")
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def test_add_note_refuses_a_secret_and_stores_nothing_but_the_refusal(store, tmp_path):
    ops = store.create_project("ops")
    with pytest.raises(NoteRefused) as refused:
        store.add_note(ops.id, NOTE)
    assert refused.value.findings == [("text", Finding(rule="github_token", kind="secret", line=5))]
    assert TOKEN not in str(refused.value)
    assert store.list_notes(ops.id) == []
    event = store.list_audit()[0]
    assert (event.action, event.project, event.detail) == (
        "note.refused",
        "ops",
        {"rules": ["github_token"]},
    )
    assert TOKEN not in dump(tmp_path)


@pytest.mark.parametrize("field", FIELDS)
def test_every_stored_field_is_screened(store, tmp_path, field):
    ops = store.create_project("ops")
    # the token alone: it fits every field's bound (FIELD_MAX)
    with pytest.raises(NoteRefused) as refused:
        store.add_note(ops.id, "# Deploy\n\nNothing here.\n", **{field: TOKEN})
    assert [(where, f.rule) for where, f in refused.value.findings] == [(field, "github_token")]
    assert store.list_notes(ops.id) == [] and TOKEN not in dump(tmp_path)


def test_the_workspace_policy_applies_to_every_view_of_it(store):
    ops = store.create_project("ops")
    writer = store.narrow((ops.id,), "write").acting_as("key", "ops-writer")
    contact = f"# Contacts\n\nWrite to {EMAIL} about it.\n"
    writer.add_note(ops.id, contact)  # the default policy: secrets only
    store.set_setting("guard.policy", "secrets+personal")
    with pytest.raises(NoteRefused):
        writer.add_note(ops.id, contact.replace("Contacts", "People"))
    store.set_setting("guard.policy", "off")
    _, created = writer.add_note(ops.id, NOTE)
    assert created


def test_a_read_only_view_is_refused_as_a_writer_and_learns_nothing(store, tmp_path):
    ops = store.create_project("ops")
    before = dump(tmp_path)
    with pytest.raises(PermissionError):
        store.narrow(None, "read").add_note(ops.id, NOTE)
    assert dump(tmp_path) == before


def test_an_unknown_project_is_unknown_before_it_is_refused(store):
    with pytest.raises(KeyError):
        store.add_note("br_000000000000", NOTE)
    assert store.list_audit() == []


def test_a_policy_changed_while_screening_is_the_one_that_decides(store, monkeypatch):
    """Matching runs outside the write; inside it, the policy in force decides, so a note
    sent while the guard was off cannot be stored after the workspace turned it on."""
    ops = store.create_project("ops")
    store.set_setting("guard.policy", "off")
    check = PatternGuard.check

    def turned_on_meanwhile(self, text):
        store.set_setting("guard.policy", "secrets")  # another request, while this one matches
        return check(self, text)

    monkeypatch.setattr(cairn.guard.PatternGuard, "check", turned_on_meanwhile)
    with pytest.raises(NoteRefused):
        store.add_note(ops.id, NOTE)
    assert store.list_notes(ops.id) == []


def test_a_view_can_carry_its_own_guard(store):
    class NoQuill:
        def check(self, text: str) -> list[Finding]:
            return [
                Finding(rule="house_rule", kind="secret", line=n)
                for n, line in enumerate(text.splitlines(), 1)
                if "quill" in line.lower()
            ]

    ops = store.create_project("ops")
    guarded = store.with_guard(NoQuill()).narrow((ops.id,), "write")
    with pytest.raises(NoteRefused) as refused:
        guarded.add_note(ops.id, "# A\n\nQuill retries.\n")
    assert [f.rule for _, f in refused.value.findings] == ["house_rule"]
    _, created = guarded.add_note(ops.id, NOTE)  # the workspace's patterns are replaced
    assert created


def test_memory_note_turns_a_store_refusal_into_a_422_for_any_field(store, tmp_path):
    store.create_project("ops")
    memory = Memory(store, provenance_gate)
    with pytest.raises(Refused) as refused:
        memory.note("ops", "# Deploy\n\nNothing here.\n", via=TOKEN)
    assert refused.value.status == 422
    assert refused.value.listed() == [
        {"rule": "github_token", "kind": "secret", "line": 1, "field": "via"}
    ]
    assert TOKEN not in str(refused.value) and TOKEN not in dump(tmp_path)
