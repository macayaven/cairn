"""M4 acceptance tests: a project its people keep from any model.

Written before implementation. Implementers must not edit this file once it is frozen. The
contract goes in the docstrings of src/cairn/store/__init__.py (the store's contract: the
switch, who changes it, where it is kept), src/cairn/arrange.py (arrange_page and the worker),
src/cairn/web/ui.py (the project's page and its form), src/cairn/web/rest.py (the address) and
the guides (privacy.md's **Models** line, sources.md's sentence below the table of forms,
privacy.md's Export section). tests/acceptance/test_m2_withheld_notes.py,
test_m3_notes_that_name_a_source.py, test_t2_project_sharing.py and test_q1_bounded_reads.py
stay as they are and still pass.

Why: when an operator connects a model, it is sent a page's title and its claims as stored.
Two rules keep some pages from it (M2, M3), and both go by what a note says about itself: its
`via`, or its `source_ref`. An agent can copy text from anywhere into a note that names no
source, and that text is sent. Both rules are the operator's to switch off with one setting,
and on a hosted service the operator is someone else. The people who own a project had no way
to say "this project never goes to a model". Now they have one switch per project.

The switch
- A project's pages may be sent to a connected model ("allowed", the default, as before), or
  they are never sent ("never"). `view.set_project_models(project_id, models)` sets it:
  ValueError for a value other than "allowed" or "never", before anything is read; KeyError
  for a project unknown or out of reach; PermissionError for anyone who may not change who
  reaches the project. Setting the state a project already has writes nothing.
  `view.kept_from_models(project_ids)` gives the ids among those, in the view's reach, whose
  project is at "never".
- Who may change it: whoever may change who reaches the project (T2): an owner of the
  workspace, the project's maker, a person at "manage" on it, and a view that acts for no
  person (the command line, a key made on it). Nobody else: a workspace admin who is not one
  of those may not. A credential acts for the person who made it, as the access rule has it,
  and is refused on any connection limited to some projects or to reading. Over MCP nothing
  changes it: no tool takes it.
- A change is one audit event, "project.models_changed", in the change's own transaction: who
  made it (the actor, and in the detail the person it acts for, as every event of a person's
  view names them as "user"), the project (by name, as every event of a project is filed, and
  by id in the detail) and the new state: detail {"project_id", "models"}, never a note's
  text.
- It is kept in the workspace's settings, with no change to the schema (still 26). A deleted
  project leaves nothing of it there. An export carries no setting: an import starts each
  project it brings as a new one of its kind starts, in the import's own transaction, so a
  project of a private kind is at "never" and every other at "allowed".

Stronger than the operator's setting
- A page of a project at "never" is never sent, whatever its notes say and whatever
  `<PREFIX>_SOURCE_NOTES` says: `send` (`withhold=()`) does not send it. Everything M2 says
  of a withheld page holds: no request is built and no call made, nothing is reserved or
  spent, nothing is added to or taken off the count towards a pause, the worker tidies it
  without the model and remembers it, and no log line or outcome names a project's name, a
  title or a note's text. One exception: switching to "never" rewrites no page, so a version
  a model put in order (its `arranged_by` names a model) is not tidied; the worker remembers
  it as it is. The version a later note makes is tidied.
- Its outcome has a name of its own, "kept from models", so an operator reading the worker's
  log or `arrange`'s lines tells it from "withheld"; `arrange_page`'s `problem` is
  `cairn.arrange.KEPT`, one fixed sentence that is not M2's WITHHELD. The check comes right
  after "gone", before anything else, so a page of such a project that also holds a withheld
  note is "kept from models".
- The check costs no statement of its own: it is learned in the read that finds the page. A
  page of a project at "never" costs arrange_page what reading the page costs and nothing
  more, and a workspace's pages cost arrange_page and the worker the same statements whether
  or not another project of the workspace is at "never".

Where people see and change it
- The project's page, beside who reaches it, says the state in one sentence and, for whoever
  may change it, holds a form posting `models` ("allowed" or "never") to
  /b/<project>/models, which answers 303 back to the project's page; everyone else sees the
  state and no form. A value the store refuses is 400, someone who may not is 403, a project
  out of reach 404, a form from another site 403, and none of these changes anything.
- REST: each project of GET /api/projects carries `models`, "allowed" or "never", and so does
  POST /api/projects's answer (MCP `projects` and `new_project` share that representation, as
  test_u1_setup.py has it). POST /api/projects/<project>/models with {"models": ...} changes
  it and answers {"project", "models"}; another value, or a key the address does not take, is
  400 (decision 112), as is a body that is no object.

A start for private projects
- A project whose kind makes it private (cairn.kinds: "personal") starts at "never", wherever
  it is made: the web form, the guided setup, REST and MCP. Its page says so. Whoever may change
  it can switch it back, and a model then arranges it. Every other kind starts at "allowed".

Every name here is made up.
"""

import asyncio
import base64
import hashlib
import html
import io
import json
import logging
import re
import secrets
import sqlite3
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.arrange import KEPT, TIDY, WITHHELD, Arranger, Price, arrange_page, tidy_page
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.llm import Reply
from cairn.server import create_app
from cairn.store import SCHEMA_VERSION, Limits, Store
from cookie_names import session_cookie

BASE = "https://cairn.example.test"
ISSUER = "https://id.example.test/pool-m4"
REDIRECT = "http://127.0.0.1:33418/callback"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
PRICE = Price(input_per_million=1_000_000, output_per_million=5_000_000)  # $1 and $5
QUIET = {"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE"}  # not counted

# What the project's page says, word for word.
ALLOWED_SAID = (
    "Models: allowed. A model the operator connects can be sent this project's pages, to put"
    " their claims in order."
)
NEVER_SAID = (
    "Models: never. No model is sent this project's pages, whatever their notes say or the"
    " operator set. Pages already arranged stay as they are."
)
PERSONAL_SAID = "Personal notes start this way."
UNDECIDED_SAID = "Models: not decided. Nobody has chosen for this project"  # Privacy 4

# Six claims each, the first line twice, so a tidy folds it.
LAUNCH_ONE = (
    "# Launch plan\n\nHeron ships in March.\n\n## Steps\n\n"
    "- Freeze the build on the first.\n- Tag the release candidate.\n"
)
LAUNCH_TWO = (
    "# Launch plan\n\nHeron ships in March.\n\n## Steps\n\n"
    "- Run the soak test for two days.\n\n## Owners\n\n- Ivo signs off the launch.\n"
)
HANDBOOK_ONE = (
    "# Release rules\n\nReleases go out on Tuesdays.\n\n## Checks\n\n"
    "- Every release has a changelog.\n- Every release names an owner.\n"
)
HANDBOOK_TWO = (
    "# Release rules\n\nReleases go out on Tuesdays.\n\n## Checks\n\n"
    "- A failed check stops the release.\n\n## Rollback\n\n- Rollback takes ten minutes.\n"
)


def address(name: str) -> str:
    return f"{name.lower()}@example.test"


def visible(page: str) -> str:
    """A web page's text as a person reads it: no tags, entities read, blanks folded."""
    text = re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", page)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())


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


def answer_for(messages, *, keep: bool) -> dict:
    """An arrangement read from the request alone: the claim numbers as given (`keep`) or
    reversed, less the later claims of each listed group of repeats, with those groups."""
    user = "\n".join(m["content"] for m in messages if m["role"] != "system")
    groups = listed(user)
    folded = {n for group in groups for n in group[1:]}
    numbers = [int(n) for n in re.findall(rf'<{tag_of(user)} n="(\d+)"', user)]
    order = numbers if keep else list(reversed(numbers))
    return {"order": [n for n in order if n not in folded], "same": groups}


class Recorder:
    """A model that records every request and answers each page with its claims reversed, in
    the order given (`keep`), or in prose (not an arrangement) when `prose` is set."""

    name = "m4-recorder"

    def __init__(self, *, prose: bool = False, keep: bool = False) -> None:
        self.prose, self.keep = prose, keep
        self.requests: list[list[dict]] = []

    def complete(self, messages) -> Reply:
        self.requests.append([dict(message) for message in messages])
        if self.prose:
            return Reply(text="A better order, in words.", prompt_tokens=100, completion_tokens=9)
        said = answer_for(messages, keep=self.keep)
        return Reply(text=json.dumps(said), prompt_tokens=300, completion_tokens=40)


def arrange_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "action", None) == "arrange"]


class Watch:
    """The statements the store's connection runs during a call (its trace callback), less
    transaction control and what SQLite runs inside a statement, as test_q1_bounded_reads.py
    counts them."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def count(self, call) -> int:
        seen: list[str] = []

        def traced(statement: str) -> None:
            text = statement.lstrip()
            if not text or text.startswith("--"):
                return
            if text.split(None, 1)[0].upper().rstrip(";") in QUIET:
                return
            seen.append(text)

        self.store._observe(trace=traced)
        try:
            call()
        finally:
            self.store._observe(trace=None)
        return len(seen)


class World:
    """One shared workspace (the default one), served in process. Ada signs in first and owns
    it; Brook is an admin; Cy, Dee, Eli and Fay are members. Cy made "launch", private, with
    Dee at "manage" and Fay at "write"; Ada made "handbook", which every member writes. Each
    has a page of six claims (one of them twice, so a tidy folds it)."""

    def __init__(self, tmp_path) -> None:
        self.db = tmp_path / "cairn.db"
        self.store = Store(self.db)
        self.app = create_app(self.store, public_url=BASE)
        self.client = TestClient(self.app, base_url=BASE)
        self.client.__enter__()
        self.operator = self.store.for_account(self.store.account_id)  # acts for no person
        self.ada, self.brook, self.cy, self.dee, self.eli, self.fay = (
            self.person(name) for name in ("Ada", "Brook", "Cy", "Dee", "Eli", "Fay")
        )
        self.ada.view.set_role(self.brook.id, "admin")
        self.handbook = self.ada.view.create_project("handbook")
        self.launch = self.cy.view.create_project("launch", everyone="none")
        self.cy.view.set_project_access(
            self.launch.id, people={self.dee.id: "manage", self.fay.id: "write"}
        )
        for view, project, notes in (
            (self.ada.view, self.handbook, (HANDBOOK_ONE, HANDBOOK_TWO)),
            (self.cy.view, self.launch, (LAUNCH_ONE, LAUNCH_TWO)),
        ):
            for text in notes:
                self.add(view, project, text)

    def person(self, name: str) -> SimpleNamespace:
        signed = self.store.sign_in(
            "sso", f"sub-{name}", address(name), name, issuer=ISSUER, join="default"
        )
        return SimpleNamespace(
            id=signed.user.id,
            name=name,
            cookie={"cookie": f"{session_cookie('https')}={signed.token}"},
            view=self.store.for_account(signed.account_id, signed.user.id),
        )

    def add(self, view, project, text: str, *, via: str | None = None) -> None:
        note, _ = view.add_note(project.id, text, via=via)
        assert compile_note(view, note, gate=lint_gate).status in ("saved", "unchanged")

    def agent(self, who: SimpleNamespace, name: str = "laptop") -> str:
        """An agent `who` approved, as an access token."""
        client = self.store.register_client(name, [REDIRECT])
        verifier = secrets.token_urlsafe(32)
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        code = who.view.create_code(client.id, REDIRECT, challenge)
        return self.store.redeem_code(code, client.id).access_token

    def page(self, project, slug: str):
        return self.store.get_page(project.id, slug)

    def state(self, project) -> str:
        kept = self.operator.kept_from_models([project.id])
        return "never" if project.id in kept else "allowed"

    def changes(self) -> list:
        """The project.models_changed events of the workspace, newest first."""
        events = self.operator.list_audit(limit=500)
        return [event for event in events if event.action == "project.models_changed"]

    def post(self, who, ref: str, models: str, headers=SAME_ORIGIN):
        return self.client.post(
            f"/b/{ref}/models",
            data={"models": models},
            headers={**headers, **who.cookie},
            follow_redirects=False,
        )

    def rest(self, key: str, ref: str, body) -> object:
        path = f"/api/projects/{ref}/models"
        return self.client.post(path, headers={"x-api-key": key}, json=body)

    def mcp(self, credential: dict, tool: str, /, **arguments) -> dict:
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
        r = self.client.post("/mcp", headers={**MCP_HEADERS, **credential}, json=body)
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        assert result["isError"] is False, result
        return json.loads(result["content"][0]["text"])

    def close(self) -> None:
        self.client.__exit__(None, None, None)
        self.store.close()


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path)
    yield made
    made.close()


def worker(world, model, **options) -> Arranger:
    def view_for(account_id):
        return world.store.for_account(account_id).limited(Limits(model_budget_micros=1_000_000))

    return Arranger(model, price=PRICE, view_for=view_for, **options)


# Stronger than the operator's setting


@pytest.mark.parametrize("withhold", [None, ()], ids=["default", "send"])
def test_a_project_at_never_has_no_page_in_any_request_while_the_one_beside_it_is_arranged(
    world, withhold
):
    launch, handbook = world.launch, world.handbook
    world.cy.view.set_project_models(launch.id, "never")
    before = world.page(launch, "launch-plan")
    assert len(before.claims) == 6
    model = Recorder()
    arranger = worker(world, model, withhold=withhold)
    arranger.queue(world.store.account_id, launch.id, "launch-plan")
    arranger.queue(world.store.account_id, handbook.id, "release-rules")
    done = asyncio.run(arranger.run_once())

    assert [(d.slug, d.status) for d in done] == [
        ("launch-plan", "kept from models"),
        ("release-rules", "arranged"),
    ]
    assert done[0].draft is None and done[0].previous == before.version
    assert len(model.requests) == 1  # the handbook's, and nothing else
    never_sent(model.requests, before)
    after = world.page(launch, "launch-plan")
    assert after.metrics["arranged_by"] == TIDY and len(after.claims) == 5  # tidied, folded
    assert done[0].version == after.version
    used = world.store.usage()
    assert (used.model_tokens_in, used.model_tokens_out, used.model_micros) == (300, 40, 500)

    arranger.queue(world.store.account_id, launch.id, "launch-plan")  # no new note: seen
    assert [d.status for d in asyncio.run(arranger.run_once())] == ["seen"]
    assert len(model.requests) == 1


def test_arrange_page_says_why_in_a_sentence_of_its_own_and_spends_nothing(world):
    launch = world.launch
    world.add(world.cy.view, launch, "# Launch plan\n\n- Ivo mailed the dates.\n", via="gmail")
    world.cy.view.set_project_models(launch.id, "never")
    page = world.page(launch, "launch-plan")
    events = len(world.operator.list_audit(limit=500))
    model = Recorder()
    for withhold in ((), frozenset({"gmail"})):  # sent by the operator, or a withheld note too
        result = arrange_page(
            world.store, launch.id, "launch-plan", model, price=PRICE, withhold=withhold
        )
        assert (result.status, result.version, result.previous) == (
            "kept from models",
            page.version,
            page.version,
        )
        assert (result.moved, result.merged, result.draft.attempts) == (0, 0, 0)
        assert result.problem == KEPT
    assert KEPT != WITHHELD and KEPT
    for words in (page.title, "launch", "gmail", *(claim.text for claim in page.claims)):
        assert words not in KEPT
    assert model.requests == []
    assert world.store.model_reservations() == []
    used = world.store.usage()
    assert (used.model_tokens_in, used.model_tokens_out, used.model_micros) == (0, 0, 0)
    assert len(world.operator.list_audit(limit=500)) == events
    assert world.page(launch, "launch-plan") == page


@pytest.mark.parametrize("withhold", [None, ()], ids=["default", "send"])
def test_a_page_a_model_put_in_order_stays_as_it_is_until_a_note_changes_it(world, withhold):
    launch = world.launch
    model = Recorder()
    assert arrange_page(world.store, launch.id, "launch-plan", model).status == "arranged"
    ordered = world.page(launch, "launch-plan")
    assert ordered.metrics["arranged_by"] == Recorder.name
    world.cy.view.set_project_models(launch.id, "never")
    arranger = worker(world, model, withhold=withhold)
    arranger.queue(world.store.account_id, launch.id, "launch-plan")
    (done,) = asyncio.run(arranger.run_once())
    assert (done.status, done.previous, done.version, done.draft) == (
        "kept from models",
        ordered.version,
        ordered.version,
        None,
    )
    assert world.page(launch, "launch-plan") == ordered  # not rewritten, not even tidied
    assert len(model.requests) == 1  # the one before the switch
    # A note that changes the page makes a version no model ordered: that one is tidied.
    world.add(world.cy.view, launch, "# Launch plan\n\n- Ivo books the hall.\n")
    arranger.queue(world.store.account_id, launch.id, "launch-plan")
    (done,) = asyncio.run(arranger.run_once())
    assert done.status == "kept from models" and len(model.requests) == 1
    assert world.page(launch, "launch-plan").metrics["arranged_by"] == TIDY


def test_a_small_page_of_a_project_at_never_is_tidied_and_never_sent(world):
    launch = world.launch
    world.add(world.cy.view, launch, "# Ship list\n\n- Pack the crates.\n\n- Pack the crates.\n")
    world.cy.view.set_project_models(launch.id, "never")
    model = Recorder()
    arranger = worker(world, model, withhold=())
    arranger.queue(world.store.account_id, launch.id, "ship-list")
    (done,) = asyncio.run(arranger.run_once())
    assert done.status == "kept from models" and model.requests == []
    assert world.page(launch, "ship-list").metrics["arranged_by"] == TIDY


def test_a_page_kept_from_models_neither_adds_to_nor_clears_the_count_towards_a_pause(world):
    launch, handbook = world.launch, world.handbook
    world.add(world.ada.view, handbook, "# Shelf one\n\n" + "".join(
        f"- Shelf one keeps crate {n}.\n" for n in range(7)
    ))  # fmt: skip
    world.cy.view.set_project_models(launch.id, "never")
    model = Recorder(prose=True)  # every answer it gives is unusable
    arranger = Arranger(
        model, price=None, view_for=world.store.for_account, pause_after=3, clock=lambda: 1000.0
    )
    for project, slug in (
        (handbook, "release-rules"),
        (launch, "launch-plan"),
        (handbook, "shelf-one"),
    ):
        arranger.queue(world.store.account_id, project.id, slug)
    done = asyncio.run(arranger.run_once())
    assert [d.status for d in done] == ["invalid", "kept from models", "invalid"]
    assert arranger.unusable == 2 and not arranger.paused()


def test_a_page_kept_from_models_is_one_line_on_the_access_log_with_no_name_or_text(world, caplog):
    launch = world.launch
    world.cy.view.set_project_models(launch.id, "never")
    page = world.page(launch, "launch-plan")
    arranger = Arranger(Recorder(), price=None, view_for=world.store.for_account)
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        arranger.queue(world.store.account_id, launch.id, "launch-plan")
        asyncio.run(arranger.run_once())
        (line,) = arrange_records(caplog)
        said = line.getMessage()
        assert line.levelno == logging.INFO and "kept from models" in said and launch.id in said
        assert "withheld" not in said
        for words in ("launch", page.title, *(claim.text for claim in page.claims)):
            assert words not in said and words not in caplog.text


# What it costs


def test_the_check_costs_no_statement_of_its_own(world):
    launch, handbook = world.launch, world.handbook
    world.add(world.cy.view, launch, "# Launch plan\n\n## Owners\n\n- Ada books the room.\n")
    assert tidy_page(world.store, launch.id, "launch-plan").status == "arranged"
    assert len(world.page(launch, "launch-plan").claims) == 6  # no repeat left, worth a call
    watch = Watch(world.store)
    model = Recorder(keep=True)  # the page's own order: "unchanged", so a call can be repeated
    world.ada.view.set_project_models(handbook.id, "never")
    world.ada.view.set_project_models(handbook.id, "allowed")  # nothing at "never" now

    def judged() -> str:
        return arrange_page(world.store, launch.id, "launch-plan", model).status

    def pass_over() -> list[str]:
        arranger = Arranger(model, price=None, view_for=world.store.for_account)
        arranger.queue(world.store.account_id, launch.id, "launch-plan")
        return [d.status for d in asyncio.run(arranger.run_once())]

    assert judged() == "unchanged" and pass_over() == ["unchanged"]
    alone = (watch.count(judged), watch.count(pass_over))
    world.ada.view.set_project_models(handbook.id, "never")  # another project, at "never"
    assert (watch.count(judged), watch.count(pass_over)) == alone

    world.cy.view.set_project_models(launch.id, "never")  # this page's own project
    asked = len(model.requests)
    read = watch.count(lambda: world.store.get_page(launch.id, "launch-plan"))
    assert judged() == "kept from models"
    assert watch.count(judged) == read  # the read that finds the page, and nothing more
    assert len(model.requests) == asked


# Who may change it


def test_the_people_who_decide_who_reaches_a_project_switch_it_on_its_page(world):
    launch, handbook = world.launch, world.handbook
    for who, models in ((world.ada, "never"), (world.cy, "allowed"), (world.dee, "never")):
        r = world.post(who, "launch", models)
        assert r.status_code == 303 and r.headers["location"] == "/b/launch", who.name
        assert world.state(launch) == models
    assert world.post(world.fay, "launch", "allowed").status_code == 403  # writes, decides nothing
    assert world.post(world.eli, "launch", "allowed").status_code == 404  # does not reach it
    assert world.post(world.brook, "handbook", "never").status_code == 403  # an admin, no more
    assert world.post(world.eli, "handbook", "never").status_code == 403
    assert world.post(world.ada, "nowhere", "never").status_code == 404
    assert world.post(world.ada, "launch", "maybe").status_code == 400
    assert world.post(world.ada, "launch", "").status_code == 400
    crossed = world.post(world.ada, "launch", "allowed", headers={"sec-fetch-site": "cross-site"})
    assert crossed.status_code == 403
    # Signed out: answered as the neighbouring form is (here, sent to sign in), nothing written.
    out = world.post(SimpleNamespace(cookie={}), "launch", "allowed")
    beside = world.client.post(
        "/b/launch/access", data={"everyone": "write"}, headers=SAME_ORIGIN, follow_redirects=False
    )
    assert (out.status_code, out.headers.get("location")) == (303, "/login")
    assert (beside.status_code, beside.headers.get("location")) == (303, "/login")
    assert (world.state(launch), world.state(handbook)) == ("never", "allowed")


def test_over_rest_a_key_acts_for_whoever_made_it_and_a_limited_one_changes_nothing(world):
    launch, handbook = world.launch, world.handbook
    ada, brook, dee = world.ada, world.brook, world.dee
    ada.view.set_role(dee.id, "admin")  # so she may make a key
    keys = {
        "owner": ada.view.create_key("ada-script"),
        "manage": dee.view.create_key("dee-script"),
        "admin": brook.view.create_key("brook-script"),
        "one project": ada.view.create_key("ada-launch", projects=[launch.id]),
        "read only": ada.view.create_key("ada-reader", access="read"),
        "operator": world.store.create_key("ops-desk"),  # made on the command line: no person
    }
    said = world.rest(keys["owner"], "launch", {"models": "never"})
    assert said.status_code == 200 and said.json() == {"project": "launch", "models": "never"}
    assert world.rest(keys["manage"], "launch", {"models": "allowed"}).json()["models"] == "allowed"
    assert world.rest(keys["operator"], "handbook", {"models": "never"}).status_code == 200
    assert world.rest(keys["admin"], "handbook", {"models": "allowed"}).status_code == 403
    assert world.rest(keys["admin"], "launch", {"models": "never"}).status_code == 404
    for limited in ("one project", "read only"):
        assert world.rest(keys[limited], "launch", {"models": "never"}).status_code == 403
    assert (world.state(launch), world.state(handbook)) == ("allowed", "never")

    # The same refusals on the store: a connection limited in any way changes nothing.
    for narrowed in (ada.view.narrow([launch.id]), ada.view.narrow(None, "read")):
        with pytest.raises(PermissionError):
            narrowed.set_project_models(launch.id, "never")
    with pytest.raises(PermissionError):
        brook.view.set_project_models(handbook.id, "allowed")
    with pytest.raises(ValueError):
        ada.view.set_project_models(launch.id, "sometimes")
    assert world.state(launch) == "allowed"


def test_rest_refuses_another_value_or_key_and_changes_nothing(world):
    key = world.ada.view.create_key("ada-script")
    for body, named in (
        ({"models": "sometimes"}, ("allowed", "never")),
        ({"models": None}, ("allowed", "never")),
        ({"models": True}, ("allowed", "never")),
        ({}, ("allowed", "never")),
        ({"models": "never", "send": False}, ("'send'", "'models'")),
    ):
        r = world.rest(key, "launch", body)
        assert r.status_code == 400, body
        error = r.json()["error"]
        assert all(word in error for word in named), (body, error)
        assert "sometimes" not in error
    r = world.client.post(
        "/api/projects/launch/models", headers={"x-api-key": key}, content=b"never"
    )
    assert r.status_code == 400
    assert world.rest(key, "nowhere", {"models": "never"}).status_code == 404
    assert world.state(world.launch) == "allowed" and world.changes() == []


def test_an_agent_connection_changes_nothing(world):
    token = world.agent(world.ada, "ada-laptop")
    bearer = {"authorization": f"Bearer {token}"}
    r = world.client.post("/api/projects/launch/models", headers=bearer, json={"models": "never"})
    assert r.status_code == 401  # REST takes a key, never an agent's token
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    tools = world.client.post("/mcp", headers={**MCP_HEADERS, **bearer}, json=body).json()
    for tool in tools["result"]["tools"]:
        assert "model" not in tool["name"]
        assert "models" not in tool["inputSchema"].get("properties", {}), tool["name"]
    assert world.state(world.launch) == "allowed"


# The audit log


def test_each_change_is_one_audit_entry_naming_who_the_project_and_the_new_state(world):
    launch = world.launch
    assert world.post(world.dee, "launch", "never").status_code == 303
    assert world.post(world.dee, "launch", "never").status_code == 303  # no change: no entry
    key = world.ada.view.create_key("ada-script")
    assert world.rest(key, "launch", {"models": "allowed"}).status_code == 200
    by_key, by_dee = world.changes()
    assert (by_dee.actor_kind, by_dee.actor, by_dee.actor_id) == ("session", "Dee", world.dee.id)
    assert (by_key.actor_kind, by_key.actor) == ("key", "ada-script")
    for event, models, who in ((by_dee, "never", world.dee), (by_key, "allowed", world.ada)):
        assert event.project == "launch"
        assert event.detail == {"project_id": launch.id, "models": models, "user": who.id}
    for claim in world.page(launch, "launch-plan").claims:
        assert claim.text not in json.dumps([e.detail for e in world.changes()])
    # Filed under the project, as its other events are: a member who does not reach it sees none.
    assert [e for e in world.eli.view.list_audit(limit=500) if e.project == "launch"] == []


# The project's page


def test_the_project_s_page_says_the_state_and_offers_the_switch_to_whoever_may_change_it(world):
    form = 'action="/b/launch/models"'
    # Privacy 4: nobody chose yet, so the page says so and offers both choices.
    undecided = world.client.get("/b/launch", headers=world.ada.cookie)
    assert UNDECIDED_SAID in visible(undecided.text) and ALLOWED_SAID not in visible(undecided.text)
    assert 'name="models" value="allowed"' in undecided.text
    world.cy.view.set_project_models(world.launch.id, "allowed")
    for who in (world.ada, world.cy, world.dee):
        shown = world.client.get("/b/launch", headers=who.cookie)
        assert shown.status_code == 200
        assert ALLOWED_SAID in visible(shown.text) and form in shown.text, who.name
        assert 'name="models" value="never"' in shown.text
    reader = world.client.get("/b/launch", headers=world.fay.cookie)
    assert ALLOWED_SAID in visible(reader.text) and form not in reader.text

    world.cy.view.set_project_models(world.launch.id, "never")
    owner = world.client.get("/b/launch", headers=world.ada.cookie)
    assert NEVER_SAID in visible(owner.text) and ALLOWED_SAID not in visible(owner.text)
    assert 'name="models" value="allowed"' in owner.text
    assert PERSONAL_SAID not in visible(owner.text)  # launch is no personal project
    reader = world.client.get("/b/launch", headers=world.fay.cookie)
    assert NEVER_SAID in visible(reader.text) and form not in reader.text

    # The sentence sits beside who reaches the project.
    text = visible(owner.text)
    assert text.index("Private") < text.index(NEVER_SAID) < text.index("Belongs here")


# A start for private projects


def test_a_personal_project_starts_at_never_wherever_it_is_made_and_can_be_switched_back(world):
    cy = world.cy
    made = world.client.post(
        "/projects",
        data={"name": "diary", "kind": "personal"},
        headers={**SAME_ORIGIN, **cy.cookie},
        follow_redirects=False,
    )
    assert made.status_code == 303
    started = world.client.post(
        "/start",
        data={"use": "personal", "name-personal": "journal"},
        headers={**SAME_ORIGIN, **cy.cookie},
        follow_redirects=False,
    )
    assert started.status_code == 303
    key = world.ada.view.create_key("ada-script")
    rest = world.client.post(
        "/api/projects", headers={"x-api-key": key}, json={"name": "notebook", "kind": "personal"}
    )
    assert rest.status_code == 201 and rest.json()["models"] == "never"
    over_mcp = world.mcp({"x-api-key": key}, "new_project", name="logbook", kind="personal")
    assert over_mcp["models"] == "never"
    general = world.client.post(
        "/api/projects", headers={"x-api-key": key}, json={"name": "scratch"}
    )
    assert general.status_code == 201 and general.json()["models"] == "allowed"
    states = {
        name: world.state(world.store.get_project(name))
        for name in ("diary", "journal", "notebook", "logbook", "scratch")
    }
    assert states == {
        "diary": "never",
        "journal": "never",
        "notebook": "never",
        "logbook": "never",
        "scratch": "allowed",
    }
    listed = world.client.get("/api/projects", headers={"x-api-key": key}).json()["projects"]
    assert {p["name"]: p["models"] for p in listed} == {
        **states,
        "handbook": "allowed",
        "launch": "allowed",
    }
    assert world.mcp({"x-api-key": key}, "projects")["projects"] == listed

    page = world.client.get("/b/diary", headers=cy.cookie)
    assert NEVER_SAID + " " + PERSONAL_SAID in visible(page.text)

    diary = world.store.get_project("diary")
    world.add(cy.view, diary, LAUNCH_ONE)
    world.add(cy.view, diary, LAUNCH_TWO)
    model = Recorder()
    assert arrange_page(world.store, diary.id, "launch-plan", model).status == "kept from models"
    assert world.post(cy, "diary", "allowed").status_code == 303  # its maker switches it back
    assert world.state(diary) == "allowed"
    assert ALLOWED_SAID in visible(world.client.get("/b/diary", headers=cy.cookie).text)
    assert arrange_page(world.store, diary.id, "launch-plan", model).status == "arranged"
    assert len(model.requests) == 1


# Where it is kept


def test_it_is_kept_in_the_settings_and_goes_with_its_project(world):
    launch = world.launch
    world.cy.view.set_project_models(launch.id, "never")
    con = sqlite3.connect(world.db)
    try:
        (version,) = con.execute("PRAGMA user_version").fetchone()
        assert version == SCHEMA_VERSION == 31
        named = "SELECT COUNT(*) FROM settings WHERE instr(name, ?) > 0 OR instr(value, ?) > 0"
        assert con.execute(named, (launch.id, launch.id)).fetchone() == (1,)
        assert world.cy.view.delete_project(launch.id) is True
        assert con.execute(named, (launch.id, launch.id)).fetchone() == (0,)
    finally:
        con.close()


def test_an_import_brings_no_setting_and_each_project_starts_as_a_new_one_of_its_kind(
    world, tmp_path
):
    world.ada.view.set_project_models(world.handbook.id, "never")
    world.ada.view.create_project("diary", kind="personal")
    world.ada.view.set_project_models(world.store.get_project("diary").id, "allowed")
    out = io.BytesIO()
    world.ada.view.export(out)
    elsewhere = Store(tmp_path / "elsewhere.db")
    try:
        elsewhere.import_export(io.BytesIO(out.getvalue()))
        projects = {p.name: p for p in elsewhere.list_projects()}
        assert set(projects) == {"handbook", "launch", "diary"}
        kept = elsewhere.kept_from_models([p.id for p in projects.values()])
        # Not what the export's workspace had (handbook at never, diary at allowed): what a
        # new project of each kind starts at.
        assert kept == {projects["handbook"].id, projects["diary"].id}
    finally:
        elsewhere.close()
