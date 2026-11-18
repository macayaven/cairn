"""K1 acceptance tests: Slack, both ways. Part 1, the foundations.

One Slack app for each workspace, made by that workspace's owner. A person files a Slack
message as a note or writes one, and asks the bot to find something; the bot answers only
the person who asked. This file is the part every other part stands on: the switch, the
three addresses Slack calls, what is stored, and what goes when the app goes. Set-up and
linking are in test_k1b_slack_setup.py; commands, direct messages and the message shortcut
in test_k1c_slack_use.py. The decisions are in docs/decisions.md; the Slack facts each test
relies on are in docs/slack-api.md, and nothing is pinned here that is not there.

Every name here is made up. Every request Slack would send is built and signed in this
file; every call cairn would make to Slack goes to an httpx.MockTransport.

The switch
- `serve --slack` and `create_app(..., slack=...)`. Off unless the operator gives it.
  Off: none of the addresses below exists (each is 404 as an address that is not a page),
  the Sources page shows nothing about Slack, and no request reads the Slack tables.
- It needs `--sign-in`, an `https://` `--public-url`, and `CAIRN_SLACK_KEY`, 32 random
  bytes in base64, the key every install's two secrets are sealed with. Without any of
  them `serve` stops before it opens the database and its last line names what is missing;
  `create_app` raises the same sentence.
- It is not offered under a brand yet (`--brand-name`: what the bot says, the shortcut's
  name and the command's name would name the software): `serve` stops before it opens the
  database, its last line saying so, and `create_app` raises the same sentence.

The three addresses Slack calls
- POST /integrations/slack/<install>/events, /commands and /actions, where <install> is
  the install's id ("sl_" and 12 hexadecimal characters). They take no session and no key:
  what proves a request is Slack's signature.
- In this order, before the body is parsed: a body over 1 MiB is 413; an install that does
  not exist, is pending or is removed is 404 with the body every unknown address gets. An
  install that is off answers at its events address only: a signed `app_uninstalled` or
  `tokens_revoked` is acted on, any other signed event is answered 200 and does nothing, and
  its commands and actions addresses are 404. The header `X-Slack-Request-Timestamp` more
  than 300 seconds from the server's clock, or `X-Slack-Signature` other than "v0=" and
  the HMAC-SHA256, in hexadecimal, of "v0:<timestamp>:<the body's bytes>" under that
  install's signing secret, is 401 with one fixed body. The comparison takes the same
  time whatever the difference.
- A request signed with another install's secret is 401. A body whose team is not the
  install's is refused after its signature holds, and nothing is done.
- A request whose signature holds is never refused because of requests whose signature did
  not, at its install or any other, from its address or any other. Failures are counted
  per install and client address: once one install had 20 from one address in a minute, a
  further request from it that fails is answered 429, until the oldest leaves the minute.
  The signature is computed for every request that comes that far.
- Slack's challenge (`"type": "url_verification"`) is answered with its `challenge`, as
  text, only when its signature holds; the first one answered turns an install that is
  ready into one that is on.
- An event is done once: one whose `event_id` was seen is answered 200 and nothing happens
  again, whatever `x-slack-retry-num` says. Ids are kept for a day. An event whose handler
  failed is answered 500 and its id forgotten, so Slack's next delivery runs it again;
  `app_uninstalled` and `tokens_revoked` too.
- What the handlers send to Slack goes through one client, made when the app starts and
  closed when it stops.
- A refusal writes nothing, and no log line or stored field holds the body, a secret or
  the signature: the access log names the action (`slack_event`, `slack_command`,
  `slack_action`), the install and the outcome.
- What a request costs is counted in statements, never time: verifying one runs the same
  number of statements in a workspace of 8 pages as in one of 48.

What is stored (schema step 29)
- An install: its workspace (one install for each), its state (pending, ready, on, off,
  removed), the Slack team it belongs to, and its signing secret and bot token sealed with
  AES-GCM under the key and bound to the install and the workspace, so that a copy of the
  database opens neither and a sealed value moved to another install does not open.
- The links between Slack users and people, the one-time offers of a link (kept as
  digests), the ids of events seen, and for every note that came through an install: which
  install, how (a filed message with its conversation and time, or a typed note), who
  brought it and when.
- An export holds none of it. A deleted workspace leaves no row. A removed person's links
  go. A deleted project is no longer anyone's Slack project. A forgotten note's record
  goes with it. Offers past their time and event ids older than a day are swept.

When the app goes
- Slack's `app_uninstalled`, or the owner removing the install: the sealed secrets and the
  links are deleted at once, the install is removed, and every note that came through it
  is due to be forgotten 10 days later, with cairn's own forgetting (H5b). Not before: on
  day 9 they are all there. "Forget them now" does it at once. Making the same Slack team's
  install ready again before the day cancels it.
- `tokens_revoked` naming the bot switches the install off and deletes its bot token; the
  signing secret stays, sealed, so that what Slack sends next can still be verified. No
  note is due to be forgotten. `app_uninstalled` after it, or before it, ends in the same
  state as `app_uninstalled` alone: both secrets and the links deleted, the install
  removed, the notes due. A `tokens_revoked` that arrives after the install is removed is
  404 like any request to a removed install, and changes nothing.
"""

import base64
import hashlib
import hmac
import io
import json
import logging
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

import cairn.slack.verify as verify
from cairn import server
from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.slack import dispatch
from cairn.store import Claim, Store
from cairn.web import slack as web_slack
from cairn.web.slack import KEY_VARIABLE, Keeper, Slack

BASE = "https://cairn.example.test"
KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))
KEY_B64 = base64.b64encode(KEY).decode()
ISSUER = "https://sso.example.test"
SIGN_IN_ENV = {"CAIRN_GITHUB_CLIENT_ID": "gh-id", "CAIRN_GITHUB_CLIENT_SECRET": "gh-secret"}
# Made-up values in the shapes Slack gives, built here so no file holds one whole.
SECRET = "8f" + "e1c0ffee" * 3 + "a1b2c3"  # Fable Works' signing secret
OTHER_SECRET = "41" + "beadfeed" * 3 + "d4e5f6"  # Quill Yard's
TOKEN = "xoxb-" + "1111-2222-" + "FableBotToken0123"
OTHER_TOKEN = "xoxb-" + "3333-4444-" + "QuillBotToken4567"
TEAM, OTHER_TEAM = "T0FABLE01", "T0QUILL01"
BOT = "U0FABLEBOT"
ADA_SLACK, BROOK_SLACK = "U0ADA0001", "U0BROOK01"
CONVERSATION = "C0LIBRARY1"
START = datetime(2031, 3, 4, 9, 0, tzinfo=UTC)
NO_PAGE = "There is no page at this address."


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def stamp(self) -> int:
        return int(self.now.timestamp())


def signed(secret: str, body: bytes, at: int) -> dict[str, str]:
    """What Slack sends beside a body (docs/slack-api.md, Verifying and acknowledging)."""
    base = b"v0:" + str(at).encode() + b":" + body
    digest = hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": str(at), "X-Slack-Signature": f"v0={digest}"}


def tampered(headers: dict[str, str]) -> dict[str, str]:
    """The same headers with one bit of the signature's decoded bytes turned."""
    raw = bytearray(bytes.fromhex(headers["X-Slack-Signature"].removeprefix("v0=")))
    raw[0] ^= 1
    return {**headers, "X-Slack-Signature": "v0=" + raw.hex()}


def command(team: str = TEAM, user: str = ADA_SLACK, text: str = "help") -> bytes:
    return urlencode(
        {
            "team_id": team, "team_domain": "fable-works", "channel_id": CONVERSATION,
            "user_id": user, "command": "/cairn", "text": text,
            "response_url": "https://hooks.slack.com/commands/T0FABLE01/1/fable",
            "trigger_id": "13345.0042.fable",
        }
    ).encode()  # fmt: skip


def action(team: str = TEAM, user: str = ADA_SLACK) -> bytes:
    payload = {
        "type": "message_action", "callback_id": "fable_unregistered",
        "team": {"id": team, "domain": "fable-works"}, "user": {"id": user},
        "channel": {"id": CONVERSATION}, "message": {"ts": "1900000000.000100", "text": "hi"},
        "response_url": "https://hooks.slack.com/actions/T0FABLE01/1/fable",
        "trigger_id": "13345.0043.fable",
    }  # fmt: skip
    return urlencode({"payload": json.dumps(payload)}).encode()


def event(kind: str, event_id: str, team: str = TEAM, **fields) -> bytes:
    return json.dumps(
        {
            "type": "event_callback", "team_id": team, "api_app_id": "A0FABLE01",
            "event": {"type": kind, **fields}, "event_id": event_id,
            "event_time": 1_900_000_000,
        }
    ).encode()  # fmt: skip


def challenge(value: str = "fable-challenge-0123456789") -> bytes:
    return json.dumps({"type": "url_verification", "challenge": value, "token": "unused"}).encode()


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def rows(db: Path, sql: str, *args) -> list[tuple]:
    con = sqlite3.connect(db)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


SLACK_TABLES = (
    "slack_installs",
    "slack_people",
    "slack_link_offers",
    "slack_events",
    "slack_notes",
)


@dataclass
class Person:
    id: str
    account_id: str
    token: str
    view: Store


def no_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))


class World:
    """A server (switch on or off) on a database with one workspace, Fable Works, owned by
    Ada, with Brook as a member; every clock is `clock`."""

    def __init__(self, tmp_path: Path, slack: bool = True, http=None) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.db = tmp_path / "cairn.db"
        self.clock = Clock()
        self.store = Store(self.db, clock=self.clock)
        sign_in = SignIn(
            providers=(GitHub("gh-id", "gh-secret"),),
            http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
            join="personal",
        )
        # the app first: a view copies the key create_app sets, as it does Google's custody
        self.app = create_app(
            self.store,
            public_url=BASE,
            sign_in=sign_in,
            clock=self.clock,
            slack=Slack(key=KEY, http=http) if slack else None,
        )
        self.client = TestClient(self.app, base_url=BASE)
        self.ada = self.person("Ada", "personal")
        brook = self.store.sign_in(
            "sso", "sub-brook", "brook@example.test", "Brook", issuer=ISSUER, join="default"
        )
        invite = self.ada.view.invite("brook@example.test")
        self.store.accept_invite(invite.token, brook.user.id)
        self.brook = Person(
            brook.user.id,
            self.ada.account_id,
            self.store.for_account(self.ada.account_id, brook.user.id).create_session(),
            self.store.for_account(self.ada.account_id, brook.user.id),
        )
        self.project = self.ada.view.create_project("library")

    def person(self, name: str, join: str) -> Person:
        made = self.store.sign_in(
            "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER, join=join
        )
        return Person(
            made.user.id,
            made.account_id,
            made.token,
            self.store.for_account(made.account_id, made.user.id),
        )

    def install(self, secret: str = SECRET, token: str = TOKEN, team: str = TEAM, view=None):
        view = view or self.ada.view
        made = view.make_slack_install()
        return view.ready_slack_install(
            made.id, signing_secret=secret, bot_token=token, team_id=team,
            team_domain="fable-works", bot_user_id=BOT,
        )  # fmt: skip

    def post(self, install: str, kind: str, body: bytes, headers: dict | None = None, **options):
        if headers is None:
            secret = options.pop("secret", SECRET)
            headers = signed(secret, body, options.pop("at", self.clock.stamp()))
        content_type = (
            "application/json" if kind == "events" else ("application/x-www-form-urlencoded")
        )
        return self.client.post(
            f"/integrations/slack/{install}/{kind}",
            content=body,
            headers={"content-type": content_type, **headers},
            **options,
        )

    def unknown_page(self) -> httpx.Response:
        return self.client.post("/integrations/nothing/here", content=b"x")

    def note(self, text: str, title: str) -> str:
        note, _ = self.ada.view.add_note(self.project.id, text, title=title)
        return note.id

    def keeper(self) -> Keeper:
        return Keeper(self.store)


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path)
    yield made
    made.store.close()


@pytest.fixture
def off(tmp_path):
    made = World(tmp_path, slack=False)
    yield made
    made.store.close()


def the_state(world: World, install: str) -> tuple:
    """What an install's going leaves: its state, whether it holds secrets, its links and the
    notes due to be forgotten (with when)."""
    found = world.ada.view.get_slack_install()
    links = rows(world.db, "SELECT COUNT(*) FROM slack_people WHERE install_id = ?", install)
    due = rows(
        world.db,
        "SELECT note_id, forget_at FROM slack_notes WHERE install_id = ? ORDER BY note_id",
        install,
    )
    return found.state, found.holds_secrets, links, due


# The switch


def test_off_none_of_the_addresses_exists(off):
    unknown = off.unknown_page()
    assert unknown.status_code == 404
    for kind, body in (("events", challenge()), ("commands", command()), ("actions", action())):
        answer = off.post("sl_0123456789ab", kind, body)
        assert answer.status_code == 404 and answer.content == unknown.content
        assert NO_PAGE in answer.text


def test_off_the_sources_page_shows_nothing_about_slack(off):
    page = off.client.get("/sources", headers={"cookie": f"cairn_session={off.ada.token}"})
    assert page.status_code == 200 and "slack" not in page.text.lower()


def test_off_no_request_reads_the_slack_tables(off):
    asked: list[str] = []
    off.store._con.set_trace_callback(asked.append)
    try:
        cookie = {"cookie": f"cairn_session={off.ada.token}"}
        for path in ("/sources", "/", "/b/library", "/healthz"):
            off.client.get(path, headers=cookie)
        off.post("sl_0123456789ab", "commands", command())
        off.post("sl_0123456789ab", "events", challenge())
    finally:
        off.store._con.set_trace_callback(None)
    assert asked and not [line for line in asked if "slack_" in line]


def serve(monkeypatch, tmp_path, argv, env):
    for name in (KEY_VARIABLE, *SIGN_IN_ENV):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    db = tmp_path / "data" / "cairn.db"
    return server.main(["serve", "--db", str(db), *argv]), apps, db


def test_serve_slack_starts_with_sign_in_an_https_public_url_and_the_key(
    tmp_path, monkeypatch, capsys
):
    argv = ["--public-url", BASE, "--sign-in", "github", "--slack"]
    code, apps, _ = serve(monkeypatch, tmp_path, argv, {**SIGN_IN_ENV, KEY_VARIABLE: KEY_B64})
    assert code == 0 and len(apps) == 1
    said = capsys.readouterr()
    assert KEY_B64 not in said.out + said.err


@pytest.mark.parametrize(
    "argv, env, named",
    [
        (["--public-url", BASE], {KEY_VARIABLE: KEY_B64}, "--sign-in"),
        (
            ["--public-url", "http://cairn.example.test", "--sign-in", "github"],
            {**SIGN_IN_ENV, KEY_VARIABLE: KEY_B64},
            "https://",
        ),
        (["--sign-in", "github"], {**SIGN_IN_ENV, KEY_VARIABLE: KEY_B64}, "--public-url"),
        (["--public-url", BASE, "--sign-in", "github"], SIGN_IN_ENV, KEY_VARIABLE),
        (
            ["--public-url", BASE, "--sign-in", "github"],
            {**SIGN_IN_ENV, KEY_VARIABLE: base64.b64encode(bytes(16)).decode()},
            KEY_VARIABLE,
        ),
    ],
)
def test_serve_slack_stops_before_the_database_naming_what_is_missing(
    tmp_path, monkeypatch, capsys, argv, env, named
):
    code, apps, db = serve(monkeypatch, tmp_path, [*argv, "--slack"], env)
    assert code == 2 and apps == [] and not db.parent.exists()
    last = capsys.readouterr().err.strip().splitlines()[-1]
    assert named in last and KEY_B64 not in last


def test_create_app_raises_the_same_sentences(tmp_path, monkeypatch, capsys):
    store = Store(tmp_path / "cairn.db")
    sign_in = SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=no_http(), join="personal")
    short = base64.b64encode(bytes(16)).decode()
    cases = [
        (
            {"public_url": BASE, "slack": Slack(key=KEY)},
            ["--public-url", BASE],
            {KEY_VARIABLE: KEY_B64},
        ),
        (
            {"public_url": "http://cairn.example.test", "sign_in": sign_in, "slack": Slack(KEY)},
            ["--public-url", "http://cairn.example.test", "--sign-in", "github"],
            {**SIGN_IN_ENV, KEY_VARIABLE: KEY_B64},
        ),
        (
            {"public_url": BASE, "sign_in": sign_in, "slack": Slack(key=bytes(16))},
            ["--public-url", BASE, "--sign-in", "github"],
            {**SIGN_IN_ENV, KEY_VARIABLE: short},
        ),
    ]
    try:
        for n, (options, argv, env) in enumerate(cases):
            with pytest.raises(ValueError) as raised:
                create_app(store, **options)
            code, _, _ = serve(monkeypatch, tmp_path / str(n), [*argv, "--slack"], env)
            assert code == 2
            assert str(raised.value) in capsys.readouterr().err.strip().splitlines()[-1]
    finally:
        store.close()


BRANDED = ["--public-url", BASE, "--sign-in", "github", "--brand-name", "Fable Notes"]


def test_serve_slack_under_a_brand_stops_before_the_database(tmp_path, monkeypatch, capsys):
    env = {**SIGN_IN_ENV, KEY_VARIABLE: KEY_B64}
    code, apps, db = serve(monkeypatch, tmp_path, [*BRANDED, "--slack"], env)
    assert code == 2 and apps == [] and not db.parent.exists()
    last = capsys.readouterr().err.strip().splitlines()[-1]
    assert "Slack is not offered under a brand yet" in last and KEY_B64 not in last
    code, apps, _ = serve(monkeypatch, tmp_path / "plain", BRANDED, env)  # without --slack
    assert code == 0 and len(apps) == 1


def test_create_app_under_a_brand_raises_the_same_sentence(tmp_path, monkeypatch, capsys):
    store = Store(tmp_path / "cairn.db")
    sign_in = SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=no_http(), join="personal")
    try:
        with pytest.raises(ValueError) as raised:
            create_app(
                store, public_url=BASE, sign_in=sign_in, brand_name="Fable Notes",
                slack=Slack(key=KEY),
            )  # fmt: skip
        env = {**SIGN_IN_ENV, KEY_VARIABLE: KEY_B64}
        assert serve(monkeypatch, tmp_path / "served", [*BRANDED, "--slack"], env)[0] == 2
        assert str(raised.value) in capsys.readouterr().err.strip().splitlines()[-1]
        assert "Slack is not offered under a brand yet" in str(raised.value)
    finally:
        store.close()


# The three addresses Slack calls


def test_an_install_id_is_sl_and_12_hex_and_its_addresses_take_no_session(world):
    install = world.install()
    assert install.id.startswith("sl_") and len(install.id) == 15
    assert all(c in "0123456789abcdef" for c in install.id[3:])
    for kind, body in (("commands", command()), ("actions", action())):
        answer = world.post(install.id, kind, body)  # no cookie, no key
        assert answer.status_code == 200


def test_a_body_over_1_mib_is_413_before_anything_else(world):
    install = world.install()
    big = b"x" * (1024 * 1024 + 1)
    for target in (install.id, "sl_000000000000"):
        answer = world.post(target, "commands", big, headers={"x-slack-signature": "v0=00"})
        assert answer.status_code == 413
    exactly = command() + b"&pad=" + b"y" * (1024 * 1024 - len(command()) - 5)
    assert len(exactly) == 1024 * 1024
    assert world.post(install.id, "commands", exactly).status_code == 200


def test_an_install_that_is_unknown_pending_or_removed_is_404_as_an_unknown_address(world):
    unknown = world.unknown_page()
    assert unknown.status_code == 404
    pending = world.ada.view.make_slack_install()
    answers = [world.post("sl_000000000000", "commands", command())]
    answers.append(world.post(pending.id, "commands", command()))
    answers.append(world.post(pending.id, "events", event("fable_ping", "Ev0PENDING1")))
    world.install()
    world.ada.view.switch_slack_install(pending.id, on=False)
    answers.append(world.post(pending.id, "commands", command()))  # off: commands and actions
    answers.append(world.post(pending.id, "actions", action()))
    world.ada.view.remove_slack_install(pending.id)
    for kind, body in (
        ("commands", command()),
        ("actions", action()),
        ("events", event("fable_ping", "Ev0REMOVED1")),
    ):
        answers.append(world.post(pending.id, kind, body))
    for answer in answers:
        assert answer.status_code == 404 and answer.content == unknown.content
    # before the signature: an unknown install with a wrong signature is 404, not 401
    bad = tampered(signed(SECRET, command(), world.clock.stamp()))
    assert world.post("sl_000000000000", "commands", command(), headers=bad).status_code == 404


def test_an_install_that_is_off_answers_at_its_events_address_only(world):
    install = world.install()
    world.post(install.id, "events", challenge())
    seen = []

    @dispatch.on_event("fable_ping")
    async def ping(envelope, context):
        seen.append(envelope.event_id)

    try:
        world.ada.view.switch_slack_install(install.id, on=False)
        unknown = world.unknown_page()
        before = dump(world.db)
        for kind, body in (("commands", command()), ("actions", action())):
            answer = world.post(install.id, kind, body)
            assert answer.status_code == 404 and answer.content == unknown.content
        answer = world.post(install.id, "events", event("fable_ping", "Ev0OFFPING1"))
        assert answer.status_code == 200 and answer.content == b""
        assert world.post(install.id, "events", challenge()).status_code == 200
        bad = tampered(signed(SECRET, event("fable_ping", "Ev0OFFPING2"), world.clock.stamp()))
        answer = world.post(install.id, "events", event("fable_ping", "Ev0OFFPING2"), bad)
        assert answer.status_code == 401  # still verified
        assert dump(world.db) == before and seen == []  # nothing done
        assert world.ada.view.get_slack_install().state == "off"
        answer = world.post(install.id, "events", event("app_uninstalled", "Ev0OFFGONE1"))
        assert answer.status_code == 200
        assert world.ada.view.get_slack_install().state == "removed"
    finally:
        dispatch.unregister("event", "fable_ping")


def test_the_timestamp_more_than_300_seconds_away_is_401(world):
    install = world.install()
    now = world.clock.stamp()
    refused = []
    for at in (now - 301, now + 301):
        answer = world.post(install.id, "commands", command(), at=at)
        assert answer.status_code == 401
        refused.append(answer.content)
    for at in (now - 300, now + 300):
        assert world.post(install.id, "commands", command(), at=at).status_code == 200
    headers = signed(SECRET, command(), now)
    for broken in ("", "soon", "1e9", str(now) + ".5"):
        answer = world.post(
            install.id, "commands", command(), {**headers, "X-Slack-Request-Timestamp": broken}
        )
        assert answer.status_code == 401
        refused.append(answer.content)
    assert len(set(refused)) == 1  # one fixed body


def test_a_signature_other_than_slacks_is_401_with_the_fixed_body(world):
    install = world.install()
    good = signed(SECRET, command(), world.clock.stamp())
    fixed = world.post(install.id, "commands", command(), tampered(good))
    assert fixed.status_code == 401
    cases = [
        {k: v for k, v in good.items() if k != "X-Slack-Signature"},
        {**good, "X-Slack-Signature": good["X-Slack-Signature"].replace("v0=", "v1=")},
        {**good, "X-Slack-Signature": good["X-Slack-Signature"].upper()},
        {**good, "X-Slack-Signature": ("v0=é" + "0" * 63).encode()},  # not ASCII
        signed(SECRET, command(text="find other"), world.clock.stamp()),  # another body's
    ]
    for headers in cases:
        answer = world.post(install.id, "commands", command(), headers)
        assert answer.status_code == 401 and answer.content == fixed.content


def test_the_comparison_takes_the_same_time_whatever_the_difference(world, monkeypatch):
    install = world.install()
    compared = []
    real = verify.hmac.compare_digest

    def spying(a, b):
        compared.append((type(a), type(b), len(a) == len(b)))
        return real(a, b)

    monkeypatch.setattr(verify.hmac, "compare_digest", spying)
    good = signed(SECRET, command(), world.clock.stamp())
    assert world.post(install.id, "commands", command(), good).status_code == 200
    assert world.post(install.id, "commands", command(), tampered(good)).status_code == 401
    assert compared == [(bytes, bytes, True)] * 2


def test_a_request_signed_with_another_installs_secret_is_401(world, tmp_path):
    install = world.install()
    quill = world.person("Quill", "personal")
    world.install(OTHER_SECRET, OTHER_TOKEN, OTHER_TEAM, view=quill.view)
    answer = world.post(install.id, "commands", command(), secret=OTHER_SECRET)
    assert answer.status_code == 401


def test_a_body_whose_team_is_not_the_installs_is_refused_and_nothing_is_done(world):
    install = world.install()
    seen = []

    @dispatch.on_event("fable_ping")
    async def ping(envelope, context):
        seen.append(envelope.event_id)

    try:
        before = dump(world.db)
        for kind, body in (
            ("commands", command(team=OTHER_TEAM)),
            ("actions", action(team=OTHER_TEAM)),
            ("events", event("fable_ping", "Ev0FOREIGN1", team=OTHER_TEAM)),
            ("events", event("app_uninstalled", "Ev0FOREIGN2", team=OTHER_TEAM)),
        ):
            answer = world.post(install.id, kind, body)
            assert answer.status_code >= 400
        assert dump(world.db) == before and seen == []
    finally:
        dispatch.unregister("event", "fable_ping")


def test_20_failures_in_a_minute_at_one_install_from_one_address_make_the_next_failure_429(
    world, caplog
):
    caplog.set_level(logging.INFO, logger="cairn.access")
    install = world.install()
    quill = world.person("Quill", "personal")
    theirs = world.install(OTHER_SECRET, OTHER_TOKEN, OTHER_TEAM, view=quill.view)
    bad = tampered(signed(SECRET, command(), world.clock.stamp()))
    for _ in range(20):
        assert world.post(install.id, "commands", command(), bad).status_code == 401
    before = dump(world.db)
    refused = world.post(install.id, "commands", command(), bad)
    assert refused.status_code == 429 and refused.text == "Too many unsigned requests came" \
        " from your address in the last minute."  # fmt: skip
    assert refused.headers["retry-after"] == "60"
    assert dump(world.db) == before  # a refusal writes nothing
    said = [(r.action, r.install, r.status) for r in caplog.records if r.name == "cairn.access"]
    assert said[-1] == ("slack_command", install.id, 429)
    # another install from the same address, and the same install from another address
    theirs_bad = tampered(signed(OTHER_SECRET, command(team=OTHER_TEAM), world.clock.stamp()))
    assert world.post(theirs.id, "commands", command(team=OTHER_TEAM), theirs_bad).status_code \
        == 401  # fmt: skip
    elsewhere = from_address(world, "198.51.100.7")
    answer = elsewhere.post(
        f"/integrations/slack/{install.id}/commands", content=command(),
        headers={"content-type": "application/x-www-form-urlencoded", **bad},
    )  # fmt: skip
    assert answer.status_code == 401
    # until the oldest failure leaves the window
    world.clock.now += timedelta(seconds=59)
    bad = tampered(signed(SECRET, command(), world.clock.stamp()))
    answer = world.post(install.id, "commands", command(), bad)
    assert answer.status_code == 429 and answer.headers["retry-after"] == "1"
    world.clock.now += timedelta(seconds=1)
    bad = tampered(signed(SECRET, command(), world.clock.stamp()))
    assert world.post(install.id, "commands", command(), bad).status_code == 401


def test_the_signature_is_computed_for_every_request_that_reaches_it(world, monkeypatch):
    install = world.install()
    good = signed(SECRET, command(), world.clock.stamp())
    for _ in range(20):
        assert world.post(install.id, "commands", command(), tampered(good)).status_code == 401
    computed = []
    real = verify.hmac.new
    monkeypatch.setattr(verify.hmac, "new", lambda *a, **k: computed.append(1) or real(*a, **k))
    assert world.post(install.id, "commands", command(), tampered(good)).status_code == 429
    assert computed == [1]
    assert world.post(install.id, "commands", command(), good).status_code == 200
    assert computed == [1, 1]
    monkeypatch.undo()


def test_an_install_that_does_not_exist_is_404_before_anything_is_counted(world, monkeypatch):
    counted = []
    real = web_slack.SignatureFailures.failed
    monkeypatch.setattr(
        web_slack.SignatureFailures, "failed",
        lambda self, *a: counted.append(a) or real(self, *a),
    )  # fmt: skip
    pending = world.ada.view.make_slack_install()
    bad = tampered(signed(SECRET, command(), world.clock.stamp()))
    for target in ("sl_000000000000", pending.id):
        assert world.post(target, "commands", command(), bad).status_code == 404
    assert counted == []
    install = world.install()
    assert world.post(install.id, "commands", command(), bad).status_code == 401
    assert [install_id for install_id, _ in counted] == [install.id]


def test_a_request_from_another_address_is_answered_as_if_no_failure_was_counted(world):
    install = world.install()
    bad = tampered(signed(SECRET, command(), world.clock.stamp()))
    for _ in range(25):
        world.post(install.id, "commands", command(), bad)
    elsewhere = from_address(world, "198.51.100.7")
    answer = elsewhere.post(
        f"/integrations/slack/{install.id}/commands", content=command(),
        headers={"content-type": "application/x-www-form-urlencoded",
                 **signed(SECRET, command(), world.clock.stamp())},
    )  # fmt: skip
    assert answer.status_code == 200


def from_address(world: World, host: str) -> TestClient:
    """A client of `world`'s server whose requests come from `host`."""
    return TestClient(world.app, base_url=BASE, client=(host, 50_000))


def test_a_signed_request_is_never_refused_because_of_failures_counted_anywhere(world):
    """The review's lockout probe: Quill's install holds a signing secret that is not its
    app's, so 20 of Slack's requests to it fail; Slack's signed requests from the same address
    are still answered, at Fable Works' install and at Quill's own."""
    fable = world.install()
    quill = world.person("Quill", "personal")
    held = "wrong" + OTHER_SECRET[5:]
    theirs = world.install(held, OTHER_TOKEN, OTHER_TEAM, view=quill.view)
    for _ in range(20):
        answer = world.post(theirs.id, "commands", command(team=OTHER_TEAM), secret=OTHER_SECRET)
        assert answer.status_code == 401
    assert world.post(fable.id, "commands", command()).status_code == 200
    answer = world.post(theirs.id, "commands", command(team=OTHER_TEAM), secret=held)
    assert answer.status_code == 200
    for _ in range(5):  # and after more failures, at either install, as often as it comes
        world.post(fable.id, "commands", command(), secret=OTHER_SECRET)
        world.post(theirs.id, "commands", command(team=OTHER_TEAM), secret=OTHER_SECRET)
        assert world.post(fable.id, "commands", command()).status_code == 200


def test_the_challenge_is_answered_only_when_signed_and_turns_ready_into_on(world):
    install = world.install()
    assert world.ada.view.get_slack_install().state == "ready"
    unsigned = world.post(install.id, "events", challenge(), headers={})
    assert unsigned.status_code == 401 and "fable-challenge" not in unsigned.text
    assert world.ada.view.get_slack_install().state == "ready"
    answer = world.post(install.id, "events", challenge())
    assert answer.status_code == 200 and answer.text == "fable-challenge-0123456789"
    assert answer.headers["content-type"].startswith("text/plain")
    assert world.ada.view.get_slack_install().state == "on"
    again = world.post(install.id, "events", challenge("fable-again"))
    assert again.text == "fable-again" and world.ada.view.get_slack_install().state == "on"


def test_an_event_is_done_once_whatever_the_retry_number_says_and_ids_are_kept_a_day(world):
    install = world.install()
    seen = []

    @dispatch.on_event("fable_ping")
    async def ping(envelope, context):
        seen.append((envelope.event_id, envelope.retry))

    try:
        body = event("fable_ping", "Ev0ONCE0001")
        assert world.post(install.id, "events", body).status_code == 200
        for retry in ("1", "2", "3"):
            headers = {**signed(SECRET, body, world.clock.stamp()), "x-slack-retry-num": retry}
            assert world.post(install.id, "events", body, headers).status_code == 200
        assert seen == [("Ev0ONCE0001", None)]
        world.clock.now += timedelta(hours=23)
        world.keeper().run_once()
        assert world.post(install.id, "events", body).status_code == 200 and len(seen) == 1
        world.clock.now += timedelta(hours=1, seconds=1)
        world.keeper().run_once()
        assert rows(world.db, "SELECT COUNT(*) FROM slack_events") == [(0,)]
        assert world.post(install.id, "events", body).status_code == 200 and len(seen) == 2
    finally:
        dispatch.unregister("event", "fable_ping")


def test_an_event_whose_handler_failed_runs_again_at_slacks_next_delivery(world):
    install = world.install()
    ran, recorded = [], []

    @dispatch.on_event("fable_ping")
    async def ping(envelope, context):
        # recorded before it runs: a second delivery now would be answered 200 and not run
        recorded.append(not world.store.slack_event_seen(install.id, envelope.event_id))
        ran.append(envelope.event_id)
        if len(ran) == 1:
            raise RuntimeError("the store is busy")

    try:
        body = event("fable_ping", "Ev0FAILS001")
        assert world.post(install.id, "events", body).status_code == 500
        headers = {**signed(SECRET, body, world.clock.stamp()), "x-slack-retry-num": "1"}
        assert world.post(install.id, "events", body, headers).status_code == 200
        assert ran == ["Ev0FAILS001"] * 2 and recorded == [True, True]
        headers = {**signed(SECRET, body, world.clock.stamp()), "x-slack-retry-num": "2"}
        assert world.post(install.id, "events", body, headers).status_code == 200
        assert len(ran) == 2  # a handler that returned is not run again
    finally:
        dispatch.unregister("event", "fable_ping")


def failing_once(monkeypatch, name: str) -> list[int]:
    """Store.<name> raises the first time it is called, and works after."""
    calls = []
    real = getattr(Store, name)

    def once(self, *args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Store, name, once)
    return calls


def test_app_uninstalled_whose_handler_failed_is_done_at_the_next_delivery(world, monkeypatch):
    install = world.install()
    world.post(install.id, "events", challenge())
    calls = failing_once(monkeypatch, "remove_slack_install")
    body = event("app_uninstalled", "Ev0GONEFAIL")
    assert world.post(install.id, "events", body).status_code == 500
    assert world.ada.view.get_slack_install().state == "on"
    assert world.post(install.id, "events", body).status_code == 200
    assert world.ada.view.get_slack_install().state == "removed" and len(calls) == 2


def test_tokens_revoked_whose_handler_failed_is_done_at_the_next_delivery(world, monkeypatch):
    install = world.install()
    world.post(install.id, "events", challenge())
    calls = failing_once(monkeypatch, "revoke_slack_bot")
    body = event("tokens_revoked", "Ev0BOTFAIL1", **revoked())
    assert world.post(install.id, "events", body).status_code == 500
    assert world.ada.view.get_slack_install().state == "on"
    assert world.post(install.id, "events", body).status_code == 200
    assert world.ada.view.get_slack_install().state == "off" and len(calls) == 2
    assert world.post(install.id, "events", body).status_code == 200 and len(calls) == 2


def test_a_failed_event_whose_id_cannot_be_forgotten_is_still_500_and_logged_by_class(
    world, monkeypatch, caplog
):
    install = world.install()
    failing_once(monkeypatch, "remove_slack_install")
    failing_once(monkeypatch, "forget_slack_event")
    caplog.set_level(logging.WARNING, logger="cairn.slack")
    body = event("app_uninstalled", "Ev0NOFORGET")
    assert world.post(install.id, "events", body).status_code == 500
    said = [r.getMessage() for r in caplog.records if r.name == "cairn.slack"]
    assert said == [
        "slack: a handler failed: OperationalError",
        "slack: a failed event was not forgotten: OperationalError",
    ]


class Made:
    """Slack's answers to every call, and each client the server made to send them."""

    def __init__(self) -> None:
        self.clients: list[httpx.AsyncClient] = []
        self.asked: list[str] = []

    def answer(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request.url.path)
        if request.url.path.endswith("/users.info"):
            cedar = {"id": "U0CEDAR01", "team_id": TEAM, "real_name": "Cedar Quill",
                     "is_bot": False, "is_stranger": False, "deleted": False,
                     "profile": {"real_name": "Cedar Quill", "display_name": "Cedar"}}  # fmt: skip
            return httpx.Response(200, json={"ok": True, "user": cedar})
        if request.url.path.endswith("/chat.postMessage"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, text="ok")

    def __call__(self) -> httpx.AsyncClient:
        made = httpx.AsyncClient(transport=httpx.MockTransport(self.answer))
        self.clients.append(made)
        return made


def shortcut(user: str = ADA_SLACK, ts: str = "1900000000.000100") -> bytes:
    payload = {
        "type": "message_action", "callback_id": "file_in_cairn",
        "team": {"id": TEAM, "domain": "fable-works"}, "user": {"id": user},
        "channel": {"id": CONVERSATION},
        "message": {"type": "message", "user": "U0CEDAR01", "ts": ts,
                    "text": "The marmalade shelf moves to row 4."},
        "response_url": "https://hooks.slack.com/actions/T0FABLE01/1/fable",
        "trigger_id": "13345.0044.fable",
    }  # fmt: skip
    return urlencode({"payload": json.dumps(payload)}).encode()


def direct(event_id: str, text: str = "marmalade") -> bytes:
    return event(
        "message", event_id, channel="D0ADA0001", channel_type="im", user=ADA_SLACK,
        text=text, ts="1900000001.000200",
    )  # fmt: skip


def everything_that_sends(world: World, install: str) -> None:
    """Commands, shortcuts and direct messages, from Ada unlinked and then linked."""
    for n in range(3):
        assert world.post(install, "commands", command(text="find marmalade")).status_code == 200
        assert world.post(install, "actions", shortcut()).status_code == 200
        assert world.post(install, "events", direct(f"Ev0UNLINKD{n}")).status_code == 200
    world.ada.view.link_slack_user(install, ADA_SLACK, world.ada.id)
    world.ada.view.set_slack_project(install, ADA_SLACK, world.project.id)
    for n in range(3):
        assert world.post(install, "commands", command(text="find marmalade")).status_code == 200
        filed = shortcut(ts=f"1900000000.00020{n}")
        assert world.post(install, "actions", filed).status_code == 200
        assert world.post(install, "events", direct(f"Ev0LINKED0{n}")).status_code == 200


def test_the_handlers_send_through_one_client_made_at_start_and_closed_at_shutdown(tmp_path):
    made = Made()
    world = World(tmp_path, http=made)
    try:
        install = world.install()
        with TestClient(world.app, base_url=BASE) as started:
            assert len(made.clients) == 1  # at start, before any request
            world.client = started
            everything_that_sends(world, install.id)
            assert len(made.clients) == 1 and not made.clients[0].is_closed
        assert made.clients[0].is_closed
        sent = set(made.asked)
        assert {"/api/users.info", "/api/chat.postMessage", "/actions/T0FABLE01/1/fable"} <= sent
    finally:
        world.store.close()


def test_without_a_start_the_handlers_still_make_one_client_for_every_call(tmp_path):
    made = Made()
    world = World(tmp_path, http=made)
    try:
        install = world.install()
        everything_that_sends(world, install.id)
        assert len(made.clients) == 1 and len(made.asked) >= 9
    finally:
        world.store.close()


def test_nothing_registered_is_acknowledged_and_does_nothing(world):
    install = world.install()
    before = dump(world.db)
    assert world.post(install.id, "commands", command(text="fable")).status_code == 200
    assert world.post(install.id, "actions", action()).status_code == 200
    after = dump(world.db)
    assert after == before
    answer = world.post(install.id, "events", event("fable_nobody", "Ev0NOBODY01"))
    assert answer.status_code == 200


def test_a_refusal_writes_nothing_and_no_log_holds_the_body_a_secret_or_the_signature(
    world, caplog
):
    caplog.set_level(logging.DEBUG)
    install = world.install()
    world.post(install.id, "events", challenge())
    good = signed(SECRET, command(text="find marmalade"), world.clock.stamp())
    before = dump(world.db)
    refusals = [
        world.post(install.id, "commands", command(text="find marmalade"), tampered(good)),
        world.post(install.id, "commands", command(text="find marmalade"), headers={}),
        world.post("sl_000000000000", "commands", command(text="find marmalade")),
        world.post(install.id, "commands", b"marmalade" * 200_000),
        world.post(install.id, "commands", command(team=OTHER_TEAM, text="find marmalade")),
    ]
    assert [r.status_code for r in refusals][:4] == [401, 401, 404, 413]
    assert dump(world.db) == before
    accepted = world.post(install.id, "commands", command(text="find marmalade"))
    assert accepted.status_code == 200
    logged = "\n".join(
        f"{record.getMessage()} {sorted(vars(record).items())}" for record in caplog.records
    )
    for secret in (
        SECRET, TOKEN, "marmalade", good["X-Slack-Signature"],
        good["X-Slack-Signature"].removeprefix("v0="), KEY_B64, KEY.hex(),
    ):  # fmt: skip
        assert secret not in logged
    records = [r for r in caplog.records if r.name == "cairn.access"]
    said = [(r.action, getattr(r, "install", None), r.status) for r in records]
    assert ("slack_command", install.id, 401) in said
    assert ("slack_command", install.id, 413) in said
    assert ("slack_command", install.id, 200) in said
    assert ("slack_event", install.id, 200) in said


def test_verifying_costs_the_same_statements_with_8_pages_as_with_48(world):
    install = world.install()

    def counted() -> int:
        asked: list[str] = []
        world.store._con.set_trace_callback(asked.append)
        try:
            assert world.post(install.id, "commands", command(text="fable")).status_code == 200
        finally:
            world.store._con.set_trace_callback(None)
        return len(asked)

    def pages(total: int) -> None:
        for n in range(total):
            claim = Claim(id=f"cl_fable{n:04d}", text=f"Fact {n}.", span_ids=())
            world.ada.view.save_page(world.project.id, f"page-{n}", f"Page {n}", "", [claim])

    pages(8)
    with_8 = counted()
    pages(40)
    assert counted() == with_8


# What is stored (schema step 29)


def test_an_install_holds_its_workspace_state_team_and_two_sealed_secrets(world, tmp_path):
    install = world.install()
    assert world.ada.view.get_slack_install().team_id == TEAM
    wal = Path(f"{world.db}-wal")
    raw = world.db.read_bytes() + (wal.read_bytes() if wal.exists() else b"")
    for secret in (SECRET, TOKEN):
        assert secret.encode() not in raw
    with pytest.raises(sqlite3.IntegrityError):  # one install a workspace
        con = sqlite3.connect(world.db)
        try:
            con.execute(
                "INSERT INTO slack_installs (id, account_id, state, command, made_at)"
                " VALUES ('sl_aaaaaaaaaaaa', ?, 'pending', '/cairn', 'x')",
                (world.ada.account_id,),
            )
        finally:
            con.close()
    # a copy of the database opens neither under another key
    copy = tmp_path / "copy.db"
    con = sqlite3.connect(world.db)
    con.execute("VACUUM INTO ?", (str(copy),))
    con.close()
    other = Store(copy)
    try:
        sign_in = SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=no_http(), join="personal")
        create_app(other, public_url=BASE, sign_in=sign_in, slack=Slack(key=OTHER_KEY))
        view = other.for_account(world.ada.account_id)
        assert view.slack_bot_token(install.id) is None
        assert other.slack_inbound(install.id).signing_secret is None
    finally:
        other.close()
    assert world.ada.view.for_account(world.ada.account_id).slack_bot_token(install.id) == TOKEN


def test_a_sealed_value_moved_to_another_install_does_not_open(world):
    install = world.install()
    quill = world.person("Quill", "personal")
    theirs = world.install(OTHER_SECRET, OTHER_TOKEN, OTHER_TEAM, view=quill.view)
    con = sqlite3.connect(world.db)
    sealed = con.execute(
        "SELECT signing_secret, bot_token FROM slack_installs WHERE id = ?", (install.id,)
    ).fetchone()
    con.execute(
        "UPDATE slack_installs SET signing_secret = ?, bot_token = ? WHERE id = ?",
        (*sealed, theirs.id),
    )
    con.commit()
    con.close()
    assert world.store.slack_inbound(theirs.id).signing_secret is None
    assert world.store.for_account(quill.account_id).slack_bot_token(theirs.id) is None
    assert world.post(theirs.id, "commands", command(team=OTHER_TEAM)).status_code == 401


def test_links_offers_events_and_notes_are_stored(world):
    install = world.install()
    view = world.ada.view
    view.link_slack_user(install.id, ADA_SLACK, world.ada.id)
    assert view.slack_person(install.id, ADA_SLACK).user_id == world.ada.id
    offer = view.offer_slack_link(install.id, BROOK_SLACK)
    assert offer not in dump(world.db)  # kept as a digest
    assert world.brook.view.slack_offer(offer).slack_user_id == BROOK_SLACK
    world.brook.view.take_slack_offer(offer)
    assert view.slack_person(install.id, BROOK_SLACK).user_id == world.brook.id
    assert world.brook.view.slack_offer(offer) is None  # used once
    world.post(install.id, "events", event("fable_nobody", "Ev0STORED01"))
    assert rows(world.db, "SELECT event_id FROM slack_events") == [("Ev0STORED01",)]
    filed = world.note("# Tides\n\nThe tide turns at nine.\n", "Tides")
    typed = world.note("# Kettles\n\nThe kettle whistles.\n", "Kettles")
    view.record_slack_note(
        install.id, filed, how="filed", brought_by=world.ada.id,
        conversation_id=CONVERSATION, message_time="1900000000.000100",
    )  # fmt: skip
    view.record_slack_note(install.id, typed, how="typed", brought_by=world.brook.id)
    assert view.slack_note_for(install.id, CONVERSATION, "1900000000.000100") == filed
    assert view.slack_note_for(install.id, CONVERSATION, "1900000000.000200") is None
    held = rows(
        world.db,
        "SELECT note_id, install_id, how, conversation_id, message_time, brought_by, brought_at"
        " FROM slack_notes ORDER BY how",
    )
    stamp = START.isoformat(timespec="microseconds")
    assert held == [
        (filed, install.id, "filed", CONVERSATION, "1900000000.000100", world.ada.id, stamp),
        (typed, install.id, "typed", None, None, world.brook.id, stamp),
    ]


def test_an_offer_works_once_for_10_minutes(world):
    install = world.install()
    offer = world.ada.view.offer_slack_link(install.id, BROOK_SLACK)
    world.clock.now += timedelta(minutes=10, seconds=1)
    assert world.brook.view.slack_offer(offer) is None
    with pytest.raises(KeyError):
        world.brook.view.take_slack_offer(offer)
    world.keeper().run_once()
    assert rows(world.db, "SELECT COUNT(*) FROM slack_link_offers") == [(0,)]


def filled(world: World) -> tuple:
    install = world.install()
    view = world.ada.view
    view.link_slack_user(install.id, ADA_SLACK, world.ada.id)
    view.link_slack_user(install.id, BROOK_SLACK, world.brook.id)
    view.set_slack_project(install.id, BROOK_SLACK, world.project.id)
    view.offer_slack_link(install.id, "U0CAROL01")
    world.post(install.id, "events", event("fable_nobody", "Ev0FILLED01"))
    note = world.note("# Tides\n\nThe tide turns at nine.\n", "Tides")
    view.record_slack_note(
        install.id, note, how="filed", brought_by=world.ada.id,
        conversation_id=CONVERSATION, message_time="1900000000.000100",
    )  # fmt: skip
    return install, note


def test_an_export_holds_none_of_it(world):
    filled(world)
    out = io.BytesIO()
    world.ada.view.export(out)
    with zipfile.ZipFile(out) as archive:
        held = b"".join(archive.read(name) for name in archive.namelist())
    for value in (TEAM, ADA_SLACK, BROOK_SLACK, CONVERSATION, "1900000000.000100", "Ev0FILLED01"):
        assert value.encode() not in held
    assert b"The tide turns at nine." in held  # the note itself is the workspace's
    import cairn.store.portable as portable

    assert "slack" in portable.__doc__.lower()


def test_a_deleted_workspace_leaves_no_row(world):
    filled(world)
    world.store.delete_account(world.ada.account_id)
    for table in SLACK_TABLES:
        assert rows(world.db, f"SELECT COUNT(*) FROM {table}") == [(0,)], table


def test_a_removed_persons_links_go(world):
    install, _ = filled(world)
    world.ada.view.remove_member(world.brook.id)
    assert world.ada.view.slack_person(install.id, BROOK_SLACK) is None
    assert world.ada.view.slack_person(install.id, ADA_SLACK).user_id == world.ada.id


def test_a_deleted_project_is_no_longer_anyones_slack_project(world):
    install, _ = filled(world)
    assert world.ada.view.slack_person(install.id, BROOK_SLACK).project_id == world.project.id
    world.ada.view.delete_project(world.project.id)
    linked = world.ada.view.slack_person(install.id, BROOK_SLACK)
    assert linked.user_id == world.brook.id and linked.project_id is None
    assert rows(world.db, "SELECT COUNT(*) FROM slack_notes") == [(0,)]


def test_a_forgotten_notes_record_goes_with_it(world):
    install, note = filled(world)
    world.ada.view.forget_note(note)
    assert rows(world.db, "SELECT COUNT(*) FROM slack_notes") == [(0,)]
    assert world.ada.view.slack_note_for(install.id, CONVERSATION, "1900000000.000100") is None


# When the app goes


def gone_at_once(world: World, install: str) -> None:
    found = world.ada.view.get_slack_install()
    assert found.state == "removed" and not found.holds_secrets
    held = rows(world.db, "SELECT signing_secret, bot_token FROM slack_installs WHERE id = ?",
                install)  # fmt: skip
    assert held == [(None, None)]
    assert rows(world.db, "SELECT COUNT(*) FROM slack_people") == [(0,)]


def notes_through(world: World, install: str) -> list[str]:
    notes = []
    for n, word in enumerate(("Tides", "Kettles", "Lanterns")):
        note = world.note(f"# {word}\n\nThe {word.lower()} story {n}.\n", word)
        world.ada.view.record_slack_note(install, note, how="typed", brought_by=world.ada.id)
        notes.append(note)
    return notes


def held(world: World, notes: list[str]) -> list[bool]:
    return [world.ada.view.get_note(note) is not None for note in notes]


@pytest.mark.parametrize("how", ["app_uninstalled", "the owner"])
def test_when_the_app_goes_secrets_and_links_go_at_once_and_notes_10_days_later(world, how):
    install = world.install()
    world.post(install.id, "events", challenge())
    world.ada.view.link_slack_user(install.id, ADA_SLACK, world.ada.id)
    notes = notes_through(world, install.id)
    other = world.note("# Maps\n\nThe map is old.\n", "Maps")  # not through Slack
    if how == "app_uninstalled":
        answer = world.post(install.id, "events", event("app_uninstalled", "Ev0GONE0001"))
        assert answer.status_code == 200
    else:
        world.ada.view.remove_slack_install(install.id)
    gone_at_once(world, install.id)
    world.clock.now += timedelta(days=9)
    world.keeper().run_once()
    assert held(world, notes) == [True] * 3
    world.clock.now += timedelta(days=1)
    world.keeper().run_once()
    assert held(world, notes) == [False] * 3 and held(world, [other]) == [True]
    assert rows(world.db, "SELECT COUNT(*) FROM slack_notes") == [(0,)]
    forgotten = rows(world.db, "SELECT COUNT(*) FROM forgotten")[0][0]
    assert forgotten == 3  # cairn's own forgetting (H5b)


def test_forget_them_now_does_it_at_once(world):
    install = world.install()
    notes = notes_through(world, install.id)
    world.ada.view.remove_slack_install(install.id)
    assert world.ada.view.forget_slack_notes_now(install.id) == 3
    assert held(world, notes) == [False] * 3


def test_making_the_same_teams_install_ready_again_before_the_day_cancels_it(world):
    install = world.install()
    notes = notes_through(world, install.id)
    world.ada.view.remove_slack_install(install.id)
    world.clock.now += timedelta(days=5)
    again = world.install()
    assert again.id == install.id and again.state == "ready"
    world.clock.now += timedelta(days=6)
    world.keeper().run_once()
    assert held(world, notes) == [True] * 3


def test_another_teams_install_made_ready_does_not_cancel_it(world):
    install = world.install()
    notes = notes_through(world, install.id)
    world.ada.view.remove_slack_install(install.id)
    world.install(OTHER_SECRET, OTHER_TOKEN, OTHER_TEAM)
    world.clock.now += timedelta(days=10)
    world.keeper().run_once()
    assert held(world, notes) == [False] * 3


def revoked(bot: str = BOT, oauth: tuple = ()) -> dict:
    return {"tokens": {"oauth": list(oauth), "bot": [bot] if bot else []}}


def test_tokens_revoked_naming_the_bot_switches_off_deletes_the_bot_token_and_forgets_nothing(
    world,
):
    install = world.install()
    world.post(install.id, "events", challenge())
    world.ada.view.link_slack_user(install.id, ADA_SLACK, world.ada.id)
    notes = notes_through(world, install.id)
    other = event("tokens_revoked", "Ev0USERONLY", **revoked(bot="", oauth=(ADA_SLACK,)))
    assert world.post(install.id, "events", other).status_code == 200
    assert world.ada.view.get_slack_install().state == "on"  # a user's token, not the bot's
    answer = world.post(install.id, "events", event("tokens_revoked", "Ev0BOTGONE1", **revoked()))
    assert answer.status_code == 200
    found = world.ada.view.get_slack_install()
    assert found.state == "off" and not found.holds_secrets and not found.holds_bot_token
    assert found.needs_bot_token  # on again only with a new bot token
    held_now = rows(
        world.db, "SELECT signing_secret IS NOT NULL, bot_token IS NULL FROM slack_installs"
    )
    assert held_now == [(1, 1)]  # the signing secret stays, sealed
    assert SECRET.encode() not in world.db.read_bytes()
    assert rows(world.db, "SELECT forget_at FROM slack_notes") == [(None,)] * 3
    with pytest.raises(ValueError):
        world.ada.view.switch_slack_install(install.id, on=True)
    world.clock.now += timedelta(days=30)
    world.keeper().run_once()
    assert held(world, notes) == [True] * 3


@pytest.mark.parametrize("order", ["uninstalled first", "revoked first"])
def test_the_two_events_in_either_order_end_as_app_uninstalled_alone(tmp_path, order):
    def run(where: Path, events: list[bytes]) -> tuple:
        world = World(where)
        try:
            install = world.install()
            world.post(install.id, "events", challenge())
            world.ada.view.link_slack_user(install.id, ADA_SLACK, world.ada.id)
            notes_through(world, install.id)
            for body in events:
                world.post(install.id, "events", body)
            state = the_state(world, install.id)
            return state[0], state[1], state[2], [when for _, when in state[3]]
        finally:
            world.store.close()

    uninstalled = event("app_uninstalled", "Ev0BOTH0001")
    bot = event("tokens_revoked", "Ev0BOTH0002", **revoked())
    alone = run(tmp_path / "alone", [uninstalled])
    assert alone[:3] == ("removed", False, [(0,)]) and None not in alone[3]
    both = [uninstalled, bot] if order == "uninstalled first" else [bot, uninstalled]
    assert run(tmp_path / "both", both) == alone


def test_tokens_revoked_after_the_install_is_removed_is_404_and_changes_nothing(world):
    install = world.install()
    world.post(install.id, "events", challenge())
    gone = world.post(install.id, "events", event("app_uninstalled", "Ev0GONE0001"))
    assert gone.status_code == 200
    before = dump(world.db)
    late = world.post(install.id, "events", event("tokens_revoked", "Ev0LATE0001", **revoked()))
    assert late.status_code == 404 and late.content == world.unknown_page().content
    assert dump(world.db) == before
