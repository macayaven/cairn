"""Unit tests for the bounds on a note's metadata (H6 fix 1a): the store refuses a title,
topic, label, source_ref or via past its bound for every caller, and each way in says
which field."""

import io
import json
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import FIELD_MAX, FieldTooLong, Store

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
NOTE = "# Fact\n\nQuill keeps one fact.\n"


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


def test_the_bounds_are_the_ones_promised():
    assert FIELD_MAX == {"title": 200, "topic": 200, "label": 80, "source_ref": 500, "via": 40}


@pytest.mark.parametrize("field", sorted(FIELD_MAX))
def test_a_field_at_its_bound_is_stored_and_one_past_it_is_refused_by_name(store, db, field):
    project = store.create_project("ops")
    bound = FIELD_MAX[field]
    _, created = store.add_note(project.id, NOTE, **{field: "a" * bound})
    assert created
    before = dump(db)
    with pytest.raises(FieldTooLong, match=field) as refused:
        store.add_note(project.id, NOTE + "more\n", **{field: "b" * (bound + 1)})
    assert isinstance(refused.value, ValueError)
    assert (refused.value.field, refused.value.bound) == (field, bound)
    assert dump(db) == before  # nothing stored, nothing metered, nothing audited


def test_characters_are_counted_not_bytes(store):
    project = store.create_project("ops")
    _, created = store.add_note(project.id, NOTE, via="é" * 40)
    assert created


def test_an_archive_from_before_the_bounds_imports_with_the_field_cut(store, tmp_path):
    """An export written before the bounds may carry a longer field: the import cuts it to
    its bound (never the note's text) and says how many it cut."""
    source = Store(tmp_path / "source.db")
    project = source.create_project("ops")
    source.add_note(project.id, NOTE, topic="Retry policy")
    archive = io.BytesIO()
    source.export(archive)
    source.close()
    rebuilt = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(archive.getvalue())) as old,
        zipfile.ZipFile(rebuilt, "w") as new,
    ):
        for item in old.infolist():
            data = old.read(item.filename)
            if item.filename.endswith("notes.jsonl"):
                line = json.loads(data)
                line["topic"] = "t" * 201
                data = (json.dumps(line) + "\n").encode()
            new.writestr(item, data)
    target = store.for_account(store.create_account("Brook").id)
    summary = target.import_export(io.BytesIO(rebuilt.getvalue()))
    assert (summary.notes, summary.fields_cut) == (1, 1)
    (project,) = target.list_projects()
    (note,) = target.list_notes(project.id)
    assert (note.topic, note.text) == ("t" * 200, NOTE)


# Each way in


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "cairn.db"
    store = Store(db)
    key = store.create_key("laptop")
    project = store.create_project("ops")
    app = create_app(store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:
        yield SimpleNamespace(client=client, store=store, db=db, key=key, project=project)
    store.close()


def test_rest_answers_400_naming_the_field(env):
    r = env.client.post(
        f"/api/projects/{env.project.id}/notes",
        headers={"x-api-key": env.key},
        json={"text": NOTE, "label": "l" * 81},
    )
    assert r.status_code == 400 and "label" in r.json()["error"] and "80" in r.json()["error"]
    assert env.store.list_notes(env.project.id) == []


def test_the_note_tool_fails_naming_the_field(env):
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "note",
            "arguments": {"project": "ops", "text": NOTE, "title": "t" * 201},
        },
    }
    r = env.client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": env.key}, json=body)
    result = r.json()["result"]
    assert result["isError"] is True and "title" in result["content"][0]["text"]
    assert env.store.list_notes(env.project.id) == []


def test_the_composer_shows_the_form_again_with_what_was_typed(env):
    env.client.post("/login", data={"key": env.key}, headers=SAME_ORIGIN)
    typed = "A thought typed on a phone."
    page = env.client.post(
        "/b/ops/notes",
        data={"text": typed, "source_ref": "r" * 501},
        headers=SAME_ORIGIN,
        follow_redirects=False,
    )
    assert page.status_code == 400 and 'role="alert"' in page.text
    assert typed in page.text and "500" in page.text
    assert env.store.list_notes(env.project.id) == []
