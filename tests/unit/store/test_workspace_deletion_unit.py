"""Unit tests for deleting a workspace in one write: the caller's own view checks, inside the
write that deletes, that the caller is an owner now (or acts for no person). A person
demoted or removed after the page checked them deletes nothing."""

import pytest
from starlette.testclient import TestClient

import cairn.web.account
from cairn.server import SESSION_COOKIE, create_app
from cairn.store import Store

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
ISSUER = "https://id.example.test"


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def two_owners(store: Store):
    """Rowan's personal workspace, with Sage invited in as a second owner."""
    rowan = store.sign_in(
        "sso", "sub-rowan", "rowan@example.test", "Rowan", issuer=ISSUER, join="personal"
    )
    account = rowan.account_id
    sage = store.sign_in(
        "sso", "sub-sage", "sage@example.test", "Sage", issuer=ISSUER, join="personal"
    )
    invite = store.for_account(account, rowan.user.id).invite("sage@example.test", role="owner")
    store.accept_invite(invite.token, sage.user.id)
    return rowan, store.for_account(account, sage.user.id)


def test_an_owner_deletes_their_own_workspace_through_their_own_view(store):
    rowan, _ = two_owners(store)
    view = store.for_account(rowan.account_id, rowan.user.id)
    assert view.delete_account(rowan.account_id) is True
    assert store.get_account(rowan.account_id) is None


@pytest.mark.parametrize("change", ["demoted", "removed"])
def test_a_person_no_longer_an_owner_when_the_write_runs_deletes_nothing(store, change):
    rowan, sage = two_owners(store)
    view = store.for_account(rowan.account_id, rowan.user.id)  # admitted as an owner
    if change == "demoted":
        sage.set_role(rowan.user.id, "member")
    else:
        sage.remove_member(rowan.user.id)
    with pytest.raises(PermissionError):
        view.delete_account(rowan.account_id)
    assert store.get_account(rowan.account_id) is not None


def test_a_person_cannot_delete_another_workspace_through_their_view(store):
    rowan, _ = two_owners(store)
    other = store.create_account("Elsewhere")
    view = store.for_account(rowan.account_id, rowan.user.id)
    with pytest.raises(PermissionError):
        view.delete_account(other.id)
    assert store.get_account(other.id) is not None


def test_a_view_acting_for_no_person_still_deletes_a_workspace(store):
    rowan, _ = two_owners(store)
    assert store.for_account(rowan.account_id).delete_account(rowan.account_id) is True


def test_the_web_view_deletes_nothing_for_an_owner_demoted_after_its_check(store, monkeypatch):
    rowan, sage = two_owners(store)
    checked = cairn.web.account._rights

    def check_then_demote(memory, *args, **kwargs):
        rights = checked(memory, *args, **kwargs)
        sage.set_role(rowan.user.id, "member")  # another owner, between check and write
        return rights

    monkeypatch.setattr(cairn.web.account, "_rights", check_then_demote)
    name = store.get_account(rowan.account_id).name
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        client.cookies.set(SESSION_COOKIE, rowan.token)
        r = client.post("/account/delete", data={"confirm": name}, headers=SAME_ORIGIN)
    assert r.status_code == 403 and 'role="alert"' in r.text
    assert store.get_account(rowan.account_id) is not None
