"""A preview's own parts (R1): the store's two reads, and the runner's bounds."""

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from cairn.sources import KINDS, NoteDraft, Pulled, SourceError, SourceKind
from cairn.sources.runner import PREVIEW_TITLES, PreviewBusy, preview_source
from cairn.store import Store
from cairn.store.access import READ_ONLY_PROJECT
from cairn.store.sources import NOT_A_SOURCE_MANAGER, OWNER_DECIDES

ISSUER = "https://id.example.test/pool-preview"
NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


class Feed:
    """A stand-in kind: each pull gives the drafts queued, and may wait on `gate` first."""

    def __init__(self) -> None:
        self.drafts: list[NoteDraft] = []
        self.gate: asyncio.Event | None = None
        self.pulling = asyncio.Event()  # set when a pull begins
        self.opened: list[dict] = []

    def check(self, config: object) -> dict:
        assert isinstance(config, dict)
        return {"projects": list(config.get("projects", []))}

    def open(self, name: str, config: dict, **options) -> "Feed":
        self.opened.append(options)
        return self

    async def pull(self, cursor: str | None) -> Pulled:
        self.pulling.set()
        if self.gate is not None:
            await self.gate.wait()
        return Pulled(drafts=list(self.drafts), cursor="c1")


def draft(n: int, project: str = "", item: str | None = None) -> NoteDraft:
    return NoteDraft(
        project=project, title=f"Entry {n}", text=f"# Entry {n}\n\nEntry {n}.\n",
        source_ref=f"feed:{n}", label="feed", item=item,
    )  # fmt: skip


@pytest.fixture
def world(tmp_path, monkeypatch):
    feed = Feed()
    monkeypatch.setitem(KINDS, "feed", SourceKind(check=feed.check, open=feed.open, via="feed"))
    db = tmp_path / "cairn.db"
    store = Store(db, clock=lambda: NOW)
    store.create_project("open")
    store.create_project("handbook", everyone="read")
    store.create_project("board", everyone="none")
    people = {}
    for name, role in (("Ada", None), ("Brook", None), ("Cy", "admin")):
        signed = store.sign_in(
            "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER,
            join="default",
        )  # fmt: skip
        if role:
            store.set_role(signed.user.id, role)
        people[name] = store.for_account(signed.account_id, signed.user.id)
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    yield SimpleNamespace(store=store, db=db, feed=feed, people=people, http=http)
    store.close()


def preview(world, view, name: str, **options):
    return asyncio.run(preview_source(view, name, http=world.http, env={}, **options))


def test_the_store_hands_the_whole_config_to_who_may_switch_it_on(world):
    world.store.add_source("feed", "feed", {"projects": ["open", "board"]})
    world.store.add_source("shared", "feed", {"projects": ["open"]})
    world.store.add_source("reading", "feed", {"projects": ["handbook"]})
    cy, brook = world.people["Cy"], world.people["Brook"]
    before = dump(world.db)
    whole = world.people["Ada"].source_to_preview("FEED")
    assert (whole.name, whole.config["projects"]) == ("feed", ["open", "board"])
    assert cy.get_source("feed").config["projects"] == ["open"]  # the list trims it
    assert cy.source_to_preview("shared").config["projects"] == ["open"]
    with pytest.raises(PermissionError, match=OWNER_DECIDES):
        cy.source_to_preview("feed")
    with pytest.raises(PermissionError, match=READ_ONLY_PROJECT):
        cy.source_to_preview("reading")
    with pytest.raises(PermissionError, match=NOT_A_SOURCE_MANAGER):
        brook.source_to_preview("shared")
    with pytest.raises(KeyError):
        world.store.source_to_preview("nope")
    opened = world.store.get_project("open").id
    for narrowed in (world.store.narrow(None, "read"), world.store.narrow([opened])):
        with pytest.raises(PermissionError):
            narrowed.source_to_preview("shared")
    assert dump(world.db) == before


def test_the_items_a_source_wrote_are_read_without_a_claim(world):
    world.store.add_source("feed", "feed", {"projects": ["open"]})
    claim = world.store.claim_source("feed")
    assert world.store.record_source_item(claim, "LED-1")
    world.store.fail_source_run(claim, "stopped")
    before = dump(world.db)
    assert world.store.source_items_written("feed", ["LED-1", "LED-2"]) == {"LED-1"}
    assert world.store.source_items_written("feed", []) == set()
    assert world.store.source_items_written("nope", ["LED-1"]) == set()
    with pytest.raises(PermissionError, match=NOT_A_SOURCE_MANAGER):
        world.people["Brook"].source_items_written("feed", ["LED-1"])
    assert dump(world.db) == before


def test_a_preview_counts_titles_items_and_reach(world):
    world.store.add_source("feed", "feed", {"projects": ["open"]})
    claim = world.store.claim_source("feed")
    world.store.record_source_item(claim, "old")
    world.store.fail_source_run(claim, "stopped")
    world.store.disable_source("feed")
    world.feed.drafts = [
        NoteDraft(
            project="open", title="T" * 300, text="# Long\n\nLong.\n", source_ref="f:l", label="f"
        ),
        draft(0, "open", item="old"),  # written before
        draft(1, "board"),  # a project Cy does not reach
        draft(2, "elsewhere"),  # no such project: nobody reaches it
        *(draft(n, "open") for n in range(3, 3 + PREVIEW_TITLES + 2)),
    ]
    before = dump(world.db)
    shown = preview(world, world.people["Cy"], "feed", days=2, clock=lambda: NOW)
    assert shown.read and shown.written_before == 1
    assert shown.out_of_reach == 2 and shown.count == PREVIEW_TITLES + 3
    assert len(shown.titles) == PREVIEW_TITLES and shown.more == 3
    # The owner reaches "board", which the source does not write into: a run skips it.
    owner = preview(world, world.store, "feed", clock=lambda: NOW)
    assert owner.out_of_reach == 1 and owner.count == PREVIEW_TITLES + 3
    assert owner.titles[0] == "T" * 200 and owner.titles[1] == "Entry 3"
    (options,) = world.feed.opened[:1]
    assert options["since"] == NOW.replace(day=14)
    assert dump(world.db) == before
    assert shown.answer()["titles"] == shown.titles and "said" not in shown.answer()


def test_the_command_line_previews_a_source_and_stores_nothing(world, capsys, monkeypatch):
    from cairn.server import main
    from cairn.web import cli

    world.store.add_source("feed", "feed", {"projects": ["open"]})
    world.store.disable_source("feed")
    world.feed.drafts = [draft(1, "open"), draft(2, "open")]
    refused = httpx.MockTransport(lambda request: httpx.Response(500))
    monkeypatch.setattr(cli, "_source_http", lambda: httpx.AsyncClient(transport=refused))
    db = str(world.db)
    before = dump(world.db)
    code = main(["source-preview", "--db", db, "--name", "feed", "--days", "3"])
    said = capsys.readouterr()
    assert code == 0, said.err
    assert said.out.splitlines() == [
        "feed: 2 new in the last 3 days; 0 written before; 0 for a project out of reach",
        "  Entry 1",
        "  Entry 2",
    ]
    assert dump(world.db) == before
    for argv, err in (
        (["--name", "nope"], "no source named 'nope'"),
        (["--name", "feed", "--days", "31"], "days is a whole number from 1 to 30"),
    ):
        assert main(["source-preview", "--db", db, *argv]) == 1
        assert err in capsys.readouterr().err
    world.feed.drafts = []

    def broken(name, config, **options):
        raise SourceError("set FEED_TOKEN in the environment of the process that syncs")

    monkeypatch.setitem(KINDS, "feed", SourceKind(check=world.feed.check, open=broken, via="f"))
    assert main(["source-preview", "--db", db, "--name", "feed"]) == 1
    assert capsys.readouterr().err.strip() == (
        "feed: could not be read: set FEED_TOKEN in the environment of the process that syncs"
    )
    assert dump(world.db) == before


def test_two_previews_of_one_source_at_once_are_refused(world):
    world.store.add_source("feed", "feed", {"projects": ["open"]})

    async def both():
        world.feed.gate = asyncio.Event()
        first = asyncio.create_task(
            preview_source(world.store, "feed", http=world.http, env={}, clock=lambda: NOW)
        )
        await world.feed.pulling.wait()  # the first holds the source while it pulls
        with pytest.raises(PreviewBusy):
            await preview_source(world.store, "FEED", http=world.http, env={}, clock=lambda: NOW)
        world.feed.gate.set()
        return await first

    assert asyncio.run(both()).read is True
    assert preview(world, world.store, "feed", clock=lambda: NOW).read is True  # let go after
