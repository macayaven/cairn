"""Sources made from a credential (R2): what a person chooses, and what the store refuses.

A credential is registered on the command line (Store.add_credential); a person makes a source
from it by choosing what it reads and one project (Store.add_source_from). The source's config
is the credential's settings merged with that choice, checked by its kind as today, and the
source is added off. Every name here is made up; every request goes to an httpx.MockTransport.
"""

import asyncio
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from cairn.sources import CHOOSING, KINDS, run_source, runner, sync_all
from cairn.sources.runner import PreviewBusy, preview_new_source, preview_source
from cairn.store import Store
from cairn.store.access import READ_ONLY_PROJECT
from cairn.store.sources import (
    DISCONNECTED,
    NAME_TAKEN,
    NOT_A_SOURCE_MANAGER,
    NOT_YOUR_ACCOUNT,
    OWNER_CANNOT_WRITE,
    OWNER_DECIDES,
    SOURCE_CREDENTIAL_PREFIX,
)

ISSUER = "https://id.example.test/pool-r2"
NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
DAY, MINUTE = timedelta(days=1), timedelta(minutes=1)
# The web preview reads the real time, not the store's clock: what it is to find within its
# days is dated from the real present, or the test passes only near the day it was written.
LATELY = datetime.now(UTC).replace(microsecond=0) - DAY
FEED_URL = "https://ledger.example.test/cairn/feed"
FEED_ENV = "R2_FEED_SECRET"
FEED_SECRET = "r2" + "-made-up-" + "feed-" + "secret-41aa"  # never a real secret

# Each kind's credential settings, and a selector it accepts: all made up.
SETTINGS = {
    "langfuse": {
        "base_url": "https://langfuse.example.test",
        "public_key_env": "R2_LF_PUBLIC",
        "secret_key_env": "R2_LF_SECRET",
    },
    "granola": {"key_env": "R2_GRANOLA_KEY"},
    "google_meet": {"credentials_env": "R2_GOOGLE", "subject": "ada@example.test"},
    "google_drive": {"credentials_env": "R2_GOOGLE", "subject": "ada@example.test"},
    "google_calendar": {"credentials_env": "R2_GOOGLE", "subject": "ada@example.test"},
    "gmail": {"credentials_env": "R2_GOOGLE", "subject": "ada@example.test"},
    "linear": {"key_env": "R2_LINEAR_KEY"},
    "github": {"token_env": "R2_GH_TOKEN"},
    "connector": {"url": FEED_URL, "secret_env": FEED_ENV},
}
SELECT = {
    "langfuse": {},
    "granola": {"folder_id": "fol_" + "Ab3" * 4 + "Zz"},
    "google_meet": {"meeting_code": "abc-mnop-xyz"},
    "google_drive": {"folder_id": "fold-r2-ledger"},
    "google_calendar": {"calendar_id": "primary"},
    "gmail": {"label": "to-ledger"},
    "linear": {"teams": ["LED"]},
    "github": {"repo": "quillworks/quill"},
    "connector": {},
}


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def rows(db: Path, table: str) -> list:
    con = sqlite3.connect(db)
    try:
        return con.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    finally:
        con.close()


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def world(tmp_path):
    """A workspace: Olive the owner, Ada and Cy admins, Brook a member; the projects
    "ledger" (every member writes), "ada-notes" (Ada's own, private) and "board" (private)."""
    clock = Clock(NOW)
    db = tmp_path / "cairn.db"
    store = Store(db, clock=clock)
    people = {}
    for name, role in (("Olive", "owner"), ("Ada", "admin"), ("Cy", "admin"), ("Brook", None)):
        signed = store.sign_in(
            "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER,
            join="default",
        )  # fmt: skip
        if role:
            store.set_role(signed.user.id, role)
        view = store.for_account(signed.account_id, signed.user.id)
        people[name] = SimpleNamespace(id=signed.user.id, view=view)
    store.create_project("ledger")
    store.create_project("board", everyone="none")
    people["Ada"].view.create_project("ada-notes", everyone="none")
    yield SimpleNamespace(store=store, db=db, clock=clock, **people)
    store.close()


def made(world, kind: str, *, owner: str | None = "ada@example.test", name: str = "ada-acct"):
    world.store.add_credential(name, kind, SETTINGS[kind], owner=owner)
    return name


# The kinds' table


def test_each_kind_names_its_credential_selector_and_options_as_its_settings():
    from cairn.sources.langfuse import CONFIG_KEYS

    assert set(CHOOSING) == set(KINDS)
    for kind, choosing in CHOOSING.items():
        module = __import__(f"cairn.sources.{kind}", fromlist=["x"])
        own = CONFIG_KEYS if kind == "langfuse" else module.SETTINGS
        named = [*choosing.credential, *choosing.selector, *choosing.options, "projects"]
        assert sorted(named) == sorted(own), kind
        assert len(set(named)) == len(named), kind


# Invariant 3: nothing selected reads nothing


NOTHING_SELECTED = {
    "linear": "name teams or one Linear project",
    "granola": "name one folder or one space",
    "google_calendar": '"primary" is your main calendar',
    "gmail": "label",
    "google_drive": "folder",
    "github": "repository",
    "google_meet": "name one meeting: the code from its address, such as abc-mnop-xyz",
}


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_nothing_selected_is_refused_kind_by_kind(world, kind):
    made(world, kind)
    before = dump(world.db)
    if kind in ("langfuse", "connector"):  # the project is the choice; the feed is
        world.Ada.view.add_source_from("each", "ada-acct", {}, {}, "ada-notes")
        assert world.store.get_source("each").enabled is False
        if kind == "connector":
            with pytest.raises(ValueError, match="the connector protocol has no selector"):
                world.Ada.view.add_source_from("each2", "ada-acct", {"topic": "x"}, {}, "ada-notes")
        return
    with pytest.raises(ValueError, match=NOTHING_SELECTED[kind]):
        world.Ada.view.add_source_from("each", "ada-acct", {}, {}, "ada-notes")
    # A selector left as null is no choice either.
    name = CHOOSING[kind].selector[0]
    with pytest.raises(ValueError, match=NOTHING_SELECTED[kind]):
        world.Ada.view.add_source_from("each", "ada-acct", {name: None}, {}, "ada-notes")
    assert dump(world.db) == before
    world.Ada.view.add_source_from("each", "ada-acct", SELECT[kind], {}, "ada-notes")
    assert world.store.get_source("each").enabled is False


def test_calendar_needs_a_calendar_named_and_primary_is_a_choice(world):
    made(world, "google_calendar")
    with pytest.raises(ValueError, match="primary"):
        world.Ada.view.add_source_from("cal", "ada-acct", {}, {}, "ada-notes")
    info = world.Ada.view.add_source_from(
        "cal", "ada-acct", {"calendar_id": "primary"}, {}, "ada-notes"
    )
    assert info.config["calendar_id"] == "primary"


def test_the_command_line_keeps_its_defaults(world):
    """source-add is unchanged: Linear without teams, Calendar without calendar_id, Meet."""
    store = world.store
    store.add_source("lin", "linear", {"key_env": "R2_LINEAR_KEY", "projects": []})
    store.add_source("cal", "google_calendar", {"credentials_env": "R2_GOOGLE", "projects": []})
    store.add_source("meet", "google_meet", {"credentials_env": "R2_GOOGLE", "projects": []})
    store.add_source("lf", "langfuse", {**SETTINGS["langfuse"], "projects": ["ledger"]})
    assert store.get_source("cal").config["calendar_id"] == "primary"
    assert store.get_source("lf").enabled is True  # langfuse starts on from source-add


def test_a_source_from_a_credential_starts_off_langfuse_too(world):
    assert KINDS["langfuse"].starts_off is False
    made(world, "langfuse")
    info = world.Ada.view.add_source_from("marks", "ada-acct", {}, {}, "ada-notes")
    assert (info.enabled, info.enabled_at) == (False, None)
    assert world.store.get_source("marks").enabled is False
    assert info.config["projects"] == ["ada-notes"]


def test_a_person_names_no_variable_address_or_subject(world):
    made(world, "gmail")
    made(world, "connector", owner=None, name="feed")
    ada, olive = world.Ada.view, world.Olive.view
    tries = [
        (ada, "ada-acct", {"label": "x", "credentials_env": "OTHER"}, {}, "credentials_env"),
        (ada, "ada-acct", {"label": "x"}, {"subject": "bo@example.test"}, "subject"),
        (ada, "ada-acct", {"label": "x", "base_url": "https://evil.example.test"}, {}, "base_url"),
        (olive, "feed", {}, {"url": "https://evil.example.test/feed"}, "url"),
        (olive, "feed", {}, {"secret_env": "HOME"}, "secret_env"),
    ]
    before = dump(world.db)
    for view, credential, select, options, name in tries:
        with pytest.raises(ValueError, match=f"{name} is set by the account, not by a source"):
            view.add_source_from("s", credential, select, options, "ada-notes")
    for wrong in (
        ({"label": "x", "projects": ["board"]}, {}),  # the project is chosen on its own
        ({"label": "x"}, {"projects": ["board"]}),
        ({"label": "x", "lookback_days": 3}, {}),  # an option sent as a selector
        ({}, {"label": "x"}),  # a selector sent as an option
        ({"label": "x"}, {"zorp": 1}),
    ):
        with pytest.raises(ValueError):
            ada.add_source_from("s", "ada-acct", *wrong, "ada-notes")
    assert dump(world.db) == before
    info = world.Ada.view.add_source_from(
        "mail", "ada-acct", {"label": "to-ledger"}, {"lookback_days": 3}, "ada-notes"
    )
    assert info.config == {
        **SETTINGS["gmail"],
        "label": "to-ledger",
        "lookback_days": 3,
        "projects": ["ada-notes"],
        "base_url": info.config["base_url"],  # the kind's default, from its check
    }


def test_a_source_from_a_credential_names_one_project(world):
    made(world, "gmail")
    view = world.Ada.view
    with pytest.raises(ValueError, match="at least one project"):
        view.add_source_from("mail", "ada-acct", {"label": "x"}, {}, None)
    with pytest.raises(ValueError, match="no project named"):
        view.add_source_from("mail", "ada-acct", {"label": "x"}, {}, "nowhere")
    with pytest.raises(ValueError, match="one project"):
        view.add_source_from("mail", "ada-acct", {"label": "x"}, {}, "ada-notes", new_project="x")
    with pytest.raises(KeyError):
        view.add_source_from("mail", "nope", {"label": "x"}, {}, "ada-notes")
    info = view.add_source_from("mail", "ada-acct", {"label": "x"}, {}, None, new_project="mine")
    assert info.config["projects"] == ["mine"] and info.enabled is False
    mine = world.store.get_project("mine")
    assert mine.maker_id == world.Ada.id
    assert world.store.project_access(mine.id).everyone == "none"  # private
    with pytest.raises(ValueError, match="already used"):  # what it reads is free: "y"
        view.add_source_from("MAIL", "ada-acct", {"label": "y"}, {}, "ada-notes")


# Who routes an account


def test_only_the_owner_routes_their_account(world):
    made(world, "gmail")
    ada, olive, cy, brook = world.Ada.view, world.Olive.view, world.Cy.view, world.Brook.view
    before = dump(world.db)
    for other in (olive, cy):  # an owner or an admin of the workspace: not their account
        with pytest.raises(PermissionError, match=NOT_YOUR_ACCOUNT):
            other.add_source_from("mail", "ada-acct", {"label": "x"}, {}, "ledger")
    with pytest.raises(PermissionError, match=NOT_A_SOURCE_MANAGER):  # S2: members wait
        brook.add_source_from("mail", "ada-acct", {"label": "x"}, {}, "ledger")
    with pytest.raises(ValueError, match="no project named"):  # out of Ada's reach
        ada.add_source_from("mail", "ada-acct", {"label": "x"}, {}, "board")
    with pytest.raises(PermissionError, match="you manage"):  # Ada writes "ledger", no more
        ada.add_source_from("mail", "ada-acct", {"label": "x"}, {}, "ledger")
    with pytest.raises(PermissionError):
        world.store.narrow(access="read").add_source_from(
            "mail", "ada-acct", {"label": "x"}, {}, "ledger"
        )
    assert dump(world.db) == before

    ada.add_source_from("mail", "ada-acct", {"label": "x"}, {}, "ada-notes")
    for other in (olive, cy):  # neither switches it on, nor elsewhere, nor previews it
        with pytest.raises(PermissionError, match=NOT_YOUR_ACCOUNT):
            other.enable_source("mail")
        with pytest.raises(PermissionError, match=NOT_YOUR_ACCOUNT):
            other.enable_source("mail", ["ledger"])
        with pytest.raises(PermissionError, match=NOT_YOUR_ACCOUNT):
            other.source_to_preview("mail")
    with pytest.raises(PermissionError, match=NOT_YOUR_ACCOUNT):
        olive.remove_source("mail")
    assert cy.remove_source("mail") is False  # it writes where Cy does not reach: unknown
    assert ada.enable_source("mail").enabled is True
    assert cy.disable_source("mail").enabled is False  # an admin may switch it off
    assert ada.enable_source("mail").enabled is True
    assert olive.disable_source("mail").enabled is False  # so may an owner
    with pytest.raises(PermissionError, match="you manage"):  # nor anywhere she does not manage
        ada.enable_source("mail", ["ledger"])
    ada.enable_source("mail", [], new_project="ada-more")  # her own new private project
    assert world.store.get_source("mail").config["projects"] == ["ada-more"]
    # The operator (a view acting for no person) may handle any source.
    assert world.store.disable_source("mail").enabled is False
    assert world.store.enable_source("mail", ["ledger"]).config["projects"] == ["ledger"]


def test_an_operators_credential_is_routed_as_today(world):
    made(world, "connector", owner=None, name="feed")
    cy, olive = world.Cy.view, world.Olive.view
    with pytest.raises(ValueError, match="no project named"):  # out of Cy's reach
        cy.add_source_from("minutes", "feed", {}, {}, "board")
    team = world.store.create_project("cy-team", everyone="none")
    world.store.set_project_access(team.id, people={world.Cy.id: "write"})
    with pytest.raises(PermissionError, match=OWNER_DECIDES):  # not every member's
        cy.add_source_from("minutes", "feed", {}, {}, "cy-team")
    with pytest.raises(PermissionError, match=OWNER_DECIDES):
        cy.add_source_from("minutes", "feed", {}, {}, None, new_project="cy-own")
    made(world, "connector", owner=None, name="feed-2")  # one source per connector account
    cy.add_source_from("open", "feed-2", {}, {}, "ledger")
    olive.add_source_from("minutes", "feed", {}, {"limit": 10}, "board")
    assert world.store.get_source("minutes").config["limit"] == 10
    with pytest.raises(PermissionError, match=OWNER_DECIDES):
        cy.enable_source("minutes")
    assert olive.enable_source("minutes").enabled is True
    assert cy.enable_source("open").enabled is True


def test_a_member_routes_their_own_account_as_an_owner_does(world):
    """R4 (question 4): what decision 138 says of the person whose account it is holds for a
    member: they make its sources, switch them, move them and remove them, into projects they
    manage."""
    world.store.add_credential("bk", "gmail", SETTINGS["gmail"], owner="brook@example.test")
    brook = world.Brook.view
    brook.create_project("brook-notes", everyone="none")
    ups = world.store.create_project("stand-ups", everyone="read")
    world.store.set_project_access(ups.id, people={world.Brook.id: "manage"})
    before = dump(world.db)
    with pytest.raises(PermissionError, match="you manage"):  # he writes "ledger" only
        brook.add_source_from("mail", "bk", {"label": "x"}, {}, "ledger")
    with pytest.raises(ValueError, match="no project named"):  # out of his reach
        brook.add_source_from("mail", "bk", {"label": "x"}, {}, "board")
    assert dump(world.db) == before
    made = brook.add_source_from("mail", "bk", {"label": "x"}, {}, "brook-notes")
    assert (made.enabled, made.config["projects"]) == (False, ["brook-notes"])
    assert brook.enable_source("mail").enabled is True
    assert brook.enable_source("mail", ["stand-ups"]).config["projects"] == ["stand-ups"]
    with pytest.raises(PermissionError, match="you manage"):
        brook.enable_source("mail", ["ledger"])
    assert brook.disable_source("mail").enabled is False
    assert brook.source_to_preview("mail").config["credentials_env"] == "R2_GOOGLE"
    brook.add_source_from("more", "bk", {"label": "y"}, {}, None, new_project="brook-new")
    assert world.Cy.view.get_project("brook-new") is None  # private: an admin does not reach it
    assert brook.remove_source("more") is True and world.store.get_source("more") is None


def test_a_member_sees_and_handles_only_the_sources_of_their_own_account(world):
    """R4: another person's source, the workspace's and the command line's are, for a member,
    as a name that does not exist; the workspace's accounts are none of theirs."""
    world.store.add_credential("bk", "gmail", SETTINGS["gmail"], owner="brook@example.test")
    made(world, "gmail")  # Ada's
    made(world, "connector", owner=None, name="feed")
    world.Brook.view.create_project("brook-notes", everyone="none")
    world.Brook.view.add_source_from("mail", "bk", {"label": "x"}, {}, "brook-notes")
    world.Ada.view.add_source_from("ada-mail", "ada-acct", {"label": "x"}, {}, "ada-notes")
    world.store.add_source_from("team", "feed", {}, {}, "ledger")
    world.store.add_source(
        "cli", "connector", {"url": FEED_URL, "secret_env": FEED_ENV, "projects": []}
    )
    brook = world.Brook.view
    assert [s.name for s in brook.list_sources()] == ["mail"]
    assert brook.source_credentials() == {"mail": "bk"}
    assert [c.name for c in brook.list_credentials()] == ["bk"]
    before = dump(world.db)
    for other in ("ada-mail", "team", "cli", "nope"):
        assert brook.get_source(other) is None
        for call in (brook.enable_source, brook.disable_source, brook.source_to_preview):
            with pytest.raises(KeyError):
                call(other)
        assert brook.remove_source(other) is False
        assert brook.source_items_written(other, ["x"]) == set()
    for credential in ("ada-acct", "feed"):
        with pytest.raises(KeyError):
            brook.add_source_from("x", credential, {"label": "z"}, {}, "brook-notes")
        with pytest.raises(KeyError):
            brook.remove_credential(credential)
    with pytest.raises(PermissionError, match="an owner of the workspace disconnects"):
        brook.remove_credential("bk")  # the operator registered it; an owner disconnects it
    with pytest.raises(PermissionError):  # a run acts for no person, never for a member
        brook.claim_source("mail")
    assert dump(world.db) == before
    assert world.Olive.view.remove_credential("bk") == []  # an owner may


def test_a_member_with_no_account_is_refused_as_before(world):
    """R4: no account of their own, no source: the refusal S2 pins, and empty lists."""
    world.store.add_source(
        "cli", "connector", {"url": FEED_URL, "secret_env": FEED_ENV, "projects": []}
    )
    made(world, "connector", owner=None, name="feed")
    brook = world.Brook.view
    assert brook.list_sources() == [] and brook.source_credentials() == {}
    assert brook.list_credentials() == [] and brook.get_source("cli") is None
    before = dump(world.db)
    for call in (
        lambda: brook.enable_source("cli", ["ledger"]),
        lambda: brook.disable_source("cli"),
        lambda: brook.source_to_preview("cli"),
        lambda: brook.remove_source("cli"),
        lambda: brook.add_source_from("x", "feed", {}, {}, "ledger"),
    ):
        with pytest.raises(PermissionError) as refused:
            call()
        assert str(refused.value) == NOT_A_SOURCE_MANAGER
    assert dump(world.db) == before


# Invariant 5: a destination its owner no longer reaches stops the write


class Feed:
    """A connector of protocol 1 behind httpx.MockTransport; `during` runs inside the pull."""

    def __init__(self) -> None:
        self.items: list[dict] = []
        self.requests = 0
        self.during = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.during is not None:
            self.during()
        return httpx.Response(
            200, json={"protocol": 1, "items": self.items, "cursor": "c1", "more": False}
        )

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))


def entry(n: int, at: datetime = NOW + MINUTE) -> dict:
    return {
        "id": f"LED-{n}",
        "text": f"# Ledger entry {n}\n\nEntry {n} closes account {n}.\n",
        "title": f"Ledger entry {n}",
        "at": at.isoformat().replace("+00:00", "Z"),
    }


async def _run(store, feed):
    async with feed.client() as http:
        return await run_source(store, "feed-src", http=http, env={FEED_ENV: FEED_SECRET})


def test_owner_without_write_stops_the_write(world):
    made(world, "connector", name="ada-feed")
    shared = world.store.create_project("shared", everyone="read")
    world.store.set_project_access(shared.id, people={world.Ada.id: "manage"})
    world.Ada.view.add_source_from("feed-src", "ada-feed", {}, {}, "shared")
    world.Ada.view.enable_source("feed-src")
    world.clock.now = NOW + 2 * MINUTE
    feed = Feed()
    feed.items = [entry(1)]

    # Mid-run: Ada stops writing "shared" during the pull. Nothing is written.
    def narrowed() -> None:
        world.store.set_project_access(shared.id, people={world.Ada.id: "read"})

    feed.during = narrowed
    report = asyncio.run(_run(world.store, feed))
    assert report.superseded is True and report.saved == 0
    assert world.store.list_notes(shared.id) == []
    info = world.store.get_source("feed-src")
    assert info.last_error == OWNER_CANNOT_WRITE and info.cursor is None
    assert rows(world.db, "source_items") == []

    # At the claim: refused, nothing read, and the claim left free.
    feed.during, feed.requests = None, 0
    before = rows(world.db, "notes")
    from cairn.sources import SourceError

    with pytest.raises(SourceError, match=OWNER_CANNOT_WRITE):
        asyncio.run(_run(world.store, feed))
    assert feed.requests == 0 and rows(world.db, "notes") == before
    refused = world.store.get_source("feed-src")
    assert (refused.last_error, refused.enabled) == (OWNER_CANNOT_WRITE, False)  # switched off

    # Write given back, and switched on again by its owner: the next run writes.
    world.store.set_project_access(shared.id, people={world.Ada.id: "manage"})
    world.Ada.view.enable_source("feed-src")
    world.clock.now = NOW + 4 * MINUTE
    feed.items = [entry(1, NOW + 3 * MINUTE)]  # after it was switched on again
    assert asyncio.run(_run(world.store, feed)).saved == 1

    # Ada leaves the workspace: no run writes for her any more, in a pass either.
    feed.items = [entry(2, NOW + 3 * MINUTE)]
    world.store.remove_member(world.Ada.id)

    async def passed():
        async with feed.client() as http:
            return await sync_all(world.store, http=http, env={FEED_ENV: FEED_SECRET})

    (outcome,) = asyncio.run(passed())
    assert outcome.report is None and outcome.error == OWNER_CANNOT_WRITE
    left = world.store.get_source("feed-src")
    assert (left.last_error, left.enabled) == (OWNER_CANNOT_WRITE, False)
    assert len(world.store.list_notes(shared.id)) == 1


def test_a_refused_claim_switches_the_source_off_once(world):
    """Two passes after the owner lost write make one write: the source is off, with the
    reason, and the second pass leaves it alone."""
    made(world, "connector", name="ada-feed")
    shared = world.store.create_project("shared", everyone="read")
    world.store.set_project_access(shared.id, people={world.Ada.id: "manage"})
    world.Ada.view.add_source_from("feed-src", "ada-feed", {}, {}, "shared")
    world.Ada.view.enable_source("feed-src")
    world.store.set_project_access(shared.id, people={world.Ada.id: "read"})
    feed = Feed()
    feed.items = [entry(1)]

    async def passed():
        async with feed.client() as http:
            return await sync_all(world.store, http=http, env={FEED_ENV: FEED_SECRET})

    world.clock.now = NOW + 2 * MINUTE
    (outcome,) = asyncio.run(passed())
    assert outcome.error == OWNER_CANNOT_WRITE
    once = dump(world.db)
    world.clock.now = NOW + 4 * MINUTE
    (again,) = asyncio.run(passed())  # off: the pass reads and writes nothing
    assert again.report.off is True and again.error is None
    assert dump(world.db) == once and feed.requests == 0
    info = world.store.get_source("feed-src")
    assert (info.enabled, info.last_error) == (False, OWNER_CANNOT_WRITE)
    events = [e for e in world.store.list_audit() if e.action == "source.disabled"]
    assert [e.detail["reason"] for e in events] == [OWNER_CANNOT_WRITE]
    with pytest.raises(PermissionError, match=NOT_YOUR_ACCOUNT):  # its owner's to switch on
        world.Olive.view.enable_source("feed-src")


def test_a_person_who_left_is_a_former_member_even_with_another_workspace(world, api):
    other = world.store.create_account("elsewhere")
    elsewhere = world.store.for_account(other.id)
    elsewhere.accept_invite(elsewhere.invite("ada@example.test").token, world.Ada.id)
    world.store.remove_member(world.Ada.id)
    shown = api.call("GET", "/api/credentials", "olive").json()["credentials"]
    assert {c["name"]: c["owner"] for c in shown} == {
        "ada-mail": "a former member",
        "feed": None,  # the workspace's own
    }


# Invariant 6: four endings


def test_four_endings_do_what_they_say(world):
    made(world, "connector", name="ada-feed")
    ada = world.Ada.view
    ada.add_source_from("feed-src", "ada-feed", {}, {}, "ada-notes")
    ada.enable_source("feed-src")  # one source per connector account (R3)
    world.clock.now = NOW + 2 * MINUTE
    feed = Feed()
    feed.items = [entry(1)]
    notes = world.store.get_project("ada-notes").id
    assert asyncio.run(_run(world.store, feed)).saved == 1

    # Switch off: keeps its cursor and notes.
    cursor = world.store.get_source("feed-src").cursor
    off = ada.disable_source("feed-src")
    assert off.enabled is False and off.cursor == cursor is not None
    assert len(world.store.list_notes(notes)) == 1

    # Disconnect: its sources are switched off in the same write and stay listed; notes stay.
    ada.enable_source("feed-src")
    with pytest.raises(PermissionError):  # an admin who is not its owner may not
        world.Cy.view.remove_credential("ada-feed")
    switched = ada.remove_credential("ada-feed")
    assert switched == ["feed-src"]
    listed = {s.name: s for s in world.store.list_sources()}
    assert listed["feed-src"].enabled is False  # still listed, off
    assert len(world.store.list_notes(notes)) == 1
    events = [e for e in world.store.list_audit() if e.action == "source.disabled"]
    assert (events[0].detail["source"], events[0].detail["reason"]) == ("feed-src", DISCONNECTED)
    with pytest.raises(ValueError, match=DISCONNECTED):  # nothing switches it on again
        world.store.enable_source("feed-src")
    # The name given to another account later does not bring the old sources back.
    world.store.add_credential("ada-feed", "connector", SETTINGS["connector"], owner=None)
    with pytest.raises(ValueError, match=DISCONNECTED):
        world.Olive.view.enable_source("feed-src")

    # Remove the source: its link and its items go; its notes stay.
    assert ada.remove_source("feed-src") is True
    assert len(world.store.list_notes(notes)) == 1
    names = [n for (_, n, *_rest) in rows(world.db, "settings")]
    assert not [n for n in names if n.startswith(SOURCE_CREDENTIAL_PREFIX + "feed-src")]
    assert rows(world.db, "source_items") == []


def test_an_owner_of_the_workspace_disconnects_any_credential(world):
    made(world, "gmail")
    world.Ada.view.add_source_from("mail", "ada-acct", {"label": "x"}, {}, "ada-notes")
    world.Ada.view.enable_source("mail")
    assert world.Olive.view.remove_credential("ada-acct") == ["mail"]
    assert world.store.get_source("mail").enabled is False
    with pytest.raises(KeyError):
        world.Olive.view.remove_credential("ada-acct")


def test_read_only_projects_stay_refused(world):
    made(world, "connector", owner=None, name="feed")
    world.store.create_project("handbook", everyone="read")
    before = dump(world.db)
    with pytest.raises(PermissionError, match=READ_ONLY_PROJECT):
        world.Cy.view.add_source_from("hb", "feed", {}, {}, "handbook")
    assert dump(world.db) == before


# The API (section 4): a key only, never an agent's token


BASE = "http://127.0.0.1:8765"
HIDDEN = ("_env", "url", "base_url", "subject")  # no answer holds a key named so


@pytest.fixture
def api(world):
    from starlette.testclient import TestClient

    from cairn.server import create_app

    world.store.add_credential("ada-mail", "gmail", SETTINGS["gmail"], owner="ada@example.test")
    world.store.add_credential("feed", "connector", SETTINGS["connector"])
    keys = {
        "ada": world.Ada.view.create_key("ada-desk"),
        "cy": world.Cy.view.create_key("cy-desk"),
        "olive": world.Olive.view.create_key("olive-desk"),
        "brook": _member_key(world),
        "operator": world.store.create_key("desk"),
        "reading": world.store.create_key("reading", access="read"),
    }
    app = create_app(world.store, public_url=BASE)
    with TestClient(app, base_url=BASE) as client:

        def call(method: str, path: str, who: str, **kwargs) -> httpx.Response:
            return client.request(method, path, headers={"x-api-key": keys[who]}, **kwargs)

        yield SimpleNamespace(client=client, call=call, keys=keys)


def _member_key(world) -> str:
    """A key acting for Brook, a member: made while Brook was an admin."""
    world.store.set_role(world.Brook.id, "admin")
    key = world.Brook.view.create_key("brook-desk")
    world.store.set_role(world.Brook.id, "member")
    return key


def keys_in(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(keys_in(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(keys_in(v) for v in value)) if value else set()
    return set()


def test_a_person_makes_routes_and_removes_a_source_over_the_api(world, api):
    body = {
        "name": "mail", "credential": "ada-mail", "select": {"label": "to-ledger"},
        "options": {"lookback_days": 3}, "project": "ada-notes", "new_project": None,
    }  # fmt: skip
    made = api.call("POST", "/api/integrations", "ada", json=body)
    assert made.status_code == 201, made.text
    said = made.json()
    assert (said["name"], said["enabled"], said["credential"]) == ("mail", False, "ada-mail")
    assert said["projects"] == ["ada-notes"] and said["select"] == {"label": "to-ledger"}
    assert said["options"] == {"lookback_days": 3}

    # Another person may switch it off, never on or elsewhere; a member not at all.
    refusals = [
        ("POST", "/api/integrations/mail/on", "olive", {"projects": ["ledger"]}, 403),
        ("POST", "/api/integrations/mail/on", "cy", {}, 403),
        ("POST", "/api/integrations/mail/on", "brook", {}, 403),
        ("POST", "/api/integrations/mail/on", "reading", {}, 403),
        ("POST", "/api/integrations/mail/on", "ada", {"projects": ["ledger"]}, 403),
        ("POST", "/api/integrations/nope/on", "ada", {}, 404),
        ("POST", "/api/integrations", "olive", {**body, "name": "m2"}, 403),
        ("POST", "/api/integrations", "ada", {**body, "select": {}}, 400),
        ("POST", "/api/integrations", "ada", {**body, "select": {"base_url": "x"}}, 400),
        ("POST", "/api/integrations", "ada", {**body, "project": None}, 400),
        ("POST", "/api/integrations", "ada", {**body, "credential": "nope"}, 404),
    ]
    before = dump(world.db)
    for method, path, who, payload, status in refusals:
        r = api.call(method, path, who, json=payload)
        assert r.status_code == status, (path, who, r.text)
        assert set(r.json()) == {"error"}
    assert dump(world.db) == before
    r = api.call("POST", "/api/integrations", "ada", json={**body, "select": {"base_url": "x"}})
    assert r.json() == {"error": "base_url is set by the account, not by a source"}
    r = api.call("POST", "/api/integrations/mail/on", "olive", json={})
    assert r.json() == {"error": NOT_YOUR_ACCOUNT}

    on = api.call("POST", "/api/integrations/mail/on", "ada", json={"projects": None})
    assert on.status_code == 200 and on.json()["enabled"] is True, on.text
    off = api.call("POST", "/api/integrations/mail/off", "cy")
    assert off.status_code == 200 and off.json()["enabled"] is False

    # Removing: confirm must match; then its notes stay.
    for confirm, status in ((None, 400), ("other", 400)):
        query = {} if confirm is None else {"confirm": confirm}
        assert api.call("DELETE", "/api/integrations/mail", "ada", params=query).status_code == (
            status
        )
    assert (
        api.call(
            "DELETE", "/api/integrations/mail", "olive", params={"confirm": "mail"}
        ).status_code
        == 403
    )
    gone = api.call("DELETE", "/api/integrations/mail", "ada", params={"confirm": "mail"})
    assert gone.status_code == 200 and gone.json() == {"removed": "mail", "notes_kept": True}
    assert world.store.get_source("mail") is None


def test_the_operators_account_over_the_api_and_the_four_endings(world, api):
    body = {
        "name": "minutes", "credential": "feed", "select": {}, "options": {"limit": 10},
        "project": None, "new_project": "cy-new",
    }  # fmt: skip
    r = api.call("POST", "/api/integrations", "cy", json=body)
    assert r.status_code == 403 and r.json() == {"error": OWNER_DECIDES}
    r = api.call("POST", "/api/integrations", "cy", json={**body, "project": "ledger",
                                                          "new_project": None})  # fmt: skip
    assert r.status_code == 201, r.text
    r = api.call("POST", "/api/integrations/minutes/on", "cy", json={"projects": ["ledger"]})
    assert r.status_code == 200 and r.json()["enabled"] is True
    world.store.add_credential("feed-2", "connector", SETTINGS["connector"])  # reads nothing yet
    taken = api.call("POST", "/api/integrations", "olive", json={**body, "credential": "feed-2",
                                                                  "new_project": None,
                                                                  "project": "ledger"})  # fmt: skip
    assert taken.status_code == 400 and "already used" in taken.json()["error"]

    # Disconnect: an owner of the workspace, confirm matching; its sources go off.
    assert (
        api.call("DELETE", "/api/credentials/feed", "cy", params={"confirm": "feed"}).status_code
        == 403
    )
    assert api.call("DELETE", "/api/credentials/feed", "olive").status_code == 400
    r = api.call("DELETE", "/api/credentials/feed", "olive", params={"confirm": "feed"})
    assert r.status_code == 200 and r.json() == {"credential": "feed", "switched_off": ["minutes"]}
    listed = api.call("GET", "/api/integrations", "olive").json()["sources"]
    assert [(s["name"], s["enabled"], s["credential"]) for s in listed] == [
        ("minutes", False, None)
    ]
    r = api.call("POST", "/api/integrations/minutes/on", "olive", json={})
    assert r.status_code == 400 and DISCONNECTED in r.json()["error"]
    assert api.call("DELETE", "/api/credentials/feed", "olive",
                    params={"confirm": "feed"}).status_code == 404  # fmt: skip


def test_no_answer_names_a_variable_an_address_or_a_subject(world, api):
    world.store.add_source(  # added on the command line: its config names all of them
        "lf", "langfuse", {**SETTINGS["langfuse"], "projects": ["ledger"]}
    )
    world.Ada.view.add_source_from("mail", "ada-mail", {"label": "to-ledger"}, {}, "ada-notes")
    con = sqlite3.connect(world.db)
    with con:
        con.execute(  # a failed run's error names the variable it found unset
            "UPDATE sources SET last_error = ? WHERE name = 'mail'",
            ("R2_GOOGLE is not set in the environment",),
        )
    con.close()
    values = [
        *(v for s in SETTINGS.values() for v in s.values()),
        "https://gmail.googleapis.com", "langfuse.example.test",
    ]  # fmt: skip
    for who in ("olive", "ada", "cy", "operator"):
        listed = api.call("GET", "/api/integrations", who)
        assert listed.status_code == 200
        assert not {k for k in keys_in(listed.json()) if k.endswith(HIDDEN)}, who
        assert not [v for v in values if v in listed.text], (who, listed.text)
    shown = {s["name"]: s for s in api.call("GET", "/api/integrations", "olive").json()["sources"]}
    assert shown["mail"]["credential"] == "ada-mail" and shown["lf"]["credential"] is None
    assert "to-ledger" in shown["mail"]["reads"]
    assert shown["mail"]["last_error"] and "R2_GOOGLE" not in shown["mail"]["last_error"]
    assert api.call("GET", "/api/integrations", "brook").json() == {"sources": []}

    creds = api.call("GET", "/api/credentials", "olive")
    assert creds.status_code == 200
    rows_ = creds.json()["credentials"]
    assert [c["name"] for c in rows_] == ["ada-mail", "feed"]
    assert set(rows_[0]) == {"name", "kind", "owner", "account", "added_at", "sources"}
    assert (rows_[0]["owner"], rows_[0]["account"], rows_[0]["sources"]) == (
        "Ada",
        "ada@example.test",
        1,
    )
    assert rows_[1]["owner"] is None and rows_[1]["account"] is None
    # The account's own address is the one value shown, under "account", and only there.
    assert not {k for k in keys_in(creds.json()) if k.endswith(HIDDEN)}
    hidden = [v for s in SETTINGS.values() for k, v in s.items() if k != "subject"]
    assert not [v for v in hidden if v in creds.text]
    assert [c["name"] for c in api.call("GET", "/api/credentials", "brook").json()[
        "credentials"]] == []  # fmt: skip
    assert api.call("GET", "/api/credentials", "reading").status_code == 403
    # A person who left: their account is still theirs, never the workspace's.
    world.store.remove_member(world.Ada.id)
    left = api.call("GET", "/api/credentials", "olive").json()["credentials"][0]
    assert left["name"] == "ada-mail" and left["owner"] == "a former member"


def test_an_agent_never_reaches_these_routes(world, api):
    import base64
    import hashlib
    import secrets

    client = world.store.register_client("r2-laptop", ["http://127.0.0.1:9/callback"])
    verifier = secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    code = world.Olive.view.create_code(client.id, "http://127.0.0.1:9/callback", challenge)
    token = world.store.redeem_code(code, client.id).access_token
    before = dump(world.db)
    for method, path in (
        ("GET", "/api/credentials"), ("GET", "/api/integrations"),
        ("POST", "/api/integrations"), ("POST", "/api/integrations/x/on"),
        ("POST", "/api/integrations/x/off"), ("DELETE", "/api/integrations/x"),
        ("DELETE", "/api/credentials/feed"),
    ):  # fmt: skip
        r = api.client.request(method, path, headers={"authorization": f"Bearer {token}"})
        assert r.status_code == 401, (method, path)
    assert dump(world.db) == before


# An operator's source is the command line's to remove


def test_an_operators_source_is_removed_on_the_command_line_only(world, api, capsys):
    from cairn.store.sources import OPERATORS_SOURCE
    from cairn.web.cli import main

    config = {**SETTINGS["connector"], "projects": ["ledger"]}
    world.store.add_source("feed-op", "connector", config)
    con = sqlite3.connect(world.db)
    with con:
        seq = con.execute("SELECT seq FROM sources WHERE name = 'feed-op'").fetchone()[0]
        con.execute(
            "INSERT INTO source_items (source, item, written_at) VALUES (?, 'x', '2026-11-16')",
            (seq,),
        )
    con.close()
    before = dump(world.db)
    for who in ("olive", "cy"):
        r = api.call("DELETE", "/api/integrations/feed-op", who, params={"confirm": "feed-op"})
        assert r.status_code == 403 and r.json() == {"error": OPERATORS_SOURCE}, (who, r.text)
    for view in (world.Olive.view, world.Cy.view):
        with pytest.raises(PermissionError, match=re.escape(OPERATORS_SOURCE)):
            view.remove_source("feed-op")
    assert dump(world.db) == before
    assert world.store.get_source("feed-op") is not None
    assert len(rows(world.db, "source_items")) == 1
    # The command line (a view acting for no person) still removes it.
    assert main(["source-remove", "--db", str(world.db), "--name", "feed-op"]) == 0
    assert world.store.get_source("feed-op") is None


# An address is hidden in every form, whatever its case


LIN_URL = "https://lin.example.test:8443/graphql"


@pytest.mark.parametrize(
    "error",
    [
        "could not reach lin.example.test in time",
        "https://LIN.example.test:8443/graphql answered 502",
        "LIN.EXAMPLE.TEST:8443 refused the connection",
    ],
)
def test_an_address_is_hidden_in_every_form_and_case(world, api, error):
    world.store.add_credential(
        "ada-lin", "linear", {"key_env": "R2_LINEAR_KEY", "base_url": LIN_URL},
        owner="ada@example.test",
    )  # fmt: skip
    world.Ada.view.add_source_from("lin", "ada-lin", {"teams": ["LED"]}, {}, "ada-notes")
    con = sqlite3.connect(world.db)
    with con:
        con.execute("UPDATE sources SET last_error = ? WHERE name = 'lin'", (error,))
    con.close()
    listed = api.call("GET", "/api/integrations", "ada")
    assert listed.status_code == 200
    (shown,) = [s for s in listed.json()["sources"] if s["name"] == "lin"]
    assert shown["last_error"] and "the service" in shown["last_error"], shown
    assert "lin.example.test" not in listed.text.casefold(), listed.text


def test_a_link_to_a_missing_account_fails_closed(world):
    """A source linked to a credential that does not exist is refused at the claim and
    switched off, the reason saying its account is gone; it never runs as the workspace's."""
    from cairn.sources import SourceError
    from cairn.store.sources import ACCOUNT_GONE

    world.store.add_source("lone", "connector", {**SETTINGS["connector"], "projects": ["ledger"]})
    world.store.enable_source("lone")
    con = sqlite3.connect(world.db)
    with con:
        con.execute(
            "INSERT INTO settings (account_id, name, value, updated_at) VALUES"
            " ('acct_default', ?, '\"ghost\"', '2026-11-16')",
            (SOURCE_CREDENTIAL_PREFIX + "lone",),
        )
    con.close()
    world.clock.now = NOW + 2 * MINUTE
    feed = Feed()
    feed.items = [entry(1)]

    async def run():
        async with feed.client() as http:
            return await run_source(world.store, "lone", http=http, env={FEED_ENV: FEED_SECRET})

    with pytest.raises(SourceError, match=ACCOUNT_GONE):
        asyncio.run(run())
    assert feed.requests == 0 and rows(world.db, "notes") == []
    info = world.store.get_source("lone")
    assert (info.enabled, info.last_error) == (False, ACCOUNT_GONE)
    events = [e for e in world.store.list_audit() if e.action == "source.disabled"]
    assert [e.detail["reason"] for e in events] == [ACCOUNT_GONE]


# Bounded work: caps on the new paths, and lists that cost the same whatever their length


def test_a_workspace_holds_at_most_so_many_accounts_and_sources(world):
    from cairn.store.sources import CREDENTIALS_MAX, SOURCES_MAX

    assert (CREDENTIALS_MAX, SOURCES_MAX) == (100, 100)
    for n in range(CREDENTIALS_MAX):
        world.store.add_credential(f"c{n}", "connector", SETTINGS["connector"])
    before = dump(world.db)
    with pytest.raises(ValueError, match="100 accounts"):
        world.store.add_credential("one-more", "connector", SETTINGS["connector"])
    assert dump(world.db) == before
    for n in range(SOURCES_MAX):  # source-add is unchanged: no cap of its own
        world.store.add_source(f"s{n}", "connector", {**SETTINGS["connector"], "projects": []})
    before = dump(world.db)
    with pytest.raises(ValueError, match="100 sources"):
        world.store.add_source_from("one-more", "c0", {}, {}, "ledger")
    assert dump(world.db) == before
    world.store.add_source("s-more", "connector", {**SETTINGS["connector"], "projects": []})


def test_the_two_lists_cost_the_same_with_1_and_50(world, api):
    def made(start: int, stop: int) -> None:
        for n in range(start, stop):
            world.store.add_credential(
                f"c{n}", "gmail", SETTINGS["gmail"], owner="ada@example.test"
            )
            world.Ada.view.add_source_from(f"s{n}", f"c{n}", {"label": f"l{n}"}, {}, "ada-notes")

    def cost(who: str, path: str) -> int:
        seen: list[str] = []
        world.store._con.set_trace_callback(seen.append)
        try:
            assert api.call("GET", path, who).status_code == 200
        finally:
            world.store._con.set_trace_callback(None)
        return len(seen)

    asked = [(who, path) for who in ("olive", "cy", "operator")
             for path in ("/api/credentials", "/api/integrations")]  # fmt: skip
    made(0, 1)
    one = {pair: cost(*pair) for pair in asked}
    made(1, 50)
    fifty = {pair: cost(*pair) for pair in asked}
    assert fifty == one
    listed = api.call("GET", "/api/credentials", "olive").json()["credentials"]
    assert len(listed) == 52 and {c["sources"] for c in listed if c["name"][1:].isdigit()} == {1}


# A source whose account was disconnected says so


def test_a_disconnected_source_says_so_and_offers_no_switch_on(world, api):
    world.store.add_source("op", "connector", {**SETTINGS["connector"], "projects": ["ledger"]})
    world.store.disable_source("op")
    world.Ada.view.add_source_from("mail", "ada-mail", {"label": "to-ledger"}, {}, "ada-notes")

    def shown() -> dict:
        listed = api.call("GET", "/api/integrations", "olive").json()["sources"]
        return {s["name"]: s for s in listed}

    before = shown()
    assert before["mail"]["disconnected"] is False and before["mail"]["switch_on"]
    assert before["op"]["disconnected"] is False and before["op"]["credential"] is None
    world.Ada.view.remove_credential("ada-mail")
    after = shown()
    assert (after["mail"]["credential"], after["mail"]["disconnected"]) == (None, True)
    assert after["mail"]["switch_on"] is None
    assert after["op"]["disconnected"] is False and after["op"]["switch_on"]


# A preview of a source made from an account names no variable and no address


def test_a_preview_names_no_variable_of_a_source_made_from_an_account(world, api, monkeypatch):
    from cairn.web.rest import ACCOUNT_KEY_UNSET

    monkeypatch.delenv("R2_GOOGLE", raising=False)
    world.Ada.view.add_source_from("mail", "ada-mail", {"label": "to-ledger"}, {}, "ada-notes")
    world.store.add_source(  # source-add: its sentence stays exactly as R1 pins it
        "op-mail", "gmail", {**SETTINGS["gmail"], "label": "to-ledger", "projects": ["ledger"]}
    )
    before = dump(world.db)
    shown = api.call("POST", "/api/integrations/mail/preview", "ada", json={})
    assert shown.status_code == 200, shown.text
    assert shown.json()["read"] is False and shown.json()["said"] == ACCOUNT_KEY_UNSET
    assert "R2_GOOGLE" not in shown.text and "ada@example.test" not in shown.text
    kept = api.call("POST", "/api/integrations/op-mail/preview", "operator", json={})
    assert kept.json()["said"].startswith("R2_GOOGLE is not set in the environment")
    assert dump(world.db) == before


# A preview before saving (R4, part 2): POST /api/integrations/preview


@pytest.fixture
def unsaved(world, monkeypatch):
    """The app with its sources read through a stand-in feed, its secret set; a key for each
    of Olive (an owner), Cy (an admin), Brook (a member with a connector account of his own)
    and the operator; the workspace's connector account "feed"; Ada's Gmail account."""
    from starlette.testclient import TestClient

    from cairn.server import create_app

    monkeypatch.setenv(FEED_ENV, FEED_SECRET)
    world.store.add_credential("feed", "connector", SETTINGS["connector"])
    world.store.add_credential("ada-mail", "gmail", SETTINGS["gmail"], owner="ada@example.test")
    world.store.add_credential("bk", "connector", SETTINGS["connector"], owner="brook@example.test")
    world.Brook.view.create_project("brook-notes", everyone="none")
    feed = Feed()
    keys = {
        "olive": world.Olive.view.create_key("olive-desk"),
        "cy": world.Cy.view.create_key("cy-desk"),
        "brook": _member_key(world),
        "operator": world.store.create_key("desk"),
    }
    app = create_app(world.store, public_url=BASE, source_http=feed.client)
    with TestClient(app, base_url=BASE) as client:

        def call(method: str, path: str, who: str, **kwargs) -> httpx.Response:
            return client.request(method, path, headers={"x-api-key": keys[who]}, **kwargs)

        yield SimpleNamespace(client=client, call=call, feed=feed)


UNSAVED = {
    "name": "minutes", "credential": "feed", "select": {}, "options": {"limit": 10},
    "project": "ledger", "new_project": None,
}  # fmt: skip


def test_a_preview_before_saving_shows_what_saving_would_bring_and_stores_nothing(world, unsaved):
    unsaved.feed.items = [entry(n, LATELY + n * MINUTE) for n in (1, 2, 3)]
    before = dump(world.db)
    shown = unsaved.call("POST", "/api/integrations/preview", "olive", json={**UNSAVED, "days": 2})
    assert shown.status_code == 200, shown.text
    said = shown.json()
    assert said == {
        "source": "minutes", "days": 2, "read": True, "count": 3,
        "titles": ["Ledger entry 1", "Ledger entry 2", "Ledger entry 3"], "more": 0,
        "written_before": 0, "out_of_reach": 0,
    }  # fmt: skip
    assert dump(world.db) == before and unsaved.feed.requests == 1
    usual = unsaved.call("POST", "/api/integrations/preview", "olive", json=UNSAVED)
    assert usual.json()["days"] == 7  # unless given
    # Into a new private project: its drafts are counted, not out of reach; nothing is made.
    fresh = {**UNSAVED, "project": None, "new_project": "minutes-new"}
    shown = unsaved.call("POST", "/api/integrations/preview", "olive", json=fresh)
    assert (shown.json()["count"], shown.json()["out_of_reach"]) == (3, 0), shown.text
    assert dump(world.db) == before and world.store.get_project("minutes-new") is None
    # Saved, then previewed, it shows the same.
    assert unsaved.call("POST", "/api/integrations", "olive", json=UNSAVED).status_code == 201
    stored = unsaved.call("POST", "/api/integrations/minutes/preview", "olive", json={"days": 2})
    assert stored.json() == said


def test_a_preview_before_saving_refuses_what_saving_refuses(world, unsaved):
    world.store.add_source_from("taken", "feed", {}, {}, "ledger")  # one per connector account
    world.store.add_credential("feed-2", "connector", SETTINGS["connector"])
    world.store.create_project("cy-team", everyone="none")
    other = {**UNSAVED, "credential": "feed-2"}
    refused = [
        ("olive", {**other, "select": {"base_url": "x"}}),  # a setting the account sets
        ("olive", {**other, "select": {"label": "x"}}),  # the connector has no selector
        ("olive", {**other, "project": None}),  # no project
        ("olive", {**other, "new_project": "two"}),  # two
        ("olive", {**other, "project": "ghost"}),  # no such project
        ("olive", {**other, "credential": "nope"}),  # no such account: 404
        ("olive", UNSAVED),  # overlaps "taken", on the same account
        ("olive", {**other, "project": None, "new_project": "ledger"}),  # a taken project name
        ("cy", {**other, "project": "cy-team"}),  # out of the admin's reach
        ("cy", {**other, "project": None, "new_project": "cy-own"}),  # OWNER_DECIDES
        ("cy", {**other, "credential": "ada-mail", "select": {"label": "x"}}),  # Ada's
        ("brook", {**other, "project": "brook-notes"}),  # the workspace's: not his
    ]
    before = dump(world.db)
    for who, body in refused:
        saving = unsaved.call("POST", "/api/integrations", who, json=body)
        assert saving.status_code in (400, 403, 404), (who, body, saving.text)
        assert dump(world.db) == before, (who, body)
        preview = unsaved.call("POST", "/api/integrations/preview", who, json=body)
        assert (preview.status_code, preview.json()) == (saving.status_code, saving.json()), (
            who, body,
        )  # fmt: skip
    for days in (0, 31, "7", True):
        r = unsaved.call("POST", "/api/integrations/preview", "olive", json={**other, "days": days})
        assert r.status_code == 400 and r.json() == {"error": "days is a whole number from 1 to 30"}
    r = unsaved.call("POST", "/api/integrations/preview", "olive", json={**other, "sorce": 1})
    assert r.status_code == 400
    assert dump(world.db) == before and unsaved.feed.requests == 0


def test_a_member_previews_only_from_their_own_account(world, unsaved):
    unsaved.feed.items = [entry(1, LATELY)]
    mine = {**UNSAVED, "credential": "bk", "project": "brook-notes"}
    before = dump(world.db)
    shown = unsaved.call("POST", "/api/integrations/preview", "brook", json=mine)
    assert shown.status_code == 200 and shown.json()["titles"] == ["Ledger entry 1"], shown.text
    for credential in ("feed", "ada-mail"):
        hidden = unsaved.call(
            "POST", "/api/integrations/preview", "brook", json={**mine, "credential": credential}
        )
        ghost = unsaved.call(
            "POST", "/api/integrations/preview", "olive", json={**mine, "credential": "ghost"}
        )
        assert hidden.status_code == ghost.status_code == 404
        assert hidden.json() == {"error": ghost.json()["error"].replace("ghost", credential)}
    led = unsaved.call(
        "POST", "/api/integrations/preview", "brook", json={**mine, "project": "ledger"}
    )
    assert led.status_code == 403  # he writes "ledger" but does not manage it
    assert dump(world.db) == before and unsaved.feed.requests == 1


# A name is one per workspace (R4, second reading): what a member learns of a taken one


def _names_held(world):
    """Ada's source "ada-mail" (from her account, in her private project), the workspace's
    "team", the command line's "cli"; Brook's account "bk" and his own source "mail"."""
    made(world, "gmail")  # Ada's
    made(world, "connector", owner=None, name="feed")
    world.store.add_credential("bk", "gmail", SETTINGS["gmail"], owner="brook@example.test")
    world.Ada.view.add_source_from("ada-mail", "ada-acct", {"label": "x"}, {}, "ada-notes")
    world.store.add_source_from("team", "feed", {}, {}, "ledger")
    world.store.add_source(
        "cli", "connector", {"url": FEED_URL, "secret_env": FEED_ENV, "projects": []}
    )
    world.Brook.view.create_project("brook-notes", everyone="none")
    world.Brook.view.add_source_from("mail", "bk", {"label": "x"}, {}, "brook-notes")


HELD = ("ada-mail", "ADA-MAIL", "team", "cli")  # names Brook does not see


def _refusal(call, *args, **kwargs) -> tuple[type, str]:
    try:
        call(*args, **kwargs)
    except Exception as refused:
        return type(refused), str(refused)
    raise AssertionError(f"not refused: {args}")


def test_a_request_refused_anyway_learns_nothing_of_a_taken_name(world):
    """(a) The checks on the caller's own account, what it reads and where it writes come
    first: a request refused for any of them is refused the same, taken name or free."""
    _names_held(world)
    add = world.Brook.view.add_source_from
    refused = [
        (("bk", {"label": "y"}, {}, "ledger"), {}),  # he writes "ledger", does not manage it
        (("bk", {"label": "y"}, {}, "board"), {}),  # out of his reach
        (("bk", {"label": "y"}, {}, "nowhere"), {}),  # no such project
        (("bk", {}, {}, "brook-notes"), {}),  # nothing chosen to read
        (("bk", {"label": "x"}, {}, "brook-notes"), {}),  # what "mail" reads already
        (("feed", {}, {}, "brook-notes"), {}),  # not his account
        (("bk", {"label": "y"}, {}, None), {"new_project": "ledger"}),  # a taken project name
    ]
    before = dump(world.db)
    for args, kwargs in refused:
        free = _refusal(add, "free-name", *args, **kwargs)
        for held in (*HELD, "mail"):
            assert _refusal(add, held, *args, **kwargs) == free, (held, args, kwargs)
    assert dump(world.db) == before


def test_a_name_held_out_of_sight_is_taken_in_one_sentence(world):
    """(b) A name held by a source the caller does not see is refused in one sentence, the
    same whatever holds it, which neither repeats nor confirms the name."""
    _names_held(world)
    add = world.Brook.view.add_source_from
    assert NAME_TAKEN == "that name is taken: choose another"
    before = dump(world.db)
    for held in HELD:
        assert _refusal(add, held, "bk", {"label": "y"}, {}, "brook-notes") == (
            ValueError, NAME_TAKEN,
        ), held  # fmt: skip
        assert _refusal(add, held, "bk", {"label": "y"}, {}, None, new_project="brook-two") == (
            ValueError, NAME_TAKEN,
        ), held  # fmt: skip
    mine = _refusal(add, "MAIL", "bk", {"label": "y"}, {}, "brook-notes")
    assert mine == (ValueError, "source name already used: 'MAIL'")  # his own: he sees it
    assert dump(world.db) == before
    world.store.add_credential("feed-2", "connector", SETTINGS["connector"])
    for manager in (world.Olive.view, world.Cy.view, world.store):  # they see every source
        for held in HELD:
            said = _refusal(manager.add_source_from, held, "feed-2", {}, {}, "ledger")
            assert said == (ValueError, f"source name already used: {held!r}")
    assert world.store.get_project("brook-two") is None


def test_a_name_held_out_of_sight_over_the_api(world, api):
    world.store.add_credential("bk", "gmail", SETTINGS["gmail"], owner="brook@example.test")
    world.Brook.view.create_project("brook-notes", everyone="none")
    world.Ada.view.add_source_from("ada-sync", "ada-mail", {"label": "x"}, {}, "ada-notes")
    world.store.add_source_from("team", "feed", {}, {}, "ledger")
    body = {
        "name": "ada-sync", "credential": "bk", "select": {"label": "y"}, "options": {},
        "project": "brook-notes", "new_project": None,
    }  # fmt: skip
    before = dump(world.db)
    for held in ("ada-sync", "TEAM"):
        r = api.call("POST", "/api/integrations", "brook", json={**body, "name": held})
        assert (r.status_code, r.json()) == (400, {"error": NAME_TAKEN}), held
    r = api.call("POST", "/api/integrations", "brook", json={**body, "project": "ledger"})
    assert r.status_code == 403  # where it writes is refused first
    assert dump(world.db) == before


def test_an_unsaved_preview_takes_no_name(world, unsaved):
    """(c) A preview before saving takes no name, so it checks none: a taken name, one that
    breaks the rules, the same answer as a free one."""
    unsaved.feed.items = [entry(1, LATELY)]
    world.store.add_source_from("minutes", "feed", {}, {}, "ledger")
    world.store.add_source(
        "cli", "connector", {"url": FEED_URL, "secret_env": FEED_ENV, "projects": []}
    )
    world.store.add_credential("feed-2", "connector", SETTINGS["connector"])
    bodies = {
        "brook": {**UNSAVED, "credential": "bk", "project": "brook-notes"},
        "olive": {**UNSAVED, "credential": "feed-2"},
    }
    before = dump(world.db)
    for who, body in bodies.items():
        free = unsaved.call("POST", "/api/integrations/preview", who, json={**body, "name": "free"})
        assert free.status_code == 200, free.text
        for name in ("minutes", "CLI", "not a name!", "x" * 65):
            r = unsaved.call("POST", "/api/integrations/preview", who, json={**body, "name": name})
            assert r.status_code == 200, (who, name, r.text)
            assert r.json() == {**free.json(), "source": name}
    assert world.Brook.view.source_from_to_preview("minutes", "bk", {}, {}, "brook-notes")
    assert dump(world.db) == before


# One preview at a time per account, saved or not (R4, second reading)

OTHER_URL = "https://other-ledger.example.test/cairn/feed"


class Gated:
    """A connector of protocol 1 whose answers from the host `held` wait for `gate`;
    `arrived` is set when the first of them comes in."""

    def __init__(self, held: str) -> None:
        self.held = held
        self.gate = asyncio.Event()
        self.arrived = asyncio.Event()

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == self.held:
            self.arrived.set()
            await self.gate.wait()
        return httpx.Response(200, json={"protocol": 1, "items": [], "cursor": "c1", "more": False})


def test_previews_of_one_account_are_one_at_a_time_and_only_once_checked(world):
    made(world, "connector", owner=None, name="feed")
    world.store.add_credential("feed-2", "connector", {"url": OTHER_URL, "secret_env": FEED_ENV})
    world.store.add_source_from("from-other", "feed-2", {}, {}, "ledger")
    world.store.add_source(
        "cli", "connector", {"url": OTHER_URL, "secret_env": FEED_ENV, "projects": ["ledger"]}
    )
    gated = Gated("ledger.example.test")
    env = {FEED_ENV: FEED_SECRET}
    store, brook = world.store, world.Brook.view

    def unsaved(view, name, credential, project="ledger"):
        return preview_new_source(view, name, credential, {}, {}, project, http=http, env=env)

    def stored(view, name):
        return preview_source(view, name, http=http, env=env)

    async def scene():
        # Named after a source of another account: it holds the account "feed", not the name.
        first = asyncio.create_task(unsaved(store, "from-other", "feed"))
        await gated.arrived.wait()
        assert (await stored(store, "from-other")).read is True
        with pytest.raises(PreviewBusy):  # another unsaved preview of "feed", any name
            await unsaved(world.Olive.view, "b", "FEED")
        # A request the store refuses is refused as it would be, busy account or not.
        with pytest.raises(ValueError, match="no project named"):
            await unsaved(store, "c", "feed", "ghost")
        with pytest.raises(KeyError):
            await unsaved(store, "c", "nope")
        store.add_source_from("from-feed", "feed", {}, {}, "ledger")
        with pytest.raises(PreviewBusy):  # a stored source of "feed", in the same words
            await stored(store, "from-feed")
        with pytest.raises(ValueError, match="already reads"):  # "from-feed" reads it all now
            await unsaved(store, "c", "feed")
        with pytest.raises(KeyError):
            await stored(store, "nope")
        with pytest.raises(PermissionError):
            await stored(brook, "from-feed")
        assert (await stored(store, "cli")).read is True  # no account: its own turn
        # Only the first holds anything: refused requests held nothing, and let nothing go.
        assert runner._previewing == {(store.account_id, "account:feed")}
        gated.gate.set()
        return await first

    http = httpx.AsyncClient(transport=httpx.MockTransport(gated.handle))
    assert asyncio.run(scene()).read is True
    assert runner._previewing == set()
    assert asyncio.run(stored(store, "from-feed")).read is True  # let go after


def test_a_held_account_answers_409_over_the_api_once_checked(world, unsaved):
    """R4, second reading: while a preview of "feed" is under way (held here by hand), every
    preview of that account answers 409 in the same words, saved or not, and nothing else:
    a refused request still gets its refusal, and another account its preview."""
    world.store.add_credential("feed-2", "connector", SETTINGS["connector"])
    world.store.add_source_from("from-feed", "feed", {}, {}, "ledger")
    held = (world.store.account_id, "account:feed")
    busy = {"error": runner.PREVIEW_BUSY}
    path = "/api/integrations/preview"
    runner._previewing.add(held)
    try:
        r = unsaved.call("POST", "/api/integrations/from-feed/preview", "olive", json={})
        assert (r.status_code, r.json()) == (409, busy)
        r = unsaved.call("POST", path, "olive", json=UNSAVED)
        assert r.status_code == 400 and "already reads" in r.json()["error"]  # not busy
        assert unsaved.call("POST", path, "brook", json=UNSAVED).status_code == 404
        r = unsaved.call("POST", path, "olive", json={**UNSAVED, "credential": "feed-2"})
        assert r.status_code == 200, r.text
        assert world.store.remove_source("from-feed") is True
        r = unsaved.call("POST", path, "olive", json={**UNSAVED, "project": "ghost"})
        assert r.status_code == 400 and "no project named" in r.json()["error"]  # not busy
        r = unsaved.call("POST", path, "olive", json=UNSAVED)
        assert (r.status_code, r.json()) == (409, busy)
    finally:
        runner._previewing.discard(held)
