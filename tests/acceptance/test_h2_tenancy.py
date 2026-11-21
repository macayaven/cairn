"""H2 acceptance tests (frozen): tenancy in the web service.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstring of src/cairn/server.py; this is what it must add.

- A request acts for exactly one account: the account of the API key it presents
  (`Store.resolve_key`), or of the session cookie it carries (`Store.resolve_session`). A
  session belongs to the account of the key that was used to log in.
- REST, the MCP tools and the web UI only ever read and write that account's data. Another
  account's project, page or claim, named by id or by name, is answered exactly like one that
  does not exist: 404 over REST and in the UI, a tool error over MCP, with the same message
  an unknown id gets.
- Project names are per account: two accounts can each have a project named "core".
- `recall` and search without a project cover every project of the caller's account, and nothing
  else.
- This holds when requests from different accounts run at the same time.
- With only the default account, everything behaves as the v0 contract
  (tests/acceptance/test_w2_server.py) says, unedited.
- GET requests and read-only tools still never write.
"""

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store
from cookie_names import session_cookie

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RUNBOOK = (FIXTURES / "runbook_synthetic.md").read_text(encoding="utf-8")
SLUG = "quill-and-lumen-runbook"
KESTREL = "# Kestrel notes\n\nKestrel caches quill results for 90 seconds.\n"
KESTREL_SLUG = "kestrel-notes"
BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def tenant(store: Store, name: str) -> SimpleNamespace:
    view = store.for_account(store.create_account(name).id)
    return SimpleNamespace(name=name, view=view, key=view.create_key("laptop"))


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "cairn.db"
    store = Store(db)
    ada, brook = tenant(store, "Ada"), tenant(store, "Brook")
    app = create_app(store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:
        yield SimpleNamespace(client=client, store=store, db=db, app=app, ada=ada, brook=brook)
    store.close()


def api(env, who, method: str, path: str, **kwargs):
    return env.client.request(method, path, headers={"x-api-key": who.key}, **kwargs)


def tool(env, who, name: str, /, **arguments) -> dict:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    r = env.client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": who.key}, json=body)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def call(env, who, name: str, /, **arguments) -> dict:
    result = tool(env, who, name, **arguments)
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def refused(env, who, name: str, /, **arguments) -> str:
    result = tool(env, who, name, **arguments)
    assert result["isError"] is True, result
    return result["content"][0]["text"]


def signed_in_headers(env, who) -> dict:
    """Log in with the tenant's key and return the Cookie header for its session."""
    env.client.cookies.clear()
    r = env.client.post("/login", data={"key": who.key}, follow_redirects=False)
    assert r.status_code == 303, r.text
    token = r.cookies[session_cookie()]
    env.client.cookies.clear()
    return {"cookie": f"{session_cookie()}={token}"}


@pytest.fixture
def seeded(env):
    """Ada and Brook each have a project named "core" with one compiled note."""
    ids = {}
    for who, text in ((env.ada, RUNBOOK), (env.brook, KESTREL)):
        ids[who.name] = call(env, who, "new_project", name="core", description=who.name)["id"]
        noted = call(env, who, "note", project="core", text=text, source_ref=f"test:{who.name}")
        assert noted["status"] == "saved"
    return SimpleNamespace(ada=ids["Ada"], brook=ids["Brook"])


def test_two_accounts_can_use_the_same_project_name(env, seeded):
    assert seeded.ada != seeded.brook
    for who, own in ((env.ada, seeded.ada), (env.brook, seeded.brook)):
        projects = call(env, who, "projects")["projects"]
        assert [(b["id"], b["name"], b["description"]) for b in projects] == [
            (own, "core", who.name)
        ]
        listed = api(env, who, "GET", "/api/projects").json()["projects"]
        assert [b["id"] for b in listed] == [own]
    # Within one account the name is still taken.
    assert api(env, env.ada, "POST", "/api/projects", json={"name": "Core"}).status_code == 409
    assert "already exists" in refused(env, env.ada, "new_project", name="CORE")


def test_rest_answers_another_accounts_ids_like_unknown_ones(env, seeded):
    ghost = "br_000000000000"
    before = dump(env.db)
    for path in (
        "/api/projects/{}/pages",
        "/api/projects/{}/pages/" + SLUG,
        "/api/search?q=quill&project={}",
    ):
        foreign = api(env, env.brook, "GET", path.format(seeded.ada))
        unknown = api(env, env.brook, "GET", path.format(ghost))
        assert foreign.status_code == unknown.status_code == 404, path
        assert foreign.json()["error"] == unknown.json()["error"].replace(ghost, seeded.ada)
    claim = api(env, env.ada, "GET", f"/api/projects/core/pages/{SLUG}").json()["claims"][0]["id"]
    trace = f"/api/projects/{seeded.ada}/pages/{SLUG}/claims/{claim}/evidence"
    assert api(env, env.brook, "GET", trace).status_code == 404
    assert api(env, env.ada, "GET", trace).status_code == 200
    note = api(env, env.brook, "POST", f"/api/projects/{seeded.ada}/notes", json={"text": "x"})
    assert note.status_code == 404
    assert dump(env.db) == before  # the refused note left nothing behind


def test_mcp_tools_answer_another_accounts_ids_like_unknown_ones(env, seeded):
    ghost = "br_000000000000"
    before = dump(env.db)
    claim = call(env, env.ada, "page", project="core", slug=SLUG)["claims"][0]["id"]
    probes = (
        ("overview", {}),
        ("page", {"slug": SLUG}),
        ("evidence", {"slug": SLUG, "claim_id": claim}),
        ("recall", {"query": "quill"}),
        ("note", {"text": "an intruder's note"}),
    )
    for name, arguments in probes:
        foreign = refused(env, env.brook, name, project=seeded.ada, **arguments)
        unknown = refused(env, env.brook, name, project=ghost, **arguments)
        assert foreign == unknown.replace(ghost, seeded.ada), name
    assert dump(env.db) == before


def test_recall_and_search_cover_only_the_callers_account(env, seeded):
    ada_hits = call(env, env.ada, "recall", query="quill kestrel lumen", limit=50)["hits"]
    brook_hits = call(env, env.brook, "recall", query="quill kestrel lumen", limit=50)["hits"]
    assert ada_hits and brook_hits
    assert all("Kestrel" not in h["text"] for h in ada_hits)
    assert all("Kestrel" in h["text"] for h in brook_hits)
    assert {h["page"] for h in ada_hits} == {SLUG}
    assert {h["page"] for h in brook_hits} == {KESTREL_SLUG}
    assert call(env, env.ada, "recall", query="kestrel")["hits"] == []
    rest = api(env, env.brook, "GET", "/api/search?q=lumen").json()
    assert rest["hits"] == []
    # The same project name resolves to the caller's own project.
    assert call(env, env.brook, "overview", project="core")["pages"][0]["slug"] == KESTREL_SLUG
    assert call(env, env.ada, "overview", project="core")["pages"][0]["slug"] == SLUG


def test_a_ui_session_belongs_to_the_account_of_its_key(env, seeded):
    ada, brook = signed_in_headers(env, env.ada), signed_in_headers(env, env.brook)
    c = env.client
    home_a, home_b = c.get("/", headers=ada), c.get("/", headers=brook)
    assert home_a.status_code == home_b.status_code == 200
    assert f"/b/{seeded.ada}" in home_a.text or "/b/core" in home_a.text
    assert "Ada" in home_a.text and "Brook" not in home_a.text  # the projects' descriptions
    assert "Brook" in home_b.text and "Ada" not in home_b.text

    assert c.get(f"/b/{seeded.ada}", headers=brook).status_code == 404
    assert c.get(f"/b/{seeded.ada}/p/{SLUG}", headers=brook).status_code == 404
    assert c.get(f"/search?q=quill&project={seeded.ada}", headers=brook).status_code == 404
    assert c.get(f"/b/{seeded.ada}", headers=ada).status_code == 200

    # "core" is the caller's own project; search shows only the caller's claims.
    assert "Kestrel" in c.get(f"/b/core/p/{KESTREL_SLUG}", headers=brook).text
    assert c.get(f"/b/core/p/{SLUG}", headers=brook).status_code == 404
    found_b = c.get("/search?q=quill", headers=brook).text
    found_a = c.get("/search?q=quill", headers=ada).text
    assert "Kestrel" in found_b and "Lumen" not in found_b
    assert "Kestrel" not in found_a

    # Logging out ends only that session.
    assert c.post("/logout", headers=brook, follow_redirects=False).status_code == 303
    c.cookies.clear()
    assert c.get("/", headers=brook, follow_redirects=False).status_code == 303
    assert c.get("/", headers=ada).status_code == 200


def test_the_default_account_is_one_more_tenant(env, seeded):
    key = env.store.create_key("default")
    default = SimpleNamespace(name="default", key=key)
    assert call(env, default, "projects")["projects"] == []
    assert call(env, default, "new_project", name="core")["id"] not in (seeded.ada, seeded.brook)
    assert call(env, default, "recall", query="quill")["hits"] == []
    assert api(env, default, "GET", f"/api/projects/{seeded.ada}/pages").status_code == 404


def test_a_revoked_key_stops_only_its_own_account(env, seeded):
    assert env.ada.view.revoke_key("laptop") is True  # Brook's key has the same label
    assert api(env, env.ada, "GET", "/api/projects").status_code == 401
    assert api(env, env.brook, "GET", "/api/projects").status_code == 200


def test_reads_never_write(env, seeded):
    cookies = {who.name: signed_in_headers(env, who) for who in (env.ada, env.brook)}
    before = dump(env.db)
    for who in (env.ada, env.brook):
        call(env, who, "projects")
        call(env, who, "recall", query="quill")
        call(env, who, "overview", project="core")
        api(env, who, "GET", "/api/projects")
        api(env, who, "GET", "/api/search?q=quill")
        api(env, who, "GET", f"/api/projects/{seeded.ada}/pages")  # one of the two is refused
        env.client.get("/", headers=cookies[who.name])
        env.client.get("/search?q=quill", headers=cookies[who.name])
        env.client.get(f"/b/{seeded.brook}", headers=cookies[who.name])
    assert dump(env.db) == before


def test_concurrent_requests_stay_in_their_own_account(tmp_path):
    """Forty interleaved REST and MCP requests from two accounts on one event loop."""
    store = Store(tmp_path / "cairn.db")
    tenants = [tenant(store, "Ada"), tenant(store, "Brook")]
    for who in tenants:
        project = who.view.create_project("core", who.name)
        who.view.create_project(f"only-{who.name.lower()}")
        who.project = project.id
    app = create_app(store, public_url=BASE)

    async def one(client: httpx.AsyncClient, n: int) -> None:
        who, other = tenants[n % 2], tenants[(n + 1) % 2]
        headers = {"x-api-key": who.key}
        expected = sorted(["core", f"only-{who.name.lower()}"])
        if n % 4 < 2:
            r = await client.get("/api/projects", headers=headers)
            projects = r.json()["projects"]
        else:
            body = {
                "jsonrpc": "2.0",
                "id": n,
                "method": "tools/call",
                "params": {"name": "projects", "arguments": {}},
            }
            r = await client.post("/mcp", headers={**MCP_HEADERS, **headers}, json=body)
            projects = json.loads(r.json()["result"]["content"][0]["text"])["projects"]
        assert r.status_code == 200
        assert sorted(b["name"] for b in projects) == expected, (n, who.name)
        assert who.project in {b["id"] for b in projects}
        assert other.project not in {b["id"] for b in projects}
        note = {"text": f"# Note {n}\n\n{who.name} wrote fact {n}.\n"}
        made = await client.post(f"/api/projects/{who.project}/notes", headers=headers, json=note)
        assert made.status_code == 201
        denied = await client.post(
            f"/api/projects/{other.project}/notes", headers=headers, json=note
        )
        assert denied.status_code == 404

    async def main() -> None:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
                await asyncio.gather(*(one(client, n) for n in range(40)))

    try:
        asyncio.run(main())
        for who in tenants:
            notes = who.view.list_notes(who.project)
            assert len(notes) == 20
            assert all(f"{who.name} wrote" in c.text for c in notes)
    finally:
        store.close()
