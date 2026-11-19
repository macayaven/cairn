"""The operator can see whether deleted text has left the files (review: Privacy 1, second
half): `stats`, its `--json` and the line `serve` logs every 15 minutes carry the install's
record of compaction (Store.compaction_state), and no page a workspace's people reach shows
it. Every clock is in 2099; disk_usage is replaced."""

import asyncio
import json
import logging
import sqlite3
from collections import namedtuple
from datetime import UTC, datetime, timedelta

import pytest
from guide_source import guide_source
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import SCHEMA_VERSION, Store
from cairn.store.base import COMPACT_DUE, COMPACTED_AT, DEFAULT_ACCOUNT_ID, REWRITTEN
from cairn.web import cli, stats

START = datetime(2099, 10, 1, 9, 0, tzinfo=UTC)
Usage = namedtuple("Usage", "total used free")
BASE = "https://cairn.example.test"
ISSUER = "https://id.example.test/pool-numbers"
DELETED = "2099-03-04T05:06:07.000000+00:00"
REWROTE = "2099-03-04T06:00:00.000000+00:00"


class Clock:
    def __init__(self, start=START) -> None:
        self.now = start

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture(autouse=True)
def disk(monkeypatch):
    monkeypatch.setattr(stats.shutil, "disk_usage", lambda path: Usage(1, 1, 10**12))


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "cairn.db"
    Store(path, clock=Clock()).close()
    return path


def record(db, *, due: str | None, rewritten: bool = False, compacted: str | None = None):
    """The install's record of compaction, as the store writes it (cairn.store.base)."""
    con = sqlite3.connect(db)
    with con:
        con.execute(
            "DELETE FROM settings WHERE account_id = ? AND name IN (?, ?)",
            (DEFAULT_ACCOUNT_ID, COMPACT_DUE, COMPACTED_AT),
        )
        insert = "INSERT INTO settings (account_id, name, value, updated_at) VALUES (?, ?, ?, ?)"
        if due is not None:
            updated = REWRITTEN if rewritten else due
            con.execute(insert, (DEFAULT_ACCOUNT_ID, COMPACT_DUE, due, updated))
        if compacted is not None:
            con.execute(insert, (DEFAULT_ACCOUNT_ID, COMPACTED_AT, compacted, compacted))
    con.close()


STATES = {
    "none": {"due": None},
    "due": {"due": DELETED, "compacted": REWROTE},
    "rewritten": {"due": DELETED, "rewritten": True, "compacted": REWROTE},
}


def state_of(db) -> dict:
    store = Store(db, clock=Clock())
    try:
        return store.compaction_state().model_dump()
    finally:
        store.close()


@pytest.mark.parametrize("state", sorted(STATES))
def test_the_numbers_carry_the_record_as_the_store_reads_it(db, state):
    record(db, **STATES[state])
    store = Store(db, clock=Clock())
    try:
        found = stats.numbers(store.numbers_part, store.database_file(), SCHEMA_VERSION)
        assert found["compaction"] == store.compaction_state().model_dump()
    finally:
        store.close()
    assert set(found["compaction"]) == {"deleted_at", "rewritten", "compacted_at"}
    assert all("compaction" not in mine for mine in found["workspaces"].values())


def test_a_deletion_shows_until_a_compaction_finishes(db):
    store = Store(db, clock=Clock())
    try:
        project = store.create_project("north")
        assert store.delete_project(project.id) is True
        found = stats.numbers(store.numbers_part, store.database_file(), SCHEMA_VERSION)
        assert found["compaction"]["deleted_at"] is not None
        store.compact()
        found = stats.numbers(store.numbers_part, store.database_file(), SCHEMA_VERSION)
    finally:
        store.close()
    assert found["compaction"]["deleted_at"] is None
    assert found["compaction"]["compacted_at"] is not None


@pytest.mark.parametrize("state", sorted(STATES))
def test_stats_json_and_its_lines_say_the_record(db, state, capsys):
    record(db, **STATES[state])
    assert cli.main(["stats", "--db", str(db), "--json"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert found["compaction"] == state_of(db)
    assert cli.main(["stats", "--db", str(db)]) == 0
    (line,) = [x for x in capsys.readouterr().out.splitlines() if x.startswith("compaction:")]
    if state == "none":
        assert line == (
            "compaction: nothing deleted since the last compaction finished; the last rewrite"
            " began never"
        )
    elif state == "due":
        assert line == (
            f"compaction: what was deleted since {DELETED} is still in the database file;"
            f" the last rewrite began {REWROTE}; `compact --db {db}` rewrites it (serve does"
            " it by itself after --compact-every hours)"
        )
    else:
        assert line == (
            f"compaction: the file was rewritten after what was deleted since {DELETED}; only"
            f" its write-ahead log is left to empty; the last rewrite began {REWROTE};"
            f" `compact --if-due --db {db}` empties it once nothing else holds the log, such"
            " as a replica"
        )
    store = Store(db, clock=Clock())
    try:
        ids = [account.id for account in store.list_accounts()]
    finally:
        store.close()
    assert not any(account_id in line for account_id in ids)


def test_the_line_serve_logs_carries_the_record(db, caplog):
    record(db, **STATES["rewritten"])
    store = Store(db, clock=Clock())
    try:
        line = stats.StatsLog(store, 15, stats.Responses())
        with caplog.at_level(logging.INFO, logger=stats.STATS_LOGGER):
            asyncio.run(line.one_pass())
        (logged,) = caplog.records
        word, _, rest = logged.getMessage().partition(" ")
        assert word == "stats"
        assert json.loads(rest)["compaction"] == store.compaction_state().model_dump()
    finally:
        store.close()


# 3. Not shown to a workspace's people


def test_no_page_a_signed_in_person_reaches_shows_the_record(tmp_path):
    store = Store(tmp_path / "cairn.db", clock=Clock(datetime(2099, 3, 4, 5, 6, 6, tzinfo=UTC)))
    try:
        signed = store.sign_in(
            "sso", "sub-ada", "ada@example.test", "Ada", issuer=ISSUER, join="personal"
        )
        mine = store.for_account(signed.account_id, signed.user.id)
        kept = mine.create_project("north")
        mine.add_note(kept.id, "# Pier\n\nThe pier opens at nine.\n")
        theirs = store.create_project("elsewhere")  # the default workspace's: another's
        store.delete_project(theirs.id)
        deleted_at = store.compaction_state().deleted_at
        assert deleted_at is not None
        cookie = {"cookie": f"__Host-cairn_session={signed.token}"}
        pages = (
            "/", "/b/north", "/b/north/notes", "/workspace", "/keys", "/agents", "/sets",
            "/members", "/audit", "/usage", "/sources", "/account", "/setup", "/search?q=pier",
        )  # fmt: skip
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            for path in pages:
                shown = client.get(path, headers=cookie)
                assert shown.status_code in (200, 403, 404), path
                assert deleted_at not in shown.text, path
                assert deleted_at[:19] not in shown.text, path
    finally:
        store.close()


# The guides


def flat(text: str) -> str:
    return " ".join(text.split())


def section(guide: str, heading: str) -> str:
    raw = guide_source(guide)
    start = raw.index(f"\n{heading}\n")
    end = raw.find("\n## ", start + len(heading) + 2)
    return raw[start : end if end != -1 else None]


def says(guide: str, heading: str, *sentences: str) -> None:
    text = flat(section(guide, heading))
    for sentence in sentences:
        assert flat(sentence) in text, sentence
        assert flat(guide_source(guide)).count(flat(sentence)) == 1, sentence


def test_privacy_md_says_the_record_is_the_operators_and_what_to_ask():
    says(
        "privacy",
        "## Deletion",
        "cairn does not show a workspace's people whether a deletion has left the database file"
        " and its log, since that record is one for the whole server and would tell one"
        " workspace when another deleted something; ask the operator whether the database has"
        " been compacted since your deletion.",
    )


def test_run_md_says_the_lines_stats_and_serve_add():
    says(
        "run",
        "## Numbers to watch",
        "It also says whether something was deleted since the last compaction finished and"
        " since when, whether the file has been rewritten since so that only its write-ahead"
        " log is left to empty, and when the last rewrite began, with the command that finishes"
        " the job; `--json` has these as `compaction`, and the line `serve` logs carries them"
        " too.",
    )
