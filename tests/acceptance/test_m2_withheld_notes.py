"""M2 acceptance tests (frozen): pages that hold notes read from Google sources go to no model
unless the operator allows it.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/sources/__init__.py (the kinds),
src/cairn/arrange.py (arrange_page and the worker) and src/cairn/web/cli_arrange.py (the
setting). tests/acceptance/test_t4b_arrange.py, test_t4c_pause.py, test_t4e_numbers.py and
test_t4f_tidy.py stay as they are and still pass.

Why: a model that arranges pages may run on someone else's servers. Google's rules for its
users' data restrict passing it on to others and to AI models, and whether an operator's model
counts is the operator's question. So by default a page that holds a note read from a Google
source is not sent; the operator who has settled the question switches it on with one setting.
One rule in one place, not a rule in each connector.

A kind says whether its notes may go to a model
- `SourceKind.withheld: bool = False`. True for every kind that reads Google Workspace data
  (`google_meet`, `google_calendar`, `google_drive`, and `gmail`, whose `via` is "gmail"),
  and, since review finding Privacy 4, for `langfuse` and `granola` too: every kind.
- `cairn.sources.withheld_via() -> frozenset[str]`: the `via` of every kind in
  `KINDS` that is withheld, read when called (a kind added to `KINDS` later counts).

A page that holds such a note is not sent
- A page holds a withheld note when at least one of its claims cites a note whose `via`
  is one of those withheld. One such note is enough, whoever wrote the rest of the page
  and whenever, and a claim that cites it after another note (a repeat folded into the
  ordinary note's claim) is enough too.
- `arrange_page(view, project_id, slug, model, ..., withhold=())` takes the `via` values to
  withhold. When the page holds a note with one of them, no request is made (not one call,
  and nothing of the page is put into a request), nothing is spent, and the
  result's `status` is "withheld", with `version` and `previous` the page's version, `moved`
  and `merged` zero, and `problem` one fixed sentence in cairn's words saying the page holds
  notes read from a source the operator has not allowed a model to read: never a note's
  text, a title or a `via`. The default, `withhold=()`, is today's behaviour.
- The worker (`Arranger(model, price=..., view_for=...)`, as it is built today) and
  `arrange --model-env` withhold the values of `withheld_via()` unless the operator
  allowed them. A withheld page is still tidied without a model, exactly as `tidy_page` tidies
  it: its repeats fold, its claims move under their headings, nothing is sent. Its `Done`
  says "withheld", with no draft. The worker treats it as a page already seen: it is
  remembered (queued again with no new note, it asks nothing and logs nothing), it leaves the
  count towards a pause as it is (neither one more nor back to zero), and it spends none of
  the workspace's model budget.
- A withheld page makes one line on the "cairn.access" log at INFO, as other statuses do,
  saying "withheld", with the fields the other lines carry and no text of a note.
- Every other page is arranged as before, in the same pass.
- The check reads only what the view reaches: another workspace's notes never make a page
  withheld, and a page whose withheld note was forgotten is judged by the notes it still
  cites.
- A note written by a person or an agent is judged by its `via` like any other: one
  written via a withheld kind's name ("gmail") is withheld too. That errs on the side
  of not sending.

The operator's setting
- `<PREFIX>_SOURCE_NOTES` beside the model's other settings: "withhold" (the default, also
  unset or blank) or "send", which makes every page eligible as before. Any other value:
  `serve` and `arrange` exit 2 before anything is opened, with one line on stderr naming the
  variable and both values and never the value given. Without `--model-env` it is not read:
  `serve`, `serve --tidy` and `arrange --tidy` behave as today whatever it holds.
- `serve --help` and `arrange --help` name the variable.

Seams this file relies on, as they are today: `serve` builds its app and hands it to
`uvicorn.run` (replaced here), and the app keeps its worker at `app.state.arranger`; the
model for `serve` and `arrange` is an OpenAI-compatible endpoint on this machine, named by the
environment (as tests/acceptance/test_t4c_pause.py runs one).
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

from cairn import server, sources
from cairn.arrange import TIDY, Arranger, Price, arrange_page, tidy_page
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.llm import Reply
from cairn.memory import Memory
from cairn.sources import KINDS, SourceKind
from cairn.store import Limits, Store

PRICE = Price(input_per_million=1_000_000, output_per_million=5_000_000)  # $1 and $5
GOOGLE = ("google_meet", "google_calendar", "google_drive", "gmail")
NOT_GOOGLE = ("langfuse", "granola")

# Ordinary notes (no via): six claims, the first line twice.
FIRST = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--dry-run` lists the changes first.\n- `--delete` removes extra files.\n"
)
SECOND = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--retries 3` helps on flaky links.\n\n## Limits\n\n- At most 500 files go in one batch.\n"
)
# A meeting's notes: seven claims, the first line twice, a heading split by another.
MEET_ONE = (
    "# Hiring loop\n\nThe loop has three rounds.\n\n## Panel\n\n"
    "- Ada runs the design round.\n- Brook runs the coding round.\n"
)
MEET_TWO = (
    "# Hiring loop\n\nThe loop has three rounds.\n\n## Timing\n\n"
    "- Each round takes forty minutes.\n\n## Panel\n\n- Cy runs the values round.\n"
    "- Dee takes the notes.\n"
)
# Mail: six claims, nothing repeats, no heading.
MAIL_ONE = (
    "# Offer letter\n\n- Brook's offer starts on the first of May.\n"
    "- The offer names salary band B3.\n- The offer covers relocation to Lisbon.\n"
)
MAIL_TWO = (
    "# Offer letter\n\n- Dee signs the offer for the hiring team.\n"
    "- The offer expires after ten days.\n- Brook replies to dee@example.test.\n"
)


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def via(kind: str) -> str:
    return KINDS[kind].via


def add(view, text, *, project="alpha", via=None):
    """One note in `project` (made when missing), compiled onto its page. Returns the note."""
    found = view.get_project(project) or view.create_project(project)
    note, _ = view.add_note(found.id, text, via=via)
    assert compile_note(view, note, gate=lint_gate).status in ("saved", "unchanged")
    return note


def page_of(view, slug, *notes, project="alpha", via=None):
    """A page made of the given notes, each written with `via`. Returns (project, page)."""
    for text in notes:
        add(view, text, project=project, via=via)
    found = view.get_project(project)
    return found, view.get_page(found.id, slug)


def told(requests) -> str:
    """Everything a model was sent, every message of every request."""
    return "\n".join(m["content"] for request in requests for m in request)


def never_sent(requests, page) -> None:
    sent = told(requests)
    assert page.title not in sent
    for claim in page.claims:
        assert claim.text not in sent


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

    name = "m2-recorder"

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


# A kind says whether its notes may go to a model


def test_the_google_kinds_are_withheld_and_the_others_are_too(monkeypatch):
    for name in GOOGLE:
        assert KINDS[name].withheld is True, name
    for name in NOT_GOOGLE:  # Privacy 4: every kind the server reads is withheld by default
        assert KINDS[name].withheld is True, name
    assert SourceKind(check=dict, open=dict, via="plain").withheld is False  # the default
    withheld = sources.withheld_via()
    assert isinstance(withheld, frozenset)
    assert withheld == {kind.via for kind in KINDS.values() if kind.withheld}
    assert {via(name) for name in GOOGLE} <= withheld and "gmail" in withheld
    assert {via(name) for name in NOT_GOOGLE} <= withheld

    stand_in = SourceKind(check=dict, open=dict, via="m2-stand-in", withheld=True)
    open_one = SourceKind(check=dict, open=dict, via="m2-open")
    with monkeypatch.context() as patched:
        patched.setitem(KINDS, "m2_stand_in", stand_in)
        patched.setitem(KINDS, "m2_open", open_one)
        assert sources.withheld_via() == withheld | {"m2-stand-in"}  # read when called
    assert sources.withheld_via() == withheld


# arrange_page and its `withhold`


def test_a_page_with_one_withheld_note_is_not_sent_and_says_so(store):
    mail_project, mail = page_of(store, "offer-letter", MAIL_ONE, MAIL_TWO, via="gmail")
    release = [
        f"# Release plan\n\n- Step {n} ships build {n}0{n} to the shelf.\n" for n in range(10)
    ]
    for text in release[:5]:
        add(store, text)
    add(
        store,
        "# Release plan\n\n- Dee approved the release by mail.\n",
        via=via("google_drive"),
    )
    for text in release[5:]:
        add(store, text)
    mixed = store.get_page(mail_project.id, "release-plan")
    assert len(mixed.claims) == 11
    _, plain = page_of(store, "drift-sync", FIRST, SECOND)
    events = len(store.list_audit(limit=100))

    model = Recorder()
    problems = []
    for page in (mail, mixed):
        result = arrange_page(
            store,
            mail_project.id,
            page.slug,
            model,
            price=PRICE,
            withhold=sources.withheld_via(),
        )
        assert (result.status, result.version, result.previous) == (
            "withheld",
            page.version,
            page.version,
        )
        assert (result.moved, result.merged, result.draft.attempts) == (0, 0, 0)
        assert store.get_page(mail_project.id, page.slug) == page
        problems.append(result.problem)
        assert result.problem and page.title not in result.problem
        for claim in page.claims:
            assert claim.text not in result.problem
        for name in GOOGLE:
            assert via(name) not in result.problem
    assert problems[0] == problems[1]  # one fixed sentence
    assert model.requests == []
    used = store.usage()
    assert (used.model_tokens_in, used.model_tokens_out, used.model_micros) == (0, 0, 0)
    assert len(store.list_audit(limit=100)) == events

    result = arrange_page(
        store, mail_project.id, "drift-sync", model, withhold=sources.withheld_via()
    )
    assert result.status == "arranged" and len(model.requests) == 1
    assert plain.claims[1].text in told(model.requests)
    never_sent(model.requests, mail)
    never_sent(model.requests, mixed)

    everything = Recorder()  # withhold=(), the default: every page is sent, as before
    for page in (mail, mixed):
        assert arrange_page(store, mail_project.id, page.slug, everything).status == "arranged"
        assert all(claim.text in told(everything.requests) for claim in page.claims)
    assert arrange_page(store, mail_project.id, "drift-sync", everything, withhold=()).status == (
        "arranged"
    )
    assert len(everything.requests) == 3


def test_withhold_is_the_rule_whatever_via_values_it_names(store):
    project, meet = page_of(store, "hiring-loop", MEET_ONE, MEET_TWO, via=via("google_meet"))
    _, granola = page_of(store, "drift-sync", FIRST, SECOND, via=via("granola"), project="alpha")
    model = Recorder()
    only_granola = {via("granola")}
    said = arrange_page(store, project.id, "drift-sync", model, withhold=only_granola)
    assert said.status == "withheld" and model.requests == []
    assert arrange_page(store, project.id, "hiring-loop", model, withhold=only_granola).status == (
        "arranged"
    )
    assert meet.claims[1].text in told(model.requests)
    never_sent(model.requests, granola)


def test_a_withheld_note_folded_into_an_ordinary_claim_still_withholds_its_page(store):
    project, _ = page_of(store, "drift-sync", FIRST)
    add(store, "# Drift sync\n\nDrift mirrors two buckets.\n", via="gmail")
    assert tidy_page(store, project.id, "drift-sync").status == "arranged"
    folded = store.get_page(project.id, "drift-sync")
    first = folded.claims[0]
    assert first.text == "Drift mirrors two buckets." and len(first.span_ids) == 2
    model = Recorder()
    result = arrange_page(store, project.id, "drift-sync", model, withhold=sources.withheld_via())
    assert result.status == "withheld" and model.requests == []


def test_a_person_s_note_is_judged_by_its_via(store):
    memory = Memory(store, lint_gate)
    store.create_project("alpha")
    titles = {"gmail": "Mailed plan", None: "Typed plan", "web": "Web plan", "api": "Api plan"}
    for said, title in titles.items():
        for n in range(2):
            memory.note("alpha", f"# {title}\n\n- {title} line {n} for Ada.\n", via=said)
    project = store.get_project("alpha")
    model = Recorder()
    statuses = {
        said: arrange_page(
            store,
            project.id,
            title.lower().replace(" ", "-"),
            model,
            withhold=sources.withheld_via(),
        ).status
        for said, title in titles.items()
    }
    assert statuses == {"gmail": "withheld", None: "arranged", "web": "arranged", "api": "arranged"}
    assert "Mailed plan" not in told(model.requests)


def test_only_the_notes_the_page_still_cites_count(store):
    project, _ = page_of(store, "drift-sync", FIRST)
    mailed = add(store, "# Drift sync\n\n- Dee mailed the bucket keys.\n", via="gmail")
    typed = add(store, SECOND)
    model = Recorder()
    store.forget_note(typed.id)  # an ordinary note goes: the mailed one is still cited
    result = arrange_page(store, project.id, "drift-sync", model, withhold=sources.withheld_via())
    assert result.status == "withheld" and model.requests == []

    store.forget_note(mailed.id)  # the mailed note goes: the page is ordinary again
    page = store.get_page(project.id, "drift-sync")
    assert "Dee mailed the bucket keys." not in [claim.text for claim in page.claims]
    result = arrange_page(store, project.id, "drift-sync", model, withhold=sources.withheld_via())
    assert result.status == "arranged" and len(model.requests) == 1


def test_another_workspace_s_notes_never_withhold_this_one_s_page(store):
    other = store.for_account(store.create_account("Other").id)
    there, _ = page_of(other, "drift-sync", FIRST, via="gmail")
    page_of(other, "drift-sync", SECOND)
    project, _ = page_of(store, "drift-sync", FIRST, SECOND)
    model = Recorder()
    here = arrange_page(store, project.id, "drift-sync", model, withhold=sources.withheld_via())
    assert here.status == "arranged" and len(model.requests) == 1
    assert arrange_page(
        other, there.id, "drift-sync", model, withhold=sources.withheld_via()
    ).status == ("withheld")
    assert len(model.requests) == 1


# The worker


def test_the_worker_tidies_a_withheld_page_and_arranges_the_others(store):
    meet = via("google_meet")
    hiring, before = page_of(store, "hiring-loop", MEET_ONE, MEET_TWO, via=meet)
    assert len(before.claims) == 7
    drift, _ = page_of(store, "drift-sync", FIRST, project="beta")
    add(store, SECOND, project="beta")  # a second note on the ordinary page beside it

    twin = store.for_account(store.create_account("Twin").id)
    twin_project, _ = page_of(twin, "hiring-loop", MEET_ONE, MEET_TWO, via=meet)
    assert tidy_page(twin, twin_project.id, "hiring-loop").status == "arranged"
    tidied = twin.get_page(twin_project.id, "hiring-loop")

    def view_for(account_id):
        return store.for_account(account_id).limited(Limits(model_budget_micros=1_000_000))

    model = Recorder()
    arranger = Arranger(model, price=PRICE, view_for=view_for)
    arranger.queue(store.account_id, hiring.id, "hiring-loop")
    arranger.queue(store.account_id, drift.id, "drift-sync")
    done = asyncio.run(arranger.run_once())

    assert [(d.slug, d.status) for d in done] == [
        ("hiring-loop", "withheld"),
        ("drift-sync", "arranged"),
    ]
    assert done[0].draft is None
    assert len(model.requests) == 1  # the ordinary page's, and nothing else
    never_sent(model.requests, before)
    after = store.get_page(hiring.id, "hiring-loop")
    assert [claim.text for claim in after.claims] == [claim.text for claim in tidied.claims]
    assert after.body == tidied.body and len(after.claims) == 6  # the repeat folded
    assert after.metrics["arranged_by"] == TIDY
    assert store.get_page(drift.id, "drift-sync").metrics["arranged_by"] == model.name
    used = store.usage()
    assert (used.model_tokens_in, used.model_tokens_out, used.model_micros) == (300, 40, 500)

    arranger.queue(store.account_id, hiring.id, "hiring-loop")  # no new note: seen
    again = asyncio.run(arranger.run_once())
    assert [(d.slug, d.status) for d in again] == [("hiring-loop", "seen")]
    assert len(model.requests) == 1 and arranger.pending() == []
    assert store.get_page(hiring.id, "hiring-loop") == after


def test_each_new_version_of_a_withheld_page_is_looked_at_again(store, caplog):
    project, _ = page_of(store, "drift-sync", FIRST, SECOND)
    mailed = add(store, "# Drift sync\n\n## Limits\n\n- Dee mailed the bucket keys.\n", via="gmail")
    model = Recorder()
    arranger = Arranger(model, price=None, view_for=store.for_account)

    def look() -> list[str]:
        arranger.queue(store.account_id, project.id, "drift-sync")
        return [d.status for d in asyncio.run(arranger.run_once())]

    with caplog.at_level(logging.INFO, logger="cairn.access"):
        assert look() == ["withheld"]
        assert len(store.get_page(project.id, "drift-sync").claims) == 6  # the repeat folded
        assert len(arrange_records(caplog)) == 1

        add(
            store,
            "# Drift sync\n\n## Flags\n\n- `--delete` removes extra files.\n"
            "- Ada owns the flags.\n",
        )
        assert len(store.get_page(project.id, "drift-sync").claims) == 8
        assert look() == ["withheld"]  # a new version: looked at again, and tidied
        assert len(store.get_page(project.id, "drift-sync").claims) == 7
        assert len(arrange_records(caplog)) == 2
        assert model.requests == []

        store.forget_note(mailed.id)  # the page is ordinary again: the model is asked
        assert look() == ["arranged"]
        assert len(model.requests) == 1
        assert "Dee mailed the bucket keys." not in told(model.requests)


def note(number: int) -> str:
    lines = "\n".join(
        f"- Shelf {number} keeps crate {number * 10 + n} on rack {n}." for n in range(7)
    )
    return f"# Page {number:02d}\n\n{lines}\n"


def test_a_withheld_page_neither_adds_to_nor_clears_the_count_towards_a_pause(store):
    project, _ = page_of(store, "page-01", note(1))
    page_of(store, "hiring-loop", MEET_ONE, MEET_TWO, via=via("google_calendar"))
    page_of(store, "page-02", note(2))
    page_of(store, "offer-letter", MAIL_ONE, MAIL_TWO, via="gmail")
    model = Recorder(prose=True)  # every answer it gives is unusable
    arranger = Arranger(
        model,
        price=None,
        view_for=store.for_account,
        pause_after=3,
        clock=lambda: 1000.0,
    )
    for slug in ("page-01", "hiring-loop", "page-02", "offer-letter"):
        arranger.queue(store.account_id, project.id, slug)
    done = asyncio.run(arranger.run_once())
    assert [d.status for d in done] == ["invalid", "withheld", "invalid", "withheld"]
    assert arranger.unusable == 2 and not arranger.paused()
    assert len(model.requests) == 4  # two tries for each ordinary page


def test_a_withheld_page_is_one_line_on_the_access_log_with_no_text(store, caplog):
    hiring, page = page_of(store, "hiring-loop", MEET_ONE, MEET_TWO, via=via("google_drive"))
    drift, _ = page_of(store, "drift-sync", FIRST, SECOND, project="beta")
    model = Recorder()
    arranger = Arranger(model, price=None, view_for=store.for_account)

    with caplog.at_level(logging.INFO, logger="cairn.access"):
        arranger.queue(store.account_id, hiring.id, "hiring-loop")
        asyncio.run(arranger.run_once())
        (line,) = arrange_records(caplog)
        assert line.levelno == logging.INFO and line.name == "cairn.access"
        said = line.getMessage()
        assert "withheld" in said and hiring.id in said
        assert "hiring-loop" not in said and page.title not in said
        for claim in page.claims:
            assert claim.text not in said and claim.text not in caplog.text

        caplog.clear()
        arranger.queue(store.account_id, drift.id, "drift-sync")
        asyncio.run(arranger.run_once())
        (ordinary,) = arrange_records(caplog)
        assert "arranged" in ordinary.getMessage()
        fields = ("actor_kind", "actor", "actor_id", "action", "project", "status")
        assert {f: getattr(line, f, None) for f in fields} == {
            f: getattr(ordinary, f, None) for f in fields
        }

        caplog.clear()
        arranger.queue(store.account_id, hiring.id, "hiring-loop")  # the same version again
        asyncio.run(arranger.run_once())
        assert arrange_records(caplog) == []
    assert len(model.requests) == 1


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
    monkeypatch.setenv("ARRANGER_MODEL", "m2-endpoint-model")
    monkeypatch.delenv("ARRANGER_SOURCE_NOTES", raising=False)
    yield Endpoint.bodies
    httpd.shutdown()
    httpd.server_close()
    thread.join(5)


@pytest.fixture
def db(tmp_path):
    """A database with an ordinary page and a page of mail, six claims each, in one project."""
    path = tmp_path / "cairn.db"
    opened = Store(path)
    project, _ = page_of(opened, "drift-sync", FIRST, SECOND)
    _, mail = page_of(opened, "offer-letter", MAIL_ONE, MAIL_TWO, via="gmail")
    pages = (project.id, mail)
    opened.close()
    return path, pages


SETTINGS = {"unset": None, "blank": "", "withhold": "withhold", "send": "send"}


def set_notes(monkeypatch, value) -> None:
    if value is None:
        monkeypatch.delenv("ARRANGER_SOURCE_NOTES", raising=False)
    else:
        monkeypatch.setenv("ARRANGER_SOURCE_NOTES", value)


@pytest.mark.parametrize("setting", list(SETTINGS))
def test_arrange_withholds_unless_the_operator_sends(db, endpoint, monkeypatch, capsys, setting):
    path, (_, mail) = db
    set_notes(monkeypatch, SETTINGS[setting])
    argv = ["arrange", "--db", str(path), "--account", "acct_default", "--model-env", "ARRANGER"]
    assert server.main(argv) == 0
    lines = capsys.readouterr().out.splitlines()
    (drift,) = [line for line in lines if line.startswith("alpha/drift-sync: ")]
    (offer,) = [line for line in lines if line.startswith("alpha/offer-letter: ")]
    assert drift.startswith("alpha/drift-sync: arranged")
    if setting == "send":
        assert offer.startswith("alpha/offer-letter: arranged") and len(endpoint) == 2
        assert all(claim.text in told(endpoint) for claim in mail.claims)
    else:
        assert offer.startswith("alpha/offer-letter: withheld") and "tokens" not in offer
        assert len(endpoint) == 1
        never_sent(endpoint, mail)


@pytest.mark.parametrize("setting", list(SETTINGS))
def test_serve_s_worker_withholds_unless_the_operator_sends(db, endpoint, monkeypatch, setting):
    path, (project_id, mail) = db
    set_notes(monkeypatch, SETTINGS[setting])
    passes = []

    def run(app, **options):  # inside serve, while its database is open
        arranger = app.state.arranger
        for slug in ("drift-sync", "offer-letter"):
            arranger.queue("acct_default", project_id, slug)
        passes.append({d.slug: d.status for d in asyncio.run(arranger.run_once())})

    monkeypatch.setattr(uvicorn, "run", run)
    assert server.main(["serve", "--db", str(path), "--model-env", "ARRANGER"]) == 0
    (statuses,) = passes
    assert statuses["drift-sync"] == "arranged"
    if setting == "send":
        assert statuses["offer-letter"] == "arranged" and len(endpoint) == 2
        assert all(claim.text in told(endpoint) for claim in mail.claims)
    else:
        assert statuses["offer-letter"] == "withheld" and len(endpoint) == 1
        never_sent(endpoint, mail)


@pytest.mark.parametrize("command", ["serve", "arrange"])
def test_any_other_value_exits_2_before_anything_is_opened(
    tmp_path, endpoint, monkeypatch, capsys, command
):
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("serve started"))
    monkeypatch.setenv("ARRANGER_SOURCE_NOTES", "maybe")
    path = tmp_path / "new.db"
    argv = [command, "--db", str(path), "--model-env", "ARRANGER"]
    if command == "arrange":
        argv += ["--account", "acct_default"]
    assert server.main(argv) == 2
    printed = capsys.readouterr()
    assert printed.err.count("\n") == 1 and printed.err.endswith("\n")
    assert "ARRANGER_SOURCE_NOTES" in printed.err
    assert "withhold" in printed.err and "send" in printed.err
    assert "maybe" not in printed.err and printed.out == ""
    assert not path.exists() and endpoint == []


def test_without_a_model_the_setting_is_not_read(db, endpoint, monkeypatch, capsys):
    path, _ = db
    monkeypatch.setenv("ARRANGER_SOURCE_NOTES", "maybe")
    monkeypatch.setenv("CAIRN_SOURCE_NOTES", "maybe")
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    for argv in ([], ["--tidy"], ["--no-tidy"]):
        assert server.main(["serve", "--db", str(path), *argv]) == 0
    default, tidy, off = (app.state.arranger for app in apps)
    assert default.model is None and tidy.model is None and off is None
    tidied = ["arrange", "--db", str(path), "--account", "acct_default", "--tidy"]
    assert server.main(tidied) == 0
    printed = capsys.readouterr()
    others = [line for line in printed.err.splitlines() if not line.startswith("cairn.audit: ")]
    assert others == [] and "alpha/offer-letter: " in printed.out  # stderr: audit lines only
    assert endpoint == []


@pytest.mark.parametrize("command", ["serve", "arrange"])
def test_the_help_names_the_setting(capsys, command):
    with pytest.raises(SystemExit) as shown:
        server.main([command, "--help"])
    assert shown.value.code == 0
    said = " ".join(capsys.readouterr().out.split())
    assert "_SOURCE_NOTES" in said
