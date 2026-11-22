"""Unit tests for people in the store: users, identities, memberships, and the person on
sessions and agent grants. They pin what tests/acceptance/test_h4_signin.py leaves open."""

import re
import sqlite3
from pathlib import Path

import pytest

from cairn.store import DEFAULT_ACCOUNT_ID, ProviderChanged, Store

REDIRECT = "http://127.0.0.1:33418/callback"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
GITHUB, ACME = "https://github.com", "https://id.acme.example"


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


def ada(store, join="personal", **changes):
    person = {"provider": "github", "subject": "1", "email": "ada@example.test"}
    person = {**person, "name": "Ada Lovelace", "issuer": "https://github.com", **changes}
    return store.sign_in(join=join, **person)


def test_a_first_sign_in_makes_a_user_a_workspace_of_their_own_and_a_session(store):
    signed = ada(store)
    assert signed.created is True
    assert re.fullmatch(r"usr_[0-9a-f]{12}", signed.user.id)
    assert (signed.user.email, signed.user.name) == ("ada@example.test", "Ada Lovelace")
    assert store.get_account(signed.account_id).name == "Ada Lovelace"
    principal = store.resolve_session(signed.token)
    assert (principal.account_id, principal.kind) == (signed.account_id, "session")
    assert principal.user_id == signed.user.id and principal.label is None
    members = store.for_account(signed.account_id).list_members()
    assert [(m.user_id, m.email, m.name, m.role) for m in members] == [
        (signed.user.id, "ada@example.test", "Ada Lovelace", "owner")
    ]
    assert store.get_user(signed.user.id) == signed.user
    assert store.get_user("usr_000000000000") is None


def test_the_same_identity_signs_in_to_the_same_workspace_and_changes_nothing(store):
    first = ada(store)
    again = ada(store, name="Ada King", email="ada.king@example.test")
    assert again.created is False and again.token != first.token
    assert (again.user, again.account_id) == (first.user, first.account_id)
    assert len(store.list_accounts()) == 2


def test_a_new_identity_with_a_known_email_is_the_same_user_whatever_its_case(store):
    first = ada(store)
    other = ada(store, provider="google", subject="g-1", email="ADA@Example.TEST", name="A.")
    assert other.created is False and other.user == first.user
    assert other.account_id == first.account_id
    someone = ada(store, provider="google", subject="g-2", email="else@example.test")
    assert someone.created is True and someone.user.id != first.user.id


def test_the_default_join_makes_the_first_person_its_owner_and_the_rest_members(store):
    clara = store.sign_in("acme", "c", "clara@acme.example", "Clara", issuer=ACME, join="default")
    dev = store.sign_in("acme", "d", "dev@acme.example", "Dev", issuer=ACME, join="default")
    assert clara.account_id == dev.account_id == DEFAULT_ACCOUNT_ID
    assert [(m.name, m.role) for m in store.list_members()] == [
        ("Clara", "owner"),
        ("Dev", "member"),
    ]
    assert [a.id for a in store.list_accounts()] == [DEFAULT_ACCOUNT_ID]


def test_names_are_tidied_and_a_blank_one_falls_back_to_the_email(store):
    long = store.sign_in(
        "github", "1", "a@example.test", "  Ada \n " + "x" * 100, issuer=GITHUB, join="personal"
    )
    assert long.user.name == ("Ada " + "x" * 100)[:80]
    assert store.get_account(long.account_id).name == long.user.name
    blank = store.sign_in("github", "2", "b@example.test", " \t ", issuer=GITHUB, join="personal")
    assert blank.user.name == "b@example.test"


@pytest.mark.parametrize(
    "bad",
    [
        {"provider": ""},
        {"subject": " "},
        {"email": ""},
        {"email": "no-at-sign"},
        {"join": "everyone"},
        {"issuer": " "},
    ],
)
def test_sign_in_refuses_what_it_cannot_store_and_writes_nothing(store, db, bad):
    before = dump(db)
    person = {"provider": "github", "subject": "1", "email": "a@example.test", "name": "A"}
    person |= {"issuer": GITHUB, "join": "personal"}
    with pytest.raises(ValueError):
        store.sign_in(**{**person, **bad})
    assert dump(db) == before


def test_a_view_acts_for_a_member_and_only_a_member(store):
    signed = ada(store)
    view = store.for_account(signed.account_id, user_id=signed.user.id)
    assert (view.account_id, view.user_id) == (signed.account_id, signed.user.id)
    assert store.for_account(signed.account_id).user_id is None
    with pytest.raises(KeyError):
        store.for_account(DEFAULT_ACCOUNT_ID, user_id=signed.user.id)  # not a member there
    with pytest.raises(KeyError):
        store.for_account(signed.account_id, user_id="usr_000000000000")


def test_what_a_member_view_creates_carries_the_person(store):
    signed = ada(store)
    view = store.for_account(signed.account_id, user_id=signed.user.id)
    assert store.resolve_session(view.create_session()).user_id == signed.user.id
    keyed = store.for_account(signed.account_id).create_session()
    assert store.resolve_session(keyed).user_id is None  # a session opened with a key

    client = store.register_client("agent", [REDIRECT])
    pair = store.redeem_code(view.create_code(client.id, REDIRECT, CHALLENGE), client.id)
    principal = store.resolve_token(pair.access_token)
    assert (principal.account_id, principal.user_id) == (signed.account_id, signed.user.id)
    plain = store.for_account(signed.account_id).create_code(client.id, REDIRECT, CHALLENGE)
    assert store.resolve_token(store.redeem_code(plain, client.id).access_token).user_id is None
    key = store.for_account(signed.account_id).create_key("laptop")
    assert store.resolve_key(key).user_id is None


def test_deleting_a_workspace_removes_people_left_with_none(store, db):
    first = ada(store)
    brook = store.sign_in(
        "github", "2", "brook@example.test", "Brook", issuer=GITHUB, join="default"
    )
    clara = store.sign_in(
        "github", "3", "clara@example.test", "Clara", issuer=GITHUB, join="personal"
    )
    assert store.delete_account(first.account_id) is True
    assert store.get_user(first.user.id) is None
    assert store.resolve_session(first.token) is None
    assert store.get_user(brook.user.id) is not None and store.get_user(clara.user.id) is not None
    assert "ada@example.test" not in dump(db) and "'github','1'" not in dump(db)
    again = ada(store)  # the identity is gone: a new person, a new workspace
    assert again.created is True and again.user.id != first.user.id


def test_a_member_of_another_workspace_stays_when_one_is_deleted(store, db):
    signed = ada(store)
    team = store.create_account("Team")
    with sqlite3.connect(db) as con:  # until invitations exist (T1), a membership by hand
        con.execute(
            "INSERT INTO memberships (account_id, user_id, role, created_at)"
            " VALUES (?, ?, 'member', '2026-11-10T00:00:00.000000+00:00')",
            (team.id, signed.user.id),
        )
    con.close()
    assert store.delete_account(signed.account_id) is True
    assert store.get_user(signed.user.id) == signed.user
    assert [m.user_id for m in store.for_account(team.id).list_members()] == [signed.user.id]
    again = ada(store)
    assert again.created is False and again.account_id == team.id


OLD_ISSUER, NEW_ISSUER = "https://id.acme.example/pool-1", "https://id.acme.example/pool-2"


def test_an_identity_records_its_issuer(store, db):
    signed = store.sign_in(
        "sso", "s-1", "ada@acme.example", "Ada", issuer=OLD_ISSUER, join="default"
    )
    row = (
        sqlite3.connect(db)
        .execute("SELECT provider, subject, user_id, issuer FROM identities")
        .fetchall()
    )
    assert row == [("sso", "s-1", signed.user.id, OLD_ISSUER)]


def test_a_subject_known_under_another_issuer_is_refused_and_nothing_changes(store, db):
    ada = store.sign_in("sso", "s-1", "ada@acme.example", "Ada", issuer=OLD_ISSUER, join="default")
    before = dump(db)
    for email in ("ada@acme.example", "mallory@acme.example"):
        with pytest.raises(ProviderChanged):
            store.sign_in("sso", "s-1", email, "M", issuer=NEW_ISSUER, join="default")
    assert dump(db) == before
    again = store.sign_in(
        "sso", "s-1", "ada@acme.example", "Ada", issuer=OLD_ISSUER, join="default"
    )
    assert again.user == ada.user
    other = store.sign_in("sso", "s-2", "bo@acme.example", "Bo", issuer=NEW_ISSUER, join="default")
    assert other.created is True  # a new subject at the new issuer is a new identity


def test_reading_people_never_writes(store, db):
    signed = ada(store)
    before = dump(db)
    store.get_user(signed.user.id)
    store.for_account(signed.account_id, user_id=signed.user.id).list_members()
    store.resolve_session(signed.token)
    assert dump(db) == before
