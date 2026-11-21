"""Two sources that claim one item (R3, invariant 4): held, written by neither.

Two sources made from one account may select one item: a Gmail message carrying the labels
of both. Such an item is held: written by neither, recorded by neither, listed under both
with its link, and judged again on the next run, whichever source runs first. Kinds whose
selectors can only overlap wholesale refuse the second source when it is made. A source
added with source-add is not touched by either rule. Every name here is made up; every
request goes to an httpx.MockTransport.
"""

import asyncio
import base64
import html
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from cairn.sources import run_source
from cairn.store import Store
from cookie_names import session_cookie

ISSUER = "https://id.example.test/pool-r3"
NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
MINUTE = timedelta(minutes=1)
GOOGLE_ENV = "R3_GOOGLE"
GOOGLE_DOC = {  # an authorized_user document: every value made up
    "type": "authorized_user",
    "client_id": "r3-" + "desktop-client" + ".apps.example.test",
    "client_secret": "r3" + "-made-up-" + "client-secret-5be1",
    "refresh_token": "1//" + "r3" + "MadeUpRefresh" + "Token77aa",
}
ENV = {GOOGLE_ENV: json.dumps(GOOGLE_DOC)}
LEDGER, BOARD = "to-ledger", "to-board"


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


def rows(db: Path, table: str) -> list:
    con = sqlite3.connect(db)
    try:
        return con.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    finally:
        con.close()


class Gmail:
    """Google's token endpoint and the three Gmail calls a pull makes: labels.list,
    messages.list (by label id) and messages.get (format=full), as one httpx mock."""

    def __init__(self) -> None:
        self.labels = [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "Label_11", "name": LEDGER, "type": "user"},
            {"id": "Label_12", "name": BOARD, "type": "user"},
            {"id": "Label_13", "name": "elsewhere", "type": "user"},
        ]
        self.messages: dict[str, dict] = {}
        self.gets: list[str] = []  # messages.get, format=full
        self.looks: list[str] = []  # messages.get of labels and headers only

    def id_of(self, name: str) -> str:
        return next(label["id"] for label in self.labels if label["name"] == name)

    def mail(self, n: int, *labels: str, at: datetime = NOW + MINUTE) -> str:
        message_id = f"r3msg{n:04d}"
        self.messages[message_id] = {
            "at": at,
            "labels": {"INBOX", *(self.id_of(name) for name in labels)},
            "subject": f"Item {n}: the plan",
            "body": f"Item {n} is agreed; Brook sends the figures.",
        }
        return message_id

    def unlabel(self, message_id: str, name: str) -> None:
        self.messages[message_id]["labels"].discard(self.id_of(name))

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(
                200, json={"access_token": "ya29." + "r3-made-up-token-0001",
                           "expires_in": 3599, "token_type": "Bearer"},
            )  # fmt: skip
        path = request.url.path
        if path.endswith("/labels"):
            return httpx.Response(200, json={"labels": self.labels})
        if path.endswith("/messages"):
            wanted = request.url.params["labelIds"]
            after = int(request.url.params["q"].removeprefix("after:"))
            listed = [  # newest first, as Gmail lists them, those after `after:` only
                {"id": message_id, "threadId": message_id}
                for message_id, m in sorted(
                    self.messages.items(), key=lambda pair: (pair[1]["at"], pair[0]), reverse=True
                )
                if wanted in m["labels"] and m["at"].timestamp() > after
            ]
            return httpx.Response(200, json={"messages": listed, "resultSizeEstimate": 1})
        message_id = path.rsplit("/", 1)[1]
        how = request.url.params.get("format", "full")
        if how == "full":
            self.gets.append(message_id)
        else:
            self.looks.append(message_id)
        m = self.messages[message_id]
        data = base64.urlsafe_b64encode(m["body"].encode()).decode().rstrip("=")
        headers = [
            {"name": "Subject", "value": m["subject"]},
            {"name": "From", "value": "Ada Lovelace <ada@example.test>"},
            {"name": "To", "value": "Brook Lane <brook@example.test>"},
        ]
        payload: dict = {"mimeType": "text/plain", "headers": headers}
        if how == "metadata":  # labels and headers only: no body
            wanted = request.url.params.get_list("metadataHeaders")
            payload = {"headers": [h for h in headers if h["name"] in wanted]}
        else:
            payload["body"] = {"size": len(m["body"]), "data": data}
        return httpx.Response(
            200,
            json={
                "id": message_id,
                "threadId": message_id,
                "labelIds": sorted(m["labels"]),
                "internalDate": str(int(m["at"].timestamp() * 1000)),
                "payload": payload,
            },
        )


@pytest.fixture
def world(tmp_path):
    """Olive owns the workspace, Ada administers it and owns the Google account "ada-g";
    "ada-notes" is Ada's own private project."""
    clock = Clock(NOW)
    db = tmp_path / "cairn.db"
    store = Store(db, clock=clock)
    people = {}
    for name, role in (("Olive", "owner"), ("Ada", "admin")):
        signed = store.sign_in(
            "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER,
            join="default",
        )  # fmt: skip
        store.set_role(signed.user.id, role)
        people[name] = SimpleNamespace(
            id=signed.user.id, view=store.for_account(signed.account_id, signed.user.id)
        )
    people["Ada"].view.create_project("ada-notes", everyone="none")
    store.add_credential(
        "ada-g", "gmail", {"credentials_env": GOOGLE_ENV}, owner="ada@example.test"
    )
    yield SimpleNamespace(store=store, db=db, clock=clock, gmail=Gmail(), **people)
    store.close()


def two_sources(world) -> None:
    ada = world.Ada.view
    ada.add_source_from("ledger-mail", "ada-g", {"label": LEDGER}, {}, "ada-notes")
    ada.add_source_from("board-mail", "ada-g", {"label": BOARD}, {}, "ada-notes")
    ada.enable_source("ledger-mail")
    ada.enable_source("board-mail")
    world.clock.now = NOW + 5 * MINUTE


def run(world, name: str):
    async def go():
        transport = httpx.MockTransport(world.gmail.handle)
        async with httpx.AsyncClient(transport=transport) as http:
            return await run_source(world.store, name, http=http, env=ENV, clock=world.clock)

    return asyncio.run(go())


def notes(world) -> list[str]:
    project = world.store.get_project("ada-notes").id
    return sorted(note.source_ref for note in world.store.list_notes(project))


def held(world, name: str) -> list[dict]:
    report = world.store.get_source(name).last_report or {}
    return [{k: d[k] for k in ("title", "link")} for d in report.get("held_items", [])]


HELD_ONE = [{"title": "Item 1: the plan", "link": "gmail:r3msg0001"}]


# Invariant 4: held, written by neither


def test_a_message_with_two_chosen_labels_goes_nowhere(world):
    two_sources(world)
    both = world.gmail.mail(1, LEDGER, BOARD)
    world.gmail.mail(2, LEDGER)
    assert run(world, "ledger-mail").saved == 1
    assert run(world, "board-mail").saved == 0
    assert notes(world) == ["gmail:r3msg0002"]  # the held message: written by neither
    assert rows(world.db, "source_items") == []  # and recorded by neither
    assert held(world, "ledger-mail") == HELD_ONE == held(world, "board-mail")

    # Judged again on the next run: its labels looked at again, held again, written nowhere.
    world.gmail.gets.clear()
    world.gmail.looks.clear()
    world.clock.now += 5 * MINUTE
    run(world, "ledger-mail")
    assert world.gmail.gets == [] and world.gmail.looks == [both]  # never read in full
    assert notes(world) == ["gmail:r3msg0002"]
    assert held(world, "ledger-mail") == HELD_ONE


def test_held_messages_never_crowd_out_new_ones(world):
    """60 held messages and 5 new ones, the held ones older: the 5 are written in the first
    pull. A message's labels are looked at first (format=metadata: LOOKS a pull for ones not
    known, RECHECKS for held ones); it is read in full (READS a pull) only once not held."""
    from cairn.sources import gmail as g

    two_sources(world)
    held_ids = [world.gmail.mail(n, LEDGER, BOARD, at=NOW + MINUTE) for n in range(1, 61)]
    new_ids = [world.gmail.mail(n, LEDGER, at=NOW + 2 * MINUTE) for n in range(61, 66)]
    report = run(world, "ledger-mail")
    assert report.saved == 5 and sorted(world.gmail.gets) == sorted(new_ids)
    assert sorted(world.gmail.looks) == sorted(held_ids + new_ids)
    assert len(report.holding) == 20 and report.holding_more == 40
    assert len(world.gmail.looks) <= g.LOOKS and len(world.gmail.gets) <= g.READS

    world.gmail.gets.clear()
    world.gmail.looks.clear()
    world.clock.now += 5 * MINUTE
    assert run(world, "ledger-mail").saved == 0
    assert world.gmail.gets == [] and sorted(world.gmail.looks) == sorted(held_ids)

    world.gmail.unlabel(held_ids[0], BOARD)  # no longer held: now read in full, and written
    world.gmail.gets.clear()
    world.clock.now += 5 * MINUTE
    assert run(world, "ledger-mail").saved == 1 and world.gmail.gets == [held_ids[0]]


def test_more_held_than_the_cursor_keeps_never_starves_new_mail(world):
    """700 held (more than HELD_KEPT) and 5 new: the 5 are written within two pulls, and no
    pull looks or reads past its budgets."""
    from cairn.sources import gmail as g

    two_sources(world)
    for n in range(1, 701):
        world.gmail.mail(n, LEDGER, BOARD, at=NOW + MINUTE)
    new_ids = [world.gmail.mail(n, LEDGER, at=NOW + 2 * MINUTE) for n in range(701, 706)]
    saved = 0
    for _ in range(2):
        world.gmail.gets.clear()
        world.gmail.looks.clear()
        saved += run(world, "ledger-mail").saved
        assert len(world.gmail.looks) <= g.LOOKS + g.RECHECKS
        assert len(world.gmail.gets) <= g.READS
        world.clock.now += 5 * MINUTE
    assert saved == 5 and notes(world) == sorted(f"gmail:{m}" for m in new_ids)


def test_every_message_not_known_is_looked_at_in_turn(world, monkeypatch):
    """The looks not given to the newest go round the others in turn, from where the last
    pull stopped: with the cursor full, a message in the middle is reached all the same."""
    from cairn.sources import gmail as g

    monkeypatch.setattr(g, "HELD_KEPT", 2)
    monkeypatch.setattr(g, "LOOKS", 4)
    monkeypatch.setattr(g, "NEWEST", 1)
    two_sources(world)
    for n in range(1, 9):
        world.gmail.mail(n, LEDGER, BOARD, at=NOW + MINUTE)
    middle = world.gmail.mail(9, LEDGER, at=NOW + 2 * MINUTE)
    for n in range(10, 13):
        world.gmail.mail(n, LEDGER, BOARD, at=NOW + 3 * MINUTE)
    for _ in range(4):
        run(world, "ledger-mail")
        world.clock.now += 5 * MINUTE
    assert notes(world) == [f"gmail:{middle}"]


def test_the_cursor_holds_no_message_id_with_another_source_on(world):
    """As C6's long run asserts, but with board-mail on: what is held is kept by digest."""
    two_sources(world)
    held_ids = [world.gmail.mail(n, LEDGER, BOARD) for n in range(1, 4)]
    world.gmail.mail(4, LEDGER)
    for _ in range(2):
        run(world, "ledger-mail")
        world.clock.now += 5 * MINUTE
    cursor = world.store.get_source("ledger-mail").cursor
    assert isinstance(json.loads(cursor), dict) and json.loads(cursor)["holds"]
    for words in ("r3msg", "Item", "plan", *held_ids):  # message ids, subjects
        assert words not in cursor, words


def test_a_cursor_that_held_raw_ids_is_read_and_its_held_ids_dropped():
    """A cursor written before the held map was keyed by digest ("held", message ids) still
    decodes: the ids are dropped, so those messages are looked at again as unknown ones."""
    from cairn.sources import gmail as g

    old = json.dumps({"v": 1, "done": {}, "parts": {}, "held": {"r3msg0001": [1, 2]}})
    decoded = g.Cursor.decode(old)
    assert decoded.held == {} and "r3msg0001" not in decoded.encode()


def test_the_cursor_keeps_at_most_held_kept_held_messages(world, monkeypatch):
    """Beyond HELD_KEPT, a held message is not kept in the cursor: it is listed as held all
    the same, and looked at again as one not known on the next pull."""
    from cairn.sources import gmail as g

    monkeypatch.setattr(g, "HELD_KEPT", 3)
    two_sources(world)
    held_ids = [world.gmail.mail(n, LEDGER, BOARD) for n in range(1, 6)]
    report = run(world, "ledger-mail")
    assert len(report.holding) == 5 and world.gmail.gets == []
    from cairn.sources.gmail import digest

    kept = json.loads(world.store.get_source("ledger-mail").cursor)["holds"]
    assert sorted(kept) == sorted(digest(m) for m in held_ids[:3])  # the oldest three

    world.gmail.looks.clear()
    world.clock.now += 5 * MINUTE
    assert len(run(world, "ledger-mail").holding) == 5
    assert sorted(world.gmail.looks) == held_ids and world.gmail.gets == []


def test_held_items_are_listed_under_both_over_the_api(world):
    from starlette.testclient import TestClient

    from cairn.server import create_app

    two_sources(world)
    world.gmail.mail(1, LEDGER, BOARD)
    run(world, "ledger-mail")
    run(world, "board-mail")
    key = world.Ada.view.create_key("ada-desk")
    base = "http://127.0.0.1:8765"
    with TestClient(create_app(world.store, public_url=base), base_url=base) as client:
        listed = client.get("/api/integrations", headers={"x-api-key": key}).json()["sources"]
    shown = {s["name"]: s for s in listed}
    for name in ("ledger-mail", "board-mail"):
        assert shown[name]["held"] == HELD_ONE and shown[name]["held_more"] == 0


def answer(world, name: str) -> dict:
    from starlette.testclient import TestClient

    from cairn.server import create_app

    key = world.Ada.view.create_key(f"ada-desk-{len(world.store.list_sources())}")
    base = "http://127.0.0.1:8765"
    with TestClient(create_app(world.store, public_url=base), base_url=base) as client:
        listed = client.get("/api/integrations", headers={"x-api-key": key}).json()["sources"]
    return next(s for s in listed if s["name"] == name)


@pytest.mark.parametrize("later", [timedelta(days=30, hours=1), timedelta(days=40)])
def test_a_held_message_that_leaves_the_window_is_counted(world, later):
    """Within the listing's margin (looked at, older than the line) or past it (no longer
    listed): left unwritten, and counted once, where the held items are shown."""
    two_sources(world)
    world.gmail.mail(1, LEDGER, BOARD)
    assert len(run(world, "ledger-mail").holding) == 1
    world.clock.now = NOW + MINUTE + later
    report = run(world, "ledger-mail")
    assert report.saved == 0 and report.holding == [] and report.held_expired == 1
    assert notes(world) == []
    kept = world.store.get_source("ledger-mail").last_report
    assert kept["held_expired"] == 1
    shown = answer(world, "ledger-mail")
    assert shown["held_expired"] == 1
    assert shown["held_said"] == "1 held item left the look-back window unwritten"

    world.clock.now += 5 * MINUTE  # counted once
    assert run(world, "ledger-mail").held_expired == 0
    assert "held_expired" not in world.store.get_source("ledger-mail").last_report
    assert answer(world, "ledger-mail")["held_said"] is None


def test_a_source_switched_on_during_a_pull_holds_by_what_is_true_after_it(world, monkeypatch):
    """The selectors are read again after the pull, before any draft is written: board-mail
    switched on while ledger-mail pulls holds the item both select, and the run stops there,
    as at its deadline: nothing after it is written, and its cursor does not move past it.
    The next run holds it again and writes the one after it, once."""
    import dataclasses

    from cairn.sources import KINDS, NoteDraft, Pulled

    ada = world.Ada.view
    ada.add_source_from("ledger-mail", "ada-g", {"label": LEDGER}, {}, "ada-notes")
    ada.add_source_from("board-mail", "ada-g", {"label": BOARD}, {}, "ada-notes")
    ada.enable_source("ledger-mail")

    class Switching:  # a kind that keeps nothing unread itself: no `holding`
        secrets: tuple[str, ...] = ()
        pulls: list = []  # noqa: RUF012

        async def pull(self, cursor):
            self.pulls.append(cursor)
            if not world.store.get_source("board-mail").enabled:
                ada.enable_source("board-mail")  # meanwhile, in another session

            def draft(n, *labels):
                return NoteDraft(
                    project="ada-notes", title=f"Item {n}", text=f"Item {n} is agreed.",
                    source_ref=f"gmail:r3msg{n:04d}", label="mail", resume=f"after-{n}",
                    item=f"m{n}",
                    selectors=tuple(f"label:{x}" for x in labels),
                )  # fmt: skip

            return Pulled([draft(1, LEDGER), draft(2, LEDGER, BOARD), draft(3, LEDGER)], [], "end")

    def opened(name, config, **options):
        return Switching()

    monkeypatch.setitem(KINDS, "gmail", dataclasses.replace(KINDS["gmail"], open=opened))
    report = run(world, "ledger-mail")
    assert report.saved == 1 and [d["link"] for d in report.holding] == ["gmail:r3msg0002"]
    assert notes(world) == ["gmail:r3msg0001"]  # the third waits for the next run
    assert world.store.get_source("ledger-mail").cursor == "after-1"  # read again from there

    again = run(world, "ledger-mail")  # board-mail on before it: held, and the run goes on
    assert Switching.pulls == [None, "after-1"]
    assert again.saved == 1 and [d["link"] for d in again.holding] == ["gmail:r3msg0002"]
    assert notes(world) == ["gmail:r3msg0001", "gmail:r3msg0003"]  # each once


def test_held_more_counts_each_item_once_whatever_its_parts():
    from cairn.sources.runner import HELD_MAX, SyncReport

    report = SyncReport()
    for n in range(HELD_MAX + 3):
        for part in ("", "/2", "/3"):  # the parts of one item: one item
            report.hold_item(f"Item {n}", f"gmail:m{n}{part}", None)
    assert len(report.holding) == HELD_MAX and report.holding_more == 3


def test_a_held_title_holding_a_credential_is_screened_as_a_previews(world):
    from cairn.sources.runner import HELD_TITLE

    two_sources(world)
    held_id = world.gmail.mail(1, LEDGER, BOARD)
    world.gmail.messages[held_id]["subject"] = "Item 1: " + GOOGLE_DOC["client_secret"]
    report = run(world, "ledger-mail")
    assert [d["title"] for d in report.holding] == [HELD_TITLE]
    assert GOOGLE_DOC["client_secret"] not in json.dumps(
        world.store.get_source("ledger-mail").last_report
    )


@pytest.mark.parametrize("first, second", [("ledger-mail", "board-mail"),
                                           ("board-mail", "ledger-mail")])  # fmt: skip
def test_held_does_not_depend_on_which_runs_first(world, first, second):
    two_sources(world)
    world.gmail.mail(1, LEDGER, BOARD)
    world.gmail.mail(2, LEDGER)
    world.gmail.mail(3, BOARD)
    run(world, first)
    run(world, second)
    assert notes(world) == ["gmail:r3msg0002", "gmail:r3msg0003"]
    assert held(world, "ledger-mail") == HELD_ONE == held(world, "board-mail")


def test_held_comes_in_once_one_label_goes(world):
    two_sources(world)
    one = world.gmail.mail(1, LEDGER, BOARD)
    run(world, "ledger-mail")
    run(world, "board-mail")
    assert notes(world) == []
    world.gmail.unlabel(one, BOARD)  # one label left: the ledger source's alone
    world.clock.now += 5 * MINUTE
    assert run(world, "ledger-mail").saved == 1
    assert held(world, "ledger-mail") == []
    world.clock.now += 5 * MINUTE
    run(world, "ledger-mail")
    run(world, "board-mail")
    assert notes(world) == ["gmail:r3msg0001"]  # once


def test_a_source_switched_off_holds_nothing_and_source_add_is_untouched(world):
    two_sources(world)
    world.Ada.view.disable_source("board-mail")
    world.gmail.mail(1, LEDGER, BOARD)
    assert run(world, "ledger-mail").saved == 1  # only sources that are on hold an item
    world.store.create_project("ops")  # elsewhere: the same text in one project is one note
    config = {"credentials_env": GOOGLE_ENV, "label": BOARD, "projects": ["ops"]}
    world.store.add_source("op-board", "gmail", config)  # source-add: no link, no hold
    world.store.enable_source("op-board")
    world.gmail.mail(2, LEDGER, BOARD, at=world.clock.now + MINUTE)  # after its switch-on
    world.clock.now += 5 * MINUTE
    assert run(world, "op-board").saved == 1
    world.store.add_source("op-board-2", "gmail", config)  # nor any refusal when made


# Refused when made: selectors that can only overlap wholesale


SETTINGS = {
    "linear": {"key_env": "R3_LINEAR_KEY"},
    "github": {"token_env": "R3_GH_TOKEN"},
    "langfuse": {
        "base_url": "https://langfuse.example.test",
        "public_key_env": "R3_LF_PUBLIC",
        "secret_key_env": "R3_LF_SECRET",
    },
    "connector": {"url": "https://ledger.example.test/feed", "secret_env": "R3_FEED_SECRET"},
    "google_drive": {"credentials_env": GOOGLE_ENV},
    "granola": {"key_env": "R3_GRANOLA_KEY"},
    "google_calendar": {"credentials_env": GOOGLE_ENV},
    "gmail": {"credentials_env": GOOGLE_ENV},
}
PROJECT = "0b7c1d2e-3f40-4a5b-8c6d-7e8f9a0b1c2d"
FOLDER, SPACE = "fol_" + "Ab3" * 4 + "Zz", "spa_" + "Cd4" * 4 + "Yy"
FOLDER_2, SPACE_2 = "fol_" + "Ef5" * 4 + "Xx", "spa_" + "Gh6" * 4 + "Ww"
OVERLAPS = [  # (kind, the first source's selector, the second's, refused?)
    ("linear", {"teams": ["LED"]}, {"teams": ["led", "OPS"]}, True),  # a team twice
    ("linear", {"teams": ["LED"]}, {"linear_project": PROJECT}, True),  # teams beside one
    ("linear", {"linear_project": PROJECT}, {"teams": ["OPS"]}, True),
    ("linear", {"linear_project": PROJECT}, {"linear_project": PROJECT}, True),
    ("linear", {"teams": ["LED"]}, {"teams": ["OPS"]}, False),
    (
        "github",
        {"repo": "quillworks/quill"},
        {"repo": "Quillworks/Quill", "items": ["issues"]},
        True,
    ),
    (
        "github",
        {"repo": "quillworks/quill", "items": ["pulls"]},
        {"repo": "quillworks/quill", "items": ["releases"]},
        False,
    ),
    ("github", {"repo": "quillworks/quill"}, {"repo": "quillworks/ink"}, False),
    ("langfuse", {}, {}, True),  # one project twice
    ("connector", {}, {}, True),  # one source per account
    ("google_drive", {"folder_id": "fold-r3-ledger-a"}, {"folder_id": "fold-r3-ledger-a"}, True),
    ("google_drive", {"folder_id": "fold-r3-ledger-a"}, {"folder_id": "fold-r3-ledger-b"}, False),
    ("granola", {"folder_id": FOLDER}, {"folder_id": FOLDER}, True),
    ("granola", {"space_id": SPACE}, {"space_id": SPACE}, True),
    # Granola: a folder may sit in the space, a note may sit in any number of folders, and
    # so in the spaces of each: no two selections of one account are shown to be apart
    ("granola", {"folder_id": FOLDER}, {"space_id": SPACE}, True),
    ("granola", {"space_id": SPACE}, {"folder_id": FOLDER}, True),
    ("granola", {"folder_id": FOLDER}, {"folder_id": FOLDER_2}, True),
    ("granola", {"folder_id": FOLDER}, {"folder_id": FOLDER.upper()}, True),
    ("granola", {"space_id": SPACE}, {"space_id": SPACE_2}, True),
    ("google_calendar", {"calendar_id": "primary"}, {"calendar_id": "primary"}, True),
    ("google_calendar", {"calendar_id": "primary"}, {"calendar_id": "PRIMARY"}, True),
    ("google_calendar", {"calendar_id": "Team@Ex.test"}, {"calendar_id": "team@ex.test"}, True),
    # `primary` beside any other id: cairn cannot tell it is not the primary calendar's id
    ("google_calendar", {"calendar_id": "primary"}, {"calendar_id": "team@example.test"}, True),
    ("google_calendar", {"calendar_id": "ada@example.test"}, {"calendar_id": "Primary"}, True),
    ("google_calendar", {"calendar_id": "led@ex.test"}, {"calendar_id": "board@ex.test"}, False),
    ("gmail", {"label": LEDGER}, {"label": LEDGER.upper()}, True),
    ("gmail", {"label": LEDGER}, {"label": BOARD}, False),  # held per message instead
]


@pytest.mark.parametrize("kind, first, second, refused", OVERLAPS)
def test_overlapping_selectors_are_refused_when_made(world, kind, first, second, refused):
    world.store.add_credential("acct", kind, SETTINGS[kind], owner="ada@example.test")
    ada = world.Ada.view
    ada.add_source_from("one", "acct", first, {}, "ada-notes")
    ada.disable_source("one")  # off or on, it still claims what it selects
    before = dump(world.db)
    if refused:
        with pytest.raises(ValueError, match="already reads"):
            ada.add_source_from("two", "acct", second, {}, "ada-notes")
        assert dump(world.db) == before  # nothing kept
    else:
        ada.add_source_from("two", "acct", second, {}, "ada-notes")


def test_primary_beside_the_address_the_account_reads_as_is_refused(world):
    """With `subject` the address is known to cairn, and still `primary` beside it, or beside
    any other id, is refused: the primary calendar's id only usually matches that address."""
    settings = {"credentials_env": GOOGLE_ENV, "subject": "ada@example.test"}
    world.store.add_credential("cal", "google_calendar", settings, owner="ada@example.test")
    ada = world.Ada.view
    ada.add_source_from("main", "cal", {"calendar_id": "primary"}, {}, "ada-notes")
    before = dump(world.db)
    for other in ("ada@example.test", "Ada@Example.TEST", "team@example.test"):
        with pytest.raises(ValueError, match="already reads"):
            ada.add_source_from("two", "cal", {"calendar_id": other}, {}, "ada-notes")
    assert dump(world.db) == before


@pytest.mark.parametrize(
    "kind, a, b",
    [
        ("granola", {}, {"folder_id": FOLDER}),  # no selector: every note the key reads
        ("granola", {"folder_id": None, "space_id": None}, {"space_id": SPACE}),
        ("granola", {}, {}),
        ("google_calendar", {}, {"calendar_id": "primary"}),  # no id is primary
        ("google_calendar", {}, {"calendar_id": "team@example.test"}),
    ],
)
def test_a_selection_left_out_overlaps_in_both_orders(kind, a, b):
    from cairn.sources import overlaps

    assert overlaps(kind, a, b) and overlaps(kind, b, a)


def test_two_calendars_by_two_other_ids_are_apart():
    from cairn.sources import overlaps

    one, two = {"calendar_id": "led@example.test"}, {"calendar_id": "board@example.test"}
    assert not overlaps("google_calendar", one, two)
    assert not overlaps("google_calendar", {**one, "subject": "ada@example.test"}, two)


@pytest.mark.parametrize(
    "kind, first, second",
    [
        ("granola", {"folder_id": FOLDER}, {"space_id": SPACE}),
        ("google_calendar", {"calendar_id": "primary"}, {"calendar_id": "team@example.test"}),
    ],
)
def test_a_preview_before_saving_refuses_as_saving_does(world, kind, first, second):
    world.store.add_credential("acct", kind, SETTINGS[kind], owner="ada@example.test")
    ada = world.Ada.view
    ada.add_source_from("one", "acct", first, {}, "ada-notes")
    before = dump(world.db)
    with pytest.raises(ValueError, match="already reads") as said:
        ada.source_from_to_preview("two", "acct", second, {}, "ada-notes")
    with pytest.raises(ValueError) as saving:
        ada.add_source_from("two", "acct", second, {}, "ada-notes")
    assert str(said.value) == str(saving.value)
    assert dump(world.db) == before


# A pair made before the rule refused it: kept, never switched on again or moved, and while
# both are on neither run reads or writes anything


GRANOLA_KEY = {"R3_GRANOLA_KEY": "grn_" + "r3MadeUpKey" * 2}
PAIRS = {  # kind: (the first source's selector, the second's), both accepted before this rule
    "granola": ({"folder_id": FOLDER}, {"space_id": SPACE}),
    "google_calendar": ({"calendar_id": "primary"}, {"calendar_id": "ada@example.test"}),
}


def _as_before(kind: str, a: dict, b: dict) -> bool:
    """The rule for Granola and Calendar before this change: the same id twice."""
    names = {"granola": ("folder_id", "space_id"), "google_calendar": ("calendar_id",)}[kind]
    return any(
        isinstance(a.get(n), str) and str(a.get(n)).casefold() == str(b.get(n)).casefold()
        for n in names
    )


@pytest.fixture(params=sorted(PAIRS))
def made_before(world, monkeypatch, request):
    """Two sources of one account, both on, made while the old rule held: "first" and
    "second", writing into "ada-notes"; "ada-more" is Ada's other project."""
    import cairn.sources

    kind = request.param
    world.store.add_credential("acct", kind, SETTINGS[kind], owner="ada@example.test")
    ada = world.Ada.view
    ada.create_project("ada-more", everyone="none")
    with monkeypatch.context() as old:
        old.setattr(cairn.sources, "overlaps", _as_before)
        for name, select in zip(("first", "second"), PAIRS[kind], strict=True):
            ada.add_source_from(name, "acct", select, {}, "ada-notes")
            ada.enable_source(name)
    world.clock.now = NOW + 5 * MINUTE
    return SimpleNamespace(kind=kind, sent=[])


def sources_rows(world) -> list:
    return rows(world.db, "sources")


def run_made_before(world, made, name: str):
    def handle(request: httpx.Request) -> httpx.Response:
        made.sent.append(request)
        return httpx.Response(401, json={"error": "made up"})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            return await run_source(
                world.store, name, http=http, env={**ENV, **GRANOLA_KEY}, clock=world.clock
            )

    return asyncio.run(go())


def test_a_pair_made_before_is_kept_when_the_server_starts(world, made_before):
    from starlette.testclient import TestClient

    from cairn.server import create_app

    before = sources_rows(world)
    base = "http://127.0.0.1:8765"
    with TestClient(create_app(world.store, public_url=base), base_url=base):
        pass  # the app's start and stop, as `serve` runs them
    again = Store(world.db, clock=world.clock)
    try:
        shown = {s.name: s for s in again.list_sources()}
    finally:
        again.close()
    assert sources_rows(world) == before  # neither removed, switched off nor changed
    assert shown["first"].enabled and shown["second"].enabled
    assert shown["first"].last_error is None and shown["second"].last_error is None


def test_a_pair_made_before_is_not_switched_on_again_nor_moved(world, made_before):
    ada = world.Ada.view
    before = dump(world.db)
    for change in (
        lambda: ada.enable_source("first"),  # on as it is: still asked
        lambda: ada.enable_source("first", ["ada-more"]),  # moved
        lambda: world.store.enable_source("second", ["ada-more"]),  # the operator too
    ):
        with pytest.raises(ValueError, match="already reads"):
            change()
    assert dump(world.db) == before
    ada.disable_source("second")  # switching one off is always allowed
    with pytest.raises(ValueError, match="already reads"):
        ada.enable_source("second")  # switched on again
    assert not world.store.get_source("second").enabled


def test_a_pair_made_before_writes_nothing_while_both_are_on(world, made_before):
    from cairn.sources import OVERLAP_ON, SourceError

    project = world.store.get_project("ada-notes").id
    for name, other in (("first", "second"), ("second", "first")):
        with pytest.raises(SourceError) as said:
            run_made_before(world, made_before, name)
        assert str(said.value) == OVERLAP_ON.format(other)
        shown = world.store.get_source(name)
        assert shown.enabled and shown.last_error == OVERLAP_ON.format(other)
    assert made_before.sent == []  # nothing read: not even a sign-in
    assert world.store.list_notes(project) == []
    world.Ada.view.disable_source("second")  # one off: the other reads again
    with pytest.raises(SourceError) as said:
        run_made_before(world, made_before, "first")
    assert made_before.sent and str(said.value) != OVERLAP_ON.format("second")


def test_the_sources_page_says_why_a_pair_made_before_wrote_nothing(world, made_before):
    from starlette.testclient import TestClient

    from cairn.server import create_app
    from cairn.sources import OVERLAP_ON, SourceError

    with pytest.raises(SourceError):
        run_made_before(world, made_before, "first")
    signed = world.store.sign_in(
        "sso", "sub-Ada", "ada@example.test", "Ada", issuer=ISSUER, join="default"
    )
    base = "http://127.0.0.1:8765"
    with TestClient(create_app(world.store, public_url=base), base_url=base) as client:
        page = client.get("/sources", headers={"cookie": f"{session_cookie()}={signed.token}"})
    assert page.status_code == 200
    assert OVERLAP_ON.format("second") in html.unescape(page.text)


def test_overlap_is_400_over_the_api_and_another_account_is_free(world):
    from starlette.testclient import TestClient

    from cairn.server import create_app

    store = world.store
    store.add_credential("acct", "connector", SETTINGS["connector"], owner="ada@example.test")
    store.add_credential("acct-2", "connector", SETTINGS["connector"], owner="ada@example.test")
    world.Ada.view.add_source_from("one", "acct", {}, {}, "ada-notes")
    key = world.Ada.view.create_key("ada-desk")
    base = "http://127.0.0.1:8765"
    body = {"name": "two", "credential": "acct", "select": {}, "options": {},
            "project": "ada-notes", "new_project": None}  # fmt: skip
    before = dump(world.db)
    with TestClient(create_app(store, public_url=base), base_url=base) as client:
        r = client.post("/api/integrations", headers={"x-api-key": key}, json=body)
        assert r.status_code == 400 and "already reads" in r.json()["error"], r.text
        assert dump(world.db) == before
        r = client.post("/api/integrations", headers={"x-api-key": key},
                        json={**body, "credential": "acct-2"})  # fmt: skip
        assert r.status_code == 201, r.text


def test_a_source_is_not_moved_onto_another_sources_selection(world):
    """Switching a source on with new projects runs the same check as making it: for
    Langfuse the project is what it selects."""
    world.store.add_credential("acct", "langfuse", SETTINGS["langfuse"], owner="ada@example.test")
    ada = world.Ada.view
    ada.create_project("ada-more", everyone="none")
    ada.add_source_from("a", "acct", {}, {}, "ada-notes")
    ada.add_source_from("b", "acct", {}, {}, "ada-more")
    before = dump(world.db)
    with pytest.raises(ValueError, match="already reads"):
        ada.enable_source("b", ["ada-notes"])
    with pytest.raises(ValueError, match="already reads"):
        world.store.enable_source("b", ["ada-more", "ada-notes"])  # the operator too
    assert dump(world.db) == before
    assert ada.enable_source("b").config["projects"] == ["ada-more"]  # where it was: fine


def test_the_guide_says_how_long_an_item_is_held_and_that_case_is_blind():
    root = Path(__file__).resolve().parents[2]
    guide = (root / "src" / "cairn" / "guides" / "sources.md").read_text()
    words = "made from one registered account are compared"
    said = next(s for s in guide.split(". ") if words in s)
    assert "case-blind" in said and "look-back window" in said and "unwritten" in said
    # The bound that is real is docs_publisher.MAX_FILE_BYTES (128 KiB): test_docs_helper shows a
    # sources guide past 70,000 bytes published and searched. This lower one only makes growth
    # a choice someone states; it was raised from 70,000 for the sentence on changing the key.
    assert len(guide.encode()) < 72_000


def test_the_guide_says_which_second_source_the_form_refuses():
    from cairn.sources import OVERLAP_ON

    root = Path(__file__).resolve().parents[2]
    guide = " ".join((root / "src" / "cairn" / "guides" / "sources.md").read_text().split())
    start = guide.index("made from one registered account are compared")
    said = guide[start : guide.index("**Four endings", start)]
    for words in (
        "one Granola account makes one source",
        "any number of folders",
        "`primary` beside another calendar",
        "one calendar twice in any case",
        "made before this rule stay",
        "switch one off",
    ):
        assert words in said, words
    words = "neither reads or writes anything"  # what the page says beside such a source
    assert words in said and words in OVERLAP_ON
