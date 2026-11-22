"""A writer that stays stuck is unhealthy (review 6.1). While a write holds the store's
lock, the health check answers busy and /healthz 200; once one holder has kept it for longer
than a bound (STUCK_AFTER, 60 seconds; COMPACT_STUCK_AFTER while a compaction runs), the
check answers stuck and /healthz 503 `{"status":"stuck"}`; 200 again as soon as the lock is
obtained. The monotonic clock is injected and the health check's wait is 0: nothing sleeps,
and threads meet at events."""

import threading
from contextlib import contextmanager

import pytest
from starlette.testclient import TestClient

import cairn.store as store_module
from cairn.store import COMPACT_STUCK_AFTER, STUCK_AFTER, Store
from cairn.web.app import create_app


class Ticks:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def ticks() -> Ticks:
    return Ticks()


@pytest.fixture
def store(tmp_path, ticks, monkeypatch):
    monkeypatch.setattr(store_module, "HEALTH_WAIT", 0.0)  # no real waiting for the lock
    s = Store(tmp_path / "cairn.db", ticks=ticks)
    yield s
    s.close()


@contextmanager
def held(lock):
    """`lock` held by another thread until the block ends: the reviewer's wedge."""
    taken, release = threading.Event(), threading.Event()

    def hold() -> None:
        with lock:
            taken.set()
            release.wait()

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert taken.wait(10)
        yield
    finally:
        release.set()
        holder.join()


def test_the_bounds_are_a_minute_and_an_hour():
    assert STUCK_AFTER == 60.0
    assert COMPACT_STUCK_AFTER == 3600.0


def test_healthz_is_503_stuck_once_one_holder_kept_the_writer_past_the_bound(store, ticks):
    """The reviewer's probe, with the clock injected: the lock held for good."""
    with TestClient(create_app(store)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        with held(store._lock):
            r = client.get("/healthz")
            assert (r.status_code, r.json()) == (200, {"status": "ok"})  # busy, as today
            ticks.now += STUCK_AFTER
            assert client.get("/healthz").status_code == 200  # the bound itself: not past it
            ticks.now += 0.001
            r = client.get("/healthz")
            assert (r.status_code, r.json()) == (503, {"status": "stuck"})
            assert r.headers["content-type"] == "application/json"
        r = client.get("/healthz")  # obtained again: healthy at once
        assert (r.status_code, r.json()) == (200, {"status": "ok"})


def test_health_says_busy_then_stuck_and_ok_once_obtained(store, ticks):
    with held(store._lock):
        assert store.health() == "busy"
        ticks.now += STUCK_AFTER + 1
        assert store.health() == "stuck"
    assert store.health() == "ok"


def test_the_bound_can_be_given(store, ticks):
    with held(store._lock):
        ticks.now += 6
        assert store.health(stuck_after=5) == "stuck"
        assert store.health(stuck_after=10) == "busy"


def test_short_writes_one_after_another_are_never_stuck(store, ticks):
    """Measured at the lock, from when its holder took it: two probes a long way apart that
    each meet a different short write are not one stuck writer."""
    with held(store._lock):
        assert store.health() == "busy"
    ticks.now += STUCK_AFTER * 2
    with held(store._lock):
        assert store.health() == "busy"


def test_a_compaction_in_progress_has_the_longer_bound(store, ticks):
    store._state.compacting = True  # as Store.compact sets it while it runs
    try:
        with held(store._lock):
            ticks.now += STUCK_AFTER + 1
            assert store.health() == "busy"
            ticks.now += COMPACT_STUCK_AFTER - STUCK_AFTER
            assert store.health() == "stuck"
    finally:
        store._state.compacting = False


def test_compact_marks_itself_in_progress_while_it_holds_the_writer(store, monkeypatch):
    seen = []
    real = store._con

    class Watch:
        def __getattr__(self, name):
            return getattr(real, name)

        def execute(self, sql, *args):
            if sql == "VACUUM":
                seen.append(store._state.compacting)
            return real.execute(sql, *args)

    monkeypatch.setattr(store, "_con", Watch())
    store.compact()
    assert seen == [True]
    assert store._state.compacting is False
