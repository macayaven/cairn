"""Unit tests for the operations that are not about a view's own workspace: a view acting
for a person, or narrowed to some projects or to reading, is refused them (PermissionError,
nothing read or written); asked about its own workspace or person, it keeps the few it
needs. A view acting for no person (the server's, the command line's) keeps them all."""

import sqlite3
from pathlib import Path

import pytest

from cairn.store import Store, reach

ISSUER = "https://id.example.test"


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    opened = Store(db)
    yield opened
    opened.close()


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def people(store: Store):
    """Ada owns the default workspace and Cy is a member of it; Dee has a workspace of her
    own and is in no other."""
    ada = store.sign_in("sso", "s-ada", "ada@example.test", "Ada", issuer=ISSUER, join="default")
    cy = store.sign_in("sso", "s-cy", "cy@example.test", "Cy", issuer=ISSUER, join="default")
    dee = store.sign_in("sso", "s-dee", "dee@example.test", "Dee", issuer=ISSUER, join="personal")
    return ada, cy, dee


def views(store: Store, ada) -> dict[str, Store]:
    project = store.create_project("ops")
    return {
        "person": store.for_account(ada.account_id, ada.user.id),
        "limited": store.narrow((project.id,)),
        "read-only": store.narrow(access="read"),
    }


@pytest.mark.parametrize("kind", ["person", "limited", "read-only"])
def test_a_bound_view_is_refused_every_operation_of_other_workspaces(store, db, kind):
    ada, _, dee = people(store)
    view = views(store, ada)[kind]
    before = dump(db)
    for name in sorted(reach.OTHER_WORKSPACES):
        with pytest.raises(PermissionError):
            getattr(view, name)("anything", "anything")
    for name in sorted(reach.OWN_WORKSPACE_ONLY):  # asked about another workspace or person
        args = ("token", dee.user.id) if name == "accept_invite" else (dee.account_id,)
        if name in ("get_user", "list_workspaces"):
            args = (dee.user.id,)
        with pytest.raises(PermissionError):
            getattr(view, name)(*args)
    assert dump(db) == before


def test_a_person_keeps_what_is_about_their_own_workspace_and_themselves(store):
    ada, cy, dee = people(store)
    view = store.for_account(ada.account_id, ada.user.id)
    assert view.get_account(ada.account_id).id == ada.account_id
    assert view.plan_in_force(ada.account_id) is None
    assert [w.account_id for w in view.list_workspaces(ada.user.id)] == [ada.account_id]
    assert view.get_user(ada.user.id).name == "Ada"
    assert view.get_user(cy.user.id).name == "Cy"  # a member of the same workspace
    with pytest.raises(PermissionError):
        view.get_user(dee.user.id)
    invite = store.for_account(dee.account_id, dee.user.id).invite("ada@example.test")
    assert view.find_invite(invite.token).account_id == dee.account_id  # by its secret
    with pytest.raises(PermissionError):
        view.accept_invite(invite.token, cy.user.id)  # not for another person
    assert view.accept_invite(invite.token, ada.user.id).user_id == ada.user.id


def test_a_view_acting_for_no_person_keeps_them_all(store):
    _, _, dee = people(store)
    other = store.create_account("Elsewhere")
    view = store.for_account(other.id)
    assert {a.id for a in view.list_accounts()} >= {other.id, dee.account_id}
    assert view.get_account(dee.account_id).id == dee.account_id
    assert view.get_user(dee.user.id).name == "Dee"
    assert view.list_workspaces(dee.user.id)
    view.set_plan(dee.account_id, "solo")
    assert view.plan_in_force(dee.account_id) == "solo"
    assert view.create_account("Third").name == "Third"
