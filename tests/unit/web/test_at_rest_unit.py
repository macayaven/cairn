"""at_rest (tests/support/web/at_rest.py): a test that counts statements or rows inside a
started app first stops the app's keeper, whose pass at start would otherwise land in the
count on a busy machine; an app with another loop that reads the store is refused. The names
at_rest reads from the lifespan, and the number of tasks the lifespan starts, are pinned here
and nowhere else: a renamed variable or a new loop fails one test, not every test that calls
at_rest."""

import inspect
import threading

import pytest
from starlette.testclient import TestClient

from cairn.arrange import Arranger
from cairn.server import create_app
from cairn.store import Store
from support.web.at_rest import OTHER_LOOPS, at_rest, lifespan_of

BASE = "https://testserver"


@pytest.fixture
def store(tmp_path):
    made = Store(tmp_path / "cairn.db")
    yield made
    made.close()


def swept(store, monkeypatch) -> list[str]:
    """The keeper's store calls, in order: each pass begins with sweep_slack."""
    calls: list[str] = []
    sweep = store.sweep_slack

    def counted():
        calls.append("sweep")
        return sweep()

    monkeypatch.setattr(store, "sweep_slack", counted)
    return calls


def test_a_pass_in_flight_has_ended_when_it_returns_and_no_later_pass_reads(store, monkeypatch):
    app = create_app(store, public_url=BASE)
    keeper = app.state.slack_keeper
    inside, order = threading.Event(), []
    stopping = threading.Event()
    stop = keeper.stop

    def stopped():
        stopping.set()
        stop()

    sweep = store.sweep_slack

    def held():
        inside.set()
        assert stopping.wait(10), "at_rest never stopped the keeper"  # a hang guard
        result = sweep()
        order.append("pass ended")
        return result

    monkeypatch.setattr(keeper, "stop", stopped)
    monkeypatch.setattr(store, "sweep_slack", held)
    with TestClient(app, base_url=BASE):
        assert inside.wait(10), "the keeper's pass at start never began"  # a hang guard
        at_rest(app)  # the pass is held until stop begins: at_rest must wait for its end
        order.append("at rest")
        calls = swept(store, monkeypatch)
        assert keeper.run_once() is None
    assert order == ["pass ended", "at rest"]
    assert calls == []


def test_before_the_client_is_entered_the_pass_at_start_reads_nothing(store, monkeypatch):
    app = create_app(store, public_url=BASE)
    keeper = app.state.slack_keeper
    calls = swept(store, monkeypatch)
    ran, results = threading.Event(), []
    run_once = keeper.run_once

    def recorded():
        results.append(run_once())
        ran.set()
        return results[-1]

    monkeypatch.setattr(keeper, "run_once", recorded)
    at_rest(app)
    with TestClient(app, base_url=BASE):
        assert ran.wait(10), "the keeper's pass at start never ran"  # a hang guard
    assert (results, calls) == ([None], [])


def test_without_it_the_pass_at_start_reads(store, monkeypatch):
    app = create_app(store, public_url=BASE)
    keeper = app.state.slack_keeper
    calls = swept(store, monkeypatch)
    ran = threading.Event()
    run_once = keeper.run_once

    def recorded():
        result = run_once()
        ran.set()
        return result

    monkeypatch.setattr(keeper, "run_once", recorded)
    with TestClient(app, base_url=BASE):
        assert ran.wait(10), "the keeper's pass at start never ran"  # a hang guard
    assert calls == ["sweep"]


def arranging(store):
    return {"arranger": Arranger(None, price=None, view_for=store.for_account, every=60)}


@pytest.mark.parametrize(
    ("settings", "loop"),
    [
        (lambda store: {"sweep_every": 60}, "sweeper"),
        (lambda store: {"stats_every": 15}, "stats log"),
        (lambda store: {"file_every": 60}, "filer"),
        (lambda store: {"sync_every": 60}, "source sync"),
        (lambda store: {"compact_every": 24}, "compactor"),
        (arranging, "arranger"),
    ],
)
def test_an_app_with_another_loop_that_reads_the_store_is_refused(store, settings, loop):
    app = create_app(store, public_url=BASE, **settings(store))
    with pytest.raises(AssertionError, match=loop):
        at_rest(app)
    with TestClient(app, base_url=BASE), pytest.raises(AssertionError, match=loop):
        at_rest(app)


def test_a_name_the_lifespan_does_not_have_is_passed_over(store, monkeypatch):
    monkeypatch.setitem(OTHER_LOOPS, "no_name_of_the_lifespan", "a loop of no app")
    app = create_app(store, public_url=BASE)
    calls = swept(store, monkeypatch)
    at_rest(app)
    assert app.state.slack_keeper.run_once() is None
    assert calls == []


def test_the_lifespan_has_every_name_at_rest_reads_and_starts_nine_tasks(store):
    lifespan = lifespan_of(create_app(store, public_url=BASE))
    missing = set(OTHER_LOOPS) - set(lifespan.__code__.co_freevars)
    assert not missing, f"the lifespan no longer reads {sorted(missing)}: at_rest cannot see them"
    # the seven loops at_rest refuses, the keeper's pass and its check of bot tokens
    assert len(OTHER_LOOPS) == 7
    started = inspect.getsource(lifespan).count("asyncio.create_task(")
    assert started == 9, f"the lifespan starts {started} tasks, not 9: tell at_rest of the change"
