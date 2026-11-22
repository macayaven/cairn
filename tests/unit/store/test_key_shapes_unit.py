"""Unit tests for the two shapes of an API key (review, finding 2): a key made now starts with
KEY_PREFIX, and a key made before, with no prefix, keeps working unchanged. Everything that
reads a key takes both: the store's lookups, `x-api-key`, `Authorization: Bearer` on /api,
the sign-in form, and the file key-create writes."""

import pytest
from starlette.testclient import TestClient

from cairn.server import SESSION_COOKIE, create_app
from cairn.store import ACCESS_TOKEN_PREFIX, KEY_PREFIX, REFRESH_TOKEN_PREFIX, Store, credentials
from cairn.web import cli

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    opened.create_project("ops", "")
    yield opened
    opened.close()


def made_before(store: Store, label: str, monkeypatch) -> str:
    """A key as the store made them before the prefix: token_urlsafe(32) alone."""
    with monkeypatch.context() as patched:
        patched.setattr(credentials, "KEY_PREFIX", "")
        key = store.create_key(label)
    assert not key.startswith(KEY_PREFIX) and len(key) == 43
    return key


@pytest.fixture
def both(store, monkeypatch) -> dict[str, str]:
    return {"new": store.create_key("new"), "old": made_before(store, "old", monkeypatch)}


def test_a_key_made_now_has_the_prefix_and_never_an_oauth_tokens(store):
    key = store.create_key("laptop")
    assert key.startswith("cairn_") and len(key) == len("cairn_") + 43
    assert not key.startswith((ACCESS_TOKEN_PREFIX, REFRESH_TOKEN_PREFIX)) and "." not in key


@pytest.mark.parametrize("shape", ["new", "old"])
def test_the_store_finds_a_key_of_either_shape(store, both, shape):
    key = both[shape]
    assert store.check_key(key) is True
    principal, _ = store.identify_key(key)
    assert principal.label == shape
    assert store.check_key(key + "x") is False


@pytest.mark.parametrize("shape", ["new", "old"])
def test_the_api_takes_a_key_of_either_shape_in_either_header(store, both, shape):
    key = both[shape]
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        assert client.get("/api/projects", headers={"x-api-key": key}).status_code == 200
        bearer = {"authorization": f"Bearer {key}"}
        assert client.get("/api/projects", headers=bearer).status_code == 200
        wrong = {"x-api-key": key[:-1]}
        assert client.get("/api/projects", headers=wrong).status_code == 401


@pytest.mark.parametrize("shape", ["new", "old"])
def test_the_sign_in_form_takes_a_key_of_either_shape(store, both, shape):
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        login = client.post(
            "/login", data={"key": both[shape]}, headers=SAME_ORIGIN, follow_redirects=False
        )
        assert login.status_code == 303 and login.cookies.get(SESSION_COOKIE)


def test_key_create_writes_a_prefixed_key_that_opens_the_api(tmp_path):
    db, out = tmp_path / "cairn.db", tmp_path / "key.env"
    Store(db).close()
    assert cli.main(["key-create", "--db", str(db), "--label", "cli", "--out", str(out)]) == 0
    (line,) = out.read_text(encoding="utf-8").splitlines()
    name, _, key = line.partition("=")
    assert name == "CAIRN_API_KEY" and key.startswith(KEY_PREFIX)
    store = Store(db)
    try:
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            assert client.get("/api/projects", headers={"x-api-key": key}).status_code == 200
    finally:
        store.close()
