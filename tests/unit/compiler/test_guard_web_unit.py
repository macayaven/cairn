"""Unit tests for the note guard, the audit log and the access log in the web service:
what tests/acceptance/test_g1_guard.py leaves open about the shape of a refusal, which policy
a narrowed key gets, who a refusal is recorded under, and who may read the log."""

import html
import json
import logging
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store
from support.cookie_names import session_cookie

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
# Built at run time, so this file holds nothing that looks like a live credential.
TOKEN = "ghp_" + "Zq7xR2" * 6
EMAIL = "grace" + "@example.test"
NOTE = f"# Deploy\n\nThe job restarts at 09:00.\n\nIt uses {TOKEN} for now.\n"


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
    ops = store.create_project("ops")
    key = store.create_key("laptop")
    app = create_app(store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:
        yield SimpleNamespace(client=client, store=store, db=db, key=key, ops=ops)
    store.close()


def rest_note(env, key: str, text: str, **fields):
    return env.client.post(
        "/api/projects/ops/notes", headers={"x-api-key": key}, json={"text": text, **fields}
    )


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


def sign_in_with_key(env) -> None:
    r = env.client.post(
        "/login", data={"key": env.key}, headers=SAME_ORIGIN, follow_redirects=False
    )
    assert r.status_code == 303


def as_person(env, role: str) -> None:
    """A session for a person: the workspace's owner, or a plain member of it."""
    people = ("Olive Owner", "Max Member") if role == "member" else ("Olive Owner",)
    for n, name in enumerate(people):
        signed = env.store.sign_in(
            "github",
            str(n),
            f"person{n}@example.test",
            name,
            issuer="https://github.com",
            join="default",
        )
    env.client.cookies.set(session_cookie(), signed.token)


def test_a_refusal_lists_each_rule_with_its_line_or_field_and_nothing_it_matched(env):
    r = rest_note(env, env.key, NOTE, title=f"Deploy with {TOKEN}", topic="deploys")
    assert r.status_code == 422
    body = r.json()
    assert TOKEN not in r.text and "ghp_" not in r.text
    assert body["findings"] == [
        {"rule": "github_token", "kind": "secret", "line": 5},
        {"rule": "github_token", "kind": "secret", "line": 1, "field": "title"},
    ]
    assert "github_token" in body["error"] and "line 5" in body["error"]


def test_a_refusal_writes_one_audit_row_and_nothing_else(env):
    before = env.store.list_audit()
    assert rest_note(env, env.key, NOTE).status_code == 422
    (event, *rest) = env.store.list_audit()
    assert rest == before
    assert (event.actor_kind, event.actor, event.action, event.project) == (
        "key",
        "laptop",
        "note.refused",
        "ops",
    )
    assert event.detail == {"rules": ["github_token"]}
    assert TOKEN not in dump(env.db)
    assert env.store.list_notes(env.ops.id) == []


def test_a_narrowed_key_gets_its_workspace_policy(env):
    writer = env.store.create_key("ops-writer", projects=(env.ops.id,), access="write")
    contact = f"# Contacts\n\nWrite to {EMAIL} about it.\n"
    assert rest_note(env, writer, contact).status_code == 201  # the default: secrets only
    env.store.set_setting("guard.policy", "secrets+personal")
    refused = rest_note(env, writer, contact.replace("Contacts", "Owners"))
    assert refused.status_code == 422
    assert refused.json()["findings"] == [{"rule": "email_address", "kind": "personal", "line": 3}]
    env.store.set_setting("guard.policy", "off")
    assert rest_note(env, writer, NOTE).status_code == 201
    assert env.store.list_audit()[0].action == "note.saved"


def test_a_read_only_key_learns_nothing_from_the_guard(env):
    """A key that may not write is refused the same way whatever its note holds, and its
    refusal is not a write either: nothing lands in the audit log."""
    reader = env.store.create_key("reader", access="read")
    before = dump(env.db)
    secret = rest_note(env, reader, NOTE)
    plain = rest_note(env, reader, "# Deploy\n\nThe job restarts at 09:00.\n")
    assert (secret.status_code, plain.status_code) == (403, 403)
    assert "github_token" not in secret.text
    assert dump(env.db) == before


def test_an_unknown_project_is_not_found_before_the_guard_looks(env):
    r = env.client.post(
        "/api/projects/nowhere/notes", headers={"x-api-key": env.key}, json={"text": NOTE}
    )
    assert r.status_code == 404 and "github_token" not in r.text
    assert [e.action for e in env.store.list_audit()] == ["key.created", "project.created"]


def test_the_composer_gives_back_the_note_as_typed_with_what_to_remove_above_it(env):
    sign_in_with_key(env)
    text = NOTE + "\nSee <b>this</b> & that.\n"
    form = {"text": text, "title": f"Deploy {TOKEN}", "topic": "deploys", "project": "ops"}
    r = env.client.post("/notes", data=form, headers=SAME_ORIGIN)
    assert r.status_code == 422
    assert "Nothing was saved. Remove what is listed below and save again." in r.text
    area = re.search(r"<textarea\b[^>]*>\n?(.*?)</textarea>", r.text, re.S).group(1)
    assert area == html.escape(text, quote=False)  # every line as typed, escaped
    assert f'value="Deploy {TOKEN}"' in r.text and 'value="deploys"' in r.text
    assert re.search(r'<option value="ops" selected>', r.text)
    form_at = r.text.index('class="stack writing composer"')  # the note form
    assert r.text.index('<span data-rule="github_token">a GitHub token</span> on line 5') < form_at
    assert (
        r.text.index('<span data-rule="github_token">a GitHub token</span> on the title') < form_at
    )
    assert env.store.list_notes(env.ops.id) == []


def test_a_refusal_in_a_session_opened_with_a_key_is_recorded_under_its_label(env):
    sign_in_with_key(env)
    r = env.client.post("/b/ops/notes", data={"text": NOTE}, headers=SAME_ORIGIN)
    assert r.status_code == 422
    event = env.store.list_audit()[0]
    assert (event.actor_kind, event.actor, event.action) == ("session", "laptop", "note.refused")


def test_a_persons_refusal_is_recorded_under_their_name_and_user(env):
    as_person(env, "owner")
    r = env.client.post("/b/ops/notes", data={"text": NOTE}, headers=SAME_ORIGIN)
    assert r.status_code == 422
    event = env.store.list_audit()[0]
    assert (event.actor_kind, event.actor, event.action) == (
        "session",
        "Olive Owner",
        "note.refused",
    )
    assert event.detail["rules"] == ["github_token"] and event.detail["user"].startswith("usr_")


def test_only_who_manages_the_workspace_reads_its_audit_log(env):
    as_person(env, "member")
    r = env.client.get("/audit")
    assert r.status_code == 403 and "key.created" not in r.text
    as_person(env, "owner")
    page = env.client.get("/audit")
    assert page.status_code == 200 and "key.created" in page.text
    assert "newest 100,000 events" in page.text  # the log is bounded, and says so


def test_one_access_record_per_mcp_call_with_its_outcome(env, caplog):
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        refused = tool(env, env.key, "note", project="ops", text=NOTE)
        missing = tool(env, env.key, "page", project="nowhere", slug="deploy")
    assert refused["isError"] is True and missing["isError"] is True
    records = [r for r in caplog.records if r.name == "cairn.access"]
    key_id = env.store.identify_key(env.key)[1]
    seen = [(r.actor_kind, r.actor, r.actor_id, r.action, r.project, r.status) for r in records]
    assert seen == [
        ("key", "laptop", key_id, "note", "ops", 422),
        ("key", "laptop", key_id, "page", None, 404),
    ]
    assert records[0].getMessage() == f"note by key 'laptop' ({key_id}), project 'ops': 422"
    everything = json.dumps([r.getMessage() for r in records]) + repr(
        [sorted(r.__dict__.items()) for r in records]
    )
    assert TOKEN not in everything and env.key not in everything and "nowhere" not in everything


def access_records(caplog) -> list[tuple]:
    return [
        (r.actor_kind, r.actor, r.actor_id, r.action, r.project, r.status)
        for r in caplog.records
        if r.name == "cairn.access"
    ]


def test_a_request_refused_before_its_route_leaves_one_record_without_the_credential(env, caplog):
    wrong = "cairn_" + "Wq9vT3" * 6  # a credential no one was given
    mcp = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        assert rest_note(env, wrong, "hello").status_code == 401
        assert env.client.get("/api/projects").status_code == 401
        bearer = {**MCP_HEADERS, "authorization": f"Bearer {wrong}"}
        assert env.client.post("/mcp", headers=bearer, json=mcp).status_code == 401
        assert env.client.get("/projects", follow_redirects=False).status_code == 303  # the UI
    assert access_records(caplog) == [("none", None, None, "denied", None, 401)] * 3
    everything = repr([sorted(r.__dict__.items()) for r in caplog.records])
    assert wrong not in everything


def test_an_mcp_call_whose_arguments_fail_validation_is_recorded_as_invalid(env, caplog):
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        result = tool(env, env.key, "recall", query="deploy", limit="hunter2-not-a-number")
    assert result["isError"] is True
    key_id = env.store.identify_key(env.key)[1]
    assert access_records(caplog) == [("key", "laptop", key_id, "invalid", None, 400)]
    access = [r for r in caplog.records if r.name == "cairn.access"]
    assert "hunter2" not in repr([sorted(r.__dict__.items()) for r in access])


class Broken(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        raise RuntimeError("the log is down")


def test_a_failing_access_log_handler_does_not_fail_the_request(env):
    access = logging.getLogger("cairn.access")
    handler, level = Broken(), access.level
    access.addHandler(handler)
    access.setLevel(logging.INFO)
    try:
        h = {"x-api-key": env.key}
        assert env.client.get("/api/projects", headers=h).status_code == 200
        assert env.client.get("/api/projects/nowhere/pages", headers=h).status_code == 404
        assert rest_note(env, env.key, NOTE).status_code == 422
        assert env.client.get("/api/projects").status_code == 401
        assert tool(env, env.key, "projects")["isError"] is False
        assert tool(env, env.key, "recall", query="x", limit="many")["isError"] is True
    finally:
        access.removeHandler(handler)
        access.setLevel(level)


def test_the_audit_log_tells_apart_two_keys_with_one_label(env):
    keys = [env.store.create_key("ci") for _ in range(2)]
    for key, name in zip(keys, ("one", "two"), strict=True):
        made = env.client.post("/api/projects", headers={"x-api-key": key}, json={"name": name})
        assert made.status_code == 201
    ids = [env.store.identify_key(key)[1] for key in keys]
    created = [e for e in env.store.list_audit() if e.action == "project.created" and e.actor]
    assert sorted(e.actor_id for e in created) == sorted(ids)
    sign_in_with_key(env)
    page = env.client.get("/audit").text
    for key_id in ids:  # shown short, after the name; the whole id on hover
        assert f'title="{key_id}"' in page and key_id[-6:] in page
    session = env.store.identify_key(env.key)[1]
    env.client.post("/projects", data={"name": "three"}, headers=SAME_ORIGIN)
    event = env.store.list_audit()[0]
    assert (event.actor_kind, event.actor, event.actor_id) == ("session", "laptop", session)
