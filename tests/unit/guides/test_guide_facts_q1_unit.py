"""Sentences of the guides about Q1 (tests/acceptance/test_q1_bounded_reads.py), each pinned
here so the guides cannot go false unnoticed: what the run guide says of a page's history,
`--keep-versions` and what `compact` removes, of `stats`, the line `serve` logs and low disk,
and what the connect guide says of lists that come in parts. Each test finds its sentences
in the guide as a person reads it (guide_source), then asks cairn. `serve` runs in this process with
uvicorn.run recorded and never run; every name is made up."""

import asyncio
import json
import logging
import threading
from collections import namedtuple
from datetime import UTC, datetime, timedelta

import pytest
from starlette.testclient import TestClient

from cairn.compiler import provenance_gate
from cairn.memory import Memory
from cairn.server import create_app
from cairn.store import Store
from cairn.web import cli, stats
from support.guide_source import guide_source

Usage = namedtuple("Usage", "total used free")

START = datetime(2099, 8, 1, 9, 0, tzinfo=UTC)
PLANS = "default: base\nplans:\n  base:\n    label: Base\n    versions_per_page: 2\n"


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


def flat(text: str) -> str:
    return " ".join(text.split())


def says(guide: str, *sentences: str) -> None:
    """Each sentence is in the guide, as a person reads it."""
    text = flat(guide_source(guide))
    for sentence in sentences:
        assert flat(sentence) in text, sentence


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: None)


def saved(db, times: int, slug: str = "a") -> list[int]:
    store = Store(db, clock=Clock())
    try:
        project = store.get_project("north") or store.create_project("north")
        for i in range(times):
            store.save_page(project.id, slug, slug.title(), f"Body {i}.", [])
        return store.page_versions(project.id, slug)
    finally:
        store.close()


def test_serve_keeps_the_newest_50_versions_and_the_option_changes_it(tmp_path, quiet):
    says(
        "run",
        "`serve` keeps the newest 50 versions of each page: a save that takes a page past 50"
        " removes its oldest versions in the same step",
        "`--keep-versions N` keeps the newest N (a whole number, 1 or more), and"
        " `--keep-versions all` keeps every version.",
    )
    assert cli.KEEP_VERSIONS == 50
    db = tmp_path / "cairn.db"
    for argv, kept in (((), 50), (("--keep-versions", "3"), 3), (("--keep-versions", "all"), None)):
        cli.main(["serve", "--db", str(db), "--no-tidy", *argv])
        store = Store(db)
        try:
            assert store._state.keep_versions is None  # a new process: a new connection
        finally:
            store.close()
        args = cli._parser().parse_args(["serve", "--db", str(db), *argv])
        assert cli._kept(args, None) == kept


def test_with_plans_the_plan_says_and_the_option_is_refused(tmp_path, capsys):
    says(
        "run",
        "Where `serve` runs with `--plans`, each plan's `versions_per_page` says how many"
        " versions a page keeps, and `--keep-versions` is refused.",
    )
    plans = tmp_path / "plans.yaml"
    plans.write_text(PLANS, encoding="utf-8")
    code = cli.main(
        ["serve", "--db", str(tmp_path / "cairn.db"), "--plans", str(plans), "--keep-versions", "5"]
    )
    assert code == 2 and "--keep-versions" in capsys.readouterr().err


def test_serve_says_so_at_start_and_removes_nothing(tmp_path, quiet, capsys):
    says(
        "run",
        "When `serve` starts on a database whose pages hold more versions than it keeps, it says"
        " so in one line, with how many pages and that `--keep-versions all` keeps them. It"
        " removes nothing then: a page's older versions go when the page is next saved, or at a"
        " compaction.",
    )
    db = tmp_path / "cairn.db"
    saved(db, 6)
    capsys.readouterr()
    assert cli.main(["serve", "--db", str(db), "--no-tidy", "--keep-versions", "4"]) == 0
    assert "1 page holds more than 4" in capsys.readouterr().err
    assert saved(db, 0) == [1, 2, 3, 4, 5, 6]


def test_compact_removes_by_the_last_serves_record(tmp_path, quiet, capsys):
    says(
        "run",
        "`serve` records in the database how many versions it keeps, and the last `serve` to"
        " start is the one recorded. `compact` removes at once what that record says no page"
        " keeps, then rewrites the file",
        "Its first line says how many versions it removed and by what rule. On a database no"
        " `serve` of this version has run on, it removes no version and says so.",
    )
    db = tmp_path / "cairn.db"
    saved(db, 6)
    capsys.readouterr()
    assert cli.main(["compact", "--db", str(db)]) == 0
    assert "removed no page version" in capsys.readouterr().out.splitlines()[0]
    assert saved(db, 0) == [1, 2, 3, 4, 5, 6]
    cli.main(["serve", "--db", str(db), "--no-tidy", "--keep-versions", "2"])
    cli.main(["serve", "--db", str(db), "--no-tidy", "--keep-versions", "4"])  # the last one
    capsys.readouterr()
    assert cli.main(["compact", "--db", str(db)]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first == "removed 2 older page versions: each page keeps its newest 4"
    assert saved(db, 0) == [3, 4, 5, 6]


def test_a_workspace_on_a_plan_the_file_does_not_name_keeps_every_version(tmp_path, quiet, capsys):
    says(
        "run",
        "With plans, each workspace keeps what its plan keeps; a workspace on a plan the plans"
        " file does not name keeps every version, and `compact` names it on a line of its own.",
    )
    db = tmp_path / "cairn.db"
    store = Store(db, clock=Clock())
    try:
        other = store.create_account("Other")
        store.set_plan(other.id, "retired")
        theirs = store.for_account(other.id)
        project = theirs.create_project("far")
        for i in range(4):
            theirs.save_page(project.id, "x", "X", f"Body {i}.", [])
    finally:
        store.close()
    plans = tmp_path / "plans.yaml"
    plans.write_text(PLANS, encoding="utf-8")
    cli.main(["serve", "--db", str(db), "--no-tidy", "--plans", str(plans)])
    capsys.readouterr()
    assert cli.main(["compact", "--db", str(db)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert other.id in lines[1] and "every version" in lines[1]
    store = Store(db)
    try:
        assert store.for_account(other.id).page_versions(project.id, "x") == [1, 2, 3, 4]
    finally:
        store.close()


def test_stats_prints_the_numbers_and_only_reads(tmp_path, monkeypatch, capsys):
    says(
        "run",
        "It shows the schema's number, the size of the database and of its write-ahead log, the"
        " free space on its disk, and, for the install and for each workspace by its id, how"
        " many projects, notes, pages, page versions, notes waiting for their page, keys and"
        " connected agents there are, when the newest note was stored, and how many sources of"
        " each kind are on and how many failed their last run. `--json` prints the same as one"
        " JSON object.",
        "It refuses a database of an older version with one line that says to start the server"
        " once on it",
    )
    monkeypatch.setattr(stats.shutil, "disk_usage", lambda path: Usage(1, 1, 10**12))
    db = tmp_path / "cairn.db"
    saved(db, 2)
    before = db.read_bytes()
    assert cli.main(["stats", "--db", str(db), "--json"]) == 0
    found = json.loads(capsys.readouterr().out)
    named = {"schema", "db_bytes", "wal_bytes", "free_bytes", "low_disk", "workspaces"}
    named |= {"compaction"}  # Privacy 1: the install's record of compaction, no workspace's
    counted = {*stats.COUNTS, "newest_note", "sources"}
    assert set(found) == named | counted
    assert all(set(mine) == counted for mine in found["workspaces"].values())
    assert db.read_bytes() == before
    assert "start the server once" in cli.cli_stats.OLDER_DATABASE


def test_serve_logs_the_numbers_every_15_minutes_and_0_turns_it_off(tmp_path):
    says(
        "run",
        "`serve` also writes the install's numbers to its log: one line at start, then one every"
        " 15 minutes, `stats` followed by a JSON object with the install's totals and"
        " `requests`, how many responses it sent since the last line, by class (`2xx`, `3xx`,"
        " `4xx`, `5xx`, and `429`, which counts in `4xx` too).",
        "`--stats-every MINUTES` changes how often, and `--stats-every 0` turns it off.",
    )
    assert cli.STATS_EVERY == 15 and stats.CLASSES == ("2xx", "3xx", "4xx", "5xx", "429")
    assert stats.WORD == "stats"
    parse = cli._parser().parse_args
    assert parse(["serve", "--db", "x.db"]).stats_every == 15
    assert parse(["serve", "--db", "x.db", "--stats-every", "0"]).stats_every == 0
    store = Store(tmp_path / "cairn.db", clock=Clock())
    try:
        assert create_app(store, stats_every=0).state.stats_every is None
        assert create_app(store, stats_every=15).state.stats_every == 15
    finally:
        store.close()


async def _never(seconds: float) -> None:
    await asyncio.Event().wait()


class _Warned(logging.Handler):
    """Counts the warnings that reach a logger and says when `count` of them have come. The
    first stats pass runs off the event loop, so a test must wait for its lines: on a slow
    machine a request is answered before the pass has logged. It keeps the messages
    itself: a handler on the stats logger runs before the record reaches the root's (pytest's
    caplog), so reading caplog when `came` is set races the pass's thread."""

    def __init__(self, count: int) -> None:
        super().__init__(level=logging.WARNING)
        self.left, self.came = count, threading.Event()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())
        self.left -= 1
        if self.left <= 0:
            self.came.set()


def test_low_disk_warns_and_names_what_stops_working(tmp_path, monkeypatch, caplog):
    says(
        "run",
        "Low disk means the free space on the database's disk is under twice the database's"
        " size. Then the line is a warning, followed by one that says so: writes can fail when"
        " the disk is full, and a compaction or a backup copy of the database cannot run",
        "Nothing else changes: the server keeps answering, and `/healthz` answers as before.",
    )
    db = tmp_path / "cairn.db"
    store = Store(db, clock=Clock())
    both = _Warned(2)  # the stats line, then the sentence that says what low disk stops
    logging.getLogger(stats.STATS_LOGGER).addHandler(both)
    try:
        size = db.stat().st_size
        monkeypatch.setattr(stats.shutil, "disk_usage", lambda path: Usage(1, 1, 2 * size - 1))
        app = create_app(store, stats_every=15, stats_wait=_never)
        with caplog.at_level(logging.INFO, logger=stats.STATS_LOGGER), TestClient(app) as client:
            assert client.get("/healthz").json() == {"status": "ok"}
            assert both.came.wait(60), "no warning at start"  # a hang guard, not a bound
            warned = list(both.messages)
    finally:
        logging.getLogger(stats.STATS_LOGGER).removeHandler(both)
        store.close()
    assert warned[0].startswith("stats {") and warned[1:] == [stats.LOW_DISK]
    for words in ("writes can fail", "compaction", "backup"):
        assert words in stats.LOW_DISK


def test_lists_come_in_parts_as_the_connect_guide_says(tmp_path):
    says(
        "connect",
        "`overview` and `GET /api/projects/<project>/pages` list 100 pages at most, by slug;"
        " `limit` asks for fewer, and `after` for the pages after a slug. An answer that leaves"
        " pages out has `pages_total`, how many there are in all, and, while more follow,"
        " `pages_next`: pass it as `after` to get the next part.",
        "A page's `versions` (`page`, `GET /api/projects/<project>/pages/<slug>`) lists its"
        " latest 50 version numbers, oldest first; `limit` (at most 50) takes fewer, and `before`"
        " the versions numbered below it. A part that leaves versions out has `versions_total` and,"
        " while earlier ones remain, `versions_next`, to pass as `before`. An answer that holds"
        " everything has none of these fields.",
    )
    store = Store(tmp_path / "cairn.db", clock=Clock())
    try:
        memory = Memory(store, provenance_gate)
        project = store.create_project("ops")
        for i in range(3):
            store.save_page(project.id, f"p{i}", f"P{i}", "Body.", [])
        for i in range(2):
            store.save_page(project.id, "p0", "P0", f"Body {i}.", [])
        part = memory.overview("ops", limit=2)
        assert [p["slug"] for p in part["pages"]] == ["p0", "p1"]
        assert (part["pages_total"], part["pages_next"]) == (3, "p1")
        rest = memory.overview("ops", limit=2, after=part["pages_next"])
        assert [p["slug"] for p in rest["pages"]] == ["p2"] and "pages_next" not in rest
        whole = memory.overview("ops")
        assert "pages_total" not in whole and "pages_next" not in whole
        page = memory.page("ops", "p0", limit=2)
        assert page["versions"] == [2, 3] and page["versions_total"] == 3
        earlier = memory.page("ops", "p0", limit=2, before=page["versions_next"])
        assert earlier["versions"] == [1] and "versions_next" not in earlier
        assert "versions_total" not in memory.page("ops", "p0")
    finally:
        store.close()
