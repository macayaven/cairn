"""Unit tests for the browser sessions opened with an API key: revoking the key ends them in
the same write, and a key revoked before its session is made opens none. Sessions a person
opened through an identity provider are not touched."""

import pytest
from starlette.testclient import TestClient

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


def owner(store: Store):
    """Ada, who signs in first through a provider and owns the default workspace."""
    signed = store.sign_in(
        "sso", "sub-ada", "ada@example.test", "Ada", issuer=ISSUER, join="default"
    )
    return signed, store.for_account(signed.account_id, signed.user.id)


def session_with(store: Store, key: str) -> str:
    """A session opened with `key`, as the login form opens one."""
    principal, key_id = store.identify_key(key)
    view = store.for_account(principal.account_id, user_id=principal.user_id)
    return view.acting_as("key", principal.label, key_id).create_session()


def test_revoking_a_key_ends_the_sessions_opened_with_it_and_no_other(store):
    signed, ada = owner(store)
    laptop, phone = ada.create_key("laptop"), ada.create_key("phone")
    by_laptop, by_phone = session_with(store, laptop), session_with(store, phone)
    workspace_key = store.create_key("cli")  # a key of the workspace, acting for no person
    by_cli = session_with(store, workspace_key)

    assert ada.revoke_key("laptop") is True
    assert store.identify_session(by_laptop) is None
    assert store.identify_session(by_phone) is not None
    assert store.identify_session(signed.token) is not None  # a provider's session stays
    assert store.revoke_key("cli") is True
    assert store.identify_session(by_cli) is None


def test_a_key_revoked_after_it_was_admitted_opens_no_session(store):
    _, ada = owner(store)
    key = ada.create_key("laptop")
    principal, key_id = store.identify_key(key)
    admitted = store.for_account(principal.account_id, user_id=principal.user_id)
    admitted = admitted.acting_as("key", principal.label, key_id)
    ada.revoke_key("laptop")
    with pytest.raises(PermissionError):
        admitted.create_session()
    assert store.list_audit()[0].action == "key.revoked"  # and nothing after it


def test_a_browser_signed_in_with_a_revoked_key_is_sent_to_log_in(store):
    signed, ada = owner(store)
    key = ada.create_key("laptop")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:

        def as_browser(token: str) -> None:
            client.cookies.clear()
            client.cookies.set(SESSION_COOKIE, token)

        def get(token: str):
            as_browser(token)
            return client.get("/", follow_redirects=False)

        login = client.post(
            "/login", data={"key": key}, headers=SAME_ORIGIN, follow_redirects=False
        )
        by_key = login.cookies[SESSION_COOKIE]
        assert login.status_code == 303 and get(by_key).status_code == 200
        as_browser(signed.token)  # Ada, in another browser
        revoked = client.post("/keys/laptop/revoke", headers=SAME_ORIGIN, follow_redirects=False)
        assert revoked.status_code == 303
        after = get(by_key)
        assert after.status_code in (303, 401)
        assert after.status_code == 401 or after.headers["location"].startswith("/login")
        assert get(signed.token).status_code == 200
