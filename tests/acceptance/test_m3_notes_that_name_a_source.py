"""M3 acceptance tests: a note whose `source_ref` says it came from a mail thread, a meeting, a
calendar, a document or a to-do list goes to no model either, unless the operator allows it.

Written before implementation. Implementers must not edit this file once it is frozen. The
contract goes in the docstrings of src/cairn/sources/__init__.py (the forms), src/cairn/arrange.py
(arrange_page and the worker) and the guides (privacy.md's **Models** line, sources.md's table of
forms). tests/acceptance/test_m2_withheld_notes.py, test_a1_written_by.py and
test_q1_bounded_reads.py stay as they are and still pass.

Why: M2 keeps from the arranging model the notes the server's own Google sources (and a
connector of the operator's own) read, by the note's `via`. The sources guide also tells people
to have an agent they already use read their mail, meetings, calendar, documents and to-dos with
its own connector and write what matters as notes, each with a `source_ref` of a form the
guide's recipes give. Such a note arrives with `via` "mcp" or "api", so M2's check does not see
it, and the mail it copied went into the request to the model under the default setting. The
server cannot know where an agent's text came from; it goes by what the note says.

The rule
- A note is kept from the arranging model when its `via` is a withheld kind's (M2, as today), or
  when its `source_ref` has one of the forms the recipes give: what stands before its first `:`
  is `meeting`, `mail`, `calendar`, `doc` or `todo`. Letter case and white space around that word
  do not count: `Mail:`, `MAIL:`, ` mail:` and `mail :` are the form `mail:`. Anything else is
  not a form and the note is sent as before: `mailbox:`, `docs:` and `email:` are other words,
  `mail` with no colon names no form, and `session:mail:1` is a `session:` note.
- A note is kept from the model too when its `source_ref` begins the way a withheld kind writes
  its own notes' (`SourceKind.ref_form`, the word before the first `:`): `gmail:` (Gmail),
  `meet:` (Google Meet), `drive:` (Google Drive), and `calendar:` (Google Calendar, already a
  recipe form). An agent that copies a mail and writes `gmail:<id>` in place of the recipe's
  `mail:<id>` says the same thing. The same letter-case and white-space rule holds. A kind that
  is not withheld adds no word, and a connector of the operator's own has no word of its own.
- Whoever wrote the note, through the MCP `note` tool, `POST /api/projects/{project}/notes` or the
  web form. One such note on a page is enough, whoever wrote the rest of the page.
- A page that holds such a note is treated exactly as M2 treats a withheld page: no request is
  built and no call is made, nothing is reserved or spent, nothing is added to or taken off the
  count towards a pause, the worker tidies it without the model, its outcome is "withheld" with
  M2's one fixed sentence (`cairn.arrange.WITHHELD`), and no log line or outcome names a
  `source_ref`, a title or a note's text.
- Where notes are withheld at all: the worker by default, `arrange --model-env` and `serve
  --model-env` unless the operator sends, and `arrange_page` given some `via` values to withhold.
  `arrange_page` with `withhold=()` withholds nothing, as before.

The setting
- `<PREFIX>_SOURCE_NOTES`, as M2 has it: `send` sends these notes with the others; unset, blank
  or `withhold` keeps both kinds from the model.

Seams this file relies on, as they are today: `create_app(store, public_url=...)` served through
starlette's TestClient, a key made with `Store.create_key` used as an MCP bearer and as REST's
`x-api-key`, and a browser signed in by `POST /login` with a key; the rest as
test_m2_withheld_notes.py has it.
"""

import asyncio
import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest
import uvicorn
from starlette.testclient import TestClient

from cairn import server, sources
from cairn.arrange import TIDY, WITHHELD, Arranger, Price, arrange_page
from cairn.llm import Reply
from cairn.server import create_app
from cairn.store import Limits, Store

BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
PRICE = Price(input_per_million=1_000_000, output_per_million=5_000_000)  # $1 and $5

# The forms the sources guide's recipes give, one `source_ref` each, ids made up.
FORMS = {
    "meeting": "meeting:m3-mtg-0417",
    "mail": "mail:m3-thread-8821",
    "calendar": "calendar:m3-event-5530",
    "calendar week": "calendar:week/2099-11-09",
    "doc": "doc:m3-file-7716",
    "todo": "todo:m3-errands/m3-item-42",
}
# Other `source_ref`s: these notes are sent as before.
ORDINARY = {
    "session": "session:m3-sess-31",
    "commit": "commit:m3-c9f2e1",
    "none": None,
}
# The forms written with other letter case or white space around the word: still the forms.
SPELLED = {
    "upper first": "Mail:m3-case-1",
    "upper": "MEETING:m3-case-2",
    "mixed": "CaLeNdAr:m3-case-3",
    "space before": " doc:m3-space-4",
    "space before the colon": "todo :m3-space-5",
    "tab and upper": "\tDOC:m3-space-6",
}
# Written the way a withheld source writes its own notes' source_ref: kept from the model too.
OWN_WAY = {
    "gmail": "gmail:m3-own-1",
    "meet": "Meet:m3-own-2/t-1",
    "drive": " drive:m3-own-3",
    "google calendar": "CALENDAR:m3-own-4",
}
# Like a form, but not one, and why each is sent as before.
RESEMBLING = {
    # Another word: the match is on the whole word before the colon, not on its start.
    "mailbox": "mailbox:m3-like-1",
    # "docs" is not "doc": a plural is another word.
    "docs": "docs:m3-like-2",
    # "email" is not "mail": the recipes write mail:, and a word is not matched inside another.
    "email": "email:m3-like-3",
    # No colon: a form is a word and a colon, so a bare word names no form.
    "bare": "mail",
    # What counts is what stands before the first colon: this is a session's note.
    "later colon": "session:mail:m3-like-5",
    # A link: before its first colon stands "https".
    "link": "https://mail.example.test/m3-like-6",
    # "meetup" is not "meet", Google Meet's word: the whole word counts here too.
    "meetup": "meetup:m3-like-7",
}


def six(title: str) -> str:
    """A note of six claims under one heading, each line its own, every one naming the title."""
    lines = "\n".join(f"- {title} keeps crate {n}{len(title)} on shelf {n}." for n in range(6))
    return f"# {title}\n\n{lines}\n"


def slug(title: str) -> str:
    return title.lower().replace(" ", "-")


class World:
    """One workspace, served in process: an agent's key, used over MCP and over REST."""

    def __init__(self, path) -> None:
        self.store = Store(path)
        self.project = self.store.create_project("alpha")
        self.app = create_app(self.store, public_url=BASE)
        self.client = TestClient(self.app, base_url=BASE)
        self.client.__enter__()
        self.key = self.store.create_key("m3-agent")

    def close(self) -> None:
        self.client.__exit__(None, None, None)
        self.store.close()

    def mcp(self, title: str, source_ref: str | None, text: str | None = None) -> None:
        arguments = {"project": "alpha", "text": text or six(title)}
        if source_ref is not None:
            arguments["source_ref"] = source_ref
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "note", "arguments": arguments},
        }
        headers = {**MCP_HEADERS, "authorization": f"Bearer {self.key}"}
        r = self.client.post("/mcp", headers=headers, json=body)
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        assert result["isError"] is False, result
        assert json.loads(result["content"][0]["text"])["status"] == "saved"

    def rest(self, title: str, source_ref: str | None, text: str | None = None) -> None:
        body = {"text": text or six(title)}
        if source_ref is not None:
            body["source_ref"] = source_ref
        r = self.client.post(
            "/api/projects/alpha/notes", headers={"x-api-key": self.key}, json=body
        )
        assert r.status_code == 201, r.text

    def web(self, title: str, source_ref: str) -> None:
        browser = TestClient(self.app, base_url=BASE)
        signed = browser.post(
            "/login", data={"key": self.key}, headers=SAME_ORIGIN, follow_redirects=False
        )
        assert signed.status_code in (302, 303), signed.text
        r = browser.post(
            "/b/alpha/notes",
            data={"text": six(title), "source_ref": source_ref},
            headers=SAME_ORIGIN,
            follow_redirects=False,
        )
        assert r.status_code == 303, r.text

    def write(self, door: str, title: str, source_ref: str | None) -> None:
        getattr(self, door)(title, source_ref)

    def page(self, title: str):
        return self.store.get_page(self.project.id, slug(title))

    def stored_ref(self, title: str) -> str | None:
        (note,) = [n for n in self.store.list_notes(self.project.id) if n.title == title]
        return note.source_ref


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path / "cairn.db")
    yield made
    made.close()


def told(requests) -> str:
    """Everything a model was sent, every message of every request."""
    return "\n".join(m["content"] for request in requests for m in request)


def never_sent(requests, page) -> None:
    sent = told(requests)
    assert page.title not in sent
    for claim in page.claims:
        assert claim.text not in sent


def all_sent(requests, page) -> None:
    sent = told(requests)
    assert page.title in sent
    for claim in page.claims:
        assert claim.text in sent


def tag_of(user: str) -> str:
    return re.search(r'<(claim-[0-9a-f]+) n="1" id="cl_[0-9a-f]{16}"', user).group(1)


def listed(user: str) -> list[list[int]]:
    tag = tag_of(user)
    head = user[: user.index(f"<{tag} ")]
    (line,) = re.findall(rf"^[^\n]*{tag}[^\n]*: (\[.*\])$", head, re.MULTILINE)
    return json.loads(line)


def reversed_answer(messages) -> dict:
    """An arrangement read from the request alone: the claim numbers reversed, less the later
    claims of each listed group of repeats, with those groups as "same"."""
    user = "\n".join(m["content"] for m in messages if m["role"] != "system")
    groups = listed(user)
    folded = {n for group in groups for n in group[1:]}
    numbers = [int(n) for n in re.findall(rf'<{tag_of(user)} n="(\d+)"', user)]
    return {"order": [n for n in reversed(numbers) if n not in folded], "same": groups}


class Recorder:
    """A model that records every request and answers each page with its claims reversed, or
    in prose (not an arrangement) when `prose` is set."""

    name = "m3-recorder"

    def __init__(self, *, prose: bool = False) -> None:
        self.prose = prose
        self.requests: list[list[dict]] = []

    def complete(self, messages) -> Reply:
        self.requests.append([dict(message) for message in messages])
        if self.prose:
            return Reply(text="A better order, in words.", prompt_tokens=100, completion_tokens=9)
        return Reply(
            text=json.dumps(reversed_answer(messages)), prompt_tokens=300, completion_tokens=40
        )


def arrange_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "action", None) == "arrange"]


def title_of(door: str, name: str) -> str:
    return f"{door.capitalize()} {name} brief"


# arrange_page


def test_a_note_of_each_form_over_mcp_or_rest_keeps_its_page_from_the_model(world):
    """For each form and each door, a page of one such note is withheld: no request, nothing
    spent, M2's one sentence; pages beside it whose notes carry other source_refs are sent."""
    for door in ("mcp", "rest"):
        for name, ref in {**FORMS, **ORDINARY}.items():
            world.write(door, title_of(door, name), ref)
    store, project = world.store, world.project
    assert world.stored_ref(title_of("mcp", "mail")) == FORMS["mail"]
    assert {n.via for n in store.list_notes(project.id)} == {"mcp", "api"}
    events = len(store.list_audit(limit=200))

    model = Recorder()
    for door in ("mcp", "rest"):
        for name in FORMS:
            page = world.page(title_of(door, name))
            assert len(page.claims) == 6
            result = arrange_page(
                store, project.id, page.slug, model, price=PRICE, withhold=sources.withheld_via()
            )
            assert (result.status, result.version, result.previous) == (
                "withheld",
                page.version,
                page.version,
            ), (door, name)
            assert (result.moved, result.merged, result.draft.attempts) == (0, 0, 0)
            assert result.problem == WITHHELD
            assert world.page(page.title) == page
    assert model.requests == []
    used = store.usage()
    assert (used.model_tokens_in, used.model_tokens_out, used.model_micros) == (0, 0, 0)
    assert len(store.list_audit(limit=200)) == events

    for door in ("mcp", "rest"):
        for name in ORDINARY:
            page = world.page(title_of(door, name))
            result = arrange_page(
                store, project.id, page.slug, model, withhold=sources.withheld_via()
            )
            assert result.status == "arranged", (door, name)
            all_sent(model.requests[-1:], page)
    assert len(model.requests) == 2 * len(ORDINARY)
    for door in ("mcp", "rest"):
        for name in FORMS:
            never_sent(model.requests, world.page(title_of(door, name)))


def test_one_such_note_among_ordinary_ones_is_enough(world):
    for n in range(5):
        world.rest(
            f"Plan part {n}", "session:m3-plan", text=f"# Release plan\n\n- Step {n} ships.\n"
        )
    world.mcp("Plan mail", FORMS["mail"], text="# Release plan\n\n- Dee approved it by mail.\n")
    for n in range(5, 10):
        world.mcp(f"Plan part {n}", None, text=f"# Release plan\n\n- Step {n} ships.\n")
    page = world.page("Release plan")
    assert len(page.claims) == 11
    model = Recorder()
    result = arrange_page(
        world.store, world.project.id, page.slug, model, withhold=sources.withheld_via()
    )
    assert result.status == "withheld" and model.requests == []


def test_the_forms_count_whatever_their_letter_case_and_the_white_space_around_the_word(world):
    """REST and MCP store a source_ref as given; the web form drops white space at its ends.
    Each of these is a form all the same, and its page is withheld."""
    for n, (name, ref) in enumerate(SPELLED.items()):
        world.write(("rest", "mcp")[n % 2], f"Spelled {name}", ref)
    world.web("Typed in the browser", " Mail:m3-web-1 ")
    assert world.stored_ref("Spelled space before") == " doc:m3-space-4"  # kept as given
    assert world.stored_ref("Typed in the browser") == "Mail:m3-web-1"
    model = Recorder()
    for title in [f"Spelled {name}" for name in SPELLED] + ["Typed in the browser"]:
        page = world.page(title)
        result = arrange_page(
            world.store, world.project.id, page.slug, model, withhold=sources.withheld_via()
        )
        assert result.status == "withheld", title
    assert model.requests == []


def test_a_source_ref_written_the_way_a_withheld_source_writes_its_own_is_withheld(world):
    """`gmail:`, `meet:`, `drive:` and `calendar:`, over MCP and over REST, any letter case."""
    for door in ("mcp", "rest"):
        for name, ref in OWN_WAY.items():
            world.write(door, title_of(door, f"own {name}"), ref)
    model = Recorder()
    for door in ("mcp", "rest"):
        for name in OWN_WAY:
            page = world.page(title_of(door, f"own {name}"))
            result = arrange_page(
                world.store, world.project.id, page.slug, model, withhold=sources.withheld_via()
            )
            assert (result.status, result.problem) == ("withheld", WITHHELD), (door, name)
    assert model.requests == []
    arranger = Arranger(model, price=None, view_for=world.store.for_account)
    arranger.queue(world.store.account_id, world.project.id, slug(title_of("mcp", "own gmail")))
    assert [d.status for d in asyncio.run(arranger.run_once())] == ["withheld"]
    assert model.requests == []


def test_a_source_ref_that_only_resembles_a_form_is_sent_as_before(world):
    for n, (name, ref) in enumerate(RESEMBLING.items()):
        world.write(("mcp", "rest")[n % 2], f"Like {name}", ref)
    model = Recorder()
    for name in RESEMBLING:
        page = world.page(f"Like {name}")
        assert len(page.claims) == 6
        result = arrange_page(
            world.store, world.project.id, page.slug, model, withhold=sources.withheld_via()
        )
        assert result.status == "arranged", name
        all_sent(model.requests[-1:], page)
    assert len(model.requests) == len(RESEMBLING)


def test_a_caller_that_withholds_nothing_sends_these_notes(world):
    """`withhold=()`, arrange_page's default and what `send` gives the worker: sent."""
    for name, ref in FORMS.items():
        world.mcp(title_of("mcp", name), ref)
    model = Recorder()
    for name in FORMS:
        page = world.page(title_of("mcp", name))
        assert arrange_page(world.store, world.project.id, page.slug, model).status == "arranged"
        all_sent(model.requests[-1:], page)
    page = world.page(title_of("mcp", "mail"))
    assert arrange_page(
        world.store, world.project.id, page.slug, Recorder(), withhold=()
    ).status in (
        "arranged",
        "unchanged",
    )


# The worker


def test_the_worker_tidies_such_a_page_spends_nothing_and_arranges_the_one_beside_it(world):
    """Two notes of one meeting (one over MCP, one over REST) on one page, a line repeated: the
    worker tidies it without the model, and sends the ordinary page beside it."""
    first = (
        "# Hiring loop\n\nThe loop has three rounds.\n\n## Panel\n\n- Ada runs design.\n"
        "- Brook runs coding.\n"
    )
    second = (
        "# Hiring loop\n\nThe loop has three rounds.\n\n## Timing\n\n- Each round is short.\n"
        "\n## Panel\n\n- Cy runs values.\n- Dee takes notes.\n"
    )
    world.mcp("Hiring one", FORMS["meeting"], text=first)
    world.rest("Hiring two", FORMS["meeting"], text=second)
    before = world.page("Hiring loop")
    assert len(before.claims) == 7
    world.mcp("Session notes", ORDINARY["session"])
    store, project = world.store, world.project

    def view_for(account_id):
        return store.for_account(account_id).limited(Limits(model_budget_micros=1_000_000))

    model = Recorder()
    arranger = Arranger(model, price=PRICE, view_for=view_for)
    for title in ("Hiring loop", "Session notes"):
        arranger.queue(store.account_id, project.id, slug(title))
    done = asyncio.run(arranger.run_once())
    assert [(d.slug, d.status) for d in done] == [
        ("hiring-loop", "withheld"),
        ("session-notes", "arranged"),
    ]
    assert done[0].draft is None and done[0].previous == before.version
    assert len(model.requests) == 1
    never_sent(model.requests, before)
    after = world.page("Hiring loop")
    assert len(after.claims) == 6 and after.metrics["arranged_by"] == TIDY  # the repeat folded
    used = store.usage()
    assert (used.model_tokens_in, used.model_tokens_out, used.model_micros) == (300, 40, 500)

    arranger.queue(store.account_id, project.id, "hiring-loop")  # no new note: seen
    assert [d.status for d in asyncio.run(arranger.run_once())] == ["seen"]
    assert len(model.requests) == 1


def test_a_page_too_small_to_send_is_still_withheld_not_small(world):
    world.rest("Short", FORMS["todo"], text="# Short\n\n- Buy the crates.\n- Book the van.\n")
    arranger = Arranger(Recorder(), price=None, view_for=world.store.for_account)
    arranger.queue(world.store.account_id, world.project.id, "short")
    assert [d.status for d in asyncio.run(arranger.run_once())] == ["withheld"]


def test_such_a_page_neither_adds_to_nor_clears_the_count_towards_a_pause(world):
    world.mcp("Page one", ORDINARY["commit"])
    world.mcp("Mail page", FORMS["mail"])
    world.rest("Page two", None)
    world.rest("Doc page", FORMS["doc"])
    model = Recorder(prose=True)  # every answer it gives is unusable
    arranger = Arranger(
        model, price=None, view_for=world.store.for_account, pause_after=3, clock=lambda: 1000.0
    )
    for title in ("Page one", "Mail page", "Page two", "Doc page"):
        arranger.queue(world.store.account_id, world.project.id, slug(title))
    done = asyncio.run(arranger.run_once())
    assert [d.status for d in done] == ["invalid", "withheld", "invalid", "withheld"]
    assert arranger.unusable == 2 and not arranger.paused()
    assert len(model.requests) == 4  # two tries for each ordinary page


def test_the_log_and_the_outcome_name_no_source_ref_title_or_text(world, caplog):
    for name, ref in FORMS.items():
        world.mcp(title_of("mcp", name), ref)
    arranger = Arranger(Recorder(), price=None, view_for=world.store.for_account)
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        for name in FORMS:
            arranger.queue(world.store.account_id, world.project.id, slug(title_of("mcp", name)))
        done = asyncio.run(arranger.run_once())
    assert [d.status for d in done] == ["withheld"] * len(FORMS)
    lines = arrange_records(caplog)
    assert len(lines) == len(FORMS)
    for name, ref in FORMS.items():
        page = world.page(title_of("mcp", name))
        for line in lines:
            said = line.getMessage()
            assert "withheld" in said and world.project.id in said
            assert ref not in said and ref.split(":", 1)[1] not in said
            assert page.title not in said and page.slug not in said
        for claim in page.claims:
            assert claim.text not in caplog.text
        assert ref not in caplog.text
        for d in done:
            assert ref not in repr(d) and page.title not in repr(d)
    assert all(d.draft is None for d in done)


# The setting, on the command line


class Endpoint(BaseHTTPRequestHandler):
    """A model endpoint on this machine: records every request's messages and answers each
    page with its claims reversed."""

    bodies: ClassVar[list[list[dict]]] = []

    def log_message(self, *args) -> None:
        pass

    def do_POST(self) -> None:
        messages = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["messages"]
        type(self).bodies.append(messages)
        content = json.dumps(reversed_answer(messages))
        body = json.dumps(
            {
                "choices": [
                    {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 300, "completion_tokens": 40},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def endpoint(monkeypatch):
    """The endpoint, named by ARRANGER_BASE_URL and ARRANGER_MODEL; the requests it saw."""
    Endpoint.bodies = []
    httpd = HTTPServer(("127.0.0.1", 0), Endpoint)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("ARRANGER_BASE_URL", f"http://127.0.0.1:{httpd.server_address[1]}/v1")
    monkeypatch.setenv("ARRANGER_MODEL", "m3-endpoint-model")
    monkeypatch.delenv("ARRANGER_SOURCE_NOTES", raising=False)
    yield Endpoint.bodies
    httpd.shutdown()
    httpd.server_close()
    thread.join(5)


@pytest.fixture
def db(tmp_path):
    """A database with an ordinary page and a page of each form, written over MCP and REST."""
    path = tmp_path / "cairn.db"
    made = World(path)
    made.rest("Session notes", ORDINARY["session"])
    for n, (name, ref) in enumerate(FORMS.items()):
        made.write(("mcp", "rest")[n % 2], title_of("form", name), ref)
    pages = {title: made.page(title) for title in [title_of("form", n) for n in FORMS]}
    project_id = made.project.id
    made.close()
    return path, project_id, pages


SETTINGS = {"unset": None, "blank": "", "withhold": "withhold", "send": "send"}


def set_notes(monkeypatch, value) -> None:
    if value is None:
        monkeypatch.delenv("ARRANGER_SOURCE_NOTES", raising=False)
    else:
        monkeypatch.setenv("ARRANGER_SOURCE_NOTES", value)


@pytest.mark.parametrize("setting", list(SETTINGS))
def test_arrange_withholds_them_unless_the_operator_sends(
    db, endpoint, monkeypatch, capsys, setting
):
    path, _, pages = db
    set_notes(monkeypatch, SETTINGS[setting])
    argv = ["arrange", "--db", str(path), "--account", "acct_default", "--model-env", "ARRANGER"]
    assert server.main(argv) == 0
    lines = capsys.readouterr().out.splitlines()
    (ordinary,) = [line for line in lines if line.startswith("alpha/session-notes: ")]
    assert ordinary.startswith("alpha/session-notes: arranged")
    for page in pages.values():
        (said,) = [line for line in lines if line.startswith(f"alpha/{page.slug}: ")]
        if setting == "send":
            assert said.startswith(f"alpha/{page.slug}: arranged")
            all_sent(endpoint, page)
        else:
            assert said.startswith(f"alpha/{page.slug}: withheld") and "tokens" not in said
            never_sent(endpoint, page)
    assert len(endpoint) == (1 + len(pages) if setting == "send" else 1)


@pytest.mark.parametrize("setting", list(SETTINGS))
def test_serve_s_worker_withholds_them_unless_the_operator_sends(
    db, endpoint, monkeypatch, setting
):
    path, project_id, pages = db
    set_notes(monkeypatch, SETTINGS[setting])
    passes = []

    def run(app, **options):  # inside serve, while its database is open
        arranger = app.state.arranger
        for slugged in ("session-notes", *(page.slug for page in pages.values())):
            arranger.queue("acct_default", project_id, slugged)
        passes.append({d.slug: d.status for d in asyncio.run(arranger.run_once(limit=20))})

    monkeypatch.setattr(uvicorn, "run", run)
    assert server.main(["serve", "--db", str(path), "--model-env", "ARRANGER"]) == 0
    (statuses,) = passes
    assert statuses["session-notes"] == "arranged"
    for page in pages.values():
        if setting == "send":
            assert statuses[page.slug] == "arranged"
            all_sent(endpoint, page)
        else:
            assert statuses[page.slug] == "withheld"
            never_sent(endpoint, page)
    assert len(endpoint) == (1 + len(pages) if setting == "send" else 1)
