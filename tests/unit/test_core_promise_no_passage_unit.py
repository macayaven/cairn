"""A note with nothing to compile is refused, not stored (review 7.6).

A note of headings only, front matter only, or both with blank lines, holds no passage: it is
refused before anything is stored, over REST, MCP and the web form alike, with one sentence
that says body text is needed under a heading. It takes no room in the plan's counts and
binds no topic. A note with one passage is unaffected; an archive that holds such a note
still imports. Made-up notes."""

import io

import pytest
from starlette.testclient import TestClient

from cairn.gate import lint_gate
from cairn.memory import Malformed, Memory, NoPassage
from cairn.server import create_app
from cairn.store import Store
from cairn.topics import topic_key

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
HEADINGS = "# Deploy Checklist\n\n## Never skip canary"
FRONT_MATTER = "---\ntitle: Release\ndate: 2026-03-14\n---\n"
NOTHING = [
    HEADINGS,
    FRONT_MATTER,
    f"{FRONT_MATTER}\n# Release\n\n\n## Steps\n\n",
    "#   Only a heading   \n\n\n",
]
SAID = "the note holds no body text, only headings or front matter: write the text under a heading"


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


@pytest.fixture
def memory(store):
    store.create_project("alpha")
    return Memory(store, lint_gate)


@pytest.mark.parametrize("text", NOTHING)
def test_a_note_with_no_passage_is_refused_and_nothing_is_stored(memory, store, text):
    before = store.usage()
    with pytest.raises(NoPassage) as refused:
        memory.note("alpha", text, title="Checklist", topic="Checklist")
    assert isinstance(refused.value, Malformed) and refused.value.status == 400
    assert str(refused.value) == SAID
    assert "Deploy" not in str(refused.value) and "canary" not in str(refused.value)
    project = store.get_project("alpha")
    assert store.list_notes(project.id) == []
    assert store.usage() == before and store.stored_bytes() == 0
    assert store.topic_page(project.id, topic_key("Checklist")) is None


def test_the_refused_note_binds_no_topic(memory, store):
    with pytest.raises(NoPassage):
        memory.note("alpha", HEADINGS, title="Checklist", topic="Deploy")
    written = memory.note("alpha", f"{HEADINGS}\n\nAlways watch the canary.\n", topic="Deploy")
    assert (written["status"], written["page"], written["version"]) == ("saved", "deploy", 1)
    assert len(store.list_notes(store.get_project("alpha").id)) == 1


@pytest.mark.parametrize("text", ["", "\n\n  \n"])
def test_blank_text_is_refused_as_before_and_nothing_is_stored(memory, store, text):
    with pytest.raises(Malformed):
        memory.note("alpha", text, title="Checklist")
    assert store.list_notes(store.get_project("alpha").id) == []


def test_a_note_with_one_passage_under_headings_is_unaffected(memory):
    answer = memory.note("alpha", f"{HEADINGS}\n\nWatch the canary for ten minutes.\n")
    assert answer["status"] == "saved" and answer["created"] is True


def test_rest_refuses_it_with_the_sentence(store):
    key = store.create_key("laptop")
    store.create_project("alpha")
    with TestClient(create_app(store, gate=lint_gate, public_url=BASE), base_url=BASE) as client:
        r = client.post(
            "/api/projects/alpha/notes", headers={"x-api-key": key}, json={"text": HEADINGS}
        )
    assert r.status_code == 400 and SAID in r.text
    assert store.list_notes(store.get_project("alpha").id) == []


def test_mcp_refuses_it_with_the_sentence(store):
    key = store.create_key("laptop")
    store.create_project("alpha")
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "note", "arguments": {"project": "alpha", "text": FRONT_MATTER}},
    }
    with TestClient(create_app(store, gate=lint_gate, public_url=BASE), base_url=BASE) as client:
        r = client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": key}, json=body)
    result = r.json()["result"]
    assert result["isError"] is True and SAID in result["content"][0]["text"]
    assert store.list_notes(store.get_project("alpha").id) == []


def test_the_web_form_refuses_it_with_the_sentence(store):
    key = store.create_key("laptop")
    store.create_project("alpha")
    with TestClient(create_app(store, gate=lint_gate, public_url=BASE), base_url=BASE) as client:
        assert client.post("/login", data={"key": key}, headers=SAME_ORIGIN).status_code < 400
        r = client.post(
            "/b/alpha/notes", data={"text": HEADINGS}, headers=SAME_ORIGIN, follow_redirects=False
        )
    assert r.status_code == 400
    assert SAID[:1].upper() + SAID[1:] + "." in r.text
    assert store.list_notes(store.get_project("alpha").id) == []


def test_an_archive_that_holds_such_a_note_still_imports(store, tmp_path):
    source = Store(tmp_path / "before.db")
    try:
        project = source.create_project("alpha")
        source.add_note(project.id, HEADINGS, title="Checklist")  # stored before this change
        archive = io.BytesIO()
        source.export(archive)
    finally:
        source.close()
    archive.seek(0)
    store.import_export(archive)
    (note,) = store.list_notes(store.get_project("alpha").id)
    assert note.text == HEADINGS


# Decision 173: a note stored before the refusal keeps its answer when it is sent again


def test_such_a_note_stored_before_is_unchanged_when_sent_again(memory, store):
    project = store.get_project("alpha")
    held, _ = store.add_note(project.id, HEADINGS, title="Checklist")  # stored before this change
    again = memory.note("alpha", HEADINGS, title="Checklist")
    assert (again["note_id"], again["created"], again["status"]) == (held.id, False, "unchanged")
    assert len(store.list_notes(project.id)) == 1


def test_rest_answers_unchanged_for_it_and_still_refuses_another_source(store):
    key = store.create_key("laptop")
    project = store.create_project("alpha")
    held, _ = store.add_note(project.id, HEADINGS, title="Checklist", source_ref="old/1")
    with TestClient(create_app(store, gate=lint_gate, public_url=BASE), base_url=BASE) as client:
        same = client.post(
            "/api/projects/alpha/notes",
            headers={"x-api-key": key},
            json={"text": HEADINGS, "title": "Checklist", "source_ref": "old/1"},
        )
        other = client.post(
            "/api/projects/alpha/notes",
            headers={"x-api-key": key},
            json={"text": HEADINGS, "title": "Checklist", "source_ref": "old/2"},
        )
    assert same.status_code < 400
    assert (same.json()["note_id"], same.json()["status"]) == (held.id, "unchanged")
    assert other.status_code == 400 and SAID in other.text
    assert [note.id for note in store.list_notes(project.id)] == [held.id]
