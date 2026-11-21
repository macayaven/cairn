"""R1 acceptance tests: a store that recovers from a failed commit, a health check that tells
the truth, and one server per database.

The contract, in plain words:

1. A failed transaction leaves nothing behind. Beginning, the body and the commit fail under
   one handler: whatever fails, the transaction is rolled back before the error goes on, the
   connection is not left inside a transaction, nothing of the failed write is kept, and the
   next read and the next write work as if nothing had happened once the cause is gone. If
   the rollback itself fails, the store is unusable and says so: that call and every later
   one raise cairn.store.StoreUnusable at once (never SQLite's "cannot start a transaction
   within a transaction"), the broken connection lets go of the file, and /healthz answers
   503. A write that fails because the disk is full or the file cannot be written raises
   cairn.store.CannotWrite; over REST it is a 503 and over MCP a tool error, each with one
   fixed sentence (cairn.memory.UNWRITABLE) that names no path and no SQL, and the audit
   log holds nothing for it. An unusable store is a 503 with cairn.memory.OUT_OF_SERVICE.
2. /healthz says whether this server can serve. 200 and exactly `{"status":"ok"}` when a
   bounded read of the store works and finds the schema this code knows; 503 and exactly
   `{"status":"unavailable"}` when the store is closed, unusable, cannot be read or holds
   another schema version. It writes nothing. It waits for the store's lock for a short
   bound only: when another thread holds the lock longer (compact does, on purpose), it
   answers 200 without waiting for it. Behind the wall and under a brand it answers the same
   to a visitor who is not signed in.
3. One `serve` per database. A second `serve` on a database a server is running on exits 2
   at once with one line on stderr (cli.ANOTHER_SERVER, naming the --db value as given and no
   other path), and changes nothing; the first server goes on. The lock is the operating
   system's: once the first server stops, or is killed, a new one starts. Other commands
   (`key-create`, `session-end`, ...) run beside a running server as before.

Made-up names only; every address is example.test.
"""

import contextlib
import os
import signal
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.identity import GitHub
from cairn.server import SignIn, create_app, main
from cairn.store import SCHEMA_VERSION, Store
from wait_helpers import poll_until

BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
OK = b'{"status":"ok"}'
UNAVAILABLE = b'{"status":"unavailable"}'
NESTED = "within a transaction"  # SQLite's sentence for a connection left in a transaction
# A note large enough to need new pages in the file (the disk-full case below).
LONG_NOTE = "# Lantern rota\n\n" + "\n\n".join(
    f"Shift {n}: the Lantern team checks the north pumps at {n % 24:02d}:00." for n in range(800)
)


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def project_names(db: Path) -> list[str]:
    """The projects the file holds, read through a connection of its own."""
    con = sqlite3.connect(db)
    try:
        return sorted(row[0] for row in con.execute("SELECT name FROM projects"))
    finally:
        con.close()


def audit_actions(store: Store) -> list[str]:
    return [event.action for event in store.list_audit(1000)]


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "cairn.db"
    store = Store(db)
    key = store.create_key("ops")
    store.create_project("alpha")
    app = create_app(store, public_url=BASE)
    client = TestClient(app, base_url=BASE, raise_server_exceptions=False)
    client.__enter__()
    try:
        yield SimpleNamespace(
            client=client, store=store, db=db, key=key, h={"x-api-key": key}, tmp=tmp_path
        )
    finally:
        client.__exit__(None, None, None)
        store.close()


def deny(*statements: str):
    """An authorizer that refuses these transaction statements ("COMMIT", "ROLLBACK")."""

    def authorizer(action, arg1, *_):
        if action == sqlite3.SQLITE_TRANSACTION and arg1 in statements:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    return authorizer


def tool(env, name: str, /, **arguments) -> dict:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}  # fmt: skip
    r = env.client.post("/mcp", headers={**MCP_HEADERS, **env.h}, json=body)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def tool_error(result: dict) -> str:
    """What a tool error says, after the SDK's own "Error executing tool NAME: "."""
    text = result["content"][0]["text"]
    return text.partition(": ")[2] if text.startswith("Error executing tool ") else text


def plain(sentence: str, env) -> None:
    """A fixed sentence for people: no path, no SQL, nothing SQLite said."""
    assert str(env.tmp) not in sentence and "cairn.db" not in sentence
    for word in ("sqlite", "SQLITE", "INSERT", "COMMIT", "ROLLBACK", "transaction", "Traceback"):
        assert word not in sentence, word


# 1. A failed commit


def test_a_commit_that_fails_keeps_nothing_and_the_store_goes_on(env):
    store, con = env.store, env.store._con
    before = audit_actions(store)
    con.set_authorizer(deny("COMMIT"))
    with pytest.raises(sqlite3.DatabaseError) as failed:
        store.create_project("beta")
    assert NESTED not in str(failed.value)
    assert con.in_transaction is False
    con.set_authorizer(None)  # the cause is gone
    assert [b.name for b in store.list_projects()] == ["alpha"]
    assert audit_actions(store) == before  # its record went with it
    assert store.create_project("gamma").name == "gamma"
    assert project_names(env.db) == ["alpha", "gamma"]  # beta was not kept
    assert env.client.get("/healthz").content == OK


def test_a_body_that_raises_keeps_nothing_and_the_store_goes_on(env):
    """The write fails half way, after its first row: the project's row is not kept either."""
    store, con = env.store, env.store._con

    def refuse_audit(action, arg1, *_):
        if action == sqlite3.SQLITE_INSERT and arg1 == "audit_events":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    con.set_authorizer(refuse_audit)
    with pytest.raises(sqlite3.DatabaseError):
        store.create_project("beta")
    assert con.in_transaction is False
    con.set_authorizer(None)
    assert [b.name for b in store.list_projects()] == ["alpha"]
    assert store.create_project("gamma").name == "gamma"
    assert project_names(env.db) == ["alpha", "gamma"]


def test_a_commit_that_fails_over_rest_then_rest_works_again(env):
    c, con = env.client, env.store._con
    con.set_authorizer(deny("COMMIT"))
    r = c.post("/api/projects", headers=env.h, json={"name": "beta"})
    con.set_authorizer(None)
    assert r.status_code >= 500
    assert NESTED not in r.text
    assert con.in_transaction is False
    r = c.get("/api/projects", headers=env.h)
    assert r.status_code == 200 and [b["name"] for b in r.json()["projects"]] == ["alpha"]
    assert c.post("/api/projects", headers=env.h, json={"name": "gamma"}).status_code == 201
    r = c.post("/api/projects/gamma/notes", headers=env.h, json={"text": "Pumps run at noon."})
    assert r.status_code == 201, r.text
    assert c.get("/healthz").content == OK
    assert project_names(env.db) == ["alpha", "gamma"]


def test_a_rollback_that_fails_makes_the_store_unusable_and_it_says_so(env):
    store, con = env.store, env.store._con
    con.set_authorizer(deny("COMMIT", "ROLLBACK"))
    with pytest.raises(Exception) as first:
        store.create_project("beta")
    with contextlib.suppress(sqlite3.Error):  # the cause is gone (a closed connection refuses)
        con.set_authorizer(None)
    with pytest.raises(Exception) as later:
        store.list_projects()
    assert NESTED not in str(later.value)

    from cairn.memory import OUT_OF_SERVICE
    from cairn.store import StoreUnusable

    assert isinstance(first.value, StoreUnusable)
    # Every later call raises the one typed error at once, whatever it is.
    for call in (
        store.list_projects,
        lambda: store.create_project("gamma"),
        lambda: store.for_account("acct_default"),
        lambda: store.resolve_key(env.key),
    ):
        with pytest.raises(StoreUnusable) as unusable:
            call()
        assert NESTED not in str(unusable.value)
    # The file holds what was committed, nothing of the failed write, and another
    # connection may write it: the broken connection holds no lock on it.
    assert project_names(env.db) == ["alpha"]
    other = sqlite3.connect(env.db, timeout=1)
    try:
        other.execute("BEGIN IMMEDIATE")
        other.execute("ROLLBACK")
    finally:
        other.close()
    h = env.client.get("/healthz")
    assert (h.status_code, h.content) == (503, UNAVAILABLE)
    r = env.client.get("/api/projects", headers=env.h)
    assert r.status_code == 503 and r.json() == {"error": OUT_OF_SERVICE}
    r = env.client.post("/api/projects", headers=env.h, json={"name": "delta"})
    assert r.status_code == 503 and r.json() == {"error": OUT_OF_SERVICE}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "projects", "arguments": {}}}  # fmt: skip
    r = env.client.post("/mcp", headers={**MCP_HEADERS, **env.h}, json=body)
    assert r.status_code == 503 and OUT_OF_SERVICE in r.text
    plain(OUT_OF_SERVICE, env)


# 1. A full disk reaches the caller as an operational error


def test_a_full_disk_is_a_503_over_rest_and_a_tool_error_over_mcp(env):
    """A genuine SQLITE_FULL: the file may not grow past the pages it has."""
    store, con, c = env.store, env.store._con, env.client
    before, actions = dump(env.db), audit_actions(store)
    pages = con.execute("PRAGMA page_count").fetchone()[0]
    con.execute(f"PRAGMA max_page_count = {pages}")
    with pytest.raises(sqlite3.OperationalError) as full:
        store.add_note(store.get_project("alpha").id, LONG_NOTE)
    r = c.post("/api/projects/alpha/notes", headers=env.h, json={"text": LONG_NOTE})
    assert r.status_code == 503, r.text

    from cairn.memory import UNWRITABLE
    from cairn.store import CannotWrite

    assert isinstance(full.value, CannotWrite)
    assert r.json() == {"error": UNWRITABLE}
    result = tool(env, "note", project="alpha", text=LONG_NOTE)
    assert result["isError"] is True and tool_error(result) == UNWRITABLE
    assert c.get("/api/projects", headers=env.h).status_code == 200  # reads go on
    assert c.get("/healthz").content == OK  # it can serve what it holds
    assert con.in_transaction is False
    assert audit_actions(store) == actions  # nothing recorded: it could not be
    assert dump(env.db) == before  # nothing kept
    plain(UNWRITABLE, env)
    con.execute("PRAGMA max_page_count = 1073741823")  # room again
    r = c.post("/api/projects/alpha/notes", headers=env.h, json={"text": LONG_NOTE})
    assert r.status_code == 201, r.text


class CommitFails:
    """The store's connection, except that the COMMIT of a write (a BEGIN IMMEDIATE) raises
    SQLite's disk-full error while `armed`, as SQLite words it (a message, no code given). A
    read's COMMIT writes nothing, so a full disk does not fail it."""

    def __init__(self, con: sqlite3.Connection) -> None:
        self._real = con
        self.armed = False
        self._writing = False

    def execute(self, sql, *args):
        statement = sql.strip().upper()
        if statement.startswith("BEGIN"):
            self._writing = statement == "BEGIN IMMEDIATE"
        if self.armed and self._writing and statement == "COMMIT":
            raise sqlite3.OperationalError("database or disk is full")
        return self._real.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_a_commit_that_fails_for_a_full_disk_is_a_503_and_nothing_is_kept(env):
    store, c = env.store, env.client
    real = store._con
    patched = CommitFails(real)
    store._con = patched
    actions = audit_actions(store)
    patched.armed = True
    r = c.post("/api/projects", headers=env.h, json={"name": "beta"})
    assert r.status_code == 503, r.text

    from cairn.memory import UNWRITABLE

    assert r.json() == {"error": UNWRITABLE}
    result = tool(env, "new_project", name="gamma")
    assert result["isError"] is True and tool_error(result) == UNWRITABLE
    r = c.post("/api/projects/alpha/notes", headers=env.h, json={"text": "Pumps run at noon."})
    assert r.status_code == 503 and r.json() == {"error": UNWRITABLE}, r.text
    assert real.in_transaction is False
    assert project_names(env.db) == ["alpha"]
    assert audit_actions(store) == actions
    patched.armed = False  # the disk has room again
    assert c.post("/api/projects", headers=env.h, json={"name": "beta"}).status_code == 201
    assert c.get("/healthz").content == OK
    assert project_names(env.db) == ["alpha", "beta"]


# 2. /healthz


def test_healthz_answers_todays_bytes_on_a_working_store_and_writes_nothing(env):
    before = dump(env.db)
    for _ in range(3):
        r = env.client.get("/healthz")
        assert (r.status_code, r.content) == (200, OK)
        assert r.headers["content-type"] == "application/json"
    assert dump(env.db) == before


def test_healthz_is_503_with_the_store_closed_underneath_the_app(env):
    env.store.close()
    r = env.client.get("/healthz")
    assert (r.status_code, r.content) == (503, UNAVAILABLE)
    assert r.headers["content-type"] == "application/json"


def test_healthz_is_503_on_a_schema_this_code_does_not_know(env):
    def stamp(version: int) -> None:
        other = sqlite3.connect(env.db)
        try:
            other.execute(f"PRAGMA user_version = {version}")
            # a step this cairn may not run on: the floor goes up with it
            other.execute("UPDATE oldest_build SET version = MAX(version, ?)", (version,))
            other.commit()
        finally:
            other.close()

    stamp(SCHEMA_VERSION + 1)
    before = dump(env.db)
    r = env.client.get("/healthz")
    assert (r.status_code, r.content) == (503, UNAVAILABLE)
    assert dump(env.db) == before
    stamp(SCHEMA_VERSION)  # read at each probe, not once
    assert env.client.get("/healthz").content == OK


def test_healthz_answers_200_while_another_thread_holds_the_store_lock(env):
    """The lock held on purpose, as compact holds it for a whole rewrite of the file: the
    probe does not wait for it, and answers 200 while it is still held."""
    held, release = threading.Event(), threading.Event()

    def hold() -> None:
        with env.store._lock:
            held.set()
            release.wait(60)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert held.wait(10)
        r = env.client.get("/healthz")
        still_held = not release.is_set()
    finally:
        release.set()
        holder.join()
    assert still_held
    assert (r.status_code, r.content) == (200, OK)


def test_healthz_behind_the_wall_and_under_a_brand_signed_out(tmp_path):
    store = Store(tmp_path / "cairn.db")
    sign_in = SignIn(
        providers=(GitHub("github-client-id", "github-client-secret"),),
        http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))),
        join="personal",
        allowed_emails=("ada@example.test",),
    )
    app = create_app(
        store, public_url=BASE, sign_in=sign_in, wall=True, brand_name="Northwind Notes"
    )
    try:
        with TestClient(app, base_url=BASE, raise_server_exceptions=False) as visitor:
            r = visitor.get("/healthz", follow_redirects=False)
            assert (r.status_code, r.content) == (200, OK)
            store.close()
            r = visitor.get("/healthz", follow_redirects=False)
            assert (r.status_code, r.content) == (503, UNAVAILABLE)
    finally:
        store.close()


# 3. One serve per database


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def serve(db: Path, port: int) -> list[str]:
    return [
        sys.executable, "-m", "cairn.server", "serve", "--db", str(db),
        "--host", "127.0.0.1", "--port", str(port), "--public-url", f"http://127.0.0.1:{port}",
    ]  # fmt: skip


class Running:
    """A real `serve` process, stopped in a finally by whoever starts it."""

    def __init__(self, db: Path) -> None:
        self.port = free_port()
        self.process = subprocess.Popen(
            serve(db, self.port), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )  # fmt: skip

    def healthz(self) -> int | None:
        try:
            return httpx.get(f"http://127.0.0.1:{self.port}/healthz", timeout=5).status_code
        except httpx.HTTPError:
            return None

    def up(self) -> "Running":
        def ready():
            assert self.process.poll() is None, "serve stopped before /healthz was ready"
            return self.healthz() == 200

        poll_until(ready, "serve did not answer /healthz", 60, every=0.1)
        return self

    def stop(self, sig: int = signal.SIGTERM) -> None:
        if self.process.poll() is None:
            self.process.send_signal(sig)
            try:
                self.process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.communicate()


def test_one_serve_per_database(tmp_path, capsys):
    db = tmp_path / "data" / "cairn.db"
    started: list[Running] = []
    try:
        first = Running(db)
        started.append(first)
        first.up()
        before = dump(db)
        try:  # a second server that starts serving never exits by itself
            second = subprocess.run(
                serve(db, free_port()), capture_output=True, text=True, timeout=60,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )  # fmt: skip
        except subprocess.TimeoutExpired:
            pytest.fail("a second serve on the same database ran instead of exiting")
        assert second.returncode == 2, second.stderr

        from cairn.web.cli import ANOTHER_SERVER

        assert second.stdout == ""
        assert second.stderr == ANOTHER_SERVER.format(db=db) + "\n"
        (line,) = second.stderr.splitlines()
        assert line.count(str(tmp_path)) == 1 and ".lock" not in line
        assert "Traceback" not in second.stderr
        assert first.healthz() == 200  # the first goes on
        assert dump(db) == before  # the second changed nothing

        # Other commands run beside the server as before.
        capsys.readouterr()
        assert main(["key-create", "--db", str(db), "--label", "ops-2",
                     "--out", str(tmp_path / "ops-2.env")]) == 0  # fmt: skip
        assert main(["session-end", "--db", str(db), "--email", "ada@example.test"]) == 0
        capsys.readouterr()
        assert first.healthz() == 200

        first.stop()  # stopped: a new server starts
        third = Running(db)
        started.append(third)
        third.up()
        third.stop(signal.SIGKILL)  # killed: what it left behind blocks nothing
        assert third.process.returncode == -signal.SIGKILL
        fourth = Running(db)
        started.append(fourth)
        fourth.up()
        r = httpx.get(
            f"http://127.0.0.1:{fourth.port}/api/projects",
            headers={"x-api-key": key_in(tmp_path / "ops-2.env")},
            timeout=5,
        )
        assert r.status_code == 200 and r.json()["projects"] == []
    finally:
        for running in started:
            running.stop()


def key_in(path: Path) -> str:
    (line,) = path.read_text(encoding="utf-8").splitlines()
    return line.partition("=")[2]
