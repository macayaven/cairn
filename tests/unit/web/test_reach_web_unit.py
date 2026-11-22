"""Unit tests for reach in the web service: what tests/acceptance/test_s1_scopes.py leaves
open about narrowed connections over REST, MCP and the web view."""

import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store
from support.cookie_names import session_cookie

BASE = "http://127.0.0.1:8765"
REDIRECT = "http://127.0.0.1:33418/callback"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
GRANTS = ["authorization_code", "refresh_token"]
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


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
    ids = {name: store.create_project(name).id for name in ("api", "journal")}
    app = create_app(store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:
        yield SimpleNamespace(client=client, store=store, db=db, ids=ids)
    store.close()


def tool(env, key: str, name: str, /, **arguments) -> dict:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    headers = {**MCP_HEADERS, "authorization": f"Bearer {key}"}
    r = env.client.post("/mcp", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()["result"]


# Connections


@pytest.mark.parametrize("reach", [{"projects": ("API",)}, {"access": "read"}])
def test_a_narrowed_key_cannot_open_a_web_session(env, reach):
    """A web session has the whole workspace, so a key that reaches less cannot make one."""
    reach = {**reach, **({"projects": (env.ids["api"],)} if "projects" in reach else {})}
    key = env.store.create_key("narrow", **reach)
    before = dump(env.db)
    r = env.client.post("/login", data={"key": key}, headers=SAME_ORIGIN, follow_redirects=False)
    assert r.status_code == 401 and session_cookie() not in r.headers.get("set-cookie", "")
    assert dump(env.db) == before


def test_a_read_only_key_that_reaches_every_project_reads_all_and_writes_nothing(env):
    key = env.store.create_key("reader", access="read")
    before = dump(env.db)
    listed = tool(env, key, "projects")
    assert listed["isError"] is False
    assert [b["name"] for b in json.loads(listed["content"][0]["text"])["projects"]] == [
        "api",
        "journal",
    ]
    made = tool(env, key, "new_project", name="extra")
    assert made["isError"] is True and "read-only" in made["content"][0]["text"]
    r = env.client.post("/api/projects", headers={"x-api-key": key}, json={"name": "extra"})
    assert r.status_code == 403 and "read-only" in r.json()["error"]
    assert dump(env.db) == before


def test_a_limited_key_names_its_limit_before_validating_a_project_name(env):
    key = env.store.create_key("api-agent", projects=(env.ids["api"],))
    for name in ("not a name", "api", "fresh"):
        r = env.client.post("/api/projects", headers={"x-api-key": key}, json={"name": name})
        assert r.status_code == 403 and "limited" in r.json()["error"], name


def test_a_full_key_still_creates_projects(env):
    key = env.store.create_key("owner")
    r = env.client.post("/api/projects", headers={"x-api-key": key}, json={"name": "fresh"})
    assert r.status_code == 201


# The web view


def sign_in(env) -> None:
    key = env.store.create_key("owner")
    r = env.client.post("/login", data={"key": key}, headers=SAME_ORIGIN, follow_redirects=False)
    assert r.status_code == 303


def as_member(env) -> None:
    """A session for a person who is a plain member of the default workspace."""
    for n, who in enumerate(("owner", "member")):
        signed = env.store.sign_in(
            "github",
            str(n),
            f"{who}@example.test",
            who,
            issuer="https://github.com",
            join="default",
        )
    env.client.cookies.set(session_cookie(), signed.token)


def post(env, path: str, data: dict | None = None):
    return env.client.post(path, data=data or {}, headers=SAME_ORIGIN, follow_redirects=False)


def pending_consent(env):
    """An agent registered and sent to the consent page, by a signed-in owner."""
    c = env.client
    client_id = c.post(
        "/register",
        json={"client_name": "bot", "redirect_uris": [REDIRECT], "grant_types": GRANTS},
    ).json()["client_id"]
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "state": "s",
    }
    sign_in(env)
    r = c.get("/authorize?" + urlencode(params), follow_redirects=False)
    page = c.get(r.headers["location"])
    assert page.status_code == 200
    request = re.search(r'name="request" value="([^"]+)"', page.text).group(1)
    return SimpleNamespace(page=page, request=request)


def code_of(response) -> str:
    assert response.status_code == 303, response.text
    return parse_qs(urlsplit(response.headers["location"]).query)["code"][0]


def test_the_consent_page_offers_the_reach_with_everything_chosen_first(env):
    work = env.store.create_set("work", (env.ids["api"],))
    page = pending_consent(env).page.text
    for label in ("All projects", "Selected projects", "Read and write", "Read only"):
        assert label in page
    assert re.search(r'name="scope" value="all"[^>]*checked', page)
    assert re.search(r'name="access" value="write"[^>]*checked', page)
    assert f'value="{work.id}"' in page and "work" in page


@pytest.mark.parametrize(
    "bad",
    [
        {"scope": "selected"},
        {"scope": "some"},
        {"access": "admin"},
        {"scope": "selected", "projects": ["api"]},  # a name, not an id
    ],
)
def test_a_bad_choice_grants_nothing_and_leaves_the_request_open(env, bad):
    pending = pending_consent(env)
    refused = post(
        env, "/oauth/consent", {"request": pending.request, "decision": "approve", **bad}
    )
    assert refused.status_code == 400 and "location" not in refused.headers
    assert env.store.list_grants() == []
    chosen = {"scope": "selected", "projects": [env.ids["api"]], "access": "read"}
    code_of(
        post(env, "/oauth/consent", {"request": pending.request, "decision": "approve", **chosen})
    )


@pytest.mark.parametrize("scope", [{}, {"scope": "all"}])
def test_ticked_projects_without_selected_projects_are_refused_not_widened(env, scope):
    """The page has no script: a person can tick a project and leave "All projects" chosen. That
    is not taken as every project, nor guessed as the ticked ones: it is 400, open to retry."""
    pending = pending_consent(env)
    ticked = {"request": pending.request, "decision": "approve", "projects": env.ids["api"]}
    refused = post(env, "/oauth/consent", {**ticked, **scope})
    assert refused.status_code == 400 and env.store.list_grants() == []
    code_of(post(env, "/oauth/consent", {**ticked, "scope": "selected"}))
    sign_in(env)
    before = dump(env.db)
    keyed = post(env, "/keys", {"label": "x", "projects": env.ids["api"], **scope})
    assert keyed.status_code == 400 and dump(env.db) == before


def test_a_denial_reads_no_reach(env):
    denied = pending_consent(env)
    r = post(env, "/oauth/consent", {"request": denied.request, "decision": "deny", "scope": "?"})
    assert r.status_code == 303 and "access_denied" in r.headers["location"]


def test_keys_show_their_reach_and_refuse_a_bad_one(env):
    work = env.store.create_set("work", (env.ids["api"],))
    env.store.create_key("ci", projects=(work.id, env.ids["journal"]), access="read")
    sign_in(env)
    listed = env.client.get("/keys").text
    ci = listed[listed.index('<span class="actor-name">ci</span>') :]
    owner = listed[listed.index('<span class="actor-name">owner</span>') :]
    ci, owner = ci[: ci.index("</tr>")], owner[: owner.index("</tr>")]
    assert "<span>set work: api</span>" in ci and "<span>journal</span>" in ci
    assert "Read only" in ci and "every other project" in ci  # and what it cannot reach
    assert "All projects" in owner
    assert "New key" in listed
    before = dump(env.db)
    for bad in (
        {"label": "x", "access": "admin"},
        {"label": "x", "scope": "selected", "projects": "br_000000000000"},
        {"label": "x", "scope": "selected", "projects": "api"},
    ):
        assert post(env, "/keys", bad).status_code == 400, bad
    assert dump(env.db) == before


def test_sets_are_listed_with_their_projects_by_name(env):
    env.store.create_set("work", (env.ids["journal"], env.ids["api"]))
    sign_in(env)
    page = env.client.get("/sets").text
    assert "work" in page and "journal, api" in page
    assert 'action="/sets"' in page


def test_set_forms_refuse_bad_input_and_unknown_sets(env):
    sign_in(env)
    before = dump(env.db)
    assert post(env, "/sets", {"name": "not a name"}).status_code == 400
    assert post(env, "/sets", {"name": "set_x"}).status_code == 400
    assert post(env, "/sets", {"name": "w", "projects": "br_000000000000"}).status_code == 400
    assert post(env, "/sets/set_000000000000", {"projects": env.ids["api"]}).status_code == 404
    assert post(env, "/sets/set_000000000000/delete").status_code == 404
    assert dump(env.db) == before
    made = post(env, "/sets", {"name": "w", "projects": env.ids["api"]})
    assert made.status_code == 303 and made.headers["location"] == "/sets"
    work = env.store.get_set("w")
    assert post(env, "/sets", {"name": work.id}).status_code == 400  # an id is not a name
    assert post(env, "/sets", {"name": "W"}).status_code == 409
    assert post(env, f"/sets/{work.id}", {"projects": "api"}).status_code == 400
    assert post(env, f"/sets/{work.id}").status_code == 303  # nothing ticked: an empty set
    assert env.store.get_set("w").project_ids == ()


def test_only_an_owner_or_admin_manages_sets(env):
    work = env.store.create_set("work", (env.ids["api"],))
    as_member(env)
    before = dump(env.db)
    page = env.client.get("/sets").text
    assert "work" in page and 'action="/sets"' not in page and "/delete" not in page
    for path, data in (
        ("/sets", {"name": "mine"}),
        (f"/sets/{work.id}", {"projects": env.ids["journal"]}),
        (f"/sets/{work.id}/delete", {}),
    ):
        assert post(env, path, data).status_code == 403, path
    for reach in ({"scope": "selected", "projects": env.ids["api"]}, {"access": "read"}):
        assert post(env, "/keys", {"label": "x", **reach}).status_code == 403
    assert dump(env.db) == before
