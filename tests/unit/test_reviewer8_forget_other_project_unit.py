"""A note forgotten through the library leaves every project that cited it."""

import io
import json
import re
from contextlib import closing
from zipfile import ZipFile

import pytest

from cairn.compiler import render_body
from cairn.gate import lint_gate
from cairn.memory import Memory, NotFound
from cairn.spans import claim_id
from cairn.store import Claim, Store

SENTENCE = "Quill sealed the vermilion envelope."


@pytest.fixture
def desk():
    with closing(Store(":memory:")) as store:
        yield Memory(store, lint_gate)


def file_note(desk, project, title, text):
    filed = desk.note(project.name, text, title=title, topic=title)
    assert filed["status"] == "saved", filed
    (passage,) = desk.store.note_spans(filed["note_id"])
    return passage


def save_claim(store, project, title, text, passages):
    claim = Claim(id=claim_id(passages[0].id), text=text, span_ids=tuple(p.id for p in passages))
    body = render_body(title, [claim], {p.id: p for p in passages})
    return store.save_page(project.id, title.lower(), title, body, [claim])


def legacy_page(store, project, title, borrowed, *local):
    """Recreate an old save even once new cross-project citations are refused.

    Temporarily put the note beside the page for the ordinary save, so versions, search
    rows and topic keys are real. Restore its original project before any read or forget.
    """
    original = store.get_note(borrowed.source_id).project_id

    def locate(project_id):
        with store._write() as con:
            con.execute(
                "UPDATE notes SET project_id = ? WHERE id = ?", (project_id, borrowed.source_id)
            )

    locate(project.id)
    try:
        return save_claim(store, project, title, borrowed.text, (borrowed, *local))
    finally:
        locate(original)


def exposed(desk, passage_id):
    try:
        page = desk.page("desk", "minutes")
    except NotFound:
        page = None
    out = io.BytesIO()
    desk.export(out)
    with ZipFile(out) as archive:
        exported = archive.read("projects/desk/pages.jsonl").decode()
    views = {
        "related": json.dumps(desk.related("desk", "quill")["related"]),
        "page": json.dumps(page),
        "search": json.dumps(desk.recall("vermilion", project="desk")["hits"]),
        "fresh export": exported,
    }
    return {name: (SENTENCE in text, passage_id in text) for name, text in views.items()}


def test_forget_removes_the_sentence_and_passage_id_from_all_four_readers(desk):
    source = desk.store.create_project("source")
    recipient = desk.store.create_project("desk")
    gone = file_note(desk, source, "Dispatch", SENTENCE)
    file_note(desk, recipient, "Quill", "The ledger opens at noon.")
    legacy_page(desk.store, recipient, "Minutes", gone)
    before = exposed(desk, gone.id)
    assert before == dict.fromkeys(before, (True, True))

    desk.forget(source.name, gone.source_id)
    assert desk.store.list_notes(source.id) == []
    assert exposed(desk, gone.id) == dict.fromkeys(before, (False, False))


def test_both_other_projects_lose_their_page_and_the_audit_counts_each(desk):
    source = desk.store.create_project("source")
    others = [desk.store.create_project(name) for name in ("east", "west")]
    gone = file_note(desk, source, "Dispatch", SENTENCE)
    for project in others:  # A slug identifies a page only within its own project.
        legacy_page(desk.store, project, "Minutes", gone)
    desk.forget(source.name, gone.source_id)
    (event,) = [e for e in desk.store.list_audit() if e.action == "note.forgotten"]
    remaining = [p.name for p in others if desk.store.get_page(p.id, "minutes") is not None]
    assert (remaining, event.detail["pages"]) == ([], 3)


@pytest.mark.parametrize("history_only", [False, True], ids=["current-fold", "older-version-only"])
def test_a_fold_continues_in_the_local_spelling_and_loses_all_earlier_versions(desk, history_only):
    source = desk.store.create_project("source")
    recipient = desk.store.create_project("desk")
    gone = file_note(desk, source, "Dispatch", "Quill paused.   \nThen left.")
    local = file_note(desk, recipient, "Minutes", "Quill paused.\nThen left.")
    assert gone.text != local.text
    latest = legacy_page(desk.store, recipient, "Minutes", gone, local)
    if history_only:
        latest = save_claim(desk.store, recipient, "Minutes", local.text, [local])

    desk.forget(source.name, gone.source_id)
    page = desk.page(recipient.name, "minutes")
    (claim,) = page["claims"]
    assert (claim["text"], claim["passage_ids"]) == (local.text, [local.id])
    assert local.text in page["body"] and gone.text not in page["body"]
    assert (page["version"], page["versions"]) == (latest.version + 1, [latest.version + 1])
    (event,) = [e for e in desk.store.list_audit() if e.action == "note.forgotten"]
    assert event.detail["pages"] == 2


def test_a_private_page_is_erased_without_naming_it_to_the_forgetter(desk):
    def sign_in(name):
        signed = desk.store.sign_in(
            "sso", name, f"{name}@example.test", name, issuer="test-issuer", join="default"
        )
        return Memory(desk.store.for_account(signed.account_id, signed.user.id), lint_gate)

    owner, maker = sign_in("owner"), sign_in("maker")
    hidden = owner.store.create_project("closed-room", everyone="none")
    source = maker.store.create_project("source")
    gone = file_note(maker, source, "Dispatch", SENTENCE)
    legacy_page(owner.store, hidden, "Embargo", gone)
    with pytest.raises(NotFound):
        maker.page(hidden.name, "embargo")

    answer = maker.forget(source.name, gone.source_id)
    assert answer["note_id"] == gone.source_id and "dispatch" in answer["removed"]
    assert all(
        value not in json.dumps(answer).lower() for value in (hidden.name, hidden.id, "embargo")
    )
    assert owner.store.get_page(hidden.id, "embargo") is None


@pytest.mark.parametrize("with_local", [False, True], ids=["sole-citation", "after-local-citation"])
def test_a_new_save_refuses_a_foreign_passage_in_one_sentence(desk, with_local):
    source = desk.store.create_project("source")
    recipient = desk.store.create_project("desk")
    foreign = file_note(desk, source, "Dispatch", SENTENCE)
    local = file_note(desk, recipient, "Minutes", "The ledger opens at noon.")
    passages = [local, foreign] if with_local else [foreign]
    title = "Minutes" if with_local else "Borrowed"
    before = desk.store.get_page(recipient.id, title.lower())
    with pytest.raises(Exception, match=r"(?i)\bprojects?\b") as refused:
        save_claim(desk.store, recipient, title, SENTENCE, passages)
    assert len(re.split(r"(?<=[.!?])\s+", str(refused.value).strip())) == 1
    assert desk.store.get_page(recipient.id, title.lower()) == before
    assert desk.recall("vermilion", project=recipient.name)["hits"] == []
