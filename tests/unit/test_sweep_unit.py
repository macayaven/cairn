"""Unit tests for Store.sweep_expired (cairn.store.expiry) and the background sweep of
`serve --sweep-every SECONDS` (cairn.web.sweeper). The store's clock is injected; each kind of
row is checked one second before its time (it stays) and at its time (it goes)."""

import asyncio
import logging
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import pytest

from cairn.store import (
    ACCESS_TOKEN_TTL,
    CODE_TTL,
    INVITE_TTL,
    REFRESH_TOKEN_TTL,
    UNUSED_CLIENT_TTL,
    Store,
)
from cairn.web.sweeper import Sweeper

ISSUER = "https://id.example.test/pool-1"
AUDIENCE = "https://cairn.example.test/mcp"
REDIRECT = "http://127.0.0.1:33418/callback"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
SECOND = timedelta(seconds=1)


class World:
    """A store on a clock the test moves, with one person in the default workspace."""

    def __init__(self, tmp_path) -> None:
        self.db = tmp_path / "cairn.db"
        self.now = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
        self.store = Store(self.db, clock=lambda: self.now)
        signed = self.store.sign_in(
            "sso", "sub-ada", "ada@example.test", "Ada", issuer=ISSUER, join="default"
        )
        self.store.end_session(signed.token)
        self.ada = self.store.for_account(signed.account_id, signed.user.id)
        self.start = self.now

    def at(self, delta: timedelta) -> None:
        self.now = self.start + delta

    def rows(self, sql: str) -> list:
        con = sqlite3.connect(self.db)
        try:
            return [row[0] for row in con.execute(sql)]
        finally:
            con.close()

    def connect(self, name: str = "agent"):
        """A client approved by Ada, its code redeemed: (client, token pair)."""
        client = self.store.register_client(name, [REDIRECT])
        code = self.ada.create_code(client.id, REDIRECT, CHALLENGE, audience=AUDIENCE)
        return client, self.store.redeem_code(code, client.id, audience=AUDIENCE)


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path)
    yield made
    made.store.close()


# Each kind of row, at its boundary


def test_a_session_stays_until_its_time_and_goes_at_it(world):
    token = world.ada.create_session(ttl_seconds=60)
    world.at(timedelta(seconds=60) - SECOND)
    assert world.store.sweep_expired() == 0
    assert world.store.resolve_session(token) is not None
    world.at(timedelta(seconds=60))
    assert world.store.sweep_expired() == 1
    assert world.rows("SELECT digest FROM sessions") == []


def test_an_invitation_stays_until_its_time_and_goes_at_it(world):
    invite = world.ada.invite("cy@example.test")
    world.at(timedelta(seconds=INVITE_TTL) - SECOND)
    assert world.store.sweep_expired() == 0
    assert world.store.find_invite(invite.token) is not None
    world.at(timedelta(seconds=INVITE_TTL))
    assert world.store.sweep_expired() == 1
    assert world.rows("SELECT email FROM invites") == []


def test_a_code_stays_until_its_time_and_goes_at_it(world):
    client = world.store.register_client("agent", [REDIRECT])
    code = world.ada.create_code(client.id, REDIRECT, CHALLENGE, audience=AUDIENCE)
    world.at(timedelta(seconds=CODE_TTL) - SECOND)
    assert world.store.sweep_expired() == 0
    assert world.store.find_code(code) is not None
    world.at(timedelta(seconds=CODE_TTL))
    assert world.store.sweep_expired() == 1
    assert world.rows("SELECT digest FROM oauth_codes") == []
    assert world.rows("SELECT id FROM oauth_clients") == [client.id]  # not 7 days old yet


def test_an_access_token_stays_until_its_time_and_goes_at_it(world):
    _, pair = world.connect()
    world.at(timedelta(seconds=ACCESS_TOKEN_TTL) - SECOND)
    world.store.sweep_expired()  # the spent code, long expired
    assert world.store.resolve_token(pair.access_token) is not None
    assert len(world.rows("SELECT digest FROM oauth_tokens")) == 2
    world.at(timedelta(seconds=ACCESS_TOKEN_TTL))
    assert world.store.sweep_expired() == 1
    assert world.rows("SELECT kind FROM oauth_tokens") == ["refresh"]
    assert len(world.rows("SELECT id FROM oauth_grants")) == 1


def test_a_grant_stays_until_its_refresh_token_runs_out_and_goes_with_all_it_holds(world):
    client, pair = world.connect()
    world.at(timedelta(seconds=REFRESH_TOKEN_TTL) - SECOND)
    world.store.sweep_expired()  # the code and the access token
    assert world.store.refresh_token_client(pair.refresh_token) == client.id
    world.at(timedelta(seconds=REFRESH_TOKEN_TTL))
    assert world.store.sweep_expired() == 2  # the grant and its refresh token
    assert world.rows("SELECT id FROM oauth_grants") == []
    assert world.rows("SELECT digest FROM oauth_tokens") == []
    assert world.rows("SELECT id FROM oauth_clients") == [client.id]  # it once got a grant


def test_a_rotated_token_is_remembered_until_its_time_and_goes_at_it(world):
    client, pair = world.connect()
    world.at(timedelta(hours=1))
    second = world.store.rotate_refresh_token(pair.refresh_token, client.id, audience=AUDIENCE)
    world.at(timedelta(hours=2))
    world.store.rotate_refresh_token(second.refresh_token, client.id, audience=AUDIENCE)
    first_gone = timedelta(hours=1, seconds=REFRESH_TOKEN_TTL)  # 30 days after its rotation
    world.at(first_gone - SECOND)
    world.store.sweep_expired()  # the code and the access token
    assert world.rows("SELECT kind FROM oauth_tokens WHERE kind = 'rotated'") == ["rotated"] * 2
    world.at(first_gone)
    assert world.store.sweep_expired() == 1  # the first rotated token, no more
    assert world.rows("SELECT kind FROM oauth_tokens WHERE kind = 'rotated'") == ["rotated"]
    assert len(world.rows("SELECT id FROM oauth_grants")) == 1  # the grant lives on


def test_an_agent_never_approved_stays_for_seven_days_and_goes_at_them(world):
    idle = world.store.register_client("idle", [REDIRECT])
    world.at(timedelta(seconds=UNUSED_CLIENT_TTL) - SECOND)
    assert world.store.sweep_expired() == 0
    assert world.store.get_client(idle.id) is not None
    world.at(timedelta(seconds=UNUSED_CLIENT_TTL))
    assert world.store.sweep_expired() == 1
    assert world.rows("SELECT id FROM oauth_clients") == []


def test_an_agent_with_a_code_waiting_stays_and_one_approved_stays(world):
    waiting = world.store.register_client("waiting", [REDIRECT])
    approved, _ = world.connect("approved")
    world.at(timedelta(seconds=UNUSED_CLIENT_TTL))
    world.ada.create_code(waiting.id, REDIRECT, CHALLENGE, audience=AUDIENCE)
    world.store.sweep_expired()
    assert set(world.rows("SELECT id FROM oauth_clients")) == {waiting.id, approved.id}
    world.at(timedelta(seconds=UNUSED_CLIENT_TTL + CODE_TTL))  # the code ran out unused
    world.store.sweep_expired()
    assert world.rows("SELECT id FROM oauth_clients") == [approved.id]


def test_only_the_store_that_opened_the_database_sweeps(world):
    world.ada.create_session(ttl_seconds=60)
    world.at(timedelta(hours=1))
    for view in (
        world.ada,
        world.store.for_account(world.ada.account_id),
        world.store.narrow(access="read"),
    ):
        with pytest.raises(PermissionError):
            view.sweep_expired()
    assert len(world.rows("SELECT digest FROM sessions")) == 1


def test_a_sweep_that_waited_for_the_lock_judges_by_the_time_it_writes(world):
    """A request holds the store; the sweep waits for it, does not deadlock, and reads the
    clock once it holds the lock."""
    world.ada.create_session(ttl_seconds=60)
    result = {}
    with world.store._lock:
        sweeping = threading.Thread(target=lambda: result.update(n=world.store.sweep_expired()))
        sweeping.start()
        sweeping.join(0.05)
        assert sweeping.is_alive()  # waiting for the request
        world.at(timedelta(seconds=60))  # the session runs out while it waits
    sweeping.join(5)
    assert not sweeping.is_alive() and result == {"n": 1}
    assert world.rows("SELECT digest FROM sessions") == []


# The sweeper


def test_a_sweeper_runs_every_more_than_zero_seconds(world):
    for bad in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            Sweeper(world.store, bad)
    assert Sweeper(world.store, 0.5).every == 0.5


def test_a_run_that_removed_something_is_one_line_with_the_count_only(world, caplog):
    token = world.ada.create_session(ttl_seconds=60)
    world.ada.invite("cy@example.test", ttl_seconds=60)
    sweeper = Sweeper(world.store, 60)
    audited = world.rows("SELECT count(*) FROM audit_events")
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        assert sweeper.run_once() == 0  # nothing has run out: no line
        world.at(timedelta(minutes=1))
        assert sweeper.run_once() == 2
    (record,) = [r for r in caplog.records if r.getMessage().startswith("sweep")]
    assert record.getMessage() == "sweep removed 2 expired rows"
    assert record.action == "sweep" and record.actor_kind == "system" and record.status == 200
    assert token not in record.getMessage() and "cy@" not in record.getMessage()
    assert world.rows("SELECT count(*) FROM audit_events") == audited  # no audit event


def test_a_run_never_overlaps_another(world):
    world.ada.create_session(ttl_seconds=60)
    world.at(timedelta(hours=1))
    sweeper = Sweeper(world.store, 60)
    with sweeper._running:  # one already running
        assert sweeper.run_once() is None
    assert sweeper.run_once() == 1


def test_a_failing_sweep_is_logged_by_its_type_and_the_loop_goes_on(world, monkeypatch, caplog):
    world.ada.create_session(ttl_seconds=60)
    world.at(timedelta(hours=1))
    real, calls = Store.sweep_expired, []

    def flaky(self):
        calls.append(len(calls))
        if len(calls) == 1:
            raise sqlite3.OperationalError("disk I/O error near sessions")
        return real(self)

    monkeypatch.setattr(Store, "sweep_expired", flaky)
    sweeper = Sweeper(world.store, 0.01)

    async def until_swept() -> None:
        task = asyncio.create_task(sweeper.loop())
        try:
            while world.rows("SELECT digest FROM sessions"):
                await asyncio.sleep(0.01)
                assert not task.done(), task.exception()
        finally:
            task.cancel()

    with caplog.at_level(logging.INFO, logger="cairn.access"):
        asyncio.run(asyncio.wait_for(until_swept(), 5))
    assert calls[:2] == [0, 1]
    failed, done = [r for r in caplog.records if r.getMessage().startswith("sweep")][:2]
    assert failed.getMessage() == "sweep failed: OperationalError" and failed.status == 500
    assert "disk" not in failed.getMessage() and "sessions" not in failed.getMessage()
    assert done.getMessage() == "sweep removed 1 expired rows"


def test_a_stopped_sweeper_runs_no_more(world):
    world.ada.create_session(ttl_seconds=60)
    world.at(timedelta(hours=1))
    sweeper = Sweeper(world.store, 60)
    sweeper.stop()
    assert sweeper.run_once() is None
    assert len(world.rows("SELECT digest FROM sessions")) == 1


def test_the_app_stops_its_sweeper_cleanly(world, monkeypatch):
    from starlette.testclient import TestClient

    from cairn.web.app import create_app

    swept = threading.Event()
    real = Store.sweep_expired

    def counted(self):
        swept.set()
        return real(self)

    monkeypatch.setattr(Store, "sweep_expired", counted)
    app = create_app(world.store, sweep_every=0.01)
    with TestClient(app) as client:
        assert swept.wait(5)
        assert client.get("/healthz").status_code == 200
    assert not app.state.sweeper._running.locked()  # no run left on a worker thread
    swept.clear()
    assert not swept.wait(0.1)  # and none starts after the app stopped


def test_the_app_has_no_sweeper_unless_asked(world):
    from cairn.web.app import create_app

    app = create_app(world.store)
    assert app.state.sweep_every is None and app.state.sweeper is None
    for bad in (0, -0.5, float("nan")):
        with pytest.raises(ValueError):
            create_app(world.store, sweep_every=bad)


def test_serve_parses_the_interval():
    from cairn.web.cli import _parser

    def parsed(*extra: str) -> float:
        return _parser().parse_args(["serve", "--db", "x.db", *extra]).sweep_every

    assert parsed() == 600.0
    assert parsed("--sweep-every", "0") == 0.0
    assert parsed("--sweep-every", "2.5") == 2.5
    for bad in ("-1", "soon", "nan", "inf", "-inf"):
        with pytest.raises(SystemExit):
            parsed("--sweep-every", bad)


def test_serve_refuses_a_sweep_more_often_than_once_a_second(tmp_path, monkeypatch):
    """A sweep takes the store's write lock: an interval of a few milliseconds would hold it."""
    import uvicorn

    from cairn import server

    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    serve = ["serve", "--db", str(tmp_path / "cairn.db")]
    for often in ("0.001", "0.5", "0.999"):
        with pytest.raises(SystemExit):
            server.main([*serve, "--sweep-every", often])
    assert server.main([*serve, "--sweep-every", "1"]) == 0
    assert [app.state.sweep_every for app in apps] == [1.0]
