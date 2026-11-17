"""G1: the gate's bounds. Five contracts, each through the seams a person or an agent uses.

1. Chunking a long line is linear. `cairn.spans.chunk` on one line of backticks (and on a
   heading with a long run of blanks after its title) takes time that grows with the line, not
   with its square, and returns what it returned before. 2,000,000 backticks on one line, which
   took the code before G1 minutes, are chunked here; the test bounds the ratio of the time for
   that line to the time for a line eight times shorter (linear work is about 8, quadratic about
   64; the bound is 24), never a number of seconds.

2. A note holds at most 30,000 facts (cairn.store.FACTS_MAX): numbers, dates, versions, paths,
   links, flags and pieces of code, counted as cairn.facts finds them, in one linear pass. A
   note with more is refused before anything of it is stored, like a note whose headings are
   past their bound: 400 over REST, a tool error over MCP, a source's run skips it with the
   reason "facts past their bound". The sentence gives the count and the bound and nothing of
   the note. Measured: the costliest shape (links) stored and compiled in 1.9 s at 30,000 facts,
   adding 106 MB.

3. A page holds at most 400,000 characters of claims (cairn.store.PAGE_CHARS_MAX), beside its
   2,000 claims. A note that would take its page past either is refused before it is stored
   by the same check and the same refusal (409, PAGE_FULL over REST and MCP, skipped as a full
   page by a source's run), and the sentence says which bound was met. A page already larger
   when the bound came stays as it is and takes no more notes, which its sentence says.
   Measured: a save onto a page of the densest notes measured took 1.8 to 2.6 s at 360,000 to
   432,000 characters, and 2.8 to 4.9 s at 510,000.

4. A server process compiles at most N notes at a time: 2, unless `serve --compiles N` (1 or
   more) says otherwise. The others wait their turn in the order they came, holding no store
   lock, and each is answered when its own compile is done. Reads, /healthz, sign-in and
   everything else that is not a compile are never behind them.

5. A number, version or date that an underscore of emphasis touches is read whole:
   `_2,700 more comments are left out._` holds 2,700 and is saved; it was rejected, the number
   read as 700. An underscore inside a word (max_retries_3, v1_2, a file name) reads as before.

Everything runs offline over real stores in temporary files, with made-up notes. A gate that
holds each compile until the test releases it stands in for a slow compile; nothing sleeps.
"""

import asyncio
import gc
import threading
import time
from collections.abc import Callable
from typing import ClassVar

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

import cairn.store.memory
from cairn import server
from cairn.compiler import compile_note
from cairn.facts import extract_facts
from cairn.gate import lint_gate
from cairn.memory import PAGE_FULL
from cairn.server import create_app
from cairn.sources import KINDS, NoteDraft, Pulled, SourceKind, run_source
from cairn.spans import chunk
from cairn.store import FACTS_MAX, PAGE_CHARS_MAX, PAGE_CLAIMS_MAX, Store
from cairn.web import cli

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
WAIT = 20.0  # seconds any one wait may take before a test fails; never what it asserts


def test_the_bounds():
    assert (FACTS_MAX, PAGE_CHARS_MAX, PAGE_CLAIMS_MAX) == (30_000, 400_000, 2_000)


# 1. Chunking is linear


def best_of(runs: int, work: Callable[[], object]) -> float:
    gc.collect()
    gc.disable()
    try:
        times = []
        for _ in range(runs):
            started = time.perf_counter()
            work()
            times.append(time.perf_counter() - started)
        return min(times)
    finally:
        gc.enable()


@pytest.mark.parametrize(
    "line",
    [
        lambda n: "`" * n + " x`\n",  # a line of backticks with one more after them
        lambda n: "# Title" + " " * n + "x\n",  # a heading with a long run of blanks
    ],
    ids=["backticks", "heading_blanks"],
)
def test_a_long_line_is_chunked_in_time_that_grows_with_it(line):
    small, large = line(250_000), line(2_000_000)
    assert [s.kind for s in chunk("c", large)] == [s.kind for s in chunk("c", small)]
    small_s = best_of(5, lambda: chunk("c", small))
    large_s = best_of(3, lambda: chunk("c", large))
    assert large_s < 24 * small_s, f"8x the line took {large_s / small_s:.1f}x the time"


# Shared seams


@pytest.fixture
def db(tmp_path):
    return tmp_path / "cairn.db"


@pytest.fixture
def key(db):
    store = Store(db)
    try:
        store.create_project("ops")
        return store.create_key("laptop")
    finally:
        store.close()


def call(client: TestClient, key: str, name: str, **arguments) -> dict:
    """An MCP tool called over HTTP, in process: the result."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
    body["params"] = {"name": name, "arguments": arguments}
    answer = client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": key}, json=body)
    return answer.json()["result"]


def post(client: TestClient, key: str, text: str, **fields) -> httpx.Response:
    return client.post(
        "/api/projects/ops/notes", headers={"x-api-key": key}, json={"text": text, **fields}
    )


class Feed:
    """A source kind of the test's own: each run pulls the drafts it is given."""

    answers: ClassVar[list[Pulled]] = []

    def __init__(self, name: str) -> None:
        self.name = name

    async def pull(self, cursor):
        return Feed.answers.pop(0)


def source_run(monkeypatch, db, *drafts: NoteDraft):
    def _open(name, config, **options):
        return Feed(name)

    monkeypatch.setitem(KINDS, "feed", SourceKind(check=dict, open=_open, via="feed"))
    store = Store(db)
    try:
        if store.get_source("ops-feed") is None:
            store.add_source("ops-feed", "feed", {"projects": ["ops"]})
        Feed.answers = [Pulled(drafts=list(drafts), cursor=f"c{len(drafts)}")]
        return asyncio.run(run_source(store, "ops-feed", http=httpx.AsyncClient(), env={}))
    finally:
        store.close()


def notes(db) -> int:
    store = Store(db)
    try:
        return len(store.list_notes(store.get_project("ops").id))
    finally:
        store.close()


# 2. A note holds at most FACTS_MAX facts


def numbers(n: int, mark: str = "Kingfisher") -> str:
    return f"# Counts\n\n{mark} " + " ".join(str(10_000 + i) for i in range(n)) + "\n"


def test_a_note_past_its_facts_is_refused_before_it_is_stored(db, key, monkeypatch):
    past = numbers(FACTS_MAX + 1)
    with TestClient(create_app(Store(db), public_url=BASE), base_url=BASE) as client:
        rest = post(client, key, past)
        said = rest.json()["error"]
        assert rest.status_code == 400
        assert "30,001 facts" in said and "30,000" in said
        assert "Kingfisher" not in said and "10000" not in said
        tool = call(client, key, "note", project="ops", text=past)
        assert tool["isError"] is True and "30,001 facts" in tool["content"][0]["text"]
        assert notes(db) == 0
        assert post(client, key, numbers(FACTS_MAX)).status_code == 201  # at the bound: saved
    report = source_run(
        monkeypatch, db, NoteDraft("ops", "Counts", past, source_ref="feed:1", label="feed")
    )
    assert report.saved == 0 and report.reasons == {"facts past their bound": 1}
    assert notes(db) == 1


# 3. A page holds at most PAGE_CHARS_MAX characters of claims

WORDS = " ".join(["the ledger holds every entry once"] * 2_900)  # 98,599 characters, no fact


def test_a_note_past_its_pages_characters_is_refused_as_a_full_page(db, key, monkeypatch):
    with TestClient(create_app(Store(db), public_url=BASE), base_url=BASE) as client:
        for n in range(4):
            assert post(client, key, f"# Ledger\n\nBook {n}: {WORDS}\n").status_code == 201
        rest = post(client, key, f"# Ledger\n\nBook 4: {WORDS}\n")
        said = rest.json()["error"]
        assert rest.status_code == 409
        assert "400,000 characters" in said and "ledger" in said and "claims a page" not in said
        tool = call(client, key, "note", project="ops", text=f"# Ledger\n\nBook 4: {WORDS}\n")
        assert tool["isError"] is True and "400,000 characters" in tool["content"][0]["text"]
        assert notes(db) == 4
        other = post(client, key, f"# Ledger\n\nBook 4: {WORDS}\n", topic="Ledger, second")
        assert other.status_code == 201  # another topic has room
    draft = NoteDraft("ops", "Ledger", f"Book 5: {WORDS}\n", source_ref="feed:5", label="feed")
    report = source_run(monkeypatch, db, draft)
    assert report.saved == 0 and report.reasons == {PAGE_FULL: 1}


def test_a_page_larger_already_stays_as_it_is_and_takes_no_more_notes(db, key, monkeypatch):
    store = Store(db)
    try:
        project = store.get_project("ops")
        monkeypatch.setattr(cairn.store.memory, "PAGE_CHARS_MAX", 10 * PAGE_CHARS_MAX)
        for n in range(5):  # saved as before the bound: 5 x 98,600 characters
            note, _ = store.add_note(project.id, f"# Ledger\n\nBook {n}: {WORDS}\n")
            assert compile_note(store, note, lint_gate).status == "saved"
        monkeypatch.undo()
        version = store.get_page(project.id, "ledger").version
    finally:
        store.close()
    with TestClient(create_app(Store(db), public_url=BASE), base_url=BASE) as client:
        rest = post(client, key, "# Ledger\n\nOne more line.\n")
        said = rest.json()["error"]
        assert rest.status_code == 409 and "stays as it is" in said
        assert "takes no more notes" in said and "400,000" in said
        page = client.get("/api/projects/ops/pages/ledger", headers={"x-api-key": key})
        assert page.status_code == 200 and page.json()["version"] == version


# 4. A server process compiles at most N notes at a time


class HeldGate:
    """The lint gate, held at its door until `release` is set; it counts who is inside."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.inside = self.most = 0

    def __call__(self, candidate, spans):
        with self.lock:
            self.inside += 1
            self.most = max(self.most, self.inside)
        try:
            assert self.release.wait(WAIT), "the test never released the gate"
            return lint_gate(candidate, spans)
        finally:
            with self.lock:
                self.inside -= 1


def until(done: Callable[[], bool], what: str) -> None:
    """A bounded wait for `done()`: a look every few milliseconds; it decides nothing."""
    pause = threading.Event()
    for _ in range(int(WAIT / 0.005)):
        if done():
            return
        pause.wait(0.005)
    raise AssertionError(f"waited {WAIT} s for {what}")


def serve(monkeypatch, db, during: Callable[[TestClient, object], object], *options: str):
    """`serve` through cairn.server.main, its gate held, uvicorn.run replaced by a runner that
    enters the app's lifespan with TestClient; what `during(client, turns)` returns."""
    ran = []

    def run(app, **_):
        with TestClient(app, base_url=BASE) as client:
            ran.append(during(client, app.state.compiles))

    monkeypatch.setattr(uvicorn, "run", run)
    assert server.main(["serve", "--db", str(db), "--no-tidy", *options]) == 0
    (result,) = ran
    return result


@pytest.mark.parametrize(("options", "n"), [((), 2), (("--compiles", "3"), 3)])
def test_eight_notes_at_once_compile_n_at_a_time_and_nothing_else_waits(
    db, key, monkeypatch, options, n
):
    gate = HeldGate()
    monkeypatch.setattr(cli, "lint_gate", gate)
    answers: dict[int, int] = {}

    def during(client: TestClient, turns) -> None:
        def send(i: int) -> None:
            answers[i] = post(client, key, f"# Topic {i}\n\nLine {i} of the run.\n").status_code

        threads = [threading.Thread(target=send, args=(i,), daemon=True) for i in range(8)]
        for thread in threads:
            thread.start()
        until(lambda: gate.inside == n and turns.waiting == 8 - n, f"{n} inside, the rest waiting")
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/projects", headers={"x-api-key": key}).status_code == 200
        assert call(client, key, "projects")["isError"] is False
        signed_in = client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
        assert signed_in.status_code == 200 and client.get("/b/ops").status_code == 200
        assert answers == {} and gate.inside == n
        gate.release.set()
        for thread in threads:
            thread.join(WAIT)

    serve(monkeypatch, db, during, *options)
    assert answers == dict.fromkeys(range(8), 201)
    assert gate.most == n


def test_serve_refuses_a_number_of_compiles_that_is_not_one_or_more(db, key):
    for wrong in ("0", "-2", "two"):
        with pytest.raises(SystemExit) as stopped:
            server.main(["serve", "--db", str(db), "--no-tidy", "--compiles", wrong])
        assert stopped.value.code == 2


# 5. A fact that emphasis touches is read whole

EMPHASIS = [
    "_2,700 more comments are left out._",
    "_about 1,250_ jobs ran.",
    "_They left out 2,700._",
    "The queue held _1,250_ jobs at noon.",
    "__2,700__ more comments are left out.",
    "_2099-03-02_ is the day, _v1.2.3_ the version.",
]


def test_a_number_that_opens_an_emphasis_is_read_whole_and_saved(db, key, monkeypatch):
    (span,) = chunk("s", "_2,700 more comments are left out._")
    assert [(f.kind, f.value) for f in extract_facts(span)] == [("number", "2,700")]
    with TestClient(create_app(Store(db), public_url=BASE), base_url=BASE) as client:
        for i, line in enumerate(EMPHASIS):
            answer = post(client, key, f"# Probe {i}\n\nA first line.\n\n{line}\n")
            assert answer.status_code == 201, (line, answer.json())
        tool = call(client, key, "note", project="ops", text="# Tool\n\n_2,700 rows were kept._\n")
        assert tool["isError"] is False
    drafts = [NoteDraft("ops", "Feed", "_3,100 issues were read._\n", source_ref="f", label="f")]
    assert source_run(monkeypatch, db, *drafts).saved == 1


@pytest.mark.parametrize(
    ("text", "read"),
    [
        ("max_retries_3 is set.", []),
        ("v1_2 is a tag.", []),
        ("notes_2026-03-14.md is a file.", [("number", "03")]),
        ("file_2.txt and x_1.2.3 are names.", []),
    ],
)
def test_an_underscore_inside_a_word_reads_as_before(text, read):
    (span,) = chunk("s", text)
    assert [(f.kind, f.value) for f in extract_facts(span)] == read
