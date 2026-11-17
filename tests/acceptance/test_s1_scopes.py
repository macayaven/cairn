"""S1 acceptance tests (frozen): connections limited to chosen projects, and project sets.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/store/__init__.py and src/cairn/server.py.

Reach
- Every connection (an API key or an agent's grant) has a reach: `projects`, which is None for
  every project of the workspace, now and later, or a tuple of project ids and set ids; and
  `access`, "write" or "read". `Principal`, `KeyInfo` and `GrantInfo` carry both.
- `view.narrow(projects=None, access="write")` returns a view of the same workspace limited
  to that reach. Through it a project outside the reach behaves exactly like one that does
  not exist, `search` covers only the reach, and with "read" every write raises
  PermissionError. A view limited to named projects cannot create projects, keys or sets
  (PermissionError). Narrowing a narrowed view can only narrow it further.
- `create_key(label, projects=None, access="write")` records a key's reach; an id that is
  not a project or set of the workspace is a ValueError. A project or set deleted later simply
  drops out of the reach.

Project sets
- A set is a named group of projects: `create_set(name, project_ids=()) -> ProjectSet` (id "set_"
  + 12 hex chars; names follow the project-name rules and are unique per workspace),
  `list_sets()`, `get_set(id or name)`, `update_set(id, project_ids)`, `delete_set(id)`.
  A reach that names a set covers whatever the set holds at the time of the request.

The server
- A request is served from its connection's narrowed view. Out of reach is 404 over REST
  and a tool error over MCP, with the message an unknown project gets. A write through a
  read-only connection is 403 (REST) or a tool error (MCP), saying "read-only". Creating a
  project through a connection limited to named projects is refused the same way.
- The consent page lists the workspace's projects and sets as checkboxes named `projects`, with
  `scope` ("all" or "selected") and `access` ("write" or "read"). Approving without them
  means all projects, read and write. "selected" with no project, or with an id that is not the
  workspace's, is 400 and grants nothing. A grant keeps its reach across token refreshes.
- POST /keys takes the same three fields. GET /agents and GET /keys show each connection's
  reach by name, and mark read-only ones.
- GET /sets lists the sets; POST /sets (`name`, `projects`) creates one;
  POST /sets/<id> (`projects`) replaces its projects; POST /sets/<id>/delete removes it.
- Web sessions keep the whole workspace.

Patterns (docs/patterns.md describes them; the last three tests run them as written)
- Many agents, one project. Many agents, many projects. Project sets per use case.
"""

import base64
import hashlib
import json
import re
import secrets
import sqlite3
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from starlette.testclient import TestClient

from cairn.compiler import compile_note
from cairn.server import create_app
from cairn.store import Store

BASE = "http://127.0.0.1:8765"
REDIRECT = "http://127.0.0.1:33418/callback"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
UNKNOWN = "br_000000000000"


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def note(view: Store, project_id: str, title: str, body: str) -> None:
    note, _ = view.add_note(project_id, f"# {title}\n\n{body}\n")
    assert compile_note(view, note).status == "saved"


class Hidden(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("type") == "hidden":
            self.fields[attributes["name"]] = attributes.get("value") or ""


def hidden_fields(html: str) -> dict[str, str]:
    parser = Hidden()
    parser.feed(html)
    return parser.fields


@pytest.fixture
def env(tmp_path):
    """One workspace with three projects, each holding a note that mentions "quill"."""
    db = tmp_path / "cairn.db"
    store = Store(db)
    projects = {}
    for name, fact in (
        ("api", "Quill retries 3 times in the API."),
        ("web", "Quill renders the web checkout."),
        ("journal", "Quill is also my cat's name."),
    ):
        projects[name] = store.create_project(name, f"the {name} project")
        note(store, projects[name].id, f"{name} notes", fact)
    key = store.create_key("owner")
    app = create_app(store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:
        yield SimpleNamespace(
            client=client, store=store, db=db, key=key, ids={n: b.id for n, b in projects.items()}
        )
    store.close()


def api(env, key: str, method: str, path: str, **kwargs):
    return env.client.request(method, path, headers={"x-api-key": key}, **kwargs)


def tool(env, credential: str, name: str, /, **arguments) -> dict:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    headers = {**MCP_HEADERS, "authorization": f"Bearer {credential}"}
    r = env.client.post("/mcp", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def call(env, credential: str, name: str, /, **arguments) -> dict:
    result = tool(env, credential, name, **arguments)
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def refused(env, credential: str, name: str, /, **arguments) -> str:
    result = tool(env, credential, name, **arguments)
    assert result["isError"] is True, result
    return result["content"][0]["text"]


def sign_in(env) -> None:
    r = env.client.post(
        "/login", data={"key": env.key}, headers=SAME_ORIGIN, follow_redirects=False
    )
    assert r.status_code == 303


def consent(env, client_name: str):
    """Register an agent and open the consent page for it, signed in as the owner."""
    c = env.client
    registered = c.post(
        "/register",
        json={
            "client_name": client_name,
            "redirect_uris": [REDIRECT],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
        },
    )
    assert registered.status_code == 201, registered.text
    client_id = registered.json()["client_id"]
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    prm = c.get("/.well-known/oauth-protected-resource/mcp").json()
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": base64.urlsafe_b64encode(digest).rstrip(b"=").decode(),
        "code_challenge_method": "S256",
        "state": "s",
        "resource": f"{BASE}/mcp",
        "scope": " ".join(prm.get("scopes_supported") or []),
    }
    sign_in(env)
    r = c.get("/authorize?" + urlencode(params), follow_redirects=False)
    assert r.status_code == 302
    page = c.get(r.headers["location"])
    assert page.status_code == 200, page.text
    return SimpleNamespace(client_id=client_id, verifier=verifier, page=page)


def decide(env, pending, **choice):
    data = {**hidden_fields(pending.page.text), "decision": "approve", **choice}
    return env.client.post("/oauth/consent", data=data, headers=SAME_ORIGIN, follow_redirects=False)


def connect(env, client_name: str, **choice) -> SimpleNamespace:
    """The whole dance; `choice` is what the person ticks on the consent page."""
    pending = consent(env, client_name)
    decided = decide(env, pending, **choice)
    assert decided.status_code == 303, decided.text
    code = parse_qs(urlsplit(decided.headers["location"]).query)["code"][0]
    token = env.client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": pending.client_id,
            "code_verifier": pending.verifier,
        },
    )
    assert token.status_code == 200, token.text
    env.client.cookies.clear()
    body = token.json()
    return SimpleNamespace(
        client_id=pending.client_id, access=body["access_token"], refresh=body["refresh_token"]
    )


# The store


def test_a_narrowed_view_treats_everything_else_as_unknown(env):
    api_id, web_id, journal_id = env.ids["api"], env.ids["web"], env.ids["journal"]
    view = env.store.narrow(projects=(api_id, web_id))
    assert [b.name for b in view.list_projects()] == ["api", "web"]
    assert view.get_project("journal") is None and view.get_project(journal_id) is None
    assert view.list_pages(journal_id) == [] and view.list_notes(journal_id) == []
    stored = env.store.list_notes(journal_id)[0]
    assert view.get_note(stored.id) is None and view.note_spans(stored.id) == []
    assert {h.project for h in view.search("quill")} == {"api", "web"}
    assert view.search("quill", project_ids=[journal_id]) == []
    with pytest.raises(KeyError):
        view.add_note(journal_id, "an intruder's note")
    note(view, api_id, "more api notes", "The API pages by cursor.")  # in reach: it can write

    narrower = view.narrow(projects=(api_id, journal_id))  # can only narrow: journal stays out
    assert [b.name for b in narrower.list_projects()] == ["api"]
    assert [b.name for b in env.store.narrow().list_projects()] == ["api", "journal", "web"]
    with pytest.raises(ValueError):
        env.store.create_key("bad", projects=(UNKNOWN,))


def test_a_read_only_view_refuses_every_write(env):
    view = env.store.narrow(access="read")
    api_id = env.ids["api"]
    page = view.list_pages(api_id)[0]
    before = dump(env.db)
    assert view.search("quill") and view.get_project("api")
    writes = (
        lambda: view.create_project("more"),
        lambda: view.add_note(api_id, "a note"),
        lambda: view.save_page(api_id, page.slug, page.title, page.body, page.claims),
        lambda: view.create_key("k"),
        lambda: view.revoke_key("owner"),
        lambda: view.create_set("work", (api_id,)),
        lambda: view.create_session(),
    )
    for write in writes:
        with pytest.raises(PermissionError):
            write()
    assert dump(env.db) == before
    assert view.narrow(access="write").narrow().list_projects()  # still readable ...
    with pytest.raises(PermissionError):  # ... and still read-only: it cannot widen
        view.narrow(access="write").add_note(api_id, "a note")

    limited = env.store.narrow(projects=(api_id,))
    for manage in (
        lambda: limited.create_project("more"),
        lambda: limited.create_key("k"),
        lambda: limited.create_set("s"),
    ):
        with pytest.raises(PermissionError):
            manage()
    assert dump(env.db) == before


def test_sets_are_named_groups_and_a_reach_follows_them(env):
    api_id, web_id, journal_id = env.ids["api"], env.ids["web"], env.ids["journal"]
    work = env.store.create_set("work", (api_id,))
    assert work.id.startswith("set_") and len(work.id) == 16
    assert (work.name, work.project_ids) == ("work", (api_id,))
    assert env.store.get_set("WORK") == work == env.store.get_set(work.id)
    assert [s.name for s in env.store.list_sets()] == ["work"]
    for bad in (lambda: env.store.create_set("Work"), lambda: env.store.create_set("not a name")):
        with pytest.raises(ValueError):
            bad()
    with pytest.raises(ValueError):
        env.store.create_set("ghost", (UNKNOWN,))

    view = env.store.narrow(projects=(work.id,))
    key = env.store.create_key("ci", projects=(work.id,), access="read")
    assert [b.name for b in view.list_projects()] == ["api"]
    env.store.update_set(work.id, (api_id, web_id))  # the set grows: so does the reach
    assert [b.name for b in view.list_projects()] == ["api", "web"]
    who = env.store.resolve_key(key)
    assert (who.projects, who.access) == ((work.id,), "read")
    reach = env.store.for_account(who.account_id).narrow(who.projects, who.access)
    assert {h.project for h in reach.search("quill")} == {"api", "web"}
    assert reach.get_project(journal_id) is None

    other = env.store.for_account(env.store.create_account("Brook").id)
    assert other.list_sets() == [] and other.get_set(work.id) is None  # sets are per workspace
    assert env.store.delete_set(work.id) is True and env.store.delete_set(work.id) is False
    assert view.list_projects() == [] and reach.search("quill") == []  # nothing left in reach


# Keys over REST and MCP


def test_a_key_reaches_only_its_projects(env):
    api_id, journal_id = env.ids["api"], env.ids["journal"]
    reader = env.store.create_key("dashboard", projects=(api_id,), access="read")
    writer = env.store.create_key("api-agent", projects=(api_id,))
    info = {k.label: (k.projects, k.access) for k in env.store.list_keys()}
    assert info["dashboard"] == ((api_id,), "read") and info["api-agent"] == ((api_id,), "write")
    assert info["owner"] == (None, "write")

    for key in (reader, writer):
        assert [b["name"] for b in call(env, key, "projects")["projects"]] == ["api"]
        assert {h["project"] for h in call(env, key, "recall", query="quill")["hits"]} == {"api"}
        listed = api(env, key, "GET", "/api/projects").json()["projects"]
        assert [b["name"] for b in listed] == ["api"]
        # Out of reach reads like unknown, by id and by name.
        gone = refused(env, key, "overview", project=UNKNOWN)
        assert refused(env, key, "overview", project=journal_id) == gone.replace(
            UNKNOWN, journal_id
        )
        assert "journal" in refused(env, key, "overview", project="journal")
        assert api(env, key, "GET", f"/api/projects/{journal_id}/pages").status_code == 404
        assert api(env, key, "GET", "/api/search?q=quill&project=journal").status_code == 404
        # A connection limited to named projects cannot make new ones.
        assert "limited" in refused(env, key, "new_project", name="extra")
        assert api(env, key, "POST", "/api/projects", json={"name": "extra"}).status_code == 403

    before = dump(env.db)
    assert "read-only" in refused(env, reader, "note", project="api", text="# x\n\ny\n")
    denied = api(env, reader, "POST", f"/api/projects/{api_id}/notes", json={"text": "# x\n\ny\n"})
    assert denied.status_code == 403 and "read-only" in denied.json()["error"]
    assert dump(env.db) == before
    made = call(env, writer, "note", project="api", text="# Paging\n\nThe API pages by cursor.\n")
    assert made["status"] == "saved"
    assert (
        api(
            env, writer, "POST", f"/api/projects/{journal_id}/notes", json={"text": "x"}
        ).status_code
        == 404
    )


# Agents choose nothing: the person does, on the consent page


def test_the_person_chooses_an_agents_reach_when_approving(env):
    api_id, web_id, journal_id = env.ids["api"], env.ids["web"], env.ids["journal"]
    work = env.store.create_set("work", (api_id, web_id))

    pending = consent(env, "review-bot")
    boxes = re.findall(r'<input\b[^>]*name="projects"[^>]*value="([^"]+)"', pending.page.text)
    assert sorted(boxes) == sorted([api_id, web_id, journal_id, work.id])
    for name in ("api", "web", "journal", "work"):
        assert name in pending.page.text
    assert 'name="scope"' in pending.page.text and 'name="access"' in pending.page.text
    for bad in ({"scope": "selected"}, {"scope": "selected", "projects": [UNKNOWN]}):
        r = decide(env, pending, **bad)
        assert r.status_code == 400 and "code=" not in r.headers.get("location", "")

    bot = connect(env, "review-bot", scope="selected", projects=[work.id], access="read")
    who = env.store.resolve_token(bot.access)
    assert (who.kind, who.projects, who.access) == ("token", (work.id,), "read")
    assert [b["name"] for b in call(env, bot.access, "projects")["projects"]] == ["api", "web"]
    assert {h["project"] for h in call(env, bot.access, "recall", query="quill")["hits"]} == {
        "api",
        "web",
    }
    assert "journal" in refused(env, bot.access, "page", project="journal", slug="journal-notes")
    assert "read-only" in refused(env, bot.access, "note", project="api", text="# x\n\ny\n")

    # A refresh keeps the reach.
    renewed = env.client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": bot.refresh,
            "client_id": bot.client_id,
        },
    ).json()
    again = env.store.resolve_token(renewed["access_token"])
    assert (again.projects, again.access) == ((work.id,), "read")

    everything = connect(env, "my-laptop")  # nothing ticked: all projects, read and write
    who = env.store.resolve_token(everything.access)
    assert (who.projects, who.access) == (None, "write")
    assert len(call(env, everything.access, "projects")["projects"]) == 3

    sign_in(env)
    page = env.client.get("/agents").text
    row = page[page.index("review-bot") :]
    assert "work" in row and "read" in row.lower()
    grants = {g.client_name: (g.projects, g.access) for g in env.store.list_grants()}
    assert grants == {"review-bot": ((work.id,), "read"), "my-laptop": (None, "write")}


def test_keys_and_sets_are_managed_in_the_web_view(env):
    api_id, web_id = env.ids["api"], env.ids["web"]
    sign_in(env)
    c = env.client
    made = c.post(
        "/sets",
        data={"name": "work", "projects": [api_id, web_id]},
        headers=SAME_ORIGIN,
        follow_redirects=False,
    )
    assert made.status_code == 303
    work = env.store.get_set("work")
    assert work.project_ids == (api_id, web_id) and "work" in c.get("/sets").text
    taken = c.post("/sets", data={"name": "WORK"}, headers=SAME_ORIGIN)
    assert taken.status_code == 409
    c.post(f"/sets/{work.id}", data={"projects": [api_id]}, headers=SAME_ORIGIN)
    assert env.store.get_set("work").project_ids == (api_id,)

    created = c.post(
        "/keys",
        data={
            "label": "report-worker",
            "scope": "selected",
            "projects": [work.id],
            "access": "read",
        },
        headers=SAME_ORIGIN,
    )
    assert created.status_code == 200
    key = next(
        k for k in re.findall(r"[A-Za-z0-9_-]{40,}", created.text) if env.store.resolve_key(k)
    )
    who = env.store.resolve_key(key)
    assert (who.label, who.projects, who.access) == ("report-worker", (work.id,), "read")
    listed = c.get("/keys").text
    row = listed[listed.index("report-worker") :]
    assert "work" in row and "read" in row.lower()
    assert (
        c.post("/keys", data={"label": "x", "scope": "selected"}, headers=SAME_ORIGIN).status_code
        == 400
    )

    gone = c.post(f"/sets/{work.id}/delete", headers=SAME_ORIGIN, follow_redirects=False)
    assert gone.status_code == 303 and env.store.list_sets() == []
    assert call(env, key, "projects")["projects"] == []  # the key's reach is now empty
    assert c.get("/b/journal").status_code == 200  # a web session keeps the whole workspace


def test_reads_through_a_scoped_connection_never_write(env):
    work = env.store.create_set("work", (env.ids["api"],))
    key = env.store.create_key("ci", projects=(work.id,), access="read")
    bot = connect(env, "bot", scope="selected", projects=[env.ids["web"]])
    sign_in(env)
    before = dump(env.db)
    for credential in (key, bot.access):
        call(env, credential, "projects")
        call(env, credential, "recall", query="quill")
        tool(env, credential, "overview", project="journal")
    env.client.get("/sets")
    env.client.get("/keys")
    env.client.get("/agents")
    env.store.narrow(projects=(work.id,), access="read").search("quill")
    assert dump(env.db) == before


# The three patterns of docs/patterns.md, run as written


def test_pattern_many_agents_one_project(tmp_path):
    """A person's laptop agents and a CI job all share one project."""
    store = Store(tmp_path / "cairn.db")
    try:
        store.create_project("core", "everything I work on")
        owner = store.create_key("owner")
        app = create_app(store, public_url=BASE)
        with TestClient(app, base_url=BASE) as client:
            env = SimpleNamespace(client=client, store=store, key=owner)
            laptop = connect(env, "claude-code")
            editor = connect(env, "cursor")
            ci = store.create_key("ci")
            call(
                env,
                laptop.access,
                "note",
                project="core",
                text="# Deploys\n\nDeploys run at 09:00.\n",
            )
            call(env, ci, "note", project="core", text="# Builds\n\nBuilds take 4 minutes.\n")
            for credential in (laptop.access, editor.access, ci):
                hits = call(env, credential, "recall", query="deploys builds")["hits"]
                assert {h["page"] for h in hits} == {"deploys", "builds"}
    finally:
        store.close()


def test_pattern_many_agents_many_projects(env):
    """Each app's agent writes to its own project and reads a shared one."""
    api_id, web_id = env.ids["api"], env.ids["web"]
    shared = env.store.create_project("handbook", "what every team reads")
    note(env.store, shared.id, "Conventions", "Quill commands are documented in the handbook.")
    api_agent = env.store.create_key("api-agent", projects=(api_id,))
    web_agent = env.store.create_key("web-agent", projects=(web_id,))
    api_reader = env.store.create_key("api-agent-handbook", projects=(shared.id,), access="read")

    call(
        env,
        api_agent,
        "note",
        project="api",
        text="# Limits\n\nThe API allows 60 calls a minute.\n",
    )
    assert "web" in refused(env, api_agent, "note", project="web", text="# x\n\ny\n")
    assert {h["project"] for h in call(env, web_agent, "recall", query="quill limits")["hits"]} == {
        "web"
    }
    assert call(env, api_reader, "recall", query="handbook")["hits"]
    assert "read-only" in refused(env, api_reader, "note", project="handbook", text="# x\n\ny\n")


def test_pattern_project_sets_per_use_case(env):
    """One person, two lives: a work set and a personal project, an agent for each."""
    api_id, web_id, journal_id = env.ids["api"], env.ids["web"], env.ids["journal"]
    work = env.store.create_set("work", (api_id, web_id))
    work_agent = connect(env, "work-laptop", scope="selected", projects=[work.id])
    home_agent = connect(env, "phone", scope="selected", projects=[journal_id])

    assert {
        h["project"] for h in call(env, work_agent.access, "recall", query="quill")["hits"]
    } == {
        "api",
        "web",
    }
    assert {
        h["project"] for h in call(env, home_agent.access, "recall", query="quill")["hits"]
    } == {"journal"}
    # A new work project joins the set, and the work agent reaches it without reconnecting.
    billing = env.store.create_project("billing")
    note(env.store, billing.id, "Invoices", "Quill invoices are sent monthly.")
    env.store.update_set(work.id, (api_id, web_id, billing.id))
    assert "billing" in {
        h["project"] for h in call(env, work_agent.access, "recall", query="quill invoices")["hits"]
    }
    assert "billing" in refused(env, home_agent.access, "overview", project="billing")
