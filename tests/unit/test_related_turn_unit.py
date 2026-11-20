"""One read of related pages at a time in a server process, over REST and MCP alike
(cairn.web.turns.RELATED_READS). A read that finds the turn taken waits on the event loop,
holding no worker thread and no connection, and goes on when the read before it ends, however
that ended; it is never refused for waiting. Page views are not behind it.

Threads meet at events; nothing sleeps to let something happen, and every wait is bounded.
The app runs in process over a real store in a temporary file; made-up names."""

import sqlite3
import threading
from collections.abc import Callable

import pytest
from starlette.testclient import TestClient

from cairn import server
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.server import create_app
from cairn.store import Store, relating
from cairn.web.turns import RELATED_READS

BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
WAIT = 20.0  # seconds any one wait may take before the test fails


def until(done: Callable[[], bool], what: str) -> None:
    pause = threading.Event()
    for _ in range(int(WAIT / 0.005)):
        if done():
            return
        pause.wait(0.005)
    raise AssertionError(f"waited {WAIT} s for {what}")


class Asked:
    """The store's own turn (ConnectionState.related_turn), counting the threads that ask."""

    def __init__(self, turn) -> None:
        self.turn, self.by, self._lock = turn, [], threading.Lock()

    def __enter__(self):
        with self._lock:
            self.by.append(threading.current_thread().name)
        return self.turn.__enter__()

    def __exit__(self, *raised):
        return self.turn.__exit__(*raised)


@pytest.fixture
def web(tmp_path, monkeypatch):
    store = Store(tmp_path / "turn.db")
    key = store.create_key("laptop")
    store.create_project("ops")
    memory = Memory(store, lint_gate)
    for title, text in (
        ("Deploy", "Run `--audit-keep` at night.\n"),
        ("Retention", "`--audit-keep` says how many; see Deploy.\n"),
    ):
        memory.note("ops", text, title=title, topic=title)
    asked = Asked(store._state.related_turn)
    monkeypatch.setattr(store._state, "related_turn", asked)
    yield store, key, asked
    store.close()


def related_over_rest(client: TestClient, key: str) -> int:
    path = "/api/projects/ops/pages/deploy/related"
    return client.get(path, headers={"x-api-key": key}).status_code


def call(client: TestClient, key: str, name: str, **arguments) -> dict:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
    body["params"] = {"name": name, "arguments": arguments}
    headers = {**MCP_HEADERS, "x-api-key": key}
    return client.post("/mcp", headers=headers, json=body).json()["result"]


def test_a_second_related_read_waits_on_the_loop_and_page_views_go_by(web, monkeypatch):
    store, key, asked = web
    inside, go_on, first = threading.Event(), threading.Event(), []
    whole = relating.related

    def held(*arguments):
        if not first:  # the first read in holds its turn until the test says
            first.append(threading.current_thread().name)
            inside.set()
            assert go_on.wait(WAIT)
        return whole(*arguments)

    monkeypatch.setattr(relating, "related", held)
    answers: dict[str, object] = {}
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:

        def by_rest() -> None:
            answers["rest"] = related_over_rest(client, key)

        def by_mcp() -> None:
            answers["mcp"] = call(client, key, "related", project="ops", slug="deploy")

        one = threading.Thread(target=by_rest, daemon=True)
        one.start()
        assert inside.wait(WAIT)
        other = threading.Thread(target=by_mcp, daemon=True)
        other.start()
        until(lambda: RELATED_READS.waiting == 1, "the tool's read waiting its turn")
        # The waiting read holds no worker thread: it has not asked the store for its turn.
        assert asked.by == first and RELATED_READS.held == 1
        page = client.get("/api/projects/ops/pages/deploy", headers={"x-api-key": key})
        assert page.status_code == 200
        assert call(client, key, "page", project="ops", slug="deploy")["isError"] is False
        assert answers == {} and RELATED_READS.waiting == 1
        go_on.set()
        for thread in (one, other):
            thread.join(WAIT)
            assert not thread.is_alive()
    assert answers["rest"] == 200 and answers["mcp"]["isError"] is False
    assert len(asked.by) == 2 and RELATED_READS.held == 0 and RELATED_READS.waiting == 0


def test_a_related_read_that_fails_gives_the_turn_back(web, monkeypatch):
    store, key, asked = web

    def failing(*arguments):
        raise sqlite3.OperationalError("disk I/O error")

    with TestClient(
        create_app(store, public_url=BASE), base_url=BASE, raise_server_exceptions=False
    ) as client:
        monkeypatch.setattr(relating, "related", failing)
        assert related_over_rest(client, key) == 500
        assert RELATED_READS.held == 0
        assert call(client, key, "related", project="ops", slug="deploy")["isError"] is True
        assert RELATED_READS.held == 0 and not asked.turn.locked()  # the store's turn too
        monkeypatch.undo()
        missing = client.get("/api/projects/ops/pages/nowhere/related", headers={"x-api-key": key})
        assert missing.status_code == 404 and RELATED_READS.held == 0
        refused = client.get(
            "/api/projects/ops/pages/deploy/related?limit=0", headers={"x-api-key": key}
        )
        assert refused.status_code == 400 and RELATED_READS.held == 0
        assert related_over_rest(client, key) == 200
        assert call(client, key, "related", project="ops", slug="deploy")["isError"] is False
    assert RELATED_READS.held == 0 and RELATED_READS.waiting == 0


def test_the_contract_says_a_related_read_waits_its_turn_and_is_never_refused_for_it():
    said = " ".join(server.__doc__.split())
    assert RELATED_READS.n == 1
    assert (
        "One read of related pages at a time in a server process, over MCP and REST alike: another"
        " waits for it holding no connection and is answered in its turn, however long that"
        " takes; none is refused for waiting." in said
    )
