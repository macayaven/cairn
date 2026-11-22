"""Unit tests for members: invitations, roles and removal. The inner TDD loop under
tests/acceptance/test_t1_members.py."""

import sqlite3
import threading
from pathlib import Path

import pytest

from cairn.store import Removed, Store

ISSUER = "https://id.example.test/pool-1"
REDIRECT = "http://127.0.0.1:33418/callback"


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


def person(store: Store, name: str, *, email: str | None = None, join: str = "default"):
    email = email or f"{name.lower()}@example.test"
    signed = store.sign_in("sso", f"sub-{name}", email, name, issuer=ISSUER, join=join)
    return store.for_account(signed.account_id, signed.user.id)


def test_an_invite_matches_its_email_ignoring_case_and_nothing_else(store):
    ada = person(store, "Ada")
    dotted = person(store, "Dot", email="b.rook+cairn@example.test", join="personal")
    plain = person(store, "Plain", email="brook@example.test", join="personal")
    invite = ada.invite("B.Rook+Cairn@EXAMPLE.test")
    with pytest.raises(PermissionError):
        store.accept_invite(invite.token, plain.user_id)  # no trimming of dots or tags
    assert store.accept_invite(invite.token, dotted.user_id).role == "member"


@pytest.mark.parametrize("email", ["", "no-at-sign", "@example.test", "brook@"])
def test_an_invite_needs_an_email_address(store, db, email):
    ada = person(store, "Ada")
    before = dump(db)
    with pytest.raises(ValueError):
        ada.invite(email)
    assert dump(db) == before


def test_an_invite_names_a_role_and_a_lifetime(store):
    ada = person(store, "Ada")
    with pytest.raises(ValueError):
        ada.invite("brook@example.test", "guest")
    with pytest.raises(ValueError):
        ada.invite("brook@example.test", ttl_seconds=-1)


def test_reading_invites_and_workspaces_writes_nothing(store, db):
    ada = person(store, "Ada")
    invite = ada.invite("brook@example.test")
    expired = ada.invite("cy@example.test", ttl_seconds=0)
    before = dump(db)
    assert [i.email for i in ada.list_invites()] == ["brook@example.test"]
    assert store.find_invite(invite.token).workspace == "default"
    assert store.find_invite(expired.token) is None
    assert [w.role for w in store.list_workspaces(ada.user_id)] == ["owner"]
    assert dump(db) == before


def test_an_expired_invite_is_removed_by_the_next_invite(store, db):
    ada = person(store, "Ada")
    ada.invite("cy@example.test", ttl_seconds=0)

    def stored() -> list[str]:
        con = sqlite3.connect(db)
        try:
            return [email for (email,) in con.execute("SELECT email FROM invites")]
        finally:
            con.close()

    assert stored() == ["cy@example.test"]
    ada.invite("brook@example.test")
    assert stored() == ["brook@example.test"]


def test_a_member_already_there_keeps_their_role_and_the_invite_is_used(store):
    ada = person(store, "Ada")
    cy = person(store, "Cy")
    invite = ada.invite("cy@example.test", "admin")
    assert store.accept_invite(invite.token, cy.user_id).role == "member"
    assert cy.role() == "member" and store.find_invite(invite.token) is None


def test_a_role_change_to_an_unknown_role_or_person_is_refused(store, db):
    ada = person(store, "Ada")
    cy = person(store, "Cy")
    before = dump(db)
    with pytest.raises(ValueError):
        ada.set_role(cy.user_id, "superuser")
    with pytest.raises(KeyError):
        ada.set_role("usr_000000000000", "admin")
    assert ada.remove_member("usr_000000000000") is False
    assert dump(db) == before


def test_setting_the_role_someone_has_writes_nothing(store, db):
    ada = person(store, "Ada")
    cy = person(store, "Cy")
    before = dump(db)
    ada.set_role(cy.user_id, "member")
    assert dump(db) == before


def test_one_transaction_decides_two_demotions_at_once(db):
    """Two owners demote each other from two connections to the file: one demotion lands,
    the other sees it and is refused, so the workspace keeps an owner."""
    setup = Store(db)
    ada, brook = person(setup, "Ada"), person(setup, "Brook")
    ada.set_role(brook.user_id, "owner")
    setup.close()
    stores = [Store(db), Store(db)]
    views = [
        stores[0].for_account(ada.account_id, ada.user_id),
        stores[1].for_account(brook.account_id, brook.user_id),
    ]
    targets = [brook.user_id, ada.user_id]
    start, outcomes = threading.Barrier(2), [None, None]

    def demote(n: int) -> None:
        start.wait()
        try:
            views[n].set_role(targets[n], "member")
            outcomes[n] = "ok"
        except (ValueError, PermissionError) as error:
            outcomes[n] = type(error).__name__

    threads = [threading.Thread(target=demote, args=(n,)) for n in (0, 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    try:
        assert sorted(outcomes) == ["PermissionError", "ok"] or sorted(outcomes) == [
            "ValueError",
            "ok",
        ]
        roles = [m.role for m in stores[0].list_members()]
        assert roles.count("owner") == 1
    finally:
        for s in stores:
            s.close()


def test_people_changes_are_audited_under_who_made_them(store):
    ada = person(store, "Ada").acting_as("session", "Ada")
    brook = person(store, "Brook", join="personal")
    invite = ada.invite("brook@example.test", "admin")
    joined = store.for_account(brook.account_id, brook.user_id).acting_as("session", "Brook")
    joined.accept_invite(invite.token, brook.user_id)
    ada.set_role(brook.user_id, "member")
    second = ada.invite("cy@example.test")
    assert ada.revoke_invite(second.id) is True
    assert ada.remove_member(brook.user_id) is True
    events = list(reversed(ada.list_audit()))
    assert [(e.action, e.actor_kind, e.actor) for e in events] == [
        ("session.opened", "session", "Ada"),  # A1: Ada's sign-in
        ("member.invited", "session", "Ada"),
        ("member.joined", "session", "Brook"),
        ("role.changed", "session", "Ada"),
        ("member.invited", "session", "Ada"),
        ("invite.revoked", "session", "Ada"),
        ("member.removed", "session", "Ada"),
    ]
    assert events[3].detail["member"] == brook.user_id
    assert (events[3].detail["from"], events[3].detail["to"]) == ("admin", "member")
    assert invite.token not in repr(events) and invite.token not in repr(invite)


def test_removal_ends_what_they_held_here_and_nothing_elsewhere(store, db):
    ada = person(store, "Ada")
    brook = person(store, "Brook", join="personal")
    store.accept_invite(ada.invite("brook@example.test", "admin").token, brook.user_id)
    here = store.for_account(ada.account_id, brook.user_id)
    key_here = here.create_key("brook-here")
    key_home = brook.create_key("brook-home")
    session_here = here.create_session()
    client = store.register_client("brook-agent", [REDIRECT])
    code = here.create_code(client.id, REDIRECT, "c" * 43)
    token = store.redeem_code(code, client.id).access_token
    pending = here.create_code(client.id, REDIRECT, "d" * 43)

    assert ada.remove_member(brook.user_id) is True
    assert store.resolve_key(key_here) is None and store.resolve_session(session_here) is None
    assert store.resolve_token(token) is None and store.find_code(pending) is None
    assert store.resolve_key(key_home) is not None  # their own workspace is untouched
    assert store.get_user(brook.user_id) is not None
    assert [w.account_id for w in store.list_workspaces(brook.user_id)] == [brook.account_id]


def test_a_person_removed_from_their_only_workspace_is_forgotten(store, db):
    ada = person(store, "Ada")
    cy = person(store, "Cy", email="cy-unique@example.test")
    assert ada.remove_member(cy.user_id) is True
    assert store.get_user(cy.user_id) is None
    assert "cy-unique@example.test" not in dump(db)


def test_a_view_of_a_person_who_left_manages_nothing(store):
    ada = person(store, "Ada")
    brook = person(store, "Brook")
    ada.set_role(brook.user_id, "admin")
    other = person(store, "Cy")
    ada.remove_member(brook.user_id)
    assert brook.manages() is False
    for refused in (
        lambda: brook.invite("eve@example.test"),
        lambda: brook.set_role(other.user_id, "admin"),
        lambda: brook.create_key("late"),
    ):
        with pytest.raises(PermissionError):
            refused()


ORPHANS = {
    "a rule naming a project or person that is gone": "SELECT COUNT(*) FROM project_people"
    " WHERE project_id NOT IN (SELECT id FROM projects) OR user_id NOT IN (SELECT id FROM users)",
    "a key made by a person who is gone": "SELECT COUNT(*) FROM api_keys"
    " WHERE user_id NOT IN (SELECT id FROM users)",
    "a project made by a person who is gone": "SELECT COUNT(*) FROM projects"
    " WHERE maker_id NOT IN (SELECT id FROM users)",
    "an invitation to a workspace that is gone": "SELECT COUNT(*) FROM invites"
    " WHERE account_id NOT IN (SELECT id FROM accounts)",
}


def test_no_row_of_ids_outlives_a_workspace_or_a_person(store, db):
    """Rows that hold only ids, which a search of the database for names cannot find."""
    ada = person(store, "Ada", join="personal")
    brook = person(store, "Brook", join="personal")
    store.accept_invite(ada.invite("brook@example.test", role="admin").token, brook.user_id)
    brooks_here = store.for_account(ada.account_id, brook.user_id)
    shared = ada.create_project("shared")
    ada.set_project_access(shared.id, everyone="none", people={brook.user_id: "write"})
    brooks_here.create_project("brooks", everyone="none")
    brooks_here.create_key("brook-ci")
    ada.invite("zed@example.test")
    assert store.delete_account(ada.account_id) is True  # Brook keeps his own workspace
    assert store.get_user(brook.user_id) is not None
    con = sqlite3.connect(db)
    try:
        for orphan, query in ORPHANS.items():
            assert con.execute(query).fetchone() == (0,), orphan
    finally:
        con.close()


# Decision A: a removal sticks under join="default"


def test_a_removed_person_does_not_rejoin_by_signing_in(store, db):
    ada = person(store, "Ada")
    brook = person(store, "Brook", email="brook@example.test")
    ada.remove_member(brook.user_id)
    stored = dump(db)
    assert "brook@example.test" not in stored  # the marker is a digest, not the address
    before = stored
    with pytest.raises(Removed, match="invitation"):
        store.sign_in("sso", "sub-Brook", "BROOK@example.test", "Brook", issuer=ISSUER,
                      join="default")  # fmt: skip
    assert dump(db) == before
    other = person(store, "Other", email="brook@other.example.test")  # another address joins
    assert other.role() == "member"


def test_a_removed_identity_does_not_rejoin_under_a_changed_address(store, db):
    """The same person at the same provider, with a changed or re-verified address: the
    identity's own marker refuses the automatic join, and an invitation that identity
    accepts clears both markers."""
    ada = person(store, "Ada")
    brook = person(store, "Brook", email="brook@example.test")
    ada.remove_member(brook.user_id)  # their only workspace: the identity goes too
    before = dump(db)
    assert "sub-Brook" not in before  # the marker is a digest, not the subject
    with pytest.raises(Removed):
        store.sign_in("sso", "sub-Brook", "brook+new@example.test", "Brook", issuer=ISSUER,
                      join="default")  # fmt: skip
    assert dump(db) == before
    assert removals(db) == 2  # one for the address, one for the identity
    ada.invite("brook+new@example.test")
    signed = store.sign_in("sso", "sub-Brook", "brook+new@example.test", "Brook", issuer=ISSUER,
                           join="default")  # fmt: skip
    assert signed.account_id == ada.account_id  # the invitation lets that identity in
    assert removals(db) == 1  # the identity's marker is cleared; the old address's stays


def test_a_fresh_invitation_clears_the_removal(store):
    ada = person(store, "Ada")
    brook = person(store, "Brook")
    ada.remove_member(brook.user_id)
    ada.invite("brook@example.test", role="admin")
    signed = store.sign_in("sso", "sub-Brook", "brook@example.test", "Brook", issuer=ISSUER,
                           join="default")  # fmt: skip
    back = store.for_account(signed.account_id, signed.user.id)
    assert (signed.account_id, back.role()) == (ada.account_id, "admin")
    assert ada.list_invites() == []  # the invitation was used
    assert "member.joined" in [e.action for e in ada.list_audit()]
    ada.remove_member(signed.user.id)  # removed again: the marker is back
    with pytest.raises(Removed):
        store.sign_in("sso", "sub-Brook", "brook@example.test", "Brook", issuer=ISSUER,
                      join="default")  # fmt: skip


def test_accepting_an_invitation_clears_the_removal_for_good(store, db):
    ada = person(store, "Ada")
    brook = person(store, "Brook", join="default")
    elsewhere = person(store, "Cy", join="personal")
    ada.remove_member(brook.user_id)
    eve = person(store, "Eve", email="brook@example.test", join="personal")  # same address
    store.accept_invite(ada.invite("brook@example.test").token, eve.user_id)
    ada.remove_member(eve.user_id)
    assert removals(db) == 3  # one per workspace and address, and one per identity
    store.accept_invite(ada.invite("brook@example.test").token, eve.user_id)
    assert store.for_account(ada.account_id, eve.user_id).role() == "member"
    assert removals(db) == 1  # Eve's address and identity are clear; Brook's identity is not
    assert elsewhere.role() == "owner"


def removals(db: Path) -> int:
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT count(*) FROM removals").fetchone()[0]
    finally:
        con.close()


def test_a_removal_marker_goes_with_its_workspace(store, db):
    team = store.create_account("Team")
    owner = store.for_account(team.id)
    signed = store.sign_in("sso", "sub-Zed", "zed@example.test", "Zed", issuer=ISSUER,
                           join="personal")  # fmt: skip
    store.accept_invite(owner.invite("zed@example.test").token, signed.user.id)
    owner.remove_member(signed.user.id)
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT count(*) FROM removals").fetchone() == (2,)  # address, identity
        assert store.delete_account(team.id) is True
        assert con.execute("SELECT count(*) FROM removals").fetchone() == (0,)
    finally:
        con.close()
