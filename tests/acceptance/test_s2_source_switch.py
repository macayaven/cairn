"""S2 acceptance tests (frozen): a source can be off, and the workspace chooses where it writes.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/sources/__init__.py,
src/cairn/store/sources.py, src/cairn/web/cli.py and src/cairn/web/ui.py.

Why: built-in sources will read a company's meetings, mail, calendar and documents. Reading
them is a decision the workspace takes, not something that begins because an operator typed
a command. Decision 25 stays: credentials are the server's environment, and only the
operator adds or removes a source, on the command line. What changes is what happens after.

A kind can start off
- `SourceKind` gains `starts_off: bool = False`. Langfuse keeps the default: a Langfuse
  source is on when added, exactly as before.
- A source of a kind that starts off is recorded off by `add_source`. For such a kind the
  config may name no project yet (`"projects": []`) when the kind's `check` allows it; a source
  cannot be switched on while it names none.

On and off
- `SourceInfo` gains `enabled: bool` and `enabled_at: str | None`: an ISO 8601 UTC stamp of
  when it was last switched on; the stamp of `add_source` for a kind that starts on; None
  while it has never been on.
- `view.enable_source(name, projects=None) -> SourceInfo` switches the source on. With
  `projects` (project names) they replace the config's `projects` first: the kind's `check` runs
  on the config with the new list (ValueError as the kind says), and each project must exist,
  be in the caller's reach and be one the caller may write (ValueError "no project named ..."
  or PermissionError, as `add_source` does). Without `projects`, the config's own are kept and
  checked the same way. No project named at all: ValueError. Switching on a source that is
  already on changes its projects when given (audited as `source.enabled` with the new projects)
  and leaves `enabled_at` as it was.
- `view.disable_source(name) -> SourceInfo` switches it off. Its cursor, its last report
  and what it noted stay.
- Both: KeyError for no such source (by name, ignoring case); only a view acting for no
  person, an owner or an admin (PermissionError with the existing NOT_A_SOURCE_MANAGER text
  otherwise); refused (PermissionError) on a connection limited to some projects or to
  reading. Audited as `source.enabled` (detail: the source and its projects) and
  `source.disabled` (detail: the source), by the actor who did it. A call that changes
  nothing writes nothing, no audit event either. A refused call writes nothing.
- A source that is off is not read: `run_source` on it returns `SyncReport(off=True)` and
  nothing else set; it takes no claim, never calls the kind's `open`, moves no cursor,
  records no run and writes no audit event: the database is as it was. `sync_all` passes
  over it the same way. `SyncReport` gains `off: bool = False`.

Only what happens while it is on
- The runner gives the kind's `open` one more keyword argument, `since`: the source's
  `enabled_at` as an aware UTC datetime. A kind that starts off must read nothing that
  ended before `since` (each connector's job, tested with each connector). Here: `since` is
  what `open` receives, it is the moment of the last switch-on, and switching off and on
  again moves it. Switching on again keeps the cursor.

A draft can name its page
- `NoteDraft` gains `topic: str | None = None`, and the runner passes it to the note it
  writes, so two drafts with one topic land on one page. A draft with no topic behaves as
  before (its page is named by its title).

The command line (`cairn.server.main`)
- `source-add` is unchanged, and for a source recorded off it prints that it is off and how
  to switch it on (`source-enable`).
- `source-enable --db PATH --name NAME [--project B]... [--workspace ACCOUNT]` and
  `source-disable --db PATH --name NAME [--workspace ACCOUNT]`: exit 0 and one line on
  stdout saying the source's state and its projects; exit 1 with one line on stderr for no
  such source, no such project, a project it may not write, or a kind that refuses the projects;
  exit 1 for a missing database or an unknown workspace, as the other source commands.
- `source-list` shows, for each source, whether it is on or off.
- `source-sync --name NAME` on a source that is off: exit 0, one line saying it is off,
  nothing read.

The web view
- GET /sources (owners and admins, as before) shows each source's state in words ("on
  since <date>" or "off"). An off source has one form, which posts to
  /integrations/<name>/on. An on source has a form that posts to /integrations/<name>/off,
  and one that posts to /integrations/<name>/on to change where it writes. Beside an off
  source the page says that switching it on reads only what happens from then on. No
  credential is shown, nor any environment variable's value.
- POST /integrations/<name>/on (web session, same-origin, owner or admin; a member gets
  403; no session is sent to sign in, as for every page): fields `project` (repeatable: names
  of existing projects) and `new_project` (optional: a name). With `new_project`, a project of that
  name is made in the same request: private (nobody but its maker and the owners reach it),
  of the general kind, made by the person who posted, and it is the destination; a name that
  is taken or breaks the project-name rules answers 400 and switches nothing on. Then the
  source is switched on with those projects. 303 to /sources when it worked; 400 with the
  reason on the page when the kind or the store refuses; 404 for no such source.
- POST /integrations/<name>/off switches it off: 303 to /sources; 404 for no such source.
- No route whose path contains "/sources" accepts anything but GET (C2 stays).

What stays: the web view adds no source; an export holds no source.

Schema: version 20 adds what the store needs. A database from version 19 is migrated in
place: every source it holds is on, with `enabled_at` equal to its `created_at`.
"""

import asyncio
import html as html_text
import io
import json
import re
import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.server import create_app, main
from cairn.sources import KINDS, NoteDraft, Pulled, SourceKind, SyncReport, run_source, sync_all
from cairn.store import DEFAULT_ACCOUNT_ID, Store
from cairn.store.sources import NOT_A_SOURCE_MANAGER

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
ISSUER = "https://id.example.test/pool-1"
# Within one web session's life: people sign in at T0.
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)  # the source is added
T1 = datetime(2026, 11, 2, 10, 30, tzinfo=UTC)  # switched on
T2 = datetime(2026, 11, 2, 13, 15, tzinfo=UTC)
T3 = datetime(2026, 11, 2, 16, 45, tzinfo=UTC)
TOKEN_ENV = "MINUTES_TOKEN"
TOKEN = "mt-" + "s2-" + "made-up-" + "0f3a9c7e"  # never a real secret
LF = "https://langfuse.example.test"
PUBLIC, SECRET = "pk-lf-" + "s2-public", "sk-lf-" + "s2-" + "do-not-show"
LF_ENV = {"S2_LF_PUBLIC": PUBLIC, "S2_LF_SECRET": SECRET}
LF_CONFIG = {
    "base_url": LF,
    "projects": ["meetings"],
    "public_key_env": "S2_LF_PUBLIC",
    "secret_key_env": "S2_LF_SECRET",
}


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def moment(stamp: str | None) -> datetime | None:
    return None if stamp is None else datetime.fromisoformat(stamp.replace("Z", "+00:00"))


class Minutes:
    """A stand-in kind of source that starts off and writes into exactly one project, as a
    meetings connector would. `open` records the keyword arguments it was given; each pull
    records the cursor it started from and hands back the next batch the test queued, every
    draft addressed to the project the config names."""

    def __init__(self) -> None:
        self.opened: list[dict] = []
        self.cursors: list[str | None] = []
        self.batches: list[list[dict]] = []
        self.pulls = 0

    def check(self, config: object) -> dict:
        if not isinstance(config, dict) or set(config) - {"projects", "token_env"}:
            raise ValueError("a minutes source takes projects and token_env")
        projects = config.get("projects", [])
        if not isinstance(projects, list) or not all(isinstance(b, str) for b in projects):
            raise ValueError("projects lists the names of projects")
        if len(projects) > 1:
            raise ValueError("a minutes source writes into exactly one project")
        return {"projects": list(projects), "token_env": config.get("token_env", TOKEN_ENV)}

    def open(self, name: str, config: dict, **options) -> "MinutesSource":
        self.opened.append(options)
        return MinutesSource(name, config["projects"][0], self)

    def kind(self) -> SourceKind:
        return SourceKind(check=self.check, open=self.open, via="minutes", starts_off=True)


class MinutesSource:
    def __init__(self, name: str, project: str, feed: Minutes) -> None:
        self.name, self.project, self.feed = name, project, feed

    async def pull(self, cursor: str | None) -> Pulled:
        self.feed.cursors.append(cursor)
        self.feed.pulls += 1
        batch = self.feed.batches.pop(0) if self.feed.batches else []
        drafts = [NoteDraft(project=self.project, **fields) for fields in batch]
        return Pulled(drafts=drafts, cursor=f"c{self.feed.pulls}")


def minute(n: int, text: str, title: str = "Payments weekly", **extra) -> dict:
    fields = {"title": title, "text": text, "source_ref": f"meeting:m-{n}", "label": "meeting"}
    return fields | extra


def never_called(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"a source that is off was read: {request.url}")


@pytest.fixture
def base(tmp_path, monkeypatch):
    """A workspace with two projects and the stand-in, not yet registered as a kind."""
    feed = Minutes()
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    for variable, value in LF_ENV.items():
        monkeypatch.setenv(variable, value)
    clock = Clock(T0)
    db = tmp_path / "cairn.db"
    store = Store(db, clock=clock)
    projects = {name: store.create_project(name) for name in ("meetings", "planning")}
    http = httpx.AsyncClient(transport=httpx.MockTransport(never_called))
    yield SimpleNamespace(store=store, db=db, clock=clock, feed=feed, projects=projects, http=http)
    store.close()


@pytest.fixture
def world(base, monkeypatch):
    """The same, with the stand-in registered as the kind "minutes"."""
    monkeypatch.setitem(KINDS, "minutes", base.feed.kind())
    return base


def run(world, name: str, view: Store | None = None) -> SyncReport:
    return asyncio.run(
        run_source(
            view or world.store,
            name,
            http=world.http,
            env={TOKEN_ENV: TOKEN},
            clock=world.clock,
        )
    )


def person(world, name: str, role: str | None = None) -> SimpleNamespace:
    """Someone signed in to the default workspace: the first one owns it, the others arrive
    as members (`role` changes theirs). `view` records what it writes as that person."""
    signed = world.store.sign_in(
        "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER, join="default"
    )
    if role is not None:
        world.store.set_role(signed.user.id, role)
    view = world.store.for_account(signed.account_id, signed.user.id)
    return SimpleNamespace(
        id=signed.user.id,
        name=name,
        cookie={"cookie": f"cairn_session={signed.token}"},
        view=view.acting_as("session", name, signed.user.id),
    )


def notes(world, project: str) -> list:
    return world.store.list_notes(world.store.get_project(project).id)


# A kind that starts off


def test_a_source_of_a_kind_that_starts_off_is_added_off_and_reads_nothing(world):
    added = world.store.add_source("standup", "minutes", {"projects": []})
    assert (added.enabled, added.enabled_at) == (False, None)
    assert added.config["projects"] == []
    named = world.store.add_source("retro", "minutes", {"projects": ["meetings"]})
    assert (named.enabled, named.enabled_at) == (False, None)
    for name in ("standup", "retro"):
        info = world.store.get_source(name)
        assert (info.enabled, info.enabled_at, info.cursor) == (False, None, None)
    assert {s.name: s.enabled for s in world.store.list_sources()} == {
        "retro": False,
        "standup": False,
    }

    world.feed.batches = [[minute(1, "# Payments weekly\n\nAda moved the release to Friday.\n")]]
    before = dump(world.db)
    for name in ("standup", "retro"):
        report = run(world, name)
        assert report == SyncReport(off=True)
    assert world.feed.opened == [] and world.feed.cursors == []
    assert dump(world.db) == before  # no claim, no cursor, no run recorded, no audit event
    info = world.store.get_source("retro")
    assert (info.last_run_at, info.last_report, info.last_error) == (None, None, None)
    assert notes(world, "meetings") == []

    outcomes = asyncio.run(
        sync_all(world.store, http=world.http, env={TOKEN_ENV: TOKEN}, clock=world.clock)
    )
    assert world.feed.opened == [] and dump(world.db) == before
    for outcome in outcomes:  # passed over: either not listed, or listed as off
        assert outcome.error is None and outcome.report.off, outcome


def test_switching_a_source_on_names_its_project_and_a_run_then_writes_there(world):
    ada = person(world, "Ada")
    world.store.add_source("standup", "minutes", {"projects": []})

    world.clock.now = T1
    on = ada.view.enable_source("standup", projects=["meetings"])
    assert on.enabled is True and moment(on.enabled_at) == T1
    assert on.config["projects"] == ["meetings"]
    stored = world.store.get_source("standup")
    assert (stored.enabled, moment(stored.enabled_at)) == (True, T1)
    assert stored.config["projects"] == ["meetings"]
    event = world.store.list_audit(limit=1)[0]
    assert (event.action, event.actor_kind, event.actor) == ("source.enabled", "session", "Ada")
    assert event.actor_id == ada.id
    assert event.detail["source"] == "standup" and event.detail["projects"] == ["meetings"]

    world.clock.now = T1 + timedelta(minutes=20)
    world.feed.batches = [[minute(1, "# Payments weekly\n\nAda moved the release to Friday.\n")]]
    report = run(world, "standup")
    assert (report.saved, report.off) == (1, False)
    (note,) = notes(world, "meetings")
    assert (note.via, note.source_ref) == ("minutes", "meeting:m-1")
    assert notes(world, "planning") == []

    (options,) = world.feed.opened
    since = options["since"]
    assert isinstance(since, datetime) and since.utcoffset() == timedelta(0)
    assert since == T1 == moment(world.store.get_source("standup").enabled_at)
    assert {"http", "env", "deadline"} <= set(options)  # the keywords it had before stay
    assert world.feed.cursors == [None]
    assert world.store.get_source("standup").cursor == "c1"


def test_each_refusal_to_switch_a_source_writes_nothing(world):
    person(world, "Ada")  # the owner
    brook = person(world, "Brook")  # a member
    cy = person(world, "Cy", role="admin")
    world.store.create_project("board", everyone="none")  # an admin does not reach it
    world.store.create_project("handbook", everyone="read")  # an admin may only read it
    world.store.add_source("standup", "minutes", {"projects": []})
    world.store.add_source("retro", "minutes", {"projects": ["handbook"]})
    meetings = world.projects["meetings"]

    refusals = [
        (KeyError, lambda: world.store.enable_source("nope", projects=["meetings"])),
        (KeyError, lambda: world.store.disable_source("nope")),
        (ValueError, lambda: world.store.enable_source("standup")),  # it names no project
        (ValueError, lambda: world.store.enable_source("standup", projects=[])),
        (ValueError, lambda: world.store.enable_source("standup", projects=["ghost"])),
        (ValueError, lambda: cy.view.enable_source("standup", projects=["board"])),
        (PermissionError, lambda: cy.view.enable_source("standup", projects=["handbook"])),
        (PermissionError, lambda: cy.view.enable_source("retro")),  # its own project, checked
        (PermissionError, lambda: world.store.narrow([meetings.id]).enable_source("standup")),
        (
            PermissionError,
            lambda: world.store.narrow(None, "read").enable_source(
                "standup", projects=["meetings"]
            ),
        ),
    ]
    before = dump(world.db)
    for expected, call in refusals:
        with pytest.raises(expected):
            call()
        assert dump(world.db) == before, call
    with pytest.raises(ValueError, match="exactly one project"):  # the kind's own words
        world.store.enable_source("standup", projects=["meetings", "planning"])
    with pytest.raises(ValueError, match="no project named"):
        world.store.enable_source("standup", projects=["ghost"])
    for call in (
        lambda: brook.view.enable_source("standup", projects=["meetings"]),
        lambda: brook.view.disable_source("standup"),
    ):
        with pytest.raises(PermissionError) as refused:
            call()
        assert str(refused.value) == NOT_A_SOURCE_MANAGER
    assert dump(world.db) == before
    assert world.store.get_source("standup").enabled is False

    # By name ignoring case; an admin may; then the refusals of switching it off.
    on = cy.view.enable_source("STANDUP", projects=["meetings"])
    assert (on.name, on.enabled) == ("standup", True)
    event = world.store.list_audit(limit=1)[0]
    assert (event.action, event.actor) == ("source.enabled", "Cy")
    before = dump(world.db)
    for call in (
        lambda: world.store.narrow([meetings.id]).disable_source("standup"),
        lambda: world.store.narrow(None, "read").disable_source("standup"),
        lambda: brook.view.disable_source("standup"),
    ):
        with pytest.raises(PermissionError):
            call()
    assert dump(world.db) == before
    assert world.store.get_source("standup").enabled is True


def test_changing_the_destination_while_on_moves_the_next_run_and_not_since(world):
    world.store.add_source("standup", "minutes", {"projects": []})
    world.clock.now = T1
    world.store.enable_source("standup", projects=["meetings"])
    world.feed.batches = [[minute(1, "# Payments weekly\n\nAda moved the release to Friday.\n")]]
    assert run(world, "standup").saved == 1

    world.clock.now = T2
    moved = world.store.enable_source("standup", projects=["planning"])
    assert moved.enabled is True and moved.config["projects"] == ["planning"]
    assert moment(moved.enabled_at) == T1  # already on: it did not move
    event = world.store.list_audit(limit=1)[0]
    assert (event.action, event.actor_kind) == ("source.enabled", "system")
    assert event.detail["projects"] == ["planning"]

    world.feed.batches = [[minute(2, "# Payments weekly\n\nBrook owns the retry fix.\n")]]
    assert run(world, "standup").saved == 1
    assert [c.source_ref for c in notes(world, "planning")] == ["meeting:m-2"]
    assert [c.source_ref for c in notes(world, "meetings")] == ["meeting:m-1"]
    assert world.feed.opened[-1]["since"] == T1
    assert world.feed.cursors == [None, "c1"]

    before = dump(world.db)  # switching on what is on, as it is, changes nothing
    world.clock.now = T3
    again = world.store.enable_source("standup")
    assert (again.enabled, moment(again.enabled_at)) == (True, T1)
    assert world.store.enable_source("standup", projects=["planning"]).config["projects"] == [
        "planning"
    ]
    assert dump(world.db) == before


def test_off_then_on_again_keeps_the_cursor_and_moves_since(world):
    ada = person(world, "Ada")
    world.store.add_source("standup", "minutes", {"projects": []})
    world.clock.now = T1
    world.store.enable_source("standup", projects=["meetings"])
    world.feed.batches = [[minute(1, "# Payments weekly\n\nAda moved the release to Friday.\n")]]
    assert run(world, "standup").saved == 1
    ran = world.store.get_source("standup")
    assert ran.cursor == "c1" and ran.last_report["saved"] == 1

    world.clock.now = T2
    off = ada.view.disable_source("standup")
    assert off.enabled is False
    kept = world.store.get_source("standup")
    assert (kept.enabled, kept.cursor, kept.last_report, kept.last_run_at) == (
        False,
        ran.cursor,
        ran.last_report,
        ran.last_run_at,
    )
    assert kept.config["projects"] == ["meetings"]
    assert len(notes(world, "meetings")) == 1  # what it noted stays
    event = world.store.list_audit(limit=1)[0]
    assert (event.action, event.actor_kind, event.actor) == ("source.disabled", "session", "Ada")
    assert event.detail["source"] == "standup"

    before = dump(world.db)
    assert world.store.disable_source("standup").enabled is False  # already off: nothing
    world.feed.batches = [[minute(2, "# Payments weekly\n\nBrook owns the retry fix.\n")]]
    assert run(world, "standup") == SyncReport(off=True)
    assert len(world.feed.opened) == 1 and dump(world.db) == before

    world.clock.now = T3
    on = world.store.enable_source("standup")  # its own project, kept
    assert (on.enabled, moment(on.enabled_at), on.cursor) == (True, T3, "c1")
    assert on.config["projects"] == ["meetings"]
    assert run(world, "standup").saved == 1
    assert world.feed.opened[-1]["since"] == T3
    assert world.feed.cursors[-1] == "c1"  # it goes on from where it was


def test_two_drafts_with_one_topic_land_on_one_page(world):
    world.store.add_source("standup", "minutes", {"projects": ["meetings"]})
    world.store.enable_source("standup")
    world.feed.batches = [
        [
            minute(
                1,
                "# Standup 3 November\n\nAda moved the release to Friday.\n",
                title="Standup 3 November",
                topic="Weekly standup",
            ),
            minute(
                2,
                "# Standup 10 November\n\nBrook owns the retry fix.\n",
                title="Standup 10 November",
                topic="Weekly standup",
            ),
            minute(3, "# Retro notes\n\nCy reviews the ledger on Monday.\n", title="Retro notes"),
        ]
    ]
    assert run(world, "standup").saved == 3
    assert sorted((c.topic or "") for c in notes(world, "meetings")) == [
        "",
        "Weekly standup",
        "Weekly standup",
    ]
    project = world.projects["meetings"].id
    pages = world.store.list_pages(project)
    assert len(pages) == 2
    together = [p for p in pages if "release to Friday" in p.body]
    assert len(together) == 1 and "retry fix" in together[0].body
    (alone,) = [p for p in pages if "ledger on Monday" in p.body]
    assert alone.title == "Retro notes" and "release to Friday" not in alone.body
    assert NoteDraft(project="b", title="t", text="x", source_ref="r", label="l").topic is None


# Langfuse: on when added, as before, and it can be switched off


class Langfuse:
    """Just enough of Langfuse's two read endpoints for one marked trace (the C2 tests have
    the whole of them); it keeps the path of every request it gets."""

    def __init__(self, now: datetime) -> None:
        self.requests: list[str] = []

        def stamp(minutes_ago: float) -> str:
            at = now - timedelta(minutes=minutes_ago)
            return at.isoformat(timespec="milliseconds").replace("+00:00", "Z")

        self.score = {
            "id": "s1",
            "projectId": "proj-s2",
            "name": "cairn",
            "dataType": "CATEGORICAL",
            "value": "meetings",
            "source": "EVAL",
            "timestamp": stamp(50),
            "environment": "production",
            "createdAt": stamp(50),
            "updatedAt": stamp(50),
            "comment": "Shows the refund rule: 5 days.",
            "configId": None,
            "metadata": None,
            "subject": {"kind": "observation", "id": "obs-t1", "traceId": "t1"},
        }
        self.root = {
            "id": "obs-t1",
            "traceId": "t1",
            "startTime": stamp(60),
            "endTime": stamp(59),
            "projectId": "proj-s2",
            "parentObservationId": None,
            "type": "SPAN",
            "name": "handle-request",
            "level": "DEFAULT",
            "environment": "production",
            "isRootObservation": True,
            "input": "How long do refunds take?",
            "output": "Refunds take 5 days.",
            "metadata": {},
            "traceName": "refund-flow",
            "tags": [],
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        if request.url.path == "/api/public/v3/scores":
            return httpx.Response(200, json={"data": [self.score], "meta": {"limit": 100}})
        if request.url.path == "/api/public/v2/observations":
            return httpx.Response(200, json={"data": [self.root], "meta": {"limit": 100}})
        return httpx.Response(404, json={"message": "not found"})


def test_a_langfuse_source_is_on_when_added_and_can_be_switched_off(base):
    world = base
    fake = Langfuse(T0)
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))

    def sync():
        return asyncio.run(
            run_source(world.store, "traces", http=http, env=LF_ENV, clock=world.clock)
        )

    added = world.store.add_source("traces", "langfuse", LF_CONFIG)
    assert added.enabled is True and moment(added.enabled_at) == moment(added.created_at) == T0
    assert sync().saved == 1 and fake.requests
    (note,) = notes(world, "meetings")
    assert note.via == "langfuse"

    world.clock.now = T1
    off = world.store.disable_source("traces")
    assert off.enabled is False
    event = world.store.list_audit(limit=1)[0]
    assert (event.action, event.actor_kind) == ("source.disabled", "system")
    fake.requests.clear()
    before = dump(world.db)
    assert sync() == SyncReport(off=True)
    assert fake.requests == [] and dump(world.db) == before

    world.clock.now = T2
    on = world.store.enable_source("traces")
    assert (on.enabled, moment(on.enabled_at), on.config["projects"]) == (True, T2, ["meetings"])
    report = sync()
    assert fake.requests and report.off is False and report.saved == 0
    assert len(notes(world, "meetings")) == 1


# The command line


def cli(capsys, *argv: str) -> tuple[int, list[str], list[str]]:
    code = main(list(argv))
    said = capsys.readouterr()
    return code, said.out.splitlines(), said.err.splitlines()


def words(line: str) -> set[str]:
    return set(re.findall(r"[a-z]+", line.lower()))


def test_the_command_line_switches_a_source_on_and_off(world, capsys, tmp_path):
    db = str(world.db)
    code, out, _ = cli(
        capsys, "source-add", "--db", db, "--name", "traces", "--kind", "langfuse",
        "--config", json.dumps(LF_CONFIG),
    )  # fmt: skip
    assert code == 0 and out and "off" not in words(" ".join(out))
    code, out, _ = cli(
        capsys, "source-add", "--db", db, "--name", "standup", "--kind", "minutes",
        "--config", json.dumps({"projects": []}),
    )  # fmt: skip
    assert code == 0 and "off" in words(" ".join(out)) and "source-enable" in " ".join(out)
    assert world.store.get_source("standup").enabled is False

    code, out, _ = cli(capsys, "source-list", "--db", db)
    assert code == 0
    (standup,) = [line for line in out if "standup" in line]
    (traces,) = [line for line in out if "traces" in line]
    assert "off" in words(standup)
    assert "on" in words(traces) and "off" not in words(traces)

    before = dump(world.db)
    code, out, _ = cli(capsys, "source-sync", "--db", db, "--name", "standup")
    assert code == 0 and len(out) == 1 and "off" in words(out[0])
    assert world.feed.opened == [] and dump(world.db) == before

    refusals = [
        ("source-enable", "--db", db, "--name", "nope", "--project", "meetings"),
        ("source-disable", "--db", db, "--name", "nope"),
        ("source-enable", "--db", db, "--name", "standup", "--project", "ghost"),
        ("source-enable", "--db", db, "--name", "standup", "--project", "meetings",
         "--project", "planning"),
    ]  # fmt: skip
    for argv in refusals:
        code, out, err = cli(capsys, *argv)
        assert (code, out, len(err)) == (1, [], 1), argv
        assert dump(world.db) == before, argv
    missing = tmp_path / "elsewhere" / "none.db"
    for argv in (
        ("source-enable", "--db", str(missing), "--name", "standup", "--project", "meetings"),
        ("source-disable", "--db", str(missing), "--name", "standup"),
        ("source-enable", "--db", db, "--name", "standup", "--project", "meetings",
         "--workspace", "acct_nobody0000"),
        ("source-disable", "--db", db, "--name", "standup", "--workspace", "acct_nobody0000"),
    ):  # fmt: skip
        code, out, err = cli(capsys, *argv)
        assert code == 1 and err, argv
    assert not missing.exists() and dump(world.db) == before

    code, out, _ = cli(
        capsys, "source-enable", "--db", db, "--name", "standup", "--project", "meetings"
    )
    assert code == 0 and len(out) == 1
    assert "on" in words(out[0]) and "meetings" in out[0]
    on = world.store.get_source("standup")
    assert on.enabled is True and on.config["projects"] == ["meetings"]
    code, out, _ = cli(capsys, "source-list", "--db", db)
    (standup,) = [line for line in out if "standup" in line]
    assert "on" in words(standup) and "off" not in words(standup)

    code, out, _ = cli(capsys, "source-disable", "--db", db, "--name", "standup")
    assert code == 0 and len(out) == 1
    assert "off" in words(out[0]) and "meetings" in out[0]
    assert world.store.get_source("standup").enabled is False

    account = world.store.account_id
    code, out, _ = cli(
        capsys, "source-enable", "--db", db, "--name", "STANDUP", "--workspace", account
    )
    assert code == 0 and world.store.get_source("standup").enabled is True


# The web view


def page_text(markup: str) -> str:
    return " ".join(html_text.unescape(re.sub(r"<[^>]+>", " ", markup)).split())


def form_to(markup: str, action: str) -> tuple[str, str]:
    """The opening tag and the inside of the form that posts to `action`."""
    found = re.search(
        r'(<form\b[^>]*\baction="' + re.escape(action) + r'"[^>]*>)(.*?)</form>',
        markup,
        re.S | re.I,
    )
    assert found, f"no form posts to {action}"
    return found.group(1), found.group(2)


def has_button(inside: str) -> bool:
    """A control that submits the form and is not disabled."""
    for tag in re.findall(r"<button\b[^>]*>|<input\b[^>]*>", inside, re.I):
        kind = re.search(r'\btype="([^"]*)"', tag, re.I)
        kind = kind.group(1).lower() if kind else ("submit" if tag[1:7].lower() == "button" else "")
        if kind == "submit" and not re.search(r"\bdisabled\b", tag, re.I):
            return True
    return False


def routes_of(found, prefix=""):
    for route in found:
        path = prefix + getattr(route, "path", "")
        if hasattr(route, "routes"):
            yield from routes_of(route.routes, path)
        else:
            yield path, set(getattr(route, "methods", None) or ())


@pytest.fixture
def web(world):
    ada = person(world, "Ada")
    brook = person(world, "Brook")
    cy = person(world, "Cy", role="admin")
    world.store.add_source("traces", "langfuse", LF_CONFIG)
    world.store.add_source("standup", "minutes", {"projects": []})
    app = create_app(world.store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:
        yield SimpleNamespace(client=client, app=app, ada=ada, brook=brook, cy=cy)


def post(web, who, path: str, data: dict | None = None, **headers) -> httpx.Response:
    sent = {**SAME_ORIGIN, **(who.cookie if who else {}), **headers}
    return web.client.post(path, data=data or {}, headers=sent, follow_redirects=False)


def test_the_sources_page_shows_each_state_and_the_forms_that_apply(world, web):
    world.store.remove_source("traces")
    page = web.client.get("/sources", headers=web.ada.cookie)
    assert page.status_code == 200
    text = page_text(page.text)
    assert "off" in words(text) and "on since" not in text.lower()

    world.store.add_source("traces", "langfuse", LF_CONFIG)
    world.clock.now = T1
    page = web.client.get("/sources", headers=web.ada.cookie)
    assert page.status_code == 200
    assert page_text(page.text).lower().count("on since") == 1  # traces is on; standup off
    # An off source offers switching on. An on source offers switching off, and changing
    # where it writes, which is the same action as switching on with other projects.
    offered = ("/integrations/standup/on", "/integrations/traces/off", "/integrations/traces/on")
    for action in offered:
        opening, _ = form_to(page.text, action)
        assert re.search(r'\bmethod="post"', opening, re.I), opening
    assert 'action="/integrations/standup/off"' not in page.text  # nothing to switch off
    _, switch_on = form_to(page.text, "/integrations/standup/on")
    assert has_button(switch_on)
    assert 'name="project"' in switch_on and 'name="new_project"' in switch_on
    _, switch_off = form_to(page.text, "/integrations/traces/off")
    assert has_button(switch_off)
    _, retarget = form_to(page.text, "/integrations/traces/on")
    assert has_button(retarget) and 'name="project"' in retarget
    for shown in (TOKEN, SECRET, PUBLIC):
        assert shown not in page.text

    # R4: a member reaches the page; with no account of their own, no source and no switch.
    member = web.client.get("/sources", headers=web.brook.cookie)
    assert member.status_code == 200
    mine = member.text.partition("<main>")[2].partition("</main>")[0]
    assert "<form" not in mine and "/integrations/" not in mine
    assert "traces" not in mine and "standup" not in mine
    assert "No account yet. Ask the person who runs this server to connect one." in mine
    paths = list(routes_of(web.app.routes))
    about_sources = [(path, methods) for path, methods in paths if "/sources" in path]
    assert about_sources and all(methods <= {"GET", "HEAD"} for _, methods in about_sources)
    switches = [path for path, methods in paths if path.startswith("/integrations/")]
    assert switches and all("/sources" not in path for path in switches)


def test_an_owner_or_an_admin_switches_a_source_in_the_web_view(world, web):
    world.clock.now = T1
    done = post(web, web.ada, "/integrations/standup/on", {"project": "meetings"})
    assert done.status_code == 303 and done.headers["location"].endswith("/sources")
    on = world.store.get_source("standup")
    assert (on.enabled, moment(on.enabled_at), on.config["projects"]) == (True, T1, ["meetings"])
    event = world.store.list_audit(limit=1)[0]
    assert (event.action, event.actor_kind, event.actor) == ("source.enabled", "session", "Ada")
    page = web.client.get("/sources", headers=web.ada.cookie)
    assert page_text(page.text).lower().count("on since") == 2

    world.clock.now = T2
    done = post(web, web.cy, "/integrations/standup/off")
    assert done.status_code == 303 and done.headers["location"].endswith("/sources")
    assert world.store.get_source("standup").enabled is False
    event = world.store.list_audit(limit=1)[0]
    assert (event.action, event.actor_kind, event.actor) == ("source.disabled", "session", "Cy")
    done = post(web, web.cy, "/integrations/traces/off")
    assert done.status_code == 303 and world.store.get_source("traces").enabled is False

    world.clock.now = T3
    done = post(web, web.cy, "/integrations/standup/on", {"project": "planning"})
    assert done.status_code == 303
    on = world.store.get_source("standup")
    assert (on.enabled, moment(on.enabled_at), on.config["projects"]) == (True, T3, ["planning"])


def test_the_web_view_refuses_without_writing_anything(world, web):
    before = dump(world.db)
    refusals = [
        (web.brook, "/integrations/standup/on", {"project": "meetings"}, {}, 403),
        (web.brook, "/integrations/traces/off", {}, {}, 403),
        (web.ada, "/integrations/nope/on", {"project": "meetings"}, {}, 404),
        (web.ada, "/integrations/nope/off", {}, {}, 404),
        (web.ada, "/integrations/standup/on", {}, {}, 400),  # it names no project
        (web.ada, "/integrations/standup/on", {"project": ["meetings", "planning"]}, {}, 400),
        (web.ada, "/integrations/standup/on", {"new_project": "meetings"}, {}, 400),  # taken
        (web.ada, "/integrations/standup/on", {"new_project": "not a name!"}, {}, 400),
        (
            web.ada,
            "/integrations/standup/on",
            {"project": "meetings"},
            {"sec-fetch-site": "cross-site"},
            403,
        ),
    ]
    for who, path, data, headers, status in refusals:
        answer = post(web, who, path, data, **headers)
        assert answer.status_code == status, (path, data, answer.status_code)
        assert dump(world.db) == before, (path, data)
    ghost = post(web, web.ada, "/integrations/standup/on", {"project": "ghost"})
    assert ghost.status_code == 400 and "ghost" in ghost.text
    assert dump(world.db) == before

    signed_out = post(web, None, "/integrations/standup/on", {"project": "meetings"})
    assert signed_out.status_code == 303 and "/login" in signed_out.headers["location"]
    assert dump(world.db) == before
    assert world.store.get_source("standup").enabled is False
    assert world.store.get_source("traces").enabled is True


def test_a_new_project_made_while_switching_on_is_private_and_the_destination(world, web):
    world.clock.now = T1
    done = post(web, web.ada, "/integrations/standup/on", {"new_project": "board-minutes"})
    assert done.status_code == 303 and done.headers["location"].endswith("/sources")
    project = world.store.get_project("board-minutes")
    assert project is not None and project.kind == "general" and project.maker_id == web.ada.id
    access = world.store.project_access(project.id)
    assert access.everyone == "none" and not access.people
    assert web.brook.view.get_project("board-minutes") is None  # a member does not reach it
    assert web.cy.view.get_project("board-minutes") is None  # nor does an admin
    on = world.store.get_source("standup")
    assert (on.enabled, on.config["projects"]) == (True, ["board-minutes"])

    world.feed.batches = [[minute(1, "# Board minutes\n\nThe board approved the budget.\n")]]
    assert run(world, "standup").saved == 1
    assert [c.source_ref for c in notes(world, "board-minutes")] == ["meeting:m-1"]
    assert world.feed.opened[-1]["since"] == T1


# What stays


def test_an_export_holds_no_source(world):
    world.store.add_source("standup", "minutes", {"projects": ["meetings"]})
    world.store.add_source("traces", "langfuse", LF_CONFIG)
    world.store.enable_source("standup")
    world.feed.batches = [[minute(1, "# Payments weekly\n\nAda moved the release to Friday.\n")]]
    assert run(world, "standup").saved == 1
    out = io.BytesIO()
    world.store.export(out)
    with zipfile.ZipFile(io.BytesIO(out.getvalue())) as archive:
        held = " ".join(archive.read(n).decode("utf-8", "replace") for n in archive.namelist())
    assert "release to Friday" in held  # the note is exported
    for name in ("standup", "traces", TOKEN_ENV, "S2_LF_SECRET", "enabled_at"):
        assert name not in held, name


# Schema


# The tables cairn wrote at schema version 2, as SQLite stored them; the steps that follow
# (cairn.store.schema._MIGRATIONS, history that never changes) bring them to version 19.
V2_TABLES = """
CREATE TABLE accounts (
    seq INTEGER PRIMARY KEY,  -- creation order, the default account first
    id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (account_id, name)
);
CREATE TABLE api_keys (
    seq INTEGER PRIMARY KEY,  -- creation order: "oldest first" even if the clock steps back
    digest TEXT NOT NULL UNIQUE,  -- SHA-256 hex of the key; the key itself is never stored
    account_id TEXT NOT NULL,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX api_keys_by_label ON api_keys (account_id, label);
CREATE TABLE sessions (
    digest TEXT PRIMARY KEY,  -- SHA-256 hex of the token
    account_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,  -- valid while expires_at > now (same fixed-width format)
    created_at TEXT NOT NULL
);
CREATE TABLE notes (
    seq INTEGER PRIMARY KEY,  -- insertion order: "oldest first" even if the clock steps back
    id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    text TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    title TEXT NOT NULL,
    topic TEXT,
    label TEXT,
    source_ref TEXT,
    via TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX notes_by_text ON notes (project_id, text_sha256);
CREATE TABLE spans (
    id TEXT PRIMARY KEY,
    note_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,  -- document order within the note
    kind TEXT NOT NULL,
    heading_path TEXT NOT NULL,  -- JSON array of heading texts
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    text TEXT NOT NULL,
    UNIQUE (note_id, ordinal)
);
CREATE TABLE page_versions (
    project_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    claims TEXT NOT NULL,  -- JSON array of {"id", "text", "span_ids"}
    metrics TEXT NOT NULL,  -- JSON object: the gate's metrics for this version
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, slug, version)
);
"""
ADDED_AT = "2026-10-01T08:00:00.000000+00:00"


def version_19_database(db: Path) -> None:
    """A database as cairn wrote it at schema version 19: the default workspace with a project
    and a Langfuse source that has run once, and a second workspace's source."""
    from cairn.store.schema import _MIGRATIONS  # history: the steps from version 2 to 19

    con = sqlite3.connect(db, isolation_level=None)
    try:
        con.executescript(V2_TABLES)
        con.execute("BEGIN")
        con.executemany(
            "INSERT INTO accounts (id, name, created_at) VALUES (?, ?, ?)",
            [
                (DEFAULT_ACCOUNT_ID, "default", ADDED_AT),
                ("acct_dee000000000", "Dee", ADDED_AT),
            ],
        )
        for version in range(3, 20):
            _MIGRATIONS[version](con)
        con.executemany(
            "INSERT INTO projects (id, account_id, name, description, created_at, kind, maker_id,"
            " everyone) VALUES (?, ?, 'meetings', '', ?, 'general', NULL, 'write')",
            [
                ("br_s2meetings01", DEFAULT_ACCOUNT_ID, ADDED_AT),
                ("br_s2meetings02", "acct_dee000000000", ADDED_AT),
            ],
        )
        report = {"saved": 2, "unchanged": 0, "refused": 0, "rejected": 0, "skipped": 0}
        con.executemany(
            "INSERT INTO sources (account_id, name, kind, config, cursor, created_at,"
            " last_run_at, last_report) VALUES (?, ?, 'langfuse', ?, ?, ?, ?, ?)",
            [
                (
                    DEFAULT_ACCOUNT_ID, "traces", json.dumps(LF_CONFIG), "cursor-19",
                    "2026-10-02T10:30:00.000000+00:00", "2026-10-03T00:00:00.000000+00:00",
                    json.dumps(report),
                ),
                (
                    "acct_dee000000000", "dee-traces", json.dumps(LF_CONFIG), None,
                    "2026-10-04T16:45:00.000000+00:00", None, None,
                ),
            ],
        )  # fmt: skip
        con.execute("PRAGMA user_version = 19")
        con.execute("COMMIT")
    finally:
        con.close()


def test_a_version_19_database_opens_with_every_source_on_since_it_was_added(tmp_path):
    db = tmp_path / "cairn.db"
    version_19_database(db)
    store = Store(db, clock=Clock(T3))  # long after: "now" is not the stamp it gets
    try:
        con = sqlite3.connect(db)
        try:
            assert con.execute("PRAGMA user_version").fetchone()[0] > 19
        finally:
            con.close()
        traces = store.get_source("traces")
        assert traces.enabled is True
        assert moment(traces.enabled_at) == moment(traces.created_at)
        assert moment(traces.created_at) == datetime(2026, 10, 2, 10, 30, tzinfo=UTC)
        assert (traces.cursor, traces.last_report["saved"]) == ("cursor-19", 2)
        assert traces.config == LF_CONFIG
        dee = store.for_account("acct_dee000000000").get_source("dee-traces")
        assert dee.enabled is True
        assert moment(dee.enabled_at) == datetime(2026, 10, 4, 16, 45, tzinfo=UTC)
        assert store.disable_source("traces").enabled is False
        assert store.get_source("traces").cursor == "cursor-19"
    finally:
        store.close()
