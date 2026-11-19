"""H8 acceptance tests (frozen): sign-in limited to named people.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/server.py and src/cairn/web/signin.py.

Why: a hosted cairn with GitHub or Google sign-in and `join="personal"` gives a workspace to
anyone who has an account at the provider. A private pre-release needs a gate that names
people, and a personal address belongs to no organisation's domain.

The list
- `SignIn(..., allowed_emails=())`. When not empty, a person signs in only when the email
  the provider has verified for them is one of these addresses: the whole address, ignoring
  case and the spaces around an entry. Nothing else matches: no pattern, no sub-address
  ("ada+notes@..." is another address), and not an address the provider lists for the
  account beside its primary one. An unverified email is refused as before, listed or not.
- With the list alone, a personal Google account whose address is listed signs in: the list
  names a person, not an organisation.
- With `allowed_email_domains` as well, either rule admits: the domains' people as before
  (for Google, still only accounts of that organisation), and the listed people from
  anywhere.
- The rule is applied at every sign-in, before anything is written. A refusal is a 403 on
  the sign-in page, sets no session, leaves the database exactly as it was, and repeats
  neither the address that was refused nor a listed one. An invitation does not open the
  gate: someone invited to a workspace who is not listed is refused, and the invitation
  stays open.
- The list limits who signs in and nothing else: a session that is already open goes on
  working when a later start of the server has a shorter list.
- An entry that is not one address (blank, no "@" or more than one, nothing before or after
  it, a space inside) is a ValueError when the app is made, and the message does not repeat
  the entry.
- A shared workspace (`join="default"`) behind GitHub or Google is accepted with a list, as
  it is with domains; with neither it is still a ValueError.

The command line
- `serve` reads the list from CAIRN_ALLOWED_EMAILS, addresses separated by commas. There is
  no option for it, so an address is never on a command line. Not set: no list. Set but
  blank, or holding an entry that is not one address: exit 2, naming the variable and never
  a value. Set without --sign-in: exit 2 (a list nobody is checked against).
- Nothing `serve` prints at start, and nothing cairn logs when it refuses someone, holds an
  address.
"""

import logging
import secrets
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, quote, urlsplit

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

from cairn import server
from cairn.identity import GitHub, Google
from cairn.server import SignIn, create_app
from cairn.store import Store
from cookie_names import session_cookie

BASE = "https://cairn.example.test"
SECRETS = {"github": "gh-client-secret-do-not-leak", "google": "g-client-secret-do-not-leak"}
ADA = "ada@example.test"
BROOK = "brook@example.test"
ADA_GITHUB = {"id": 990017734, "login": "ada", "name": "Ada Lovelace", "email": ADA}
BROOK_GITHUB = {"id": 2002, "login": "brook", "name": "Brook Ng", "email": BROOK}
SECONDARY = "old@example.test"  # every GitHub account here also lists this one, verified


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def query(url: str) -> dict:
    return {name: values[0] for name, values in parse_qs(urlsplit(url).query).items()}


class FakeProviders:
    """GitHub's and Google's OAuth and profile endpoints, as an httpx mock."""

    def __init__(self) -> None:
        self.codes: dict[str, tuple[str, dict, bool]] = {}
        self.tokens: dict[str, tuple[dict, bool]] = {}

    def approve(self, provider: str, profile: dict, verified: bool = True) -> str:
        code = f"code-{provider}-{secrets.token_hex(4)}"
        self.codes[code] = (provider, profile, verified)
        return code

    def _token(self, provider: str, request: httpx.Request) -> httpx.Response:
        sent = dict(parse_qsl(request.content.decode()))
        expected = self.codes.pop(sent.get("code", ""), None)
        if expected is None or expected[0] != provider:
            return httpx.Response(400, json={"error": "bad_verification_code"})
        token = f"provider-access-token-{secrets.token_hex(8)}"
        self.tokens[token] = (expected[1], expected[2])
        return httpx.Response(200, json={"access_token": token, "token_type": "bearer"})

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == "https://github.com/login/oauth/access_token":
            return self._token("github", request)
        if url == "https://oauth2.googleapis.com/token":
            return self._token("google", request)
        found = self.tokens.get(request.headers.get("authorization", "").partition(" ")[2])
        if found is None:
            return httpx.Response(401, json={"message": "Bad credentials"})
        profile, verified = found
        if url == "https://api.github.com/user":
            return httpx.Response(200, json={k: profile[k] for k in ("id", "login", "name")})
        if url == "https://api.github.com/user/emails":
            emails = [
                {"email": SECONDARY, "primary": False, "verified": True},
                {"email": profile["email"], "primary": True, "verified": verified},
            ]
            return httpx.Response(200, json=emails)
        if url == "https://openidconnect.googleapis.com/v1/userinfo":
            return httpx.Response(200, json={**profile, "email_verified": verified})
        return httpx.Response(404)


def providers() -> tuple:
    return (
        GitHub("github-client-id", SECRETS["github"]),
        Google("google-client-id", SECRETS["google"]),
    )


def app_on(store: Store, fake: FakeProviders, **sign_in):
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    return create_app(
        store, public_url=BASE, sign_in=SignIn(providers=providers(), http=http, **sign_in)
    )


class World:
    def __init__(self, tmp_path: Path, **sign_in) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.db = tmp_path / "cairn.db"
        self.store = Store(self.db)
        self.fake = FakeProviders()
        self.app = app_on(self.store, self.fake, **sign_in)
        self.client: TestClient | None = None


@pytest.fixture
def make(tmp_path):
    """Worlds for one test, each with its own database, closed when the test ends."""
    opened = []

    def build(name: str = "one", **sign_in) -> World:
        world = World(tmp_path / name, **sign_in)
        client = TestClient(world.app, base_url=BASE)
        client.__enter__()
        world.client = client
        opened.append(world)
        return world

    yield build
    for world in opened:
        world.client.__exit__(None, None, None)
        world.store.close()


def sign_in(world, provider: str, profile: dict, *, verified=True, next_path=None, client=None):
    """The whole trip: start, approve at the provider, come back to the callback."""
    client = client or world.client
    path = f"/auth/{provider}" + (f"?next={quote(next_path, safe='')}" if next_path else "")
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 302, r.text
    sent = query(r.headers["location"])
    code = world.fake.approve(provider, profile, verified)
    return client.get(
        f"/auth/{provider}/callback?code={code}&state={sent['state']}", follow_redirects=False
    )


def refused(world, provider: str, profile: dict, *listed: str, **how) -> None:
    """A sign-in that is turned away: 403, no session, nothing written, no address shown."""
    before = dump(world.db)
    had = world.client.cookies.get(session_cookie("https"))
    r = sign_in(world, provider, profile, **how)
    assert r.status_code == 403, (profile["email"], r.status_code)
    assert world.client.cookies.get(session_cookie("https")) == had
    assert dump(world.db) == before
    page = r.text.lower()
    for address in (profile["email"], *listed):
        assert address.lower() not in page, address


def signed_in_as(world) -> str:
    principal = world.store.resolve_session(world.client.cookies[session_cookie("https")])
    assert principal is not None and principal.user_id
    return world.store.get_user(principal.user_id).email


# The list


def test_only_listed_people_sign_in(make):
    world = make(join="personal", allowed_emails=(ADA,))
    refused(world, "github", BROOK_GITHUB, ADA)
    assert sign_in(world, "github", ADA_GITHUB).status_code == 200
    assert signed_in_as(world) == ADA
    assert len(world.store.list_accounts()) == 2  # the default one, and Ada's own


def test_the_whole_address_is_compared_ignoring_case_and_outer_spaces(make):
    world = make(join="personal", allowed_emails=("  ADA@Example.Test ", BROOK))
    for other in (
        "ada+notes@example.test",
        "xada@example.test",
        "ada@example.test.elsewhere.example",
        "ada@sub.example.test",
        "ada.@example.test",
    ):
        stranger = {"id": 3003, "login": "other", "name": "Someone Else", "email": other}
        refused(world, "github", stranger, ADA, BROOK)
    shouting = {**ADA_GITHUB, "email": "Ada@EXAMPLE.test"}
    assert sign_in(world, "github", shouting).status_code == 200


def test_an_address_beside_the_primary_one_does_not_count(make):
    world = make(join="personal", allowed_emails=(SECONDARY,))
    refused(world, "github", ADA_GITHUB)  # GitHub lists old@example.test for her, verified


def test_an_unverified_address_is_refused_listed_or_not(make):
    world = make(join="personal", allowed_emails=(ADA,))
    before = dump(world.db)
    assert sign_in(world, "github", ADA_GITHUB, verified=False).status_code == 403
    assert session_cookie("https") not in world.client.cookies and dump(world.db) == before


def test_a_personal_google_account_on_the_list_signs_in(make):
    world = make(join="personal", allowed_emails=(ADA,))
    personal = {"sub": "g-ada-1", "name": "Ada L.", "email": ADA}  # no `hd`: not a Workspace
    assert sign_in(world, "google", personal).status_code == 200
    assert signed_in_as(world) == ADA
    refused(make("two", join="personal", allowed_emails=(ADA,)), "google",
            {"sub": "g-brook-1", "name": "Brook", "email": BROOK}, ADA)  # fmt: skip


def test_domains_and_the_list_each_admit(make):
    listed = "guest@elsewhere.example"
    world = make(join="default", allowed_email_domains=("acme.example",), allowed_emails=(listed,))
    managed = {"sub": "g-clara", "name": "Clara Diaz", "email": "clara@acme.example",
               "hd": "acme.example"}  # fmt: skip
    assert sign_in(world, "google", managed).status_code == 200  # the organisation's own
    guest = {"id": 4004, "login": "guest", "name": "A Guest", "email": listed}
    assert sign_in(world, "github", guest).status_code == 200  # listed, from anywhere
    assert sorted(m.email for m in world.store.list_members()) == ["clara@acme.example", listed]
    refused(world, "github", ADA_GITHUB, listed)  # neither
    # The domain rule is as it was: the organisation's address on a personal Google account.
    personal = {"sub": "g-dev-personal", "name": "Dev Patel", "email": "dev@acme.example"}
    refused(world, "google", personal, listed)


def test_an_invitation_does_not_open_the_gate(make):
    world = make(join="personal", allowed_emails=(ADA,))
    assert sign_in(world, "github", ADA_GITHUB).status_code == 200
    principal = world.store.resolve_session(world.client.cookies[session_cookie("https")])
    invite = world.store.for_account(principal.account_id, principal.user_id).invite(BROOK)
    browser = TestClient(world.app, base_url=BASE)  # Brook's own browser
    before = dump(world.db)
    r = sign_in(world, "github", BROOK_GITHUB, next_path=f"/join/{invite.token}", client=browser)
    assert r.status_code == 403 and session_cookie("https") not in browser.cookies
    assert BROOK not in r.text and dump(world.db) == before
    found = world.store.find_invite(invite.token)
    assert found is not None and found.email == BROOK


def test_the_list_holds_on_every_request_and_deletes_nothing(make):
    world = make(join="personal", allowed_emails=(ADA, BROOK))
    assert sign_in(world, "github", BROOK_GITHUB).status_code == 200
    session = world.client.cookies[session_cookie("https")]
    before = dump(world.db)
    # The server starts again with a shorter list, on the same database.
    later = app_on(world.store, world.fake, join="personal", allowed_emails=(ADA,))
    with TestClient(later, base_url=BASE) as browser:
        browser.cookies.set(session_cookie("https"), session)
        home = browser.get("/", follow_redirects=False)
        assert "Brook Ng" not in home.text  # ended: answered as to someone signed out
        assert dump(world.db) == before  # nothing deleted
        fresh = TestClient(later, base_url=BASE)  # signing in again is another matter
        r = sign_in(world, "github", BROOK_GITHUB, client=fresh)
        assert r.status_code == 403 and session_cookie("https") not in fresh.cookies


@pytest.mark.parametrize(
    "entry",
    [
        "",
        "   ",
        "zelda-with-no-at",
        "zelda@",
        "@zelda.example",
        "zelda@quux@zelda.example",
        "zel da@zelda.example",
        "zelda@zel da.example",
    ],
)
def test_an_entry_that_is_not_one_address_is_refused_when_the_app_is_made(tmp_path, entry):
    store = Store(tmp_path / "cairn.db")
    try:
        with pytest.raises(ValueError) as refusal:
            app_on(store, FakeProviders(), join="personal", allowed_emails=(ADA, entry))
        message = str(refusal.value).lower()
        assert "zelda" not in message and "quux" not in message and ADA not in message
    finally:
        store.close()


def test_a_shared_workspace_behind_a_public_provider_is_accepted_with_a_list(tmp_path):
    store = Store(tmp_path / "cairn.db")
    try:
        with pytest.raises(ValueError):
            app_on(store, FakeProviders(), join="default")
        app_on(store, FakeProviders(), join="default", allowed_emails=(ADA,))
    finally:
        store.close()


# The command line

GITHUB_ENV = {
    "CAIRN_GITHUB_CLIENT_ID": "gh-id-from-env",
    "CAIRN_GITHUB_CLIENT_SECRET": "gh-secret-from-env",
}


def serve(monkeypatch, tmp_path, argv, env):
    """Run `serve` with uvicorn replaced; returns (exit code, the apps it would have run)."""
    monkeypatch.delenv("CAIRN_ALLOWED_EMAILS", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    db = ["--db", str(tmp_path / "cairn.db"), "--public-url", BASE]
    return server.main(["serve", *db, *argv]), apps


SHARED = ["--sign-in", "github", "--join", "default"]  # refused unless something limits it


def test_serve_reads_the_list_from_the_environment(tmp_path, monkeypatch, capsys):
    code, apps = serve(monkeypatch, tmp_path, SHARED, GITHUB_ENV)
    assert code == 2 and apps == []  # the control: open to everyone, so refused
    capsys.readouterr()
    listed = {**GITHUB_ENV, "CAIRN_ALLOWED_EMAILS": f" {ADA} , Brook@Example.test"}
    code, apps = serve(monkeypatch, tmp_path, SHARED, listed)
    assert code == 0 and len(apps) == 1  # the list reached the app: it is what closes it
    said = capsys.readouterr()
    for text in (said.out.lower(), said.err.lower()):
        assert ADA not in text and BROOK not in text


def test_serve_has_no_option_for_the_list(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("serve started"))
    for option in ("--allowed-email", "--allowed-emails"):
        with pytest.raises(SystemExit) as stopped:
            server.main(["serve", "--db", str(tmp_path / "cairn.db"), option, ADA])
        assert stopped.value.code == 2
    capsys.readouterr()


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        f"{ADA},,{BROOK}",
        f"{ADA},",
        "zelda-with-no-at",
        f"{ADA}, zelda@",
        f"{ADA} {BROOK}",
    ],
)
def test_serve_names_the_variable_for_a_list_it_cannot_use(tmp_path, monkeypatch, capsys, value):
    env = {**GITHUB_ENV, "CAIRN_ALLOWED_EMAILS": value}
    code, apps = serve(monkeypatch, tmp_path, ["--sign-in", "github"], env)
    err = capsys.readouterr().err
    assert code == 2 and apps == [] and "CAIRN_ALLOWED_EMAILS" in err
    assert "zelda" not in err.lower() and ADA not in err.lower() and BROOK not in err.lower()


def test_serve_refuses_a_list_nobody_is_checked_against(tmp_path, monkeypatch, capsys):
    code, apps = serve(monkeypatch, tmp_path, [], {"CAIRN_ALLOWED_EMAILS": ADA})
    err = capsys.readouterr().err
    assert code == 2 and apps == []
    assert "CAIRN_ALLOWED_EMAILS" in err and "--sign-in" in err and ADA not in err


def test_a_refusal_logs_no_address(make, caplog):
    caplog.set_level(logging.DEBUG)
    world = make(join="personal", allowed_emails=(ADA,))
    refused(world, "github", BROOK_GITHUB, ADA)
    assert sign_in(world, "github", ADA_GITHUB).status_code == 200  # and neither does a pass
    logged = "\n".join(record.getMessage() for record in caplog.records).lower()
    assert BROOK not in logged and ADA not in logged
