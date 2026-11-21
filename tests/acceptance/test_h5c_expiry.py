"""H5c acceptance tests (frozen): what has run out is removed on a timer, not only kept from use.

Written by the orchestrator before implementation. Implementers must not edit this file.

The privacy guide says how long a browser session, an agent's token, an invitation and an
agent that was never approved are kept. Until now each only stopped working at its time: its
row (a digest, an invited address) stayed until some later write of the same kind happened
to remove it, and a session's row was never removed. This is the rule that makes the guide
true.

The store
- `Store.sweep_expired() -> int` removes, in one write, every row whose time has passed, in
  every workspace: sessions, invitations, authorization codes, access tokens, grants whose
  refresh token has expired with all they hold, and agents that registered 7 days ago or
  earlier and never got a grant (one with a code still waiting is left). It returns how many
  rows it removed.
- It removes nothing that still works: a session, an invitation or a connection that has
  not run out is untouched and still does what it did.
- With nothing to remove it returns 0 and writes nothing to the database.

The server
- `create_app(store, sweep_every=SECONDS)` runs the sweep in the background every that many
  seconds, off the event loop, from the moment the app starts; None, the default, never
  does, and a number of 0 or less is a ValueError. `app.state.sweep_every` holds the value.
- `serve` sweeps every 600 seconds unless told: `--sweep-every SECONDS`, and
  `--sweep-every 0` turns it off.
"""

import asyncio
import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
import uvicorn

from cairn import server
from cairn.server import create_app
from cairn.store import Store
from wait_helpers import next_loop_turn, wait_for_event

ISSUER = "https://id.example.test/pool-1"
AUDIENCE = "https://cairn.example.test/mcp"
REDIRECT = "http://127.0.0.1:33418/callback"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
HOUR, DAY = timedelta(hours=1), timedelta(days=1)


class World:
    """A store on a clock the test moves, with one person in a shared workspace."""

    def __init__(self, tmp_path) -> None:
        self.db = tmp_path / "cairn.db"
        self.now = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
        self.store = Store(self.db, clock=lambda: self.now)
        signed = self.store.sign_in(
            "sso", "sub-ada", "ada@example.test", "Ada", issuer=ISSUER, join="default"
        )
        self.store.end_session(signed.token)  # the tests count the sessions they make
        self.ada = self.store.for_account(signed.account_id, signed.user.id)

    def column(self, sql: str, *args: object) -> list:
        con = sqlite3.connect(self.db)
        try:
            return [row[0] for row in con.execute(sql, args)]
        finally:
            con.close()

    def data_version(self, con: sqlite3.Connection) -> int:
        return con.execute("PRAGMA data_version").fetchone()[0]


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path)
    yield made
    made.store.close()


def test_what_has_run_out_is_removed_and_what_still_works_stays(world):
    ada, store = world.ada, world.store
    short = ada.create_session(ttl_seconds=60)
    long = ada.create_session()  # 12 hours
    ada.invite("cy@example.test", ttl_seconds=3600)
    ada.invite("dee@example.test")  # 7 days
    idle = store.register_client("never approved", [REDIRECT])
    agent = store.register_client("approved", [REDIRECT])
    code = ada.create_code(agent.id, REDIRECT, CHALLENGE, audience=AUDIENCE)
    pair = store.redeem_code(code, agent.id, audience=AUDIENCE)
    waiting = store.register_client("mid-approval", [REDIRECT])

    world.now += 2 * HOUR
    removed = store.sweep_expired()

    assert removed >= 3  # the session, the invitation and the access token at least
    assert world.column("SELECT digest FROM sessions") == [digest(long)]
    assert store.resolve_session(long) is not None and store.resolve_session(short) is None
    assert world.column("SELECT email FROM invites") == ["dee@example.test"]
    assert digest(pair.access_token) not in world.column("SELECT digest FROM oauth_tokens")
    assert set(world.column("SELECT id FROM oauth_clients")) == {idle.id, agent.id, waiting.id}
    renewed = store.rotate_refresh_token(pair.refresh_token, agent.id, audience=AUDIENCE)
    assert renewed is not None and store.resolve_token(renewed.access_token) is not None

    world.now += 8 * DAY
    ada.create_code(waiting.id, REDIRECT, CHALLENGE, audience=AUDIENCE)  # someone is deciding
    assert store.sweep_expired() >= 3  # the other session, the other invitation, the idle agent
    assert world.column("SELECT digest FROM sessions") == []
    assert world.column("SELECT email FROM invites") == []
    assert set(world.column("SELECT id FROM oauth_clients")) == {agent.id, waiting.id}

    world.now += 31 * DAY  # the connection was not used for 30 days
    assert store.sweep_expired() >= 2
    assert world.column("SELECT id FROM oauth_grants") == []
    assert world.column("SELECT digest FROM oauth_tokens") == []
    assert world.column("SELECT digest FROM oauth_codes") == []


def test_a_sweep_with_nothing_to_remove_writes_nothing(world):
    ada, store = world.ada, world.store
    session = ada.create_session()
    ada.invite("dee@example.test")
    watcher = sqlite3.connect(world.db)
    try:
        before = world.data_version(watcher)

        assert store.sweep_expired() == 0

        assert world.data_version(watcher) == before  # no other connection wrote
    finally:
        watcher.close()
    assert store.resolve_session(session) is not None


def test_the_sweep_covers_every_workspace(world):
    store = world.store
    other = store.for_account(store.create_account("Brook's").id)
    mine, theirs = world.ada.create_session(ttl_seconds=60), other.create_session(ttl_seconds=60)
    world.now += HOUR

    assert store.sweep_expired() == 2
    assert world.column("SELECT digest FROM sessions") == []
    assert store.resolve_session(mine) is None and store.resolve_session(theirs) is None


def test_the_app_sweeps_in_the_background_when_asked(world, monkeypatch):
    world.ada.create_session(ttl_seconds=60)
    world.now += HOUR
    app = create_app(world.store, sweep_every=0.05)
    assert app.state.sweep_every == 0.05

    async def run() -> None:
        swept = asyncio.Event()
        loop = asyncio.get_running_loop()
        sweep = Store.sweep_expired

        def observed(self):
            removed = sweep(self)
            if removed:
                loop.call_soon_threadsafe(swept.set)
            return removed

        monkeypatch.setattr(Store, "sweep_expired", observed)
        async with app.router.lifespan_context(app):
            await wait_for_event(swept, "the background sweep did not run")

    asyncio.run(run())
    assert world.column("SELECT digest FROM sessions") == []


def test_without_the_option_the_app_never_sweeps(world, monkeypatch):
    world.ada.create_session(ttl_seconds=60)
    world.now += HOUR

    def unexpected(*args, **kwargs):
        pytest.fail("a sweeper was made with sweeping turned off")

    with monkeypatch.context() as patch:
        patch.setattr("cairn.web.app.Sweeper", unexpected)
        app = create_app(world.store)
        assert app.state.sweep_every is None

        async def run() -> None:
            async with app.router.lifespan_context(app):
                await next_loop_turn()

        asyncio.run(run())
        assert len(world.column("SELECT digest FROM sessions")) == 1
    for bad in (0, -1):
        with pytest.raises(ValueError):
            create_app(world.store, sweep_every=bad)


def test_serve_sweeps_every_ten_minutes_unless_told(tmp_path, monkeypatch):
    db = tmp_path / "serve.db"
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    serve = ["serve", "--db", str(db)]

    assert server.main(serve) == 0
    assert server.main([*serve, "--sweep-every", "45"]) == 0
    assert server.main([*serve, "--sweep-every", "0"]) == 0

    assert [app.state.sweep_every for app in apps] == [600.0, 45.0, None]
    for bad in ("-1", "soon", "nan", "inf"):
        with pytest.raises(SystemExit):
            server.main([*serve, "--sweep-every", bad])
