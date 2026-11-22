"""Unit tests for project kinds and the guided setup: what tests/acceptance/test_u1_setup.py
leaves open. The setup's choices for names that clash, uses that are not offered, a member
who may not make keys, a server without OAuth, and the pages that lead a new person to it."""

import html
import json
import re
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.kinds import KINDS
from cairn.server import create_app
from cairn.store import ProjectTaken, Store
from support.cookie_names import session_cookie

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def serve(tmp_path, public_url: str | None = BASE):
    db = tmp_path / "cairn.db"
    store = Store(db)
    key = store.create_key("owner")
    client = TestClient(create_app(store, public_url=public_url), base_url=BASE)
    return SimpleNamespace(client=client, store=store, db=db, key=key)


@pytest.fixture
def env(tmp_path):
    env = serve(tmp_path)
    with env.client:
        yield env
    env.store.close()


def sign_in(env) -> None:
    r = post(env, "/login", {"key": env.key})
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


def post(env, path: str, data: dict):
    return env.client.post(path, data=data, headers=SAME_ORIGIN, follow_redirects=False)


def tool(env, key: str, name: str, /, **arguments) -> dict:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    r = env.client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": key}, json=body)
    assert r.status_code == 200, r.text
    return r.json()["result"]


# Kinds


def test_what_people_read_of_a_kind_shows_in_html_as_it_is_written():
    """Titles, summaries and phrases are matched against pages as written: no quote marks,
    ampersands or angle brackets, which the template turns into entities, and no dashes
    standing in for punctuation. Titles and summaries are in sentence case; phrases are
    list items, lower case."""
    for kind in KINDS.values():
        for text in (kind.title, kind.summary, *kind.belongs, *kind.avoid):
            assert not set(text) & set("'\"&<>\N{EM DASH}\N{EN DASH}"), (kind.id, text)
            assert text == text.strip(), (kind.id, text)
        assert kind.title[0].isupper() and kind.summary[0].isupper(), kind.id
        assert all(p[0].islower() for p in (*kind.belongs, *kind.avoid)), kind.id
        assert "\N{EM DASH}" not in kind.guidance, kind.id
    hints = [kind.name_hint for kind in KINDS.values()]
    assert len(set(hints)) == len(hints)


# Kinds over MCP and REST


def test_a_new_project_answers_with_its_kind_and_guidance(env):
    made = tool(env, env.key, "new_project", name="handbook", kind="team")
    answer = json.loads(made["content"][0]["text"])
    assert (answer["name"], answer["kind"]) == ("handbook", "team")
    assert answer["guidance"] == KINDS["team"].guidance
    plain = env.client.post("/api/projects", headers={"x-api-key": env.key}, json={"name": "core"})
    assert plain.status_code == 201 and plain.json()["kind"] == "general"


def test_a_kind_defaults_only_when_absent_and_is_refused_alike_over_rest_and_mcp(env):
    api = {"x-api-key": env.key}
    for n, body in enumerate(({}, {"kind": None})):  # absent or null: general
        made = env.client.post("/api/projects", headers=api, json={"name": f"core{n}", **body})
        assert made.status_code == 201 and made.json()["kind"] == "general"
    before = dump(env.db)
    for kind in ("", "spreadsheet"):
        rest = env.client.post("/api/projects", headers=api, json={"name": "x", "kind": kind})
        mcp = tool(env, env.key, "new_project", name="x", kind=kind)
        assert rest.status_code == 400 and mcp["isError"] is True, kind
        said = rest.json()["error"]
        assert said in mcp["content"][0]["text"] and all(k in said for k in KINDS), said
    assert dump(env.db) == before


def test_a_limited_key_hears_limited_whatever_kind_it_asks_for(env):
    """Permission comes before the kind's check, as it comes before the name's."""
    project = env.store.create_project("api")
    limited = env.store.create_key("worker", projects=(project.id,))
    refused = tool(env, limited, "new_project", name="other", kind="spreadsheet")
    assert refused["isError"] is True and "limited" in refused["content"][0]["text"]
    bad = env.client.post(
        "/api/projects", headers={"x-api-key": env.key}, json={"name": "x", "kind": 7}
    )
    assert bad.status_code == 400


def test_a_project_of_a_kind_no_longer_shipped_reads_as_general(env):
    env.store.create_project("old")
    con = sqlite3.connect(env.db)
    with con:
        con.execute("UPDATE projects SET kind = 'retired'")
    con.close()
    (listed,) = env.client.get("/api/projects", headers={"x-api-key": env.key}).json()["projects"]
    assert (listed["kind"], listed["guidance"]) == ("general", KINDS["general"].guidance)
    sign_in(env)
    assert env.client.get("/b/old").status_code == 200


# The web view


def test_the_project_page_says_what_to_keep_out_and_the_home_page_names_each_kind(env):
    env.store.create_project("payments-api", kind="repo")
    sign_in(env)
    page = env.client.get("/b/payments-api").text
    assert all(phrase in page for phrase in KINDS["repo"].avoid)
    assert KINDS["repo"].title in env.client.get("/").text


def test_a_refused_new_project_keeps_the_kind_that_was_chosen(env):
    sign_in(env)
    r = post(env, "/projects", {"name": "not a name", "kind": "meetings"})
    assert r.status_code == 400 and 'role="alert"' in r.text
    assert re.search(r'<option\b[^>]*value="meetings"[^>]*selected', r.text)


# The guided setup


def ticks(text: str) -> list[str]:
    return re.findall(r'<input\b[^>]*name="use"[^>]*value="([^"]+)"[^>]*checked', text)


def test_one_name_for_two_uses_is_refused_and_nothing_is_made(env):
    sign_in(env)
    before = dump(env.db)
    r = post(env, "/start", {"use": ["repo", "team"], "name-repo": "core", "name-team": "CORE"})
    assert r.status_code == 400 and 'role="alert"' in r.text
    assert dump(env.db) == before
    assert ticks(r.text) == ["repo", "team"]  # what was chosen and typed is kept
    assert 'value="CORE"' in r.text


def test_a_name_taken_by_a_project_of_another_kind_is_refused_and_nothing_is_made(env):
    env.store.create_project("handbook", kind="general")
    sign_in(env)
    before = dump(env.db)
    chosen = {"use": ["repo", "team"], "name-repo": "payments-api", "name-team": "Handbook"}
    r = post(env, "/start", chosen)
    assert r.status_code == 409 and 'role="alert"' in r.text
    assert "The project name &#39;Handbook&#39; is not available" in r.text  # the name typed
    assert dump(env.db) == before


@pytest.mark.parametrize("use", ["general", "spreadsheet"])
def test_a_use_the_page_does_not_offer_is_refused(env, use):
    sign_in(env)
    before = dump(env.db)
    r = post(env, "/start", {"use": [use], f"name-{use}": "core"})
    assert r.status_code == 400 and 'role="alert"' in r.text
    assert dump(env.db) == before


def test_two_setups_at_once_make_each_project_once_and_both_succeed(tmp_path):
    """Two servers on one file, each sent the same form at the same moment."""
    db = tmp_path / "cairn.db"
    stores = [Store(db), Store(db)]
    wanted = [("payments-api", "repo"), ("story-bible", "feature")]
    start = threading.Barrier(2)
    made: list[list[str]] = []
    failed: list[BaseException] = []

    def setup(store: Store) -> None:
        start.wait()
        try:
            made.append([project.id for project in store.create_projects(wanted)])
        except BaseException as error:  # reported below, not swallowed
            failed.append(error)

    threads = [threading.Thread(target=setup, args=(store,)) for store in stores]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    try:
        assert failed == [] and len(made) == 2 and made[0] == made[1]
        assert [(b.name, b.kind) for b in stores[0].list_projects()] == [
            ("payments-api", "repo"),
            ("story-bible", "feature"),
        ]
        assert [e.action for e in stores[0].list_audit()] == ["project.created"] * 2
    finally:
        for store in stores:
            store.close()


def test_a_project_made_meanwhile_with_the_same_kind_is_kept(env, monkeypatch):
    """Another post made story-bible after this one looked and before it wrote."""
    other = Store(env.db)
    looked = Store.get_project

    def meanwhile(self, ref):
        found = looked(self, ref)
        if ref == "story-bible" and looked(other, "story-bible") is None:
            other.create_project("story-bible", kind="feature")
        return found

    monkeypatch.setattr(Store, "get_project", meanwhile)
    sign_in(env)
    r = post(env, "/start", {"use": ["feature"], "name-feature": "story-bible"})
    other.close()
    assert r.status_code == 303 and r.headers["location"] == "/start/connect"
    assert [(b.name, b.kind) for b in env.store.list_projects()] == [("story-bible", "feature")]


def test_a_setup_that_fails_halfway_makes_no_project(env, monkeypatch):
    recorded = Store._audit
    calls = []

    def fail_on_the_second_project(self, con, action, **kwargs):
        if action == "project.created":
            calls.append(kwargs.get("project"))
            if len(calls) == 2:
                raise RuntimeError("disk full")
        return recorded(self, con, action, **kwargs)

    sign_in(env)
    before = dump(env.db)
    monkeypatch.setattr(Store, "_audit", fail_on_the_second_project)
    with pytest.raises(RuntimeError, match="disk full"):
        post(env, "/start", {"use": ["repo", "team"], "name-repo": "api", "name-team": "ops"})
    monkeypatch.undo()
    assert dump(env.db) == before and env.store.list_projects() == []


def test_create_projects_keeps_the_same_kind_and_refuses_another_with_nothing_made(env):
    kept = env.store.create_project("handbook", kind="team")
    first = env.store.create_projects([("handbook", "team"), ("api", "repo")])
    assert first[0].id == kept.id and [b.kind for b in first] == ["team", "repo"]
    before = dump(env.db)
    for wanted in (
        [("web", "repo"), ("HANDBOOK", "meetings")],  # taken by a project of another kind
        [("web", "repo"), ("Web", "team")],  # one name twice
        [("web", "repo"), ("br_web", "team")],  # not a name
        [("web", "repo"), ("notes", "spreadsheet")],  # not a kind
    ):
        with pytest.raises(ValueError):
            env.store.create_projects(wanted)
        assert dump(env.db) == before, wanted
    with pytest.raises(ProjectTaken) as taken:
        env.store.create_projects([("web", "repo"), ("HANDBOOK", "meetings")])
    assert (taken.value.project.name, taken.value.project.kind) == ("handbook", "team")


def form_size(env) -> int:
    return len(env.client.get("/start").content)


def test_a_huge_name_is_refused_and_echoed_only_as_far_as_a_name_goes(env):
    sign_in(env)
    huge = "a" * (100 * 1024)
    r = post(env, "/start", {"use": ["repo", "team"], "name-repo": huge, "name-team": huge + "!"})
    assert r.status_code == 400 and 'role="alert"' in r.text
    assert len(r.content) < form_size(env) + 1024  # the form, a message, two cut names
    assert "a" * 65 not in r.text and "a" * 64 + "\N{HORIZONTAL ELLIPSIS}" in r.text
    assert env.store.list_projects() == []


def test_ten_thousand_unknown_fields_are_refused_small_and_never_echoed(env):
    """Past 1,000 fields the form parser refuses the body before the route reads it."""
    sign_in(env)
    data = {f"field-{n}": "x" * 10 for n in range(10_000)}
    uses = [f"use{n:05d}" for n in range(10_000)]
    r = post(env, "/start", {**data, "use": ["repo", *uses], "name-repo": "api"})
    assert r.status_code == 400 and len(r.content) < form_size(env) + 1024
    assert "use0" not in r.text and "field-" not in r.text
    assert env.store.list_projects() == []


def test_unknown_uses_and_fields_are_ignored_or_refused_without_being_echoed(env):
    sign_in(env)
    data = {f"field-{n}": "x" * 10 for n in range(400)}
    uses = [f"use{n:05d}" for n in range(500)]
    r = post(env, "/start", {**data, "use": ["repo", *uses], "name-repo": "api"})
    assert r.status_code == 400 and 'role="alert"' in r.text  # the parser let it through
    assert len(r.content) < form_size(env) + 1024
    assert "use0" not in r.text and "field-" not in r.text
    assert env.store.list_projects() == []
    ignored = post(env, "/start", {**data, "use": ["repo"], "name-repo": "api"})
    assert ignored.status_code == 303  # fields the form does not have change nothing
    assert [(b.name, b.kind) for b in env.store.list_projects()] == [("api", "repo")]


def test_a_name_beginning_like_an_id_is_refused(env):
    sign_in(env)
    for name in ("br_payments", "BR_Payments"):
        r = post(env, "/start", {"use": ["repo"], "name-repo": name})
        assert r.status_code == 400 and env.store.list_projects() == []
        said = html.unescape(r.text)
        why = 'a name cannot begin with "br_", because that is how ids begin.'
        assert f"{name!r} is not a valid name for Code repository: {why}" in said
        assert "starting with a letter or digit" not in said  # the rule it meets is not quoted
    r = post(env, "/start", {"use": ["repo"], "name-repo": "not a name"})
    assert "starting with a letter or digit" in html.unescape(r.text)


def test_new_project_says_why_a_name_beginning_like_an_id_is_refused(env):
    """The refusal over MCP and REST names the reason, not a rule the name meets."""
    key = env.store.create_key("agent")
    said = (
        "'br_payments' is not a valid project name:"
        ' a name cannot begin with "br_", because that is how ids begin'
    )
    r = env.client.post("/api/projects", headers={"x-api-key": key}, json={"name": "br_payments"})
    assert (r.status_code, r.json()["error"]) == (400, said)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "new_project", "arguments": {"name": "br_payments"}}}  # fmt: skip
    headers = {"accept": "application/json, text/event-stream", "x-api-key": key}
    result = env.client.post("/mcp", headers=headers, json=body).json()["result"]
    assert result["isError"] is True and result["content"][0]["text"].endswith(said)
    r = env.client.post("/api/projects", headers={"x-api-key": key}, json={"name": "-x"})
    assert "starting with a letter or digit" in r.json()["error"]
    assert env.store.list_projects() == []


def test_connect_shows_a_member_who_makes_an_apps_key_instead_of_a_form(env):
    env.store.create_project("story-bible", kind="feature")
    env.store.create_project("payments-api", kind="repo")
    as_member(env)
    before = dump(env.db)
    page = env.client.get("/start/connect")
    assert page.status_code == 200 and dump(env.db) == before
    assert 'action="/keys"' not in page.text
    assert "owner or an admin" in page.text and "story-bible" in page.text
    assert f"claude mcp add --transport http cairn {BASE}/mcp" in page.text


def test_connect_with_no_project_points_back_to_start(env):
    sign_in(env)
    page = env.client.get("/start/connect").text
    assert 'href="/start"' in page and 'action="/keys"' not in page


def test_connect_without_oauth_sends_agents_to_a_key(tmp_path):
    env = serve(tmp_path, public_url=None)
    with env.client:
        env.store.create_project("payments-api", kind="repo")
        sign_in(env)
        r = env.client.get("/start/connect")
        assert r.status_code == 200 and "payments-api" in r.text
        assert "claude mcp add" not in r.text and "https://&lt;your-host&gt;/mcp" in r.text
        assert "sign in from" not in r.text  # no heading promises a sign-in it lacks
    env.store.close()


def test_welcome_leads_to_start_until_the_workspace_has_a_project(env):
    sign_in(env)
    assert 'href="/start"' in env.client.get("/welcome").text
    env.store.create_project("core")
    welcome = env.client.get("/welcome").text
    assert 'href="/start"' not in welcome and f"{BASE}/mcp" in welcome


# The guard, as add_note enforces it, over what the guided setup and the system tests write

TOKEN = "ghp_" + "Zq7xR2" * 6  # built at run time: nothing in this file looks like a live key
JOURNEY_NOTE = "# Mira\n\nMira is nine and always wears a red scarf.\n"  # test_journey's note


def app_key(env, project_name: str) -> str:
    """The key the app form on /start/connect makes for one project, read from the page."""
    form = env.client.get("/start/connect").text
    project_id = re.search(r'name="projects" value="([^"]+)"', form).group(1)
    assert project_id == env.store.get_project(project_name).id
    made = post(
        env,
        "/keys",
        {
            "label": f"{project_name} app",
            "scope": "selected",
            "projects": project_id,
            "access": "write",
        },
    )
    assert made.status_code == 200
    return re.search(r"<pre>([^<]+)</pre>", made.text).group(1).strip()


@pytest.mark.parametrize("policy", ["off", "secrets", "secrets+personal"])
def test_an_app_key_made_on_connect_writes_under_the_workspace_guard(env, policy):
    env.store.set_setting("guard.policy", policy)
    sign_in(env)
    made = post(env, "/start", {"use": ["feature"], "name-feature": "story-bible"})
    assert made.status_code == 303
    key = app_key(env, "story-bible")
    notes = "/api/projects/story-bible/notes"
    kept = env.client.post(notes, headers={"x-api-key": key}, json={"text": JOURNEY_NOTE})
    assert kept.status_code == 201, kept.text
    before = dump(env.db)
    secret = {"text": f"# Mira\n\nThe image service takes {TOKEN}.\n"}
    sent = env.client.post(notes, headers={"x-api-key": key}, json=secret)
    if policy == "off":
        assert sent.status_code == 201
    else:
        assert sent.status_code == 422 and TOKEN not in sent.text
        assert TOKEN not in dump(env.db) and dump(env.db) != before  # only note.refused
        assert env.store.list_audit()[0].action == "note.refused"


def test_an_app_key_from_connect_is_shown_once_with_its_two_lines(env):
    env.store.create_project("story-bible", kind="feature")
    sign_in(env)
    form = env.client.get("/start/connect").text
    project_id = re.search(r'name="projects" value="([^"]+)"', form).group(1)
    made = post(
        env,
        "/keys",
        {
            "label": "story-bible app",
            "scope": "selected",
            "projects": project_id,
            "access": "write",
        },
    )
    assert made.status_code == 200 and made.headers["cache-control"] == "no-store"
    key = re.search(r"<pre>([^<]+)</pre>", made.text).group(1).strip()
    assert made.text.count(key) == 2  # the key alone, then in the two lines
    assert made.text.count("CAIRN_API_KEY=") == 1 and made.text.count("CAIRN_URL=") == 1
    assert key not in dump(env.db)  # only its digest is kept
    for page in ("/start/connect", "/keys", "/start/connect"):  # going back, and back again
        later = env.client.get(page)
        assert later.status_code == 200 and later.headers["cache-control"] == "no-store"
        assert key not in later.text and f"CAIRN_API_KEY={key}" not in later.text, page
        if page == "/keys":
            assert "CAIRN_API_KEY=" not in later.text


def test_what_fill_writes_passes_the_strictest_guard(env):
    """tests/system/test_invariants.py's fill() writes its note before it sets the strictest
    policy; the same note, label, origin and via pass under that policy too."""
    env.store.set_setting("guard.policy", "secrets+personal")
    project = env.store.create_project(
        "zebrafinch-a-project", "zebrafinch a description", kind="repo"
    )
    note, _ = env.store.add_note(
        project.id,
        "# zebrafinch a note\n\nThe zebrafinch a fact is 42.\n",
        label="zebrafinch-a-label",
        source_ref="zebrafinch:a:ref",
        via="system-test",
    )
    assert env.store.list_notes(project.id) == [note]


def test_a_new_key_comes_with_the_two_lines_an_app_needs(tmp_path):
    env = serve(tmp_path, public_url=None)
    with env.client:
        sign_in(env)
        made = post(env, "/keys", {"label": "worker"})
        assert made.status_code == 200
        key = re.search(r"<pre>([^<]+)</pre>", made.text).group(1).strip()
        assert env.store.resolve_key(key) is not None  # the first block is the key alone
        assert f"CAIRN_API_KEY={key}" in made.text and "CAIRN_URL=https://" in made.text
        assert key not in env.client.get("/keys").text
    env.store.close()
