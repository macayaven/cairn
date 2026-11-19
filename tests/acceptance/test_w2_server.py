"""W2 acceptance tests (frozen): the cairn web service (auth, MCP, REST, web UI).

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract is in the docstring of src/cairn/server.py.
"""

import html
import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store
from cookie_names import session_cookie

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RUNBOOK = (FIXTURES / "runbook_synthetic.md").read_text(encoding="utf-8")
FOLLOWUP = "# Quill and Lumen runbook\n\nQuill retries 3 times before failing.\n"
SLUG = "quill-and-lumen-runbook"
BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
TOOLS = {"projects", "new_project", "note", "recall", "page", "evidence", "overview", "graph"}


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "cairn.db"
    store = Store(db)
    key = store.create_key("test")
    app = create_app(store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:
        yield SimpleNamespace(client=client, key=key, store=store, db=db)
    store.close()


def rpc(env, method: str, params: dict | None = None, headers: dict | None = None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    hdrs = {**MCP_HEADERS, "x-api-key": env.key, **(headers or {})}
    return env.client.post("/mcp", headers=hdrs, json=body)


def tool(env, tool_name: str, /, **arguments) -> dict:
    r = rpc(env, "tools/call", {"name": tool_name, "arguments": arguments})
    assert r.status_code == 200, r.text
    return r.json()["result"]


def call(env, tool_name: str, /, **arguments) -> dict:
    result = tool(env, tool_name, **arguments)
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def login(env) -> None:
    r = env.client.post("/login", data={"key": env.key}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/")


def seeded(env) -> dict:
    call(env, "new_project", name="alpha", description="first")
    call(env, "new_project", name="beta")
    return call(env, "note", project="alpha", text=RUNBOOK, source_ref="test:runbook")


# Auth


def test_api_needs_a_valid_key(env):
    c = env.client
    assert c.get("/api/projects").status_code == 401
    assert c.get("/api/projects", headers={"x-api-key": env.key + "x"}).status_code == 401
    assert c.get("/api/projects", headers={"x-api-key": env.key}).status_code == 200
    assert c.get("/api/projects", headers={"authorization": f"Bearer {env.key}"}).status_code == 200
    assert c.get("/healthz").status_code == 200


def test_mcp_needs_a_valid_key(env):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    assert env.client.post("/mcp", headers=MCP_HEADERS, json=body).status_code == 401
    bad = {**MCP_HEADERS, "x-api-key": "nope"}
    assert env.client.post("/mcp", headers=bad, json=body).status_code == 401
    assert rpc(env, "tools/list").status_code == 200


def test_revoked_key_stops_working(env):
    env.store.revoke_key("test")
    assert env.client.get("/api/projects", headers={"x-api-key": env.key}).status_code == 401
    assert rpc(env, "tools/list").status_code == 401


def test_mcp_rejects_a_foreign_host_header(env):
    assert rpc(env, "tools/list", headers={"host": "evil.example"}).status_code == 421


def test_ui_needs_a_session_and_login_sets_one(env):
    c = env.client
    for path in ("/", "/b/alpha", "/setup", "/search?q=x"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].endswith("/login"), path
    assert c.get("/login").status_code == 200
    assert c.post("/login", data={"key": "wrong"}, follow_redirects=False).status_code == 401
    r = c.post("/login", data={"key": env.key}, follow_redirects=False)
    assert r.status_code == 303
    cookie = r.headers["set-cookie"].lower()
    assert f"{session_cookie()}=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert c.get("/").status_code == 200
    c.post("/logout", follow_redirects=False)
    assert c.get("/", follow_redirects=False).status_code == 303


# MCP


def test_initialize_returns_usage_instructions(env):
    r = rpc(
        env,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"},
        },
    )
    instructions = r.json()["result"]["instructions"]
    assert "recall" in instructions and "note" in instructions


def test_tools_are_cairns_own(env):
    names = {t["name"] for t in rpc(env, "tools/list").json()["result"]["tools"]}
    assert names == TOOLS


def test_end_to_end_across_two_projects(env):
    created = seeded(env)
    assert created["created"] is True
    assert (created["status"], created["page"], created["version"]) == ("saved", SLUG, 1)
    assert created["violations"] == []
    again = call(env, "note", project="alpha", text=RUNBOOK, source_ref="test:runbook")
    assert (again["created"], again["note_id"]) == (False, created["note_id"])
    assert {b["name"]: b["pages"] for b in call(env, "projects")["projects"]} == {
        "alpha": 1,
        "beta": 0,
    }

    update = call(env, "note", project="alpha", text=FOLLOWUP, source_ref="test:followup")
    assert (update["status"], update["page"], update["version"]) == ("saved", SLUG, 2)

    page = call(env, "page", project="alpha", slug=SLUG)
    assert (page["version"], page["versions"]) == (2, [1, 2])
    assert page["claims"][-1]["text"] == "Quill retries 3 times before failing."
    assert all(c["passage_ids"] for c in page["claims"])
    assert page["metrics"]["claims"] == len(page["claims"])
    old = call(env, "page", project="alpha", slug=SLUG, version=1)
    assert len(old["claims"]) == len(page["claims"]) - 1

    hits = call(env, "recall", query="quill run", project="alpha")["hits"]
    assert hits and {h["project"] for h in hits} == {"alpha"} and hits[0]["page"] == SLUG
    assert call(env, "recall", query="quill run", project="beta")["hits"] == []
    assert {h["project"] for h in call(env, "recall", query="retries")["hits"]} == {"alpha"}

    claim = next(c for c in page["claims"] if "--set cache.enabled=false" in c["text"])
    traced = call(env, "evidence", project="alpha", slug=SLUG, claim_id=claim["id"])
    (span,) = traced["passages"]
    lines = RUNBOOK.splitlines()[span["start_line"] - 1 : span["end_line"]]
    assert span["text"] == "\n".join(lines) == claim["text"]
    assert span["note_id"] == created["note_id"]
    assert span["note_title"] == "Quill and Lumen runbook"

    assert call(env, "overview", project="beta")["pages"] == []
    overview = call(env, "overview", project="alpha")
    assert overview["notes"] == 2 and [p["slug"] for p in overview["pages"]] == [SLUG]


def test_unknown_things_are_tool_errors(env):
    seeded(env)
    assert tool(env, "note", project="gamma", text="x")["isError"] is True
    assert tool(env, "page", project="alpha", slug="nope")["isError"] is True
    assert tool(env, "page", project="beta", slug=SLUG)["isError"] is True  # projects are isolated
    assert tool(env, "evidence", project="alpha", slug=SLUG, claim_id="cl_nope")["isError"] is True
    assert tool(env, "new_project", name="ALPHA")["isError"] is True


# REST


def test_rest_api_mirrors_the_tools(env):
    c, h = env.client, {"x-api-key": env.key}
    assert c.post("/api/projects", headers=h, json={"name": "alpha"}).status_code == 201
    assert c.post("/api/projects", headers=h, json={"name": "alpha"}).status_code == 409
    assert c.post("/api/projects", headers=h, json={"name": "bad name"}).status_code == 400
    r = c.post("/api/projects/alpha/notes", headers=h, json={"text": RUNBOOK})
    assert r.status_code == 201 and r.json()["page"] == SLUG
    page = c.get(f"/api/projects/alpha/pages/{SLUG}", headers=h).json()
    assert page["version"] == 1 and page["claims"]
    claim = page["claims"][0]
    trace = c.get(f"/api/projects/alpha/pages/{SLUG}/claims/{claim['id']}/evidence", headers=h)
    assert trace.json()["passages"][0]["text"] == claim["text"]
    assert [p["slug"] for p in c.get("/api/projects/alpha/pages", headers=h).json()["pages"]] == [
        SLUG
    ]
    hits = c.get("/api/search", headers=h, params={"q": "lumen", "project": "alpha"}).json()["hits"]
    assert hits and hits[0]["page"] == SLUG
    assert c.get("/api/projects/zzz/pages", headers=h).status_code == 404
    assert c.get("/api/projects/alpha/pages/nope", headers=h).status_code == 404


# Web UI


def test_page_view_links_every_claim_to_its_source_spans(env):
    seeded(env)
    login(env)
    r = env.client.get(f"/b/alpha/p/{SLUG}")
    assert r.status_code == 200
    page = call(env, "page", project="alpha", slug=SLUG)
    for claim in page["claims"]:
        assert f'id="{claim["id"]}"' in r.text
        for span_id in claim["passage_ids"]:
            assert re.search(rf'href="[^"]*#{span_id}"', r.text), span_id
            assert f'id="{span_id}"' in r.text
    first_line = page["claims"][0]["text"].splitlines()[0]
    assert html.escape(first_line, quote=False) in r.text
    for name in page["metrics"]:
        assert name in r.text


def test_ui_lists_projects_pages_and_search_hits(env):
    seeded(env)
    login(env)
    c = env.client
    assert "alpha" in c.get("/").text and "beta" in c.get("/").text
    assert f"/b/alpha/p/{SLUG}" in c.get("/b/alpha").text
    assert f"/b/alpha/p/{SLUG}" in c.get("/search", params={"q": "lumen"}).text
    assert c.get("/b/zzz").status_code == 404


def test_setup_page_explains_integration_without_secrets(env):
    login(env)
    text = env.client.get("/setup").text
    assert f"{BASE}/mcp" in text
    assert "cairn.env" in text and 'href="/docs/connect"' in text
    assert "client-mcp-manager" not in text and "agent-vault" not in text
    assert env.key not in text


def test_reads_never_write(env):
    seeded(env)
    login(env)
    before = dump(env.db)
    h = {"x-api-key": env.key}
    page = call(env, "page", project="alpha", slug=SLUG)
    claim_id = page["claims"][0]["id"]
    for path in (
        "/api/projects",
        "/api/projects/alpha/pages",
        f"/api/projects/alpha/pages/{SLUG}",
        f"/api/projects/alpha/pages/{SLUG}/claims/{claim_id}/evidence",
        "/api/search?q=quill",
    ):
        assert env.client.get(path, headers=h).status_code == 200, path
    for path in ("/", "/b/alpha", f"/b/alpha/p/{SLUG}", "/search?q=quill", "/setup"):
        assert env.client.get(path).status_code == 200, path
    call(env, "projects")
    call(env, "recall", query="quill")
    call(env, "overview", project="alpha")
    call(env, "evidence", project="alpha", slug=SLUG, claim_id=claim_id)
    assert dump(env.db) == before
