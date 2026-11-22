"""Q1 C: the install's numbers put together (cairn.web.stats.numbers), the responses counted
by class (Responses, CountResponses), the line a StatsLog logs, and `stats` on the command
line (cairn.web.cli_stats). Every clock is in 2099; disk_usage is replaced."""

import asyncio
import json
import logging
from collections import namedtuple
from datetime import UTC, datetime, timedelta

import pytest

from cairn.store import SCHEMA_VERSION, Store
from cairn.web import cli, cli_stats, stats

START = datetime(2099, 10, 1, 9, 0, tzinfo=UTC)
FREE = 123_456_789
Usage = namedtuple("Usage", "total used free")


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def disk(monkeypatch):
    asked: list[str] = []

    def usage(path):
        asked.append(str(path))
        return Usage(10**12, 10**11, FREE)

    monkeypatch.setattr(stats.shutil, "disk_usage", usage)
    return asked


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "cairn.db"
    store = Store(path, clock=Clock())
    try:
        north = store.create_project("north")
        store.add_note(north.id, "A note.")
        store.save_page(north.id, "a", "A", "Body.", [])
        other = store.create_account("Other")
        store.for_account(other.id).create_project("south")
    finally:
        store.close()
    return path


def test_numbers_hold_the_install_and_each_workspace(db, disk):
    store = Store(db, clock=Clock())
    try:
        found = stats.numbers(store.numbers_part, store.database_file(), SCHEMA_VERSION)
    finally:
        store.close()
    assert list(found)[:5] == ["schema", "db_bytes", "wal_bytes", "free_bytes", "low_disk"]
    assert found["free_bytes"] == FREE and len(disk) == 1  # its folder, asked once
    assert (found["projects"], found["notes"], found["pages"], found["waiting"]) == (2, 1, 1, 1)
    assert set(found["workspaces"]) and all(
        set(mine) == {*stats.COUNTS, "newest_note", "sources"}
        for mine in found["workspaces"].values()
    )
    assert sum(m["projects"] for m in found["workspaces"].values()) == 2


@pytest.mark.parametrize(("free", "low"), [(0, True), (199, True), (200, False)])
def test_low_disk_is_free_space_under_twice_the_database(tmp_path, monkeypatch, free, low):
    path = tmp_path / "x.db"
    path.write_bytes(b"x" * 100)
    monkeypatch.setattr(stats.shutil, "disk_usage", lambda p: Usage(1, 1, free))
    found = stats.numbers(lambda part: [], str(path), 1)
    assert found["low_disk"] is low and found["db_bytes"] == 100 and found["wal_bytes"] == 0


def test_responses_count_by_class_and_start_again_when_taken():
    responses = stats.Responses()
    for status in (200, 204, 302, 401, 429, 500, 101):
        responses.count(status)
    assert responses.taken() == {"2xx": 2, "3xx": 1, "4xx": 2, "5xx": 1, "429": 1}
    assert responses.taken() == dict.fromkeys(stats.CLASSES, 0)


def test_an_error_before_a_response_counts_as_500():
    responses = stats.Responses()

    async def broken(scope, receive, send):
        raise RuntimeError("no")

    counted = stats.CountResponses(broken, responses)
    with pytest.raises(RuntimeError):
        asyncio.run(counted({"type": "http"}, None, None))
    assert responses.taken()["5xx"] == 1


def test_a_pass_logs_one_line_and_a_failure_its_class_only(db, disk, caplog):
    store = Store(db, clock=Clock())
    try:
        line = stats.StatsLog(store, 15, stats.Responses())
        with caplog.at_level(logging.INFO, logger=stats.STATS_LOGGER):
            asyncio.run(line.one_pass())
            store.numbers_part = lambda part: (_ for _ in ()).throw(OSError("/secret/path"))
            asyncio.run(line.one_pass())
    finally:
        store.close()
    first, failed = caplog.records
    word, _, rest = first.getMessage().partition(" ")
    numbers = json.loads(rest)
    assert word == "stats" and "workspaces" not in numbers and numbers["requests"]
    assert failed.getMessage() == "stats pass failed: OSError"
    assert {r.name for r in caplog.records} == {stats.STATS_LOGGER}


def test_stats_reads_without_making_a_file_beside_the_database(db, disk, capsys):
    before = sorted(p.name for p in db.parent.iterdir()), db.read_bytes()
    assert cli.main(["stats", "--db", str(db), "--json"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert found["schema"] == SCHEMA_VERSION and len(found["workspaces"]) == 2
    assert (sorted(p.name for p in db.parent.iterdir()), db.read_bytes()) == before
    assert cli.main(["stats", "--db", str(db)]) == 0
    plain = capsys.readouterr().out
    assert plain.startswith(f"schema {SCHEMA_VERSION}\n") and "install: projects 2," in plain


def test_an_older_database_is_refused_with_a_line_that_says_to_start_the_server(
    tmp_path, disk, capsys
):
    older = tmp_path / "older.db"
    from support.store.store import empty_v0_database

    empty_v0_database(older)
    assert cli.main(["stats", "--db", str(older)]) == 1
    out, err = capsys.readouterr()
    assert (
        out == ""
        and err == cli_stats.OLDER_DATABASE.format(db=older, found=1, known=SCHEMA_VERSION) + "\n"
    )
