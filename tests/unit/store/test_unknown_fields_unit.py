"""Unit tests for X1 (tests/acceptance/test_x1_unknown_fields.py): what the acceptance file
leaves open. The sentence itself (cairn.memory.not_taken); that every /api handler reads its
query and its body only through cairn.web.rest's two helpers, so a new address cannot skip
them (read from each handler's own code and its module's); that each parameter keeps its
own rules; and how an MCP refusal is recorded beside the other refusals. Every name is
made up."""

import ast
import asyncio
import inspect
import logging
import sqlite3
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.routing import Route
from starlette.testclient import TestClient

from cairn.memory import not_taken
from cairn.server import create_app
from cairn.store import Store
from cairn.web import rest
from cairn.web.mcp import _Server, build_mcp

BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
NOTE = "# Quill runbook\n\nQuill retries 3 times before failing.\n"


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
    key = store.create_key("desk")
    app = create_app(store, public_url=BASE)
    headers = {"x-api-key": key}
    with TestClient(app, base_url=BASE) as client:
        assert client.post("/api/projects", headers=headers, json={"name": "alpha"}).is_success
        noted = client.post("/api/projects/alpha/notes", headers=headers, json={"text": NOTE})
        yield SimpleNamespace(
            client=client, store=store, db=db, key=key, headers=headers, slug=noted.json()["page"]
        )
    store.close()


def tool(env, name: str, arguments: dict) -> dict:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    r = env.client.post("/mcp", headers={**MCP_HEADERS, **env.headers}, json=body)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def access_records(caplog) -> list[tuple]:
    return [
        (r.actor_kind, r.actor, r.action, r.project, r.status)
        for r in caplog.records
        if r.name == "cairn.access"
    ]


# The sentence


def test_one_unknown_name_and_what_is_taken_in_its_own_order():
    said = not_taken("key", {"sorce_ref"}, "this address", ("text", "source_ref", "label"))
    assert said == "unknown key 'sorce_ref'; this address takes 'text', 'source_ref' and 'label'"


def test_several_unknown_names_are_sorted_and_the_word_is_plural():
    said = not_taken("query parameter", ["zeta", "alfa", "mid"], "this address", ("q",))
    assert said == "unknown query parameters 'alfa', 'mid' and 'zeta'; this address takes 'q'"


def test_taking_nothing_is_said_in_words():
    assert not_taken("argument", ["x"], "projects", ()) == (
        "unknown argument 'x'; projects takes no arguments"
    )
    assert not_taken("query parameter", ["x"], "this address", ()) == (
        "unknown query parameter 'x'; this address takes no query parameters"
    )


def test_a_name_with_quotes_or_nothing_in_it_is_quoted_so_it_can_be_read():
    said = not_taken("key", ["", "it's"], "this address", ("text",))
    assert said == """unknown keys '' and "it's"; this address takes 'text'"""


def test_six_unknown_names_show_the_first_five_sorted_and_count_the_rest():
    said = not_taken("key", ["f", "e", "d", "c", "b", "a"], "this address", ("text",))
    assert said == "unknown keys 'a', 'b', 'c', 'd', 'e' and 1 more; this address takes 'text'"
    said = not_taken("key", [f"k{i:02d}" for i in range(17)], "this address", ("text",))
    assert said.startswith("unknown keys 'k00', 'k01', 'k02', 'k03', 'k04' and 12 more;")


def test_a_long_name_is_cut_to_64_characters_and_says_so():
    long = "a" * 9_999 + "z"
    said = not_taken("argument", [long], "note", ("project", "text"))
    assert said == f"unknown argument '{'a' * 64}'...; note takes 'project' and 'text'"
    assert not_taken("key", ["b" * 64], "x", ()) == f"unknown key '{'b' * 64}'; x takes no keys"
    assert not_taken("key", ["b" * 65], "x", ()) == f"unknown key '{'b' * 64}'...; x takes no keys"


def test_what_is_taken_is_listed_in_full_however_many():
    takes = [f"field_{i}" for i in range(12)]
    said = not_taken("key", ["zorp"], "this address", takes)
    assert said.endswith("'field_10' and 'field_11'") and "more" not in said


def test_a_newline_or_an_escape_in_a_name_never_reaches_the_sentence_raw():
    said = not_taken("key", ["sorce\nref", "x\x1b[31mred"], "this address", ("text",))
    assert said == ("unknown keys 'sorce\\nref' and 'x\\x1b[31mred'; this address takes 'text'")
    assert "\n" not in said and "\x1b" not in said


def test_a_name_cut_is_cut_as_it_is_shown_so_escapes_cannot_grow_it():
    """A name of characters repr writes long (\\x00 is four) is cut where what is shown
    between the quotes reaches 64 characters, never past: the bound holds for any name."""
    said = not_taken("key", ["\x00" * 10_000], "x", ())
    assert said == f"unknown key '{'\\x00' * 16}'...; x takes no keys"
    worst = not_taken("key", [chr(0x10FFFF) * 10_000 + str(i) for i in range(9)], "x", ())
    assert len(worst) < 600


# Every /api handler reads through the helpers


def api_handlers() -> list[tuple[str, object]]:
    store = Store(":memory:")
    try:
        app = create_app(store, public_url=BASE)
    finally:
        store.close()
    found = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if path == "/api" or path.startswith("/api/"):
            assert isinstance(route, Route), path
            found.append((f"{sorted(route.methods or ())} {path}", inspect.unwrap(route.endpoint)))
    assert len(found) >= 11  # every address there is today, and any added later
    return found


READS = {"query_params", "json", "body", "form", "stream"}  # what a Request is read by
HELPERS = {"_query", "_body"}  # cairn.web.rest's two helpers: the only readers


def _tree(thing) -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(thing)))


def test_every_api_handler_calls_the_query_helper_and_reads_nothing_itself():
    for where, handler in api_handlers():
        tree = _tree(handler)
        reads = sorted(
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in READS
        )
        assert reads == [], f"{where}: {handler.__name__} reads {reads}; use _query and _body"
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "_query" in called, f"{where}: {handler.__name__} never calls _query"


def test_in_every_module_with_an_api_handler_only_the_helpers_read_a_query_or_a_json_body():
    modules = {inspect.getmodule(handler) for _, handler in api_handlers()}
    assert rest in modules
    for module in modules:
        tree = _tree(module)
        allowed: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name in HELPERS:
                allowed.update(id(inner) for inner in ast.walk(node))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("query_params", "json"):
                assert id(node) in allowed, (
                    f"{module.__name__}, line {node.lineno}: .{node.attr} outside _query or _body"
                )


# Each parameter keeps its own rules


def test_a_known_parameter_given_twice_or_empty_is_read_as_before(env):
    h = env.headers
    r = env.client.get("/api/projects/alpha/pages?limit=&after=", headers=h)
    assert r.status_code == 200 and [p["slug"] for p in r.json()["pages"]] == [env.slug]
    r = env.client.get("/api/search?q=quill&limit=1&limit=2", headers=h)
    assert r.status_code == 200
    r = env.client.get("/api/search?q=quill&limit=two", headers=h)
    assert (r.status_code, r.json()) == (400, {"error": "'limit' is a whole number, 1 to 50"})


def test_a_parameter_with_no_name_is_unknown_too(env):
    r = env.client.get("/api/projects?=alpha", headers=env.headers)
    said = "unknown query parameter ''; this address takes no query parameters"
    assert (r.status_code, r.json()) == (400, {"error": said})


def test_head_is_refused_as_get_is(env):
    assert env.client.head("/api/projects?zorp=1", headers=env.headers).status_code == 400
    assert env.client.head("/api/projects", headers=env.headers).status_code == 200


def test_a_body_that_is_no_object_is_refused_as_before(env):
    for body in ("[]", '"text"', "{", ""):
        r = env.client.post(
            "/api/projects/alpha/notes",
            headers={**env.headers, "content-type": "application/json"},
            content=body,
        )
        assert (r.status_code, r.json()) == (400, {"error": "the body must be a JSON object"})


def test_an_unknown_key_is_named_before_any_key_is_checked(env):
    before = dump(env.db)
    r = env.client.post(
        "/api/projects/alpha/notes", headers=env.headers, json={"text": 5, "zorp": "1"}
    )
    said = (
        "unknown key 'zorp'; this address takes 'text', 'title', 'topic', 'label' and 'source_ref'"
    )
    assert (r.status_code, r.json()) == (400, {"error": said})
    assert dump(env.db) == before


def test_a_refused_request_is_one_access_record_with_status_400(env, caplog):
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        r = env.client.get("/api/search?q=quill&projct=alpha", headers=env.headers)
    assert r.status_code == 400
    assert access_records(caplog) == [("key", "desk", "recall", None, 400)]
    assert "quill" not in caplog.text and "projct" not in caplog.text


def test_the_web_page_view_shares_the_number_rule_and_still_ignores_what_it_does_not_know(env):
    r = env.client.post("/login", data={"key": env.key}, follow_redirects=False)
    assert r.status_code == 303
    r = env.client.get(f"/b/alpha/p/{env.slug}?version=1&before=2&zorp=1")
    assert r.status_code == 200
    assert env.client.get(f"/b/alpha/p/{env.slug}?version=one").status_code == 400


# MCP


def test_a_call_with_an_unknown_and_a_wrong_argument_is_one_record(env, caplog):
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        result = tool(env, "recall", {"query": "quill", "limit": "many", "projct": "alpha"})
    assert result["isError"] is True
    assert result["content"][0]["text"] == (
        "Error executing tool recall: unknown argument 'projct';"
        " recall takes 'query', 'project' and 'limit'"
    )
    assert access_records(caplog) == [("key", "desk", "invalid", None, 400)]


def test_the_context_is_no_argument_a_caller_can_send(env):
    result = tool(env, "projects", {"ctx": {}})
    assert result["isError"] is True
    assert result["content"][0]["text"].endswith("projects takes no arguments")


def test_an_unknown_tool_is_answered_as_before_and_not_recorded_as_invalid(env, caplog):
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        result = tool(env, "remember", {"zorp": 1})
    assert result == {
        "content": [{"type": "text", "text": "Unknown tool: remember"}],
        "isError": True,
    }
    assert access_records(caplog) == []


def test_the_schemas_are_the_tools_own_with_additional_properties_closed():
    server = build_mcp()
    assert isinstance(server, _Server)
    closed = asyncio.run(server.list_tools())
    own = asyncio.run(super(_Server, server).list_tools())
    assert [t.name for t in closed] == [t.name for t in own]
    for mine, theirs in zip(closed, own, strict=True):
        assert mine.input_schema == {**theirs.input_schema, "additionalProperties": False}
        assert mine.model_dump(exclude={"input_schema"}) == theirs.model_dump(
            exclude={"input_schema"}
        )
