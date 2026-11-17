"""Sentences of the guides about X1 (tests/acceptance/test_x1_unknown_fields.py), pinned here
so the guides cannot go false unnoticed: what the connect guide says of a key, a query
parameter or a tool argument cairn does not know. The test finds the sentences in the guide
as a person reads it (guide_source: the product's name filled in), then asks cairn over REST
and over MCP. Everything runs over a real store in a temporary file; every name is made up."""

import sqlite3
from pathlib import Path

from guide_source import guide_source
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store

BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


def flat(text: str) -> str:
    return " ".join(text.split())


def says(guide: str, *sentences: str) -> None:
    """Each sentence is in the guide, as a person reads it."""
    text = flat(guide_source(guide))
    for sentence in sentences:
        assert flat(sentence) in text, sentence


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def test_a_key_a_parameter_or_an_argument_cairn_does_not_know_is_refused(tmp_path):
    says(
        "connect",
        "cairn never drops a field it does not know. A body key or a query parameter that a"
        " REST address does not take is refused with 400, and an argument that a tool does not"
        " have is refused as a tool error; the refusal names it and lists every key, parameter"
        " or argument the address or the tool takes.",
        'Each tool\'s input schema says so in advance, with `"additionalProperties": false`.',
    )
    db = tmp_path / "cairn.db"
    store = Store(db)
    key = store.create_key("desk")
    h = {"x-api-key": key}
    try:
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            assert client.post("/api/projects", headers=h, json={"name": "ops"}).is_success
            before = dump(db)
            r = client.post(
                "/api/projects/ops/notes", headers=h, json={"text": "Mote.", "sorce_ref": "d:1"}
            )
            assert (r.status_code, r.json()) == (
                400,
                {
                    "error": "unknown key 'sorce_ref'; this address takes 'text', 'title',"
                    " 'topic', 'label' and 'source_ref'"
                },
            )
            r = client.get("/api/projects/ops/pages", headers=h, params={"limt": "5"})
            assert (r.status_code, r.json()) == (
                400,
                {"error": "unknown query parameter 'limt'; this address takes 'limit' and 'after'"},
            )

            def rpc(method: str, params: dict) -> dict:
                body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
                answer = client.post("/mcp", headers={**MCP_HEADERS, **h}, json=body)
                return answer.json()["result"]

            called = rpc("tools/call", {"name": "overview", "arguments": {"projct": "ops"}})
            assert called["isError"] is True
            assert called["content"][0]["text"].endswith(
                "unknown argument 'projct'; overview takes 'project', 'limit' and 'after'"
            )
            for tool in rpc("tools/list", {})["tools"]:
                assert tool["inputSchema"]["additionalProperties"] is False, tool["name"]
            assert dump(db) == before
    finally:
        store.close()
