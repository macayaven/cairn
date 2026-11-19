"""Unit tests for the background compaction of `serve --compact-every HOURS`
(cairn.web.compactor) and the store's record of a deletion not yet compacted. Time is
injected; nothing sleeps."""

import asyncio
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cairn.store import DEFAULT_ACCOUNT_ID, Store, base
from cairn.web.compactor import Compactor

T0 = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def ticks(self) -> float:
        """A monotonic clock that moves with this one (cairn.web.compactor measures every
        interval on it): seconds since T0."""
        return (self.now - T0).total_seconds()


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


def test_a_deletion_is_recorded_until_a_compaction_finishes_across_restarts(db):
    store = Store(db)
    assert store.compaction_state().deleted_at is None
    gone = store.create_account("Gone")
    assert store.delete_account(gone.id) is True
    store.close()
    store = Store(db)  # the record survives a restart
    try:
        state = store.compaction_state()
        assert state.deleted_at is not None and state.compacted_at is None
        store.compact()
    finally:
        store.close()
    store = Store(db)
    try:
        state = store.compaction_state()
        assert state.deleted_at is None and state.compacted_at is not None
    finally:
        store.close()


def test_the_record_is_not_a_setting_anyone_can_read_or_change(store):
    gone = store.create_account("Gone")
    store.delete_account(gone.id)
    for name in ("store.compact_due", "store.compacted_at"):
        with pytest.raises(ValueError):
            store.get_setting(name)
        with pytest.raises(ValueError):
            store.set_setting(name, "x")
    assert store.for_account(DEFAULT_ACCOUNT_ID).list_audit() == []


def test_an_unknown_account_or_a_view_records_nothing(store):
    assert store.delete_account("acct_000000000000") is False
    assert store.compaction_state().deleted_at is None
    ops = store.create_project("ops")
    with pytest.raises(PermissionError):
        store.narrow((ops.id,)).compaction_state()


def test_one_is_due_after_a_deletion_once_the_interval_has_passed(store, monkeypatch):
    clock = Clock(T0)
    monkeypatch.setattr(base, "_clock", clock)  # the store records compactions by it too
    compactor = Compactor(store, hours=24, clock=clock, ticks=clock.ticks)
    assert not compactor.due()  # nothing deleted yet
    store.delete_account(store.create_account("Gone").id)
    assert compactor.due()  # never compacted: at once
    assert compactor.run_once() is True
    assert not compactor.due()
    store.delete_account(store.create_account("Gone again").id)
    clock.now = T0 + timedelta(hours=23, minutes=59)
    assert not compactor.due()  # the interval since the last compaction has not passed
    clock.now = T0 + timedelta(hours=24)
    assert compactor.due()


def test_a_run_logs_how_long_it_took_and_never_overlaps(store, caplog):
    compactor = Compactor(store, hours=1, clock=Clock(T0))
    store.delete_account(store.create_account("Gone").id)
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        with compactor._running:  # one already running
            assert compactor.run_once() is False
        assert compactor.run_once() is True
    (record,) = [r for r in caplog.records if r.getMessage().startswith("compact")]
    assert " ms" in record.getMessage() and record.action == "compact"


def test_zero_hours_means_never(store):
    compactor = Compactor(store, hours=0, clock=Clock(T0))
    store.delete_account(store.create_account("Gone").id)
    assert not compactor.due()


def test_a_negative_interval_is_refused(store):
    with pytest.raises(ValueError):
        Compactor(store, hours=-1)


def test_serve_compacts_every_24_hours_unless_told_otherwise():
    from cairn.web.cli import _parser

    assert _parser().parse_args(["serve", "--db", "x.db"]).compact_every == 24
    assert (
        _parser().parse_args(["serve", "--db", "x.db", "--compact-every", "0"]).compact_every == 0
    )
    with pytest.raises(SystemExit):
        _parser().parse_args(["serve", "--db", "x.db", "--compact-every", "-1"])


def test_serve_passes_compact_every_to_the_app_it_runs(tmp_path, monkeypatch):
    """The parsed value reaches the compactor of the app `serve` runs: 24 hours by default,
    the hours given, and no compactor for 0."""
    import uvicorn

    from cairn.web import cli

    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    serve = ["serve", "--db", str(tmp_path / "serve.db")]
    assert cli.main(serve) == 0
    assert cli.main([*serve, "--compact-every", "6"]) == 0
    assert cli.main([*serve, "--compact-every", "0"]) == 0
    assert apps[0].state.compactor.every == timedelta(hours=24)
    assert apps[1].state.compactor.every == timedelta(hours=6)
    assert apps[2].state.compactor is None


def test_the_app_runs_the_compactor_for_its_lifespan_only_when_asked(store):
    from starlette.testclient import TestClient

    from cairn.web.app import create_app

    assert create_app(store).state.compactor is None  # 0 hours, the default: never
    app = create_app(store, compact_every=24)
    assert app.state.compactor.every == timedelta(hours=24)
    with TestClient(app) as client:  # the task starts with the lifespan and is cancelled with it
        assert client.get("/healthz").status_code == 200


def test_the_loop_compacts_when_one_is_due_and_a_failure_stops_no_later_run(
    store, monkeypatch, caplog
):
    from cairn.web import compactor as compactor_module

    monkeypatch.setattr(compactor_module, "POLL_SECONDS", 0.0)
    store.delete_account(store.create_account("Gone").id)
    real_compact, calls = Store.compact, []

    def flaky(self):
        calls.append(len(calls))
        if len(calls) == 1:
            raise sqlite3.OperationalError("disk I/O error")
        real_compact(self)

    monkeypatch.setattr(Store, "compact", flaky)
    compactor = Compactor(store, hours=1, clock=Clock(T0))

    async def until_compacted() -> None:
        task = asyncio.create_task(compactor.loop())
        try:
            while store.compaction_state().deleted_at is not None:
                await asyncio.sleep(0)
                assert not task.done(), task.exception()
        finally:
            task.cancel()

    with caplog.at_level(logging.INFO, logger="cairn.access"):
        asyncio.run(until_compacted())
    assert calls == [0, 1]
    failed, done = [r for r in caplog.records if r.getMessage().startswith("compact")]
    assert "failed" in failed.getMessage() and "OperationalError" in failed.getMessage()
    assert "disk I/O" not in failed.getMessage() and failed.status == 500
    assert "done" in done.getMessage()


def test_compaction_and_source_sync_run_side_by_side_and_stop_on_their_own(store, monkeypatch):
    """Both background tasks on: the sync holds its task forever and the compaction still
    runs; the compaction waits for the sync to have started and the sync is not held up;
    shutdown stops each and closes the sync's client."""
    import threading

    import httpx
    from starlette.testclient import TestClient

    from cairn.web import compactor as compactor_module
    from cairn.web.app import create_app
    from cairn.web.auth import Accounts

    monkeypatch.setattr(compactor_module, "POLL_SECONDS", 0.0)
    store.delete_account(store.create_account("Gone").id)
    syncing, compacted, seen = threading.Event(), threading.Event(), {}

    async def sync_forever(self, every, *, http, env):
        seen["http"] = http
        syncing.set()
        try:
            await asyncio.Event().wait()  # a sync that never ends on its own
        except asyncio.CancelledError:
            seen["sync stopped"] = True
            raise

    real_compact = Store.compact

    def compact_after_the_sync_started(self):
        assert syncing.wait(5)  # the sync started while the compactor waits here
        real_compact(self)
        compacted.set()

    monkeypatch.setattr(Accounts, "sync_sources", sync_forever)
    monkeypatch.setattr(Store, "compact", compact_after_the_sync_started)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    app = create_app(store, sync_every=60, source_http=lambda: client, compact_every=1)
    with TestClient(app) as web:
        assert compacted.wait(5)  # not held up by a sync that never ends
        assert web.get("/healthz").status_code == 200
        assert "sync stopped" not in seen
    assert seen["sync stopped"] and seen["http"].is_closed
    assert store.compaction_state().deleted_at is None
    assert not app.state.compactor._running.locked()


def test_the_arranger_runs_beside_the_sync_and_the_compactor_and_each_stops_on_its_own(
    store, monkeypatch
):
    """All three background tasks in one lifespan: the sync never ends on its own, the
    compaction still runs and the arranger keeps making passes; shutdown stops each, the
    arranger through its own stop, and it makes no pass after that."""
    import threading
    import time

    import httpx
    from starlette.testclient import TestClient

    from cairn.arrange import Arranger
    from cairn.llm import FakeModel
    from cairn.web import compactor as compactor_module
    from cairn.web.app import create_app
    from cairn.web.auth import Accounts

    monkeypatch.setattr(compactor_module, "POLL_SECONDS", 0.0)
    store.delete_account(store.create_account("Gone").id)
    syncing, compacted, seen, passes = threading.Event(), threading.Event(), {}, []

    async def sync_forever(self, every, *, http, env):
        syncing.set()
        try:
            await asyncio.Event().wait()  # a sync that never ends on its own
        except asyncio.CancelledError:
            seen["sync stopped"] = True
            raise

    real_compact, real_pass = Store.compact, Arranger.run_once

    def compact(self):
        real_compact(self)
        compacted.set()

    async def one_pass(self, limit=10):
        passes.append(syncing.is_set())
        return await real_pass(self, limit)

    monkeypatch.setattr(Accounts, "sync_sources", sync_forever)
    monkeypatch.setattr(Store, "compact", compact)
    monkeypatch.setattr(Arranger, "run_once", one_pass)
    arranger = Arranger(FakeModel([]), price=None, view_for=store.for_account, every=0.01)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    app = create_app(store, sync_every=60, source_http=lambda: client, compact_every=1,
                     arranger=arranger)  # fmt: skip
    with TestClient(app) as web:
        assert syncing.wait(5) and compacted.wait(5)
        deadline = time.monotonic() + 5  # a hang guard on the real loops, and nothing else
        while len(passes) < 3 and time.monotonic() < deadline:  # pass after pass, beside them
            assert web.get("/healthz").status_code == 200
        assert len(passes) >= 3 and passes[-1] is True
        assert "sync stopped" not in seen and not arranger._stopping.is_set()
    assert seen["sync stopped"] and arranger._stopping.is_set()
    made = len(passes)
    time.sleep(0.1)  # ten of its intervals: a loop still running would have made more
    assert len(passes) == made


def test_a_compaction_that_cannot_finish_is_tried_again_after_the_interval_not_at_every_poll(
    store, db, monkeypatch
):
    """Beside a replica service a connection reads the write-ahead log for good, so the
    truncating checkpoint never finishes. Each try rewrites the whole file under the store's
    lock: it was tried at every poll, every 10 minutes, for as long as the server ran. A try
    that did not finish now waits the interval like one that did; the deletion stays
    recorded, so nothing is forgotten."""
    clock = Clock(T0)
    monkeypatch.setattr(base, "_clock", clock)
    compactor = Compactor(store, hours=24, clock=clock, ticks=clock.ticks)
    store.delete_account(store.create_account("Gone").id)
    reader = sqlite3.connect(db)  # as the replica service: a read that does not end
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM settings").fetchone()
        assert compactor.due()
        assert compactor.run_once() is False  # not finished: the reader held the log
        assert store.compaction_state().deleted_at is not None  # still recorded as due
        clock.now = T0 + timedelta(minutes=10)
        assert not compactor.due()  # the next poll does not rewrite the file again
        clock.now = T0 + timedelta(hours=23, minutes=59)
        assert not compactor.due()
        clock.now = T0 + timedelta(hours=24)
        assert compactor.due()  # one try an interval, while it cannot finish
    finally:
        reader.close()
    assert compactor.run_once() is True  # the reader gone, it finishes
    assert not compactor.due()


def test_a_restart_tries_once_again_at_once(store, db, monkeypatch):
    """What a try that did not finish remembers is the process's own: a restarted server
    tries once more at its first poll, then waits the interval."""
    clock = Clock(T0)
    monkeypatch.setattr(base, "_clock", clock)
    store.delete_account(store.create_account("Gone").id)
    reader = sqlite3.connect(db)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM settings").fetchone()
        first = Compactor(store, hours=24, clock=clock, ticks=clock.ticks)
        assert first.run_once() is False and not first.due()
        assert Compactor(store, hours=24, clock=clock, ticks=clock.ticks).due()  # another process's
    finally:
        reader.close()
