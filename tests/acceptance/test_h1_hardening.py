"""H1 acceptance tests: five hardening items an outside security review left open.

The contract, in plain words:

1. A database the command line makes is private. Under a umask of 022, every folder a
   command makes for its database is 0700, and the database file is made 0600 before SQLite
   opens it (O_CREAT | O_EXCL | O_NOFOLLOW), so its -wal and -shm files are 0600 too. A
   --db that is a symbolic link is refused in one line on stderr, exit 2, and nothing is
   made at either end of the link. An existing database that others on this machine can
   read (its mode lets them, and every folder above it lets them through) is not changed
   and not refused: the command goes on and says so once on stderr, in one line that names
   the file's mode and `chmod 600`. `serve` says it at start. A private file, or one in a
   folder only its owner can enter (a container's /data, pytest's own folders), is quiet.
2. A key can be made narrow from the command line. `key-create --project NAME` (repeatable)
   makes a key that reaches only those projects, `--read-only` one that only reads. A project
   the workspace does not have is refused, exit 1, naming it, and no key and no key file is
   made. Without either option the key is what it always was. `key-list` prints each key's
   reach as the Keys page shows it. The help of both says, in one sentence, what a key made
   with no option can do: read and write every project, export the workspace, and open a web
   session.
3. Answers to a machine are not kept by a cache. Every answer on /api and /mcp, to a
   request with or without a credential, refused or not, carries `Cache-Control: no-store`.
4. Wrong keys and wrong logins are limited per client address. After 30 refused
   credentials in a minute from one address (behind a trusted proxy, the address the proxy
   gives), a further request from it with a wrong credential on /api, /mcp or POST /login,
   or with none on /api or POST /login, gets 429 with a Retry-After and one fixed sentence
   in place of the 401, until the minute has passed. The credential is still looked up (one
   indexed read): a correct one works, always, from any address, whether this server has
   seen it before or not, and never counts. So one script that keeps retrying a revoked key
   does not lock out the people and agents behind its address, after a restart as before
   one: after 100 wrong keys from one address in a minute, a correct key the server has
   never seen works from it at once, over REST, over MCP and at POST /login. A request with
   no credential at all never counts, and /mcp answers one as before (401 and where OAuth
   starts), so an agent's first probes never lock it out: an agent that connects by URL
   after 100 probes from its address gets in. The count is kept in memory, for a bounded
   number of addresses, and nothing of a credential is kept.
5. A plain-http listener on a public address says so. `serve` on an address that is not
   loopback, with no --public-url or an http one, prints one line at start: credentials
   travel in clear text on this listener unless a proxy in front ends TLS, and
   `--public-url https://...` says one does. It does not refuse. Nothing is printed for a
   loopback host or an https public URL.

Made-up names only; every address is example.test.
"""

import asyncio
import html
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import httpx2
import pytest
from mcp.client import Client as McpClient
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata
from pydantic import AnyUrl
from starlette.testclient import TestClient

from cairn.gate import lint_gate
from cairn.plans import Plan, Plans
from cairn.server import create_app, main
from cairn.store import Store
from cairn.web import cli, ratelimit


def mode(path: Path | str) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def command(*args: object) -> list[str]:
    return [sys.executable, "-m", "cairn.server", *map(str, args)]


def run(*args: object) -> subprocess.CompletedProcess:
    """The real command line in a process of its own, under a umask of 022."""
    return subprocess.run(command(*args), capture_output=True, text=True, umask=0o022)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def listened(monkeypatch) -> list:
    """`serve` in this process, with uvicorn.run recorded and never run."""
    calls: list = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    return calls


@pytest.fixture
def open_folder():
    """A folder others on this machine may enter, every folder above it too (pytest's own
    folders are its owner's only): under /tmp, mode 0755."""
    folder = Path(tempfile.mkdtemp(prefix="cairn-h1-", dir="/tmp"))
    os.chmod(folder, 0o755)
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# 1. A database the command line makes is private


def test_key_create_and_import_make_private_folders_and_files(tmp_path):
    db = tmp_path / "fresh" / "nested" / "cairn.db"
    made = run("key-create", "--db", db, "--label", "laptop", "--out", tmp_path / "laptop.env")
    assert made.returncode == 0, made.stderr
    assert made.stderr == ""
    assert mode(tmp_path / "fresh") == 0o700 and mode(db.parent) == 0o700
    assert mode(db) == 0o600
    store = Store(db)
    try:  # while a store holds it open, SQLite's side files take the database's mode
        store.create_project("ops", "")
        assert mode(f"{db}-wal") == 0o600 and mode(f"{db}-shm") == 0o600
    finally:
        store.close()
    archive = tmp_path / "export.zip"
    assert run("export", "--db", db, "--account", "acct_default", "--out", archive).returncode == 0
    copy = tmp_path / "restored" / "deep" / "cairn.db"
    restored = run("import", "--db", copy, "--account", "acct_default", "--in", archive)
    assert restored.returncode == 0, restored.stderr
    assert mode(tmp_path / "restored") == 0o700 and mode(copy.parent) == 0o700
    assert mode(copy) == 0o600
    assert run("compact", "--db", copy).returncode == 0
    assert mode(copy) == 0o600  # compacting rewrites the file in place


def test_serve_makes_a_private_database(tmp_path):
    db = tmp_path / "data" / "cairn.db"
    port = free_port()
    process = subprocess.Popen(
        command("serve", "--db", db, "--port", port),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, umask=0o022,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )  # fmt: skip
    try:
        deadline = time.monotonic() + 60
        while True:
            assert process.poll() is None, process.stdout.read()
            assert time.monotonic() < deadline, "serve did not answer /healthz within 60 s"
            try:
                if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=5).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        assert mode(db.parent) == 0o700 and mode(db) == 0o600
        assert mode(f"{db}-wal") == 0o600 and mode(f"{db}-shm") == 0o600
    finally:
        process.terminate()
        process.communicate(timeout=30)


def test_a_db_that_is_a_link_is_refused_in_one_line(tmp_path, listened, capsys):
    target = tmp_path / "elsewhere.db"
    link = tmp_path / "link.db"
    link.symlink_to(target)
    for args in (
        ["key-create", "--db", str(link), "--label", "laptop", "--out", str(tmp_path / "k.env")],
        ["serve", "--db", str(link)],
        ["import", "--db", str(link), "--account", "acct_default", "--in", str(tmp_path / "x")],
    ):
        assert main(args) == 2, args
        out, err = capsys.readouterr()
        (line,) = err.splitlines()
        assert out == "" and str(link) in line and "link" in line
    assert listened == []
    assert not target.exists() and not (tmp_path / "k.env").exists()
    Store(target).close()  # a link to a database that exists is refused all the same
    assert main(["workspace-list", "--db", str(link)]) == 2
    out, err = capsys.readouterr()
    assert out == "" and len(err.splitlines()) == 1


def test_an_open_database_is_named_once_and_left_as_it_is(open_folder, listened, capsys):
    db = open_folder / "cairn.db"
    Store(db).close()
    os.chmod(db, 0o644)  # as a database made before this change, under a umask of 022
    assert main(["workspace-list", "--db", str(db)]) == 0
    out, err = capsys.readouterr()
    (line,) = err.splitlines()
    assert "0644" in line and f"chmod 600 {db}" in line and "others on this machine" in line
    assert out.startswith("[")  # the command went on
    assert mode(db) == 0o644  # and changed nothing
    assert main(["serve", "--db", str(db)]) == 0
    _, err = capsys.readouterr()
    assert err.splitlines() == [line]  # serve says it at start, once
    assert len(listened) == 1
    os.chmod(db, 0o600)
    assert main(["workspace-list", "--db", str(db)]) == 0
    assert main(["serve", "--db", str(db)]) == 0
    assert capsys.readouterr().err == ""  # quiet when the mode is private


def test_an_open_file_in_a_private_folder_is_quiet(tmp_path, capsys):
    """Others cannot reach a file in a folder only its owner may enter: a container's /data
    is 0700, and so are pytest's own folders."""
    db = tmp_path / "cairn.db"
    Store(db).close()
    os.chmod(db, 0o644)
    assert mode(tmp_path) == 0o700
    assert main(["workspace-list", "--db", str(db)]) == 0
    assert capsys.readouterr().err == ""


# 2. A key can be made narrow from the command line

KEY_DEFAULT = (
    "A key made without --project or --read-only can read and write every project, export the"
    " workspace, and open a web session."
)


def key_from(path: Path) -> str:
    (line,) = path.read_text(encoding="utf-8").splitlines()
    return line.partition("=")[2]


def test_key_create_makes_a_key_for_chosen_projects_that_only_reads(tmp_path, capsys):
    db = tmp_path / "cairn.db"
    store = Store(db)
    ids = {name: store.create_project(name, "").id for name in ("alpha", "beta", "gamma")}
    store.close()
    out = tmp_path / "reader.env"
    args = ["--project", "alpha", "--project", "beta", "--read-only"]
    assert main(["key-create", "--db", str(db), "--label", "reader", "--out", str(out), *args]) == 0
    assert capsys.readouterr().out == f"{out}\n"
    store = Store(db)
    try:
        (info,) = store.list_keys()
        assert sorted(info.projects) == sorted([ids["alpha"], ids["beta"]])
        assert info.access == "read"
        who = store.resolve_key(key_from(out))
        view = store.narrow(who.projects, who.access)
        assert sorted(b.name for b in view.list_projects()) == ["alpha", "beta"]
        with pytest.raises(PermissionError):
            view.create_project("delta", "")
    finally:
        store.close()


def test_a_project_the_workspace_does_not_have_is_refused_and_no_key_is_made(tmp_path, capsys):
    db = tmp_path / "cairn.db"
    store = Store(db)
    store.create_project("alpha", "")
    store.close()
    out = tmp_path / "k.env"
    args = ["--db", str(db), "--label", "k", "--out", str(out), "--project", "alpha"]
    assert main(["key-create", *args, "--project", "nowhere"]) == 1
    printed = capsys.readouterr()
    (line,) = printed.err.splitlines()
    assert printed.out == "" and "'nowhere'" in line
    assert not out.exists()
    store = Store(db)
    try:
        assert store.list_keys() == []
    finally:
        store.close()
    fresh = tmp_path / "fresh" / "cairn.db"  # no database yet: it has no project at all
    assert main(["key-create", "--db", str(fresh), "--label", "k", "--out", str(out),
                 "--project", "alpha"]) == 1  # fmt: skip
    assert "'alpha'" in capsys.readouterr().err and not out.exists() and not fresh.exists()


def test_a_key_made_with_no_option_is_what_it_always_was(tmp_path):
    db, out = tmp_path / "cairn.db", tmp_path / "k.env"
    assert main(["key-create", "--db", str(db), "--label", "k", "--out", str(out)]) == 0
    store = Store(db)
    try:
        (info,) = store.list_keys()
        assert (info.projects, info.access) == (None, "write")
    finally:
        store.close()


def test_key_list_shows_each_keys_reach_as_the_keys_page_does(tmp_path, capsys):
    db = tmp_path / "cairn.db"
    store = Store(db)
    for name in ("alpha", "beta", "gamma"):
        store.create_project(name, "")
    store.close()
    for label, extra in (("laptop", []), ("reader", ["--project", "beta", "--read-only"])):
        out = tmp_path / f"{label}.env"
        assert main(["key-create", "--db", str(db), "--label", label, "--out", str(out),
                     *extra]) == 0  # fmt: skip
    capsys.readouterr()
    assert main(["key-list", "--db", str(db)]) == 0
    printed = capsys.readouterr()
    assert printed.err == ""
    laptop, reader = printed.out.splitlines()
    assert laptop.startswith(
        "laptop: Read and write: All projects; No access: nothing in this workspace; made by the"
        " workspace on "
    )
    assert reader.startswith(
        "reader: Read only: beta; No access: every other project (2 you can see); made by the"
        " workspace on "
    )
    for label in ("laptop", "reader"):  # never a key
        assert key_from(tmp_path / f"{label}.env") not in printed.out


def test_the_help_of_both_says_what_a_key_with_no_option_can_do(capsys):
    for name in ("key-create", "key-list"):
        with pytest.raises(SystemExit):
            main([name, "--help"])
        assert KEY_DEFAULT in " ".join(capsys.readouterr().out.split()), name


# 3. Answers to a machine are not kept by a cache

BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


@pytest.fixture
def served(tmp_path):
    """The app in this process, as `serve` builds it, with a key and a project."""
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("ops")
    store.create_project("alpha", "")
    plans = Plans({"small": Plan("Small", requests_per_minute=7)}, "small")
    client = TestClient(
        create_app(store, gate=lint_gate, public_url=BASE, plans=plans),
        base_url=BASE,
        raise_server_exceptions=False,
    )
    client.__enter__()
    try:
        yield client, key
    finally:
        client.__exit__(None, None, None)
        store.close()


def test_every_answer_on_api_and_mcp_says_no_store(served):
    client, key = served
    good, wrong = {"x-api-key": key}, {"x-api-key": "not-a-key-of-this-server"}
    elsewhere = {**MCP_HEADERS, **good, "host": "evil.example.test"}
    answers = [
        client.get("/api/projects", headers=good),  # 200
        client.get("/api/account/export", headers=good),  # 200, a zip
        client.get("/api/projects"),  # 401, no credential
        client.get("/api/projects", headers=wrong),  # 401, a wrong one
        client.get("/api/projects/nowhere/pages", headers=good),  # 404
        client.post("/api/projects", headers=good, json={}),  # 4xx, a bad body
        client.post("/mcp", headers={**MCP_HEADERS, **good}, json=LIST_TOOLS),  # 200
        client.post("/mcp", headers=MCP_HEADERS, json=LIST_TOOLS),  # 401, where OAuth starts
        client.post("/mcp", headers=elsewhere, json=LIST_TOOLS),  # 421, the transport's own
    ]
    answers += [client.get("/api/projects", headers=good) for _ in range(3)]  # the last is 429
    statuses = [answer.status_code for answer in answers]
    assert statuses[:5] == [200, 200, 401, 401, 404] and statuses[6:9] == [200, 401, 421]
    assert statuses[-1] == 429 and 400 <= statuses[5] < 500
    for answer in answers:
        assert answer.headers.get_list("cache-control") == ["no-store"], answer.request.url


def test_pages_and_files_keep_their_own_caching(served):
    client, _ = served
    assert client.get("/login").headers.get_list("cache-control") == ["no-store"]
    assert "no-store" not in client.get(
        "/static/fonts/atkinson-hyperlegible-next.OFL.txt"
    ).headers.get("cache-control", "")


# 4. Wrong keys and wrong logins are limited per client; a correct one always works

OFFICE, HOME, CAFE = "203.0.113.7", "198.51.100.20", "192.0.2.44"
REDIRECT = "http://127.0.0.1:33418/callback"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}


class Clock:
    """The app's clock, far from today, moved by hand."""

    def __init__(self) -> None:
        self.now = datetime(2099, 3, 1, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class Limited:
    """The app in this process, with a key, a project and an injected clock; `client(address)`
    is a TestClient whose requests come from that address. The first one runs the app's
    lifespan (an app runs it once), so it is the one that calls /mcp."""

    def __init__(self, tmp_path) -> None:
        self.store = Store(tmp_path / "cairn.db")
        self.key = self.store.create_key("ops")
        self.store.create_project("alpha", "")
        self.clock = Clock()
        self.app = create_app(self.store, gate=lint_gate, public_url=BASE, clock=self.clock)
        self.clients: list[TestClient] = []

    def client(self, address: str) -> TestClient:
        client = TestClient(self.app, base_url=BASE, client=(address, 50000))
        if not self.clients:
            client.__enter__()
            self.clients.append(client)
        return client

    def close(self) -> None:
        for client in self.clients:
            client.__exit__(None, None, None)
        self.store.close()


@pytest.fixture
def limited(tmp_path):
    world = Limited(tmp_path)
    try:
        yield world
    finally:
        world.close()


def looked_up(store: Store, monkeypatch) -> list[str]:
    """Every key the store is asked about, from now on."""
    asked: list[str] = []
    identify = store.identify_key

    def counting(key: str):
        asked.append(key)
        return identify(key)

    monkeypatch.setattr(store, "identify_key", counting)
    return asked


def test_thirty_wrong_keys_and_the_next_wrong_one_is_429(limited, monkeypatch):
    office = limited.client(OFFICE)
    for n in range(30):
        wrong = office.get("/api/projects", headers={"x-api-key": f"wrong-{n}"})
        assert wrong.status_code == 401, n
    asked = looked_up(limited.store, monkeypatch)
    bearer = {**MCP_HEADERS, "authorization": "Bearer wrong-31"}
    refused = [
        office.get("/api/projects", headers={"x-api-key": "wrong-30"}),
        office.post("/mcp", headers=bearer, json=LIST_TOOLS),
        office.get("/api/projects"),  # no credential, where one is required
    ]
    for answer in refused:
        assert answer.status_code == 429
        assert answer.json() == {"error": ratelimit.TOO_MANY_FAILURES}
        assert answer.headers["retry-after"] == "60"
    assert "wrong-30" in asked  # still looked up: a correct one would have worked
    assert limited.client(HOME).get("/api/projects", headers={"x-api-key": "x"}).status_code == 401
    limited.clock.now += timedelta(seconds=61)  # the minute has passed
    again = office.get("/api/projects", headers={"x-api-key": "wrong-32"})
    assert again.status_code == 401 and asked[-1] == "wrong-32"


def test_wrong_logins_count_and_are_refused_the_same_way(limited, monkeypatch):
    office = limited.client(OFFICE)
    for n in range(20):
        assert (
            office.post("/login", data={"key": f"wrong-{n}"}, headers=SAME_ORIGIN).status_code
            == 401
        )
    for n in range(10):
        assert office.get("/api/projects", headers={"x-api-key": f"x-{n}"}).status_code == 401
    asked = looked_up(limited.store, monkeypatch)
    for data in ({"key": "wrong-20"}, {"key": ""}):
        busy = office.post("/login", data=data, headers=SAME_ORIGIN, follow_redirects=False)
        assert busy.status_code == 429 and busy.headers["retry-after"] == "60"
        assert ratelimit.TOO_MANY_FAILURES in busy.text and "<form" in busy.text
    assert asked == ["wrong-20"]  # the wrong key was looked up; an empty one never is


def test_after_100_wrong_keys_a_correct_key_never_seen_works_at_once(limited, monkeypatch):
    """The outage case: one stale script behind an office's address keeps retrying a revoked
    key, and the server has just restarted, so it has accepted nothing yet. After 100 wrong
    keys from that address in one minute, a correct key the server has never seen works from
    it at once, over REST, over MCP and at POST /login, and never counts."""
    office = limited.client(OFFICE)
    revoked = limited.store.create_key("stale-script")
    assert limited.store.revoke_key("stale-script")
    answers = [
        office.get("/api/projects", headers={"x-api-key": revoked}).status_code for _ in range(100)
    ]
    assert answers == [401] * 30 + [429] * 70
    never_seen = limited.store.create_key("colleague")
    asked = looked_up(limited.store, monkeypatch)
    good = {"x-api-key": never_seen}
    assert office.get("/api/projects", headers=good).status_code == 200
    mcp = office.post("/mcp", headers={**MCP_HEADERS, **good}, json=LIST_TOOLS)
    assert mcp.status_code == 200
    login = office.post("/login", data={"key": never_seen}, headers=SAME_ORIGIN,
                        follow_redirects=False)  # fmt: skip
    assert login.status_code == 303
    assert never_seen in asked  # looked up, so known or not made no difference
    for _ in range(50):  # a correct key never counts, and is never refused
        assert office.get("/api/projects", headers=good).status_code == 200
    assert office.get("/api/projects", headers={"x-api-key": revoked}).status_code == 429


def test_correct_credentials_never_count(limited):
    office = limited.client(OFFICE)
    good = {"x-api-key": limited.key}
    for _ in range(29):
        office.get("/api/projects", headers={"x-api-key": "wrong"})
    for _ in range(100):
        assert office.get("/api/projects", headers=good).status_code == 200
    assert office.get("/api/projects", headers={"x-api-key": "wrong"}).status_code == 401
    assert office.get("/api/projects", headers={"x-api-key": "wrong"}).status_code == 429


class Tokens(TokenStorage):
    def __init__(self) -> None:
        self.tokens = None
        self.client_info = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client_info

    async def set_client_info(self, client_info):
        self.client_info = client_info


def test_an_agent_connects_by_url_after_a_hundred_probes(limited):
    """Every MCP client's first request carries no credential and is answered 401 with where
    OAuth starts. A request with no credential never counts: after 100 such probes from one
    address, an agent from it still connects with only the URL."""
    transport = httpx2.ASGITransport(app=limited.app, client=(CAFE, 50001))

    async def probe() -> None:
        async with httpx2.AsyncClient(transport=transport, base_url=BASE) as cafe:
            for n in range(100):
                answer = await cafe.post("/mcp", headers=MCP_HEADERS, json=LIST_TOOLS)
                assert answer.status_code == 401, n
                assert "resource_metadata=" in answer.headers["www-authenticate"]

    async def connect() -> set[str]:
        browser = httpx2.AsyncClient(transport=transport, base_url=BASE)
        seen: dict[str, str] = {}

        async def redirect_handler(url: str) -> None:
            consent = await browser.get(url)
            assert consent.status_code == 302
            page = urlsplit(consent.headers["location"])
            target = f"{page.path}?{page.query}"
            signed_in = await browser.post(
                "/login", data={"key": limited.key, "next": target}, headers=SAME_ORIGIN
            )
            assert signed_in.status_code == 303
            form = await browser.get(target)
            fields = dict(
                re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', form.text)
            )
            fields = {k: html.unescape(v) for k, v in fields.items()}
            approved = await browser.post(
                "/oauth/consent", data={**fields, "decision": "approve"}, headers=SAME_ORIGIN
            )
            assert approved.status_code == 303, approved.text
            seen["location"] = approved.headers["location"]

        async def callback_handler() -> AuthorizationCodeResult:
            answer = parse_qs(urlsplit(seen["location"]).query)
            return AuthorizationCodeResult(code=answer["code"][0], state=answer["state"][0])

        oauth = OAuthClientProvider(
            server_url=f"{BASE}/mcp",
            client_metadata=OAuthClientMetadata(
                client_name="h1-agent",
                redirect_uris=[AnyUrl(REDIRECT)],
                token_endpoint_auth_method="none",
                grant_types=["authorization_code", "refresh_token"],
            ),
            storage=Tokens(),
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )
        http = httpx2.AsyncClient(transport=transport, auth=oauth, timeout=30)
        async with (
            browser,
            http,
            McpClient(
                streamable_http_client(f"{BASE}/mcp", http_client=http), mode="legacy"
            ) as agent,
        ):
            return {tool.name for tool in (await agent.list_tools()).tools}

    async def run_it() -> set[str]:
        async with limited.app.router.lifespan_context(limited.app):
            await probe()
            return await connect()

    assert "recall" in asyncio.run(run_it())


def test_the_count_is_bounded_in_memory(limited):
    failures = limited.app.state.credential_failures
    for n in range(failures.addresses + 50):
        failures.failed(f"10.{n // 65536}.{n // 256 % 256}.{n % 256}")
    assert failures.tracked() <= failures.addresses


# 5. A plain-http listener on a public address says so


@pytest.fixture
def serve_says(tmp_path, listened, capsys):
    """What `serve` prints on stderr before it listens, with the options given; it always
    goes on to listen (uvicorn.run is recorded in `.listened`, never run)."""

    def says(*args: str) -> list[str]:
        assert main(["serve", "--db", str(tmp_path / "cairn.db"), *args]) == 0
        return capsys.readouterr().err.splitlines()

    says.listened = listened
    return says


def test_a_public_listener_without_https_says_credentials_travel_in_clear(serve_says):
    for args in (
        ["--host", "0.0.0.0"],  # the image's default command
        ["--host", "0.0.0.0", "--public-url", "http://cairn.example.test"],
        ["--host", "192.0.2.10", "--public-url", "http://127.0.0.1:8765"],
        ["--host", "::"],
        ["--host", "cairn.example.test"],
    ):
        (line,) = serve_says(*args)
        assert line == cli.CLEAR_TEXT.format(host=args[1]), args
        assert "clear text" in line and "--public-url https://" in line and "TLS" in line
    hosts = [call[1]["host"] for call in serve_says.listened]  # it never refuses
    assert hosts == ["0.0.0.0", "0.0.0.0", "192.0.2.10", "::", "cairn.example.test"]


def test_loopback_or_https_is_quiet(serve_says):
    for args in (
        [],  # 127.0.0.1, the default
        ["--host", "localhost"],
        ["--host", "::1"],
        ["--host", "127.0.0.2"],
        ["--host", "0.0.0.0", "--public-url", "https://cairn.example.test"],
        ["--host", "0.0.0.0", "--public-url", "HTTPS://cairn.example.test"],
    ):
        assert serve_says(*args) == [], args
    assert len(serve_says.listened) == 6
