"""`serve --idle-minutes N`: a session not used for N minutes is over, whatever its lifetime,
and the next request is answered as any ended session is. Off unless given. A use is
recorded at most once a minute for one session (so a session ends up to a minute after its N
minutes), never once per request. The absolute lifetime (12 hours, or `--session-days`) is
unchanged, and a session ends at whichever comes first. Schema step 30 adds `used_at` to
sessions. Every name is made up; the clock is the store's, pinned."""

import sqlite3
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie

import httpx
import pytest
from starlette.testclient import TestClient

from cairn import server
from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.store import Store
from cairn.store import schema as store_schema

BASE = "https://kb.example.test"
START = datetime(2099, 11, 14, 9, 0, tzinfo=UTC)
SECOND, MINUTE, HOUR = timedelta(seconds=1), timedelta(minutes=1), timedelta(hours=1)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    made = Store(tmp_path / "kb.db", clock=clock)
    yield made
    made.close()


class GitHubFake:
    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == "https://github.com/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_made_up"})
        if url == "https://api.github.com/user":
            return httpx.Response(200, json={"id": 7, "login": "ada", "name": "Ada"})
        if url == "https://api.github.com/user/emails":
            emails = [{"email": "ada@example.test", "primary": True, "verified": True}]
            return httpx.Response(200, json=emails)
        return httpx.Response(404)


def app(store, clock, **settings):
    http = httpx.AsyncClient(transport=httpx.MockTransport(GitHubFake()))
    sign_in = SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=http)
    return create_app(store, public_url=BASE, sign_in=sign_in, clock=clock, **settings)


def signed_in(store, clock, **settings) -> TestClient:
    browser = TestClient(app(store, clock, **settings), base_url=BASE)
    started = browser.get("/auth/github", follow_redirects=False)
    state = dict(httpx.URL(started.headers["location"]).params)["state"]
    back = browser.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
    assert back.status_code == 200, back.text
    return browser


def in_use(browser) -> bool:
    r = browser.get("/workspace", follow_redirects=False)
    assert r.status_code in (200, 303), r.status_code
    return r.status_code == 200


def at(clock, delta) -> None:
    clock.now = START + delta


# Ending a session not used for N minutes


def test_a_session_used_within_the_minutes_stays_and_one_left_alone_ends(store, clock):
    browser = signed_in(store, clock, idle_minutes=1)
    for seconds in (0, 59, 61):  # the use at 59 s is not recorded (one write a minute)
        at(clock, seconds * SECOND)
        assert in_use(browser), seconds
    at(clock, 61 * SECOND + MINUTE + MINUTE)  # a minute and the grain after the last use
    assert not in_use(browser)
    r = browser.get("/workspace", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"  # as any ended one


def test_an_ended_session_is_not_brought_back_by_a_later_request(store, clock):
    browser = signed_in(store, clock, idle_minutes=5)
    at(clock, 7 * MINUTE)
    assert not in_use(browser)
    with store._read() as con:
        assert con.execute("SELECT used_at FROM sessions").fetchone() == (None,)
    at(clock, 7 * MINUTE + SECOND)
    assert not in_use(browser)


def test_it_ends_up_to_a_minute_late_never_early(store, clock):
    browser = signed_in(store, clock, idle_minutes=10)
    at(clock, 10 * MINUTE)  # not used for exactly ten minutes: not over yet
    assert in_use(browser)
    at(clock, 10 * MINUTE + 11 * MINUTE)  # eleven minutes after that use: over
    assert not in_use(browser)


def test_without_the_option_a_session_is_never_idle_and_no_use_is_recorded(store, clock):
    browser = signed_in(store, clock)
    at(clock, 11 * HOUR)
    assert in_use(browser)
    with store._read() as con:
        assert con.execute("SELECT used_at FROM sessions").fetchone() == (None,)


# Whichever comes first


def test_the_lifetime_still_ends_a_session_in_use(store, clock):
    browser = signed_in(store, clock, idle_minutes=60)
    for half_hours in range(1, 24):
        at(clock, half_hours * 30 * MINUTE)
        assert in_use(browser), half_hours
    at(clock, 12 * HOUR)  # the 12 hours: over, used or not
    assert not in_use(browser)


def test_with_session_days_the_idle_minutes_come_first(store, clock):
    browser = signed_in(store, clock, idle_minutes=30, session_days=7)
    at(clock, 20 * MINUTE)
    assert in_use(browser)
    at(clock, 20 * MINUTE + 32 * MINUTE)
    assert not in_use(browser)


# One write a minute, never one per request


def statements_of(store, run) -> list[str]:
    seen: list[str] = []
    store._con.set_trace_callback(seen.append)
    try:
        run()
    finally:
        store._con.set_trace_callback(None)
    return seen


def session_writes(seen: list[str]) -> list[str]:
    return [s for s in seen if s.lstrip().upper().startswith("UPDATE SESSIONS")]


def test_a_hundred_requests_in_a_minute_record_one_use(store, clock):
    browser = signed_in(store, clock, idle_minutes=5)
    at(clock, 2 * MINUTE)

    def hundred() -> None:
        for n in range(100):
            clock.now = START + 2 * MINUTE + n * 500 * timedelta(milliseconds=1)
            assert in_use(browser)

    seen = statements_of(store, hundred)
    assert len(session_writes(seen)) == 1
    with store._read() as con:
        (used,) = con.execute("SELECT used_at FROM sessions").fetchone()
    assert used == (START + 2 * MINUTE).isoformat(timespec="microseconds")


def test_without_the_option_a_request_reads_the_same_and_writes_nothing(store, clock):
    plain = signed_in(store, clock)
    at(clock, 2 * MINUTE)
    without = statements_of(store, lambda: in_use(plain))
    assert session_writes(without) == []
    idle = signed_in(store, clock, idle_minutes=5)
    at(clock, 2 * MINUTE + 30 * SECOND)
    in_use(idle)  # records its use
    at(clock, 2 * MINUTE + 40 * SECOND)
    with_option = statements_of(store, lambda: in_use(idle))  # not due: no write
    assert session_writes(with_option) == []
    assert len(with_option) == len(without)


# The option


@pytest.mark.parametrize("minutes", [0, 1441, -5, True, 2.5, "5"])
def test_the_minutes_are_a_whole_number_from_1_to_1440(store, clock, minutes):
    with pytest.raises(ValueError, match="--idle-minutes"):
        app(store, clock, idle_minutes=minutes)


def test_the_option_needs_sign_in(store):
    with pytest.raises(ValueError, match="--idle-minutes needs --sign-in"):
        create_app(store, public_url=BASE, idle_minutes=5)


def test_serve_takes_the_option_and_refuses_what_it_cannot_use(tmp_path, monkeypatch, capsys):
    import uvicorn

    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    monkeypatch.setenv("CAIRN_GITHUB_CLIENT_ID", "gh-id")
    monkeypatch.setenv("CAIRN_GITHUB_CLIENT_SECRET", "gh-made-up")
    argv = ["serve", "--db", str(tmp_path / "kb.db"), "--public-url", BASE]
    assert server.main([*argv, "--sign-in", "github", "--idle-minutes", "15"]) == 0
    assert len(apps) == 1
    for bad in ("0", "1441", "x"):
        with pytest.raises(SystemExit):
            server.main([*argv, "--sign-in", "github", "--idle-minutes", bad])
    capsys.readouterr()
    assert server.main([*argv, "--idle-minutes", "15"]) == 2
    assert "--idle-minutes needs --sign-in" in capsys.readouterr().err


# Schema step 30


def test_step_30_adds_the_last_use_and_keeps_every_session(tmp_path):
    db = tmp_path / "kb.db"
    made = Store(db)
    signed = made.sign_in("github", "1", "ada@example.test", "Ada", issuer="https://github.com",
                          join="personal")  # fmt: skip
    made.close()
    assert store_schema._LAST_USE == 30 == store_schema.SCHEMA_VERSION
    columns = "digest, account_id, expires_at, created_at, user_id, label, key_id, vouched_email"
    con = sqlite3.connect(db)
    try:  # the database as version 29 left it
        con.execute("BEGIN")
        before = con.execute(f"SELECT {columns} FROM sessions").fetchall()
        con.execute("ALTER TABLE sessions RENAME TO sessions_v30")
        con.execute(store_schema._V22_SESSIONS)
        con.execute(f"INSERT INTO sessions SELECT {columns} FROM sessions_v30")
        con.execute("DROP TABLE sessions_v30")
        con.execute("PRAGMA user_version = 29")
        con.commit()
    finally:
        con.close()
    again = Store(db)
    try:
        assert again.resolve_session(signed.token) is not None
    finally:
        again.close()
    con = sqlite3.connect(db)
    fresh = sqlite3.connect(tmp_path / "fresh.db")
    try:
        assert con.execute("PRAGMA user_version").fetchone() == (30,)
        assert con.execute(f"SELECT {columns}, used_at FROM sessions").fetchall() == [
            (*row, None) for row in before
        ]
        Store(tmp_path / "fresh.db").close()
        shape = "SELECT sql FROM sqlite_master WHERE name = 'sessions'"
        assert con.execute(shape).fetchall() == fresh.execute(shape).fetchall()
    finally:
        con.close()
        fresh.close()


def test_a_session_cookie_is_not_changed_by_a_recorded_use(store, clock):
    """Recording a use writes the database only: no cookie is sent for it."""
    browser = signed_in(store, clock, idle_minutes=5)
    at(clock, 2 * MINUTE)
    r = browser.get("/workspace", follow_redirects=False)
    assert r.status_code == 200
    jar = SimpleCookie()
    for header in r.headers.get_list("set-cookie"):
        jar.load(header)
    assert not [name for name in jar if name.endswith("session")]


def test_the_first_start_with_the_option_counts_from_the_sign_in(store, clock):
    """No use is recorded without the option, so a server first started with it counts a
    session made before from its sign-in, used or not."""
    old = signed_in(store, clock)
    at(clock, 25 * MINUTE)
    assert in_use(old)  # used, but nothing recorded
    recent = signed_in(store, clock)
    at(clock, 30 * MINUTE)
    with_option = app(store, clock, idle_minutes=10)
    for browser, expected in ((old, False), (recent, True)):
        client = TestClient(with_option, base_url=BASE)
        client.cookies = browser.cookies
        assert in_use(client) is expected
