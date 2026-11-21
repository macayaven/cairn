"""One read of related pages at a time in a server process, over REST, MCP and the web view
alike (cairn.web.turns.RELATED_READS). A read that finds the turn taken waits on the event loop,
holding no worker thread and no connection, and goes on when the read before it ends, however
that ended; it is never refused for waiting. Page views are not behind it. A caller has one
read in that line at a time: its further reads wait behind its own first, so a read waits
behind at most one read of each other caller.

Threads meet at events; nothing sleeps to let something happen, and every wait is bounded.
The app runs in process over a real store in a temporary file; made-up names."""

import asyncio
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
from cairn.web.turns import RELATED_READS, RelatedReads

BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
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


def test_the_related_page_and_the_explorers_view_wait_on_the_loop_too(web, monkeypatch):
    """The web view's two reads of related pages (cairn.web.ui.memory_pages and
    cairn.web.graph_routes) take the same turn: while the explorer's view of a page's related
    pages is held, the related pages link and a second view of the explorer wait for it on the
    event loop, holding no worker thread, and the page itself is shown meanwhile."""
    store, key, asked = web
    inside, go_on, first = threading.Event(), threading.Event(), []
    whole = relating.related

    def held(*arguments):
        if not first:
            first.append(threading.current_thread().name)
            inside.set()
            assert go_on.wait(WAIT)
        return whole(*arguments)

    monkeypatch.setattr(relating, "related", held)
    answers: dict[str, int] = {}
    app = create_app(store, public_url=BASE, graph_explorer=True)
    with TestClient(app, base_url=BASE) as client:
        assert client.post("/login", data={"key": key}, headers=SAME_ORIGIN).status_code == 200

        def shown(name: str, path: str) -> threading.Thread:
            def get() -> None:
                answers[name] = client.get(path).status_code

            thread = threading.Thread(target=get, daemon=True)
            thread.start()
            return thread

        threads = [shown("explorer", "/b/ops/graph?focus=related%3Adeploy")]
        assert inside.wait(WAIT)
        threads.append(shown("link", "/b/ops/p/deploy/related"))
        until(lambda: RELATED_READS.waiting == 1, "the related pages link waiting its turn")
        threads.append(shown("again", "/b/ops/graph?focus=related%3Aretention"))
        until(lambda: RELATED_READS.waiting == 2, "the explorer's second view waiting")
        assert asked.by == first and RELATED_READS.held == 1  # neither waiting holds a thread
        assert client.get("/b/ops/p/deploy").status_code == 200  # the page, meanwhile
        assert answers == {}
        go_on.set()
        for thread in threads:
            thread.join(WAIT)
            assert not thread.is_alive()
    assert answers == {"explorer": 200, "link": 200, "again": 200}
    assert len(asked.by) == 3 and RELATED_READS.held == 0 and RELATED_READS.waiting == 0


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
    assert RELATED_READS.held == 0 and RELATED_READS.waiting == 0 and RELATED_READS.callers == 0


def test_the_contract_says_a_related_read_waits_its_turn_and_is_never_refused_for_it():
    said = " ".join(server.__doc__.split())
    assert RELATED_READS.n == 1
    assert (
        "One read of related pages at a time in a server process, for every workspace it"
        " serves, over MCP, REST and the web view alike: another waits for it holding no"
        " thread and no connection and is answered in its turn, however long that takes; none"
        " is refused for waiting. A caller (a key, an agent, or a person whichever session they"
        " use) has one read in that line at a time, its others waiting behind it to enter: that"
        " rule governs entry to the line. So a read in the line waits behind at most one read of"
        " each other caller; one still waiting behind its own caller's can see two reads of"
        " another caller go first, and more when more of its caller's are ahead of it." in said
    )


# The turn of each caller, before the process's (RelatedReads), on its own event loop


def run(main):
    return asyncio.run(asyncio.wait_for(main(), WAIT))


async def settled(reads: RelatedReads, waiting: int) -> None:
    for _ in range(int(WAIT / 0.005)):
        if reads.waiting == waiting:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"{reads.waiting} waiting, not {waiting}")


def test_a_callers_further_reads_wait_behind_its_own_first():
    """Eight reads of one caller and then one of another, while a read is under way: the
    other caller's read goes second, after the first caller's first, and the first caller's
    others after it, in the order they asked. A read in the process's line waits behind at
    most one read of each other caller."""
    reads, order = RelatedReads(), []

    async def read(caller: str, n: int) -> None:
        async with reads.turn(caller):
            order.append((caller, n))
            await asyncio.sleep(0)

    async def main() -> None:
        async with reads.turn("someone else"):  # a read under way
            tasks = []
            for n in range(8):
                tasks.append(asyncio.create_task(read("hog", n)))
                await settled(reads, n + 1)
            tasks.append(asyncio.create_task(read("calm", 0)))
            await settled(reads, 9)
            assert reads.held == 1 and reads.callers == 3
        await asyncio.gather(*tasks)

    run(main)
    assert order == [("hog", 0), ("calm", 0), *(("hog", n) for n in range(1, 8))]
    assert (reads.held, reads.waiting, reads.callers) == (0, 0, 0)


def test_a_read_waiting_behind_its_own_callers_can_see_two_reads_of_another_go_first():
    """While a read is under way: Brook's first read waits in the process's line, then
    Ada's first behind it; Ada's second and third wait behind Ada's first to enter the line,
    and Brook's second and third behind Brook's first. Ada's second was asked before any of
    Brook's began, and two of Brook's go first: Brook's second enters the line when Brook's
    first ends, before Ada's first has ended and let Ada's second in. Ada's third, with two
    of Ada's ahead of it, sees three of Brook's go first."""
    reads, order = RelatedReads(), []

    async def read(caller: str, n: int) -> None:
        async with reads.turn(caller):
            order.append((caller, n))
            await asyncio.sleep(0)

    async def main() -> None:
        async with reads.turn("someone else"):  # a read under way
            tasks = []
            for waiting, (caller, n) in enumerate(
                [("brook", 1), ("ada", 1), ("ada", 2), ("ada", 3), ("brook", 2), ("brook", 3)], 1
            ):
                tasks.append(asyncio.create_task(read(caller, n)))
                await settled(reads, waiting)
        await asyncio.gather(*tasks)

    run(main)
    assert order == [("brook", 1), ("ada", 1), ("brook", 2), ("ada", 2), ("brook", 3), ("ada", 3)]
    assert (reads.held, reads.waiting, reads.callers) == (0, 0, 0)


@pytest.mark.parametrize("where", ["behind its own first", "in the process's line"])
def test_a_read_cancelled_while_it_waits_gives_both_turns_back(where):
    """A request cancelled while it waits, in its caller's line or in the process's: it
    leaves its line, the reads behind it go on in order, and when all is done nothing is
    held, nobody waits and no caller is remembered."""
    reads, order = RelatedReads(), []

    async def read(caller: str, n: int) -> None:
        async with reads.turn(caller):
            order.append((caller, n))

    async def main() -> None:
        async with reads.turn("someone else"):
            first = asyncio.create_task(read("hog", 0))
            await settled(reads, 1)
            second = asyncio.create_task(read("hog", 1))
            await settled(reads, 2)
            other = asyncio.create_task(read("calm", 0))
            await settled(reads, 3)
            cancelled = second if where == "behind its own first" else other
            cancelled.cancel()
            await settled(reads, 2)
        for task in (first, second, other):
            try:
                await task
            except asyncio.CancelledError:
                assert task is cancelled

    run(main)
    assert ("hog", 1) not in order if where == "behind its own first" else ("calm", 0) not in order
    assert (reads.held, reads.waiting, reads.callers) == (0, 0, 0)


def test_a_read_that_raises_gives_both_turns_back_and_its_callers_next_read_goes_on():
    reads, order = RelatedReads(), []

    async def read(caller: str, n: int, fails: bool = False) -> None:
        async with reads.turn(caller):
            order.append((caller, n))
            if fails:
                raise sqlite3.OperationalError("disk I/O error")

    async def main() -> None:
        with pytest.raises(sqlite3.OperationalError):
            await read("hog", 0, fails=True)
        assert (reads.held, reads.waiting, reads.callers) == (0, 0, 0)
        await read("hog", 1)

    run(main)
    assert order == [("hog", 0), ("hog", 1)]
    assert (reads.held, reads.waiting, reads.callers) == (0, 0, 0)


def test_a_read_whose_client_goes_away_while_it_waits_gives_both_turns_back(web):
    """Over REST, a request whose client has disconnected while it waits (the server says so
    in what the request receives): when it ends, both its turns are given back. Starlette does not
    cancel a handler whose client went, so the read is done in its turn and its answer goes
    nowhere; it holds the caller's own line only, as any of its reads does."""
    store, key, _asked = web
    app = create_app(store, public_url=BASE)
    sent: list[dict] = []

    async def gone() -> dict:
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "server": ("127.0.0.1", 8765),
        "client": ("127.0.0.1", 50000),
        "root_path": "",
        "path": "/api/projects/ops/pages/deploy/related",
        "raw_path": b"/api/projects/ops/pages/deploy/related",
        "query_string": b"",
        "headers": [(b"host", b"127.0.0.1:8765"), (b"x-api-key", key.encode())],
    }

    async def main() -> None:
        async with RELATED_READS.turn("someone else"):
            asked = asyncio.create_task(app(scope, gone, send))
            await settled(RELATED_READS, 1)
        await asked
        assert (RELATED_READS.held, RELATED_READS.waiting, RELATED_READS.callers) == (0, 0, 0)

    run(main)
    assert (RELATED_READS.held, RELATED_READS.waiting, RELATED_READS.callers) == (0, 0, 0)
    assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [200]


def test_a_caller_is_the_actor_a_request_is_admitted_as(web, monkeypatch):
    """Who a read waits behind is told by the actor its request acts as, as the audit log
    records it: a key by its id (a session opened with it is the same caller), an agent by
    its grant, a person by their id whichever session. Two keys are two callers."""
    store, key, _asked = web
    other = store.create_key("desk")
    callers: list[object] = []
    taken = RELATED_READS.turn

    def recorded(caller=None):
        callers.append(caller)
        return taken(caller)

    monkeypatch.setattr(RELATED_READS, "turn", recorded)
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        assert related_over_rest(client, key) == 200
        assert call(client, key, "related", project="ops", slug="deploy")["isError"] is False
        assert related_over_rest(client, other) == 200
        assert client.post("/login", data={"key": key}, headers=SAME_ORIGIN).status_code == 200
        assert client.get("/b/ops/p/deploy/related").status_code == 200
    assert len(callers) == 4 and None not in callers
    assert callers[0] == callers[1] == callers[3] != callers[2]
