"""Sentences that cairn's own pages show people, and that agents read over MCP, each pinned to
the behaviour it states, so the sentence cannot go false unnoticed. Each test's docstring
quotes the sentence and names where it lives. The app runs in process over a real store in a
temporary file; sign-in providers and Langfuse are stand-ins; nothing leaves the process."""

import html
import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from starlette.testclient import TestClient

from cairn import server
from cairn.gate import GateResult
from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.store import PAGE_CLAIMS_MAX, Store
from cairn.store.grants import REFRESH_TOKEN_TTL
from cairn.web import cli
from cookie_names import session_cookie

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
REDIRECT = "http://127.0.0.1:33418/callback"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
ISSUER = "https://id.example.test"
NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)
TOOLS = {"projects", "new_project", "note", "recall", "page", "evidence", "overview", "graph"}


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def rpc(client, key: str, method: str, params=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": key}, json=body)


def tool(client, key: str, name: str, /, **arguments) -> dict:
    r = rpc(client, key, "tools/call", {"name": name, "arguments": arguments})
    assert r.status_code == 200, r.text
    return r.json()["result"]


def answer(client, key: str, name: str, /, **arguments) -> dict:
    result = tool(client, key, name, **arguments)
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def post(client, path: str, data: dict | None = None, **headers):
    return client.post(
        path, data=data or {}, headers={**SAME_ORIGIN, **headers}, follow_redirects=False
    )


@pytest.fixture
def env(tmp_path):
    """One workspace with the project "ops", a key of no person, and a client signed in with it
    when a test calls `sign_in`."""
    db = tmp_path / "cairn.db"
    store = Store(db)
    store.create_project("ops")
    key = store.create_key("owner")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:

        def sign_in() -> None:
            r = post(client, "/login", {"key": key})
            assert r.status_code == 303

        yield SimpleNamespace(
            client=client, store=store, db=db, key=key, headers={"x-api-key": key}, sign_in=sign_in
        )
    store.close()


def routes(app):
    """Every path the app answers, with its methods, mounted routes included."""

    def walk(found, prefix=""):
        for route in found:
            path = prefix + getattr(route, "path", "")
            yield path, getattr(route, "methods", None)
            yield from walk(getattr(route, "routes", []) or [], path)

    return list(walk(app.routes))


# web/mcp.py: the recall tool


def test_recall_takes_a_limit_of_1_to_50_and_refuses_any_other(env):
    """web/mcp.py, recall's `limit`: "Most hits to return, 1 to 50." A limit outside that is
    refused, not cut (Q3; decision 112: a request never loses a field silently), over MCP
    and REST alike, in one sentence that says the range; the tool's input schema states the
    range, so a client can check before it calls."""
    text = "# Ledger\n\n" + "".join(f"Quill line {i} holds.\n\n" for i in range(60))
    assert answer(env.client, env.key, "note", project="ops", text=text)["status"] == "saved"
    for limit in (1, 50):
        hits = answer(env.client, env.key, "recall", query="quill", limit=limit)["hits"]
        assert len(hits) == limit
        rest = env.client.get(f"/api/search?q=quill&limit={limit}", headers=env.headers)
        assert rest.status_code == 200 and len(rest.json()["hits"]) == limit
    said = "'limit' is a whole number, 1 to 50"
    for limit in (0, -5, 51, 500):
        refused = tool(env.client, env.key, "recall", query="quill", limit=limit)
        assert refused["isError"] is True, limit
        assert refused["content"][0]["text"].endswith(said), refused
        rest = env.client.get(f"/api/search?q=quill&limit={limit}", headers=env.headers)
        assert (rest.status_code, rest.json()) == (400, {"error": said}), limit
    listed = rpc(env.client, env.key, "tools/list").json()["result"]["tools"]
    (schema,) = [t["inputSchema"]["properties"]["limit"] for t in listed if t["name"] == "recall"]
    assert (schema["minimum"], schema["maximum"]) == (1, 50)


# memory.py: the note tool's refusals


def test_an_empty_note_over_mcp_is_a_tool_error_and_stores_nothing(env):
    """memory.py: "the note's text is empty". Over MCP it is a tool error, and nothing of
    the note is stored."""
    before = dump(env.db)
    for blank in ("", "   \n\n\t"):
        refused = tool(env.client, env.key, "note", project="ops", text=blank)
        assert refused["isError"] is True and "empty" in refused["content"][0]["text"]
    assert env.store.list_notes(env.store.get_project("ops").id) == []
    assert dump(env.db) == before


def test_a_page_filled_during_the_compile_keeps_the_note_and_adds_nothing(env, monkeypatch):
    """memory.py, PageFull.kept: "The note was kept, but page ... came to hold the 2,000
    claims a page holds meanwhile, and nothing was added to it." The look before the store
    (Memory._room_on_page) is skipped, as when another note fills the page between that
    look and the compile."""
    ops = env.store.get_project("ops")
    for n in range(PAGE_CLAIMS_MAX // 400):  # notes of 400 one-line claims fill the page
        text = "# Ledger\n\n" + "".join(f"Line {n}-{i} holds.\n\n" for i in range(400))
        r = env.client.post(
            f"/api/projects/{ops.id}/notes", headers=env.headers, json={"text": text}
        )
        assert r.status_code == 201, n
    page = env.store.get_page(ops.id, "ledger")
    assert len(page.claims) == PAGE_CLAIMS_MAX
    notes = len(env.store.list_notes(ops.id))

    monkeypatch.setattr("cairn.memory.Memory._room_on_page", lambda *args: None)
    late = "# Ledger\n\nOne more line, kingfisher.\n"
    r = env.client.post(f"/api/projects/{ops.id}/notes", headers=env.headers, json={"text": late})
    assert r.status_code == 409
    said = r.json()["error"]
    assert said.startswith("The note was kept") and "nothing was added" in said
    assert "kingfisher" not in said
    assert len(env.store.list_notes(ops.id)) == notes + 1  # kept
    after = env.store.get_page(ops.id, "ledger")
    assert (after.version, len(after.claims)) == (page.version, PAGE_CLAIMS_MAX)  # nothing added


# search.html: how many claims matched


def test_the_search_page_counts_only_below_the_most_it_shows(env):
    """search.html: the page asks for at most 30 hits (web/ui/memory_pages.py UI_HITS), so with 30
    it says "The first 30 claims that match" and below that it keeps the count."""
    text = "# Ledger\n\n" + "".join(f"Quill line {i} holds.\n\n" for i in range(35))
    text += "# Kite\n\nThe kite flies high.\n\nThe kite is red.\n"
    assert answer(env.client, env.key, "note", project="ops", text=text)["status"] == "saved"
    env.sign_in()
    many = env.client.get("/search?q=quill").text
    assert "The first 30 claims that match." in many and "matched" not in many
    assert many.count('class="hit"') == 30
    few = env.client.get("/search?q=kite&project=ops").text
    assert "2 claims matched in ops." in few and "The first" not in few


# web/mcp.py: the evidence tool


def test_trace_gives_each_spans_heading_path_and_its_notes_source_ref(env):
    """web/mcp.py, evidence: "each cited passage's exact text, heading path and line range, and the
    note it came from (id, title, source_ref)"."""
    text = "# Runbook\n\n## Deploys\n\nQuill deploys at noon on weekdays.\n"
    noted = answer(env.client, env.key, "note", project="ops", text=text, source_ref="commit 4f2a")
    page = answer(env.client, env.key, "page", project="ops", slug=noted["page"])
    claim = next(c for c in page["claims"] if "noon" in c["text"])
    traced = answer(
        env.client, env.key, "evidence", project="ops", slug=noted["page"], claim_id=claim["id"]
    )
    [span] = traced["passages"]
    assert span["heading_path"] == ["Runbook", "Deploys"]
    assert span["source_ref"] == "commit 4f2a"
    assert span["note_id"] == noted["note_id"] and span["note_title"] == "Runbook"
    assert span["text"].strip() == "Quill deploys at noon on weekdays."
    assert span["start_line"] <= span["end_line"]


# oauth.py: the consent form


def test_the_consent_form_refuses_a_project_of_another_workspace(env):
    """oauth.py: "Selected projects: tick only projects and sets of this workspace." A project of
    another workspace is refused, grants nothing, and the request stays open."""
    elsewhere = env.store.sign_in(
        "sso", "x", "x@example.test", "Xan", issuer=ISSUER, join="personal"
    ).account_id
    theirs = env.store.for_account(elsewhere).create_project("theirs")
    client_id = env.client.post(
        "/register",
        json={
            "client_name": "bot",
            "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"],
        },
    ).json()["client_id"]
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "state": "s",
    }
    env.sign_in()
    sent = env.client.get("/authorize?" + urlencode(params), follow_redirects=False)
    page = env.client.get(sent.headers["location"])
    request = re.search(r'name="request" value="([^"]+)"', page.text).group(1)
    before = dump(env.db)
    chosen = {"request": request, "decision": "approve", "scope": "selected", "access": "write"}
    refused = post(env.client, "/oauth/consent", {**chosen, "projects": theirs.id})
    assert refused.status_code == 400
    assert "Selected projects: tick only projects and sets of this workspace." in refused.text
    assert dump(env.db) == before
    ops = env.store.get_project("ops")
    approved = post(env.client, "/oauth/consent", {**chosen, "projects": ops.id})
    assert approved.status_code == 303
    assert "code" in parse_qs(urlsplit(approved.headers["location"]).query)


# web/sharing.py: how far a project is shared


def test_a_private_project_with_no_maker_says_owners_only(tmp_path):
    """web/sharing.py: "Private: owners only", for a private project whose rule names nobody,
    such as one a key of no person made."""
    store = Store(tmp_path / "cairn.db")
    try:
        ada = store.sign_in("sso", "a", "ada@example.test", "Ada", issuer=ISSUER, join="default")
        vault = store.create_project("vault", everyone="none")  # the root store acts for no one
        assert vault.maker_id is None
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            home = client.get("/", headers={"cookie": f"{session_cookie()}={ada.token}"})
            assert home.status_code == 200
            row = home.text[home.text.index(">vault<") :]
            assert "Private: owners only" in row[: row.index("</li>")]
    finally:
        store.close()


# project_delete.html and forget.html: "This cannot be undone."


def test_no_route_restores_a_deleted_project_or_a_forgotten_note(env):
    """project_delete.html and forget.html: "This cannot be undone." No web, REST or MCP route
    brings a deleted project or a forgotten note back, and a project made again under the same
    name starts empty."""
    app = env.client.app
    for path, _ in routes(app):
        assert not re.search(r"restore|undelete|undo|recover|trash|unforget|bin\b", path), path
    tools = rpc(env.client, env.key, "tools/list").json()["result"]["tools"]
    assert {t["name"] for t in tools} == TOOLS

    ops = env.store.get_project("ops")
    kept = answer(env.client, env.key, "note", project="ops", text="# A\n\nQuill keeps this.\n")
    gone = answer(env.client, env.key, "note", project="ops", text="# B\n\nQuill forgets this.\n")
    r = env.client.delete(f"/api/projects/{ops.id}/notes/{gone['note_id']}", headers=env.headers)
    assert r.status_code == 200
    assert [c.id for c in env.store.list_notes(ops.id)] == [kept["note_id"]]
    r = env.client.delete(f"/api/projects/{ops.id}?confirm=ops", headers=env.headers)
    assert r.status_code == 200
    again = answer(env.client, env.key, "new_project", name="ops")
    assert again["id"] != ops.id
    assert env.store.list_notes(again["id"]) == [] and env.store.list_pages(again["id"]) == []
    assert answer(env.client, env.key, "recall", query="quill")["hits"] == []


# sources.html: a source's keys live in the server's environment


PUBLIC, SECRET = "pk-lf-facts-public", "sk-lf-facts-secret-never-stored"


def _langfuse(request: httpx.Request) -> httpx.Response:
    """One marked trace, every page at once."""
    stamp = (datetime.now(UTC) - timedelta(minutes=60)).isoformat().replace("+00:00", "Z")
    if request.url.path.endswith("/scores"):
        score = {
            "id": "s1",
            "projectId": "proj",
            "name": "cairn",
            "dataType": "CATEGORICAL",
            "value": "ops",
            "timestamp": stamp,
            "comment": "The refund rule.",
            "subject": {"kind": "trace", "id": "t1"},
        }
        return httpx.Response(200, json={"data": [score], "meta": {"limit": 100}})
    root = {
        "id": "o1",
        "traceId": "t1",
        "startTime": stamp,
        "isRootObservation": True,
        "input": "Refund order 4411",
        "output": "Quill refunds take 5 days.",
        "traceName": "refund-flow",
    }
    return httpx.Response(200, json={"data": [root], "meta": {}})


def test_a_sources_config_names_environment_variables_and_never_holds_their_values(
    tmp_path, monkeypatch, capsys
):
    """sources.html: "Its keys live in the server's environment, never in cairn." The config
    holds the variables' names; after a sync that read them, their values are nowhere in the
    database, and a key put where a name goes is refused."""
    db = tmp_path / "cairn.db"
    store = Store(db)
    store.create_project("ops")
    with pytest.raises(ValueError, match="environment variable"):
        store.add_source(
            "leaky",
            "langfuse",
            {
                "base_url": "https://langfuse.example.test",
                "projects": ["ops"],
                "public_key_env": PUBLIC,
                "secret_key_env": SECRET,
            },
        )
    store.close()
    monkeypatch.setattr(
        cli, "_source_http", lambda: httpx.AsyncClient(transport=httpx.MockTransport(_langfuse))
    )
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", PUBLIC)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", SECRET)
    config = {
        "base_url": "https://langfuse.example.test",
        "projects": ["ops"],
        "public_key_env": "LANGFUSE_PUBLIC_KEY",
        "secret_key_env": "LANGFUSE_SECRET_KEY",
    }
    added = server.main(
        ["source-add", "--db", str(db), "--name", "traces", "--kind", "langfuse",
         "--config", json.dumps(config)]
    )  # fmt: skip
    assert added == 0
    assert server.main(["source-sync", "--db", str(db), "--name", "traces"]) == 0
    printed = capsys.readouterr()
    assert "1 saved" in printed.out
    stored = dump(db)
    assert "LANGFUSE_SECRET_KEY" in stored and "LANGFUSE_PUBLIC_KEY" in stored
    assert SECRET not in stored and PUBLIC not in stored
    assert SECRET not in printed.out + printed.err


# signin.html: a provider's sign-in


class GitHubFake:
    """GitHub's endpoints for one person, who approves every time."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == "https://github.com/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_facts_provider_token"})
        if url == "https://api.github.com/user":
            return httpx.Response(200, json={"id": 7, "login": "ada", "name": "Ada Lovelace"})
        if url == "https://api.github.com/user/emails":
            emails = [{"email": "ada@example.test", "primary": True, "verified": True}]
            return httpx.Response(200, json=emails)
        return httpx.Response(404)


def test_a_provider_sign_in_sets_a_twelve_hour_cookie_and_keeps_no_password(tmp_path):
    """signin.html: "cairn keeps no password" and the session cookie "is valid for 12 hours".
    The cookie's max-age is 12 hours, the session ends then, and neither the provider's
    client secret nor its token is stored; the database has no password column."""
    db = tmp_path / "cairn.db"
    now = [NOW]

    def clock() -> datetime:
        return now[0]

    store = Store(db, clock=clock)
    http = httpx.AsyncClient(transport=httpx.MockTransport(GitHubFake()))
    sign_in = SignIn(providers=(GitHub("gh-id", "gh-client-secret-xyz"),), http=http)
    app = create_app(store, public_url="https://cairn.example.test", sign_in=sign_in, clock=clock)
    try:
        with TestClient(app, base_url="https://cairn.example.test") as client:
            started = client.get("/auth/github", follow_redirects=False)
            state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
            done = client.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
            assert done.status_code == 200
            cookie = next(
                c
                for c in done.headers.get_list("set-cookie")
                if c.startswith(f"{session_cookie('https')}=")
            )
            assert "max-age=43200" in cookie.lower()
            now[0] = NOW + timedelta(hours=12) - timedelta(seconds=1)
            assert client.get("/workspace", follow_redirects=False).status_code == 200
            now[0] = NOW + timedelta(hours=12)
            ended = client.get("/workspace", follow_redirects=False)
            assert ended.status_code == 303 and ended.headers["location"].startswith("/login")
        stored = dump(db)
        assert "gho_facts_provider_token" not in stored and "gh-client-secret-xyz" not in stored
        assert "password" not in stored.lower()
    finally:
        store.close()


# web/rest.py: a malformed body


def test_rest_says_what_is_wrong_with_a_malformed_body(env):
    """web/rest.py: "the body must be a JSON object", "'x' is required", "'x' must be a
    string" and "'x' must be a whole number". Each is 400, and nothing is stored."""
    ops = env.store.get_project("ops")
    notes = f"/api/projects/{ops.id}/notes"
    before = dump(env.db)
    for raw in ("[1, 2]", "not json", '"text"', "null"):
        r = env.client.post(
            notes, headers={**env.headers, "content-type": "application/json"}, content=raw
        )
        assert (r.status_code, r.json()["error"]) == (400, "the body must be a JSON object"), raw
    cases = (
        (notes, {}, "'text' is required"),
        (notes, {"text": None}, "'text' is required"),
        (notes, {"text": 5}, "'text' must be a string"),
        (notes, {"text": "# A\n\nB.\n", "topic": ["x"]}, "'topic' must be a string"),
        ("/api/projects", {"description": "no name"}, "'name' is required"),
    )
    for path, body, said in cases:
        r = env.client.post(path, headers=env.headers, json=body)
        assert (r.status_code, r.json()["error"]) == (400, said), body
    for limit in ("ten", "1.5", "2e3"):  # search says its range, as for 0 or 51 (R1.2)
        r = env.client.get(f"/api/search?q=quill&limit={limit}", headers=env.headers)
        assert (r.status_code, r.json()["error"]) == (400, "'limit' is a whole number, 1 to 50")
        r = env.client.get(f"/api/projects/{ops.id}/pages?limit={limit}", headers=env.headers)
        assert (r.status_code, r.json()["error"]) == (400, "'limit' must be a whole number")
    assert dump(env.db) == before


# web/compose.py: the gate refuses the page


def test_the_composer_keeps_the_note_when_the_gate_refuses_the_page(tmp_path):
    """web/compose.py: "The note was kept, but no page was written: the gate refused it." The
    note is stored; no page is."""

    def refuse(page, spans):
        return GateResult(passed=False, violations=("retention.fact_dropped: 1 time",))

    store = Store(tmp_path / "cairn.db")
    alpha = store.create_project("alpha")
    key = store.create_key("owner")
    try:
        with TestClient(create_app(store, public_url=BASE, gate=refuse), base_url=BASE) as client:
            assert post(client, "/login", {"key": key}).status_code == 303
            r = post(client, "/b/alpha/notes", {"text": "# Draft\n\nQuill retries 3 times.\n"})
            assert r.status_code == 422
            assert "The note was kept, but no page was written: the gate refused it." in r.text
        [note] = store.list_notes(alpha.id)
        assert "Quill retries 3 times." in note.text
        assert store.list_pages(alpha.id) == []
    finally:
        store.close()


# web/ui/workspace_pages.py: keys made and revoked in the web view


def test_a_keys_label_has_1_to_80_printable_characters_and_an_unknown_one_is_not_revoked(env):
    """web/ui/workspace_pages.py: "A key's label has 1 to 80 printable characters." and "no
    key labelled X". A refused label makes no key; revoking an unknown label is 404 and
    revokes nothing."""
    env.sign_in()
    before = dump(env.db)
    for label in ("", "   ", "x" * 81, "bell\x07", "tab\there"):
        r = post(env.client, "/keys", {"label": label, "scope": "all", "access": "write"})
        assert r.status_code == 400, repr(label)
        assert "A key&#39;s label has 1 to 80 printable characters." in r.text, repr(label)
    assert dump(env.db) == before
    made = post(env.client, "/keys", {"label": "y" * 80, "scope": "all", "access": "write"})
    assert made.status_code == 200
    with_it = dump(env.db)
    r = post(env.client, "/keys/no-such-label/revoke")
    assert r.status_code == 404 and "No key labelled &#39;no-such-label&#39;." in r.text
    assert dump(env.db) == with_it


# kinds.py: "Personal notes ... kept apart from the projects you share."


@pytest.fixture
def people(tmp_path):
    """Ada owns the default workspace and Brook is a member. Ada has a session and a key;
    both act for her."""
    db = tmp_path / "cairn.db"
    store = Store(db)
    ada = store.sign_in("sso", "a", "ada@example.test", "Ada", issuer=ISSUER, join="default")
    brook = store.sign_in("sso", "b", "brook@example.test", "Brook", issuer=ISSUER, join="default")
    ada_view = store.for_account(ada.account_id, ada.user.id)
    brook_view = store.for_account(brook.account_id, brook.user.id)
    key = ada_view.create_key("ada-laptop")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        yield SimpleNamespace(
            client=client,
            ada=ada_view,
            brook=brook_view,
            key=key,
            cookie={"cookie": f"{session_cookie()}={ada.token}"},
            brook_cookie={"cookie": f"{session_cookie()}={brook.token}"},
        )
    store.close()


def everyone(people, name: str) -> str:
    """What any member gets of the project named `name`: "write", "read" or "none"."""
    return people.ada.project_access(people.ada.get_project(name).id).everyone


def test_the_guided_setup_makes_a_personal_project_private_to_its_maker(people):
    """kinds.py, Personal notes: "kept apart from the projects you share". The setup makes it
    private to the person who made it (and the owners); the other uses stay open."""
    chosen = {"use": ["personal", "repo"], "name-personal": "journal", "name-repo": "api"}
    made = post(people.client, "/start", chosen, **people.cookie)
    assert made.status_code == 303
    assert everyone(people, "journal") == "none"
    assert people.ada.get_project("journal").maker_id == people.ada.user_id
    assert everyone(people, "api") == "write"
    assert [b.name for b in people.brook.list_projects()] == ["api"]


def test_the_new_project_form_makes_a_personal_project_private_whether_ticked_or_not(people):
    """The home page's form: a personal project is private when Private is ticked and when it is
    not; any other kind is private only when ticked. The form says so beside the box."""
    home = people.client.get("/", headers=people.cookie).text
    assert "A project for personal notes is private whether this is ticked or not" in home
    for name, kind, ticked, want in (
        ("diary", "personal", False, "none"),
        ("diary2", "personal", True, "none"),
        ("handbook", "team", False, "write"),
        ("secret-team", "team", True, "none"),
    ):
        form = {"name": name, "kind": kind} | ({"private": "on"} if ticked else {})
        assert post(people.client, "/projects", form, **people.cookie).status_code == 303, name
        assert everyone(people, name) == want, name


def test_new_project_over_mcp_and_rest_makes_a_personal_project_private_unless_told(people):
    """The new_project tool and POST /api/projects: with `private` left out, a personal project is
    private and any other kind open to the workspace; `private` given decides either way."""
    key, client = people.key, people.client
    answer(client, key, "new_project", name="m-personal", kind="personal")
    answer(client, key, "new_project", name="m-shared-personal", kind="personal", private=False)
    answer(client, key, "new_project", name="m-repo", kind="repo")
    answer(client, key, "new_project", name="m-general")
    answer(client, key, "new_project", name="m-private-repo", kind="repo", private=True)
    for name, body in (
        ("r-personal", {"kind": "personal"}),
        ("r-shared-personal", {"kind": "personal", "private": False}),
        ("r-repo", {"kind": "repo"}),
        ("r-general", {}),
        ("r-private-repo", {"kind": "repo", "private": True}),
    ):
        r = client.post("/api/projects", headers={"x-api-key": key}, json={"name": name, **body})
        assert r.status_code == 201, name
    for door in ("m", "r"):
        assert everyone(people, f"{door}-personal") == "none", door
        assert everyone(people, f"{door}-shared-personal") == "write", door
        assert everyone(people, f"{door}-repo") == "write", door
        assert everyone(people, f"{door}-general") == "write", door
        assert everyone(people, f"{door}-private-repo") == "none", door
    seen = {b.name for b in people.brook.list_projects()}
    assert "m-personal" not in seen and "r-personal" not in seen
    assert {"m-shared-personal", "r-repo", "m-general"} <= seen
    tools = rpc(client, key, "tools/list").json()["result"]["tools"]
    [new_project] = [t for t in tools if t["name"] == "new_project"]
    said = new_project["inputSchema"]["properties"]["private"]["description"]
    assert "Left out: the kind decides; a personal project is private" in said


# Who reaches a private project: new_project.html, access.html and the new_project tool


THE_OPERATORS = "the keys and sources that belong to whoever runs the server"


def test_who_reaches_a_private_project_names_the_operators_keys_everywhere_it_is_said(people):
    """new_project.html, access.html and the new_project tool's `private` name, besides the maker,
    the people named and the owners, the credentials of no person that reach a private
    project too (unit/test_guide_facts_web_unit.py proves that they do)."""
    people.ada.create_project("journal", everyone="none")
    home = people.client.get("/", headers=people.cookie).text
    hint = (
        "Only you, the people you name, and the workspace's owners reach it, along with"
        f" {THE_OPERATORS}, such as a key made on its command line."
    )
    assert hint in home
    access = people.client.get("/b/journal/access", headers=people.cookie).text
    assert f"the workspace's owners reach it, along with {THE_OPERATORS}" in access
    tools = rpc(people.client, people.key, "tools/list").json()["result"]["tools"]
    [new_project] = [t for t in tools if t["name"] == "new_project"]
    said = new_project["inputSchema"]["properties"]["private"]["description"]
    assert "credentials of no person (the server operator's keys and sources)" in said


def test_a_source_added_on_the_command_line_writes_into_a_private_project(
    tmp_path, monkeypatch, capsys
):
    """The sentences above say a source reaches a private project: one added on the command
    line acts for no person, and writes into a project a person made private."""
    db = tmp_path / "cairn.db"
    store = Store(db)
    ada = store.sign_in("sso", "a", "ada@example.test", "Ada", issuer=ISSUER, join="default")
    ops = store.for_account(ada.account_id, ada.user.id).create_project("ops", everyone="none")
    store.close()
    monkeypatch.setattr(
        cli, "_source_http", lambda: httpx.AsyncClient(transport=httpx.MockTransport(_langfuse))
    )
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", PUBLIC)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", SECRET)
    config = {
        "base_url": "https://langfuse.example.test",
        "projects": ["ops"],
        "public_key_env": "LANGFUSE_PUBLIC_KEY",
        "secret_key_env": "LANGFUSE_SECRET_KEY",
    }
    added = server.main(
        ["source-add", "--db", str(db), "--name", "traces", "--kind", "langfuse",
         "--config", json.dumps(config)]
    )  # fmt: skip
    assert added == 0
    assert server.main(["source-sync", "--db", str(db), "--name", "traces"]) == 0
    assert "1 saved" in capsys.readouterr().out
    store = Store(db)
    try:
        assert store.project_access(ops.id).everyone == "none"
        assert len(store.list_notes(ops.id)) == 1
    finally:
        store.close()


# Who may delete, forget or change who reaches a project: memory.py and web/members.py


OPERATORS_KEY = "a key that belongs to whoever runs the server, such as one made on its command"


def test_the_refusals_to_delete_forget_or_share_name_the_operators_key(people):
    """memory.py NOT_AN_ERASER and web/members.py NOT_THE_MAKER name, besides the maker and
    the owners, a key that belongs to whoever runs the server, which may too
    (tests/acceptance/test_h5b_forget.py)."""
    handbook = people.ada.create_project("handbook")
    noted, _ = people.ada.add_note(handbook.id, "# Rule\n\nQuill ships on Fridays.\n")
    headers = {**people.brook_cookie, **SAME_ORIGIN}
    for path in ("/b/handbook/delete", f"/b/handbook/notes/{noted.id}/forget"):
        r = people.client.post(path, data={"confirm": "handbook"}, headers=headers)
        assert r.status_code == 403, path
        said = html.unescape(r.text)
        assert f"Only the project's maker, the workspace's owners and {OPERATORS_KEY}" in said, path
        assert "can delete this project or forget its notes" in said, path
    r = people.client.post("/b/handbook/access", data={"everyone": "none"}, headers=headers)
    assert r.status_code == 403
    said = html.unescape(r.text)
    assert f"Only the project's maker, the workspace's owners and {OPERATORS_KEY}" in said
    assert "can change who reaches it." in said
    assert people.ada.project_access(handbook.id).everyone == "write"
    assert [c.id for c in people.ada.list_notes(handbook.id)] == [noted.id]


# agents.html: how long an agent stays connected


def test_the_agents_page_says_every_way_a_connection_ends(people):
    """agents.html: a connected agent stays until someone disconnects it, it goes 30 days
    unused (its refresh token's lifetime, cairn.store.grants REFRESH_TOKEN_TTL; swept by
    tests/acceptance/test_h5c_expiry.py), or its person leaves the workspace
    (web/members.py remove); a replaced token used again ends it too
    (tests/acceptance/test_h3_oauth.py::test_refresh_rotates_and_reuse_ends_the_grant)."""
    assert REFRESH_TOKEN_TTL == 30 * 86_400
    page = html.unescape(people.client.get("/agents", headers=people.cookie).text)
    assert (
        "It stays connected until someone disconnects it, it goes 30 days unused, or the"
        " person who approved it leaves the workspace; cairn also ends it if one of its"
        " replaced tokens is used again."
    ) in page
    assert "until you disconnect it" not in page


# "Every project" is every project the person reaches: _reach.html, consent.html, start_connect.html


EVERY_PROJECT_YOU_REACH = "every project you reach in this workspace, now and later"


def test_every_project_is_said_as_every_project_you_reach(people):
    """_reach.html, consent.html and start_connect.html: a grant or key acts for the person
    who made it, so "All projects" is every project that person reaches
    (tests/acceptance/test_t1_members.py), and the person chooses when approving."""
    people.ada.create_project("api", kind="repo")
    client, cookie = people.client, people.cookie
    client_id = client.post(
        "/register",
        json={
            "client_name": "bot",
            "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"],
        },
    ).json()["client_id"]
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "state": "s",
    }
    sent = client.get("/authorize?" + urlencode(params), headers=cookie, follow_redirects=False)
    consent = client.get(sent.headers["location"], headers=cookie).text
    assert f"by default, {EVERY_PROJECT_YOU_REACH}, to read and write." in consent
    assert f'<span class="hint">{EVERY_PROJECT_YOU_REACH}</span>' in consent  # All projects
    keys = client.get("/keys", headers=cookie).text
    assert f'<span class="hint">{EVERY_PROJECT_YOU_REACH}</span>' in keys
    connect = client.get("/start/connect", headers=cookie).text
    assert (
        f"reaches {EVERY_PROJECT_YOU_REACH}; when you approve it, you can choose fewer" in connect
    )
    assert "fewer projects, or read only." in connect
