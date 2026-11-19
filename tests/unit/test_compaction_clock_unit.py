"""Intervals are measured on a clock that does not step (review 6.6). The compactor's "at
most once every N hours" is measured on a monotonic clock (`ticks`, time.monotonic, injected
here); the wall clock (`clock`) is read only to place the stored time of the last rewrite
(a wall-clock time, as every time the database holds) at the first decision after it is
seen. A wall clock that steps back or forward neither suppresses nor forces a run. Nothing
sleeps."""

import asyncio
import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest

from cairn.store import Store, base
from cairn.web.compactor import Compactor

T0 = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
HOUR = 3600.0


class Clocks:
    def __init__(self) -> None:
        self.wall = T0
        self.mono = 500.0

    def clock(self) -> datetime:
        return self.wall

    def ticks(self) -> float:
        return self.mono


@pytest.fixture
def clocks(monkeypatch) -> Clocks:
    c = Clocks()
    monkeypatch.setattr(base, "_clock", c.clock)  # the store stamps by the wall clock
    return c


@pytest.fixture
def db(tmp_path):
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db, clocks):
    s = Store(db)
    yield s
    s.close()


def deleted(store) -> None:
    store.delete_account(store.create_account("Gone").id)


def compactor(store, clocks) -> Compactor:
    return Compactor(store, hours=24, clock=clocks.clock, ticks=clocks.ticks)


def held_log(db):
    reader = sqlite3.connect(db)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM settings").fetchone()
    return reader


def test_the_default_clock_for_intervals_is_monotonic(store):
    assert Compactor(store, hours=24)._ticks is time.monotonic


def test_a_wall_clock_that_steps_back_does_not_suppress_the_next_try(store, db, clocks):
    """The reviewer's case: a try that did not finish, then the wall clock set back. The
    next try still comes one interval later, by the monotonic clock."""
    c = compactor(store, clocks)
    deleted(store)
    reader = held_log(db)
    try:
        assert c.run_once() is False
        clocks.wall -= timedelta(hours=5)  # stepped back
        clocks.mono += 24 * HOUR
        assert c.due()
    finally:
        reader.close()


def test_a_wall_clock_that_steps_forward_does_not_force_a_run(store, clocks):
    c = compactor(store, clocks)
    deleted(store)
    assert c.run_once() is True
    deleted(store)
    clocks.wall += timedelta(hours=48)  # stepped forward
    clocks.mono += 1 * HOUR
    assert not c.due()
    clocks.mono += 23 * HOUR
    assert c.due()


def test_after_a_restart_the_stored_time_is_read_once_on_the_wall_clock(store, clocks):
    deleted(store)
    assert compactor(store, clocks).run_once() is True  # compacted_at = T0, by the wall
    deleted(store)
    clocks.wall = T0 + timedelta(hours=1)
    restarted = compactor(store, clocks)  # a new process: nothing of its own yet
    assert not restarted.due()  # 1 hour since the stored rewrite, on the wall clock
    clocks.wall -= timedelta(hours=10)  # then the wall clock steps back
    clocks.mono += 22 * HOUR
    assert not restarted.due()
    clocks.mono += 1 * HOUR  # 23 hours on, 24 since the stored rewrite
    assert restarted.due()


def test_a_stored_time_in_the_future_counts_from_the_first_decision(store, clocks):
    """The wall clock stepped back past the last rewrite before the server started: the
    stored time is later than now, and the interval counts from the first decision."""
    deleted(store)
    assert compactor(store, clocks).run_once() is True
    deleted(store)
    clocks.wall = T0 - timedelta(hours=3)
    restarted = compactor(store, clocks)
    assert not restarted.due()
    clocks.mono += 24 * HOUR - 1
    assert not restarted.due()
    clocks.mono += 1
    assert restarted.due()


def test_the_next_interval_in_the_same_process_empties_the_log_without_a_rewrite(store, db, clocks):
    seen: list[str] = []
    store._observe(trace=seen.append)
    c = compactor(store, clocks)
    deleted(store)
    reader = held_log(db)
    try:
        assert c.run_once() is False  # rewritten; the log held
        clocks.mono += 10 * 60
        assert not c.due()  # the next poll: the interval has not passed
        clocks.mono += 24 * HOUR
        assert c.due()
    finally:
        reader.close()
    assert c.run_once() is True
    assert sum(s.strip().upper() == "VACUUM" for s in seen) == 1


def test_the_slack_loop_waits_on_the_event_loops_monotonic_clock(store):
    """The Slack loop's one in-memory interval is its wait between passes, asyncio.sleep,
    which the event loop measures on time.monotonic; the times it compares a check with are
    stored in the database and are wall-clock times (Store.take_slack_checks)."""
    from cairn.web.slack import Keeper

    assert Keeper(store).wait is asyncio.sleep
    loop = asyncio.new_event_loop()
    try:
        before = time.monotonic()
        assert before <= loop.time() <= time.monotonic()
    finally:
        loop.close()
