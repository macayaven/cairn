"""G1 item 4: a server process compiles at most N notes at a time (cairn.web.turns.Compiles;
`serve --compiles N`, 2 by default). The others wait their turn in arrival order, on the event
loop, holding no thread and no lock of the store, and are answered when their own compile is
done. Reads and everything else that is not a compile are never behind them.

Nothing here sleeps to let something happen: a gate the test controls blocks each compile on
an event, and the tests wait on what the gate and the turns say, each wait bounded."""

import asyncio
import threading
from collections.abc import Callable
from typing import ClassVar

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

from cairn import server
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.server import create_app
from cairn.sources import KINDS, NoteDraft, Pulled, SourceKind, run_source
from cairn.store import Store
from cairn.web import cli
from cairn.web.auth import Accounts
from cairn.web.filing import Filer
from cairn.web.turns import COMPILES, Compiles
from wait_helpers import poll_until_async, wait_for_event

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
WAIT = 20.0  # seconds any one wait may take before the test fails


async def until_async(done: Callable[[], bool], what: str) -> None:
    async def ready():
        return done()

    await poll_until_async(ready, what, WAIT, every=0.001)


def until(done: Callable[[], bool], what: str) -> None:
    """Wait, a bounded time, for `done()`: a look every few milliseconds, never a sleep that
    decides the outcome."""
    pause = threading.Event()
    for _ in range(int(WAIT / 0.005)):
        if done():
            return
        pause.wait(0.005)
    raise AssertionError(f"waited {WAIT} s for {what}")


class HeldGate:
    """The lint gate, held at its door until `release` is set: it counts who is inside, the
    most at once, and the order in which notes came in (by their first claim's text)."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.inside = 0
        self.most = 0
        self.order: list[str] = []

    def __call__(self, candidate, spans):
        with self.lock:
            self.inside += 1
            self.most = max(self.most, self.inside)
            self.order.append(candidate.claims[-1].text if candidate.claims else "")
        try:
            assert self.release.wait(WAIT), "the test never released the gate"
            return lint_gate(candidate, spans)
        finally:
            with self.lock:
                self.inside -= 1


# The turns


def test_the_default_is_two_and_the_number_is_a_whole_number_of_one_or_more():
    assert COMPILES == 2 and Compiles().n == 2
    for wrong in (0, -1, 1.5, "2", True, None):
        with pytest.raises(ValueError, match="--compiles"):
            Compiles(wrong)


def test_turns_are_given_in_the_order_they_were_asked_and_never_more_than_n():
    async def scenario() -> list[int]:
        turns, inside, most, order = Compiles(2), [0], [0], []
        gates = [asyncio.Event() for _ in range(6)]

        asked = [asyncio.Event() for _ in range(6)]
        finished = [asyncio.Event() for _ in range(6)]

        async def one(i: int) -> None:
            asked[i].set()
            async with turns.turn():
                inside[0] += 1
                most[0] = max(most[0], inside[0])
                order.append(i)
                await gates[i].wait()
                inside[0] -= 1
            finished[i].set()

        tasks = []
        for i in range(6):
            tasks.append(asyncio.create_task(one(i)))
            await wait_for_event(asked[i], "the caller asked for its turn", WAIT)
        await until_async(lambda: turns.waiting == 4, "four waiting")
        assert order == [0, 1] and turns.held == 2
        for i in (1, 0, 2, 3, 4, 5):  # out of order: the next in line still goes first
            gates[i].set()
            await wait_for_event(finished[i], "the turn finished", WAIT)
        await asyncio.gather(*tasks)
        assert most[0] == 2 and turns.held == 0 and turns.waiting == 0
        return order

    assert asyncio.run(scenario()) == [0, 1, 2, 3, 4, 5]


def test_a_waiter_cancelled_gives_up_its_place_and_a_turn_handed_to_it_goes_on():
    async def scenario() -> None:
        turns = Compiles(1)
        held, entered = asyncio.Event(), asyncio.Event()

        async def holder() -> None:
            async with turns.turn():
                entered.set()
                await held.wait()

        first = asyncio.create_task(holder())
        await wait_for_event(entered, "first turn held", WAIT)
        waiter = asyncio.create_task(turns.turn().__aenter__())
        await until_async(lambda: turns.waiting == 1, "the cancelled turn queued")
        assert turns.waiting == 1
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert turns.waiting == 0
        late = asyncio.create_task(holder())
        await until_async(lambda: turns.waiting == 1, "the next turn queued")
        held.set()
        await asyncio.gather(first, late)
        assert turns.held == 0

    asyncio.run(scenario())


# Through the app


@pytest.fixture
def web(tmp_path):
    store = Store(tmp_path / "web.db")
    key = store.create_key("laptop")
    store.create_project("ops")
    gate = HeldGate()
    yield store, key, gate
    gate.release.set()
    store.close()


def post_note(client: TestClient, key: str, n: int, answers: dict[int, int]) -> threading.Thread:
    def send() -> None:
        text = f"# Topic {n}\n\nLine {n} of the run.\n"
        answers[n] = client.post(
            "/api/projects/ops/notes", headers={"x-api-key": key}, json={"text": text}
        ).status_code

    thread = threading.Thread(target=send, daemon=True)
    thread.start()
    return thread


def test_eight_notes_at_once_compile_two_at_a_time_and_reads_are_answered_meanwhile(web):
    store, key, gate = web
    app = create_app(store, public_url=BASE, gate=gate)
    turns = app.state.compiles
    answers: dict[int, int] = {}
    with TestClient(app, base_url=BASE) as client:
        threads = []
        for n in range(8):  # one at a time, so the order they came in is known
            threads.append(post_note(client, key, n, answers))
            until(lambda n=n: gate.inside + turns.waiting == min(n + 1, 8), f"note {n} in line")
        until(lambda: gate.inside == 2 and turns.waiting == 6, "two inside and six waiting")
        # Every turn is held: a read, /healthz and a page of the UI are answered all the same.
        assert client.get("/api/projects", headers={"x-api-key": key}).status_code == 200
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/projects/ops/pages", headers={"x-api-key": key}).status_code == 200
        signed_in = client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
        assert signed_in.status_code == 200 and client.get("/b/ops").status_code == 200
        assert answers == {} and gate.inside == 2
        assert sorted(gate.order) == ["Line 0 of the run.", "Line 1 of the run."]  # first come
        gate.release.set()
        for thread in threads:
            thread.join(WAIT)
    assert answers == dict.fromkeys(range(8), 201)
    assert gate.most == 2
    assert turns.held == 0 and turns.waiting == 0


def test_with_one_turn_notes_compile_in_the_order_they_came(web):
    store, key, gate = web
    app = create_app(store, public_url=BASE, gate=gate, compiles=1)
    turns = app.state.compiles
    answers: dict[int, int] = {}
    with TestClient(app, base_url=BASE) as client:
        threads = []
        for n in range(5):
            threads.append(post_note(client, key, n, answers))
            until(lambda n=n: gate.inside + turns.waiting == n + 1, f"note {n} in line")
        gate.release.set()
        for thread in threads:
            thread.join(WAIT)
    assert answers == dict.fromkeys(range(5), 201)
    assert gate.order == [f"Line {n} of the run." for n in range(5)] and gate.most == 1


def test_the_mcp_tool_and_the_composer_take_a_turn_and_reads_go_by(web):
    store, key, gate = web
    app = create_app(store, public_url=BASE, gate=gate, compiles=1)
    turns = app.state.compiles
    answers: dict[str, object] = {}
    headers = {**MCP_HEADERS, "x-api-key": key}

    def call(name: str, **arguments) -> dict:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
        body["params"] = {"name": name, "arguments": arguments}
        return client.post("/mcp", headers=headers, json=body).json()["result"]

    with TestClient(app, base_url=BASE) as client:
        client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
        first = post_note(client, key, 0, {})
        until(lambda: gate.inside == 1, "the first note inside")

        def by_mcp() -> None:
            answers["mcp"] = call("note", project="ops", text="# MCP\n\nBy the tool.\n")

        def by_form() -> None:
            form = {"text": "# Form\n\nBy the composer.\n"}
            answers["form"] = client.post("/b/ops/notes", data=form, headers=SAME_ORIGIN)

        others = [threading.Thread(target=by_mcp), threading.Thread(target=by_form)]
        for thread in others:
            thread.start()
        until(lambda: turns.waiting == 2, "the tool's note and the form's waiting")
        assert call("projects")["isError"] is False  # a tool that reads goes by
        assert client.get("/b/ops").status_code == 200  # so does a page of the UI
        assert gate.inside == 1 and answers == {}
        gate.release.set()
        for thread in (first, *others):
            thread.join(WAIT)
    assert answers["mcp"]["isError"] is False
    assert answers["form"].status_code == 200  # the composer lands on the page it saved
    assert gate.most == 1


class Feed:
    answers: ClassVar[list[Pulled]] = []

    def __init__(self, name: str) -> None:
        self.name = name

    async def pull(self, cursor):
        return Feed.answers.pop(0)


def test_a_sources_run_waits_for_a_turn(tmp_path, monkeypatch):
    def _open(name, config, **options):
        return Feed(name)

    monkeypatch.setitem(KINDS, "feed", SourceKind(check=dict, open=_open, via="feed"))
    store = Store(tmp_path / "cairn.db")
    try:
        store.create_project("ops")
        store.add_source("ops-feed", "feed", {"projects": ["ops"]})
        draft = NoteDraft("ops", "Feed", "One line.\n", source_ref="feed:1", label="feed")
        Feed.answers = [Pulled(drafts=[draft], cursor="c1")]

        async def scenario():
            turns = Compiles(1)
            release, entered = asyncio.Event(), asyncio.Event()

            async def holder() -> None:
                async with turns.turn():
                    entered.set()
                    await release.wait()

            held = asyncio.create_task(holder())
            await wait_for_event(entered, "the other turn held", WAIT)
            run = asyncio.create_task(
                run_source(store, "ops-feed", http=httpx.AsyncClient(), env={}, turn=turns.turn)
            )
            await until_async(lambda: turns.waiting == 1, "the run's note waiting")
            assert not run.done() and store.list_notes(store.get_project("ops").id) == []
            release.set()
            await held
            return await run

        report = asyncio.run(scenario())
        assert report.saved == 1
    finally:
        store.close()


def test_the_filer_takes_a_turn_like_any_note(tmp_path):
    store = Store(tmp_path / "cairn.db")
    try:
        project = store.create_project("ops")
        note, _ = store.add_note(project.id, "# Waiting\n\nIt waits.\n")
        turns = Compiles(1)
        filer = Filer(Accounts(store, lint_gate), 3600, turns)

        async def scenario():
            release, entered = asyncio.Event(), asyncio.Event()

            async def holder() -> None:
                async with turns.turn():
                    entered.set()
                    await release.wait()

            held = asyncio.create_task(holder())
            await wait_for_event(entered, "the other turn held", WAIT)
            passing = asyncio.create_task(filer._pass(store.waiting_notes()))
            await until_async(lambda: turns.waiting == 1, "the filer's note waiting")
            assert store.list_pages(project.id) == []
            release.set()
            await held
            await passing

        asyncio.run(scenario())
        assert [page.slug for page in store.list_pages(project.id)] == ["waiting"]
        assert store.waiting_notes() == [] and note.id
        app = create_app(store, public_url=BASE, file_every=60, compiles=3)
        assert app.state.filer._compiles is app.state.compiles  # the app's filer takes its turns
    finally:
        store.close()


def test_serve_takes_the_number_and_refuses_one_that_is_not(tmp_path, monkeypatch, capsys):
    made = []
    real = cli.create_app

    def create(*args, **kwargs):
        app = real(*args, **kwargs)
        made.append(app.state.compiles.n)
        return app

    monkeypatch.setattr(cli, "create_app", create)
    monkeypatch.setattr(uvicorn, "run", lambda app, **_: None)
    db = str(tmp_path / "cairn.db")
    assert server.main(["serve", "--db", db, "--no-tidy"]) == 0
    assert server.main(["serve", "--db", db, "--no-tidy", "--compiles", "5"]) == 0
    assert made == [2, 5]
    for wrong in ("0", "-1", "two", "1.5"):
        with pytest.raises(SystemExit) as exited:
            server.main(["serve", "--db", db, "--no-tidy", "--compiles", wrong])
        assert exited.value.code == 2
    assert "--compiles" in capsys.readouterr().err


def test_a_waiting_note_holds_no_lock_of_the_store(web):
    """While a note waits for its turn, another connection writes to the store at once."""
    store, key, gate = web
    app = create_app(store, public_url=BASE, gate=gate, compiles=1)
    turns = app.state.compiles
    with TestClient(app, base_url=BASE) as client:
        first = post_note(client, key, 0, {})
        until(lambda: gate.inside == 1, "the first note inside")
        second = post_note(client, key, 1, {})
        until(lambda: turns.waiting == 1, "the second note waiting")
        Memory(store, lint_gate).store.create_project("elsewhere")  # a write, answered at once
        gate.release.set()
        first.join(WAIT)
        second.join(WAIT)
    assert store.get_project("elsewhere") is not None
