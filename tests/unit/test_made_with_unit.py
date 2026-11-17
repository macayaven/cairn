"""A1, part B in the store: a key or an agent's grant made through a key records that key by
its id; a revoke can say what a key made and take it, all of it or nothing."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from cairn.store import Store
from cairn.store.models import Made

CLOCK = datetime(2099, 2, 3, 4, 5, tzinfo=UTC)
REDIRECT = "http://127.0.0.1:9/callback"


class Clock:
    def __init__(self) -> None:
        self.now = CLOCK

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    s = Store(tmp_path / "cairn.db", clock=clock)
    yield s
    s.close()


def as_key(store: Store, key: str):
    """A view acting as `key`, as a web session opened with it does."""
    principal, key_id = store.identify_key(key)
    return store.acting_as("key", principal.label, key_id)


def approve(view, store: Store, name: str) -> str:
    """An agent `name` approved through `view`: its code, not yet redeemed."""
    client = store.register_client(name, [REDIRECT])
    return view.create_code(client.id, REDIRECT, "challenge"), client.id


def connect(view, store: Store, name: str) -> None:
    code, client_id = approve(view, store, name)
    assert store.redeem_code(code, client_id) is not None


def chain(store: Store) -> dict[str, str]:
    """laptop makes relay and connects probe; relay makes phone; bystander makes spare."""
    keys = {label: store.create_key(label) for label in ("laptop", "bystander")}
    keys["relay"] = as_key(store, keys["laptop"]).create_key("relay")
    connect(as_key(store, keys["laptop"]), store, "probe")
    keys["phone"] = as_key(store, keys["relay"]).create_key("phone")
    keys["spare"] = as_key(store, keys["bystander"]).create_key("spare")
    return keys


# Recorded and shown


def test_what_a_key_makes_records_it_and_the_lists_name_it(store):
    chain(store)
    made = {k.label: (k.made_with, k.made_with_revoked) for k in store.list_keys()}
    assert made == {
        "laptop": (None, False),
        "bystander": (None, False),
        "relay": ("laptop", False),
        "phone": ("relay", False),
        "spare": ("bystander", False),
    }
    assert [(g.client_name, g.made_with) for g in store.list_grants()] == [("probe", "laptop")]


def test_what_a_person_or_the_operator_makes_records_no_key(store):
    store.create_key("cli")
    person = store.acting_as("session", "Ada")  # a person's session: no key
    person.create_key("ada-key")
    connect(person, store, "ada-agent")
    assert {k.made_with for k in store.list_keys()} == {None}
    assert {g.made_with for g in store.list_grants()} == {None}


def test_a_revoked_maker_is_not_named_and_its_label_taken_again_made_nothing(store):
    chain(store)
    assert store.revoke_key("laptop") is True
    store.create_key("laptop")
    shown = {k.label: (k.made_with, k.made_with_revoked) for k in store.list_keys()}
    assert shown["relay"] == (None, True) and shown["laptop"] == (None, False)
    ((name, made_with, revoked),) = [
        (g.client_name, g.made_with, g.made_with_revoked) for g in store.list_grants()
    ]
    assert (name, made_with, revoked) == ("probe", None, True)
    assert store.made_by("laptop") == Made()


# What a key made


def test_made_by_names_what_a_key_made_through_every_level(store):
    chain(store)
    assert store.made_by("laptop") == Made(keys=("relay", "phone"), agents=("probe",))
    assert store.made_by("relay") == Made(keys=("phone",))
    assert store.made_by("phone") == Made() and store.made_by("nobody") == Made()


def test_made_by_walks_through_a_key_already_revoked(store):
    chain(store)
    store.revoke_key("relay")
    assert store.made_by("laptop") == Made(keys=("phone",), agents=("probe",))


def test_a_key_from_before_the_recording_says_so(store, tmp_path, clock):
    store.create_key("old")
    con = sqlite3.connect(tmp_path / "cairn.db")
    (moment,) = con.execute("SELECT at FROM recorded_since").fetchone()
    earlier = (CLOCK - timedelta(days=1)).isoformat(timespec="microseconds")
    con.execute("UPDATE api_keys SET created_at = ? WHERE label = 'old'", (earlier,))
    con.commit()
    con.close()
    clock.now = CLOCK + timedelta(days=1)
    store.create_key("new")
    assert store.made_by("old") == Made(unrecorded_before=moment)
    assert store.made_by("new") == Made()  # made after the moment: nothing unrecorded


# Revoking with what it made


def test_revoking_with_made_takes_every_level_and_nothing_else(store):
    keys = chain(store)
    sessions = {label: as_key(store, keys[label]).create_session() for label in keys}
    pending, _ = approve(as_key(store, keys["relay"]), store, "waiting")
    assert store.revoke_key("laptop", with_made=True) is True

    assert sorted(k.label for k in store.list_keys()) == ["bystander", "spare"]
    assert store.list_grants() == []
    assert store.find_code(pending) is None  # an approval nobody redeemed goes too
    for label, token in sessions.items():
        assert store.check_session(token) is (label in ("bystander", "spare")), label

    events = store.list_audit(100)
    assert sorted(e.detail["label"] for e in events if e.action == "key.revoked") == [
        "laptop",
        "phone",
        "relay",
    ]
    assert [e.detail["agent"] for e in events if e.action == "agent.disconnected"] == ["probe"]
    (together,) = [e for e in events if e.action == "key.revoked_with_made"]
    assert together.detail == {
        "label": "laptop",
        "keys": ["phone", "relay"],
        "agents": ["probe"],
    }


def test_revoking_without_made_leaves_what_it_made(store):
    chain(store)
    assert store.revoke_key("laptop") is True
    assert sorted(k.label for k in store.list_keys()) == ["bystander", "phone", "relay", "spare"]
    assert [g.client_name for g in store.list_grants()] == ["probe"]
    assert not [e for e in store.list_audit(100) if e.action == "key.revoked_with_made"]


def test_revoking_with_made_a_key_that_made_nothing_is_a_plain_revoke(store):
    chain(store)
    assert store.revoke_key("phone", with_made=True) is True
    assert [e.action for e in store.list_audit(1)] == ["key.revoked"]
    assert store.revoke_key("nobody", with_made=True) is False


def test_only_a_manager_reads_what_a_key_made(store):
    chain(store)
    for n, name in enumerate(("ada", "brook")):  # the first owns the workspace
        signed = store.sign_in(
            "sso", f"sub-{n}", f"{name}@example.test", name, issuer="https://id.example.test",
            join="default",
        )  # fmt: skip
    member = store.for_account(signed.account_id, user_id=signed.user.id)
    with pytest.raises(PermissionError):
        member.made_by("laptop")


def test_a_session_opened_with_a_key_a_person_made_records_it_behind_https(tmp_path):
    """The session's key is looked up by the same cookie the session was admitted by,
    whatever the cookie is called (A1: Accounts.admit)."""
    from starlette.testclient import TestClient

    from cairn.server import create_app

    base = "https://memory.example.test"
    clock = Clock()
    store = Store(tmp_path / "web.db", clock=clock)
    try:
        signed = store.sign_in(
            "sso", "sub-1", "ada@example.test", "Ada", issuer="https://id.example.test",
            join="default",
        )  # fmt: skip
        ada = store.for_account(signed.account_id, user_id=signed.user.id)
        desk = ada.acting_as("session", "Ada").create_key("ada-desk")
        app = create_app(store, public_url=base, clock=clock)
        with TestClient(app, base_url=base) as browser:
            same = {"sec-fetch-site": "same-origin"}
            r = browser.post("/login", data={"key": desk}, headers=same, follow_redirects=False)
            assert r.status_code == 303, r.text
            made = browser.post("/keys", data={"label": "relay"}, headers=same)
            assert made.status_code == 200, made.text
        shown = {k.label: k.made_with for k in store.list_keys()}
        assert shown == {"ada-desk": None, "relay": "ada-desk"}
    finally:
        store.close()


def test_a_code_approved_through_a_key_moves_to_its_grant(store, tmp_path):
    keys = chain(store)
    code, client_id = approve(as_key(store, keys["bystander"]), store, "later")
    con = sqlite3.connect(tmp_path / "cairn.db")
    assert con.execute("SELECT count(*) FROM made_with WHERE id LIKE 'code:%'").fetchone() == (1,)
    store.redeem_code(code, client_id)
    assert con.execute("SELECT count(*) FROM made_with WHERE id LIKE 'code:%'").fetchone() == (0,)
    con.close()
    assert store.made_by("bystander") == Made(keys=("spare",), agents=("later",))
