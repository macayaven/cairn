"""Unit tests for the web service, beyond the frozen W2 contract.

They pin what tests/acceptance/test_w2_server.py leaves open: secret handling, the host
allow-list, error mapping, the page view's edge cases and the CLI. The app runs over
FakeStore, a small in-memory stand-in for cairn.store.Store that follows the store's
docstring wherever the server branches on its answers. The tenancy tests at the end pin what
tests/acceptance/test_h2_tenancy.py leaves open: how the caller's account reaches an MCP tool,
and that nothing falls back to a default account.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import secrets
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from mcp.types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import cairn.memory
import cairn.web.cli
from cairn import compiler, server
from cairn.compiler import CandidatePage, CompileResult, GateResult
from cairn.kinds import KINDS
from cairn.server import create_app
from cairn.spans import chunk
from cairn.store import (
    DEFAULT_ACCOUNT_ID,
    Claim,
    NameTaken,
    Note,
    Page,
    PageHead,
    Principal,
    Project,
    ProjectSharing,
    SearchHit,
    Store,
    ranking,
)
from cairn.web.stats import STATS_LOGGER
from cookie_names import session_cookie

BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
CONTENT_KINDS = {"paragraph", "bullet", "code", "table", "quote"}
NOTE = "# Retry policy\n\nQuill retries 3 times before failing.\n\n- Lumen never retries.\n"


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


class FakeStore:
    """In memory. Unknown projects, pages and notes give None, create_project raises ValueError
    for a bad or taken name, add_note raises KeyError for an unknown project, and get_spans
    leaves out unknown ids, as the real store's contract says. It holds the default account
    only: every key and session resolves to it, and its view is the store itself, so a test
    sees what a handler wrote. for_account raises KeyError for an id not in `accounts`."""

    user_id = None  # its view acts for no person, as a key's does

    def __init__(self, path: object = ":memory:") -> None:
        self.path = path
        self.accounts = {DEFAULT_ACCOUNT_ID}
        self.keys: dict[str, str] = {}  # digest -> label
        self.sessions: dict[str, float] = {}  # digest -> expiry
        self.projects: dict[str, Project] = {}
        self.notes: dict[str, Note] = {}
        self.spans: dict[str, list] = {}  # note id -> spans
        self.pages: dict[tuple[str, str], list[Page]] = {}
        self.searches: list[tuple] = []
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def health(self, wait: float = 1.0) -> str:
        """What /healthz reads (Store.health): the fake can serve while it is open. The
        real read is tested over the real store."""
        return "unavailable" if self.closed else "ok"

    @property
    def account_id(self) -> str:
        return DEFAULT_ACCOUNT_ID

    def for_account(self, account_id: str) -> "FakeStore":
        if account_id not in self.accounts:
            raise KeyError(account_id)
        return self

    def narrow(self, projects=None, access: str = "write") -> "FakeStore":
        """Every key and session here reaches every project to write: narrowing to that reach
        is the view itself. The real narrowing is tested over the real store."""
        assert (projects, access) == (None, "write"), "FakeStore only knows the whole reach"
        return self

    # G1: the actor, settings and the audit log. The fake keeps no log and has no settings;
    # both are tested over the real store.
    actor = ("system", None)
    actor_id = None

    def acting_as(self, kind: str, name: str | None, actor_id: str | None = None) -> "FakeStore":
        return self

    # A1: who wrote each note. The fake records no writer: every note it holds reads as one
    # stored before writers were recorded. Writers are tested over the real store.
    def writing_as(self, kind: str, name: str | None, key_id: str | None = None) -> "FakeStore":
        return self

    def note_writers(self, note_ids) -> dict:
        return {i: ("unknown", None, self.notes[i].via) for i in note_ids if i in self.notes}

    def note_origins(self, note_ids) -> dict:  # 8.1: note_writers', with when and no Slack
        from cairn.store.memory import NoteOrigin

        return {
            i: NoteOrigin("unknown", None, self.notes[i].via, self.notes[i].created_at, False)
            for i in note_ids
            if i in self.notes
        }

    def get_setting(self, name: str, default: str | None = None) -> str | None:
        return default

    def project_access(self, project_id: str) -> None:
        """The view acts for no person, so it may open every project's rule; what a rule says,
        and who else may open it, is tested over the real store."""
        if project_id not in self.projects:
            raise KeyError(project_id)

    def may_erase(self, project_id: str) -> bool:
        """The view acts for no person, so it may delete every project and forget every note
        (H5b); who else may is tested over the real store."""
        if project_id not in self.projects:
            raise KeyError(project_id)
        return True

    def project_sharing(self, project_id: str) -> ProjectSharing:
        """Every project here is open to every member to write; private projects are tested over
        the real store."""
        if project_id not in self.projects:
            raise KeyError(project_id)
        return ProjectSharing(everyone="write", people=frozenset())

    def kept_from_models(self, project_ids) -> frozenset[str]:
        """M4: no project here is kept from models; the switch is tested over the real
        store."""
        return frozenset()

    def models_choices(self, project_ids) -> dict[str, str]:
        """M4: nobody here has chosen for a project (not decided)."""
        return {}

    def record_note(self, project_id: str, outcome: str, detail: dict, filed=None) -> None:
        if project_id not in self.projects:
            raise KeyError(project_id)

    def create_key(self, label: str) -> str:
        key = secrets.token_urlsafe(32)
        self.keys[_digest(key)] = label
        return key

    def revoke_key(self, label: str) -> bool:
        found = [digest for digest, owner in self.keys.items() if owner == label]
        for digest in found:
            del self.keys[digest]
        return bool(found)

    def resolve_key(self, key: str) -> Principal | None:
        label = self.keys.get(_digest(key)) if key else None
        if label is None:
            return None
        return Principal(account_id=DEFAULT_ACCOUNT_ID, kind="key", label=label)

    def identify_key(self, key: str) -> tuple[Principal, str] | None:
        principal = self.resolve_key(key)
        return (principal, "key_" + principal.label) if principal else None

    def create_session(self, ttl_seconds: int = 43_200) -> str:
        token = secrets.token_urlsafe(32)
        self.sessions[_digest(token)] = time.time() + ttl_seconds
        return token

    def end_session(self, token: str) -> None:
        self.sessions.pop(_digest(token), None)

    def resolve_session(self, token: str) -> Principal | None:
        if not token or self.sessions.get(_digest(token), 0) <= time.time():
            return None
        return Principal(account_id=DEFAULT_ACCOUNT_ID, kind="session")

    def identify_session(self, token: str) -> tuple[Principal, None] | None:
        principal = self.resolve_session(token)
        return (principal, None) if principal else None

    def create_project(
        self, name: str, description: str = "", kind: str = "general", *, everyone: str = "write"
    ) -> Project:
        if kind not in KINDS or not NAME.fullmatch(name):
            raise ValueError(f"bad kind, or bad project name: {name!r}")
        if self._by_name(name):
            raise NameTaken(name)
        project = Project(
            id="br_" + secrets.token_hex(6),
            name=name,
            description=description,
            created_at=_now(),
            kind=kind,
        )
        self.projects[project.id] = project
        return project

    def _by_name(self, name: str) -> Project | None:
        return next((b for b in self.projects.values() if b.name.lower() == name.lower()), None)

    def list_projects(self) -> list[Project]:
        return sorted(self.projects.values(), key=lambda b: b.name.lower())

    def get_project(self, ref: str) -> Project | None:
        return self.projects.get(ref) or self._by_name(ref)

    def add_note(self, project_id, text, *, title=None, topic=None, label=None, source_ref=None,
                    via=None):  # fmt: skip
        if project_id not in self.projects:
            raise KeyError(project_id)
        for note in self.notes.values():
            if (note.project_id, note.text, note.source_ref) == (project_id, text, source_ref):
                return note, False
        note_id = "cap_" + secrets.token_hex(6)
        spans = chunk(note_id, text)
        heading = next((s.heading_path[-1] for s in spans if s.kind == "heading"), None)
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        note = Note(
            id=note_id,
            project_id=project_id,
            text=text,
            title=(title or heading or first_line)[:80],
            topic=topic,
            label=label,
            source_ref=source_ref,
            via=via,
            created_at=_now(),
        )
        self.notes[note_id] = note
        self.spans[note_id] = spans
        return note, True

    def get_note(self, note_id: str) -> Note | None:
        return self.notes.get(note_id)

    def list_notes(self, project_id: str) -> list[Note]:
        return [c for c in self.notes.values() if c.project_id == project_id]

    def note_spans(self, note_id: str) -> list:
        return list(self.spans.get(note_id, []))

    def get_spans(self, span_ids) -> dict:
        known = {s.id: s for spans in self.spans.values() for s in spans}
        return {i: known[i] for i in span_ids if i in known}

    def save_page(self, project_id, slug, title, body, claims, metrics=None) -> Page:
        versions = self.pages.setdefault((project_id, slug), [])
        page = Page(
            project_id=project_id,
            slug=slug,
            title=title,
            body=body,
            version=len(versions) + 1,
            claims=tuple(claims),
            metrics=dict(metrics or {}),
            updated_at=_now(),
        )
        versions.append(page)
        return page

    def get_page(self, project_id: str, slug: str, version: int | None = None) -> Page | None:
        versions = self.pages.get((project_id, slug), [])
        if version is None:
            return versions[-1] if versions else None
        return versions[version - 1] if 1 <= version <= len(versions) else None

    def list_pages(self, project_id: str) -> list[Page]:
        return [v[-1] for (owner, _), v in sorted(self.pages.items()) if owner == project_id]

    def topic_page(self, project_id: str, key: str) -> str | None:
        """The page of topic key `key`, as fake_compile files one: by its slug."""
        slug = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-") or "untitled"
        return slug if (project_id, slug) in self.pages else None

    def taken_slugs(self, project_id: str, slugs) -> set[str]:
        return {slug for slug in slugs if (project_id, slug) in self.pages}

    def page_versions(self, project_id: str, slug: str) -> list[int]:
        return [p.version for p in self.pages.get((project_id, slug), [])]

    def bound_history(self, keep: int | None) -> None:  # Q1: create_app sets the bound
        self.keep_versions = keep

    # Q1: the reads that cost what they return, answered from the same dicts; what each
    # costs is tested over the real store.
    def page_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for owner, _ in self.pages:
            counts[owner] = counts.get(owner, 0) + 1
        return counts

    def count_notes(self, project_id: str) -> int:
        return len(self.list_notes(project_id))

    def page_heads(self, project_id: str, limit: int, after: str | None = None) -> tuple:
        pages = self.list_pages(project_id)
        heads = [
            PageHead(slug=p.slug, title=p.title, version=p.version, claims=len(p.claims),
                     updated_at=p.updated_at)
            for p in pages
            if after is None or p.slug > after
        ]  # fmt: skip
        return heads[:limit], len(pages)

    def version_numbers(self, project_id: str, slug: str, limit: int, before=None) -> tuple:
        numbers = self.page_versions(project_id, slug)
        below = [n for n in reversed(numbers) if before is None or n < before]
        return below[:limit], len(numbers), max(numbers, default=None)

    def get_notes(self, note_ids) -> dict:
        return {i: self.notes[i] for i in dict.fromkeys(note_ids) if i in self.notes}

    def projects_sharing(self, project_ids) -> dict:
        return {
            i: self.project_sharing(i) for i in dict.fromkeys(project_ids) if i in self.projects
        }

    def find(self, query, project_ids=None, limit=10):
        """Store.find (Q2): this fake's search, which never leaves anything out."""
        counted, left_out = ranking.split(query)
        hits = self.search(query, project_ids, limit)
        return ranking.found(counted, left_out, ranking.Ranked(hits, len(hits), len(hits), "all"))

    def search(self, query, project_ids=None, limit=10) -> list[SearchHit]:
        self.searches.append((query, project_ids, limit))
        words = set(re.findall(r"\w+", query.lower()))
        hits = []
        for (project_id, slug), versions in self.pages.items():
            if project_ids is not None and project_id not in project_ids:
                continue
            page = versions[-1]
            for claim in page.claims:
                score = len(words & set(re.findall(r"\w+", f"{claim.text} {page.title}".lower())))
                if score:
                    hits.append(
                        SearchHit(
                            project_id=project_id,
                            project=self.projects[project_id].name,
                            slug=slug,
                            title=page.title,
                            claim_id=claim.id,
                            text=claim.text,
                            span_ids=claim.span_ids,
                            version=page.version,
                            score=float(score),
                        )
                    )
        return sorted(hits, key=lambda h: -h.score)[:limit]


def fake_compile(store, note, gate) -> CompileResult:
    """A small extractive compile: one verbatim claim per new content span."""
    title = note.topic or note.title
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "untitled"
    existing = store.get_page(note.project_id, slug)
    version = existing.version if existing else None
    claims = list(existing.claims) if existing else []
    cited = {i for c in claims for i in c.span_ids}
    new = [s for s in store.note_spans(note.id) if s.kind in CONTENT_KINDS]
    new = [s for s in new if s.id not in cited]
    if not new:
        return CompileResult(note_id=note.id, slug=slug, status="unchanged", version=version)
    claims += [Claim(id="cl_" + s.id[3:], text=s.text, span_ids=(s.id,)) for s in new]
    body = "\n\n".join([f"# {title}", *(c.text for c in claims)]) + "\n"
    page = CandidatePage(slug=slug, title=title, body=body, claims=tuple(claims))
    result = gate(page, store.get_spans({i for c in claims for i in c.span_ids}))
    if not result.passed:
        return CompileResult(
            note_id=note.id, slug=slug, status="rejected", version=version, gate=result
        )
    saved = store.save_page(note.project_id, slug, title, body, page.claims, result.metrics)
    return CompileResult(
        note_id=note.id, slug=slug, status="saved", version=saved.version, gate=result
    )


def passing_gate(page: CandidatePage, spans) -> GateResult:
    cited = {i for c in page.claims for i in c.span_ids}
    return GateResult(passed=True, metrics={"claims": len(page.claims), "spans_cited": len(cited)})


@pytest.fixture(autouse=True)
def compile_in_memory(monkeypatch):
    monkeypatch.setattr(cairn.memory, "compile_note", fake_compile)


@pytest.fixture
def store():
    return FakeStore()


def serve(store, **kwargs) -> TestClient:
    kwargs.setdefault("gate", passing_gate)
    return TestClient(create_app(store, **kwargs), base_url=BASE)


@pytest.fixture
def env(store):
    key = store.create_key("test")
    with serve(store, public_url=BASE) as client:
        yield SimpleNamespace(client=client, key=key, store=store)


def rpc(client, key, method, params=None, headers=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post(
        "/mcp", headers={**MCP_HEADERS, "x-api-key": key, **(headers or {})}, json=body
    )


def login(env) -> None:
    r = env.client.post("/login", data={"key": env.key}, follow_redirects=False)
    assert r.status_code == 303


# Auth and sessions


def test_healthz_is_public(env):
    r = env.client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_a_rejected_key_gets_a_json_401_that_does_not_echo_it(env):
    wrong = env.key[::-1]
    for headers in ({"x-api-key": wrong}, {"authorization": f"Bearer {wrong}"}, {}):
        for path in ("/api/projects", "/api/nowhere"):
            r = env.client.get(path, headers=headers)
            assert r.status_code == 401 and r.json()["error"], (path, headers)
            assert wrong not in r.text and env.key not in r.text


def test_a_wrong_login_shows_the_form_again_without_the_submitted_key(env):
    wrong = "not-the-key-" + "x" * 32
    r = env.client.post("/login", data={"key": wrong}, follow_redirects=False)
    assert r.status_code == 401 and 'name="key"' in r.text
    assert wrong not in r.text and "set-cookie" not in r.headers


@pytest.mark.parametrize(
    ("public_url", "secure"), [("https://memory.example.net", True), (BASE, False), (None, False)]
)
def test_the_session_cookie_is_secure_only_behind_https(store, public_url, secure):
    key = store.create_key("test")
    with serve(store, public_url=public_url) as client:
        r = client.post("/login", data={"key": key}, follow_redirects=False)
    cookie = r.headers["set-cookie"].lower()
    assert ("; secure" in cookie) is secure
    assert key not in r.text and key not in cookie


def test_logout_ends_the_session_on_the_server(env):
    r = env.client.post("/login", data={"key": env.key}, follow_redirects=False)
    token = r.cookies[session_cookie()]
    env.client.post("/logout", follow_redirects=False)
    env.client.cookies.set(session_cookie(), token)
    assert env.client.get("/", follow_redirects=False).status_code == 303


def test_api_and_mcp_ignore_a_session_cookie(env):
    login(env)
    assert env.client.get("/api/projects").status_code == 401
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    assert env.client.post("/mcp", headers=MCP_HEADERS, json=body).status_code == 401


def test_ui_pages_send_protective_headers(env):
    pages = [env.client.get("/login")]
    login(env)
    pages.append(env.client.get("/"))
    for r in pages:
        assert r.status_code == 200
        assert "default-src 'none'" in r.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["cache-control"] == "no-store"


def test_a_path_that_is_no_page_is_404_signed_in_or_not(env):
    """A path no route serves is not a page behind the login: 404 for everyone. A path that
    is a page for another method still asks for a session; /api keeps its JSON 401."""
    for path in ("/server", "/nowhere/at/all", "/docs/x/y"):
        r = env.client.get(path, follow_redirects=False)
        assert r.status_code == 404 and "text/html" in r.headers["content-type"], path
        assert "set-cookie" not in r.headers
        assert 'href="/login"' in r.text and 'href="/workspace"' not in r.text
    assert env.client.get("/projects", follow_redirects=False).status_code == 303  # POST only
    assert env.client.get("/api/nowhere").status_code == 401
    login(env)
    signed_in = env.client.get("/server", follow_redirects=False)
    assert signed_in.status_code == 404  # and the reader keeps the app's own navigation
    assert 'href="/workspace"' in signed_in.text and 'href="/login"' not in signed_in.text


def test_static_files_are_public(env):
    r = env.client.get("/static/cairn.css")
    assert r.status_code == 200 and "text/css" in r.headers["content-type"]


def test_a_cards_labels_are_in_the_interface_font_whatever_the_cell_holds(env):
    # On a phone a row's cells read "label: value"; a cell in the code font ("What" in the
    # audit log) must not carry its label into that font.
    css = env.client.get("/static/cairn.css").text
    phone = css.split("@media (max-width: 40rem) {\n  .rows, .rows tbody", 1)[1]
    rule = re.search(r"\.rows td::before[^{]*\{([^}]*)\}", phone)
    assert rule and "attr(data-label)" in rule[1]
    assert "font-family: var(--font-sans)" in rule[1] and "font-size: 13px" in rule[1]


def test_websockets_are_refused_before_routing(env):
    for path in ("/api/projects", "/", "/healthz"):
        with pytest.raises(WebSocketDisconnect) as refused, env.client.websocket_connect(path):
            pass
        assert refused.value.code == 1008  # policy violation, not the router's plain close


# The MCP host allow-list


def test_mcp_accepts_loopback_the_public_host_and_allowed_hosts(store):
    key = store.create_key("test")
    app_args = {
        "public_url": "https://memory.example.net",
        "allowed_hosts": ["cairn.tail.example:8443", "box.example"],
    }
    with serve(store, **app_args) as client:

        def status(host: str, origin: str | None = None) -> int:
            headers = {"host": host, **({"origin": origin} if origin else {})}
            return rpc(client, key, "tools/list", headers=headers).status_code

        for host in (
            "127.0.0.1:8765",
            "localhost:8765",
            "memory.example.net",  # portless, as behind an https proxy
            "memory.example.net:443",
            "cairn.tail.example:8443",
            "box.example",
            "box.example:8765",
        ):
            assert status(host) == 200, host
        assert status("memory.example.net", "https://memory.example.net") == 200
        assert status("evil.example") == 421
        assert status("memory.example.net.evil.example") == 421
        assert status("memory.example.net", "https://evil.example") == 403


# MCP tools and the REST API


def call(env, name, **arguments) -> dict:
    r = rpc(env.client, env.key, "tools/call", {"name": name, "arguments": arguments})
    assert r.status_code == 200, r.text
    return r.json()["result"]


def ok(env, name, **arguments) -> dict:
    result = call(env, name, **arguments)
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def api(env, method, path, **kwargs):
    return env.client.request(method, path, headers={"x-api-key": env.key}, **kwargs)


def test_read_tools_say_they_are_read_only(env):
    tools = rpc(env.client, env.key, "tools/list").json()["result"]["tools"]
    hints = {t["name"]: t.get("annotations", {}).get("readOnlyHint") for t in tools}
    assert hints == {
        "projects": True,
        "recall": True,
        "page": True,
        "evidence": True,
        "overview": True,
        "note": False,
        "new_project": False,
        "graph": True,  # 8.3
    }
    assert all(t["description"] for t in tools)


def test_a_note_needs_text(env):
    env.store.create_project("alpha")
    result = call(env, "note", project="alpha", text=" \n ")
    assert result["isError"] is True and "empty" in result["content"][0]["text"]
    assert api(env, "POST", "/api/projects/alpha/notes", json={"text": "\n"}).status_code == 400
    assert env.store.notes == {}


def test_notes_record_the_via_they_came_through(env):
    env.store.create_project("alpha")
    ok(env, "note", project="alpha", text=NOTE, source_ref="t:mcp")
    api(env, "POST", "/api/projects/alpha/notes", json={"text": NOTE, "source_ref": "t:api"})
    via_values = {c.source_ref: c.via for c in env.store.notes.values()}
    assert via_values == {"t:mcp": "mcp", "t:api": "api"}


def test_a_duplicate_rest_note_is_reported_not_stored_twice(env):
    env.store.create_project("alpha")
    first = api(env, "POST", "/api/projects/alpha/notes", json={"text": NOTE}).json()
    r = api(env, "POST", "/api/projects/alpha/notes", json={"text": NOTE})
    assert r.status_code == 201
    again = r.json()
    assert (again["created"], again["note_id"], again["status"]) == (
        False,
        first["note_id"],
        "unchanged",
    )
    assert again["version"] == first["version"] == 1 and again["violations"] == []


def test_a_gate_rejection_is_reported_with_its_violations(store):
    key = store.create_key("test")
    store.create_project("alpha")

    def reject(page, spans):
        return GateResult(passed=False, violations=["test.reject: cl_x: no"])

    with serve(store, gate=reject) as client:
        env = SimpleNamespace(client=client, key=key, store=store)
        result = ok(env, "note", project="alpha", text=NOTE)
    assert (result["status"], result["version"], result["created"]) == ("rejected", None, True)
    assert result["violations"] == ["test.reject: cl_x: no"]
    assert store.pages == {}


def test_the_default_gate_is_the_provenance_gate(store, monkeypatch):
    seen = []

    def spy(store, note, gate):
        seen.append(gate)
        return fake_compile(store, note, passing_gate)

    monkeypatch.setattr(cairn.memory, "compile_note", spy)
    key = store.create_key("test")
    store.create_project("alpha")
    with TestClient(create_app(store), base_url=BASE) as client:
        r = client.post(
            "/api/projects/alpha/notes", headers={"x-api-key": key}, json={"text": NOTE}
        )
    assert r.status_code == 201 and seen == [compiler.provenance_gate]


def test_a_recall_limit_outside_1_to_50_is_refused_before_the_store_is_searched(env):
    env.store.create_project("alpha")
    for limit in (0, -3, 51, 1000):
        assert call(env, "recall", query="quill", limit=limit)["isError"] is True, limit
        r = api(env, "GET", "/api/search", params={"q": "quill", "limit": str(limit)})
        assert r.status_code == 400, limit
    for limit in (1, 50):
        ok(env, "recall", query="quill", limit=limit)
        assert api(env, "GET", "/api/search", params={"q": "quill", "limit": limit}).is_success
    assert [limit for _, _, limit in env.store.searches] == [1, 1, 50, 50]


def test_rest_search_runs_off_the_event_loop(env, monkeypatch):
    """A search reads the database under the store's lock: on the event loop it would hold
    up every other request meanwhile."""
    seen = []

    def search(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            seen.append("on the event loop")
        except RuntimeError:
            seen.append("in a worker thread")
        return []

    monkeypatch.setattr(env.store, "search", search)
    assert api(env, "GET", "/api/search", params={"q": "quill"}).status_code == 200
    assert seen == ["in a worker thread"]


def test_an_empty_project_filter_searches_every_project(env):
    env.store.create_project("alpha")
    ok(env, "note", project="alpha", text=NOTE)
    assert ok(env, "recall", query="quill", project="")["hits"]
    r = api(env, "GET", "/api/search", params={"q": "quill", "project": ""})
    assert r.status_code == 200 and r.json()["hits"]


def test_rest_rejects_malformed_bodies_and_parameters(env):
    env.store.create_project("alpha")
    created = api(env, "POST", "/api/projects/alpha/notes", json={"text": NOTE}).json()
    bad_requests = [
        ("POST", "/api/projects", {"content": b"{not json"}),
        ("POST", "/api/projects", {"json": ["alpha"]}),
        ("POST", "/api/projects", {"json": {}}),
        ("POST", "/api/projects", {"json": {"name": 7}}),
        ("POST", "/api/projects", {"json": {"name": "gamma", "description": ["x"]}}),
        ("POST", "/api/projects/alpha/notes", {"json": {"title": "no text"}}),
        ("POST", "/api/projects/alpha/notes", {"json": {"text": NOTE, "source_ref": 3}}),
        ("GET", f"/api/projects/alpha/pages/{created['page']}", {"params": {"version": "two"}}),
        ("GET", "/api/search", {"params": {"q": "quill", "limit": "many"}}),
    ]
    for method, path, kwargs in bad_requests:
        r = api(env, method, path, **kwargs)
        assert r.status_code == 400 and r.json()["error"], (method, path, kwargs)
    assert set(env.store.projects.values()) == {env.store.get_project("alpha")}


def test_rest_reports_unknown_things_as_json_404s(env):
    env.store.create_project("alpha")
    page = api(env, "POST", "/api/projects/alpha/notes", json={"text": NOTE}).json()["page"]
    for method, path in [
        ("POST", "/api/projects/gamma/notes"),
        ("GET", "/api/projects/alpha/pages/nope"),
        ("GET", f"/api/projects/alpha/pages/{page}?version=9"),
        ("GET", f"/api/projects/alpha/pages/{page}/claims/cl_nope/evidence"),
        ("GET", "/api/search?q=x&project=gamma"),
        ("GET", "/api/nowhere"),
    ]:
        r = api(env, method, path, json={"text": NOTE} if method == "POST" else None)
        assert r.status_code == 404 and r.json()["error"], path


def test_trace_never_shows_a_span_from_another_project(env):
    alpha, beta = env.store.create_project("alpha"), env.store.create_project("beta")
    foreign, _ = env.store.add_note(beta.id, "# Beta\n\nbeta secret plan\n")
    span = next(s for s in env.store.note_spans(foreign.id) if s.kind == "paragraph")
    claim = Claim(id="cl_borrowed", text=span.text, span_ids=(span.id,))
    env.store.save_page(
        alpha.id, "borrowed", "Borrowed", "# Borrowed\n\nbeta secret plan\n", [claim]
    )
    traced = ok(env, "evidence", project="alpha", slug="borrowed", claim_id="cl_borrowed")
    assert traced["claim"]["id"] == "cl_borrowed" and traced["passages"] == []


def test_trace_names_the_note_behind_each_span(env):
    env.store.create_project("alpha")
    noted = ok(env, "note", project="alpha", text=NOTE, source_ref="test:origin")
    claim = ok(env, "page", project="alpha", slug=noted["page"])["claims"][0]
    (span,) = ok(env, "evidence", project="alpha", slug=noted["page"], claim_id=claim["id"])[
        "passages"
    ]
    assert (span["note_id"], span["source_ref"]) == (noted["note_id"], "test:origin")
    assert span["heading_path"] == ["Retry policy"] and span["text"] == claim["text"]


def test_request_bodies_are_capped_at_4_mib(env):
    env.store.create_project("alpha")
    r = api(env, "POST", "/api/projects/alpha/notes", json={"text": "x" * (4 * 1024 * 1024)})
    assert r.status_code == 413 and env.store.notes == {}


# Web UI


def seed_page(store, project_name: str = "alpha") -> Page:
    project = store.create_project(project_name)
    note, _ = store.add_note(project.id, NOTE, source_ref="test:ui")
    return store.get_page(project.id, fake_compile(store, note, passing_gate).slug)


def test_home_lists_projects_with_page_counts_and_a_search_box(env):
    login(env)
    seed_page(env.store)
    env.store.create_project("beta", "the second project")
    text = env.client.get("/").text
    assert 'href="/b/alpha"' in text and 'href="/b/beta"' in text
    assert "the second project" in text and "1 page" in text and "0 pages" in text
    assert 'action="/search"' in text


def test_a_project_page_searches_within_that_project(env):
    login(env)
    seed_page(env.store)
    text = env.client.get("/b/alpha").text
    assert '<input type="hidden" name="project" value="alpha">' in text
    assert 'href="/b/alpha/p/retry-policy"' in text


def test_the_page_view_marks_sources_it_cannot_show(env):
    login(env)
    alpha, beta = env.store.create_project("alpha"), env.store.create_project("beta")
    foreign, _ = env.store.add_note(beta.id, "# Beta\n\nbeta secret plan\n")
    span = next(s for s in env.store.note_spans(foreign.id) if s.kind == "paragraph")
    claims = [
        Claim(id="cl_lost", text="lost", span_ids=("sp_0000000000000000",)),
        Claim(id="cl_borrowed", text="borrowed", span_ids=(span.id,)),
    ]
    env.store.save_page(alpha.id, "odd", "Odd", "# Odd\n\nlost\n\nborrowed\n", claims)
    r = env.client.get("/b/alpha/p/odd")
    assert r.status_code == 200
    for span_id in ("sp_0000000000000000", span.id):
        assert f'id="{span_id}"' in r.text and f'href="#{span_id}"' in r.text
    assert "beta secret plan" not in r.text  # another project's source stays out
    assert r.text.count("was not found in this project") == 2


def test_the_page_view_escapes_what_it_shows(env):
    login(env)
    project = env.store.create_project("alpha")
    hostile = "<script>alert(1)</script> & <b>bold</b>"
    note, _ = env.store.add_note(project.id, f"# Hostile\n\n{hostile}\n")
    fake_compile(env.store, note, passing_gate)
    text = env.client.get("/b/alpha/p/hostile").text
    assert "<script>" not in text and "<b>" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &lt;b&gt;bold&lt;/b&gt;" in text


def test_the_page_view_shows_sections_and_rounded_metrics(env):
    login(env)
    project = env.store.create_project("alpha")
    text = "# Tools\n\n## Quill\n\nQuill retries.\n\n## Lumen\n\nLumen waits.\n"
    note, _ = env.store.add_note(project.id, text)
    spans = [s for s in env.store.note_spans(note.id) if s.kind == "paragraph"]
    claims = [Claim(id="cl_" + s.id[3:], text=s.text, span_ids=(s.id,)) for s in spans]
    metrics = {"claims": 2, "retention": 20 / 21}
    env.store.save_page(project.id, "tools", "Tools", "# Tools\n", claims, metrics)
    html = env.client.get("/b/alpha/p/tools").text
    assert '<h2 class="section">Quill</h2>' in html and '<h2 class="section">Lumen</h2>' in html
    # The page's <time datetime> stamps hold microseconds, and "17:55:10.952381" holds
    # "0.95238" too: the value is read in its own row, and the page without its stamps.
    row = re.search(r'<tr data-metric="retention">(.*?)</tr>', html, re.S)
    assert row, "no retention row"
    assert '<td data-label="Value" class="num">0.952</td>' in row.group(1)
    assert "0.95238" not in re.sub(r'\bdatetime="[^"]*"', "", html)


def test_the_page_footer_says_its_checks_and_who_cites_a_source_in_words(env):
    login(env)
    project = env.store.create_project("alpha")
    note, _ = env.store.add_note(project.id, "# Tools\n\nQuill retries.\n\nLumen waits.\n")
    spans = [s for s in env.store.note_spans(note.id) if s.kind == "paragraph"]
    claims = [Claim(id="cl_" + s.id[3:], text=s.text, span_ids=(s.id,)) for s in spans]
    metrics = {"claims": 2, "spans_cited": 2, "retention": 1.0, "invented_here": 7}
    env.store.save_page(project.id, "tools", "Tools", "# Tools\n", claims, metrics)
    html = env.client.get("/b/alpha/p/tools").text
    assert "Gate metrics" not in html
    checks = html.split("<h2>Checks</h2>", 1)[1].split("</table>", 1)[0]
    shown = re.findall(r'<td data-label="Check">([^<]+)</td>', checks)
    assert shown == [
        "Claims",
        "Source passages cited",
        "Facts of the sources kept",
        "invented_here",
    ]
    sources = html.split('<ol class="sources">', 1)[1]
    assert f'Cited by <a href="#{claims[1].id}">claim 2</a>' in sources
    assert f">{claims[0].id}<" not in sources  # the number, not the id


def test_an_old_version_links_to_the_latest_and_marks_itself(env):
    login(env)
    project = env.store.create_project("alpha")
    for text in (NOTE, "# Retry policy\n\nLumen waits 2 s between tries.\n"):
        note, _ = env.store.add_note(project.id, text)
        fake_compile(env.store, note, passing_gate)
    old = env.client.get("/b/alpha/p/retry-policy", params={"version": 1}).text
    assert "Lumen waits" not in old
    assert 'href="/b/alpha/p/retry-policy?version=2"' in old
    assert 'href="/b/alpha/p/retry-policy"' in old  # back to the latest
    latest = env.client.get("/b/alpha/p/retry-policy").text
    assert "Lumen waits" in latest and 'href="/b/alpha/p/retry-policy?version=1"' in latest
    assert 'href="/b/alpha/p/retry-policy?version=2"' not in latest


def test_ui_errors_are_html_pages(env):
    login(env)
    seed_page(env.store)
    for path, status in [
        ("/b/alpha/p/nope", 404),
        ("/b/alpha/p/retry-policy?version=7", 404),
        ("/b/alpha/p/retry-policy?version=x", 400),
        ("/b/zzz", 404),
        ("/search?q=x&project=zzz", 404),
        ("/nowhere", 404),
    ]:
        r = env.client.get(path)
        assert r.status_code == status, path
        assert r.headers["content-type"].startswith("text/html"), path
        assert "default-src 'none'" in r.headers["content-security-policy"], path


def test_search_keeps_the_query_escaped_and_the_project_choice(env):
    login(env)
    seed_page(env.store)
    env.store.create_project("beta")
    r = env.client.get("/search", params={"q": 'quill "><script>x</script>', "project": "alpha"})
    assert r.status_code == 200 and "<script>x</script>" not in r.text
    assert '<option value="alpha" selected>' in r.text
    assert "/b/alpha/p/retry-policy#cl_" in r.text
    everywhere = env.client.get("/search", params={"q": "quill", "project": ""})
    assert everywhere.status_code == 200 and "/b/alpha/p/retry-policy#cl_" in everywhere.text


def test_setup_shows_placeholders_until_a_public_url_is_set(store):
    key = store.create_key("test")
    with serve(store) as client:
        token = client.post("/login", data={"key": key}, follow_redirects=False).cookies[
            session_cookie()
        ]
        text = client.get("/setup").text
    assert "https://&lt;your-host&gt;/mcp" in text
    assert "CAIRN_URL=https://&lt;your-host&gt;" in text and "CAIRN_API_KEY=&lt;" in text
    # The key goes into a file only its owner reads and from there into the agent's
    # environment; the connect guide has each client's steps. Nothing here is one person's
    # own tooling: no private directory, no script that is not in this repository.
    assert "<code>cairn.env</code>" in text and "mode 0600" in text
    assert 'href="/docs/connect"' in text
    assert "agent-vault" not in text and "client-mcp-manager" not in text and "node " not in text
    assert "python -m cairn.server key-create --db PATH --label L --out FILE" in text
    assert "named after the repo" in text
    assert key not in text and token not in text


# The command line (over the real store)


def key_create(db, out, label: str = "laptop") -> int:
    return server.main(["key-create", "--db", str(db), "--label", label, "--out", str(out)])


def test_key_create_writes_a_private_env_file_and_prints_only_its_path(tmp_path, capsys):
    db, out = tmp_path / "cairn.db", tmp_path / "laptop.env"
    assert key_create(db, out) == 0
    printed = capsys.readouterr()
    assert (printed.out, printed.err) == (f"{out}\n", "")
    assert out.stat().st_mode & 0o777 == 0o600
    name, _, key = out.read_text().strip().partition("=")
    assert name == "CAIRN_API_KEY" and len(key) >= 32
    store = Store(db)
    try:
        assert store.check_key(key)
    finally:
        store.close()


def test_key_create_never_overwrites_or_follows_a_link(tmp_path, capsys):
    db, taken, link = tmp_path / "cairn.db", tmp_path / "taken.env", tmp_path / "link.env"
    taken.write_text("CAIRN_URL=https://example.invalid\n")
    link.symlink_to(tmp_path / "elsewhere.env")
    assert key_create(db, taken) == 1 and key_create(db, link) == 1
    printed = capsys.readouterr()
    assert printed.out == "" and "already exists" in printed.err
    assert taken.read_text() == "CAIRN_URL=https://example.invalid\n"
    assert not (tmp_path / "elsewhere.env").exists()
    store = Store(db)
    try:
        assert store.revoke_key("laptop") is False  # no key was minted
    finally:
        store.close()


def test_key_create_starts_a_database_in_a_new_directory(tmp_path):
    db, out = tmp_path / "runs" / "dev" / "cairn.db", tmp_path / "dev.env"
    assert key_create(db, out) == 0
    store = Store(db)
    try:
        assert store.check_key(out.read_text().strip().partition("=")[2])
    finally:
        store.close()


def test_key_create_reports_what_it_cannot_create_and_leaves_no_half_made_file(
    tmp_path, capsys, monkeypatch
):
    assert key_create(tmp_path / "cairn.db", tmp_path / "missing" / "k.env") == 1
    printed = capsys.readouterr()
    assert printed.out == "" and "cannot create" in printed.err

    class FailingStore(Store):
        def create_key(self, label: str) -> str:
            raise RuntimeError("disk full")

    monkeypatch.setattr(cairn.web.cli, "Store", FailingStore)
    out = tmp_path / "k.env"
    with pytest.raises(RuntimeError):
        key_create(tmp_path / "cairn.db", out)
    assert not out.exists()


def test_labels_cannot_be_blank_and_revoking_needs_a_database(tmp_path, capsys):
    db, out = tmp_path / "cairn.db", tmp_path / "k.env"
    for argv in (
        ["key-create", "--db", str(db), "--label", " ", "--out", str(out)],
        ["key-revoke", "--db", str(db), "--label", ""],
    ):
        with pytest.raises(SystemExit) as stopped:
            server.main(argv)
        assert stopped.value.code == 2
    assert not out.exists()
    missing = tmp_path / "nowhere" / "cairn.db"
    assert server.main(["key-revoke", "--db", str(missing), "--label", "laptop"]) == 1
    assert "no database" in capsys.readouterr().err and not missing.parent.exists()


def test_key_revoke_retires_every_key_with_the_label(tmp_path, capsys):
    db = tmp_path / "cairn.db"
    store = Store(db)
    laptop, phone = [store.create_key("laptop"), store.create_key("laptop")], store.create_key("p")
    store.close()
    revoke = ["key-revoke", "--db", str(db), "--label", "laptop"]
    assert server.main(revoke) == 0 and server.main(revoke) == 1
    printed = capsys.readouterr()
    assert not any(key in printed.out + printed.err for key in [*laptop, phone])
    store = Store(db)
    try:
        assert not any(store.check_key(key) for key in laptop) and store.check_key(phone)
    finally:
        store.close()


def test_serve_binds_loopback_by_default_and_creates_the_database_directory(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: calls.append((app, options)))
    db = tmp_path / "runs" / "dev" / "cairn.db"
    assert server.main(["serve", "--db", str(db)]) == 0
    ((app, options),) = calls
    assert isinstance(app, Starlette)
    # No access log: uvicorn's would print query strings, where OAuth carries `state` and the
    # sealed authorization request.
    # No proxy is trusted unless named (--trusted-proxy), not even loopback.
    assert options == {
        "host": "127.0.0.1",
        "port": 8765,
        "access_log": False,
        "proxy_headers": False,
        "forwarded_allow_ips": [],
    }
    assert db.parent.is_dir()


def test_serve_passes_the_public_url_and_allowed_hosts_to_the_app(tmp_path, monkeypatch):
    db = tmp_path / "cairn.db"
    store = Store(db)
    key = store.create_key("test")
    store.close()
    seen = {}

    def run(app, *, host, port, access_log, proxy_headers, forwarded_allow_ips):
        seen.update(host=host, port=port, access_log=access_log, proxy_headers=proxy_headers)
        with TestClient(app, base_url=BASE) as client:
            for name in ("memory.example.net", "box.example:8765", "evil.example"):
                seen[name] = rpc(client, key, "tools/list", headers={"host": name}).status_code
            cookie = client.post("/login", data={"key": key}, follow_redirects=False)
            seen["secure"] = "; secure" in cookie.headers["set-cookie"].lower()

    monkeypatch.setattr(uvicorn, "run", run)
    argv = ["serve", "--db", str(db), "--host", "127.0.0.2", "--port", "9000"]
    argv += ["--public-url", "https://memory.example.net", "--allowed-host", "box.example"]
    assert server.main(argv) == 0
    assert seen == {
        "host": "127.0.0.2",
        "port": 9000,
        "access_log": False,
        "proxy_headers": False,
        "memory.example.net": 200,
        "box.example:8765": 200,
        "evil.example": 421,
        "secure": True,
    }
    with pytest.raises(SystemExit):
        server.main(["serve", "--db", str(db), "--public-url", "memory.example.net"])


def test_serve_writes_the_access_log_one_plain_line_per_call(tmp_path, monkeypatch, capsys):
    db = tmp_path / "cairn.db"
    store = Store(db)
    key = store.create_key("ci-runner")
    key_id = store.identify_key(key)[1]
    store.close()
    access = logging.getLogger(cairn.memory.ACCESS_LOGGER)
    before = (list(access.handlers), access.propagate, access.level)
    numbers = logging.getLogger(STATS_LOGGER)  # Q1: the line with the numbers, on by default
    numbers_before = (list(numbers.handlers), numbers.propagate, numbers.level)
    seen = {}

    def run(app, *, host, port, access_log, **proxy):
        # One line each, whatever else the root logger (configured by the MCP SDK) prints.
        seen["propagate"] = access.propagate
        with TestClient(app, base_url=BASE) as client:
            h = {"x-api-key": key}
            assert client.get("/api/search?q=hunter2-in-a-query", headers=h).status_code == 200
            assert client.get("/api/projects/nowhere/pages", headers=h).status_code == 404

    monkeypatch.setattr(uvicorn, "run", run)
    assert server.main(["serve", "--db", str(db)]) == 0
    lines = capsys.readouterr().err.splitlines()
    stats_lines = [line for line in lines if line.startswith(f"{STATS_LOGGER}: stats {{")]
    assert [line for line in lines if line not in stats_lines] == [
        f"cairn.access: recall by key 'ci-runner' ({key_id}), project None: 200",
        f"cairn.access: pages by key 'ci-runner' ({key_id}), project None: 404",
    ]
    assert len(stats_lines) == 1  # Q1: the one at start, on a logger of its own
    assert seen == {"propagate": False}
    # When serve returns, the loggers are as it found them: serve leaves no process-wide state.
    assert (list(access.handlers), access.propagate, access.level) == before
    assert (list(numbers.handlers), numbers.propagate, numbers.level) == numbers_before


def test_the_module_runs_as_a_command(tmp_path):
    out = tmp_path / "ci.env"
    argv = ["key-create", "--db", str(tmp_path / "cairn.db"), "--label", "ci", "--out", str(out)]
    done = subprocess.run(
        [sys.executable, "-m", "cairn.server", *argv],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    key = out.read_text().strip().partition("=")[2]
    assert (done.returncode, done.stdout) == (0, f"{out}\n")
    assert key not in done.stdout + done.stderr


def test_serve_gates_notes_with_the_lint_gate(tmp_path, monkeypatch):
    db = tmp_path / "cairn.db"
    store = Store(db)
    key = store.create_key("test")
    store.close()
    seen = {}

    def run(app, *, host, port, access_log, **proxy):
        with TestClient(app, base_url=BASE) as client:
            h = {"x-api-key": key}
            client.post("/api/projects", headers=h, json={"name": "alpha"})
            text = "# Timing\n\nStarted 2026-03-14T10:00:00Z with --fast.\n"
            note = client.post("/api/projects/alpha/notes", headers=h, json={"text": text}).json()
            seen["status"] = note["status"]
            page = client.get(f"/api/projects/alpha/pages/{note['page']}", headers=h).json()
            seen["metrics"] = page["metrics"]

    monkeypatch.setattr(uvicorn, "run", run)
    assert server.main(["serve", "--db", str(db)]) == 0
    assert seen["status"] == "saved"
    assert {"retention", "code_line_retention", "passage_coverage", "omission_rate"} <= set(
        seen["metrics"]
    )


# From the cross-vendor security review (2026-11-10)


def test_ui_search_by_project_id_searches_exactly_that_project(env, monkeypatch):
    alpha = env.store.create_project("alpha")
    clash = Project(id="br_" + "b" * 12, name=alpha.id, created_at=alpha.created_at)
    env.store.projects[clash.id] = clash  # a name equal to another project's id (store forbids it)
    seen = []

    def search(query, *args, **kwargs):
        seen.append(kwargs.get("project_ids", args[0] if args else None))
        return []

    monkeypatch.setattr(env.store, "search", search)
    login(env)
    assert env.client.get("/search", params={"q": "marker", "project": clash.id}).status_code == 200
    assert seen == [[clash.id]]


@pytest.mark.parametrize(
    "headers",
    [
        {"sec-fetch-site": "same-site"},
        {"sec-fetch-site": "cross-site"},
        {"origin": "https://evil.example.net"},
        {"origin": "null"},
    ],
)
def test_a_logout_posted_from_another_origin_is_refused(env, headers):
    login(env)
    r = env.client.post("/logout", headers=headers, follow_redirects=False)
    assert r.status_code == 403
    assert env.client.get("/", follow_redirects=False).status_code == 200


def test_a_same_origin_logout_still_works(env):
    login(env)
    headers = {"sec-fetch-site": "same-origin", "origin": BASE}
    assert env.client.post("/logout", headers=headers, follow_redirects=False).status_code == 303
    assert env.client.get("/", follow_redirects=False).status_code == 303


def test_a_login_posted_from_another_site_sets_no_session(env):
    r = env.client.post(
        "/login",
        data={"key": env.key},
        headers={"sec-fetch-site": "cross-site"},
        follow_redirects=False,
    )
    assert r.status_code == 403 and "set-cookie" not in r.headers


def test_login_from_the_public_origin_behind_a_proxy_works(store):
    key = store.create_key("t")
    with serve(store, public_url="https://memory.example.net") as client:
        headers = {"origin": "https://memory.example.net"}
        r = client.post("/login", data={"key": key}, headers=headers, follow_redirects=False)
        assert r.status_code == 303


def test_api_posts_rely_on_the_key_not_the_origin(env):
    headers = {"x-api-key": env.key, "origin": "https://evil.example.net"}
    r = env.client.post("/api/projects", headers=headers, json={"name": "alpha"})
    assert r.status_code == 201


# Tenancy (beyond the frozen H2 contract)

MODERN = "2026-07-28"  # the SDK serves this revision on another code path than the earlier ones


def tool_call(name: str, arguments: dict, *, modern: bool = False) -> tuple[dict, dict]:
    """Headers and body of a tools/call: handshake-era by default, else a 2026-07-28 request."""
    headers, params = dict(MCP_HEADERS), {"name": name, "arguments": arguments}
    if modern:
        headers |= {"mcp-protocol-version": MODERN, "mcp-method": "tools/call", "mcp-name": name}
        params["_meta"] = {PROTOCOL_VERSION_META_KEY: MODERN, CLIENT_CAPABILITIES_META_KEY: {}}
    return headers, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}


@pytest.mark.parametrize("modern", [False, True], ids=["handshake-era", MODERN])
def test_overlapping_calls_from_two_accounts_each_stay_in_their_own(monkeypatch, modern):
    """The SDK runs a sync tool on a worker thread, as Starlette does a sync REST handler. A
    barrier holds four calls (two accounts, each over MCP and over REST) inside Memory.projects
    at the same moment, so none of them can have finished before another began; each must
    still answer from its own caller's account. Both of the SDK's wire paths are covered."""
    store = Store(":memory:")
    keys = {}
    for name in ("ada", "brook"):
        view = store.for_account(store.create_account(name).id)
        view.create_project(f"only-{name}")
        keys[name] = view.create_key("laptop")
    together = threading.Barrier(4, timeout=10)  # broken, and every call fails, without overlap
    threads = []
    listing = server.Memory.projects

    def held(memory):
        together.wait()
        threads.append(threading.current_thread())
        return listing(memory)

    monkeypatch.setattr(server.Memory, "projects", held)
    app = create_app(store, gate=passing_gate, public_url=BASE)
    tool_headers, body = tool_call("projects", {}, modern=modern)

    async def over_mcp(client: httpx.AsyncClient, name: str) -> list[str]:
        headers = {**tool_headers, "x-api-key": keys[name]}
        r = await client.post("/mcp", headers=headers, json=body)
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        assert result["isError"] is False, result
        assert ("resultType" in result) is modern  # only the 2026-07-28 path answers with one
        return [b["name"] for b in json.loads(result["content"][0]["text"])["projects"]]

    async def over_rest(client: httpx.AsyncClient, name: str) -> list[str]:
        r = await client.get("/api/projects", headers={"x-api-key": keys[name]})
        assert r.status_code == 200, r.text
        return [b["name"] for b in r.json()["projects"]]

    async def main() -> list[list[str]]:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
                calls = [via(client, name) for name in keys for via in (over_mcp, over_rest)]
                return await asyncio.gather(*calls)

    try:
        answers = asyncio.run(main())
    finally:
        store.close()
    assert answers == [["only-ada"], ["only-ada"], ["only-brook"], ["only-brook"]]
    assert len(threads) == 4 and threading.main_thread() not in threads


def test_handlers_reached_without_auth_fail_closed():
    """Auth puts the caller's Memory on the request. A handler or tool that runs without it has
    no account to act for, and must not fall back to one: a tool error over MCP, a 500 from
    REST and the UI."""
    mcp = server.build_mcp()
    mcp_app = mcp.streamable_http_app(json_response=True, stateless_http=True)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

    routes = [*server.rest_routes(), *server.ui_routes(None), Route("/mcp", mcp_app)]
    app = Starlette(routes=routes, lifespan=lifespan)
    with TestClient(app, base_url=BASE, raise_server_exceptions=False) as client:
        for modern in (False, True):
            headers, body = tool_call("projects", {}, modern=modern)
            r = client.post("/mcp", headers=headers, json=body)
            assert r.status_code == 200, r.text
            result = r.json()["result"]
            assert result["isError"] is True and "projects" in result["content"][0]["text"]
            assert "{" not in result["content"][0]["text"]  # an error, not a listing
        assert client.get("/api/projects").status_code == 500
        assert client.get("/").status_code == 500


def test_a_credential_whose_account_is_gone_is_refused(env):
    """A key or session can resolve just before its account is deleted. The request is then
    unauthenticated: it is not served from another account, and it is not a server error."""
    login(env)
    env.store.create_project("alpha")
    env.store.accounts.clear()
    assert api(env, "GET", "/api/projects").status_code == 401
    assert rpc(env.client, env.key, "tools/list").status_code == 401
    r = env.client.get("/", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/login")
    r = env.client.post("/login", data={"key": env.key}, follow_redirects=False)
    assert r.status_code == 401 and "set-cookie" not in r.headers


def test_no_tool_takes_the_request_context_as_an_argument(env):
    """Tools read the caller's account from the SDK's request context, a parameter the SDK
    fills in. It must never show up in a tool's input schema."""
    tools = rpc(env.client, env.key, "tools/list").json()["result"]["tools"]
    arguments = {
        t["name"]: (set(t["inputSchema"]["properties"]), set(t["inputSchema"].get("required", [])))
        for t in tools
    }
    assert arguments == {
        "projects": (set(), set()),
        "new_project": ({"name", "description", "kind", "private"}, {"name"}),  # U1, T1
        "note": ({"project", "text", "title", "topic", "label", "source_ref"}, {"project", "text"}),
        "recall": ({"query", "project", "limit"}, {"query"}),
        "page": ({"project", "slug", "version", "limit", "before"}, {"project", "slug"}),  # Q1
        "evidence": (
            {"project", "slug", "claim_id", "version"},  # E1
            {"project", "slug", "claim_id"},
        ),
        "overview": ({"project", "limit", "after"}, {"project"}),  # Q1
        "graph": ({"project", "limit"}, {"project"}),  # 8.3
    }
