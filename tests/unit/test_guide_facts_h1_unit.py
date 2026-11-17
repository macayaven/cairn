"""Sentences of src/cairn/guides/run.md and security.md about H1's hardening, each pinned
here so the guides cannot go false unnoticed: the guide's sentence is in each test's
docstring, each test checks it is still in the guide (read as /docs/<name> reads it, its
markers filled with the software's name: guide_source), then makes it happen. The command line
runs in this process (uvicorn.run recorded, never run) or, for file modes, in a process of
its own under a umask of 022. Every name is made up."""

import asyncio
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn
from guide_source import guide_source
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store
from cairn.web import cli

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
RUN = guide_source("run")  # as /docs/run reads: markers filled with the software's name
SECURITY = guide_source("security")


def one_line(text: str) -> str:
    return " ".join(text.split())


def said(guide: str, sentence: str) -> None:
    assert one_line(sentence) in one_line(guide), sentence


def mode(path: Path | str) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


@pytest.fixture
def listened(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    return calls


@pytest.fixture
def open_folder():
    """A folder under /tmp, mode 0755: others may enter it and every folder above it."""
    folder = Path(tempfile.mkdtemp(prefix="cairn-h1-", dir="/tmp"))
    os.chmod(folder, 0o755)
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_a_command_that_makes_the_database_makes_it_private(tmp_path):
    """run.md, "Where your data lives": "A command that makes the database makes it readable
    by its owner only (mode 0600), and its two working files with it, and makes each folder
    it needs for it 0700.\""""
    said(
        RUN,
        "A command that makes the database makes it readable by its owner only (mode 0600),"
        " and its two working files with it, and makes each folder it needs for it 0700.",
    )
    db = tmp_path / "data" / "cairn.db"
    made = subprocess.run(
        [sys.executable, "-m", "cairn.server", "key-create", "--db", str(db), "--label",
         "laptop", "--out", str(tmp_path / "laptop.env")],
        capture_output=True, text=True, umask=0o022,
    )  # fmt: skip
    assert made.returncode == 0, made.stderr
    assert mode(db.parent) == 0o700 and mode(db) == 0o600
    store = Store(db)
    try:
        store.create_project("ops", "")
        assert mode(f"{db}-wal") == 0o600 and mode(f"{db}-shm") == 0o600
    finally:
        store.close()


def test_a_database_others_can_read_is_named_and_left(open_folder, listened, capsys):
    """run.md: "A database that others on the machine can read is left as it is: each
    command, and `serve` when it starts, says so in one line that names the file's mode and
    the `chmod 600` that makes it private.\""""
    said(
        RUN,
        "A database that others on the machine can read is left as it is: each command, and"
        " `serve` when it starts, says so in one line that names the file's mode and the"
        " `chmod 600` that makes it private.",
    )
    db = open_folder / "cairn.db"
    Store(db).close()
    os.chmod(db, 0o644)
    for args in (["usage", "--account", "acct_default"], ["serve"]):
        assert cli.main([args[0], "--db", str(db), *args[1:]]) == 0
        (line,) = capsys.readouterr().err.splitlines()
        assert "0644" in line and f"chmod 600 {db}" in line
    assert mode(db) == 0o644 and len(listened) == 1


def key_of(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip().partition("=")[2]


def make_key(db: Path, out: Path, *extra: str) -> str:
    assert cli.main(["key-create", "--db", str(db), "--label", out.stem, "--out", str(out),
                     *extra]) == 0  # fmt: skip
    return key_of(out)


@pytest.fixture
def workspace(tmp_path) -> Path:
    db = tmp_path / "cairn.db"
    store = Store(db)
    for name in ("alpha", "beta"):
        store.create_project(name, "")
    store.close()
    return db


def test_a_key_made_with_no_option_reaches_everything(workspace, tmp_path, capsys):
    """run.md, "Make a key": "A key made this way can read and write every project, export the
    workspace, and open a web session.\""""
    said(
        RUN,
        "A key made this way can read and write every project, export the workspace, and open a"
        " web session.",
    )
    key = make_key(workspace, tmp_path / "laptop.env")
    store = Store(workspace)
    try:
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            h = {"x-api-key": key}
            assert len(client.get("/api/projects", headers=h).json()["projects"]) == 2
            for project in ("alpha", "beta"):
                note = {"text": f"# {project}\n\nNote 1.\n"}
                assert (
                    client.post(f"/api/projects/{project}/notes", headers=h, json=note).status_code
                    == 201
                )
            assert client.get("/api/account/export", headers=h).status_code == 200
            login = client.post(
                "/login", data={"key": key}, headers=SAME_ORIGIN, follow_redirects=False
            )
            assert login.status_code == 303
    finally:
        store.close()


def test_project_and_read_only_narrow_a_key(workspace, tmp_path, capsys):
    """run.md: "For an agent that needs less, add `--project NAME` once for each project it may
    reach, and `--read-only` for a key that never writes; a project the workspace does not have
    is refused, and no key is made." security.md, "Agents and keys": "One made there with
    `--project` or `--read-only` reaches only the projects it names, or only reads, and manages no
    key, exports nothing and opens no web session.\""""
    said(
        RUN,
        "For an agent that needs less, add `--project NAME` once for each project it may reach, and"
        " `--read-only` for a key that never writes; a project the workspace does not have is"
        " refused, and no key is made.",
    )
    said(
        SECURITY,
        "One made there with `--project` or `--read-only` reaches only the projects it names, or"
        " only reads, and manages no key, exports nothing and opens no web session.",
    )
    one = make_key(workspace, tmp_path / "one.env", "--project", "alpha")
    reader = make_key(workspace, tmp_path / "reader.env", "--read-only")
    refused = tmp_path / "refused.env"
    args = ["--db", str(workspace), "--label", "x", "--out", str(refused), "--project", "gamma"]
    assert cli.main(["key-create", *args]) == 1 and not refused.exists()
    store = Store(workspace)
    try:
        assert len(store.list_keys()) == 2
        for key in (one, reader):
            who = store.resolve_key(key)
            assert not store.narrow(who.projects, who.access).manages()
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            listed = client.get("/api/projects", headers={"x-api-key": one}).json()["projects"]
            assert [b["name"] for b in listed] == ["alpha"]
            note = {"text": "# n\n\nNote 1.\n"}
            assert client.post("/api/projects/alpha/notes", headers={"x-api-key": reader},
                               json=note).status_code == 403  # fmt: skip
            for key in (one, reader):
                h = {"x-api-key": key}
                assert client.get("/api/account/export", headers=h).status_code == 403
                login = client.post(
                    "/login", data={"key": key}, headers=SAME_ORIGIN, follow_redirects=False
                )
                assert login.status_code == 401
    finally:
        store.close()


def test_key_list_prints_what_each_key_reaches(workspace, tmp_path, capsys):
    """run.md: "`key-list --db /data/cairn.db` prints each key with what it reaches, as the
    Keys page shows it.\""""
    said(
        RUN,
        "`key-list --db /data/cairn.db` prints each key with what it reaches, as the Keys page"
        " shows it.",
    )
    make_key(workspace, tmp_path / "one.env", "--project", "beta", "--read-only")
    capsys.readouterr()
    assert cli.main(["key-list", "--db", str(workspace)]) == 0
    (line,) = capsys.readouterr().out.splitlines()
    assert line.startswith("one: Read only: beta; No access: every other project (1 you can see)")


def test_a_db_that_is_a_link_is_refused(tmp_path, capsys):
    """run.md: "A `--db` that is a symbolic link is refused.\""""
    said(RUN, "A `--db` that is a symbolic link is refused.")
    link = tmp_path / "cairn.db"
    link.symlink_to(tmp_path / "elsewhere.db")
    assert cli.main(["key-revoke", "--db", str(link), "--label", "laptop"]) == 2
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_answers_to_agents_and_scripts_are_not_cached(workspace):
    """security.md, "Agents and keys": "Every answer on the REST API and on MCP says
    `Cache-Control: no-store`, a refusal included.\""""
    said(
        SECURITY,
        "Every answer on the REST API and on MCP says `Cache-Control: no-store`, a refusal"
        " included.",
    )
    store = Store(workspace)
    key = store.create_key("ops")
    mcp = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    try:
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            answers = [
                client.get("/api/projects", headers={"x-api-key": key}),
                client.get("/api/projects"),
                client.post("/mcp", headers={**mcp, "x-api-key": key}, json=body),
                client.post("/mcp", headers=mcp, json=body),
            ]
            assert [a.status_code for a in answers] == [200, 401, 200, 401]
            assert all(a.headers.get_list("cache-control") == ["no-store"] for a in answers)
    finally:
        store.close()


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2099, 3, 1, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def test_wrong_keys_are_limited_per_client_address(workspace):
    """run.md, "Make a key": "cairn counts the wrong keys and tokens each client address
    presents, to the REST API, to MCP and to the sign-in form. Once an address has presented
    30 in a minute, a wrong key or token from it is answered 429 in place of 401 until the
    minute has passed. A correct one is never refused by this limit, from any address, so a
    script that keeps retrying a revoked key does not lock out the people and agents behind
    the same address. A request that carries no key at all never counts." security.md, "What
    cairn leaves to the operator": "cairn also limits the wrong keys and tokens each client
    address presents: once an address has presented 30 in a minute, a wrong one from it is
    answered 429 in place of 401 for the rest of that minute. A correct key or token is never
    refused by this limit, from any address.\""""
    said(
        RUN,
        "cairn counts the wrong keys and tokens each client address presents, to the REST API,"
        " to MCP and to the sign-in form. Once an address has presented 30 in a minute, a wrong"
        " key or token from it is answered 429 in place of 401 until the minute has passed. A"
        " correct one is never refused by this limit, from any address, so a script that keeps"
        " retrying a revoked key does not lock out the people and agents behind the same"
        " address. A request that carries no key at all never counts.",
    )
    said(
        SECURITY,
        "cairn also limits the wrong keys and tokens each client address presents: once an"
        " address has presented 30 in a minute, a wrong one from it is answered 429 in place of"
        " 401 for the rest of that minute. A correct key or token is never refused by this"
        " limit, from any address.",
    )
    store = Store(workspace)
    revoked = store.create_key("stale-script")
    assert store.revoke_key("stale-script")
    clock = Clock()
    mcp = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    try:
        app = create_app(store, public_url=BASE, clock=clock)
        with TestClient(app, base_url=BASE, client=("203.0.113.7", 50000)) as client:
            for _ in range(50):  # no key at all: never counted
                assert client.get("/api/projects").status_code == 401
            for n in range(10):  # wrong ones, on the REST API, on MCP and at sign-in
                assert (
                    client.get("/api/projects", headers={"x-api-key": revoked}).status_code == 401
                )
                bearer = {**mcp, "authorization": f"Bearer w-{n}"}
                assert client.post("/mcp", headers=bearer, json=body).status_code == 401
                wrong = {"key": f"wrong-{n}"}
                assert client.post("/login", data=wrong, headers=SAME_ORIGIN).status_code == 401
            assert client.get("/api/projects", headers={"x-api-key": revoked}).status_code == 429
            key = store.create_key("colleague")  # made after the limit was reached
            assert client.get("/api/projects", headers={"x-api-key": key}).status_code == 200
            assert (
                client.post("/mcp", headers={**mcp, "x-api-key": key}, json=body).status_code == 200
            )
            assert client.get("/api/projects", headers={"x-api-key": revoked}).status_code == 429
            clock.now += timedelta(seconds=60)
            assert client.get("/api/projects", headers={"x-api-key": revoked}).status_code == 401
    finally:
        store.close()


def test_behind_a_trusted_proxy_each_client_has_its_own_count(tmp_path, monkeypatch):
    """run.md, "Make a key": "Behind a reverse proxy, the address is the one `--trusted-proxy`
    lets cairn read." serve runs with uvicorn stubbed; the app is loaded as uvicorn would load
    it, with its proxy-header middleware, and wrong keys arrive through one trusted proxy for
    two clients, and from an untrusted peer that claims a client of its own."""
    said(RUN, "Behind a reverse proxy, the address is the one `--trusted-proxy` lets cairn read.")
    proxy, stranger = "10.9.8.7", "198.51.100.7"

    async def statuses(app, sends: list[tuple[str, str]]) -> list[int]:
        out = []
        for peer, forwarded in sends:
            transport = httpx.ASGITransport(app=app, client=(peer, 40000))
            async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
                headers = {"x-api-key": "wrong", "x-forwarded-for": forwarded}
                out.append((await client.get("/api/projects", headers=headers)).status_code)
        return out

    seen: list[list[int]] = []

    def run(app, **options):
        config = uvicorn.Config(app, log_config=None, **options)
        config.load()
        sends = [(proxy, "203.0.113.5")] * 31 + [(proxy, "203.0.113.6")]
        sends += [(stranger, f"203.0.113.{100 + n}") for n in range(31)]
        seen.append(asyncio.run(statuses(config.loaded_app, sends)))

    monkeypatch.setattr(uvicorn, "run", run)
    db = str(tmp_path / "cairn.db")
    assert cli.main(["serve", "--db", db, "--trusted-proxy", proxy]) == 0
    (got,) = seen
    assert got[:30] == [401] * 30 and got[30] == 429  # 203.0.113.5, through the proxy
    assert got[31] == 401  # 203.0.113.6, through the same proxy: a count of its own
    assert got[32:62] == [401] * 30 and got[62] == 429  # the stranger counts as itself


def test_a_listener_in_clear_text_says_so_and_starts(tmp_path, listened, capsys):
    """run.md, "Start it": "When cairn listens on an address that is not loopback, as the
    default command does inside the container, and `--public-url` is not https, it says at
    start, in one line of its log, that keys, tokens and session cookies travel in clear text
    on that listener unless a proxy in front of it ends TLS. It starts all the same: ... and
    behind a proxy that ends TLS, `--public-url https://...` says so and the line goes.\""""
    said(
        RUN,
        "When cairn listens on an address that is not loopback, as the default command does"
        " inside the container, and `--public-url` is not https, it says at start, in one line"
        " of its log, that keys, tokens and session cookies travel in clear text on that"
        " listener unless a proxy in front of it ends TLS. It starts all the same:",
    )
    said(RUN, "`--public-url https://...` says so and the line goes.")
    said(RUN, "published on 127.0.0.1 as above, nothing else on your network reaches it")
    assert "-p 127.0.0.1:8765:8765 -v cairn-data:/data" in RUN  # "as above": the run command
    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text(encoding="utf-8")
    default = '["serve", "--db", "/data/cairn.db", "--host", "0.0.0.0"]'
    assert f"CMD {default}" in dockerfile  # the image's default command, as the guide says
    db = str(tmp_path / "cairn.db")
    assert cli.main(["serve", "--db", db, "--host", "0.0.0.0"]) == 0
    (line,) = capsys.readouterr().err.splitlines()
    assert "API keys, tokens and session cookies travel in clear text" in line
    assert "unless a proxy in front of it ends TLS" in line
    url = "https://cairn.example.test"
    assert cli.main(["serve", "--db", db, "--host", "0.0.0.0", "--public-url", url]) == 0
    assert capsys.readouterr().err == ""
    assert len(listened) == 2
