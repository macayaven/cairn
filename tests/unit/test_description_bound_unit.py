"""A project's description is bounded like a note's fields, and an import's times are times.

Both were lengths nothing counted: a description could hold megabytes that no cap sees, and
an archive's timestamp could carry a fraction of a hundred thousand digits that the store
kept as written.
"""

import io
import json
import zipfile

import pytest
from starlette.testclient import TestClient

from cairn.compiler import compile_note
from cairn.server import create_app
from cairn.store import DESCRIPTION_MAX, DescriptionTooLong, Store

BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def test_a_description_past_its_bound_is_refused_through_every_door(store):
    assert DESCRIPTION_MAX == 500
    assert store.create_project("fits", "d" * DESCRIPTION_MAX).description == "d" * DESCRIPTION_MAX
    with pytest.raises(DescriptionTooLong) as refused:
        store.create_project("long", "d" * (DESCRIPTION_MAX + 1))
    assert isinstance(refused.value, ValueError) and "ddd" not in str(refused.value)
    assert store.get_project("long") is None

    key = {"x-api-key": store.create_key("laptop")}
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        over = {"name": "long", "description": "d" * (DESCRIPTION_MAX + 1)}
        answer = client.post("/api/projects", headers=key, json=over)
        assert answer.status_code == 400
        assert "description" in answer.json()["error"] and "500" in answer.json()["error"]
        call = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "new_project", "arguments": over},
        }
        tool = client.post("/mcp", headers={**MCP_HEADERS, **key}, json=call).json()["result"]
        assert tool["isError"] is True and "description" in tool["content"][0]["text"]
    assert store.get_project("long") is None


def archive_of(store: Store) -> bytes:
    project = store.create_project("ops", "operations")
    note, _ = store.add_note(project.id, "# Runbook\n\nHeron restarts after 3 failures.\n")
    assert compile_note(store, note).status == "saved"
    out = io.BytesIO()
    store.export(out)
    return out.getvalue()


def changed(archive: bytes, edit) -> bytes:
    source, out = zipfile.ZipFile(io.BytesIO(archive)), io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for name in source.namelist():
            target.writestr(name, edit(name, source.read(name)))
    return out.getvalue()


def test_an_import_cuts_a_long_description_and_counts_it(store, tmp_path):
    def long_description(name, data):
        if name != "export.json":
            return data
        manifest = json.loads(data)
        manifest["projects"][0]["description"] = "d" * 100_000
        return json.dumps(manifest).encode()

    target = Store(tmp_path / "target.db")
    try:
        summary = target.import_export(io.BytesIO(changed(archive_of(store), long_description)))
        assert target.get_project("ops").description == "d" * DESCRIPTION_MAX
        assert summary.fields_cut == 1
    finally:
        target.close()


@pytest.mark.parametrize("where", ["export.json", "notes.jsonl", "pages.jsonl"])
def test_an_import_refuses_a_time_that_is_longer_than_a_time(store, tmp_path, where):
    long_time = "2026-11-10T00:00:00." + "1" * 100_000 + "+00:00"

    def long_times(name, data):
        if not name.endswith(where):
            return data
        if name == "export.json":
            manifest = json.loads(data)
            manifest["projects"][0]["created_at"] = long_time
            return json.dumps(manifest).encode()
        lines = [json.loads(line) for line in data.decode().splitlines() if line]
        for line in lines:
            line["created_at"] = long_time
        return "".join(json.dumps(line) + "\n" for line in lines).encode()

    target = Store(tmp_path / "target.db")
    try:
        with pytest.raises(ValueError, match="ISO 8601 time"):
            target.import_export(io.BytesIO(changed(archive_of(store), long_times)))
        assert target.list_projects() == []
    finally:
        target.close()
