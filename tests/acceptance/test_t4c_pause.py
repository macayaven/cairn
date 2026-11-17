"""T4c acceptance tests (frozen): the worker stops asking a model that keeps failing.

Written by the orchestrator before implementation, after the arranging step was first run
against real models: of six, five could not arrange pages, and the worker would have asked
each of them again for every note, two calls a page, for as long as notes came. Implementers
must not edit this file. tests/acceptance/test_t4b_arrange.py stays as it is and still passes.

Counting
- The worker counts the pages in a row whose answer could not be used: "invalid" (not an
  arrangement after its retries) and "failed" (the provider's failure). A page that is
  "arranged", "unchanged" or "rejected" (an arrangement, whatever the gate then said) sets
  the count back to zero. Every other outcome ("small", "seen", "gone", "large", "guarded",
  "no budget", "stale", "invalid page") leaves the count as it is: no model was asked, or
  what it said was not the trouble. The count is the worker's, across workspaces.

Pausing
- `Arranger(..., pause_after=5, pause_seconds=600.0)`. After `pause_after` such pages in a
  row the worker pauses for `pause_seconds` on its clock: a pass (`run_once`) asks the model
  for no page, and every queued page stays queued (`pending()`). Pages the pass had taken
  and not yet started are queued again, not done.
- When the pause is over the worker goes on, one failure from pausing again: the first page
  whose answer cannot be used pauses it for another `pause_seconds`, and the first page
  whose answer can be used clears the count.
- `pause_after=0` never pauses.
- Each time a pause begins there is one line on the "cairn.access" log that says "paused",
  names the model, the number of answers in a row and the seconds. It names no page.

The `arrange` command
- One pass that runs into the pause stops there: it prints the pages it asked about as
  before, then one line on stderr that says it stopped, how many answers in a row could not
  be used and how many pages it did not ask about, and exits 1.
"""

import asyncio
import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from cairn import server
from cairn.arrange import Arranger
from cairn.compiler import compile_note
from cairn.llm import LLMError, Reply
from cairn.store import Store

CLAIM_ID = re.compile(r' id="(cl_[0-9a-f]+)"')
TITLE = re.compile(r"^Page title: (.*)$", re.MULTILINE)


def note(number: int) -> str:
    lines = "\n".join(
        f"- Shelf {number} keeps crate {number * 10 + n} on rack {n}." for n in range(7)
    )
    return f"# Page {number:02d}\n\n{lines}\n"


def make_pages(store: Store, count: int) -> str:
    """`count` pages of seven claims each in one project; its id."""
    project = store.get_project("alpha") or store.create_project("alpha")
    for number in range(1, count + 1):
        stored, _ = store.add_note(project.id, note(number))
        compile_note(store, stored)
    return project.id


class Model:
    """Answers each page with its claims in the order given ("unchanged"), with prose
    ("invalid"), or with a provider failure ("failed"), by the page's title."""

    name = "pause-test-model"

    def __init__(self, good: set[str] | None = None, *, fails: bool = False) -> None:
        self.good = good  # titles answered properly; None: every page
        self.fails = fails
        self.calls = 0
        self.titles: list[str] = []

    def complete(self, messages) -> Reply:
        self.calls += 1
        user = messages[-1]["content"]
        title = TITLE.search(user).group(1)
        self.titles.append(title)
        if self.fails:
            raise LLMError("the endpoint answered HTTP 500")
        if self.good is None or title in self.good:
            text = json.dumps({"order": CLAIM_ID.findall(user), "same": []})
            return Reply(text=text, prompt_tokens=100, completion_tokens=20)
        return Reply(
            text="Here is a better order, in words.", prompt_tokens=100, completion_tokens=9
        )


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def worker(store: Store, model: Model, clock: Clock, **options) -> Arranger:
    return Arranger(
        model, price=None, view_for=store.for_account, min_claims=1, clock=clock, **options
    )


def queue_all(arranger: Arranger, store: Store, project_id: str) -> None:
    for page in store.list_pages(project_id):
        arranger.queue(store.account_id, project_id, page.slug)


def statuses(done) -> list[str]:
    return [d.status for d in done]


def test_after_five_unusable_answers_in_a_row_the_worker_asks_for_nothing(store):
    project = make_pages(store, 8)
    model, clock = Model(good=set()), Clock()
    arranger = worker(store, model, clock)
    queue_all(arranger, store, project)

    done = asyncio.run(arranger.run_once(limit=10))

    assert statuses(done) == ["invalid"] * 5
    assert model.calls == 10  # two tries a page, five pages, and not one more
    assert len(arranger.pending()) == 3

    clock.now += 599
    assert asyncio.run(arranger.run_once(limit=10)) == []
    assert model.calls == 10 and len(arranger.pending()) == 3


def test_after_the_pause_one_more_failure_pauses_again_and_a_good_answer_clears_it(store):
    project = make_pages(store, 12)
    model, clock = Model(good=set()), Clock()
    arranger = worker(store, model, clock)
    queue_all(arranger, store, project)
    assert statuses(asyncio.run(arranger.run_once(limit=20))) == ["invalid"] * 5

    clock.now += 600
    again = asyncio.run(arranger.run_once(limit=20))
    assert statuses(again) == ["invalid"]  # one page, then paused again
    assert model.calls == 12 and len(arranger.pending()) == 6

    model.good = None  # the model is fixed
    clock.now += 600
    rest = asyncio.run(arranger.run_once(limit=20))
    assert statuses(rest) == ["unchanged"] * 6 and arranger.pending() == []

    # The count is back at zero: four failures in a row are not five.
    more = make_pages(store, 16)
    model.good = set()
    for number in range(13, 17):
        arranger.queue(store.account_id, more, f"page-{number:02d}")
    assert statuses(asyncio.run(arranger.run_once(limit=20))) == ["invalid"] * 4
    assert arranger.pending() == []


def test_a_usable_answer_between_failures_starts_the_count_again(store):
    project = make_pages(store, 10)
    model, clock = Model(good={"Page 05"}), Clock()
    arranger = worker(store, model, clock)
    queue_all(arranger, store, project)

    done = asyncio.run(arranger.run_once(limit=20))

    by_title = dict(zip([f"Page {n:02d}" for n in range(1, 11)], statuses(done), strict=False))
    assert statuses(done) == ["invalid"] * 4 + ["unchanged"] + ["invalid"] * 5
    assert by_title["Page 05"] == "unchanged"
    assert arranger.pending() == []  # the fifth failure in a row was the last page
    arranger.queue(store.account_id, project, "page-01")  # a new note would queue it again
    assert asyncio.run(arranger.run_once()) == []  # paused


def test_a_provider_that_fails_counts_as_well(store):
    project = make_pages(store, 7)
    model, clock = Model(fails=True), Clock()
    arranger = worker(store, model, clock)
    queue_all(arranger, store, project)

    done = asyncio.run(arranger.run_once(limit=10))

    assert statuses(done) == ["failed"] * 5
    assert model.calls == 5 and len(arranger.pending()) == 2


def test_the_pause_can_be_turned_off_and_set(store):
    project = make_pages(store, 7)
    model, clock = Model(good=set()), Clock()
    never = worker(store, model, clock, pause_after=0)
    queue_all(never, store, project)
    assert statuses(asyncio.run(never.run_once(limit=10))) == ["invalid"] * 7

    model.calls = 0
    soon = worker(store, model, clock, pause_after=2, pause_seconds=30.0)
    queue_all(soon, store, project)
    assert statuses(asyncio.run(soon.run_once(limit=10))) == ["invalid"] * 2
    clock.now += 29
    assert asyncio.run(soon.run_once(limit=10)) == []
    clock.now += 1
    assert statuses(asyncio.run(soon.run_once(limit=10))) == ["invalid"]
    for bad in (-1, 1.5):
        with pytest.raises(ValueError):
            worker(store, model, clock, pause_after=bad)
    with pytest.raises(ValueError):
        worker(store, model, clock, pause_seconds=0)


def test_a_pause_is_one_line_on_the_access_log_that_names_no_page(store, caplog):
    project = make_pages(store, 6)
    model, clock = Model(good=set()), Clock()
    arranger = worker(store, model, clock)
    queue_all(arranger, store, project)

    with caplog.at_level(logging.INFO, logger="cairn.access"):
        asyncio.run(arranger.run_once(limit=10))

    paused = [r.getMessage() for r in caplog.records if "paused" in r.getMessage()]
    assert len(paused) == 1
    assert model.name in paused[0] and "5" in paused[0] and "600" in paused[0]
    assert "page-0" not in paused[0] and "Shelf" not in caplog.text


class Endpoint(BaseHTTPRequestHandler):
    """A model endpoint on this machine that answers every page in prose."""

    requests = 0

    def log_message(self, *args) -> None:
        pass

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers["Content-Length"]))
        type(self).requests += 1
        message = {"role": "assistant", "content": "Here is a better order, in words."}
        body = json.dumps(
            {
                "choices": [{"message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 9},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_the_arrange_command_stops_at_the_pause_and_says_so(tmp_path, monkeypatch, capsys):
    db = tmp_path / "cairn.db"
    opened = Store(db)
    make_pages(opened, 7)
    opened.close()
    Endpoint.requests = 0
    httpd = HTTPServer(("127.0.0.1", 0), Endpoint)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("T4C_BASE_URL", f"http://127.0.0.1:{httpd.server_address[1]}/v1")
    monkeypatch.setenv("T4C_MODEL", "prose-model")
    try:
        code = server.main(
            ["arrange", "--db", str(db), "--account", "acct_default", "--model-env", "T4C"]
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)

    printed = capsys.readouterr()
    assert code == 1
    assert Endpoint.requests == 10  # five pages, two tries each, then nothing
    asked = [line for line in printed.out.splitlines() if ": invalid" in line]
    assert len(asked) == 5
    stopped = [line for line in printed.err.splitlines() if "stopped" in line]
    assert len(stopped) == 1
    assert "5" in stopped[0] and "2" in stopped[0] and "prose-model" in stopped[0]
