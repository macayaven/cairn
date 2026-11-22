"""Unit tests: a store write reads the clock once it holds the write lock.

The rule tests/acceptance/test_h3b_oauth_strict.py set for OAuth codes and refresh tokens,
held everywhere else an expiry or a month is decided in a write: invitations, sign-ins,
sessions, model-use reservations and source claims. Each test starts the call while a
second connection holds the database's write lock, moves the store's clock while the call
waits, then releases the lock: the decision is the one the moved clock makes.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cairn.store import SOURCE_CLAIM_TTL, Removed, Store
from cairn.store.usage import Limits
from wait_helpers import while_database_locked

ISSUER = "https://id.example.test/pool-1"
SOURCE = {
    "base_url": "https://langfuse.example.test",
    "projects": ["payments-api"],
    "public_key_env": "LANGFUSE_PUBLIC_KEY",
    "secret_key_env": "LANGFUSE_SECRET_KEY",
}


class Clock:
    """The store's clock: `start`, plus a shift the test moves."""

    def __init__(self, start: datetime) -> None:
        self.start, self.shift = start, timedelta(0)

    def __call__(self) -> datetime:
        return self.start + self.shift


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2026, 11, 30, 23, 0, tzinfo=UTC))


@pytest.fixture
def store(db, clock):
    s = Store(db, clock=clock)
    yield s
    s.close()


def while_the_database_is_locked(db, store, call, then):
    def result():
        try:
            return call()
        except Exception as error:  # the call's answer, for the test to judge
            return error

    return while_database_locked(db, store, result, then)


def count(db, query: str, *args) -> int:
    con = sqlite3.connect(db)
    try:
        return con.execute(query, args).fetchone()[0]
    finally:
        con.close()


def person(store: Store, name: str, *, email: str | None = None, join: str = "default"):
    email = email or f"{name.lower()}@example.test"
    signed = store.sign_in("sso", f"sub-{name}", email, name, issuer=ISSUER, join=join)
    return store.for_account(signed.account_id, signed.user.id)


def move(clock: Clock, by: timedelta):
    return lambda: setattr(clock, "shift", clock.shift + by)


# Invitations


def test_an_invitation_that_expires_while_its_acceptance_waits_is_refused(store, clock, db):
    ada = person(store, "Ada")
    brook = person(store, "Brook", join="personal")
    invite = ada.invite("brook@example.test", ttl_seconds=60)
    answer = while_the_database_is_locked(
        db,
        store,
        lambda: store.accept_invite(invite.token, brook.user_id),
        move(clock, timedelta(minutes=2)),
    )
    assert isinstance(answer, KeyError)
    joined = "SELECT count(*) FROM memberships WHERE account_id = ? AND user_id = ?"
    assert count(db, joined, ada.account_id, brook.user_id) == 0  # nobody joined


def test_a_sign_in_during_which_the_invitation_expires_does_not_join_by_it(store, clock, db):
    ada = person(store, "Ada")
    brook = person(store, "Brook")
    ada.remove_member(brook.user_id)  # removed: only an open invitation lets Brook back in
    ada.invite("brook@example.test", ttl_seconds=60)
    answer = while_the_database_is_locked(
        db,
        store,
        lambda: store.sign_in(
            "sso", "sub-Brook", "brook@example.test", "Brook", issuer=ISSUER, join="default"
        ),
        move(clock, timedelta(minutes=2)),
    )
    assert isinstance(answer, Removed)
    joined = "SELECT count(*) FROM memberships WHERE account_id = ? AND user_id = ?"
    assert count(db, joined, ada.account_id, brook.user_id) == 0


def test_an_invitation_made_while_its_write_waits_lasts_its_whole_life_from_the_write(
    store, clock, db
):
    ada = person(store, "Ada")
    ada.invite("old@example.test", ttl_seconds=60)  # expires during the wait below
    made = while_the_database_is_locked(
        db,
        store,
        lambda: ada.invite("new@example.test", ttl_seconds=3_600),
        move(clock, timedelta(minutes=30)),
    )
    written = clock()  # the clock as the write found it
    assert datetime.fromisoformat(made.created_at) == written
    assert datetime.fromisoformat(made.expires_at) == written + timedelta(hours=1)
    # The cleanup that runs with every invitation removed the one that expired meanwhile.
    assert count(db, "SELECT count(*) FROM invites WHERE email = ?", "old@example.test") == 0
    assert [i.email for i in ada.list_invites()] == ["new@example.test"]


# Sessions


def test_a_session_opened_while_its_write_waits_lasts_its_whole_life_from_the_write(
    store, clock, db
):
    ada = person(store, "Ada")
    token = while_the_database_is_locked(
        db, store, lambda: ada.create_session(ttl_seconds=3_600), move(clock, timedelta(minutes=30))
    )
    clock.shift += timedelta(minutes=59)
    assert ada.check_session(token) is True  # 59 minutes after the write, not 89
    clock.shift += timedelta(minutes=1)
    assert ada.check_session(token) is False


def test_a_sign_in_while_its_write_waits_gets_a_session_that_lasts_from_the_write(store, clock, db):
    person(store, "Ada")
    signed = while_the_database_is_locked(
        db,
        store,
        lambda: store.sign_in(
            "sso",
            "sub-Cy",
            "cy@example.test",
            "Cy",
            issuer=ISSUER,
            join="personal",
            ttl_seconds=3_600,
        ),
        move(clock, timedelta(minutes=30)),
    )
    view = store.for_account(signed.account_id, signed.user.id)
    clock.shift += timedelta(minutes=59)
    assert view.check_session(signed.token) is True
    clock.shift += timedelta(minutes=1)
    assert view.check_session(signed.token) is False


# Model-use reservations


def test_a_reservation_that_waits_across_the_months_end_is_charged_to_the_new_month(
    store, clock, db
):
    clock.start = datetime(2026, 11, 30, 23, 59, 59, 500_000, tzinfo=UTC)
    limited = store.limited(Limits(model_budget_micros=1_000))
    assert limited.reserve_model_use(1_000) is not None  # November's budget is spent
    reserved = while_the_database_is_locked(
        db, store, lambda: limited.reserve_model_use(400), move(clock, timedelta(seconds=1))
    )
    assert isinstance(reserved, str)  # December's budget is untouched
    assert store.usage("2026-12").model_micros == 400
    assert store.usage("2026-11").model_micros == 1_000


# Source claims


def test_a_source_claim_is_judged_and_stamped_when_its_write_happens(store, clock, db):
    store.create_project("payments-api")
    store.add_source("traces", "langfuse", SOURCE)
    assert store.claim_source("traces") is not None  # its run dies holding it
    clock.shift = timedelta(minutes=1)
    taken = while_the_database_is_locked(
        db,
        store,
        lambda: store.claim_source("traces"),
        move(clock, SOURCE_CLAIM_TTL),  # the old claim is past its limit by the write
    )
    assert taken is not None and not isinstance(taken, Exception)
    # The new claim is stamped at the write: it holds for the whole limit from then.
    clock.shift += SOURCE_CLAIM_TTL - timedelta(seconds=1)
    assert store.claim_source("traces") is None
    clock.shift += timedelta(seconds=1)
    assert store.claim_source("traces") is not None
