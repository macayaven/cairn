"""Sentences of the user guides (src/cairn/guides/*.md) that the store and the sources runner
make true, each pinned here so the guide cannot go false unnoticed. The guide's sentence is
in each test's docstring. Everything runs over a real store in a temporary file, with
synthetic notes; a fake source kind stands in for Langfuse."""

import asyncio
import io
import json
import logging
import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from cairn.compiler import compile_note
from cairn.sources import KINDS, NoteDraft, Pulled, SourceKind, run_source
from cairn.store import PERSON_ACCESS, Removed, Store
from cairn.store.usage import Limits
from cairn.web.members import PERSON_LABELS

ISSUER = "https://id.example.test/pool-1"
MARMOT = "http://127.0.0.1:33418/marmot"
OCTOBER = datetime(2026, 10, 14, 9, tzinfo=UTC)
NOVEMBER = datetime(2026, 11, 10, 12, tzinfo=UTC)
PASSWORD_OR_PAYMENT = {
    "password", "passwd", "passphrase", "pwd", "card", "cvc", "cvv", "iban", "bank", "pan",
}  # fmt: skip
LETTERS = "aB3dE5gH7jK9mN1pQ2rS4tU6vW8xY0zA"  # made up; shaped like a secret, never one


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


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


def person(store: Store, name: str, *, join: str = "default") -> Store:
    signed = store.sign_in(
        "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER, join=join
    )
    return store.for_account(signed.account_id, signed.user.id)


def noted(view: Store, project_id: str, text: str, **fields):
    note, _ = view.add_note(project_id, text, **fields)
    assert compile_note(view, note).status == "saved"
    return note


# patterns.md: one reach holds a set and a project together


def test_a_connection_that_ticks_a_set_and_a_project_reaches_both_and_nothing_else(store):
    """patterns.md, "A product team with AI features": "Engineers' coding agents connect once
    each and tick the set `engineering` plus `handbook`."""
    ids = {n: store.create_project(n).id for n in ("therapy", "tracking", "handbook", "story")}
    engineering = store.create_set("engineering", (ids["therapy"], ids["tracking"]))
    key = store.create_key("coding-agent", projects=(engineering.id, ids["handbook"]))
    who = store.resolve_key(key)
    view = store.narrow(who.projects, who.access)
    assert sorted(b.name for b in view.list_projects()) == ["handbook", "therapy", "tracking"]
    for name in ("therapy", "tracking", "handbook"):
        assert view.get_project(name) is not None, name
    assert view.get_project("story") is None and view.get_project(ids["story"]) is None


# sources.md: cairn adds; it does not take back


class Feed:
    """A source whose pulls answer, in turn, what the test put in `answers`."""

    answers: ClassVar[list[Pulled]] = []

    def __init__(self, name: str) -> None:
        self.name = name

    async def pull(self, cursor):
        return Feed.answers.pop(0)


def _check(config):
    return dict(config)


def _open(name, config, *, http, env, clock=None, sleep=None, deadline=None, since=None):
    return Feed(name)


@pytest.fixture
def feed(monkeypatch, store):
    monkeypatch.setitem(KINDS, "feed", SourceKind(check=_check, open=_open, via="feed"))
    Feed.answers = []
    store.create_project("meetings")
    store.add_source("meetings-feed", "feed", {"projects": ["meetings"]})
    return store


def run(store: Store, **options):
    return asyncio.run(
        run_source(store, "meetings-feed", http=httpx.AsyncClient(), env={}, **options)
    )


def meeting(n: int, text: str, title: str = "Payments weekly") -> NoteDraft:
    return NoteDraft(
        project="meetings", title=title, text=text, source_ref=f"meeting:m-{n}", label="meeting"
    )


def test_a_later_run_that_no_longer_returns_an_item_takes_nothing_back(feed, db):
    """sources.md: "cairn adds; it does not take back ... An item removed at the source stays
    in cairn until someone forgets it there.\""""
    first = meeting(41, "# Payments weekly\n\nAda moved the release to Friday.\n")
    second = meeting(42, "# Payments weekly\n\nBrook owns the retry fix.\n")
    Feed.answers = [Pulled(drafts=[first, second], cursor="c1")]
    assert run(feed).saved == 2
    project = feed.get_project("meetings").id
    notes = {c.id: c.source_ref for c in feed.list_notes(project)}
    pages = {p.slug: feed.page_versions(project, p.slug) for p in feed.list_pages(project)}
    claims = {c.text for p in feed.list_pages(project) for c in p.claims}
    assert sorted(notes.values()) == ["meeting:m-41", "meeting:m-42"] and pages

    # The source no longer has meeting 41; its next run returns only a new meeting.
    third = meeting(43, "# Payments weekly\n\nCy reviews the ledger on Monday.\n")
    Feed.answers = [Pulled(drafts=[third], cursor="c2")]
    assert run(feed).saved == 1
    after = {c.id: c.source_ref for c in feed.list_notes(project)}
    assert notes.items() <= after.items()  # every note of the first run is still there
    assert all(feed.get_note(c) is not None for c in notes)
    for slug, versions in pages.items():
        assert set(versions) <= set(feed.page_versions(project, slug)), slug
    now = {c.text for p in feed.list_pages(project) for c in p.claims}
    assert claims <= now  # and so is every claim it made, on the latest version
    assert any("Ada moved the release to Friday" in text for text in now)


# privacy.md: a note the guard refuses during a source run


def test_a_note_the_guard_refuses_in_a_source_run_leaves_only_its_link_and_project(
    feed, db, caplog
):
    """privacy.md, "What cairn never stores": "A note the note guard refused. Nothing of it
    is stored or logged, only that a note was refused and under which rule. When the note came
    from a source the server reads, the source's last report also keeps the link to the item
    that was refused.\""""
    title = "Quarzite rollout credentials"
    text = f'# {title}\n\nThe staging box uses db_password = "{LETTERS[:20]}" for now.\n'
    refused = NoteDraft(
        project="meetings", title=title, text=text,
        source_ref="https://notes.example.test/m-77", label="meeting",
    )  # fmt: skip
    Feed.answers = [Pulled(drafts=[refused], cursor="c1")]
    with caplog.at_level(logging.DEBUG):
        report = run(feed)  # under the workspace's own policy, which by default is "secrets"
    assert (report.saved, report.refused) == (0, 1)
    project = feed.get_project("meetings").id
    stored = dump(db)
    logged = "\n".join(r.getMessage() + json.dumps(r.__dict__, default=str) for r in caplog.records)
    for part in (LETTERS[:20], "Quarzite", "staging box"):
        assert part not in stored, part
        assert part not in logged, part
    assert feed.list_notes(project) == []
    declined = feed.get_source("meetings-feed").last_report["declined"]
    assert declined == [
        {
            "link": "https://notes.example.test/m-77",
            "status": "refused",
            "codes": ["assigned_secret"],
            "project": project,
        }
    ]
    assert [e.action for e in feed.list_audit(5)][:2] == ["source.synced", "note.refused"]


# run.md and privacy.md: what an export holds


def test_an_export_holds_no_key_session_agent_set_setting_source_or_audit_log(store):
    """privacy.md, "Export": "It does not hold keys, sessions, agents' connections, sets,
    other settings, sources or the audit log; of the settings it holds one, each project's
    **Models** choice." run.md says the same of a backup made with `export`."""
    view = store.for_account(store.create_account("Heron").id)
    ops = view.create_project("ops", "operations", kind="repo")
    noted(view, ops.id, "# Queue runbook\n\nThe queue restarts after 3 failures.\n")
    view.create_key("wombat-key")
    view.create_session()
    view.create_set("zephyr-set", (ops.id,))
    view.set_setting("guard.policy", "secrets+personal")
    view.add_source(
        "quokka-feed",
        "langfuse",
        {
            "base_url": "https://langfuse.example.test",
            "projects": ["ops"],
            "public_key_env": "QUOKKA_PUBLIC_KEY",
            "secret_key_env": "QUOKKA_SECRET_KEY",
        },
    )
    agent = store.register_client("marmot-agent", [MARMOT])
    assert store.redeem_code(view.create_code(agent.id, MARMOT, "c" * 43), agent.id)
    out = io.BytesIO()
    view.export(out)
    archive = zipfile.ZipFile(io.BytesIO(out.getvalue()))
    held = {name: archive.read(name) for name in archive.namelist()}
    assert any(b"The queue restarts after 3 failures." in data for data in held.values())
    absent = (
        "wombat-key", "zephyr-set", "guard.policy", "secrets+personal", "quokka-feed",
        "QUOKKA", "langfuse", "marmot", "key.created", "set.created", "agent.connected",
        "session",
    )  # fmt: skip
    for word in absent:
        assert all(word not in name for name in held), word
        assert all(word.encode() not in data for data in held.values()), word


# run.md, security.md and privacy.md: no passwords


def test_no_table_has_a_column_for_a_password_or_a_payment_detail(store, db):
    """security.md: "cairn keeps no passwords." privacy.md, "What cairn never stores":
    "Passwords." No table has a column for a payment detail either."""
    con = sqlite3.connect(db)
    try:
        tables = [t for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        columns = {
            f"{table}.{column}"
            for table in tables
            for (column,) in con.execute("SELECT name FROM pragma_table_info(?)", (table,))
        }
    finally:
        con.close()
    assert len(tables) > 20 and "users.email" in columns  # the schema was read
    named = {c: set(c.split(".", 1)[1].lower().split("_")) for c in columns}
    assert sorted(c for c, words in named.items() if words & PASSWORD_OR_PAYMENT) == []


# security.md: an admin's invitation brings a removed person back


def test_a_removed_person_comes_back_by_an_admins_invitation_and_not_by_signing_in(store):
    """security.md, "Who reaches what": "A removed person cannot come back by signing in
    again: an owner or an admin has to invite them.\""""
    ada = person(store, "Ada")
    brook, cy = person(store, "Brook"), person(store, "Cy")
    ada.set_role(brook.user_id, "admin")
    assert brook.remove_member(cy.user_id) is True  # an admin removes too

    with pytest.raises(Removed):
        person(store, "Cy")
    assert [m.name for m in ada.list_members()] == ["Ada", "Brook"]

    brook.invite("cy@example.test")  # the admin invites; no owner takes part
    back = person(store, "Cy")
    assert back.role() == "member"
    assert [m.name for m in ada.list_members()] == ["Ada", "Brook", "Cy"]


# security.md: three levels for a person on a project


def test_the_third_level_decides_one_projects_rule_and_removes_nothing(store, db):
    """security.md, "Who reaches what": "A project's rule gives a person Read only, Read and
    write, or Read and write, and decide who reaches it. The third holds for that one project
    and is not a workspace role: whoever has it changes who reaches that project, their own
    level included, but cannot shut out its maker or an owner, and cannot delete the project or
    forget its notes. A workspace admin decides for a project only as any member does: as its
    maker, or when its rule gives them that level.\""""
    ada, brook, cy, dee = (person(store, name) for name in ("Ada", "Brook", "Cy", "Dee"))
    ada.set_role(brook.user_id, "admin")
    handbook = ada.create_project("handbook")
    journal = cy.create_project("journal", everyone="none")
    note = noted(cy, journal.id, "# Diary\n\nThe Quill review is on Thursday.\n")
    assert PERSON_ACCESS == ("manage", "write", "read")
    assert (PERSON_LABELS["read"], PERSON_LABELS["write"], PERSON_LABELS["manage"]) == (
        "Read only",
        "Read and write",
        "Read and write, and decide who reaches it",
    )
    with pytest.raises(ValueError):
        cy.set_project_access(journal.id, everyone="manage")  # a person's level only

    for view in (brook, dee):  # the admin and a member alike: not theirs to decide
        with pytest.raises(PermissionError):
            view.project_access(handbook.id)
    for view in (brook, dee):  # each decides for a project they made
        made = view.create_project(f"made-by-{view.user_id[-4:]}", everyone="read")
        assert view.set_project_access(made.id, everyone="none").everyone == "none"

    cy.set_project_access(journal.id, people={brook.user_id: "manage"})
    with pytest.raises(PermissionError):
        brook.set_project_access(handbook.id, everyone="read")  # one project, not a role
    shut_out = {brook.user_id: "manage", cy.user_id: "read", ada.user_id: "read"}
    brook.set_project_access(journal.id, people=shut_out)
    cy.add_note(journal.id, "# Diary\n\nCy still writes here.\n")
    ada.add_note(journal.id, "# Diary\n\nAda still writes here.\n")
    before = dump(db)
    assert brook.may_erase(journal.id) is False
    with pytest.raises(PermissionError):
        brook.delete_project(journal.id)
    with pytest.raises(PermissionError):
        brook.forget_note(note.id)
    assert dump(db) == before
    brook.set_project_access(journal.id, people={dee.user_id: "read"})  # his own level too
    assert "journal" not in [b.name for b in brook.list_projects()]
    with pytest.raises(PermissionError):
        dee.add_note(journal.id, "# Diary\n\nDee only reads.\n")


def test_a_list_of_addresses_gives_members_a_level_and_strangers_nothing(store, db):
    """security.md, "Who reaches what": "Several people can be given a level at once by email
    address. An address that is not a member's gets nothing and no invitation, so someone
    outside the workspace is invited under People first.\""""
    ada, cy, dee = (person(store, name) for name in ("Ada", "Cy", "Dee"))
    eli = person(store, "Eli", join="personal")  # signed up, in a workspace of his own
    journal = cy.create_project("journal", everyone="none")
    shared = cy.share_project(journal.id, "write", ["dee@example.test", "eli@example.test"])
    assert (shared.granted, shared.unknown) == ((dee.user_id,), ("eli@example.test",))
    assert cy.project_access(journal.id).people == {dee.user_id: "write"}
    assert ada.list_invites() == []
    assert eli.user_id not in str(store.list_audit()[0].detail)  # nothing given to Eli

    invitation = ada.invite("eli@example.test")  # under People
    store.accept_invite(invitation.token, eli.user_id)
    assert cy.share_project(journal.id, "read", ["eli@example.test"]).granted == (eli.user_id,)
    assert cy.project_access(journal.id).people == {dee.user_id: "write", eli.user_id: "read"}


# privacy.md: an invitation opens for seven days


def test_an_invitation_opens_for_seven_days_and_not_a_second_more(db):
    """privacy.md, "How long it is kept": "An invitation | 7 days, or until it is used or
    revoked.\""""
    clock = Clock(NOVEMBER)
    store = Store(db, clock=clock)
    try:
        ada = person(store, "Ada")
        dee = person(store, "Dee", join="personal")
        invitation = ada.invite("dee@example.test")
        clock.now = NOVEMBER + timedelta(days=7) - timedelta(seconds=1)
        assert store.find_invite(invitation.token) is not None
        clock.now = NOVEMBER + timedelta(days=7)
        assert store.find_invite(invitation.token) is None
        with pytest.raises(KeyError):
            store.accept_invite(invitation.token, dee.user_id)
    finally:
        store.close()


# privacy.md: forgetting while the write-ahead log is held open


def test_forgetting_a_note_while_another_reader_holds_the_log_succeeds_and_warns_once(
    store, db, caplog
):
    """privacy.md, "The write-ahead log": "When another connection to the database keeps it
    from doing so, the deletion stands, but the text can stay in that log until the next
    checkpoint that succeeds; the server logs a warning when this happens.\""""
    project = store.create_project("ops")
    gone = noted(store, project.id, "# Heron runbook\n\nHeron restarts the queue at night.\n")
    store._con.execute("PRAGMA busy_timeout = 0")  # refuse at once, not after 5 s of retries
    reader = sqlite3.connect(db)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM notes").fetchone()
        with caplog.at_level(logging.WARNING, logger="cairn.store"):
            store.forget_note(gone.id)
        assert store.get_note(gone.id) is None and store.list_pages(project.id) == []
        (warning,) = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warning.name == "cairn.store" and "write-ahead log" in warning.getMessage()
        assert gone.id in warning.getMessage() and "Heron" not in warning.getMessage()
    finally:
        reader.close()


# privacy.md: one digest per forgotten note, bounded by no month


def test_forgetting_adds_one_digest_per_note_whatever_the_months_cap_on_notes(db):
    """privacy.md, "One note": "Digests are not counted against a plan; a project holds at most
    one for each note it ever held." The audit found no monthly bound: forgetting adds one
    digest for each note forgotten, whenever it was written."""
    clock = Clock(OCTOBER)
    store = Store(db, clock=clock)
    try:
        project = store.create_project("ops")
        written = [
            noted(store, project.id, f"# Note {n}\n\nKestrel batch {n} runs at {n} am.\n")
            for n in (1, 2, 3)
        ]
        clock.now = NOVEMBER
        capped = store.limited(Limits(notes_per_month=1))
        noted(capped, project.id, "# November\n\nThe month's one note is written.\n")
        for note in written:  # all three, in a month whose cap of one is already used
            capped.forget_note(note.id)
        con = sqlite3.connect(db)
        try:
            (digests,) = con.execute(
                "SELECT count(*) FROM forgotten WHERE project_id = ?", (project.id,)
            ).fetchone()
        finally:
            con.close()
        assert digests == 3
    finally:
        store.close()


def test_a_removed_person_is_told_who_can_invite_them_back():
    """The message a removed person reads names everyone whose invitation works: an admin's
    does too (the test above), so sending them to an owner only would be wrong."""
    from cairn.store.people import REMOVED

    assert "owners or admins" in REMOVED


# sources.md, "Sources the server reads" (S2)


class Opened:
    """A kind that starts off; it records the `since` each run hands it."""

    since: ClassVar[list] = []

    @staticmethod
    def open(name, config, **options):
        Opened.since.append(options["since"])
        return Feed(name)


@pytest.fixture
def minutes(monkeypatch, store):
    kind = SourceKind(check=_check, open=Opened.open, via="minutes", starts_off=True)
    monkeypatch.setitem(KINDS, "minutes", kind)
    Feed.answers, Opened.since = [], []
    store.create_project("meetings")
    store.add_source("standup", "minutes", {"projects": []})
    return store


def test_a_kind_that_starts_off_is_added_off_and_an_off_source_is_not_read(minutes, db):
    """sources.md: "a source of that kind is added off, and reads nothing until someone
    switches it on. A source that is off is not read at all: a sync passes over it and
    nothing is written.\""""
    from cairn.sources import sync_all

    assert minutes.get_source("standup").enabled is False
    before = dump(db)
    outcomes = asyncio.run(sync_all(minutes, http=httpx.AsyncClient(), env={}))
    assert [(o.source, o.report.off) for o in outcomes] == [("standup", True)]
    assert Opened.since == [] and dump(db) == before


def test_switching_on_hands_each_run_the_moment_and_off_and_on_goes_on_from_there(store, db):
    """sources.md: "each run hands the source the moment it was last switched on ...
    Switched off and on again, a source keeps its place and the notes it wrote, and a kind
    that starts off reads nothing from the time it was off.\""""
    clock = Clock(OCTOBER)
    view = Store(db, clock=clock)
    try:
        KINDS["minutes"] = SourceKind(
            check=_check, open=Opened.open, via="minutes", starts_off=True
        )
        Opened.since = []
        view.create_project("meetings")
        view.add_source("standup", "minutes", {"projects": ["meetings"]})
        view.enable_source("standup")
        Feed.answers = [Pulled(drafts=[meeting(1, "# Payments weekly\n\nAda.\n")], cursor="c1")]
        asyncio.run(run_source(view, "standup", http=httpx.AsyncClient(), env={}))
        clock.now = NOVEMBER
        view.disable_source("standup")
        view.enable_source("standup")
        Feed.answers = [Pulled(cursor="c2")]
        asyncio.run(run_source(view, "standup", http=httpx.AsyncClient(), env={}))
        assert Opened.since == [OCTOBER, NOVEMBER]
        assert view.get_source("standup").cursor == "c2"
        assert len(view.list_notes(view.get_project("meetings").id)) == 1
    finally:
        del KINDS["minutes"]
        view.close()


def test_a_new_project_made_when_switching_on_is_private_and_an_admin_needs_write(minutes):
    """sources.md: "An owner can also type the name of a new project: it is made private, so
    only they and the workspace's other owners reach it until they share it." ... "An admin
    chooses only among projects every member of the workspace reaches and that the admin may
    write to: making a new project for a source, or sending it into a project private to some
    people, is for an owner.\""""
    ada = person(minutes, "Ada")  # the owner
    cy = person(minutes, "Cy")
    minutes.set_role(cy.user_id, "admin")
    minutes.create_project("handbook", everyone="read")
    cy.create_project("cy-inbox", everyone="none")
    for call in (
        lambda: cy.enable_source("standup", ["handbook"]),  # every member reads, Cy too
        lambda: cy.enable_source("standup", new_project="board"),
        lambda: cy.enable_source("standup", ["cy-inbox"]),  # Cy writes, members do not
    ):
        with pytest.raises(PermissionError):
            call()
    assert cy.enable_source("standup", ["meetings"]).enabled  # every member writes there
    ada.enable_source("standup", [], new_project="board")
    board = minutes.get_project("board")
    assert minutes.project_access(board.id).everyone == "none" and board.maker_id == ada.user_id
    assert cy.get_project("board") is None  # an admin does not reach it


def test_every_manager_sees_every_source_and_any_can_switch_it_off(minutes):
    """sources.md: "Every owner and admin sees every source on that page, and any of them
    can switch any source off; where a source writes is named only for the projects you
    reach, and the others show as one row, \"a project you do not reach\".\""""
    ada = person(minutes, "Ada")  # the owner
    cy = person(minutes, "Cy")
    minutes.set_role(cy.user_id, "admin")
    ada.enable_source("standup", [], new_project="board")  # private: owners only
    (shown,) = cy.list_sources()
    assert (shown.name, shown.config["projects"], shown.unreached) == ("standup", [], True)
    assert cy.disable_source("standup").enabled is False
    assert ada.get_source("standup").config["projects"] == ["board"]


def test_deleting_a_project_switches_off_a_source_left_with_none(minutes):
    """sources.md: "Deleting a project takes it out of every source that writes into it, and a
    source left with no project is switched off: a new project of the same name gets nothing
    until someone switches the source on and chooses it.\""""
    minutes.add_source("retro", "minutes", {"projects": ["meetings"]})
    minutes.enable_source("standup", ["meetings"])
    minutes.enable_source("retro")
    minutes.delete_project(minutes.get_project("meetings").id)
    for name in ("standup", "retro"):
        source = minutes.get_source(name)
        assert (source.enabled, source.config["projects"]) == (False, [])
    minutes.create_project("meetings")
    with pytest.raises(ValueError):
        minutes.enable_source("standup")  # it names no project: someone has to choose one
    assert minutes.enable_source("standup", ["meetings"]).enabled


def test_a_project_made_private_by_one_who_is_not_an_owner_leaves_its_sources(minutes):
    """sources.md: "Making a project private, so that no member reaches it any more, does the
    same when anyone but an owner does it: the project is taken out of every source that
    writes into it.\""""
    ada, eve = person(minutes, "Ada"), person(minutes, "Eve")  # the owner, a member
    shared = eve.create_project("eve-notes", everyone="write")
    owned = ada.create_project("ada-notes", everyone="write")
    minutes.add_source("retro", "minutes", {"projects": ["ada-notes"]})
    minutes.enable_source("standup", ["eve-notes"])
    minutes.enable_source("retro")
    eve.set_project_access(shared.id, everyone="none")
    ada.set_project_access(owned.id, everyone="none")
    standup, retro = minutes.get_source("standup"), minutes.get_source("retro")
    assert (standup.enabled, standup.config["projects"]) == (False, [])
    assert (retro.enabled, retro.config["projects"]) == (True, ["ada-notes"])


def test_langfuse_reads_a_mark_made_while_it_was_off_once_it_is_on_again(store, monkeypatch):
    """sources.md: "Langfuse is the exception: a trace is read because a person marked it, so
    a mark made while the source was off is read once it is on again.\""""
    clock = Clock(OCTOBER)
    marked: list[datetime] = []

    def stamp(at: datetime) -> str:
        return at.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def langfuse(request: httpx.Request) -> httpx.Response:
        start = datetime.fromisoformat(request.url.params.get("fromTimestamp", stamp(OCTOBER)))
        if request.url.path == "/api/public/v3/scores":
            scores = [
                {"id": f"s{i}", "projectId": "p", "name": "cairn", "dataType": "CATEGORICAL",
                 "value": "meetings", "source": "EVAL", "timestamp": stamp(at),
                 "environment": "production", "createdAt": stamp(at), "updatedAt": stamp(at),
                 "comment": "why", "configId": None, "metadata": None,
                 "subject": {"kind": "observation", "id": f"o{i}", "traceId": f"t{i}"}}
                for i, at in enumerate(marked) if at >= start
            ]  # fmt: skip
            return httpx.Response(200, json={"data": scores, "meta": {"limit": 100}})
        trace = request.url.params["traceId"]
        root = {
            "id": f"o{trace[1:]}", "traceId": trace, "startTime": stamp(OCTOBER),
            "endTime": stamp(OCTOBER), "projectId": "p", "parentObservationId": None,
            "type": "SPAN", "name": "handle", "level": "DEFAULT", "environment": "production",
            "isRootObservation": True, "input": f"Question {trace}?",
            "output": f"Answer {trace}.", "metadata": {}, "traceName": "flow", "tags": [],
        }  # fmt: skip
        return httpx.Response(200, json={"data": [root], "meta": {"limit": 100}})

    monkeypatch.setenv("G_PUBLIC", "pk-lf-guide")
    monkeypatch.setenv("G_SECRET", "sk-lf-guide")
    view = store
    view.create_project("meetings")
    config = {
        "base_url": "https://langfuse.example.test",
        "projects": ["meetings"],
        "public_key_env": "G_PUBLIC",
        "secret_key_env": "G_SECRET",
    }
    view.add_source("traces", "langfuse", config)
    http = httpx.AsyncClient(transport=httpx.MockTransport(langfuse))

    def sync():
        env = {"G_PUBLIC": "pk-lf-guide", "G_SECRET": "sk-lf-guide"}
        return asyncio.run(run_source(view, "traces", http=http, env=env, clock=clock))

    marked.append(OCTOBER - timedelta(hours=1))
    assert sync().saved == 1
    clock.now = OCTOBER + timedelta(hours=1)
    view.disable_source("traces")
    marked.append(OCTOBER + timedelta(hours=2))  # marked while it is off
    clock.now = OCTOBER + timedelta(hours=3)
    assert sync().off
    view.enable_source("traces")
    assert sync().saved == 1


def test_a_source_goes_to_the_default_workspace_unless_the_command_names_another(db, capsys):
    """sources.md: "**Which workspace.** A source belongs to one workspace. `source-add` and
    the other source commands act on the default workspace unless `--workspace ACCOUNT` names
    another. Where each person has a workspace of their own (`serve --join personal`, which
    is what `serve` does unless told otherwise), a source added without it goes to the
    default workspace, not to theirs: name the person's workspace, and `workspace-list`
    prints the ids.\" The same in deploy/README.md."""
    from cairn import server
    from cairn.store import DEFAULT_ACCOUNT_ID
    from cairn.web import cli

    root = Path(__file__).resolve().parents[2]
    said = " ".join((root / "src/cairn/guides/sources.md").read_text().split())
    assert (
        "**Which workspace.** A source belongs to one workspace. `source-add` and the other"
        " source commands act on the default workspace unless `--workspace ACCOUNT` names"
        " another. Where each person has a workspace of their own (`serve --join personal`,"
        " which is what `serve` does unless told otherwise), a source added without it goes to"
        " the default workspace, not to theirs: name the person's workspace, and"
        " `workspace-list` prints the ids." in said
    )
    readme = " ".join((root / "deploy/README.md").read_text().split())
    assert (
        "Each acts on the default workspace unless `--workspace ACCOUNT` names another: with"
        " `--join personal` name the person's own, whose id `workspace-list` prints." in readme
    )
    assert cli._parser().parse_args(["serve", "--db", str(db)]).join == "personal"

    store = Store(db)
    try:
        ada = person(store, "Ada", join="personal")
        theirs = ada._account_id
        assert theirs != DEFAULT_ACCOUNT_ID
    finally:
        store.close()
    config = '{"key_env": "GRANOLA_API_KEY"}'
    added = ["source-add", "--db", str(db), "--kind", "granola", "--config", config]
    assert server.main([*added, "--name", "unnamed"]) == 0
    assert server.main([*added, "--name", "named", "--workspace", theirs]) == 0
    assert server.main(["workspace-list", "--db", str(db)]) == 0
    listed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert theirs in [row["account"] for row in listed]
    store = Store(db)
    try:
        assert [s.name for s in store.list_sources()] == ["unnamed"]
        assert [s.name for s in store.for_account(theirs).list_sources()] == ["named"]
    finally:
        store.close()
