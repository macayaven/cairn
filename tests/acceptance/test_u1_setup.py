"""U1 acceptance tests (frozen): project kinds, and a setup that starts from the use case.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/kinds.py, src/cairn/store/__init__.py and
src/cairn/server.py.

Kinds
- `cairn.kinds.KINDS` maps an id to a Kind: `id`, `title`, `summary` (one sentence),
  `belongs` and `avoid` (short phrases), `guidance` (what an agent using such a project
  should do), `name_hint` (a suggested project name) and `connects` ("agents" for things a
  person signs in from, "app" for a headless service). The ids are "general", "repo",
  "team", "feature", "meetings", "documents" and "personal". Kinds are data: adding one
  needs no other change.
- A project has a kind: `create_project(name, description="", kind="general")` (ValueError for
  an unknown kind) and `Project.kind`. Projects made before kinds existed are "general".
- Agents see it. The `projects` tool and GET /api/projects give each project's `kind` and
  `guidance`, and list the kinds (`id`, `title`, `summary`) so an agent can choose one;
  `overview` gives the project's `kind` and `guidance`; `new_project` and POST /api/projects take
  an optional `kind`. An unknown kind is refused with the list of valid ones. No tool is
  added or renamed.
- The web view shows a project's kind with what belongs in it, and the home page's new-project
  form has a `kind` select.

Guided setup (a web session; plain HTML forms)
- GET /start asks what cairn will be used for: one option per kind except "general", each
  with a checkbox named `use` (value: the kind's id) and a name field `name-<id>`
  pre-filled with the kind's name hint.
- POST /start creates one project per ticked kind, of that kind, under the given name, and
  redirects (303) to /start/connect. A name that is not valid, or nothing ticked, is 400
  with the form again and a message. A project that already exists with that name and kind is
  kept, so the form can be sent twice.
- GET /start/connect shows, for each project of the workspace, how to connect what will use
  it. For kinds that connect "agents": the MCP URL and the `claude mcp add` and
  `codex mcp add` commands. For kinds that connect an "app": a form posting to /keys that
  creates a key reaching only that project, after which the key is shown once together with
  the two environment lines an app needs (CAIRN_URL and CAIRN_API_KEY).
- A workspace with no project is pointed to /start from the home page.
- Opening these pages writes nothing.
"""

import json
import re
import sqlite3
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.kinds import KINDS
from cairn.server import create_app
from cairn.store import Store

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
IDS = ["general", "repo", "team", "feature", "meetings", "documents", "personal"]


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
    key = store.create_key("owner")
    app = create_app(store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:
        yield SimpleNamespace(client=client, store=store, db=db, key=key)
    store.close()


class KeyForm(HTMLParser):
    """The fields of the first form that posts to /keys."""

    def __init__(self) -> None:
        super().__init__()
        self.inside, self.done = False, False
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form" and not self.done:
            self.inside = attributes.get("action") == "/keys"
        elif tag == "input" and self.inside and attributes.get("name"):
            self.fields[attributes["name"]] = attributes.get("value") or ""

    def handle_endtag(self, tag):
        if tag == "form" and self.inside:
            self.inside, self.done = False, True


def key_form(html: str) -> dict[str, str]:
    parser = KeyForm()
    parser.feed(html)
    assert parser.fields, "no form posting to /keys"
    return parser.fields


def sign_in(env) -> None:
    r = env.client.post(
        "/login", data={"key": env.key}, headers=SAME_ORIGIN, follow_redirects=False
    )
    assert r.status_code == 303


def post(env, path: str, data: dict):
    return env.client.post(path, data=data, headers=SAME_ORIGIN, follow_redirects=False)


def tool(env, name: str, /, **arguments) -> dict:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    r = env.client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": env.key}, json=body)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def call(env, name: str, /, **arguments) -> dict:
    result = tool(env, name, **arguments)
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# Kinds


def test_kinds_are_data_with_everything_a_person_and_an_agent_need():
    assert list(KINDS) == IDS
    for kind_id, kind in KINDS.items():
        assert kind.id == kind_id
        assert kind.title and kind.summary.endswith(".") and len(kind.summary) <= 160
        assert kind.guidance and len(kind.guidance) <= 900
        assert kind.connects in ("agents", "app")
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", kind.name_hint), kind_id
        if kind_id != "general":
            assert kind.belongs and kind.avoid, kind_id
    assert KINDS["feature"].connects == "app" and KINDS["repo"].connects == "agents"
    # Every kind tells agents never to store secrets.
    assert all("secret" in kind.guidance.lower() for kind in KINDS.values())
    titles = [kind.title for kind in KINDS.values()]
    assert len(set(titles)) == len(titles)


def test_a_project_has_a_kind(env):
    plain = env.store.create_project("scratch")
    repo = env.store.create_project("payments-api", "the payments service", kind="repo")
    assert (plain.kind, repo.kind) == ("general", "repo")
    assert env.store.get_project("payments-api").kind == "repo"
    assert [b.kind for b in env.store.list_projects()] == ["repo", "general"]
    with pytest.raises(ValueError):
        env.store.create_project("other", kind="spreadsheet")
    assert env.store.get_project("other") is None


def test_agents_see_each_projects_kind_and_guidance(env):
    env.store.create_project("scratch")
    made = call(env, "new_project", name="story-bible", description="characters", kind="feature")
    assert made["name"] == "story-bible"

    answer = call(env, "projects")
    by_name = {b["name"]: b for b in answer["projects"]}
    assert by_name["story-bible"]["kind"] == "feature"
    assert by_name["story-bible"]["guidance"] == KINDS["feature"].guidance
    assert by_name["scratch"]["kind"] == "general"
    assert [k["id"] for k in answer["kinds"]] == IDS
    assert answer["kinds"][1] == {
        "id": "repo",
        "title": KINDS["repo"].title,
        "summary": KINDS["repo"].summary,
    }
    overview = call(env, "overview", project="story-bible")
    assert (overview["kind"], overview["guidance"]) == ("feature", KINDS["feature"].guidance)

    refused = tool(env, "new_project", name="other", kind="spreadsheet")
    assert refused["isError"] is True
    assert all(kind_id in refused["content"][0]["text"] for kind_id in IDS)

    headers = {"x-api-key": env.key}
    rest = env.client.get("/api/projects", headers=headers).json()
    assert rest == answer
    created = env.client.post(
        "/api/projects", headers=headers, json={"name": "standups", "kind": "meetings"}
    )
    assert created.status_code == 201 and env.store.get_project("standups").kind == "meetings"
    bad = env.client.post("/api/projects", headers=headers, json={"name": "x", "kind": "nope"})
    assert bad.status_code == 400 and "meetings" in bad.json()["error"]

    names = {t["name"] for t in tools_list(env)}
    assert names == {
        "projects", "new_project", "note", "recall", "page", "evidence", "overview", "graph"
    }  # fmt: skip


def tools_list(env) -> list:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    r = env.client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": env.key}, json=body)
    return r.json()["result"]["tools"]


def test_the_web_view_shows_what_a_project_is_for(env):
    env.store.create_project("payments-api", kind="repo")
    sign_in(env)
    page = env.client.get("/b/payments-api").text
    assert KINDS["repo"].title in page
    assert all(phrase in page for phrase in KINDS["repo"].belongs)
    home = env.client.get("/").text
    select = re.search(r'<select\b[^>]*name="kind".*?</select>', home, re.S).group(0)
    assert re.findall(r'<option\b[^>]*value="([^"]*)"', select) == IDS
    made = post(env, "/projects", {"name": "handbook", "kind": "team"})
    assert made.status_code == 303 and env.store.get_project("handbook").kind == "team"
    assert post(env, "/projects", {"name": "other", "kind": "nope"}).status_code == 400


# Guided setup


def test_setup_starts_from_the_use_case(env):
    sign_in(env)
    assert 'href="/start"' in env.client.get("/").text  # no project yet: the way in
    before = dump(env.db)
    page = env.client.get("/start")
    assert page.status_code == 200 and "<script" not in page.text.lower()
    ticks = re.findall(r'<input\b[^>]*name="use"[^>]*value="([^"]+)"', page.text)
    assert ticks == IDS[1:]  # every kind but "general"
    for kind_id in IDS[1:]:
        kind = KINDS[kind_id]
        assert kind.title in page.text and kind.summary in page.text
        field = re.search(rf'<input\b[^>]*name="name-{kind_id}"[^>]*>', page.text).group(0)
        assert f'value="{kind.name_hint}"' in field
    assert dump(env.db) == before

    nothing = post(env, "/start", {})
    assert nothing.status_code == 400 and 'role="alert"' in nothing.text
    invalid = post(env, "/start", {"use": ["repo"], "name-repo": "not a name"})
    assert invalid.status_code == 400 and 'role="alert"' in invalid.text
    assert env.store.list_projects() == []

    chosen = {
        "use": ["repo", "feature"],
        "name-repo": "payments-api",
        "name-feature": "story-bible",
        "name-team": "ignored-because-not-ticked",
    }
    done = post(env, "/start", chosen)
    assert done.status_code == 303 and done.headers["location"] == "/start/connect"
    assert {b.name: b.kind for b in env.store.list_projects()} == {
        "payments-api": "repo",
        "story-bible": "feature",
    }
    again = post(env, "/start", chosen)  # sending the form twice changes nothing
    assert again.status_code == 303 and len(env.store.list_projects()) == 2
    assert 'href="/start"' not in env.client.get("/").text


def test_setup_ends_with_a_working_connection_for_each_project(env):
    sign_in(env)
    chosen = {"use": ["repo", "feature"], "name-repo": "payments-api"}
    post(env, "/start", {**chosen, "name-feature": "story-bible"})
    before = dump(env.db)
    page = env.client.get("/start/connect")
    assert page.status_code == 200
    assert dump(env.db) == before
    text = page.text
    # A repository's project is used by agents a person signs in from: the URL is enough.
    assert f"claude mcp add --transport http cairn {BASE}/mcp" in text
    assert f"codex mcp add cairn --url {BASE}/mcp" in text
    assert "payments-api" in text and "story-bible" in text

    # A feature's project is used by an app: it gets a key that reaches only that project.
    bible = env.store.get_project("story-bible")
    fields = key_form(text)
    assert fields["scope"] == "selected" and fields["projects"] == bible.id
    assert fields["access"] == "write" and fields["label"]
    created = post(env, "/keys", fields)
    assert created.status_code == 200
    key = next(
        k for k in re.findall(r"[A-Za-z0-9_-]{40,}", created.text) if env.store.resolve_key(k)
    )
    who = env.store.resolve_key(key)
    assert (who.projects, who.access) == ((bible.id,), "write")
    assert f"CAIRN_URL={BASE}" in created.text and f"CAIRN_API_KEY={key}" in created.text

    # The app can use it at once, and only for its own project.
    headers = {"x-api-key": key}
    noted = env.client.post(
        f"/api/projects/{bible.id}/notes",
        headers=headers,
        json={"text": "# Mira\n\nMira is nine and always wears a red scarf.\n"},
    )
    assert noted.status_code == 201
    found = env.client.get("/api/search?q=red+scarf", headers=headers).json()["hits"]
    assert found and found[0]["project"] == "story-bible"
    listed = env.client.get("/api/projects", headers=headers).json()["projects"]
    assert [b["name"] for b in listed] == ["story-bible"]
