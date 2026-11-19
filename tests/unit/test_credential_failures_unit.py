"""Unit tests for the limit on refused credentials per client address (H1, item 4; the
acceptance test is tests/acceptance/test_h1_hardening.py): cairn.web.ratelimit's
CredentialFailures, on a clock moved by hand, and how Auth and POST /login use it. Every
address is from the ranges set aside for documentation."""

import pytest
from starlette.testclient import TestClient

from cairn.memory import ACCESS_LOGGER
from cairn.store import Store
from cairn.web.app import create_app
from cairn.web.ratelimit import FAILURE_WINDOW, FAILURES, CredentialFailures

SAME_ORIGIN = {"sec-fetch-site": "same-origin"}


class Clock:
    def __init__(self) -> None:
        self.now = 4_000_000_000.0  # 2096: far from today

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


def test_thirty_failures_in_a_minute_block_the_address(clock):
    limit = CredentialFailures(clock)
    for n in range(FAILURES):
        assert limit.blocked("203.0.113.7") == 0, n
        limit.failed("203.0.113.7")
    assert limit.blocked("203.0.113.7") == FAILURE_WINDOW
    assert limit.blocked("198.51.100.20") == 0  # another address is not touched
    clock.now += 20
    assert limit.blocked("203.0.113.7") == FAILURE_WINDOW - 20  # what Retry-After says
    clock.now += FAILURE_WINDOW - 20
    assert limit.blocked("203.0.113.7") == 0  # the minute has passed


def test_asking_counts_nothing(clock):
    limit = CredentialFailures(clock)
    for _ in range(FAILURES * 3):
        assert limit.blocked("203.0.113.7") == 0
    assert limit.tracked() == 0


def test_failures_leave_the_window_one_by_one(clock):
    limit = CredentialFailures(clock, limit=3, window=60)
    for _ in range(3):
        limit.failed("203.0.113.7")
        clock.now += 10
    assert limit.blocked("203.0.113.7") == 30  # the first, made at 0 s, leaves at 60 s
    clock.now += 30
    assert limit.blocked("203.0.113.7") == 0
    limit.failed("203.0.113.7")
    assert limit.blocked("203.0.113.7") == 10  # the second leaves next


def test_a_clock_that_steps_back_does_not_free_an_address(clock):
    limit = CredentialFailures(clock, limit=2, window=60)
    limit.failed("203.0.113.7")
    limit.failed("203.0.113.7")
    clock.now -= 3600
    assert limit.blocked("203.0.113.7") == 60


def test_an_ipv6_64_is_one_address_and_no_address_is_one_too(clock):
    """The sign-in limit's rule (cairn.oauth.RateLimit): one /64 is one subscriber, and every
    request without a usable address shares one count."""
    limit = CredentialFailures(clock, limit=2)
    limit.failed("2001:db8:1:2::1")
    limit.failed("2001:db8:1:2:ffff::9")
    assert limit.blocked("2001:db8:1:2::77") > 0
    assert limit.blocked("2001:db8:1:3::1") == 0
    limit.failed(None)
    limit.failed("testclient")
    assert limit.blocked(None) > 0
    limit.failed("::ffff:192.0.2.1")
    limit.failed("192.0.2.1")
    assert limit.blocked("192.0.2.1") > 0


def test_addresses_are_bounded_and_the_oldest_is_forgotten(clock):
    limit = CredentialFailures(clock, limit=2, addresses=3)
    for host in ("192.0.2.1", "192.0.2.2", "192.0.2.3"):
        limit.failed(host)
        limit.failed(host)
    limit.failed("192.0.2.4")
    assert limit.tracked() == 3
    assert limit.blocked("192.0.2.1") == 0  # seen longest ago: forgotten, starts again
    assert limit.blocked("192.0.2.3") > 0


def test_it_keeps_addresses_only_and_nothing_of_a_credential(clock):
    """A correct credential is looked up, so nothing needs to remember one: the limit takes
    addresses, never a credential, and keeps nothing but their failures' times."""
    limit = CredentialFailures(clock)
    assert not hasattr(limit, "accepted") and not hasattr(limit, "known")
    limit.failed("203.0.113.7")
    assert set(vars(limit)) == {
        "_clock", "_limit", "_window", "addresses", "_failures", "_lock", "_holding",
    }  # fmt: skip
    assert set(limit._holding._order) == {"203.0.113.7"}  # Security 4: which to forget, by address


BASE = "http://127.0.0.1:8765"
MCP = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
PROBE = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


@pytest.fixture
def app(tmp_path):
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("ops")
    app = create_app(store, public_url=BASE)
    try:
        yield app, store, key
    finally:
        store.close()


def from_address(app, address: str) -> TestClient:
    return TestClient(app, base_url=BASE, client=(address, 50000))


def test_requests_with_no_credential_count_nothing(app):
    app, _, _ = app
    with from_address(app, "203.0.113.7") as client:
        for _ in range(100):
            assert client.get("/api/projects").status_code == 401
            assert client.post("/mcp", headers=MCP, json=PROBE).status_code == 401
            assert client.post("/login", data={"key": " "}, headers=SAME_ORIGIN).status_code == 401
    assert app.state.credential_failures.tracked() == 0


def test_a_blocked_address_is_still_told_where_oauth_starts(app):
    """A probe of /mcp with no credential is how every agent finds where OAuth starts: from a
    blocked address it is answered as before, 401 with the challenge, while /api with none
    is 429."""
    app, _, _ = app
    with from_address(app, "203.0.113.7") as client:
        for n in range(FAILURES):
            client.get("/api/projects", headers={"x-api-key": f"wrong-{n}"})
        probe = client.post("/mcp", headers=MCP, json=PROBE)
        assert (
            probe.status_code == 401 and "resource_metadata=" in probe.headers["www-authenticate"]
        )
        assert client.get("/api/projects").status_code == 429


def test_a_refused_bearer_and_a_token_on_api_count(app):
    app, _, _ = app
    with from_address(app, "203.0.113.7") as client:
        for n in range(FAILURES):
            bearer = {"authorization": f"Bearer cat_not-a-token-{n}"}
            assert client.get("/api/projects", headers=bearer).status_code == 401
        assert client.get("/api/projects", headers={"authorization": "Bearer x"}).status_code == 429


def test_a_blocked_address_still_gets_in_with_a_correct_key(app):
    """Over the limit, the credential is still looked up: a correct key is admitted, from an
    address no correct credential came from before, on /api, on /mcp and at POST /login, and
    is not counted; a wrong one is answered 429 in place of 401."""
    app, _, key = app
    with from_address(app, "203.0.113.7") as client:
        for n in range(FAILURES):
            wrong = {"x-api-key": f"wrong-{n}"}
            assert client.get("/api/projects", headers=wrong).status_code == 401
        for _ in range(FAILURES):
            assert client.get("/api/projects", headers={"x-api-key": key}).status_code == 200
        mcp = client.post("/mcp", headers={**MCP, "x-api-key": key}, json=PROBE)
        assert mcp.status_code == 200
        login = client.post("/login", data={"key": key}, headers=SAME_ORIGIN,
                            follow_redirects=False)  # fmt: skip
        assert login.status_code == 303
        assert client.get("/api/projects", headers={"x-api-key": "wrong"}).status_code == 429
        assert client.post("/login", data={"key": "wrong"}, headers=SAME_ORIGIN).status_code == 429


def test_the_refusal_is_recorded_without_what_was_presented(app, caplog):
    app, _, _ = app
    with from_address(app, "203.0.113.7") as client, caplog.at_level("INFO"):
        for n in range(FAILURES + 1):
            client.get("/api/projects", headers={"x-api-key": f"secret-looking-{n}"})
    lines = [r.getMessage() for r in caplog.records if r.name == ACCESS_LOGGER]
    assert lines[-1] == "denied by an unknown caller: 429"
    assert not any("secret-looking" in line for line in lines)


def test_settings_that_cannot_work_are_refused(clock):
    for bad in ({"limit": 0}, {"window": 0}, {"addresses": 0}):
        with pytest.raises(ValueError):
            CredentialFailures(clock, **bad)
