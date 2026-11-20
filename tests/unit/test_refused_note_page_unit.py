"""A note whose page the gate refuses, sent to a topic that has a page already, does not name
that page in its first answer (second report, finding 8, the second row, outside Slack).

The answer names a page by the Notes page's rule (Store.note_page): only one whose latest
version cites the note, as the answer to a note sent again does (FiledOnce). Over MCP and
REST alike. The app runs in process over a real store in a temporary file. Made-up notes."""

import json
from datetime import UTC, datetime

import pytest
from starlette.testclient import TestClient

from cairn.candidate import GateResult
from cairn.compiler import provenance_gate
from cairn.memory import NO_PAGE, Memory
from cairn.server import create_app
from cairn.store import Store

BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
WRITTEN = datetime(2099, 2, 3, 4, 5, tzinfo=UTC)
FIRST, SECOND = "- Quill retries 3 times.\n", "- Brook keeps the relay.\n"


def refusing_after(first: int):
    """A gate that passes its first `first` pages and refuses every one after."""
    calls = [0]

    def gate(page, spans):
        calls[0] += 1
        if calls[0] > first:
            return GateResult(passed=False, violations=["made.up: -: refused"], metrics={})
        return provenance_gate(page, spans)

    return gate


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db", clock=lambda: WRITTEN)
    store.create_project("ops")
    yield store
    store.close()


def by_mcp(client, key: str, **arguments) -> dict:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "note", "arguments": arguments},
    }
    r = client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": key}, json=body)
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["isError"] is False
    return json.loads(result["content"][0]["text"])


def by_rest(client, key: str, **arguments) -> dict:
    project = arguments.pop("project")
    r = client.post(f"/api/projects/{project}/notes", headers={"x-api-key": key}, json=arguments)
    assert r.status_code == 201
    return r.json()


@pytest.mark.parametrize("send", [by_mcp, by_rest], ids=["mcp", "rest"])
def test_a_refused_note_to_a_topic_with_a_page_names_no_page(store, send):
    key = store.create_key("owner")
    app = create_app(store, public_url=BASE, gate=refusing_after(1))
    with TestClient(app, base_url=BASE) as client:
        made = send(client, key, project="ops", text=FIRST, topic="Retry policy")
        assert (made["status"], made["page"]) == ("saved", "retry-policy")
        refused = send(client, key, project="ops", text=SECOND, topic="Retry policy")
    assert refused["status"] == "rejected"
    assert refused["page"] is None and refused["message"] == NO_PAGE
    assert refused["version"] is None


def test_memory_names_the_page_only_when_its_latest_version_cites_the_note(store):
    memory = Memory(store, refusing_after(1))
    assert memory.note("ops", FIRST, topic="Retry policy")["page"] == "retry-policy"
    refused = memory.note("ops", SECOND, topic="Retry policy")
    assert (refused["status"], refused["page"], refused["version"]) == ("rejected", None, None)
    again = memory.note("ops", FIRST, topic="Retry policy")  # a note its page cites
    assert (again["status"], again["page"]) == ("unchanged", "retry-policy")
