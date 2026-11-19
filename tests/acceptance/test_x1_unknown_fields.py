"""X1 acceptance tests (frozen): a request never loses a field silently.

Written before implementation. Implementers must not edit this file. The contract goes in
the docstrings of src/cairn/server.py (REST, MCP) and in the connect guide.

A caller who misspells a field must hear about it: a note sent with `sorce_ref` must not be
stored without its reference, and a search sent with `projct` must not search every
project. So:

1. REST bodies. Every address under /api that reads a JSON body refuses a key it does not
   take, before anything is stored or changed: 400, the status the API already uses for a
   malformed request, and the answer is exactly {"error": <sentence>}. The sentence names
   the unknown key, or the unknown keys sorted, and lists the keys the address takes, in
   the order the address declares them:
       unknown key 'sorce_ref'; this address takes 'text', 'title', 'topic', 'label' and
       'source_ref'
       unknown keys 'lable' and 'sorce_ref'; this address takes ...
2. REST query parameters. Every address under /api, whatever its method, refuses a query
   parameter it does not take, in the same way and with the same status:
       unknown query parameter 'projct'; this address takes 'q', 'project' and 'limit'
   and an address that takes none says so:
       unknown query parameter 'zorp'; this address takes no query parameters
   Each parameter an address takes keeps its own rules as they were.
3. MCP. A tool call with an argument the tool does not have is refused as a tool error
   (isError true), whose text ends with the sentence
       unknown argument 'sorce_ref'; note takes 'project', 'text', 'title', 'topic', 'label'
       and 'source_ref'
   (or `projects takes no arguments`), the arguments listed in the order of the tool's
   input schema. It is recorded on the access log as an invalid call is: one record, its
   caller, action "invalid", no project, status 400. Each tool's input schema says so in
   advance: "additionalProperties": false.
4. Nothing changes for a request that refuses: the database is the same before and after,
   byte for byte as its dump reads.
5. Nothing a correct caller sends changes: a request that uses every key, parameter and
   argument the address or the tool takes succeeds as before.
6. What stays as it is: the web pages and their forms, and the OAuth addresses, which are
   outside /api and /mcp, still ignore a parameter they do not know.
7. A refusal names enough to find the mistake, and no more: whatever is sent, it shows at
   most the first 5 unknown names in sorted order and says how many more there are
   ("... 'zorp004' and 495 more;"), and a name longer than 64 characters is cut, shown
   as its first 64 characters in quotes followed by "..." ('zzz...zzz'...). The names
   the address or the tool takes are listed in full. So the whole refusal stays under
   1,000 characters, over REST (a body key, a query parameter) and over MCP (an
   argument), sent a 100,000-character name (60,000 for a query parameter: the longest
   query an HTTP client sends) or 500 unknown names, and neither the access log nor the
   MCP server's log holds a long name in full.

The addresses and the tools are not listed here by hand. The addresses are every route of
the app's route table under /api (HEAD is GET's answer without its body, so GET stands for
it); the tools are the MCP server's own list. Each has one valid request below, with every
key, parameter or argument it takes; an address or a tool that has none fails the test that
says so, so a route or a tool added later is covered or named. Every name is made up.
"""

import asyncio
import logging
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.routing import Route
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store
from cairn.web.mcp import build_mcp

BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
NOTE = "# Quill runbook\n\nQuill retries 3 times before failing.\n"
EXTRA = "zorp_sorce"  # a key, a parameter or an argument nothing takes


def _is_api(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _api_routes() -> list[tuple[str, str]]:
    """(method, path) of every route under /api in the app's route table, HEAD aside."""
    store = Store(":memory:")
    try:
        app = create_app(store, public_url=BASE)
    finally:
        store.close()
    found: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not _is_api(path):
            continue
        assert isinstance(route, Route), f"{path}: under /api but not a Route ({route!r})"
        found.update((method, path) for method in route.methods or () if method != "HEAD")
    return sorted(found)


def _tool_names() -> list[str]:
    return sorted(tool.name for tool in asyncio.run(build_mcp().list_tools()))


API_ROUTES = _api_routes()
TOOL_NAMES = _tool_names()

# One valid request per address: its query and its body (None: it reads no body), each with
# every parameter or key the address takes, in the order the address declares them, and the
# status it answers. Path parameters are filled from the seeded world (`fill`).
VALID: dict[tuple[str, str], dict] = {
    ("GET", "/api/account/export"): {"query": {}, "body": None, "status": 200},
    ("GET", "/api/projects"): {"query": {}, "body": None, "status": 200},
    ("POST", "/api/projects"): {
        "query": {},
        "body": {"name": "gamma", "description": "third", "kind": "general", "private": False},
        "status": 201,
    },
    ("DELETE", "/api/projects/{project}"): {
        "query": {"confirm": "alpha"},
        "body": None,
        "status": 200,
    },
    ("POST", "/api/projects/{project}/notes"): {
        "query": {},
        "body": {
            "text": "# Lumen runbook\n\nLumen waits 5 minutes between tries.\n",
            "title": "Lumen runbook",
            "topic": "Lumen runbook",
            "label": "ops",
            "source_ref": "desk:41",
        },
        "status": 201,
    },
    ("DELETE", "/api/projects/{project}/notes/{note_id}"): {
        "query": {},
        "body": None,
        "status": 200,
    },
    ("GET", "/api/projects/{project}/pages"): {
        "query": {"limit": "10", "after": "a"},
        "body": None,
        "status": 200,
    },
    ("GET", "/api/projects/{project}/pages/{slug}"): {
        "query": {"version": "1", "limit": "5", "before": "2"},
        "body": None,
        "status": 200,
    },
    ("GET", "/api/projects/{project}/pages/{slug}/claims/{claim_id}/evidence"): {
        "query": {"version": "1"},  # E1
        "body": None,
        "status": 200,
    },
    ("GET", "/api/search"): {
        "query": {"q": "quill", "project": "alpha", "limit": "3"},
        "body": None,
        "status": 200,
    },
    ("GET", "/api/usage"): {"query": {}, "body": None, "status": 200},
    ("GET", "/api/graph"): {
        "query": {"project": "alpha", "limit": "10"},
        "body": None,
        "status": 200,
    },
    ("POST", "/api/projects/{project}/models"): {
        "query": {},
        "body": {"models": "never"},
        "status": 200,
    },
    ("POST", "/api/integrations/{name}/preview"): {"query": {}, "body": {"days": 7}, "status": 200},
    ("POST", "/api/integrations/preview"): {
        "query": {},
        "body": {
            "name": "minutes",
            "credential": "ledger",
            "select": {},
            "options": {"limit": 10},
            "project": "alpha",
            "new_project": None,
            "days": 7,
        },
        "status": 200,
    },
    ("GET", "/api/audit"): {
        "query": {"before": "1000", "limit": "5", "action": "project", "project": "alpha"},
        "body": None,
        "status": 200,
    },
    ("GET", "/api/credentials"): {"query": {}, "body": None, "status": 200},
    ("DELETE", "/api/credentials/{name}"): {
        "query": {"confirm": "ledger"},
        "body": None,
        "status": 200,
    },
    ("GET", "/api/integrations"): {"query": {}, "body": None, "status": 200},
    ("POST", "/api/integrations"): {
        "query": {},
        "body": {
            "name": "minutes",
            "credential": "ledger",
            "select": {},
            "options": {"limit": 10},
            "project": "alpha",
            "new_project": None,
        },
        "status": 201,
    },
    ("DELETE", "/api/integrations/{name}"): {
        "query": {"confirm": "ledger"},
        "body": None,
        "status": 200,
    },
    ("POST", "/api/integrations/{name}/on"): {
        "query": {},
        "body": {"projects": [], "new_project": "epsilon"},
        "status": 200,
    },
    ("POST", "/api/integrations/{name}/off"): {"query": {}, "body": None, "status": 200},
}

# One valid call per tool, with every argument it takes. `{slug}` and `{claim_id}` are
# filled from the seeded world.
CALLS: dict[str, dict] = {
    "projects": {},
    "new_project": {"name": "delta", "description": "fourth", "kind": "general", "private": False},
    "note": {
        "project": "alpha",
        "text": "# Mote runbook\n\nMote checks the queue hourly.\n",
        "title": "Mote runbook",
        "topic": "Mote runbook",
        "label": "ops",
        "source_ref": "desk:42",
    },
    "recall": {"query": "quill", "project": "alpha", "limit": 3},
    "page": {"project": "alpha", "slug": "{slug}", "version": 1, "limit": 5, "before": 2},
    "evidence": {"project": "alpha", "slug": "{slug}", "claim_id": "{claim_id}", "version": 1},
    "overview": {"project": "alpha", "limit": 10, "after": "a"},
}


def names(given) -> str:
    quoted = [repr(name) for name in given]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " and " + quoted[-1]


def sentence(what: str, unknown: list[str], who: str, takes: list[str], plural: str) -> str:
    """The refusal: the unknown names sorted, then what `who` takes, in its own order."""
    unknown = sorted(unknown)
    said = f"unknown {what}{'s' if len(unknown) > 1 else ''} {names(unknown)}; "
    return said + (f"{who} takes {names(takes)}" if takes else f"{who} takes no {plural}")


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


@pytest.fixture
def world(tmp_path):
    """A workspace with projects alpha and beta, a note in each, and a key that reaches all."""
    db = tmp_path / "cairn.db"
    store = Store(db)
    key = store.create_key("desk")
    store.add_source(
        "ledger",
        "connector",
        {
            "url": "https://ledger.example.test/feed",
            "secret_env": "X1_UNSET_SECRET",
            "projects": [],
        },
    )
    store.add_credential(
        "ledger",
        "connector",
        {"url": "https://ledger.example.test/feed", "secret_env": "X1_UNSET_SECRET"},
    )
    app = create_app(store, public_url=BASE)
    headers = {"x-api-key": key}
    with TestClient(app, base_url=BASE) as client:
        for name in ("alpha", "beta"):
            assert client.post("/api/projects", headers=headers, json={"name": name}).status_code
        noted = client.post(
            "/api/projects/alpha/notes", headers=headers, json={"text": NOTE, "source_ref": "s:1"}
        ).json()
        client.post("/api/projects/beta/notes", headers=headers, json={"text": NOTE})
        slug = noted["page"]
        page = client.get(f"/api/projects/alpha/pages/{slug}", headers=headers).json()
        params = {
            "project": "alpha",
            "slug": slug,
            "claim_id": page["claims"][0]["id"],
            "note_id": noted["note_id"],
            "name": "ledger",
        }
        yield SimpleNamespace(
            client=client, store=store, db=db, key=key, headers=headers, params=params
        )
    store.close()


def fill(path: str, params: dict) -> str:
    def one(match: re.Match) -> str:
        name = match.group(1)
        assert name in params, f"{path}: the test cannot fill the path parameter {name!r}"
        return params[name]

    return re.sub(r"\{(\w+)(?::\w+)?\}", one, path)


def send(world, method: str, path: str, query: dict, body: dict | None):
    url = fill(path, world.params)
    if body is None:
        return world.client.request(method, url, headers=world.headers, params=query)
    return world.client.request(method, url, headers=world.headers, params=query, json=body)


def refused(r, said: str, *where) -> None:
    """The API's answer to a malformed request: 400 and exactly {"error": said}."""
    assert r.status_code == 400, (*where, r.status_code)
    assert r.json() == {"error": said}, where


def valid(method: str, path: str) -> dict:
    assert (method, path) in VALID, (
        f"{method} {path}: the test cannot build a valid request for this address;"
        " add one to VALID with every key and parameter it takes"
    )
    return VALID[(method, path)]


def access_records(caplog) -> list[tuple]:
    return [
        (r.actor_kind, r.actor, r.actor_id, r.action, r.project, r.status)
        for r in caplog.records
        if r.name == "cairn.access"
    ]


def rpc(world, method: str, params: dict):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    r = world.client.post("/mcp", headers={**MCP_HEADERS, **world.headers}, json=body)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def call(world, name: str, arguments: dict) -> dict:
    filled = {
        key: value.format(**world.params) if isinstance(value, str) and "{" in value else value
        for key, value in arguments.items()
    }
    return rpc(world, "tools/call", {"name": name, "arguments": filled})


# The table covers what there is


def test_every_api_address_has_a_valid_request():
    missing = [f"{m} {p}" for m, p in API_ROUTES if (m, p) not in VALID]
    assert not missing, f"no valid request for: {missing}; add each to VALID"
    assert set(VALID) <= set(API_ROUTES), sorted(set(VALID) - set(API_ROUTES))


def test_every_tool_has_a_valid_call(world):
    listed = sorted(tool["name"] for tool in rpc(world, "tools/list", {})["tools"])
    assert listed == TOOL_NAMES
    missing = [name for name in TOOL_NAMES if name not in CALLS]
    assert not missing, f"no valid call for the tools: {missing}; add each to CALLS"


# REST


@pytest.mark.parametrize(("method", "path"), API_ROUTES)
def test_a_correct_request_still_succeeds(world, method, path):
    request = valid(method, path)
    r = send(world, method, path, request["query"], request["body"])
    assert r.status_code == request["status"], (method, path, r.text)


@pytest.mark.parametrize(("method", "path"), API_ROUTES)
def test_an_unknown_query_parameter_is_refused_and_changes_nothing(world, method, path):
    request = valid(method, path)
    before = dump(world.db)
    query = {**request["query"], EXTRA: "1"}
    r = send(world, method, path, query, request["body"])
    takes = list(request["query"])
    said = sentence("query parameter", [EXTRA], "this address", takes, "query parameters")
    refused(r, said, method, path)
    assert dump(world.db) == before, (method, path)


@pytest.mark.parametrize(
    ("method", "path"), [route for route in API_ROUTES if VALID.get(route, {}).get("body")]
)
def test_an_unknown_body_key_is_refused_and_changes_nothing(world, method, path):
    request = valid(method, path)
    before = dump(world.db)
    r = send(world, method, path, request["query"], {**request["body"], EXTRA: "desk:43"})
    said = sentence("key", [EXTRA], "this address", list(request["body"]), "keys")
    refused(r, said, method, path)
    assert dump(world.db) == before, (method, path)


def test_the_misspelled_reference_is_refused_and_no_note_is_stored(world):
    before = dump(world.db)
    r = world.client.post(
        "/api/projects/alpha/notes",
        headers=world.headers,
        json={"text": "# Lumen\n\nLumen waits.\n", "sorce_ref": "desk:41", "lable": "ops"},
    )
    takes = ["text", "title", "topic", "label", "source_ref"]
    said = sentence("key", ["sorce_ref", "lable"], "this address", takes, "keys")
    assert said == (
        "unknown keys 'lable' and 'sorce_ref';"
        " this address takes 'text', 'title', 'topic', 'label' and 'source_ref'"
    )
    refused(r, said)
    assert dump(world.db) == before


def test_the_misspelled_project_is_refused_rather_than_searching_everything(world):
    r = world.client.get(
        "/api/search", headers=world.headers, params={"q": "quill", "projct": "alpha"}
    )
    said = "unknown query parameter 'projct'; this address takes 'q', 'project' and 'limit'"
    refused(r, said)


def test_an_address_that_takes_no_parameter_says_so(world):
    r = world.client.get("/api/projects", headers=world.headers, params={"zorp": "1"})
    said = "unknown query parameter 'zorp'; this address takes no query parameters"
    refused(r, said)


# MCP


def test_each_tool_schema_forbids_additional_properties(world):
    for tool in rpc(world, "tools/list", {})["tools"]:
        assert tool["inputSchema"].get("additionalProperties") is False, tool["name"]


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_a_correct_tool_call_still_succeeds(world, name):
    arguments = CALLS.get(name)
    assert arguments is not None, f"{name}: no valid call; add one to CALLS"
    result = call(world, name, arguments)
    assert result["isError"] is False, (name, result)


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_an_unknown_argument_is_refused_recorded_and_changes_nothing(world, name, caplog):
    arguments = CALLS.get(name)
    assert arguments is not None, f"{name}: no valid call; add one to CALLS"
    schema = next(t for t in rpc(world, "tools/list", {})["tools"] if t["name"] == name)
    takes = list(schema["inputSchema"].get("properties", {}))
    assert set(arguments) == set(takes), f"{name}: CALLS must give every argument"
    before = dump(world.db)
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        result = call(world, name, {**arguments, EXTRA: "desk:43"})
    assert result["isError"] is True, (name, result)
    text = result["content"][0]["text"]
    assert text.endswith(sentence("argument", [EXTRA], name, takes, "arguments")), text
    key_id = world.store.identify_key(world.key)[1]
    assert access_records(caplog) == [("key", "desk", key_id, "invalid", None, 400)]
    assert dump(world.db) == before, name


def test_the_misspelled_reference_over_mcp_is_refused_and_no_note_is_stored(world):
    before = dump(world.db)
    result = call(
        world, "note", {"project": "alpha", "text": "# Mote\n\nMote waits.\n", "sorce_ref": "d:4"}
    )
    assert result["isError"] is True
    assert result["content"][0]["text"].endswith(
        "unknown argument 'sorce_ref';"
        " note takes 'project', 'text', 'title', 'topic', 'label' and 'source_ref'"
    )
    assert dump(world.db) == before


def test_a_tool_that_takes_no_argument_says_so(world):
    result = call(world, "projects", {"zorp": 1})
    assert result["isError"] is True
    assert result["content"][0]["text"].endswith(
        "unknown argument 'zorp'; projects takes no arguments"
    )


# A refusal has a bound

LONG = "z" * 100_000  # one name of 100,000 characters
# The longest query the HTTP client sends is 65,536 characters (uvicorn reads a request head of
# 16 KiB at most), so a query parameter of 100,000 characters never reaches cairn: 60,000.
LONG_QUERY = "z" * 60_000
MANY = [f"zorp{i:03d}" for i in range(500)]  # 500 unknown names
CUT = f"'{'z' * 64}'..."  # LONG as a refusal shows it
FIRST_FIVE = "'zorp000', 'zorp001', 'zorp002', 'zorp003', 'zorp004' and 495 more"
NOTE_KEYS = "'text', 'title', 'topic', 'label' and 'source_ref'"
BOUND = 1_000


def not_in_full(text: str, unknown: list[str]) -> None:
    """No long name, and no name past the first five, is in `text` in full."""
    for name in [*sorted(unknown)[5:], *(n for n in unknown if len(n) > 64)]:
        assert name not in text, name[:80]


def logged(caplog) -> str:
    """What the access log and the MCP server's log recorded, every field of every record."""
    kept = [r for r in caplog.records if r.name == "cairn.access" or r.name.startswith("mcp")]
    assert any(r.name == "cairn.access" for r in kept)
    return repr([sorted(r.__dict__.items()) for r in kept]) + "".join(r.getMessage() for r in kept)


@pytest.mark.parametrize(
    ("unknown", "said"),
    [
        ([LONG], f"unknown key {CUT}; this address takes {NOTE_KEYS}"),
        (MANY, f"unknown keys {FIRST_FIVE}; this address takes {NOTE_KEYS}"),
    ],
    ids=["long", "many"],
)
def test_a_refused_body_key_is_named_within_a_bound(world, caplog, unknown, said):
    before = dump(world.db)
    body = {"text": "# Lumen\n\nLumen waits.\n", **dict.fromkeys(unknown, "1")}
    with caplog.at_level(logging.INFO):
        r = world.client.post("/api/projects/alpha/notes", headers=world.headers, json=body)
    refused(r, said)
    assert len(r.json()["error"]) < BOUND
    not_in_full(logged(caplog), unknown)
    assert dump(world.db) == before


@pytest.mark.parametrize(
    ("unknown", "said"),
    [
        (
            [LONG_QUERY],
            f"unknown query parameter {CUT}; this address takes 'q', 'project' and 'limit'",
        ),
        (
            MANY,
            f"unknown query parameters {FIRST_FIVE}; this address takes 'q', 'project' and 'limit'",
        ),
    ],
    ids=["long", "many"],
)
def test_a_refused_query_parameter_is_named_within_a_bound(world, caplog, unknown, said):
    query = {"q": "quill", **dict.fromkeys(unknown, "1")}
    with caplog.at_level(logging.INFO):
        r = world.client.get("/api/search", headers=world.headers, params=query)
    refused(r, said)
    assert len(r.json()["error"]) < BOUND
    not_in_full(logged(caplog), unknown)


@pytest.mark.parametrize(
    ("unknown", "said"),
    [
        ([LONG], f"unknown argument {CUT}; recall takes 'query', 'project' and 'limit'"),
        (MANY, f"unknown arguments {FIRST_FIVE}; recall takes 'query', 'project' and 'limit'"),
    ],
    ids=["long", "many"],
)
def test_a_refused_argument_is_named_within_a_bound(world, caplog, unknown, said):
    before = dump(world.db)
    with caplog.at_level(logging.INFO):
        result = call(world, "recall", {"query": "quill", **dict.fromkeys(unknown, 1)})
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert text.endswith(said), text[:BOUND]
    assert len(text) < BOUND
    not_in_full(logged(caplog), unknown)
    assert dump(world.db) == before


# What stays as it is


def test_web_pages_and_oauth_addresses_still_ignore_what_they_do_not_know(world):
    r = world.client.post("/login", data={"key": world.key}, follow_redirects=False)
    assert r.status_code == 303
    for path in ("/?zorp=1", "/b/alpha?zorp=1", "/search?q=quill&zorp=1"):
        assert world.client.get(path).status_code == 200, path
    form = {"text": "# Mote\n\nMote checks the queue hourly.\n", EXTRA: "1"}
    r = world.client.post(
        "/b/alpha/notes",
        data=form,
        headers={"sec-fetch-site": "same-origin"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text  # the form's note is stored, its extra field ignored
    for path in (
        "/.well-known/oauth-authorization-server?zorp=1",
        "/.well-known/oauth-protected-resource/mcp?zorp=1",
    ):
        assert world.client.get(path).status_code == 200, path
