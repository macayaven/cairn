"""G1 acceptance tests (frozen): the note guard and the audit log.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/guard.py, src/cairn/store/__init__.py and
src/cairn/server.py.

The guard
- Before a note is stored, its title and text are checked against patterns. This is pattern
  matching, not understanding: it recognises the shapes listed here and nothing else. It
  does not recognise a person's name, an address or free-text health or financial details.
- `cairn.guard.PatternGuard(policy).check(text) -> list[Finding]`; a Finding has `rule`,
  `kind` ("secret" or "personal") and `line` (1-based), and never the matched text.
  Policies: "off"; "secrets" (the default); "secrets+personal".
- Secret rules: private_key, aws_access_key, github_token, slack_token, model_api_key,
  jwt, cairn_credential, assigned_secret (a password, secret, token or api key assigned a
  literal value of 12 or more characters; placeholders and variable references are not
  literals).
- Personal rules: email_address, phone_number, us_ssn, payment_card (Luhn-valid), iban
  (checksum-valid).
- A workspace's policy is its setting "guard.policy" (`view.get_setting`,
  `view.set_setting`). `create_app(..., guard=...)` replaces the guard altogether: anything
  with `check(text) -> list[Finding]`.
- A note with findings is refused and nothing of it is stored or logged: 422 over REST with
  `{"error", "findings": [{"rule", "kind", "line"}]}`, a tool error over MCP naming the
  rules and lines, and the composer again with the same list in the web view.

The audit log
- `view.list_audit(limit=100) -> list[AuditEvent]`, newest first: `at`, `actor_kind` ("key",
  "token", "session" or "system"), `actor` (the key's label, the agent's client name, or
  the person), `action`, `project` (a name, or None) and `detail` (a small dict, never note
  text). GET /audit shows it in the web view.
- Recorded: note.saved, note.unchanged, note.rejected (the gate), note.refused (the guard,
  with the rule names), project.created, key.created, key.revoked, agent.connected,
  agent.disconnected, setting.changed.
- A view records what it writes under its actor. The server gives each request's view the
  caller as actor; a view used directly, as the command line does, acts as "system".
- Reads are not recorded in the database: reads never write. Each read and write over MCP
  and REST is one INFO record on the logger "cairn.access" with `actor_kind`, `actor`,
  `action` and `project` attributes, and no query text, note text or credential in it.
- Deleting a workspace deletes its audit log.
"""

import json
import logging
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.guard import Finding, PatternGuard
from cairn.server import create_app
from cairn.store import Store

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}

# Built at run time, so this file holds nothing that looks like a live credential.
LETTERS = "aB3dE5gH7jK9mN1pQ2rS4tU6vW8xY0zA"
PEM = "RSA PRIVATE KEY-----"
SECRETS = {
    "private_key": f"-----BEGIN {PEM}\nMIIEow{LETTERS}\n-----END {PEM}",
    "aws_access_key": "AKIA" + "IOSFODNN7EXAMPLE",
    "github_token": "ghp_" + LETTERS + "aB3d",
    "slack_token": "xoxb-" + "123456789012-" + "123456789012-" + LETTERS[:24],
    "model_api_key": "sk-ant-" + "api03-" + LETTERS + LETTERS,
    "jwt": ".".join(("eyJ" + "hbGciOiJIUzI1NiJ9", "eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0", LETTERS)),
    "cairn_credential": "cairn.at." + LETTERS + "aB3dE5gH7jK",
    "assigned_secret": 'db_password = "' + LETTERS[:20] + '"',
}
PERSONAL = {
    "email_address": "Write to ada.lovelace@example.test about it.",
    "phone_number": "Call +34 600 123 456 before noon.",
    "us_ssn": "SSN 078-05-1120 was on the form.",
    "payment_card": "Card 4111 1111 1111 1111 was charged.",
    "iban": "Pay to GB82 WEST 1234 5698 7654 32 today.",
}
HARMLESS = """# Release notes

- Commit 9dc01794e638a1b2c3d4e5f60718293a4b5c6d7e fixed the retry bug in v2.14.3.
- Run id 3f2504e0-4f89-11d3-9a0c-0305e82c3301 took 4 minutes on 2026-11-10.
- Set `API_KEY=${CAIRN_API_KEY}` and `password = <your password>` in the env file.
- The digest is sha256:e2a413d8659298fc83aa53de91305a48e9ca5c34b494d9626448b9500c249e06.
- Docs: https://example.test/guides/retries?page=2#backoff, port 8765, 60 calls a minute.
- Card numbers such as 1234 5678 9012 3456 fail the checksum and are not card numbers.
"""


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
    store.create_project("ops", "operations notes")
    key = store.create_key("laptop")
    app = create_app(store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:
        yield SimpleNamespace(client=client, store=store, db=db, key=key)
    store.close()


def mcp(env, name: str, /, **arguments) -> dict:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    r = env.client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": env.key}, json=body)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def rest_note(env, text: str, **fields):
    return env.client.post(
        "/api/projects/ops/notes", headers={"x-api-key": env.key}, json={"text": text, **fields}
    )


def sign_in(env) -> None:
    r = env.client.post(
        "/login", data={"key": env.key}, headers=SAME_ORIGIN, follow_redirects=False
    )
    assert r.status_code == 303


def note_with(secret: str) -> str:
    return f"# Deploy notes\n\nThe service restarts at 09:00.\n\n{secret}\n\nRetries: 3.\n"


# The guard on its own


def test_the_pattern_guard_names_rules_and_lines_never_the_text():
    guard = PatternGuard("secrets")
    for rule, secret in SECRETS.items():
        findings = guard.check(note_with(secret))
        assert findings, rule
        assert {f.rule for f in findings} == {rule}, rule
        assert all(isinstance(f, Finding) and f.kind == "secret" and f.line >= 5 for f in findings)
        shown = json.dumps([f.model_dump() for f in findings])
        assert secret.splitlines()[-1][-12:] not in shown, rule
    assert guard.check(HARMLESS) == []
    assert guard.check("\n".join(PERSONAL.values())) == []  # personal rules are another policy

    strict = PatternGuard("secrets+personal")
    for rule, line in PERSONAL.items():
        findings = strict.check(f"# Contact\n\n{line}\n")
        assert [(f.rule, f.kind, f.line) for f in findings] == [(rule, "personal", 3)], rule
    assert strict.check(HARMLESS) == []
    assert {f.rule for f in strict.check(note_with(SECRETS["jwt"]))} == {"jwt"}
    assert PatternGuard("off").check(note_with(SECRETS["private_key"])) == []
    with pytest.raises(ValueError):
        PatternGuard("everything")


# Through the service


def test_a_note_with_a_secret_is_refused_and_leaves_no_trace(env, caplog):
    before = env.store.list_notes(env.store.get_project("ops").id)
    with caplog.at_level(logging.DEBUG):
        for rule, secret in SECRETS.items():
            r = rest_note(env, note_with(secret))
            assert r.status_code == 422, rule
            body = r.json()
            assert [(f["rule"], f["kind"]) for f in body["findings"]] == [(rule, "secret")], rule
            assert body["findings"][0]["line"] >= 5 and rule in body["error"]
            tail = secret.splitlines()[-1][-12:]
            assert tail not in r.text, rule

            result = mcp(env, "note", project="ops", text=note_with(secret))
            assert result["isError"] is True and rule in result["content"][0]["text"]
            assert tail not in json.dumps(result), rule
    stored = dump(env.db)
    logged = "\n".join(record.getMessage() + str(record.__dict__) for record in caplog.records)
    for secret in SECRETS.values():
        tail = secret.splitlines()[-1][-12:]
        assert tail not in stored and tail not in logged
    assert env.store.list_notes(env.store.get_project("ops").id) == before == []
    # A secret in the title is caught too.
    titled = rest_note(env, "# Notes\n\nNothing here.\n", title=SECRETS["github_token"])
    assert titled.status_code == 422

    ok = rest_note(env, HARMLESS)
    assert ok.status_code == 201 and ok.json()["status"] == "saved"


def test_the_policy_is_a_workspace_setting(env):
    assert env.store.get_setting("guard.policy", "secrets") == "secrets"
    contact = "# Contact\n\n" + PERSONAL["email_address"] + "\n"
    assert rest_note(env, contact).status_code == 201  # personal patterns pass by default

    env.store.set_setting("guard.policy", "secrets+personal")
    for rule, line in PERSONAL.items():
        r = rest_note(env, f"# Contact two\n\n{line}\n")
        assert r.status_code == 422 and r.json()["findings"][0]["rule"] == rule, rule
    assert rest_note(env, HARMLESS).status_code == 201

    env.store.set_setting("guard.policy", "off")
    assert rest_note(env, note_with(SECRETS["aws_access_key"])).status_code == 201
    with pytest.raises(ValueError):
        env.store.set_setting("guard.policy", "everything")

    other = env.store.for_account(env.store.create_account("Brook").id)
    assert other.get_setting("guard.policy", "secrets") == "secrets"  # settings are per workspace


def test_the_web_composer_shows_what_the_guard_found(env):
    sign_in(env)
    typed = note_with(SECRETS["slack_token"])
    r = env.client.post(
        "/b/ops/notes", data={"text": typed}, headers=SAME_ORIGIN, follow_redirects=False
    )
    assert r.status_code == 422 and 'role="alert"' in r.text
    assert "slack_token" in r.text and "line 5" in r.text


def test_a_custom_guard_replaces_the_default(tmp_path):
    class NoQuill:
        def check(self, text: str) -> list[Finding]:
            lines = text.splitlines()
            return [
                Finding(rule="house_rule", kind="secret", line=n)
                for n, line in enumerate(lines, 1)
                if "quill" in line.lower()
            ]

    store = Store(tmp_path / "cairn.db")
    try:
        store.create_project("ops")
        key = store.create_key("laptop")
        app = create_app(store, public_url=BASE, guard=NoQuill())
        with TestClient(app, base_url=BASE) as client:
            headers = {"x-api-key": key}
            r = client.post(
                "/api/projects/ops/notes", headers=headers, json={"text": "# A\n\nQuill retries.\n"}
            )
            assert r.status_code == 422
            assert r.json()["findings"] == [{"rule": "house_rule", "kind": "secret", "line": 3}]
            leaked = "# B\n\n" + SECRETS["aws_access_key"] + "\n"
            ok = client.post("/api/projects/ops/notes", headers=headers, json={"text": leaked})
            assert ok.status_code == 201  # the default patterns are gone with the default guard
    finally:
        store.close()


# The audit log


def test_writes_and_security_events_are_recorded(env):
    assert rest_note(env, "# Runbook\n\nRestart with `svc restart`.\n").status_code == 201
    assert rest_note(env, "# Runbook\n\nRestart with `svc restart`.\n").status_code == 201
    assert rest_note(env, note_with(SECRETS["jwt"])).status_code == 422
    assert mcp(env, "new_project", name="notes")["isError"] is False
    second = env.store.create_key("ci")
    env.store.revoke_key("ci")
    env.store.set_setting("guard.policy", "secrets+personal")

    events = env.store.list_audit()
    actions = [e.action for e in reversed(events)]
    assert actions == [
        "project.created",  # the fixture's project and key, made on the store directly
        "key.created",
        "note.saved",
        "note.unchanged",
        "note.refused",
        "project.created",
        "key.created",
        "key.revoked",
        "setting.changed",
    ]
    by_action = {e.action: e for e in reversed(events)}  # the newest of each action
    assert (events[-1].actor_kind, events[-1].project) == ("system", "ops")
    saved = by_action["note.saved"]
    assert (saved.actor_kind, saved.actor, saved.project) == ("key", "laptop", "ops")
    assert saved.at and saved.detail["page"] == "runbook"
    assert by_action["note.refused"].detail == {"rules": ["jwt"]}
    made = by_action["project.created"]
    assert (made.actor_kind, made.actor, made.project) == ("key", "laptop", "notes")
    assert by_action["key.created"].detail == {"label": "ci"}
    assert by_action["setting.changed"].detail == {"name": "guard.policy"}
    text = json.dumps([e.model_dump() for e in events])
    assert "svc restart" not in text and second not in text and env.key not in text
    assert [e.action for e in env.store.list_audit(limit=2)] == ["setting.changed", "key.revoked"]

    sign_in(env)
    page = env.client.get("/audit")
    assert page.status_code == 200
    for needle in ("note.saved", "note.refused", "laptop", "jwt"):
        assert needle in page.text
    env.client.cookies.clear()
    assert env.client.get("/audit", follow_redirects=False).status_code == 303


def test_reads_go_to_the_access_log_not_the_database(env, caplog):
    assert rest_note(env, "# Runbook\n\nQuill restarts with `svc restart`.\n").status_code == 201
    before = dump(env.db)
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        assert mcp(env, "recall", query="quill restart secret-word-in-query")["isError"] is False
        assert mcp(env, "page", project="ops", slug="runbook")["isError"] is False
        r = env.client.get(
            "/api/search?q=quill+secret-word-in-query", headers={"x-api-key": env.key}
        )
        assert r.status_code == 200
    assert dump(env.db) == before

    records = [r for r in caplog.records if r.name == "cairn.access"]
    seen = [(r.actor_kind, r.actor, r.action, r.project) for r in records]
    assert ("key", "laptop", "recall", None) in seen
    assert ("key", "laptop", "page", "ops") in seen
    assert len(records) == 3
    everything = "\n".join(r.getMessage() + repr(sorted(r.__dict__.items())) for r in records)
    assert "secret-word-in-query" not in everything and env.key not in everything
    assert not re.search(r"svc restart", everything)


def test_deleting_a_workspace_deletes_its_audit_log(env):
    other = env.store.for_account(env.store.create_account("Brook").id)
    other.create_project("private")
    other.create_key("brook-laptop")
    assert [e.action for e in other.list_audit()] == ["key.created", "project.created"]
    assert all(e.actor != "brook-laptop" for e in env.store.list_audit())  # per workspace
    env.store.delete_account(other.account_id)
    assert "brook-laptop" not in dump(env.db)
