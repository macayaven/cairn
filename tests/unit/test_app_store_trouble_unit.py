"""Unit tests for how the app answers the store's own trouble (R1), beside what
tests/acceptance/test_r1_store_health.py pins: /healthz on threads of its own and on an
error it does not know, a page and the sign-in renewal on a full disk or an unusable store,
and the access log's 503."""

import logging
import sqlite3
import threading

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.identity import GitHub
from cairn.memory import OUT_OF_SERVICE, UNWRITABLE
from cairn.server import SignIn, create_app
from cairn.store import CannotWrite, Store, StoreUnusable

BASE = "https://kb.example.test"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}


class GitHubFake:
    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == "https://github.com/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_made_up"})
        if url == "https://api.github.com/user":
            return httpx.Response(200, json={"id": 7, "login": "ada", "name": "Ada Quill"})
        if url == "https://api.github.com/user/emails":
            emails = [{"email": "ada@example.test", "primary": True, "verified": True}]
            return httpx.Response(200, json=emails)
        return httpx.Response(404)


@pytest.fixture
def store(tmp_path):
    made = Store(tmp_path / "kb.db")
    yield made
    made.close()


def signed_in(store, **settings) -> TestClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(GitHubFake()))
    sign_in = SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=http)
    app = create_app(store, public_url=BASE, sign_in=sign_in, **settings)
    browser = TestClient(app, base_url=BASE, raise_server_exceptions=False)
    started = browser.get("/auth/github", follow_redirects=False)
    state = dict(httpx.URL(started.headers["location"]).params)["state"]
    back = browser.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
    assert back.status_code == 200, back.text
    return browser


def full() -> CannotWrite:
    return CannotWrite(sqlite3.OperationalError("database or disk is full"))


# /healthz


def test_healthz_reads_the_store_on_threads_of_its_own(store, monkeypatch):
    """Requests that wait for the store's lock (compact) run on Starlette's threads; the
    probe does not queue behind them."""
    seen = []
    health = store.health

    def recorded(*args, **kwargs):
        seen.append(threading.current_thread().name)
        return health(*args, **kwargs)

    monkeypatch.setattr(store, "health", recorded)
    client = TestClient(create_app(store, public_url=BASE))
    assert client.get("/healthz").status_code == 200
    assert len(seen) == 1 and seen[0].startswith("cairn-healthz")


@pytest.mark.parametrize("answer", ["ok", "busy"])
def test_healthz_is_200_for_a_store_at_work(store, monkeypatch, answer):
    monkeypatch.setattr(store, "health", lambda *a, **k: answer)
    r = TestClient(create_app(store, public_url=BASE)).get("/healthz")
    assert (r.status_code, r.content) == (200, b'{"status":"ok"}')


def test_healthz_is_503_on_an_error_it_does_not_know(store, monkeypatch):
    def fails(*args, **kwargs):
        raise RuntimeError("something unforeseen at /srv/kb.db")

    monkeypatch.setattr(store, "health", fails)
    r = TestClient(create_app(store, public_url=BASE), raise_server_exceptions=False).get(
        "/healthz"
    )
    assert (r.status_code, r.content) == (503, b'{"status":"unavailable"}')


def test_head_healthz_answers_the_same_status(store):
    client = TestClient(create_app(store, public_url=BASE))
    assert client.head("/healthz").status_code == 200
    store.close()
    assert client.head("/healthz").status_code == 503


# A page and the sign-in's renewal


def test_a_page_that_meets_a_full_disk_is_a_503_page_with_the_sentence(store, monkeypatch):
    browser = signed_in(store)

    def refused(*args, **kwargs):
        raise full()

    monkeypatch.setattr(store, "list_projects", refused)
    r = browser.get("/")
    assert r.status_code == 503
    assert UNWRITABLE.split(":")[0] in r.text
    assert "kb.db" not in r.text and "sqlite" not in r.text.lower()


def test_an_unusable_store_is_a_503_page_for_a_signed_in_browser(store):
    browser = signed_in(store)
    store._con.set_authorizer(
        lambda action, arg1, *_: (
            sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_TRANSACTION and arg1 in ("COMMIT", "ROLLBACK")
            else sqlite3.SQLITE_OK
        )
    )
    with pytest.raises(StoreUnusable):
        store.create_project("beta")
    r = browser.get("/")
    assert r.status_code == 503
    assert OUT_OF_SERVICE.split(":")[0] in r.text


def test_a_renewal_the_disk_refuses_leaves_the_request_to_go_on(store, monkeypatch):
    """B1's renewal is a write on each use of a remembered sign-in; a full disk skips it,
    and the reader still reads."""
    browser = signed_in(store, session_days=30)

    def refused(*args, **kwargs):
        raise full()

    monkeypatch.setattr(store, "renew_session", refused)
    assert browser.get("/").status_code == 200


def test_a_renewal_that_finds_the_store_unusable_is_a_503(store, monkeypatch):
    browser = signed_in(store, session_days=30)

    def unusable(*args, **kwargs):
        raise StoreUnusable()

    monkeypatch.setattr(store, "renew_session", unusable)
    r = browser.get("/")
    assert r.status_code == 503 and OUT_OF_SERVICE.split(":")[0] in r.text


def test_an_unusable_store_behind_the_wall_tells_a_visitor_nothing_more(store, monkeypatch):
    browser = signed_in(store, wall=True)
    browser.cookies.clear()

    def unusable(*args, **kwargs):
        raise StoreUnusable()

    monkeypatch.setattr(store, "identify_session", unusable)
    browser.cookies.set("cairn_session", "made-up-token")
    r = browser.get("/projects", follow_redirects=False)
    assert (r.status_code, r.text) == (503, "Service Unavailable")


# The access log


def test_the_access_log_records_a_full_disk_as_503(store, caplog):
    key = store.create_key("ops")
    store.create_project("alpha")
    client = TestClient(create_app(store, public_url=BASE), raise_server_exceptions=False)
    pages = store._con.execute("PRAGMA page_count").fetchone()[0]
    store._con.execute(f"PRAGMA max_page_count = {pages}")
    text = "\n\n".join(f"Pump {n} runs at {n % 24:02d}:00 on the north line." for n in range(400))
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        r = client.post(
            "/api/projects/alpha/notes", headers={"x-api-key": key}, json={"text": text}
        )
    assert r.status_code == 503 and r.json() == {"error": UNWRITABLE}
    (record,) = [r for r in caplog.records if r.name == "cairn.access"]
    assert (record.action, record.project, record.status) == ("note", "alpha", 503)
