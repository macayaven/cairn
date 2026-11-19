"""System test: the real server process, over real HTTP, across features.

The acceptance files check one feature each, inside the test process. This file starts
`python -m cairn.server serve` as a subprocess on a loopback port and walks through what a
person and their agents actually do, end to end. It grows with each milestone.

Nothing leaves the machine: the server binds 127.0.0.1 and the clients talk to it there.
"""

import asyncio
import base64
import html
import importlib.util
import json
import os
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit
from zoneinfo import ZoneInfo

import httpx2
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from mcp.client import Client
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata
from pydantic import AnyUrl

from cairn.store import SCHEMA_VERSION, Store

REDIRECT = "http://127.0.0.1:33418/callback"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
NOTE = (
    "# Retry policy\n\nQuill retries a failed charge 3 times, 30 s apart.\n\n"
    "```bash\nquill run --max-retries 3 --backoff 30s\n```\n"
)
# A made-up token in a token's shape, built here so no file holds one.
LEAKED = "ghp_" + "Zq7xR2" * 6


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def cairn(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "cairn.server", *args], capture_output=True, text=True, timeout=60,
        env={**os.environ, **(env or {})},  # timeout: a hang guard, and nothing else
    )  # fmt: skip


class Server:
    """`python -m cairn.server serve` on a loopback port, with its output kept."""

    runs = ("-m", "cairn.server")  # what runs `serve` (the Slack journey's launcher, there)

    def __init__(self, db: Path, *options: str, env: dict[str, str] | None = None) -> None:
        self.db = db
        self.options, self.env = options, env or {}
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.process: subprocess.Popen | None = None
        self.output = ""
        self.killed = False  # it did not exit within stop's wait once asked to
        self.returncode: int | None = None

    def start(self) -> "Server":
        command = [sys.executable, *self.runs, "serve", "--db", str(self.db)]
        command += ["--host", "127.0.0.1", "--port", str(self.port), "--public-url", self.url]
        self.process = subprocess.Popen(
            [*command, *self.options], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1", **self.env},
        )  # fmt: skip
        deadline = time.monotonic() + 30  # a hang guard on the real process, nothing else
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError(f"the server stopped at once:\n{self.process.stdout.read()}")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return self
            except OSError:
                time.sleep(0.1)
        raise AssertionError("the server did not start listening within 30 s")

    def stop(self) -> str:
        if self.process is not None:
            self.process.terminate()
            try:
                self.output += self.process.communicate(timeout=20)[0]
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.killed = True
                self.output += self.process.communicate()[0]
            self.returncode = self.process.returncode
            self.process = None
        return self.output


class Hidden(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("type") == "hidden":
            self.fields[attributes["name"]] = attributes.get("value") or ""


class Memory(TokenStorage):
    tokens = None
    client_info = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client_info

    async def set_client_info(self, client_info):
        self.client_info = client_info


def text_of(result) -> dict:
    return json.loads(result.content[0].text)


async def connect_by_url(url: str, person: httpx2.AsyncClient, key: str, name: str, work):
    """An agent connects with only the URL; `person` signs in with `key` and approves.
    `work(client)` runs with the connected MCP client. Returns (work's result, storage)."""
    seen: dict[str, str] = {}

    async def redirect_handler(authorization_url: str) -> None:
        r = await person.get(authorization_url)
        consent = r.headers["location"]
        r = await person.get(consent)
        if r.status_code == 303:
            target = parse_qs(urlsplit(r.headers["location"]).query)["next"][0]
            await person.post("/login", data={"key": key, "next": target}, headers=SAME_ORIGIN)
            r = await person.get(target)
        assert r.status_code == 200 and name in r.text, r.text
        form = Hidden()
        form.feed(r.text)
        decided = await person.post(
            "/oauth/consent", data={**form.fields, "decision": "approve"}, headers=SAME_ORIGIN
        )
        seen["location"] = decided.headers["location"]

    async def callback_handler() -> AuthorizationCodeResult:
        answer = parse_qs(urlsplit(seen["location"]).query)
        return AuthorizationCodeResult(code=answer["code"][0], state=answer["state"][0])

    storage = Memory()
    oauth = OAuthClientProvider(
        server_url=f"{url}/mcp",
        client_metadata=OAuthClientMetadata(
            client_name=name,
            redirect_uris=[AnyUrl(REDIRECT)],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
        ),
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
    http = httpx2.AsyncClient(auth=oauth, timeout=30)
    async with http, Client(streamable_http_client(f"{url}/mcp", http_client=http)) as client:
        return await work(client), storage


def _install(tmp_path: Path, *options: str):
    db, key_file = tmp_path / "data" / "cairn.db", tmp_path / "owner.env"
    minted = cairn("key-create", "--db", str(db), "--label", "owner", "--out", str(key_file))
    assert minted.returncode == 0, minted.stderr
    assert key_file.stat().st_mode & 0o777 == 0o600
    key = key_file.read_text().strip().split("=", 1)[1]
    assert key not in minted.stdout + minted.stderr  # only the path is printed
    server = Server(db, *options).start()
    try:
        yield server, key
    finally:
        server.stop()


@pytest.fixture
def install(tmp_path):
    """A fresh install: a database, one API key in a 0600 file, and a running server, with
    serve's defaults (it tidies pages: decision 76)."""
    yield from _install(tmp_path)


@pytest.fixture
def install_without_tidy(tmp_path):
    """`install` with `serve --no-tidy`, for a test about something else whose notes would
    have cairn tidy a page in the background, a moment after the note, while the test reads
    its versions."""
    yield from _install(tmp_path, "--no-tidy")


def test_a_person_and_two_agents_share_memory_end_to_end(install):
    server, key = install
    url = server.url
    secrets_seen: list[str] = [key]

    async def journey() -> None:
        async with (
            httpx2.AsyncClient(base_url=url, timeout=30) as person,
            httpx2.AsyncClient(base_url=url, timeout=30) as anyone,
        ):
            # Nothing is reachable without credentials; the MCP endpoint says how to get them.
            assert (await anyone.get("/healthz")).status_code == 200
            assert (await anyone.get("/api/projects")).status_code == 401
            refused = await anyone.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            )
            assert (
                refused.status_code == 401
                and "resource_metadata" in refused.headers["www-authenticate"]
            )

            # The first agent connects with only the URL and writes the first note.
            async def first_agent(client):
                tools = {tool.name for tool in (await client.list_tools()).tools}
                assert {"projects", "new_project", "note", "recall", "page", "evidence"} <= tools
                made = text_of(await client.call_tool("new_project", {"name": "payments-api"}))
                noted = text_of(
                    await client.call_tool(
                        "note",
                        {
                            "project": "payments-api",
                            "text": NOTE,
                            "source_ref": "session:system-test",
                        },
                    )
                )
                assert noted["status"] == "saved"
                # A note carrying a token is refused: the agent hears which rule, not what.
                leaked = await client.call_tool(
                    "note", {"project": "payments-api", "text": f"# Deploy\n\nUse {LEAKED}.\n"}
                )
                said = leaked.content[0].text
                assert leaked.is_error and "github_token" in said and LEAKED not in said
                return made["id"], noted["page"]

            (project_id, slug), storage = await connect_by_url(
                url, person, key, "laptop-agent", first_agent
            )
            secrets_seen.extend([storage.tokens.access_token, storage.tokens.refresh_token])

            # A headless agent with the API key finds it at once, with its source lines.
            headers = {"x-api-key": key}
            hits = (await anyone.get("/api/search?q=quill+retries", headers=headers)).json()["hits"]
            assert hits and hits[0]["project"] == "payments-api" and hits[0]["page"] == slug
            hits_before = hits
            claim = hits[0]["claim_id"]
            trace = await anyone.get(
                f"/api/projects/{project_id}/pages/{slug}/claims/{claim}/evidence", headers=headers
            )
            span = trace.json()["passages"][0]
            assert span["text"] in NOTE and span["source_ref"] == "session:system-test"
            assert span["start_line"] >= 3

            # The person sees the page, the agent and nothing secret in the web view.
            page = await person.get(f"/b/payments-api/p/{slug}")
            assert page.status_code == 200 and "Quill retries a failed charge" in page.text
            agents = await person.get("/agents")
            assert "laptop-agent" in agents.text
            for secret in secrets_seen:
                assert secret not in page.text and secret not in agents.text

            # From a phone: the installed web app is handed shared text, the person files it
            # as a note, and the headless agent finds it.
            manifest = (await anyone.get("/static/manifest.webmanifest")).json()
            share = manifest["share_target"]
            assert manifest["display"] == "standalone" and share["method"] == "GET"
            shared = {"title": "Refund cap", "text": "Quill caps a refund at 500 EUR."}
            composer = await person.get(
                share["action"], params={share["params"][name]: shared[name] for name in shared}
            )
            assert composer.status_code == 200 and shared["text"] in composer.text
            filed = await person.post(
                "/notes", data={"project": "payments-api", **shared}, headers=SAME_ORIGIN
            )
            assert filed.status_code == 303, filed.text
            assert (await person.get(filed.headers["location"])).status_code == 200
            hits = (await anyone.get("/api/search?q=refund+cap+EUR", headers=headers)).json()[
                "hits"
            ]
            assert any("500 EUR" in hit["text"] for hit in hits)

            # The same token typed into the composer is refused too: the form comes back to
            # the person as typed, with what to remove listed above it, and nothing is kept.
            typed = {"project": "payments-api", "title": "Deploy", "text": f"Token: {LEAKED}"}
            refused = await person.post("/notes", data=typed, headers=SAME_ORIGIN)
            assert (
                refused.status_code == 422
                and '<span data-rule="github_token">a GitHub token</span> on line 1' in refused.text
            )
            assert "Nothing was saved. Remove what is listed below" in refused.text
            assert f"Token: {LEAKED}</textarea>" in refused.text
            # Both refusals are in the workspace's audit log, by who sent them.
            audit = await person.get("/audit")
            assert audit.status_code == 200 and LEAKED not in audit.text
            assert audit.text.count("note.refused") == 2
            assert "laptop-agent" in audit.text and "owner" in audit.text

            # A worker gets a key that reaches one project, to read only: made in the web view.
            await anyone.post("/api/projects", headers=headers, json={"name": "journal"})
            form = {"label": "report-worker", "scope": "selected", "access": "read"}
            made = await person.post(
                "/keys", data={**form, "projects": [project_id]}, headers=SAME_ORIGIN
            )
            assert made.status_code == 200, made.text
            limited = re.search(r"<pre>([^<]+)</pre>", made.text).group(1).strip()
            secrets_seen.append(limited)
            worker = {"x-api-key": limited}
            seen = (await anyone.get("/api/projects", headers=worker)).json()["projects"]
            assert [b["name"] for b in seen] == ["payments-api"]  # journal does not exist for it
            assert (await anyone.get("/api/search?q=quill", headers=worker)).json()["hits"]
            write = await anyone.post(
                f"/api/projects/{project_id}/notes", headers=worker, json={"text": "# X\n\nNo.\n"}
            )
            assert write.status_code == 403
            assert (
                await anyone.post("/api/projects", headers=worker, json={"name": "x"})
            ).status_code == 403

            # Disconnecting the agent ends its access at once: its row links a page that says
            # what stops working, and that page's button disconnects it.
            ask = agents.text.split('href="/agents/')[1].split('"')[0]
            asked = await person.get(f"/agents/{ask}")
            assert asked.status_code == 200, asked.text
            action = asked.text.split('action="/agents/')[1].split('"')[0]
            gone = await person.post(f"/agents/{action}", headers=SAME_ORIGIN)
            assert gone.status_code == 303
            dead = await anyone.post(
                "/mcp",
                headers={"authorization": f"Bearer {storage.tokens.access_token}"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            assert dead.status_code == 401

            # The workspace is the person's to take: the export downloads in the web view,
            # and the command line restores it into another install's fresh database, where
            # the same note is found with the same source lines.
            download = await person.get("/account/export")
            assert download.status_code == 200
            assert download.headers["content-type"] == "application/zip"
            moved = server.db.parent.parent / "moved"
            moved.mkdir()
            archive, elsewhere = moved / "export.zip", moved / "cairn.db"
            archive.write_bytes(download.content)
            minted = cairn("key-create", "--db", str(elsewhere), "--label", "mover",
                           "--out", str(moved / "mover.env"))  # fmt: skip
            assert minted.returncode == 0, minted.stderr
            restored = cairn("import", "--db", str(elsewhere), "--account", "acct_default",
                             "--in", str(archive))  # fmt: skip
            assert restored.returncode == 0, restored.stderr
            assert "Quill" not in restored.stdout + restored.stderr  # counts, never text
            mover = (moved / "mover.env").read_text().strip().split("=", 1)[1]
            secrets_seen.append(mover)
            second = Server(elsewhere).start()
            try:
                async with httpx2.AsyncClient(base_url=second.url, timeout=30) as there:
                    found = await there.get(
                        "/api/search?q=quill+retries", headers={"x-api-key": mover}
                    )
                    hit = found.json()["hits"][0]
                    assert (hit["project"], hit["page"], hit["claim_id"]) == (
                        hits_before[0]["project"],
                        hits_before[0]["page"],
                        hits_before[0]["claim_id"],
                    )
                    moved_trace = await there.get(
                        f"/api/projects/payments-api/pages/{slug}/claims/{claim}/evidence",
                        headers={"x-api-key": mover},
                    )
                    assert moved_trace.json()["passages"][0] == span
            finally:
                second.stop()

    secrets_seen.append(LEAKED)
    asyncio.run(journey())

    # A restart keeps everything, and the logs never held a secret or a query string.
    output = server.stop()
    restarted = Server(server.db).start()
    try:

        async def after_restart() -> None:
            async with httpx2.AsyncClient(base_url=restarted.url, timeout=30) as anyone:
                r = await anyone.get("/api/search?q=backoff", headers={"x-api-key": key})
                assert r.json()["hits"], "the note did not survive a restart"

        asyncio.run(after_restart())
    finally:
        output += restarted.stop()
    for secret in secrets_seen:
        assert secret not in output
    assert "?" not in output.replace("? ", ""), "a query string reached the server's output"
    # The access log: one line per call, naming the caller and the project, never the note.
    agent = r"note by token 'laptop-agent' \(grant_[0-9a-f]{12}\), project 'payments-api': 422"
    assert re.search(agent, output)
    assert "recall by key 'owner'" in output
    con = sqlite3.connect(server.db)
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        stored = "\n".join(con.iterdump())
    finally:
        con.close()
    for secret in secrets_seen:
        assert secret not in stored  # digests only


class KeyForm(HTMLParser):
    """The fields of the first form that posts to /keys, as a browser would send them."""

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


def test_a_new_person_starts_from_the_use_case_and_connects_an_app(install):
    """U1: an empty workspace points to /start; two uses ticked make two projects; the app's
    project gets a key on /start/connect that reaches it and nothing else."""
    server, key = install
    url = server.url
    secrets_seen = [key]

    async def journey() -> None:
        async with (
            httpx2.AsyncClient(base_url=url, timeout=30) as person,
            httpx2.AsyncClient(base_url=url, timeout=30) as app,
        ):
            await person.post("/login", data={"key": key}, headers=SAME_ORIGIN)
            home = await person.get("/")
            assert home.status_code == 200 and 'href="/start"' in home.text
            start = await person.get("/start")
            assert start.status_code == 200 and "<script" not in start.text.lower()

            chosen = {
                "use": ["repo", "feature"],
                "name-repo": "payments-api",
                "name-feature": "story-bible",
            }
            made = await person.post("/start", data=chosen, headers=SAME_ORIGIN)
            assert made.status_code == 303 and made.headers["location"] == "/start/connect"
            connect = await person.get("/start/connect")
            assert connect.status_code == 200
            assert f"claude mcp add --transport http cairn {url}/mcp" in connect.text

            form = KeyForm()
            form.feed(connect.text)
            assert form.fields["scope"] == "selected" and form.fields["access"] == "write"
            created = await person.post("/keys", data=form.fields, headers=SAME_ORIGIN)
            assert created.status_code == 200, created.text
            app_key = re.search(r"<pre>([^<]+)</pre>", created.text).group(1).strip()
            secrets_seen.append(app_key)
            assert f"CAIRN_URL={url}\nCAIRN_API_KEY={app_key}" in html.unescape(created.text)

            # The app writes to its project and reads it back; the other project does not exist
            # for it.
            headers = {"x-api-key": app_key}
            listed = (await app.get("/api/projects", headers=headers)).json()
            assert [(b["name"], b["kind"]) for b in listed["projects"]] == [
                ("story-bible", "feature")
            ]
            noted = await app.post(
                "/api/projects/story-bible/notes",
                headers=headers,
                json={"text": "# Mira\n\nMira is nine and always wears a red scarf.\n"},
            )
            assert noted.status_code == 201, noted.text
            hits = (await app.get("/api/search?q=red+scarf", headers=headers)).json()["hits"]
            assert hits and hits[0]["project"] == "story-bible"
            hidden = await app.get("/api/projects/payments-api/pages", headers=headers)
            assert hidden.status_code == 404
            refused = await app.post("/api/projects", headers=headers, json={"name": "more"})
            assert refused.status_code == 403

    asyncio.run(journey())
    output = server.stop()
    for secret in secrets_seen:
        assert secret not in output
    con = sqlite3.connect(server.db)
    try:
        kinds = dict(con.execute("SELECT name, kind FROM projects").fetchall())
        stored = "\n".join(con.iterdump())
    finally:
        con.close()
    assert kinds == {"payments-api": "repo", "story-bible": "feature"}
    for secret in secrets_seen:
        assert secret not in stored  # digests only


class IdentityProvider:
    """A stand-in OpenID Connect provider on a loopback port: discovery, authorize, token
    and userinfo. `person` is whoever is at the browser when cairn sends it here."""

    CLIENT_ID, CLIENT_SECRET = "cairn-system-test", "idp-client-secret-do-not-leak"

    def __init__(self) -> None:
        self.person: dict[str, str] = {}
        self.codes: dict[str, dict] = {}
        self.issued: list[str] = []  # every token handed out
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def answer(self, status: int, body: dict | None = None, location: str = "") -> None:
                raw = json.dumps(body or {}).encode()
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                url = urlsplit(self.path)
                query = {name: values[0] for name, values in parse_qs(url.query).items()}
                if url.path == "/.well-known/openid-configuration":
                    names = ("authorization", "token", "userinfo")
                    document = {f"{n}_endpoint": f"{provider.url}/{n}" for n in names}
                    return self.answer(200, {"issuer": provider.url, **document})
                if url.path == "/authorization" and query.get("client_id") == provider.CLIENT_ID:
                    code = f"code-{len(provider.codes) + 1}"
                    provider.codes[code] = {**provider.person, "nonce": query["nonce"]}
                    back = urlencode({"code": code, "state": query["state"]})
                    return self.answer(302, location=f"{query['redirect_uri']}?{back}")
                if url.path == "/userinfo":
                    token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                    person = provider.codes.get(token.removeprefix("access-"))
                    if person:
                        return self.answer(200, {**person, "email_verified": True})
                self.answer(404)

            def do_POST(self) -> None:
                size = int(self.headers.get("Content-Length", "0"))
                form = {n: v[0] for n, v in parse_qs(self.rfile.read(size).decode()).items()}
                basic = self.headers.get("Authorization", "").removeprefix("Basic ")
                secret = form.get("client_secret") or base64.b64decode(basic).decode()
                person = provider.codes.get(form.get("code", ""))
                if self.path != "/token" or provider.CLIENT_SECRET not in secret or not person:
                    return self.answer(400, {"error": "invalid_grant"})
                claims = {
                    **person,
                    "iss": provider.url,
                    "aud": provider.CLIENT_ID,
                    "exp": int(time.time()) + 300,
                    "email_verified": True,
                }
                parts = ({"alg": "RS256"}, claims)
                encoded = [
                    base64.urlsafe_b64encode(json.dumps(part).encode()).rstrip(b"=").decode()
                    for part in parts
                ]
                id_token = ".".join([*encoded, "signature-not-checked"])
                access = f"access-{form['code']}"
                provider.issued += [id_token, access]
                self.answer(
                    200, {"access_token": access, "token_type": "Bearer", "id_token": id_token}
                )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


async def sign_in(
    person: httpx2.AsyncClient, idp: IdentityProvider, who: dict[str, str], via: str = "/login"
) -> str:
    """Click the provider's button on the login page `via` as `who`, and follow the
    browser's hops. Returns the page cairn sends the person to."""
    idp.person = who
    login = await person.get(via)
    button = re.search(r'href="(/auth/sso[^"]*)"', login.text)
    assert login.status_code == 200 and button, login.text
    assert 'name="key"' not in login.text  # no key form once sign-in is on
    to_provider = await person.get(html.unescape(button.group(1)))
    assert to_provider.status_code == 302
    assert to_provider.headers["location"].startswith(idp.url + "/authorization?")
    back = await person.get(to_provider.headers["location"])
    callback = await person.get(back.headers["location"])
    assert callback.status_code == 200, callback.text
    target = re.search(r'http-equiv="refresh" content="0; url=([^"]+)"', callback.text)
    assert target, callback.text
    return html.unescape(target.group(1))


def test_a_team_signs_in_through_its_identity_provider(tmp_path):
    idp = IdentityProvider()
    env = {
        "CAIRN_OIDC_ISSUER": idp.url,
        "CAIRN_OIDC_CLIENT_ID": idp.CLIENT_ID,
        "CAIRN_OIDC_CLIENT_SECRET": idp.CLIENT_SECRET,
        "CAIRN_OIDC_LABEL": "Acme SSO",
    }
    options = ("--sign-in", "oidc", "--join", "default")
    server = Server(
        tmp_path / "cairn.db", *options, "--allowed-email-domain", "acme.example.test", env=env
    )
    server.start()
    url = server.url
    secrets_seen = [idp.CLIENT_SECRET]
    ada = {"sub": "idp-ada", "email": "ada@acme.example.test", "name": "Ada Example"}
    brook = {"sub": "idp-brook", "email": "brook@acme.example.test", "name": "Brook Example"}
    outsider = {"sub": "idp-oscar", "email": "oscar@elsewhere.example.test", "name": "Oscar"}

    async def journey() -> None:
        async with (
            httpx2.AsyncClient(base_url=url, timeout=30) as first,
            httpx2.AsyncClient(base_url=url, timeout=30) as second,
            httpx2.AsyncClient(base_url=url, timeout=30) as anyone,
        ):
            # Signed out, a shared install shows nothing but the way in.
            home = await anyone.get("/")
            assert home.status_code == 303 and home.headers["location"].startswith("/login")
            assert "Acme SSO" in (await anyone.get("/login")).text
            assert (await anyone.post("/login", data={"key": "anything"})).status_code == 404

            # The first person to sign in owns the workspace and is shown how to connect.
            assert await sign_in(first, idp, ada) == "/welcome"
            welcome = await first.get("/welcome")
            assert welcome.status_code == 200 and "Ada Example" in welcome.text
            assert f"{url}/mcp" in welcome.text

            # Their agent connects with only the URL: no key exists anywhere.
            async def agent(client):
                await client.call_tool("new_project", {"name": "handbook"})
                noted = text_of(
                    await client.call_tool("note", {"project": "handbook", "text": NOTE})
                )
                assert noted["status"] == "saved"
                return noted["page"]

            slug, storage = await connect_by_url(url, first, "", "ada-laptop", agent)
            secrets_seen.extend([storage.tokens.access_token, storage.tokens.refresh_token])

            # A colleague signs in, lands in the same workspace and reads the page.
            assert await sign_in(second, idp, brook) == "/welcome"
            page = await second.get(f"/b/handbook/p/{slug}")
            assert page.status_code == 200 and "Quill retries a failed charge" in page.text
            assert "Brook Example" in page.text

            # A member does not manage the workspace's keys: no form, and a refusal.
            refused = await second.post("/keys", data={"label": "brooks-job"}, headers=SAME_ORIGIN)
            assert refused.status_code == 403
            members_view = await second.get("/keys")
            assert members_view.status_code == 200 and 'action="/keys"' not in members_view.text

            # The owner makes a key for a job without a browser in the web view, shown once.
            made = await first.post("/keys", data={"label": "nightly-job"}, headers=SAME_ORIGIN)
            assert made.status_code == 200
            key = re.search(r"<pre>([^<]+)</pre>", made.text).group(1).strip()
            secrets_seen.append(key)
            assert key not in (await first.get("/keys")).text
            assert "nightly-job" in (await second.get("/keys")).text
            job = {"x-api-key": key}
            assert (await anyone.get("/api/search?q=quill", headers=job)).json()["hits"]
            revoked = await first.post("/keys/nightly-job/revoke", headers=SAME_ORIGIN)
            assert revoked.status_code == 303
            assert (await anyone.get("/api/search?q=quill", headers=job)).status_code == 401

            # Someone the provider knows, but outside the organisation's domain, is turned
            # away at the callback and gets no session.
            idp.person = outsider
            to_provider = await anyone.get("/auth/sso")
            back = await anyone.get(to_provider.headers["location"])
            turned_away = await anyone.get(back.headers["location"])
            assert turned_away.status_code == 403 and "email domains" in turned_away.text
            assert "cairn_session" not in anyone.cookies
            assert (await anyone.get("/keys")).status_code == 303

            # Signing out ends the session.
            assert (await first.post("/logout", headers=SAME_ORIGIN)).status_code == 303
            assert (await first.get("/keys")).status_code == 303

    try:
        asyncio.run(journey())
    finally:
        output = server.stop()
        idp.close()

    assert len(idp.issued) == 6  # three callbacks, an ID token and an access token each
    for secret in [*secrets_seen, *idp.issued]:
        assert secret not in output
    assert "?" not in output.replace("? ", ""), "a query string reached the server's output"
    con = sqlite3.connect(server.db)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        stored = "\n".join(con.iterdump())
        members = con.execute(
            "SELECT users.email, memberships.role FROM memberships"
            " JOIN users ON users.id = memberships.user_id ORDER BY memberships.rowid"
        ).fetchall()
    finally:
        con.close()
    assert members == [(ada["email"], "owner"), (brook["email"], "member")]
    assert outsider["email"] not in stored and outsider["sub"] not in stored  # no user row
    for secret in [*secrets_seen, *idp.issued]:
        assert secret not in stored  # no provider token, no key, no client secret


async def call_tool(
    http: httpx2.AsyncClient, token: str, name: str, **arguments
) -> httpx2.Response:
    """One MCP tool call over plain HTTP with an agent's access token."""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    headers = {
        "authorization": f"Bearer {token}",
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
    }
    return await http.post("/mcp", json=body, headers=headers)


def invitation(page: str, url: str) -> str:
    """The token of the invitation link a members page shows once."""
    found = re.search(re.escape(url) + r"/join/([A-Za-z0-9_-]+)", page)
    assert found, page
    return found.group(1)


def test_an_invited_colleague_joins_and_their_agent_leaves_with_them(tmp_path):
    """T1. Everyone who signs in gets a workspace of their own; an owner invites people to
    theirs by email. A colleague joins by the link, their agent makes a private project that
    the owner reaches and an admin does not, and removing the colleague ends that agent's
    access at once."""
    idp = IdentityProvider()
    env = {
        "CAIRN_OIDC_ISSUER": idp.url,
        "CAIRN_OIDC_CLIENT_ID": idp.CLIENT_ID,
        "CAIRN_OIDC_CLIENT_SECRET": idp.CLIENT_SECRET,
        "CAIRN_OIDC_LABEL": "Acme SSO",
    }
    server = Server(tmp_path / "cairn.db", "--sign-in", "oidc", "--join", "personal", env=env)
    server.start()
    url = server.url
    secrets_seen = [idp.CLIENT_SECRET]
    ada = {"sub": "idp-ada", "email": "ada@acme.example.test", "name": "Ada Example"}
    brook = {"sub": "idp-brook", "email": "brook@acme.example.test", "name": "Brook Example"}
    cy = {"sub": "idp-cy", "email": "Cy@Acme.example.test", "name": "Cy Example"}

    async def journey() -> None:
        async with (
            httpx2.AsyncClient(base_url=url, timeout=30) as owner,
            httpx2.AsyncClient(base_url=url, timeout=30) as admin,
            httpx2.AsyncClient(base_url=url, timeout=30) as colleague,
            httpx2.AsyncClient(base_url=url, timeout=30) as anyone,
        ):
            assert await sign_in(owner, idp, ada) == "/welcome"
            home = await owner.get("/workspaces")
            assert home.status_code == 200 and "Your workspaces" in home.text

            # An admin, by invitation: they reach projects as members do.
            made = await owner.post(
                "/members/invite",
                data={"email": brook["email"], "role": "admin"},
                headers=SAME_ORIGIN,
            )
            assert made.status_code == 200
            to_brook = invitation(made.text, url)
            secrets_seen.append(to_brook)
            assert await sign_in(admin, idp, brook) == "/welcome"
            joined = await admin.post(f"/join/{to_brook}", headers=SAME_ORIGIN)
            assert joined.status_code == 303 and joined.headers["location"] == "/"

            # A colleague, invited by email (case aside), follows the link before signing in.
            made = await owner.post(
                "/members/invite",
                data={"email": "cy@acme.example.test", "role": "member"},
                headers=SAME_ORIGIN,
            )
            to_cy = invitation(made.text, url)
            secrets_seen.append(to_cy)
            assert to_cy not in (await owner.get("/members")).text  # shown once
            signed_out = await colleague.get(f"/join/{to_cy}")
            assert signed_out.status_code == 303
            back = await sign_in(colleague, idp, cy, via=signed_out.headers["location"])
            assert back.startswith("/welcome?next=") and to_cy in back.replace("%2F", "/")
            offer = await colleague.get(f"/join/{to_cy}")
            assert offer.status_code == 200 and f'action="/join/{to_cy}"' in offer.text
            joined = await colleague.post(f"/join/{to_cy}", headers=SAME_ORIGIN)
            assert joined.status_code == 303 and joined.headers["location"] == "/"
            assert (await colleague.get(f"/join/{to_cy}")).status_code == 404  # used once

            # Their agent connects with only the URL and keeps a private project.
            async def agent(client):
                made = await client.call_tool("new_project", {"name": "cy-notes", "private": True})
                assert not made.is_error, made
                noted = text_of(
                    await client.call_tool("note", {"project": "cy-notes", "text": NOTE})
                )
                assert noted["status"] == "saved"
                return noted["page"]

            slug, storage = await connect_by_url(url, colleague, "", "cy-laptop", agent)
            token = storage.tokens.access_token
            secrets_seen.extend([token, storage.tokens.refresh_token])

            # The owner reaches it; the admin does not, anywhere.
            assert (await owner.get(f"/b/cy-notes/p/{slug}")).status_code == 200
            assert (await admin.get(f"/b/cy-notes/p/{slug}")).status_code == 404
            assert "cy-notes" not in (await admin.get("/")).text
            assert "Quill" not in (await admin.get("/search?q=quill")).text
            assert (await admin.get("/b/cy-notes/access")).status_code == 404
            rule = await colleague.get("/b/cy-notes/access")
            assert "only the maker, the people named, and the workspace" in rule.text
            reached = await call_tool(anyone, token, "recall", query="quill")
            assert reached.status_code == 200 and "Quill" in reached.text

            # Removing the colleague ends their agent and their session at once.
            people = (await owner.get("/members")).text
            row = next(r for r in people.split("<tr>") if "cy@acme.example.test" in r.lower())
            removal = re.search(r'href="(/members/[^"]+/remove)"', row).group(1)
            assert (await owner.post(removal, headers=SAME_ORIGIN)).status_code == 303
            assert (await call_tool(anyone, token, "recall", query="quill")).status_code == 401
            assert (await colleague.get("/members")).status_code == 303  # to /login
            landing = await colleague.get("/")  # the signed-out landing page, nothing more
            assert 'href="/login"' in landing.text and "cy-notes" not in landing.text
            assert "Cy Example" not in landing.text
            assert (await owner.get(f"/b/cy-notes/p/{slug}")).status_code == 200  # it stays
            assert (await admin.get(f"/b/cy-notes/p/{slug}")).status_code == 404

    try:
        asyncio.run(journey())
    finally:
        output = server.stop()
        idp.close()

    for secret in [*secrets_seen, *idp.issued]:
        assert secret not in output
    assert "?" not in output.replace("? ", ""), "a query string reached the server's output"
    con = sqlite3.connect(server.db)
    try:
        stored = "\n".join(con.iterdump())
    finally:
        con.close()
    for secret in [*secrets_seen, *idp.issued]:
        assert secret not in stored  # invitations and tokens are kept as digests


def test_a_removed_colleague_comes_back_only_by_invitation(tmp_path):
    """T1, decision A. In a shared install (`--join default`) signing in puts a new person in
    the workspace; once an owner removed them, signing in again is refused with the way
    back, and only a fresh invitation lets them in."""
    idp = IdentityProvider()
    env = {
        "CAIRN_OIDC_ISSUER": idp.url,
        "CAIRN_OIDC_CLIENT_ID": idp.CLIENT_ID,
        "CAIRN_OIDC_CLIENT_SECRET": idp.CLIENT_SECRET,
        "CAIRN_OIDC_LABEL": "Acme SSO",
    }
    options = ("--sign-in", "oidc", "--join", "default")
    server = Server(
        tmp_path / "cairn.db", *options, "--allowed-email-domain", "acme.example.test", env=env
    )
    server.start()
    url = server.url
    ada = {"sub": "idp-ada", "email": "ada@acme.example.test", "name": "Ada Example"}
    brook = {"sub": "idp-brook", "email": "brook@acme.example.test", "name": "Brook Example"}
    invited: list[str] = []

    async def journey() -> None:
        async with (
            httpx2.AsyncClient(base_url=url, timeout=30) as owner,
            httpx2.AsyncClient(base_url=url, timeout=30) as colleague,
        ):
            assert await sign_in(owner, idp, ada) == "/welcome"
            assert await sign_in(colleague, idp, brook) == "/welcome"
            people = (await owner.get("/members")).text
            row = next(r for r in people.split("<tr>") if brook["email"] in r)
            removal = re.search(r'href="(/members/[^"]+/remove)"', row).group(1)
            assert (await owner.post(removal, headers=SAME_ORIGIN)).status_code == 303
            assert (await colleague.get("/keys")).status_code == 303  # the session ended

            # Signing in again does not undo the removal; the page says what to do instead.
            idp.person = {**brook, "email": "Brook@Acme.example.test"}  # case aside
            to_provider = await colleague.get("/auth/sso")
            back = await colleague.get(to_provider.headers["location"])
            refused = await colleague.get(back.headers["location"])
            assert refused.status_code == 403 and "invitation" in refused.text
            assert (await colleague.get("/keys")).status_code == 303  # no session

            # A fresh invitation lets them back in, with the role it gives.
            made = await owner.post(
                "/members/invite",
                data={"email": brook["email"], "role": "admin"},
                headers=SAME_ORIGIN,
            )
            invited.append(invitation(made.text, url))
            # the sign-in took the invitation: straight into the workspace, not to its link
            assert await sign_in(colleague, idp, brook) == "/"
            assert (await colleague.get("/keys")).status_code == 200
            roles = (await owner.get("/members")).text
            role_cell = r'brook@acme\.example\.test</td>\s*<td data-label="Role">admin</td>'
            assert re.search(role_cell, roles), roles

    try:
        asyncio.run(journey())
    finally:
        output = server.stop()
        idp.close()
    con = sqlite3.connect(server.db)
    try:
        stored = "\n".join(con.iterdump())
    finally:
        con.close()
    for secret in [*invited, *idp.issued]:
        assert secret not in output and secret not in stored


class Langfuse:
    """A stand-in Langfuse on a loopback port: the two endpoints a pull reads, behind HTTP
    Basic auth with made-up keys. `traces` are the traces marked for cairn, one score each;
    `requests` is every request it answered (path and query), `refused` how many it turned
    away for their credentials."""

    PUBLIC, SECRET = "pk-lf-system-test", "sk-lf-system-test-do-not-leak"

    def __init__(self) -> None:
        self.traces: list[dict[str, str]] = []
        self.requests: list[str] = []
        self.refused = 0
        stand_in = self
        expected = "Basic " + base64.b64encode(f"{self.PUBLIC}:{self.SECRET}".encode()).decode()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def answer(self, status: int, body: dict) -> None:
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                stand_in.requests.append(self.path)
                if self.headers.get("authorization") != expected:
                    stand_in.refused += 1
                    return self.answer(401, {"message": "Invalid credentials"})
                parts = urlsplit(self.path)
                if parts.path == "/api/public/v3/scores":
                    data = [stand_in.score(t) for t in stand_in.traces]
                    return self.answer(200, {"data": data, "meta": {"limit": 100}})
                if parts.path == "/api/public/v2/observations":
                    wanted = parse_qs(parts.query)["traceId"][0]
                    data = [stand_in.root(t) for t in stand_in.traces if t["id"] == wanted]
                    return self.answer(200, {"data": data, "meta": {}})
                return self.answer(404, {"message": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def mark(self, trace_id: str, question: str, answer: str) -> None:
        at = (datetime.now(UTC) - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
        self.traces.append({"id": trace_id, "in": question, "out": answer, "at": at})

    @staticmethod
    def score(trace: dict[str, str]) -> dict:
        return {
            "id": f"score-{trace['id']}",
            "projectId": "project-system-test",
            "name": "cairn",
            "dataType": "CATEGORICAL",
            "value": "payments-api",
            "timestamp": trace["at"],
            "comment": "The agent got this right; keep it.",
            "subject": {"kind": "trace", "id": trace["id"]},
        }

    @staticmethod
    def root(trace: dict[str, str]) -> dict:
        return {
            "id": f"observation-{trace['id']}",
            "traceId": trace["id"],
            "startTime": trace["at"],
            "isRootObservation": True,
            "input": trace["in"],
            "output": trace["out"],
            "traceName": "refund-agent",
        }

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def test_an_operator_syncs_traces_marked_in_langfuse(install):
    server, key = install
    langfuse = Langfuse()
    env = {"CAIRN_TEST_LF_PUBLIC": langfuse.PUBLIC, "CAIRN_TEST_LF_SECRET": langfuse.SECRET}
    config = {
        "base_url": langfuse.url,
        "projects": ["payments-api"],
        "public_key_env": "CAIRN_TEST_LF_PUBLIC",
        "secret_key_env": "CAIRN_TEST_LF_SECRET",
    }
    secrets = [key, langfuse.PUBLIC, langfuse.SECRET]
    printed: list[str] = []

    def run(*args: str, with_keys: bool = True) -> subprocess.CompletedProcess:
        done = cairn(*args, "--db", str(server.db), env=env if with_keys else None)
        printed.extend((done.stdout, done.stderr))
        return done

    def notes() -> int:
        con = sqlite3.connect(server.db)
        try:
            return con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        finally:
            con.close()

    async def recall(url: str, query: str) -> list[dict]:
        """An agent with the API key asks over MCP."""
        http = httpx2.AsyncClient(headers={"x-api-key": key}, timeout=30)
        async with http, Client(streamable_http_client(f"{url}/mcp", http_client=http)) as agent:
            return text_of(await agent.call_tool("recall", {"query": query}))["hits"]

    async def new_project() -> None:
        async with httpx2.AsyncClient(base_url=server.url, timeout=30) as anyone:
            made = await anyone.post(
                "/api/projects", headers={"x-api-key": key}, json={"name": "payments-api"}
            )
            assert made.status_code == 201, made.text

    restarted = None
    try:
        asyncio.run(new_project())
        # The operator adds the source: its config names the variables, never the keys.
        added = run("source-add", "--name", "traces", "--kind", "langfuse",
                    "--config", json.dumps(config), with_keys=False)  # fmt: skip
        assert added.returncode == 0, added.stderr
        # Someone marks a trace in Langfuse for the payments-api project; one sync writes it.
        langfuse.mark("trace-refund", "Can I get my money back?", "Quill refunds a duplicate"
                      " charge within 5 days.")  # fmt: skip
        synced = run("source-sync", "--name", "traces")
        assert synced.returncode == 0 and "traces: 1 saved" in synced.stdout, synced.stderr
        hits = asyncio.run(recall(server.url, "duplicate charge refunds"))
        assert hits and hits[0]["project"] == "payments-api"
        assert any("duplicate charge" in hit["text"] for hit in hits)
        # Syncing again writes nothing; without the keys in its environment it says which.
        before = notes()
        again = run("source-sync", "--name", "traces")
        assert again.returncode == 0 and "traces: 0 saved, 0 unchanged" in again.stdout
        assert notes() == before
        keyless = run("source-sync", "--name", "traces", with_keys=False)
        assert keyless.returncode == 1 and "set CAIRN_TEST_LF_PUBLIC" in keyless.stderr
        listed = run("source-list", with_keys=False)
        assert "set CAIRN_TEST_LF_PUBLIC" in listed.stdout  # the last run's error names it

        # `serve --sync-every` syncs in the background: a newly marked trace arrives alone,
        # and a source whose key is not in the server's environment is logged, by name.
        broken = {**config, "secret_key_env": "CAIRN_TEST_LF_MISSING"}
        added = run("source-add", "--name", "broken", "--kind", "langfuse",
                    "--config", json.dumps(broken), with_keys=False)  # fmt: skip
        assert added.returncode == 0, added.stderr
        server.stop()
        langfuse.mark("trace-payout", "Why is my payout late?", "Quill holds payouts above"
                      " 10000 EUR for a manual review.")  # fmt: skip
        # --no-tidy: both traces are named refund-agent, so the second lands on the first's
        # page with the same "Why it was kept" line, and the default tidy would fold it in
        # the background; this test is about the sync, not the tidy.
        restarted = Server(server.db, "--sync-every", "1", "--no-tidy", env=env).start()
        deadline = time.monotonic() + 30  # a hang guard on the real process, nothing else
        while not (found := asyncio.run(recall(restarted.url, "payouts manual review"))):
            assert time.monotonic() < deadline, "the background sync wrote nothing in 30 s"
            time.sleep(0.2)
        assert found[0]["project"] == "payments-api"
    finally:
        output = server.stop() + (restarted.stop() if restarted else "")
        langfuse.close()

    assert langfuse.refused == 0  # every request carried the keys from the environment
    assert {urlsplit(r).path for r in langfuse.requests} == {
        "/api/public/v3/scores",
        "/api/public/v2/observations",
    }
    for secret in secrets:
        assert secret not in output and not any(secret in text for text in printed)
    assert re.search(r"source 'broken' of workspace \S+ failed: SourceError", output), output
    assert "CAIRN_TEST_LF_MISSING" not in output  # the class, never the message
    assert "?" not in output.replace("? ", ""), "a query string reached the server's output"
    con = sqlite3.connect(server.db)
    try:
        stored = "\n".join(con.iterdump())
    finally:
        con.close()
    for secret in secrets:
        assert secret not in stored


def test_a_workspace_reaches_its_plans_cap_and_the_operator_lifts_it(tmp_path):
    """`serve --plans` with a tiny free plan: the note cap is reached, the person reads why in
    the composer and the agent over MCP, the operator moves the workspace to a bigger plan
    on the command line, writing works again, and /usage shows the numbers."""
    plans = tmp_path / "plans.yaml"
    plans.write_text(
        "# Synthetic plans for this test.\n"
        "default: tiny\n"
        "plans:\n"
        "  tiny: {label: Tiny, notes_per_month: 2, storage_bytes: 100000, projects: 1}\n"
        "  roomy: {label: Roomy, notes_per_month: 100, storage_bytes: 1000000, projects: 5}\n"
    )
    db, key_file = tmp_path / "data" / "cairn.db", tmp_path / "owner.env"
    minted = cairn("key-create", "--db", str(db), "--label", "owner", "--out", str(key_file))
    assert minted.returncode == 0, minted.stderr
    key = key_file.read_text().strip().split("=", 1)[1]
    private = "the margin we never tell customers"  # note text that must stay out of answers
    server = Server(db, "--plans", str(plans)).start()
    headers = {"x-api-key": key}

    async def capped() -> None:
        async with (
            httpx2.AsyncClient(base_url=server.url, timeout=30) as person,
            httpx2.AsyncClient(base_url=server.url, timeout=30) as anyone,
        ):
            made = await anyone.post("/api/projects", headers=headers, json={"name": "ops"})
            assert made.status_code == 201, made.text
            for n in range(2):
                wrote = await anyone.post(
                    "/api/projects/ops/notes", headers=headers, json={"text": f"# N{n}\n\nOne.\n"}
                )
                assert wrote.status_code == 201, wrote.text
            over = await anyone.post(
                "/api/projects/ops/notes", headers=headers, json={"text": f"# N\n\n{private}.\n"}
            )
            assert over.status_code == 402 and over.json()["limit"] == "notes_per_month"
            assert "2 of 2" in over.json()["error"] and private not in over.text

            # The person, in the web composer: the sentence and what they typed.
            await person.post("/login", data={"key": key}, headers=SAME_ORIGIN)
            composed = await person.post(
                "/b/ops/notes", data={"text": f"# Web\n\n{private}.\n"}, headers=SAME_ORIGIN
            )
            assert composed.status_code == 402 and 'role="alert"' in composed.text
            assert "2 of 2" in html.unescape(composed.text) and "Tiny" in composed.text

            # The agent, over MCP: a tool error with the same sentence.
            http = httpx2.AsyncClient(headers=headers, timeout=30)
            mcp = streamable_http_client(f"{server.url}/mcp", http_client=http)
            async with http, Client(mcp) as agent:
                result = await agent.call_tool("note", {"project": "ops", "text": "# A\n\nB.\n"})
                said = result.content[0].text
                assert result.is_error and "2 of 2" in said and "Tiny" in said

    async def lifted() -> None:
        async with httpx2.AsyncClient(base_url=server.url, timeout=30) as person:
            wrote = await person.post(
                "/api/projects/ops/notes", headers=headers, json={"text": "# N2\n\nThree.\n"}
            )
            assert wrote.status_code == 201, wrote.text
            usage = (await person.get("/api/usage", headers=headers)).json()
            assert (usage["plan"], usage["label"]) == ("roomy", "Roomy")
            assert usage["limits"]["notes_per_month"] == {"used": 3, "cap": 100}
            await person.post("/login", data={"key": key}, headers=SAME_ORIGIN)
            page = await person.get("/usage")
            assert page.status_code == 200 and "Roomy" in page.text
            assert re.search(r"3\s+of\s+100", page.text) and re.search(r"1\s+of\s+5", page.text)

    try:
        asyncio.run(capped())
        account = "acct_default"  # key-create mints into the default workspace
        moved = cairn("plan-set", "--db", str(db), "--account", account, "--plan", "roomy")
        assert moved.returncode == 0, moved.stderr
        asyncio.run(lifted())
        printed = cairn("usage", "--db", str(db), "--account", account)
        assert printed.returncode == 0, printed.stderr
        assert json.loads(printed.stdout)["notes"] == 3
    finally:
        output = server.stop()
    assert private not in output and key not in output
    refused = r"cairn\.access: note by (key|session) 'owner' \(\w+\), project 'ops': 402"
    refusals = re.findall(refused, output)
    assert sorted(refusals) == ["key", "key", "session"], output  # REST, MCP and the composer


class Arranger:
    """A stand-in OpenAI-compatible model on a loopback port: /v1/chat/completions answers
    with the claim ids it was given, in reverse order, and reports its tokens. It refuses a
    request without the made-up key. `asked` counts the requests it answered."""

    KEY = "sk-arrange-system-test-do-not-leak"

    def __init__(self) -> None:
        self.asked = 0
        self.refused = 0
        stand_in = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                if self.headers.get("authorization") != f"Bearer {stand_in.KEY}":
                    stand_in.refused += 1
                    status, answer = 401, {"error": "bad key"}
                elif urlsplit(self.path).path != "/v1/chat/completions":
                    status, answer = 404, {"error": "not found"}
                else:
                    stand_in.asked += 1
                    user = body["messages"][-1]["content"]
                    ids = re.findall(r' id="(cl_[0-9a-f]+)"', user)
                    content = json.dumps({"order": ids[::-1], "same": []})
                    status, answer = 200, {
                        "choices": [{"message": {"role": "assistant", "content": content},
                                     "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 900, "completion_tokens": 120},
                    }  # fmt: skip
                raw = json.dumps(answer).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def test_a_model_arranges_a_page_and_writes_none_of_it(tmp_path):
    """`serve --model-env` with a stand-in model: two notes on one page, the page gains an
    arranged version with the same claims, the web view says who arranged it, the usage
    shows the tokens, and the model's key is in no output and nowhere in the database."""
    model = Arranger()
    env = {
        "CAIRN_TEST_ARRANGE_BASE_URL": model.url,
        "CAIRN_TEST_ARRANGE_API_KEY": model.KEY,
        "CAIRN_TEST_ARRANGE_MODEL": "stand-in-arranger",
    }
    db, key_file = tmp_path / "data" / "cairn.db", tmp_path / "owner.env"
    minted = cairn("key-create", "--db", str(db), "--label", "owner", "--out", str(key_file))
    assert minted.returncode == 0, minted.stderr
    key = key_file.read_text().strip().split("=", 1)[1]
    headers = {"x-api-key": key}
    notes = (
        "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
        "- `--dry-run` lists the changes first.\n- `--delete` removes extra files.\n",
        "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
        "- `--retries 3` helps on flaky links.\n\n## Limits\n\n"
        "- At most 500 files go in one batch.\n",
    )
    server = Server(
        db, "--model-env", "CAIRN_TEST_ARRANGE", "--model-price", "1,5", env=env
    ).start()

    async def journey() -> None:
        async with httpx2.AsyncClient(base_url=server.url, timeout=30) as person:
            made = await person.post("/api/projects", headers=headers, json={"name": "ops"})
            assert made.status_code == 201, made.text
            for text in notes:
                wrote = await person.post(
                    "/api/projects/ops/notes", headers=headers, json={"text": text}
                )
                assert wrote.status_code == 201 and wrote.json()["status"] == "saved", wrote.text
            page_path = "/api/projects/ops/pages/drift-sync"
            before = (await person.get(page_path, headers=headers, params={"version": 2})).json()
            deadline = time.monotonic() + 30  # a hang guard on the real process, nothing else
            while (latest := (await person.get(page_path, headers=headers)).json())["version"] < 3:
                assert time.monotonic() < deadline, "no arranged version in 30 s"
                await asyncio.sleep(0.2)
            assert latest["version"] == 3
            texts = [claim["text"] for claim in latest["claims"]]
            answer = [claim["text"] for claim in before["claims"]][::-1]  # the model reverses
            # kept to the tree: the claims with no heading first, then Limits (its claim came
            # first in the answer), then Flags, each in the answer's order; the same words
            top = [text for text in answer if not text.startswith("- ")]
            assert texts == top + [text for text in answer if text.startswith("- ")]
            assert top == ["Drift mirrors two buckets."] * 2 and texts[2].startswith("- At most")

            await person.post("/login", data={"key": key}, headers=SAME_ORIGIN)
            view = await person.get("/b/ops/p/drift-sync")
            assert view.status_code == 200 and "Arranged by stand-in-arranger" in view.text
            earlier = await person.get("/b/ops/p/drift-sync", params={"version": 2})
            assert "Arranged by" not in earlier.text  # the version before is still there

    try:
        asyncio.run(journey())
    finally:
        output = server.stop()
        model.close()
    assert model.asked == 1 and model.refused == 0  # one call: the page's newest version
    usage = cairn("usage", "--db", str(db), "--account", "acct_default")
    assert usage.returncode == 0, usage.stderr
    used = json.loads(usage.stdout)
    assert (used["model_tokens_in"], used["model_tokens_out"]) == (900, 120)
    assert used["model_micros"] == 900 * 1 + 120 * 5
    assert re.search(r"cairn\.access: arrange arranged in project br_\w+: version 2 to 3", output)
    for shown in (output, usage.stdout, usage.stderr):
        assert model.KEY not in shown and "Drift mirrors" not in shown
    con = sqlite3.connect(db)
    try:
        stored = "\n".join(con.iterdump())
    finally:
        con.close()
    assert model.KEY not in stored
    assert '"arranged_by": "stand-in-arranger"' in stored


def test_serve_tidies_a_page_by_default_with_no_model(tmp_path):
    """`serve` with no arranging option (decision 76): two notes repeat a line under one
    heading of one page, and a moment later the page has a version arranged by cairn where
    the line shows once, citing both notes. Every claim's words are its sources' own, and
    the version before, with the line twice, is still there."""
    db, key_file = tmp_path / "data" / "cairn.db", tmp_path / "owner.env"
    minted = cairn("key-create", "--db", str(db), "--label", "owner", "--out", str(key_file))
    assert minted.returncode == 0, minted.stderr
    key = key_file.read_text().strip().split("=", 1)[1]
    headers = {"x-api-key": key}
    repeated = "- `--dry-run` lists the changes first."
    notes = (
        f"# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n{repeated}\n",
        f"# Drift sync\n\n## Flags\n\n{repeated}\n- `--retries 3` helps on flaky links.\n",
    )
    server = Server(db).start()  # no --tidy, no --model-env: the default
    page_path = "/api/projects/ops/pages/drift-sync"

    async def journey() -> None:
        async with httpx2.AsyncClient(base_url=server.url, timeout=30) as person:
            made = await person.post("/api/projects", headers=headers, json={"name": "ops"})
            assert made.status_code == 201, made.text
            for text in notes:
                wrote = await person.post(
                    "/api/projects/ops/notes", headers=headers, json={"text": text}
                )
                assert wrote.status_code == 201 and wrote.json()["status"] == "saved", wrote.text
            noted = wrote.json()["version"]  # the version the second note made
            deadline = time.monotonic() + 30  # a hang guard on the real process, nothing else
            while (latest := (await person.get(page_path, headers=headers)).json())["metrics"].get(
                "arranged_by"
            ) != "cairn":
                assert time.monotonic() < deadline, "no version arranged by cairn in 30 s"
                await asyncio.sleep(0.1)
            assert latest["version"] == noted + 1
            texts = [claim["text"] for claim in latest["claims"]]
            assert texts.count(repeated) == 1  # folded
            for claim in latest["claims"]:
                trace = await person.get(
                    f"{page_path}/claims/{claim['id']}/evidence", headers=headers
                )
                spans = trace.json()["passages"]
                assert spans and all(
                    " ".join(claim["text"].split()) in " ".join(span["text"].split())
                    for span in spans
                ), claim  # the sources' own words
                assert any(claim["text"] in note for note in notes)
                if claim["text"] == repeated:
                    assert len(spans) == 2  # one line, citing both notes
            before = (
                await person.get(page_path, headers=headers, params={"version": noted})
            ).json()
            assert [claim["text"] for claim in before["claims"]].count(repeated) == 2
            assert "arranged_by" not in before["metrics"]

            await person.post("/login", data={"key": key}, headers=SAME_ORIGIN)
            view = await person.get("/b/ops/p/drift-sync")
            assert view.status_code == 200 and "Arranged by cairn" in view.text
            earlier = await person.get("/b/ops/p/drift-sync", params={"version": noted})
            assert earlier.status_code == 200 and "Arranged by" not in earlier.text

    try:
        asyncio.run(journey())
    finally:
        output = server.stop()
    assert re.search(
        r"cairn\.access: arrange arranged in project br_\w+: version \d+ to \d+", output
    )
    assert "dry-run" not in output and key not in output


def test_a_private_pre_release_admits_the_listed_and_the_operator_sets_the_plan(tmp_path):
    """The hosted service before it opens: `serve` with sign-in and plans, and
    CAIRN_ALLOWED_EMAILS naming one person. A stranger is turned away and leaves nothing
    behind. The listed person gets a workspace of their own on the default plan. The
    operator finds the workspace with `workspace-list` and puts it on another plan with
    `plan-set`. No address is in any output."""
    plans = tmp_path / "plans.yaml"
    plans.write_text(
        "# Synthetic plans for this test.\n"
        "default: free\n"
        "plans:\n"
        "  free: {label: Free, notes_per_month: 1}\n"
        "  solo: {label: Solo, notes_per_month: 100}\n"
    )
    idp = IdentityProvider()
    ada = {"sub": "idp-ada", "email": "ada@example.test", "name": "Ada Example"}
    stranger = {"sub": "idp-oscar", "email": "oscar@example.test", "name": "Oscar Example"}
    env = {
        "CAIRN_OIDC_ISSUER": idp.url,
        "CAIRN_OIDC_CLIENT_ID": idp.CLIENT_ID,
        "CAIRN_OIDC_CLIENT_SECRET": idp.CLIENT_SECRET,
        "CAIRN_OIDC_LABEL": "Acme SSO",
        "CAIRN_ALLOWED_EMAILS": " Ada@Example.test ",
    }
    options = ("--sign-in", "oidc", "--join", "personal", "--plans", str(plans))
    db = tmp_path / "cairn.db"
    server = Server(db, *options, env=env).start()

    async def journey() -> None:
        async with (
            httpx2.AsyncClient(base_url=server.url, timeout=30) as person,
            httpx2.AsyncClient(base_url=server.url, timeout=30) as other,
        ):
            # Someone the operator did not list goes through the provider and is turned away.
            idp.person = stranger
            button = re.search(r'href="(/auth/sso[^"]*)"', (await other.get("/login")).text)
            to_provider = await other.get(html.unescape(button.group(1)))
            back = await other.get(to_provider.headers["location"])
            refused = await other.get(back.headers["location"])
            assert refused.status_code == 403, refused.text
            assert "oscar@example.test" not in refused.text.lower()
            assert "cairn_session" not in other.cookies

            # The listed person signs in and gets a workspace of their own, on the free plan.
            assert await sign_in(person, idp, ada) == "/welcome"
            usage = await person.get("/usage")
            assert usage.status_code == 200 and "the Free plan" in usage.text

    try:
        asyncio.run(journey())
    finally:
        output = server.stop()
        idp.close()
    listed = cairn("workspace-list", "--db", str(db))
    assert listed.returncode == 0, listed.stderr
    rows = {row["account"]: row for row in json.loads(listed.stdout)}
    (account,) = set(rows) - {"acct_default"}  # the stranger left no workspace behind
    assert account.startswith("acct_")
    assert (rows[account]["plan"], rows[account]["members"]) == (None, 1)
    by_hand = cairn("plan-set", "--db", str(db), "--account", account, "--plan", "solo")
    assert by_hand.returncode == 0, by_hand.stderr
    again = {
        row["account"]: row for row in json.loads(cairn("workspace-list", "--db", str(db)).stdout)
    }
    assert again[account]["plan"] == "solo"
    for text in (output, listed.stdout, by_hand.stdout):
        assert "example.test" not in text.lower()
    assert idp.CLIENT_SECRET not in output


def test_a_rollback_onto_a_newer_database_is_one_line_from_the_real_process(tmp_path):
    """An older cairn meets a database a newer one migrated (infra/gcp/README.md,
    "Rollback"): `usage` and `serve` each exit 1 by themselves with one line on stderr, the
    word SchemaTooNew and both versions, no traceback, and leave the file as it was."""
    db = tmp_path / "cairn.db"
    Store(db).close()  # made by this cairn, then stamped as a newer one leaves it
    con = sqlite3.connect(db)
    try:
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        con.commit()
    finally:
        con.close()
    before = db.read_bytes()
    port = str(free_port())
    for argv in (["usage", "--account", "acct_default"], ["serve", "--port", port]):
        result = cairn(argv[0], "--db", str(db), *argv[1:])  # one that does not exit: 60 s, out
        assert result.returncode == 1, (argv, result.stderr)
        assert result.stdout == ""
        assert "Traceback" not in result.stderr
        (line,) = result.stderr.splitlines()
        assert line.startswith("SchemaTooNew: the database has schema version ")
        assert f"version {SCHEMA_VERSION + 1}; this cairn knows up to {SCHEMA_VERSION}." in line
        assert db.read_bytes() == before, argv[0]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["cairn.db"]


def test_a_file_that_is_not_a_database_is_one_line_from_the_real_process(tmp_path):
    """--db names a file that is not a SQLite database: `serve` and `workspace-list` each
    exit 1 by themselves with one line on stderr that names the file, no traceback, nothing
    on stdout, and leave the file as it was."""
    db = tmp_path / "cairn.db"
    db.write_bytes(b"a text file, not a database\n" * 64)
    before = db.read_bytes()
    for argv in (["serve", "--port", str(free_port())], ["workspace-list"]):
        result = cairn(argv[0], "--db", str(db), *argv[1:])  # one that does not exit: 60 s, out
        assert result.returncode == 1, (argv, result.stderr)
        assert result.stdout == ""
        assert "Traceback" not in result.stderr
        (line,) = result.stderr.splitlines()
        assert line.startswith(f"the file at {db} is not a cairn database; --db names the ")
        assert "a text file" not in line
        assert db.read_bytes() == before, argv[0]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["cairn.db"]


class Forms(HTMLParser):
    """Every form on a page as a browser reads it: its `action`, its `method` and the fields
    it would send, in order. A text or hidden input sends its value, a checkbox or a radio
    only when it is ticked, a select its selected option (else its first), a textarea its
    text; buttons carry no name here and send nothing. Each field keeps its tag's attributes,
    and a select its options (value, label, selected)."""

    def __init__(self, page: str) -> None:
        super().__init__()
        self.forms: list[SimpleNamespace] = []
        self._form: SimpleNamespace | None = None
        self._open: SimpleNamespace | None = None  # a select or a textarea being read
        self._option: SimpleNamespace | None = None
        self.feed(page)
        self.close()

    def handle_starttag(self, tag, attrs):
        given = dict(attrs)
        if tag == "form":
            method = (given.get("method") or "get").lower()
            self._form = SimpleNamespace(action=given.get("action") or "", method=method, fields=[])
            self.forms.append(self._form)
        elif self._form is None or not (given.get("name") or tag == "option"):
            return
        elif tag == "input":
            kind = (given.get("type") or "text").lower()
            ticked = kind not in ("checkbox", "radio") or "checked" in given
            if ticked and kind not in ("submit", "button", "reset", "image", "file"):
                default = "on" if kind in ("checkbox", "radio") else ""
                value = given.get("value") or default
                field = SimpleNamespace(name=given["name"], value=value, attrs=given, options=[])
                self._form.fields.append(field)
        elif tag in ("select", "textarea"):
            self._open = SimpleNamespace(name=given["name"], value="", attrs=given, options=[])
        elif tag == "option" and self._open is not None:
            self._option = SimpleNamespace(
                value=given.get("value") or "", label="", selected="selected" in given
            )
            self._open.options.append(self._option)

    def handle_data(self, data):
        if self._option is not None:
            self._option.label += data
        elif self._open is not None:
            self._open.value += data

    def handle_endtag(self, tag):
        if tag == "option":
            self._option = None
        elif tag in ("select", "textarea") and self._open is not None and self._form is not None:
            chosen = [o for o in self._open.options if o.selected] or self._open.options[:1]
            if tag == "select":
                self._open.value = chosen[0].value if chosen else ""
            self._form.fields.append(self._open)
            self._open = None
        elif tag == "form":
            self._form = None


def form_to(page: str, action: str) -> SimpleNamespace:
    """The one form on `page` that posts to `action`."""
    found = [form for form in Forms(page).forms if form.action == action]
    assert len(found) == 1, (action, [form.action for form in Forms(page).forms])
    return found[0]


def as_sent(form: SimpleNamespace) -> dict[str, list[str]]:
    """A form's fields as the browser posts them: each name with its values, in order."""
    sent: dict[str, list[str]] = {}
    for field in form.fields:
        sent.setdefault(field.name, []).append(field.value)
    return sent


def section_of(page: str, source: str) -> str:
    """The part of /sources about one source."""
    found = re.search(
        rf'<section class="integration" id="source-{source}".*?</section>', page, re.DOTALL
    )
    assert found, page
    return found.group(0)


def database_text(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


EMAIL_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


def addresses_on(page: str, viewer: str) -> list[str]:
    """Every email address in a page's HTML but one: the layout gives the signed-in person
    their own address as the tooltip of their name (base.html, title="{{ person.email }}"),
    once, which is the viewer's and not the page's."""
    own = f'title="{viewer}">'
    assert page.count(own) == 1, page.count(own)
    return [found.group(0) for found in EMAIL_ADDRESS.finditer(page.replace(own, ""))]


def words_of(page: str) -> str:
    """A page's text as a person reads it: no tags, entities decoded, white space as one."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())


def on_since(section: str) -> datetime:
    """When a source was last switched on, as /sources gives it to the browser."""
    found = re.search(r'On since <time datetime="([^"]+)"', section)
    assert found, section
    return datetime.fromisoformat(found.group(1))


def rfc3339(moment: datetime) -> str:
    """A time as Granola and Google write one, in UTC with "Z", to the microsecond: cairn
    compares an item's times with the moment its source was switched on, to the microsecond,
    so a stand-in that rounded them could put an item made just after it before it."""
    return moment.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class Granola:
    """A stand-in Granola on a loopback port: List notes and Get note in the shapes of
    docs/granola-api.md, behind a made-up Bearer key. `meeting` puts a note there, made and
    last changed at `made`; `asked` is every request it answered (path and query), `heard`
    the same with the time.monotonic() it came at, `listed` the ids List notes gave,
    `refused` how many requests it turned away for their key. `fail_once` has the next List
    notes answered 500 and the one after it held until `released` is set."""

    # A made-up key in the documented shape, built here so no file holds one.
    KEY = "grn_" + "J0urney" + "MadeUpKey" + "7c1e"

    def __init__(self) -> None:
        self.notes: dict[str, dict] = {}
        self.asked: list[str] = []
        self.heard: list[tuple[float, str]] = []
        self.listed: set[str] = set()
        self.refused = 0
        self.failures = 0
        self.released = threading.Event()
        self.released.set()
        stand_in = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def answer(self, status: int, body: dict) -> None:
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                stand_in.heard.append((time.monotonic(), self.path))
                stand_in.asked.append(self.path)
                if self.headers.get("authorization") != f"Bearer {stand_in.KEY}":
                    stand_in.refused += 1
                    return self.answer(401, {"code": "INVALID_API_KEY", "message": "Bad key"})
                parts = urlsplit(self.path)
                asked = {name: values[0] for name, values in parse_qs(parts.query).items()}
                if parts.path == "/v1/notes":
                    if stand_in.failures:
                        stand_in.failures -= 1
                        stand_in.released.clear()  # the next listing waits to be let through
                        return self.answer(500, {"code": "INTERNAL_ERROR", "message": "Oops"})
                    stand_in.released.wait(20)
                    return self.answer(*stand_in.list_notes(asked))
                note = stand_in.notes.get(parts.path.removeprefix("/v1/notes/"))
                if note is None:
                    return self.answer(404, {"code": "NOT_FOUND", "message": "Not found"})
                return self.answer(200, note)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def meeting(self, title: str, decision: str, made: datetime) -> str:
        note_id = f"not_J1note{len(self.notes) + 1:08d}"
        self.notes[note_id] = {
            "id": note_id, "object": "note", "title": title,
            "owner": {"name": "Ada Example", "email": "ada@example.test"},
            "created_at": rfc3339(made), "updated_at": rfc3339(made), "deleted_at": None,
            "web_url": f"https://notes.granola.example.test/d/{note_id}",
            "calendar_event": None,
            "attendees": [{"name": "Ada Example", "email": "ada@example.test"},
                          {"name": "Brook Example", "email": "brook@example.test"},
                          {"name": None, "email": "guest@example.test"}],
            "folder_membership": [], "space_membership": [],
            "summary_text": f"Decisions. {decision}",
            "summary_markdown": f"### Decisions\n\n- {decision}\n",
            "private_notes_text": "Ada's private aside: ask about the budget.",
            "private_notes_markdown": None,
            "transcript": None,
        }  # fmt: skip
        return note_id

    def list_notes(self, asked: dict[str, str]) -> tuple[int, dict]:
        """List notes: the notes at or after each bound given, newest first, on one page."""
        try:
            size = int(asked.get("page_size", "10"))
            bounds = {n: datetime.fromisoformat(asked[n]) for n in asked if n.endswith("_after")}
        except ValueError:
            return 400, {"code": "VALIDATION_ERROR", "message": "Bad request"}
        if not 1 <= size <= 30 or set(bounds) - {"created_after", "updated_after"}:
            return 400, {"code": "VALIDATION_ERROR", "message": "Bad request"}
        found = [
            note for note in self.notes.values()
            if all(datetime.fromisoformat(note[n.replace("_after", "_at")]) >= bound
                   for n, bound in bounds.items())
        ]  # fmt: skip
        found.sort(key=lambda note: note["created_at"], reverse=True)
        keys = ("id", "object", "title", "owner", "created_at", "updated_at", "deleted_at")
        notes = [{k: note[k] for k in keys} for note in found[:size]]
        self.listed.update(note["id"] for note in notes)
        return 200, {"notes": notes, "hasMore": False, "cursor": None}

    def got(self, note_id: str) -> bool:
        """Whether cairn asked Get note for this note."""
        return self.gets(note_id) > 0

    def gets(self, note_id: str) -> int:
        """How many times cairn asked Get note for this note."""
        return sum(urlsplit(path).path == f"/v1/notes/{note_id}" for path in list(self.asked))

    def listings(self, after: float = float("-inf")) -> list[float]:
        """When each List notes request came, on time.monotonic(), those after `after`."""
        heard = list(self.heard)
        return [at for at, path in heard if urlsplit(path).path == "/v1/notes" and at > after]

    def fail_once(self) -> None:
        self.failures = 1

    def close(self) -> None:
        self.released.set()  # a held listing is let go, so the server is not left waiting
        self.server.shutdown()
        self.server.server_close()


def test_a_teams_meetings_arrive_by_themselves_and_are_shared_with_a_list(tmp_path):
    """C3, S2 and T2 together, through the real process: the operator adds a Granola source
    on the command line, against a stand-in on a loopback port; the owner switches it on in
    the web view into the project it proposes, which is made private; a sync writes the meeting
    made after that and nothing of the one before; the owner shares the project with a list of
    two members at "Read only" and gives one of them the level that decides who reaches it;
    switched off, the source reads nothing, and the meeting made meanwhile is never read; a
    member's agent recalls the meeting with its source lines and cannot write there. The key
    is in no output and nowhere in the database."""
    idp, granola = IdentityProvider(), Granola()
    key_env = "CAIRN_JOURNEY_GRANOLA_KEY"
    env = {
        "CAIRN_OIDC_ISSUER": idp.url,
        "CAIRN_OIDC_CLIENT_ID": idp.CLIENT_ID,
        "CAIRN_OIDC_CLIENT_SECRET": idp.CLIENT_SECRET,
        "CAIRN_OIDC_LABEL": "Acme SSO",
        key_env: granola.KEY,  # the server's environment holds the key, never cairn
    }
    options = ("--sign-in", "oidc", "--join", "default", "--allowed-email-domain", "example.test")
    server = Server(tmp_path / "cairn.db", *options, env=env)
    db, url = server.db, server.url
    ada = {"sub": "idp-ada", "email": "ada@example.test", "name": "Ada Example"}
    brook = {"sub": "idp-brook", "email": "brook@example.test", "name": "Brook Example"}
    cy = {"sub": "idp-cy", "email": "cy@example.test", "name": "Cy Example"}
    dee = {"sub": "idp-dee", "email": "dee@example.test", "name": "Dee Example"}
    secrets_seen = [granola.KEY, idp.CLIENT_SECRET]
    printed: list[str] = []
    # settle_minutes 0: Granola's note is read once it has not changed for that many minutes
    # (30 unless set), so that a summary still being written waits. A meeting made a moment
    # ago is then read at once, where the default would have this test wait half an hour or
    # give the stand-in's notes times that have not come yet.
    config = {"key_env": key_env, "base_url": f"{granola.url}/v1", "settle_minutes": 0}
    decided = "Quill moves the payout cutoff to 16:00 CET."

    def run(*args: str) -> subprocess.CompletedProcess:
        """The operator's command, with the key in its environment as the server has it."""
        done = cairn(*args, "--db", str(db), env={key_env: granola.KEY})
        printed.extend((done.stdout, done.stderr))
        return done

    async def journey() -> None:
        async with (
            httpx2.AsyncClient(base_url=url, timeout=30) as owner,
            httpx2.AsyncClient(base_url=url, timeout=30) as reader,
            httpx2.AsyncClient(base_url=url, timeout=30) as manager,
            httpx2.AsyncClient(base_url=url, timeout=30) as newcomer,
        ):
            # 1. The operator adds the source on the command line: its config names the
            # variable that holds the key, and it is added off.
            added = run("source-add", "--name", "granola", "--kind", "granola",
                        "--config", json.dumps(config))  # fmt: skip
            assert added.returncode == 0, added.stderr
            assert "it is off and reads nothing until it is switched on" in added.stdout
            assert "source-enable --name granola --project meetings" in added.stdout
            listed = run("source-list")
            assert listed.returncode == 0 and listed.stdout.startswith("granola (granola) off;")
            assert "never run" in listed.stdout and granola.asked == []

            # 2. Ada, the first to sign in and so the workspace's owner, finds it off on the
            # Sources page, which says what switching on reads, and proposes `meetings`.
            assert await sign_in(owner, idp, ada) == "/welcome"
            page = await owner.get("/sources")
            assert page.status_code == 200, page.text
            shown = section_of(page.text, "granola")
            said = words_of(shown)
            assert "Off. It reads nothing." in said
            assert "Switching it on reads only what happens from then on" in said
            assert "each is read as soon as it is listed" in said  # settle_minutes 0
            on = form_to(shown, "/integrations/granola/on")
            assert on.method == "post"
            # no project to tick yet: the project it proposes is typed in as a new one
            assert [(field.name, field.value) for field in on.fields] == [
                ("new_project", "meetings")
            ]
            switched = await owner.post(on.action, data=as_sent(on), headers=SAME_ORIGIN)
            assert switched.status_code == 303, switched.text
            assert switched.headers["location"] == "/sources"
            shown = section_of((await owner.get("/sources")).text, "granola")
            since = on_since(shown)
            assert "On since" in words_of(shown)
            assert '<span class="scope-mode is-write">Writes</span><span>meetings</span>' in shown
            assert form_to(shown, "/integrations/granola/off").method == "post"

            # 3. Two meetings at Granola, one made ten minutes before the switch-on (inside
            # the hour cairn lists back, so it is listed and cairn must leave it), one after.
            # One sync saves one.
            before = granola.meeting(
                "Kickoff with Zephyrine", "The zephyrine budget stays frozen.",
                since - timedelta(minutes=10),
            )  # fmt: skip
            made = datetime.now(UTC)
            assert made > since
            after = granola.meeting("Payout cutoff review", decided, made)
            synced = run("source-sync", "--name", "granola")
            assert synced.returncode == 0, synced.stderr
            assert synced.stdout.startswith("granola: 1 saved, 0 unchanged, 0 refused"), synced
            assert before in granola.listed and not granola.got(before) and granola.got(after)
            project = await owner.get("/b/meetings")
            assert project.status_code == 200
            pages = re.findall(r'href="(/b/meetings/p/[^"]+)"', project.text)
            assert len(pages) == 1, pages
            (meeting_page,) = pages
            seen = await owner.get(meeting_page)
            said = words_of(seen.text)
            assert seen.status_code == 200 and "Payout cutoff review" in said and decided in said
            assert "Attendees: Ada Example, Brook Example, and 1 more without a name" in said
            assert "Owner: Ada Example" in said
            assert addresses_on(seen.text, ada["email"]) == []
            assert "private aside" not in said
            for query in ("zephyrine", "kickoff"):
                searched = await owner.get("/search", params={"q": query})
                assert searched.status_code == 200 and "budget stays frozen" not in searched.text
                assert "Kickoff with Zephyrine" not in searched.text
            searched = await owner.get("/search", params={"q": "payout cutoff"})
            assert meeting_page in searched.text

            # 4. The project is private: a member gets what a project that does not exist gives.
            assert await sign_in(reader, idp, cy) == "/welcome"
            assert await sign_in(manager, idp, dee) == "/welcome"
            assert await sign_in(newcomer, idp, brook) == "/welcome"
            hidden, missing = await reader.get("/b/meetings"), await reader.get("/b/no-such")
            assert hidden.status_code == missing.status_code == 404
            assert hidden.text.replace("meetings", "?") == missing.text.replace("no-such", "?")
            assert (await reader.get(meeting_page)).status_code == 404
            searched = await reader.get("/search", params={"q": "payout cutoff"})
            assert searched.status_code == 200 and meeting_page not in searched.text
            # Ada adds Cy and Dee at "Read only", with "Add several people" as rendered.
            access = await owner.get("/b/meetings/access")
            assert access.status_code == 200, access.text
            share = form_to(access.text, "/b/meetings/share")
            fields = {field.name: field for field in share.fields}
            assert share.method == "post" and list(fields) == ["emails", "level"]
            assert fields["emails"].value == "" and fields["level"].value == "read"
            levels = {option.value: option.label for option in fields["level"].options}
            assert levels == {
                "read": "Read only",
                "write": "Read and write",
                "manage": "Read and write, and decide who reaches it",
            }
            fields["emails"].value = "cy@example.test, dee@example.test"
            shared = await owner.post(share.action, data=as_sent(share), headers=SAME_ORIGIN)
            assert shared.status_code == 303, shared.text
            assert shared.headers["location"] == "/b/meetings/access"
            # Then she gives Dee the level that adds deciding who reaches it.
            rule = form_to((await owner.get("/b/meetings/access")).text, "/b/meetings/access")
            assert rule.method == "post"
            assert [f.value for f in rule.fields if f.name == "everyone"] == ["none"]
            people = {f.attrs["aria-label"]: f for f in rule.fields if f.name == "people"}
            assert set(people) == {f"What {who['name']} gets" for who in (brook, cy, dee)}
            assert people["What Cy Example gets"].value.endswith(":read")
            assert people["What Brook Example gets"].value.endswith(":none")
            dees = people["What Dee Example gets"]
            assert dees.value.endswith(":read")
            (manage,) = [o for o in dees.options if o.label == levels["manage"]]
            dees.value = manage.value
            changed = await owner.post(rule.action, data=as_sent(rule), headers=SAME_ORIGIN)
            assert changed.status_code == 303, changed.text
            # Cy reads the meeting's page and cannot write there, nor open the access page.
            read = await reader.get(meeting_page)
            assert read.status_code == 200 and decided in words_of(read.text)
            note = {"text": "# Cutoff\n\nCy thinks 17:00 is better.\n"}
            wrote = await reader.post("/b/meetings/notes", data=note, headers=SAME_ORIGIN)
            assert wrote.status_code == 403, wrote.status_code
            assert (await reader.get("/b/meetings/access")).status_code == 403
            # Dee opens the access page and changes it: Brook gets "Read only".
            theirs = await manager.get("/b/meetings/access")
            assert theirs.status_code == 200, theirs.text
            rule = form_to(theirs.text, "/b/meetings/access")
            people = {f.attrs["aria-label"]: f for f in rule.fields if f.name == "people"}
            assert people["What Dee Example gets"].value == manage.value
            brooks = people["What Brook Example gets"]
            assert (await newcomer.get(meeting_page)).status_code == 404
            brooks.value = next(o.value for o in brooks.options if o.label == "Read only")
            refused = await reader.post(rule.action, data=as_sent(rule), headers=SAME_ORIGIN)
            assert refused.status_code == 403
            assert (await newcomer.get(meeting_page)).status_code == 404  # Cy changed nothing
            given = await manager.post(rule.action, data=as_sent(rule), headers=SAME_ORIGIN)
            assert given.status_code == 303, given.text
            assert (await newcomer.get(meeting_page)).status_code == 200

            # 5. A second sync writes nothing new. Switched off on the Sources page, the
            # source is not read; a meeting made meanwhile is never read, even once it is on
            # again, while one made after that is.
            again = run("source-sync", "--name", "granola")
            assert again.returncode == 0 and again.stdout.startswith("granola: 0 saved, 0 unc")
            shown = section_of((await owner.get("/sources")).text, "granola")
            off = form_to(shown, "/integrations/granola/off")
            assert off.method == "post" and off.fields == []
            switched = await owner.post(off.action, data=as_sent(off), headers=SAME_ORIGIN)
            assert switched.status_code == 303 and switched.headers["location"] == "/sources"
            shown = section_of((await owner.get("/sources")).text, "granola")
            assert "Off. It reads nothing." in words_of(shown)
            while_off = granola.meeting(
                "Chargeback rota", "The marsupial rota starts in December.", datetime.now(UTC)
            )
            asked = len(granola.asked)
            skipped = run("source-sync", "--name", "granola")
            assert skipped.returncode == 0, skipped.stderr
            assert skipped.stdout == "granola: off, so not read (source-enable switches it on)\n"
            assert len(granola.asked) == asked  # Granola was asked nothing
            on = form_to(shown, "/integrations/granola/on")
            assert as_sent(on) == {"project": ["meetings"], "new_project": [""]}  # ticked: its own
            switched = await owner.post(on.action, data=as_sent(on), headers=SAME_ORIGIN)
            assert switched.status_code == 303, switched.text
            again_since = on_since(section_of((await owner.get("/sources")).text, "granola"))
            assert again_since > since
            made = datetime.now(UTC)
            assert made > again_since
            later = granola.meeting("Refund queue triage", "Refunds over 500 EUR wait a day.", made)
            synced = run("source-sync", "--name", "granola")
            assert synced.returncode == 0, synced.stderr
            assert synced.stdout.startswith("granola: 1 saved, 0 unchanged, 0 refused"), synced
            assert while_off in granola.listed and not granola.got(while_off)
            assert granola.got(later) and not granola.got(before)
            project = await owner.get("/b/meetings")
            assert len(re.findall(r'href="/b/meetings/p/[^"]+"', project.text)) == 2
            assert "Refund queue triage" in project.text and "Chargeback rota" not in project.text

            # 6. Cy's agent, connected by URL, recalls the meeting with its source lines from
            # the project Cy reaches at "Read only", and a note into it is refused.
            async def agent(client):
                hits = text_of(await client.call_tool("recall", {"query": "payout cutoff"}))
                hit = next(h for h in hits["hits"] if "16:00 CET" in h["text"])
                assert hit["project"] == "meetings" and meeting_page.endswith(f"/p/{hit['page']}")
                where = {"project": "meetings", "slug": hit["page"], "claim_id": hit["claim_id"]}
                (span,) = text_of(await client.call_tool("evidence", where))["passages"]
                assert span["source_ref"] == f"meeting:granola/{after}"
                assert decided in span["text"] and span["start_line"] > 1
                wrote = await client.call_tool(
                    "note", {"project": "meetings", "text": "# Cutoff\n\nIt moves to 17:00.\n"}
                )
                assert wrote.is_error, wrote

            _, storage = await connect_by_url(url, reader, "", "cy-laptop", agent)
            secrets_seen.extend([storage.tokens.access_token, storage.tokens.refresh_token])

    try:
        server.start()
        asyncio.run(journey())
    finally:
        output = server.stop()
        idp.close()
        granola.close()

    assert granola.refused == 0  # every request carried the key from the environment
    for secret in [*secrets_seen, *idp.issued]:
        assert secret not in output and not any(secret in text for text in printed)
    assert "?" not in output.replace("? ", ""), "a query string reached the server's output"
    stored = database_text(db)
    for secret in [*secrets_seen, *idp.issued]:
        assert secret not in stored
    for never_read in ("zephyrine", "marsupial", "guest@example.test", "private aside"):
        assert never_read not in stored.lower(), never_read


def unpadded_b64(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


class Google:
    """A stand-in Google on a loopback port: the OAuth token endpoint, for a service account,
    and the Calendar API's events.list, in the shapes of docs/google-auth.md and
    docs/google-calendar-api.md. The service account's key is made here, at run time.

    The token endpoint issues a token only to a request it verifies with the key's public half:
    a form of exactly `grant_type` and `assertion`, a JWT signed RS256 whose header names the
    key, whose `iss` is the account, `scope` the Calendar scope, `aud` Google's token endpoint
    (whatever `token_uri` says), `sub` the person it acts for, and whose lifetime is an hour
    from now. `granted` holds the claims of each token issued, `turned_away` why any request
    was refused. events.list answers only the token issued last, lists `events` that overlap
    `timeMin` to `timeMax` (an all-day event's days in the calendar's zone), and gives the
    answers queued in `refusals` first, one each. `listings` is every events.list request:
    when it came, its path and query, its headers."""

    SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
    AUDIENCE = "https://oauth2.googleapis.com/token"
    JWT_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
    ACCOUNT = "cairn-reader@journey-project.iam.example.test"
    KEY_ID = "j2" + "5c0ffee1" * 4  # made up
    ZONE = "Europe/Madrid"

    def __init__(self, subject: str) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        self.public = key.public_key()
        self.subject = subject
        self.events: list[dict] = []
        self.granted: list[dict] = []
        self.assertions: list[str] = []
        self.turned_away: list[str] = []
        self.issued: list[str] = []
        self.listings: list[SimpleNamespace] = []
        self.refusals: list[tuple[int, str]] = []
        stand_in = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def answer(self, status: int, body: dict) -> None:
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self) -> None:
                size = int(self.headers.get("content-length", "0"))
                form = parse_qs(self.rfile.read(size).decode(), keep_blank_values=True)
                why = stand_in.unsigned(form) if self.path == "/token" else "not the endpoint"
                if why:
                    stand_in.turned_away.append(why)
                    return self.answer(400, {"error": "invalid_grant", "error_description": why})
                token = "ya29." + "J2MadeUp" + f"AccessToken{len(stand_in.issued) + 1:04d}"
                stand_in.issued.append(token)
                issued = {"access_token": token, "expires_in": 3599, "token_type": "Bearer"}
                self.answer(200, issued)

            def do_GET(self) -> None:
                parts = urlsplit(self.path)
                asked = {name: values[0] for name, values in parse_qs(parts.query).items()}
                auth = self.headers.get("authorization")
                heard = SimpleNamespace(at=time.monotonic(), path=self.path, auth=auth)
                heard.headers = str(self.headers)
                stand_in.listings.append(heard)
                if parts.path != "/calendar/v3/calendars/primary/events":
                    return stand_in.refusal(self, 404, "notFound")
                if self.headers.get("authorization") != f"Bearer {(stand_in.issued or [''])[-1]}":
                    return stand_in.refusal(self, 401, "authError")
                if stand_in.refusals:
                    return stand_in.refusal(self, *stand_in.refusals.pop(0))
                if (asked.get("singleEvents"), asked.get("orderBy")) != ("true", "startTime"):
                    return stand_in.refusal(self, 400, "invalid")
                low, high = (datetime.fromisoformat(asked[n]) for n in ("timeMin", "timeMax"))
                items = [e for e in stand_in.events if stand_in.overlaps(e, low, high)]
                self.answer(200, {
                    "kind": "calendar#events", "summary": "Ada Example", "updated":
                    rfc3339(datetime.now(UTC)), "timeZone": stand_in.ZONE, "accessRole": "owner",
                    "defaultReminders": [], "items": items,
                })  # fmt: skip

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def credential(self) -> str:
        """The service account's key file, as the text the environment variable holds."""
        return json.dumps({
            "type": "service_account", "project_id": "journey-project",
            "private_key_id": self.KEY_ID, "private_key": self.pem,
            "client_email": self.ACCOUNT, "client_id": "1" + "07" * 10,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": f"{self.url}/token",  # this machine: cairn allows it for stand-ins
        })  # fmt: skip

    def key_lines(self) -> list[str]:
        """The key's body, line by line (the short last one aside, which could match by
        chance): none may show anywhere but in the variable."""
        lines = self.pem.splitlines()
        return [line for line in lines if len(line) >= 16 and not line.startswith("-----")]

    def unsigned(self, form: dict[str, list[str]]) -> str | None:
        """Why a token request is not one this account signed as documented; None when it is."""
        if sorted(form) != ["assertion", "grant_type"] or form["grant_type"] != [self.JWT_GRANT]:
            return "not exactly grant_type and assertion"
        (signed,) = form["assertion"]
        self.assertions.append(signed)
        try:
            head, body, signature = signed.split(".")
            header, claims = json.loads(unpadded_b64(head)), json.loads(unpadded_b64(body))
            self.public.verify(
                unpadded_b64(signature), f"{head}.{body}".encode(), padding.PKCS1v15(),
                hashes.SHA256(),
            )  # fmt: skip
        except (ValueError, InvalidSignature):
            return "not a JWT this key signed"
        if header != {"alg": "RS256", "typ": "JWT", "kid": self.KEY_ID}:
            return "not the header of this key"
        expected = {"iss": self.ACCOUNT, "scope": self.SCOPE, "aud": self.AUDIENCE}
        if {name: claims.get(name) for name in expected} != expected:
            return "not the account, the scope or the audience"
        if claims.get("sub") != self.subject:
            return "not the subject"
        lifetime = claims.get("exp", 0) - claims.get("iat", 0)
        if lifetime != 3600 or abs(claims.get("iat", 0) - time.time()) > 60:
            return "not an hour from now"
        self.granted.append(claims)
        return None

    def overlaps(self, event: dict, low: datetime, high: datetime) -> bool:
        """Whether an event is in events.list's window: ends after timeMin, starts before
        timeMax."""

        def moment(given: dict) -> datetime:
            if "dateTime" in given:
                return datetime.fromisoformat(given["dateTime"])
            day = date.fromisoformat(given["date"])
            return datetime.combine(day, datetime.min.time(), ZoneInfo(self.ZONE))

        return moment(event["end"]) > low and moment(event["start"]) < high

    @staticmethod
    def refusal(handler: BaseHTTPRequestHandler, status: int, reason: str) -> None:
        domain = "usageLimits" if reason == "rateLimitExceeded" else "global"
        item = {"domain": domain, "reason": reason, "message": f"Refused: {reason}"}
        handler.answer(status, {"error": {"errors": [item], "code": status, "message": reason}})

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def timed(start: datetime, end: datetime) -> dict:
    return {"start": {"dateTime": rfc3339(start)}, "end": {"dateTime": rfc3339(end)}}


def test_a_companys_calendar_is_read_by_the_server_signed_in_as_a_service_account(tmp_path):
    """C5 with Google's server sign-in and S2, through the real process: the operator adds a
    Google Calendar source whose credential, a service account's key file made at run time, is
    in the environment of the server and of the command line; the owner switches it on from
    the Sources page; the token endpoint, a stand-in on a loopback port, issues a token only
    once it has verified what the real process signed; one sync writes the one meeting that
    ended after the switch-on, and the page says who met without an address. A rate limit is
    waited out; the Calendar API refusing the token (401) is one line that says what to check,
    shown on the Sources page until the next good sync. The key and the token are in no output
    and nowhere stored."""
    idp, google = IdentityProvider(), Google(subject="ada@example.test")
    credential_env = "CAIRN_JOURNEY_GOOGLE_CREDENTIAL"
    env = {
        "CAIRN_OIDC_ISSUER": idp.url,
        "CAIRN_OIDC_CLIENT_ID": idp.CLIENT_ID,
        "CAIRN_OIDC_CLIENT_SECRET": idp.CLIENT_SECRET,
        "CAIRN_OIDC_LABEL": "Acme SSO",
        credential_env: google.credential(),
    }
    options = ("--sign-in", "oidc", "--join", "default", "--allowed-email-domain", "example.test")
    server = Server(tmp_path / "cairn.db", *options, env=env)
    db, url = server.db, server.url
    ada = {"sub": "idp-ada", "email": "ada@example.test", "name": "Ada Example"}
    config = {
        "credentials_env": credential_env,
        "subject": "ada@example.test",
        "base_url": f"{google.url}/calendar/v3",  # this machine: allowed for a stand-in
    }
    printed: list[str] = []
    madrid = ZoneInfo(Google.ZONE)
    description = (
        "<p>Agenda for the quarter:</p><ul><li>Budget</li><li>Hiring</li></ul>"
        '<p>Read <a href="https://docs.example.test/plan">the plan</a> first.</p>'
    )

    def run(*args: str) -> subprocess.CompletedProcess:
        """The operator's command, with the credential in its environment."""
        done = cairn(*args, "--db", str(db), env={credential_env: google.credential()})
        printed.extend((done.stdout, done.stderr))
        return done

    def ended(end: datetime) -> None:
        while datetime.now(UTC) <= end:
            pass  # the real clock, which the command line reads, has passed it (a millisecond)

    async def journey() -> None:
        async with httpx2.AsyncClient(base_url=url, timeout=30) as owner:
            # 1. The operator adds the source: off, its config names the variable, a subject
            # and the stand-in's address.
            added = run("source-add", "--name", "team-calendar", "--kind", "google_calendar",
                        "--config", json.dumps(config))  # fmt: skip
            assert added.returncode == 0, added.stderr
            assert "it is off and reads nothing until it is switched on" in added.stdout
            assert "source-enable --name team-calendar --project calendar" in added.stdout
            listed = run("source-list")
            assert listed.stdout.startswith("team-calendar (google_calendar) off;"), listed
            assert google.listings == [] and google.assertions == []  # nothing was sent

            # 2. Ada switches it on from the Sources page, into the project it proposes.
            assert await sign_in(owner, idp, ada) == "/welcome"
            shown = section_of((await owner.get("/sources")).text, "team-calendar")
            said = words_of(shown)
            assert "Off. It reads nothing." in said
            assert "Events of the primary calendar of ada@example.test" in said
            assert credential_env not in shown and "PRIVATE KEY" not in shown
            on = form_to(shown, "/integrations/team-calendar/on")
            assert on.method == "post"
            assert [(field.name, field.value) for field in on.fields] == [
                ("new_project", "calendar")
            ]
            switched = await owner.post(on.action, data=as_sent(on), headers=SAME_ORIGIN)
            assert switched.status_code == 303, switched.text
            since = on_since(section_of((await owner.get("/sources")).text, "team-calendar"))

            # 3. The calendar holds five events; one is a meeting of Ada's that has ended
            # since the switch-on, and only it is read.
            just_after = since + timedelta(milliseconds=1)
            planning = {
                "id": "j2planning0001", "status": "confirmed", "summary": "Quarterly planning",
                **timed(since - timedelta(minutes=45), just_after),
                "organizer": {"email": "ada@example.test", "displayName": "Ada Example",
                              "self": True},
                "attendees": [
                    {"email": "ada@example.test", "displayName": "Ada Example", "self": True,
                     "responseStatus": "accepted"},
                    {"email": "brook@example.test", "displayName": "Brook Example",
                     "responseStatus": "accepted"},
                    {"email": "guest@example.test", "responseStatus": "needsAction"},
                    {"email": "board-4@resource.example.test", "displayName": "Board Room Four",
                     "resource": True, "responseStatus": "accepted"},
                ],
                "conferenceData": {
                    "conferenceId": "abc-defg-hij",
                    "conferenceSolution": {"key": {"type": "hangoutsMeet"}, "name": "Google Meet"},
                    "entryPoints": [{"entryPointType": "video",
                                     "uri": "https://meet.google.com/abc-defg-hij"}],
                },
                "description": description,
            }  # fmt: skip
            day = since.astimezone(madrid).date()  # the switch-on's day, in the calendar's zone
            google.events = [
                planning,
                {"id": "j2retro000001", "summary": "Lighthouse retro",  # ended before it
                 **timed(since - timedelta(hours=3), since - timedelta(hours=2))},
                {"id": "j2standup0001", "summary": "Ongoing standup",  # not ended yet
                 **timed(since - timedelta(minutes=20), since + timedelta(hours=1))},
                {"id": "j2private0001", "summary": "Doctor visit", "visibility": "private",
                 **timed(since - timedelta(hours=1), just_after)},
                # yesterday, all day: it ended at midnight in Madrid, before the switch-on
                {"id": "j2allday00001", "summary": "Offsite yesterday",
                 "start": {"date": (day - timedelta(days=1)).isoformat()},
                 "end": {"date": day.isoformat()}},
            ]  # fmt: skip
            ended(just_after)
            synced = run("source-sync", "--name", "team-calendar")
            assert synced.returncode == 0, synced.stderr
            assert synced.stdout.startswith("team-calendar: 1 saved, 0 unchanged, 0 refused")
            # The token endpoint verified one request, for the scope and the subject; the
            # Calendar API saw that token, and never the key or the signed request.
            assert google.turned_away == [] and len(google.granted) == 1
            (claims,) = google.granted
            assert (claims["scope"], claims["sub"]) == (Google.SCOPE, "ada@example.test")
            (token,) = google.issued
            assert [r.path.split("?")[0] for r in google.listings] == [
                "/calendar/v3/calendars/primary/events"
            ]
            for request in google.listings:
                assert request.auth == f"Bearer {token}"
                seen = request.path + request.headers
                assert not any(line in seen for line in google.key_lines())
                assert not any(signed in seen for signed in google.assertions)
            # The page, as a person reads it in the web view.
            project = await owner.get("/b/calendar")
            pages = re.findall(r'href="(/b/calendar/p/[^"]+)"', project.text)
            assert len(pages) == 1, pages
            seen = await owner.get(pages[0])
            assert seen.status_code == 200
            said = words_of(seen.text)
            start, end = (datetime.fromisoformat(planning[n]["dateTime"]) for n in ("start", "end"))
            assert "Quarterly planning" in said
            assert f"When: {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC" in said
            assert "Organiser: Ada Example" in said
            assert "Attendees: Ada Example, Brook Example and 1 more" in said
            assert "Board Room Four" not in said  # a room is not a person
            assert "Meet: abc-defg-hij" in said
            assert "Agenda for the quarter:" in said and "- Budget - Hiring" in said
            assert "Read the plan (https://docs.example.test/plan) first." in said
            assert "<li>" not in said and "<a " not in said  # text, not the HTML
            assert addresses_on(seen.text, ada["email"]) == []
            for title in ("Lighthouse retro", "Ongoing standup", "Doctor visit", "Offsite"):
                assert title not in project.text, title

            # 4. Google answers the listing with a rate limit once: the run waits and goes on.
            # (It really sleeps, 1 to 2 seconds for a first wait, so the test asks for one.)
            now = datetime.now(UTC)
            vendor = {"id": "j2vendor00001", "summary": "Vendor call"}
            google.events.append({**vendor, **timed(now - timedelta(minutes=30), now)})
            ended(now)
            google.refusals = [(403, "rateLimitExceeded")]
            heard = len(google.listings)
            waited = run("source-sync", "--name", "team-calendar")
            assert waited.returncode == 0, waited.stderr
            assert waited.stdout.startswith("team-calendar: 1 saved, 0 unchanged"), waited
            refused, retried = google.listings[heard:]
            gap = retried.at - refused.at
            assert gap >= 1.0, gap  # it really waited, 2^0 s at least; how long it asks
            # for is pinned with an injected sleep (test_google_api_unit: 1.5 s, then 2.5)
            # Then a 401: the command exits 1 with one line that says what to check.
            google.refusals = [(401, "authError")]
            failed = run("source-sync", "--name", "team-calendar")
            assert failed.returncode == 1 and failed.stdout == ""
            lines = failed.stderr.splitlines()
            assert len(lines) == 2 and lines[0].startswith("no plan is applied"), lines
            failure = lines[1]
            assert failure.startswith("team-calendar: failed: Google Calendar refused access"), (
                failure
            )
            assert f"check that the credential in {credential_env} carries the scope" in failure
            assert not any(token in failure for token in google.issued)
            shown = section_of((await owner.get("/sources")).text, "team-calendar")
            assert "Failed: Google Calendar refused access (HTTP 401)" in words_of(shown)
            assert not any(token in shown for token in google.issued)
            # The next good sync clears it.
            cleared = run("source-sync", "--name", "team-calendar")
            assert cleared.returncode == 0, cleared.stderr
            assert cleared.stdout.startswith("team-calendar: 0 saved, 0 unchanged"), cleared
            said = words_of(section_of((await owner.get("/sources")).text, "team-calendar"))
            assert "Failed" not in said and "0 saved, 0 unchanged" in said
            assert len(re.findall(r'href="/b/calendar/p/', (await owner.get("/b/calendar")).text)
                       ) == 2  # fmt: skip

    try:
        server.start()
        asyncio.run(journey())
    finally:
        output = server.stop()
        idp.close()
        google.close()

    assert google.turned_away == []  # every token request was signed as documented
    secrets = [*google.key_lines(), *google.assertions, *google.issued, idp.CLIENT_SECRET]
    stored = database_text(db)
    for secret in [s for s in secrets if s]:
        assert secret not in output and not any(secret in text for text in printed)
        assert secret not in stored
    assert "?" not in output.replace("? ", ""), "a query string reached the server's output"
    for never_read in ("Lighthouse", "Ongoing standup", "Doctor visit", "Offsite", "guest@"):
        assert never_read not in stored, never_read


# A run's counts as the Sources page shows them (cairn.web.ui.SOURCE_COUNTS).
COUNTS = re.compile(r"\d+ saved, \d+ unchanged, \d+ refused, \d+ rejected, \d+ skipped")


def last_run(section: str) -> datetime | None:
    """When a source's last run ended, as its section on the Sources page says; None: never."""
    found = re.search(r'<dt>Last run</dt>\s*<dd><time datetime="([^"]+)"', section)
    return datetime.fromisoformat(found[1]) if found else None


async def until(what: str, check, seconds: float = 30.0):
    """`check()` awaited every tenth of a second until it gives something true, which is
    returned; AssertionError naming `what` once `seconds` have passed without: a hang guard
    on the real process and nothing else, so it is generous."""
    deadline = time.monotonic() + seconds
    while True:
        found = await check()
        if found:
            return found
        if time.monotonic() > deadline:
            raise AssertionError(f"{what}: not within {seconds:g} s")
        await asyncio.sleep(0.1)


def test_meetings_arrive_by_themselves_while_the_server_runs(tmp_path):
    """The background sync, through the real process and with no `source-sync` run: the
    server runs with `--sync-every 1` and the Granola key in its own environment; the
    operator adds a source, off, and Granola is asked nothing; the owner switches it on in
    the web view and meetings arrive in their project by themselves, each read once; switched
    off, Granola is asked nothing more and the meeting made meanwhile never arrives, even
    once it is on again; a failure Granola answers is on the Sources page in words until the
    next pass clears it. The key is in no output and nowhere in the database, and the server
    exits when asked."""
    idp, granola = IdentityProvider(), Granola()
    key_env = "CAIRN_JOURNEY_GRANOLA_KEY"
    env = {
        "CAIRN_OIDC_ISSUER": idp.url,
        "CAIRN_OIDC_CLIENT_ID": idp.CLIENT_ID,
        "CAIRN_OIDC_CLIENT_SECRET": idp.CLIENT_SECRET,
        "CAIRN_OIDC_LABEL": "Acme SSO",
        key_env: granola.KEY,  # only the server's environment holds the key: it syncs
    }
    every = 1.0  # seconds between two passes' starts: the least --sync-every takes
    options = ("--sign-in", "oidc", "--join", "default", "--allowed-email-domain", "example.test")
    server = Server(tmp_path / "cairn.db", *options, "--sync-every", f"{every:g}", env=env)
    db, url = server.db, server.url
    ada = {"sub": "idp-ada", "email": "ada@example.test", "name": "Ada Example"}
    secrets_seen = [granola.KEY, idp.CLIENT_SECRET]
    printed: list[str] = []
    # settle_minutes 0: Granola's note is read once it has not changed for that many minutes
    # (30 unless set), so that a summary still being written waits. A meeting made a moment
    # ago is then read on the next pass, where the default would hold it for half an hour.
    config = {"key_env": key_env, "base_url": f"{granola.url}/v1", "settle_minutes": 0}
    decided = "Quill moves the payout cutoff to 16:00 CET."
    made_ids: dict[str, str] = {}

    async def quiet(seconds: float, after: float) -> None:
        """Watch for `seconds`, a tenth at a time, failing at once if Granola is listed."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            assert granola.listings(after) == [], "Granola was listed while the source was off"
            await asyncio.sleep(0.1)

    async def journey() -> None:
        async with httpx2.AsyncClient(base_url=url, timeout=30) as owner:

            async def section() -> str:
                page = await owner.get("/sources")
                assert page.status_code == 200, page.text
                return section_of(page.text, "granola")

            async def pages(count: int) -> list[str] | None:
                project = await owner.get("/b/meetings")
                assert project.status_code == 200, project.text
                found = re.findall(r'href="(/b/meetings/p/[^"]+)"', project.text)
                assert len(found) <= count, found
                return found if len(found) == count else None

            async def a_pass_ends() -> None:
                """Until the Last run on the Sources page changes: a pass has just ended,
                and the next one starts about an interval after this one started."""
                before = last_run(await section())

                async def changed() -> bool:
                    return last_run(await section()) != before

                await until("a background pass to end", changed)

            # 1. The operator adds the source on the command line, against the database the
            # server runs on, with no key in the command's own environment. It is off, and
            # two intervals later Granola has been asked nothing at all.
            started = time.monotonic()
            added = cairn("source-add", "--name", "granola", "--kind", "granola",
                          "--config", json.dumps(config), "--db", str(db))  # fmt: skip
            printed.extend((added.stdout, added.stderr))
            assert added.returncode == 0, added.stderr
            assert "it is off and reads nothing until it is switched on" in added.stdout
            await quiet(2 * every + 0.5, after=float("-inf"))
            assert granola.heard == [] and time.monotonic() - started >= 2 * every

            # 2. Ada, the first to sign in and so the owner, switches it on from the Sources
            # page with the form as rendered: the project it proposes, `meetings`, made new.
            assert await sign_in(owner, idp, ada) == "/welcome"
            shown = await section()
            assert "Off. It reads nothing." in words_of(shown) and last_run(shown) is None
            on = form_to(shown, "/integrations/granola/on")
            assert on.method == "post"
            assert [(field.name, field.value) for field in on.fields] == [
                ("new_project", "meetings")
            ]
            switched = await owner.post(on.action, data=as_sent(on), headers=SAME_ORIGIN)
            assert switched.status_code == 303 and switched.headers["location"] == "/sources"
            since = on_since(await section())

            # 3. A meeting is made at Granola. With no command run, its page is in the project
            # within a few seconds, and the Sources page shows the run that wrote it.
            made = datetime.now(UTC)
            assert made > since
            made_ids["first"] = granola.meeting("Payout cutoff review", decided, made)
            (first_page,) = await until("the first meeting's page", lambda: pages(1))
            seen = await owner.get(first_page)
            said = words_of(seen.text)
            assert seen.status_code == 200 and "Payout cutoff review" in said and decided in said
            assert "Attendees: Ada Example, Brook Example, and 1 more without a name" in said
            assert "Owner: Ada Example" in said
            assert addresses_on(seen.text, ada["email"]) == [] and "private aside" not in said
            shown = await section()
            assert "On since" in words_of(shown) and last_run(shown) > since
            assert COUNTS.search(words_of(shown)) and "Failed" not in words_of(shown)

            # 4. A second meeting arrives too, on its own page; the first is not written
            # again, a pass later still.
            second = granola.meeting(
                "Refund queue triage", "Refunds over 500 EUR wait a day.", datetime.now(UTC)
            )
            made_ids["second"] = second
            both = await until("the second meeting's page", lambda: pages(2))
            assert first_page in both
            await a_pass_ends()
            assert await pages(2) == both
            assert granola.gets(made_ids["first"]) == granola.gets(second) == 1

            # 5. Ada switches it off from the page. A pass that has claimed the source before
            # the switch-off still sends its requests (claim_source is the last check before
            # the listing), so the switch-off is sent just after a pass has ended, when the
            # next is most of an interval away. From the moment it is answered, Granola is
            # not listed for three intervals and more, and the meeting made meanwhile is not
            # in the project.
            await a_pass_ends()
            off = form_to(await section(), "/integrations/granola/off")
            assert off.method == "post" and off.fields == []
            switched = await owner.post(off.action, data=as_sent(off), headers=SAME_ORIGIN)
            answered = time.monotonic()
            assert switched.status_code == 303 and switched.headers["location"] == "/sources"
            assert "Off. It reads nothing." in words_of(await section())
            made_ids["while_off"] = granola.meeting(
                "Chargeback rota", "The marsupial rota starts in December.", datetime.now(UTC)
            )
            await quiet(3 * every + 0.5, after=answered)
            assert granola.listings(answered) == [] and await pages(2) == both

            # 6. Switched on again, into the project it names, a meeting made after that arrives
            # by itself; the one made while it was off is listed and left.
            on = form_to(await section(), "/integrations/granola/on")
            assert as_sent(on) == {"project": ["meetings"], "new_project": [""]}
            switched = await owner.post(on.action, data=as_sent(on), headers=SAME_ORIGIN)
            assert switched.status_code == 303, switched.text
            again = on_since(await section())
            assert again > since
            made = datetime.now(UTC)
            assert made > again
            made_ids["later"] = granola.meeting(
                "Vendor shortlist", "Two vendors stay on the shortlist.", made
            )
            three = await until("the meeting made after the switch-on", lambda: pages(3))
            assert set(both) < set(three)
            assert made_ids["while_off"] in granola.listed
            project = (await owner.get("/b/meetings")).text
            assert "Vendor shortlist" in project and "Chargeback rota" not in project

            # 7. Granola fails once: the next listing is answered 500, and the one after it
            # is held until the failure has been seen. The Sources page says it in words;
            # let through, the next pass's good run clears it, with no command run.
            granola.fail_once()

            async def failed() -> str | None:
                shown = await section()
                return shown if "Failed:" in shown else None

            shown = await until("the failure on the Sources page", failed)
            (alert,) = re.findall(r'<span role="alert">(.*?)</span>', shown)
            assert words_of(alert) == (
                "Failed: Granola answered HTTP 500 to List notes, a fault on its side: the"
                " next run tries again"
            ), alert
            assert key_env not in shown and granola.KEY not in shown
            granola.released.set()

            async def cleared() -> str | None:
                said = words_of(await section())
                return said if "Failed" not in said and COUNTS.search(said) else None

            await until("the failure cleared by the next pass", cleared)
            assert await pages(3) == three

    try:
        server.start()
        asyncio.run(journey())
    finally:
        granola.released.set()  # a held listing is let go before the server is asked to stop
        output = server.stop()
        idp.close()
        granola.close()

    # 8. The server exited when asked, within Server.stop's wait, having shut its application
    # down (uvicorn ends a graceful stop by raising the SIGTERM it was sent again, so the code
    # is -SIGTERM); every request carried the key from its environment, which is in no output
    # and nowhere in the database.
    assert not server.killed and server.returncode == -signal.SIGTERM, server.returncode
    assert "Application shutdown complete." in output, output[-2000:]
    assert granola.refused == 0
    for name in ("first", "second", "later"):
        assert granola.gets(made_ids[name]) == 1, name  # each meeting read once
    assert granola.gets(made_ids["while_off"]) == 0
    failures = re.findall(r"source 'granola' of workspace \S+ failed: (\w+)", output)
    assert failures == ["SourceError"], output[-2000:]
    for secret in [*secrets_seen, *idp.issued]:
        assert secret not in output and not any(secret in text for text in printed)
    assert "?" not in output.replace("? ", ""), "a query string reached the server's output"
    stored = database_text(db)
    for secret in [*secrets_seen, *idp.issued]:
        assert secret not in stored
    for never_read in ("marsupial", "Chargeback rota", "guest@example.test", "private aside"):
        assert never_read not in stored, never_read


LOGO = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 32">'
    b'<text x="4" y="22" fill="#1b2a24">Juniper Field</text></svg>\n'
)


def test_an_install_under_its_own_name_shows_only_the_sign_in_and_remembers_it(tmp_path):
    """B1: `serve` with sign-in through a provider, the wall, a brand with a logo and a sign-in
    kept for 30 days. A browser that never signed in is sent to the sign-in page, whose bytes
    and headers do not name the software. The person signs in; an agent connects with only
    the URL, writes a note and reads it. The server starts again on the same database: the
    same browser is still signed in, and the agent still reads its note."""
    logo = tmp_path / "logo.svg"
    logo.write_bytes(LOGO)
    idp = IdentityProvider()
    env = {
        "CAIRN_OIDC_ISSUER": idp.url,
        "CAIRN_OIDC_CLIENT_ID": idp.CLIENT_ID,
        "CAIRN_OIDC_CLIENT_SECRET": idp.CLIENT_SECRET,
        "CAIRN_OIDC_LABEL": "Juniper Field SSO",
    }
    options = ("--sign-in", "oidc", "--join", "personal", "--wall")
    options += ("--brand-name", "Knowledge Base", "--brand-logo", str(logo))
    options += ("--session-days", "30")
    ada = {"sub": "idp-ada", "email": "ada@example.test", "name": "Ada Example"}
    server = Server(tmp_path / "data" / "kb.db", *options, env=env)
    (tmp_path / "data").mkdir()
    server.start()
    url = server.url
    secrets_seen = [idp.CLIENT_SECRET]

    def names_nothing(r: httpx2.Response, what: str) -> None:
        assert b"cairn" not in r.content.lower(), what
        for name, value in r.headers.items():
            assert "cairn" not in f"{name}: {value}".lower(), (what, name)

    async def read(client, slug: str) -> str:
        return text_of(await client.call_tool("page", {"project": "handbook", "slug": slug}))[
            "body"
        ]

    async def journey() -> None:
        async with (
            httpx2.AsyncClient(base_url=url, timeout=30) as person,
            httpx2.AsyncClient(base_url=url, timeout=30) as anyone,
        ):
            # A browser that has never signed in is sent to the sign-in page, and only there.
            for address in ("/", "/docs", "/b/handbook", "/no-such-page"):
                sent = await person.get(address)
                assert sent.status_code == 303, address
                assert sent.headers["location"].startswith("/login"), address
                names_nothing(sent, address)
            login = await person.get("/login")
            assert login.status_code == 200 and login.headers["x-robots-tag"] == "noindex"
            names_nothing(login, "the sign-in page")
            assert "Knowledge Base" in login.text and "Juniper Field SSO" in login.text
            assert 'alt="Knowledge Base"' in login.text
            shown = re.search(r'<img src="(/static/logo-[0-9a-f]{16}\.svg)"', login.text)
            image = await anyone.get(shown.group(1))
            assert image.status_code == 200 and image.content == LOGO
            assert image.headers["content-type"].startswith("image/svg+xml")
            assert "default-src 'none'" in image.headers["content-security-policy"]
            # What machines use answers as without the wall.
            assert (await anyone.get("/healthz")).status_code == 200
            assert (await anyone.post("/mcp", json={})).status_code == 401

            # The person signs in; the session cookie has its neutral name and lasts 30 days.
            assert await sign_in(person, idp, ada) == "/welcome"
            assert "cairn_session" not in person.cookies and "session" in person.cookies
            (kept,) = [c for c in person.cookies.jar if c.name == "session"]
            assert 29 * 86_400 < kept.expires - time.time() <= 30 * 86_400  # not 12 hours
            welcome = await person.get("/welcome")
            assert welcome.status_code == 200 and "Ada Example" in welcome.text
            assert "<title>" in welcome.text and " · Knowledge Base</title>" in welcome.text

            # An agent connects with only the URL, writes a note and reads it back.
            async def agent(client):
                await client.call_tool("new_project", {"name": "handbook"})
                noted = text_of(
                    await client.call_tool("note", {"project": "handbook", "text": NOTE})
                )
                assert noted["status"] == "saved"
                assert "Quill retries a failed charge" in await read(client, noted["page"])
                return noted["page"]

            slug, storage = await connect_by_url(url, person, "", "ada-laptop", agent)
            secrets_seen.extend([storage.tokens.access_token, storage.tokens.refresh_token])
            secrets_seen.append(person.cookies["session"])

            # The server starts again on the same database.
            server.stop()
            server.start()

            # The same browser is still signed in, and the agent still reads its note.
            again = await person.get(f"/b/handbook/p/{slug}")
            assert again.status_code == 200 and "Quill retries a failed charge" in again.text
            assert "Knowledge Base" in again.text and "Ada Example" in again.text
            heard = await call_tool(anyone, storage.tokens.access_token, "page", project="handbook",
                                    slug=slug)  # fmt: skip
            assert heard.status_code == 200 and "Quill retries a failed charge" in heard.text
            # Someone else is still shown only the way in.
            refused = await anyone.get(f"/b/handbook/p/{slug}")
            assert refused.status_code == 303 and refused.headers["location"].startswith("/login")

    try:
        asyncio.run(journey())
    finally:
        output = server.stop()
        idp.close()

    for secret in [*secrets_seen, *idp.issued]:
        assert secret not in output
    assert str(logo) not in output  # the logo's path is never shown
    assert "ada@example.test" not in output.lower()
    stored = database_text(server.db)
    for secret in [*secrets_seen, *idp.issued]:
        assert secret not in stored


# K1: Slack, both ways, from set-up to removal

LAUNCHER = Path(__file__).with_name("slack_serve.py")
STAND_IN = Path(__file__).resolve().parents[2] / "scripts" / "slack_standin.py"
PUBLIC = "https://cairn.example.test"
SLACK_KEY = base64.b64encode(bytes(range(100, 132))).decode()  # made up: 32 bytes
LINK = re.compile(re.escape(PUBLIC) + r"/integrations/slack/link\?t=([A-Za-z0-9_-]+)")


def slack_stand_in():
    """scripts/slack_standin.py's SlackStandIn, a colleague's stand-in for Slack, loaded as
    its own tests load it."""
    spec = importlib.util.spec_from_file_location("slack_standin", STAND_IN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SlackStandIn


class Loopback(httpx2.AsyncBaseTransport):
    """A browser's way to PUBLIC: a request to its host goes to the server's loopback port, as
    a proxy that ends TLS would send it, every header kept (Host among them); any other (the
    identity provider's) goes where it says. The browser's cookie jar sees https, so it sends
    back the cookies a server with an https public URL marks Secure."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.inner = httpx2.AsyncHTTPTransport()

    async def handle_async_request(self, request):
        if request.url.host == urlsplit(PUBLIC).hostname:
            local = request.url.copy_with(scheme="http", host="127.0.0.1", port=self.port)
            request = httpx2.Request(
                request.method, local, headers=request.headers, stream=request.stream
            )
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


def slack_said(message: bytes | dict) -> str:
    """What a message of the bot says: its fallback text and each block's text."""
    found = json.loads(message) if isinstance(message, bytes) else message
    texts = [found.get("text", "")]
    texts += [block["text"]["text"] for block in found.get("blocks", []) if "text" in block]
    return "\n".join(texts)


def slack_rows(db: Path, query: str, *args) -> list[tuple]:
    con = sqlite3.connect(db)
    try:
        return con.execute(query, args).fetchall()
    finally:
        con.close()


INSTALLS = "SELECT state, bot_token IS NULL, signing_secret IS NULL FROM slack_installs"


def test_a_workspace_uses_slack_from_set_up_to_removal(tmp_path):
    """K1 on the real server process. `serve --slack` runs through tests/system/slack_serve.py:
    `python -m cairn.server` with Slack's Web API and reply addresses pointed at the stand-in
    (scripts/slack_standin.py) on loopback and, for the last part, the store's clock moved 10
    days on at a restart; everything else is the real command. An owner sets the app up and
    pastes the two secrets; the stand-in's signed challenge turns it on; a person is offered a
    link by the slash command and confirms it signed in; they choose a project; the message
    shortcut files a message whose author is of the team and refuses one whose author is not;
    `find` answers with the claim and its link; a direct message is answered. Slack revokes
    the bot's token and the install is off; Slack uninstalls the app and the notes are due,
    not forgotten; 10 days on they are gone. A second workspace's install takes the two
    removal events in the other order and ends in the same state. No secret, one-time value or
    message is in the server's output, and no secret or one-time value in the database."""
    idp = IdentityProvider()
    standin = slack_stand_in()().__enter__()
    env = {
        "CAIRN_OIDC_ISSUER": idp.url,
        "CAIRN_OIDC_CLIENT_ID": idp.CLIENT_ID,
        "CAIRN_OIDC_CLIENT_SECRET": idp.CLIENT_SECRET,
        "CAIRN_OIDC_LABEL": "Fable Works SSO",
        "CAIRN_SLACK_KEY": SLACK_KEY,
        "JOURNEY_SLACK_API": standin.api_base,
    }
    options = ("--sign-in", "oidc", "--join", "personal", "--public-url", PUBLIC, "--slack")
    server = Server(tmp_path / "cairn.db", *options, env=env)
    server.runs = (str(LAUNCHER),)
    ada = {"sub": "idp-ada", "email": "ada@fable.example.test", "name": "Ada Example"}
    brook = {"sub": "idp-brook", "email": "brook@quill.example.test", "name": "Brook Example"}
    secrets_seen = [standin.signing_secret, standin.bot_token, SLACK_KEY, idp.CLIENT_SECRET]
    words = [
        "The tide turns at nine on the north pier.",
        "The harbour office opens at eight.",
        "The pilot boat leaves at six.",
    ]

    def address(install: str, kind: str) -> str:
        return f"{server.url}/integrations/slack/{install}/{kind}"  # where a proxy sends Slack

    async def replied(method: str, count: int) -> dict:
        """The `count`th call the server made to the stand-in by `method`, once it has."""

        async def check():
            found = [call.fields for call in standin.calls if call.method == method]
            return found[count - 1] if len(found) >= count else None

        return await until(f"call {count} to {method}", check)

    async def set_up(person) -> str:
        """The owner sets Slack up on the Sources page and pastes the two secrets; Slack's
        signed challenge turns it on. Returns the install's id."""
        assert ">Set up Slack</button>" in (await person.get("/sources")).text
        made = await person.post("/integrations/slack/setup", headers=SAME_ORIGIN)
        assert made.status_code == 303 and made.headers["location"] == "/sources#slack"
        page = html.unescape((await person.get("/sources")).text)
        (install,) = set(re.findall(r"/integrations/slack/(sl_[0-9a-f]{12})/events", page))
        assert f'"request_url": "{PUBLIC}/integrations/slack/{install}/events"' in page
        assert "https://api.slack.com/apps?new_app=1&manifest_json=" in page
        assert "Set up started." in page and 'type="password"' in page
        kept = await person.post(
            "/integrations/slack/secrets", headers=SAME_ORIGIN,
            data={"signing_secret": standin.signing_secret, "bot_token": standin.bot_token},
        )  # fmt: skip
        assert kept.status_code == 303
        page = (await person.get("/sources")).text
        assert "Ready: Slack accepted the bot token" in page
        assert standin.signing_secret not in page and standin.bot_token not in page
        assert standin.verify_url(address(install, "events")) == (200, b"review-challenge")
        assert "On: Slack verified the events address" in (await person.get("/sources")).text
        return install

    async def linked(person, install: str) -> None:
        """The slash command offers the Slack user a link; the person says yes signed in and
        is shown a code; the same Slack user types it back with the command."""
        status, body = standin.send_command(address(install, "commands"), text="find tide")
        assert status == 200 and json.loads(body)["response_type"] == "ephemeral"
        (value,) = set(LINK.findall(slack_said(body)))
        secrets_seen.append(value)
        shown = await person.get(f"/integrations/slack/link?t={value}")
        assert shown.status_code == 200 and "Link this Slack user to you?" in shown.text
        assert "Ada Review" in shown.text  # the name Slack gives the user (users.info)
        yes = await person.post(f"/integrations/slack/link?t={value}", headers=SAME_ORIGIN)
        assert yes.status_code == 200 and yes.headers["cache-control"] == "no-store"
        (code,) = set(re.findall(r"<code>([A-Z0-9]{4}-[A-Z0-9]{4})</code>", yes.text))
        secrets_seen.append(code)
        assert f"/cairn link {code}" in html.unescape(yes.text)
        again = await person.post(f"/integrations/slack/link?t={value}", headers=SAME_ORIGIN)
        assert again.status_code == 404  # used up
        status, body = standin.send_command(address(install, "commands"), text=f"link {code}")
        assert status == 200 and slack_said(body).startswith("Linked to ")
        page = (await person.get("/sources")).text
        assert f"<code>{standin.user_id}</code>" in page

    async def chose(person, install: str, project: str) -> None:
        """The person makes a project and makes it their Slack project."""
        made = await person.post(
            "/projects", data={"name": project, "kind": "general"}, headers=SAME_ORIGIN
        )
        assert made.status_code in (200, 303), made.text
        status, body = standin.send_command(address(install, "commands"), text=f"project {project}")
        assert status == 200 and f"Slack project: {project}" in slack_said(body)

    async def journey() -> None:
        async with (
            httpx2.AsyncClient(base_url=PUBLIC, transport=Loopback(server.port), timeout=30) as one,
            httpx2.AsyncClient(base_url=PUBLIC, transport=Loopback(server.port), timeout=30) as two,
        ):
            await sign_in(one, idp, ada)
            secrets_seen.extend(cookie.value for cookie in one.cookies.jar)
            first = await set_up(one)
            await linked(one, first)
            await chose(one, first, "tides")

            # The message shortcut files a message whose author is of the team...
            message = {"type": "message", "user": standin.user_id, "ts": "1700000000.000100",
                       "text": words[0]}  # fmt: skip
            sent = standin.send_action(address(first, "actions"), {"message": message})
            assert sent == (200, b"")
            filed = await replied("response_url", 1)
            assert filed["response_type"] == "ephemeral"
            assert slack_said(filed).startswith("Filed: ")
            assert f"<{PUBLIC}/b/tides/p/" in slack_said(filed)
            # ...and refuses one whose author is not, saying why.
            outside = {**message, "user": standin.external_user_id, "ts": "1700000000.000200"}
            sent = standin.send_action(address(first, "actions"), {"message": outside})
            assert sent == (200, b"")
            refused = await replied("response_url", 2)
            assert "author is not of this Slack workspace" in slack_said(refused)
            assert refused["response_type"] == "ephemeral"

            # `find` answers with the claim and its link, for the asker alone.
            status, body = standin.send_command(address(first, "commands"), text="find tide")
            assert status == 200 and json.loads(body)["response_type"] == "ephemeral"
            found = slack_said(body)
            assert words[0] in found and "Project: tides" in found
            claim = re.escape(f"<{PUBLIC}/b/tides/p/") + r"[a-z0-9-]+#cl_[0-9a-z]+\|"
            assert re.search(claim, found), found

            # A direct message is answered in that conversation: a search, then a note.
            direct = {"type": "message", "channel": "D00000001", "channel_type": "im",
                      "user": standin.user_id, "text": "north pier",
                      "ts": "1700000001.000100"}  # fmt: skip
            assert standin.send_event(address(first, "events"), direct) == (200, b"")
            answer = await replied("chat.postMessage", 1)
            assert answer["channel"] == "D00000001" and words[0] in slack_said(answer)
            note = {**direct, "text": f"note: {words[1]}", "ts": "1700000001.000200"}
            assert standin.send_event(address(first, "events"), note) == (200, b"")
            assert slack_said(await replied("chat.postMessage", 2)).startswith("Filed: ")
            came = slack_rows(server.db, "SELECT note_id, how FROM slack_notes ORDER BY how")
            assert [how for _, how in came] == ["filed", "typed"]
            notes = [note_id for note_id, _ in came]

            # Slack revokes the bot's token: off, the token deleted, nothing due.
            assert standin.revoke_bot(address(first, "events")) == (200, b"")
            page = html.unescape((await one.get("/sources")).text)
            assert "Off, because Slack revoked the bot's token." in page
            assert slack_rows(server.db, f"{INSTALLS} WHERE id = ?", first) == [("off", 1, 0)]
            assert standin.send_command(address(first, "commands"), text="find tide")[0] == 404
            due = "SELECT COUNT(*), COUNT(forget_at) FROM slack_notes"
            assert slack_rows(server.db, due) == [(2, 0)]

            # Slack uninstalls the app: removed, both secrets and the links gone, notes due.
            gone = standin.send_event(address(first, "events"), {"type": "app_uninstalled"})
            assert gone == (200, b"")
            page = (await one.get("/sources")).text
            assert "2 notes came through Slack: they will be forgotten on" in page
            assert ">Forget them now</button>" in page
            assert slack_rows(server.db, f"{INSTALLS} WHERE id = ?", first) == [("removed", 1, 1)]
            assert slack_rows(server.db, "SELECT COUNT(*) FROM slack_people") == [(0,)]
            assert slack_rows(server.db, due) == [(2, 2)]
            (when,) = {at for (at,) in slack_rows(server.db, "SELECT forget_at FROM slack_notes")}
            left = datetime.fromisoformat(when) - datetime.now(UTC)
            assert timedelta(days=9, hours=23) < left <= timedelta(days=10)
            kept = "SELECT COUNT(*) FROM notes WHERE id IN (?, ?)"
            assert slack_rows(server.db, kept, *notes) == [(2,)]  # due, not forgotten

            # Ten days on (the store's clock, from a restart), they are gone.
            server.stop()
            server.env["JOURNEY_DAYS_AHEAD"] = "10.05"
            server.start()

            async def forgotten():
                return slack_rows(server.db, kept, *notes) == [(0,)]

            await until("the notes that came through Slack forgotten", forgotten)
            assert slack_rows(server.db, due) == [(0, 0)]
            stored = database_text(server.db)
            assert words[0] not in stored and words[1] not in stored

            # A second workspace's install: Slack uninstalls first, then revokes the token.
            await sign_in(two, idp, brook)
            secrets_seen.extend(cookie.value for cookie in two.cookies.jar)
            second = await set_up(two)
            assert second != first
            await linked(two, second)
            await chose(two, second, "harbour")
            status, body = standin.send_command(
                address(second, "commands"), text=f"note {words[2]}"
            )
            assert status == 200 and slack_said(body).startswith("Filed: ")
            uninstalled, revoked = standin.uninstall(address(second, "events"), revoked_first=False)
            assert uninstalled == (200, b"") and revoked[0] == 404  # removed: no address
            both = slack_rows(server.db, f"{INSTALLS} WHERE id IN (?, ?)", first, second)
            assert both == [("removed", 1, 1), ("removed", 1, 1)]
            assert slack_rows(server.db, "SELECT COUNT(*) FROM slack_people") == [(0,)]
            assert slack_rows(server.db, due) == [(1, 1)]
            page = (await two.get("/sources")).text
            assert "1 note came through Slack: it will be forgotten on" in page

    server.start()
    try:
        asyncio.run(journey())
    finally:
        output = server.stop()
        standin.__exit__(None, None, None)
        idp.close()

    for secret in [*secrets_seen, *idp.issued]:
        assert secret not in output, "a secret or a one-time value reached the server's output"
    assert "?t=" not in output, "a one-time address reached the server's output"
    for said in [*words, "north pier"]:
        assert said not in output, said
    stored = database_text(server.db)
    for secret in [*secrets_seen, *idp.issued]:
        assert secret not in stored, "a secret or a one-time value is in the database"
