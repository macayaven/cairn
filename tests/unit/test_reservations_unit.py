"""Unit tests for model-use reservations: what a model call may cost is taken from a
workspace's budget before the call, in one transaction, and replaced by what it spent after.
Synthetic fixtures; nothing leaves the process."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cairn.plans import Plan, Plans
from cairn.store import Store
from cairn.store.usage import Limits


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 10, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(db, clock):
    s = Store(db, clock=clock)
    yield s
    s.close()


def test_a_reservation_takes_its_amount_from_what_is_left_at_once(store):
    limited = store.limited(Limits(model_budget_micros=1_000))
    first = limited.reserve_model_use(600)
    assert isinstance(first, str) and first.startswith("rs_")
    assert limited.model_budget_left() == 400 and store.usage().model_micros == 600
    assert limited.reserve_model_use(401) is None  # it does not fit: nothing is taken
    assert limited.model_budget_left() == 400
    assert limited.reserve_model_use(400) is not None
    assert limited.model_budget_left() == 0
    assert limited.reserve_model_use(0) is None  # nothing left: not even a call at no cost


def test_without_a_budget_a_reservation_is_always_granted(store):
    assert store.reserve_model_use(10**12) is not None
    assert store.limited(Limits(notes_per_month=1)).reserve_model_use(5) is not None
    assert store.model_budget_left() is None


def test_settling_replaces_the_reservation_with_what_was_spent(store):
    limited = store.limited(Limits(model_budget_micros=1_000))
    held = limited.reserve_model_use(600)
    limited.settle_model_use(held, 300, 40, 500)
    used = store.usage()
    assert (used.model_tokens_in, used.model_tokens_out, used.model_micros) == (300, 40, 500)
    assert limited.model_budget_left() == 500 and store.model_reservations() == []
    with pytest.raises(KeyError):
        limited.settle_model_use(held, 1, 1, 1)  # settled once
    assert store.usage().model_micros == 500


def test_what_was_spent_counts_even_past_what_was_reserved(store):
    limited = store.limited(Limits(model_budget_micros=100))
    held = limited.reserve_model_use(100)
    limited.settle_model_use(held, 9, 9, 250)
    assert store.usage().model_micros == 250 and limited.model_budget_left() == 0


def test_a_reservation_never_settled_counts_in_full_and_is_listed(store, clock):
    limited = store.limited(Limits(model_budget_micros=1_000))
    held = limited.reserve_model_use(700)
    (open_one,) = store.model_reservations()
    assert (open_one.id, open_one.period, open_one.micros) == (held, "2026-11", 700)
    assert open_one.reserved_at.startswith("2026-11-10T12:00:00")
    assert limited.model_budget_left() == 300
    clock.now = datetime(2026, 12, 1, tzinfo=UTC)  # settled in the month after
    limited.settle_model_use(held, 1, 2, 3)
    assert store.usage("2026-11").model_micros == 3 and store.usage("2026-12").model_micros == 0


def test_two_connections_cannot_both_take_the_last_of_a_budget(db, store, clock):
    other = Store(db, clock=clock)  # one clock: a budget is a calendar month's
    try:
        mine = store.limited(Limits(model_budget_micros=100))
        theirs = other.limited(Limits(model_budget_micros=100))
        assert mine.reserve_model_use(60) is not None
        assert theirs.reserve_model_use(60) is None
        assert theirs.model_budget_left() == 40
    finally:
        other.close()


def test_a_reservation_is_its_workspaces_own(store):
    ada = store.for_account(store.create_account("Ada").id)
    brook = store.for_account(store.create_account("Brook").id)
    held = ada.reserve_model_use(5)
    with pytest.raises(KeyError):
        brook.settle_model_use(held, 1, 1, 1)
    assert brook.model_reservations() == [] and len(ada.model_reservations()) == 1
    assert ada.usage().model_micros == 5 and brook.usage().model_micros == 0


@pytest.mark.parametrize(
    "bad", [(-1,), (1.5,), (True,), ("5",)], ids=["negative", "float", "bool", "text"]
)
def test_an_amount_that_is_not_a_whole_number_is_refused(store, bad):
    with pytest.raises(ValueError):
        store.reserve_model_use(*bad)
    held = store.reserve_model_use(1)
    with pytest.raises(ValueError):
        store.settle_model_use(held, 1, 1, *bad)
    assert len(store.model_reservations()) == 1


def test_a_narrowed_view_meters_but_reads_no_reservation(store):
    store.create_project("alpha")
    narrowed = store.narrow(projects=(), access="read")
    held = narrowed.reserve_model_use(3)
    narrowed.settle_model_use(held, 1, 1, 2)
    assert store.usage().model_micros == 2
    with pytest.raises(PermissionError):
        narrowed.model_reservations()


def test_deleting_a_workspace_deletes_its_reservations(db, store):
    ada = store.create_account("Ada")
    store.for_account(ada.id).reserve_model_use(5)
    store.delete_account(ada.id)
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM model_reservations").fetchone() == (0,)
    finally:
        con.close()


def test_a_reservation_reads_the_budget_of_the_plan_in_force_inside_its_transaction(store):
    plans = Plans(
        default="free",
        table={"free": Plan("Free", model_budget_micros=0),
               "solo": Plan("Solo", model_budget_micros=1_000)},
    )  # fmt: skip
    store.set_plan(store.account_id, "solo")
    view = plans.limit(store, plans.in_force(store, store.account_id)[1])
    assert view.reserve_model_use(400) is not None  # on Solo
    store.set_plan(store.account_id, "free")  # downgraded after the view was resolved
    assert view.reserve_model_use(1) is None  # read again in the reservation's write
    assert store.usage().model_micros == 400 and len(store.model_reservations()) == 1
