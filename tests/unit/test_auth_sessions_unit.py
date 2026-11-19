"""Unit tests for a sign-in that is remembered (B1): the store's renewal and the end of a
person's sessions, and how the web app keeps the cookie. They pin what
tests/acceptance/test_b1_wall.py leaves open."""

import contextlib
import re
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie

import httpx
import pytest
from starlette.testclient import TestClient

from cairn import server
from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.store import Store

BASE = "https://kb.example.test"
START = datetime(2099, 11, 14, 9, 0, tzinfo=UTC)  # far ahead of the real clock
DAY, HOUR = timedelta(days=1), timedelta(hours=1)
N = 30 * 86_400  # a 30-day session, in seconds
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}


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


def person(store, number=1, email="ada@example.test"):
    return store.sign_in(
        "github", str(number), email, f"Person {number}", issuer="https://github.com",
        join="personal", ttl_seconds=N,
    )  # fmt: skip


def expiry(store, token) -> str:
    with store._read() as con:
        (found,) = con.execute(
            "SELECT expires_at FROM sessions WHERE digest = ?", (_digest(token),)
        ).fetchone()
    return found


def _digest(token: str) -> str:
    from cairn.store.base import _digest as digest

    return digest(token)


# The store: one guarded renewal


def test_a_session_is_renewed_only_more_than_a_day_after_its_last_renewal(store, clock):
    signed = person(store)
    made = expiry(store, signed.token)
    clock.now = START + DAY  # exactly a day: not more
    assert store.renew_session(signed.token, N, 86_400) is False
    assert expiry(store, signed.token) == made
    clock.now = START + DAY + timedelta(seconds=1)
    assert store.renew_session(signed.token, N, 86_400) is True
    renewed = expiry(store, signed.token)
    assert renewed > made
    clock.now += 23 * HOUR  # within a day of that renewal, not of the sign-in
    assert store.renew_session(signed.token, N, 86_400) is False
    assert expiry(store, signed.token) == renewed


def test_two_requests_at_once_renew_once(store, clock, tmp_path, monkeypatch):
    """Both read that the session is due; only the first UPDATE's guard still holds."""
    signed = person(store)
    clock.now = START + 2 * DAY
    other = Store(tmp_path / "kb.db", clock=clock)  # another process on the same file
    renewed_meanwhile = []
    real_read = store._read

    @contextlib.contextmanager
    def read_then_the_other_request():
        with real_read() as con:
            yield con
        renewed_meanwhile.append(other.renew_session(signed.token, N, 86_400))

    monkeypatch.setattr(store, "_read", read_then_the_other_request)
    try:
        assert store.renew_session(signed.token, N, 86_400) is False
        assert renewed_meanwhile == [True]
    finally:
        other.close()


def test_a_session_that_ended_or_ran_out_is_never_brought_back(store, clock):
    ended, ran_out = person(store, 1), person(store, 2, "brook@example.test")
    store.for_account(ended.account_id).end_session(ended.token)
    clock.now = START + timedelta(seconds=N) + HOUR
    assert store.renew_session(ended.token, N, 86_400) is False
    assert store.renew_session(ran_out.token, N, 86_400) is False
    assert store.resolve_session(ran_out.token) is None
    assert store.renew_session("no-such-token", N, 86_400) is False


def test_a_session_opened_with_a_key_is_never_renewed(store, clock):
    signed = person(store)
    view = store.for_account(signed.account_id, signed.user.id)
    key = view.create_key("laptop")
    found = store.identify_key(key)
    assert found is not None
    keyed = view.acting_as("key", "laptop", found[1]).create_session(N)
    clock.now = START + 2 * DAY
    assert store.renew_session(keyed, N, 86_400) is False
    assert store.renew_session(signed.token, N, 86_400) is True


def test_only_the_store_that_opened_the_database_renews_or_ends_sessions(store):
    signed = person(store)
    view = store.for_account(signed.account_id, signed.user.id)
    with pytest.raises(PermissionError):
        view.renew_session(signed.token, N, 86_400)
    with pytest.raises(PermissionError):
        view.end_person_sessions("ada@example.test")


# The store: ending one person's sessions


def test_ending_a_persons_sessions_ends_every_one_in_every_workspace_and_audits_each(store):
    ada, brook = person(store, 1), person(store, 2, "brook@example.test")
    invite = store.for_account(brook.account_id, brook.user.id).invite("ada@example.test")
    store.for_account(brook.account_id).accept_invite(invite.token, ada.user.id)
    there = store.for_account(brook.account_id, ada.user.id).create_session()
    second_here = store.for_account(ada.account_id, ada.user.id).create_session()

    ended = store.end_person_sessions("  ADA@Example.TEST ")  # the allowlist's rule

    assert ended == {ada.account_id: 2, brook.account_id: 1}
    for token in (ada.token, there, second_here):
        assert store.resolve_session(token) is None
    assert store.resolve_session(brook.token) is not None
    for account, count in ended.items():
        (event,) = [
            e for e in store.for_account(account).list_audit() if e.action == "sessions.ended"
        ]
        assert event.actor_kind == "system" and event.actor is None
        assert event.detail == {"member": ada.user.id, "sessions": count}
        assert "@" not in str(event.detail)
    assert store.end_person_sessions("ada@example.test") == {}  # nothing left: no event
    assert store.end_person_sessions("nobody@example.test") == {}
    assert store.end_person_sessions("ada@example") == {}  # the whole address, not a part


# The command


def test_session_end_prints_the_count_and_writes_one_event_per_workspace(tmp_path, capsys):
    db = tmp_path / "kb.db"
    made = Store(db)
    try:
        ada = made.sign_in("github", "1", "ada@example.test", "Ada", issuer="https://github.com",
                           join="personal")  # fmt: skip
    finally:
        made.close()
    capsys.readouterr()
    assert server.main(["session-end", "--db", str(db), "--email", "ada@example.test"]) == 0
    out = capsys.readouterr().out
    assert re.findall(r"\d+", out) == ["1"]
    assert out == "ended 1 web session of that address\n"
    after = Store(db)
    try:
        assert after.resolve_session(ada.token) is None
        events = after.for_account(ada.account_id).list_audit()
        assert [e.action for e in events][:1] == ["sessions.ended"]
    finally:
        after.close()


def test_session_end_needs_a_database_and_says_so(tmp_path, capsys):
    db = tmp_path / "missing.db"
    assert server.main(["session-end", "--db", str(db), "--email", "ada@example.test"]) == 1
    assert capsys.readouterr().err == f"no database at {db}\n"
    assert not db.exists()


def test_session_end_help_says_what_it_does(capsys):
    with pytest.raises(SystemExit):
        server.main(["session-end", "--help"])
    shown = " ".join(capsys.readouterr().out.split())
    assert "--email ADDRESS" in shown
    assert "API keys and agents are not touched" in shown


# The web app: the cookie of a remembered sign-in


class GitHubFake:
    def __init__(self) -> None:
        self.n = 0

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


def signed_in(browser) -> tuple[str, str, dict]:
    started = browser.get("/auth/github", follow_redirects=False)
    state = dict(httpx.URL(started.headers["location"]).params)["state"]
    back = browser.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
    assert back.status_code == 200, back.text
    return the_session_cookie(back)


def the_session_cookie(response) -> tuple[str, str, dict]:
    found = []
    for header in response.headers.get_list("set-cookie"):
        jar = SimpleCookie()
        jar.load(header)
        for name, morsel in jar.items():
            if morsel["path"] == "/" and "signin" not in name:  # not the state cookie
                found.append((name, morsel.value, {k: v for k, v in morsel.items() if v}))
    assert len(found) == 1, response.headers.get_list("set-cookie")
    return found[0]


def test_signing_out_after_a_renewal_sends_only_the_cookie_that_ends_it(store, clock):
    browser = TestClient(app(store, clock, session_days=30), base_url=BASE)
    name, token, _ = signed_in(browser)
    clock.now = START + 2 * DAY  # due: the request that signs out is the one that renews
    out = browser.post("/logout", headers=SAME_ORIGIN, follow_redirects=False)
    assert out.status_code == 303
    cookie_name, value, attributes = the_session_cookie(out)
    assert cookie_name == name and value == "" and attributes["max-age"] == "0"
    assert store.resolve_session(token) is None


def test_a_request_that_sets_a_new_session_does_not_also_send_the_renewed_one(store, clock):
    browser = TestClient(app(store, clock, session_days=30), base_url=BASE)
    _, token, _ = signed_in(browser)
    home = store.resolve_session(token).account_id
    clock.now = START + 2 * DAY
    r = browser.post(f"/workspaces/{home}/switch", headers=SAME_ORIGIN, follow_redirects=False)
    assert r.status_code == 303
    _, value, attributes = the_session_cookie(r)  # exactly one: the new session's
    assert value != token and attributes["max-age"] == str(N)


def test_without_the_setting_a_session_a_week_old_is_not_renewed(store, clock):
    browser = TestClient(app(store, clock), base_url=BASE)
    _, _, made = signed_in(browser)
    assert made["max-age"] == "43200"
    clock.now = START + 11 * HOUR
    r = browser.get("/", follow_redirects=False)
    assert r.status_code == 200 and "set-cookie" not in r.headers


def test_a_twelve_hour_session_made_before_the_setting_is_renewed_after_it(store, clock):
    """A server started again with --session-days: the sessions it made before are renewed
    on their next use, to the new length (their last renewal reads as long ago)."""
    browser = TestClient(app(store, clock), base_url=BASE)
    name, token, _ = signed_in(browser)
    clock.now = START + HOUR
    later = TestClient(app(store, clock, session_days=30), base_url=BASE)
    later.cookies.set(name, token)
    r = later.get("/", follow_redirects=False)
    assert r.status_code == 200
    _, value, attributes = the_session_cookie(r)
    assert value == token and attributes["max-age"] == str(N)
    assert store.resolve_session(token) is not None


@pytest.mark.parametrize(
    ("days", "said"),
    [
        (None, "a session cookie, valid for 12 hours.</p>"),
        (1, "a session cookie, valid for 1 day.</p>"),
        (
            30,
            "a session cookie, valid for 30 days; a visit more than a day after it was set or"
            " last renewed starts the 30 days again.</p>",
        ),
    ],
)
def test_the_sign_in_page_says_how_long_the_sign_in_lasts(store, clock, days, said):
    """signin.html says how long the cookie it sets is valid: 12 hours today, or the days
    --session-days gives and when a visit renews it (never "12 hours" when it is not, and no
    renewal for 1 day, which none can renew: the next test)."""
    settings = {} if days is None else {"session_days": days}
    page = TestClient(app(store, clock, **settings), base_url=BASE).get("/login")
    assert page.status_code == 200 and said in page.text
    assert ("12 hours" in page.text) is (days is None)


def test_a_one_day_sign_in_is_never_renewed(store, clock):
    """--session-days 1: a use renews a session only more than a day after its last renewal,
    and by then a one-day session has run out. So it lasts a day from the sign-in, however
    it is used."""
    browser = TestClient(app(store, clock, session_days=1), base_url=BASE)
    name, token, attributes = signed_in(browser)
    assert attributes["max-age"] == "86400"
    for hours in (1, 12, 23):
        clock.now = START + timedelta(hours=hours)
        r = browser.get("/", follow_redirects=False)
        assert r.status_code == 200 and name not in r.headers.get("set-cookie", ""), hours
    clock.now = START + DAY
    assert store.resolve_session(token) is None
    assert browser.get("/workspace", follow_redirects=False).status_code == 303


# Signing out: every name the session cookie has had


def cookies_set(response) -> dict[str, tuple[str, str, str]]:
    """Each cookie the response sets: its value, Max-Age and path."""
    found = {}
    for header in response.headers.get_list("set-cookie"):
        jar = SimpleCookie()
        jar.load(header)
        found.update({n: (m.value, m["max-age"], m["path"]) for n, m in jar.items()})
    return found


def sign_in_as(browser) -> tuple[str, str]:
    """Sign in; the session cookie's name and token (the one set with a value at Path=/)."""
    started = browser.get("/auth/github", follow_redirects=False)
    state = dict(httpx.URL(started.headers["location"]).params)["state"]
    back = browser.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
    assert back.status_code == 200, back.text
    ((name, token),) = [
        (n, value) for n, (value, _, path) in cookies_set(back).items() if value and path == "/"
    ]
    return name, token


def test_signing_out_ends_and_clears_every_name_the_session_cookie_has_had(store, clock):
    """A browser signed in before a brand was switched on, or off, still holds the cookie of
    the other name, and its session is stored. Signing out ends and clears every name the
    server has used for the session cookie (cairn_session, session, __Host-session,
    __Host-cairn_session) that the browser sends, wherever its session is, so none comes back
    when the brand is switched."""
    plain = TestClient(app(store, clock), base_url=BASE)
    _, old = sign_in_as(plain)
    branded = TestClient(app(store, clock, brand_name="Knowledge Base"), base_url=BASE)
    name, new = sign_in_as(branded)
    _, older = sign_in_as(TestClient(app(store, clock), base_url=BASE))
    branded.cookies.set("__Host-cairn_session", old)
    branded.cookies.set("session", older)  # the name over http, or before __Host-
    out = branded.post("/logout", headers=SAME_ORIGIN, follow_redirects=False)
    assert out.status_code == 303
    cleared = cookies_set(out)
    assert cleared == {n: ("", "0", "/") for n in (name, "__Host-cairn_session", "session")}
    for token in (new, old, older):
        assert store.resolve_session(token) is None

    # The other way: a session made under the brand, signing out without it.
    _, under_brand = sign_in_as(branded)
    _, plain_token = sign_in_as(plain)
    plain.cookies.set(name, under_brand)
    out = plain.post("/logout", headers=SAME_ORIGIN, follow_redirects=False)
    assert out.status_code == 303 and set(cookies_set(out)) == {"__Host-cairn_session", name}
    assert store.resolve_session(plain_token) is None
    assert store.resolve_session(under_brand) is None


def test_signing_out_with_only_the_current_cookie_clears_only_that_one(store, clock):
    """What the browser does not send is not cleared: a sign-out without a brand, from a
    browser holding only __Host-cairn_session, answers as it did before."""
    browser = TestClient(app(store, clock), base_url=BASE)
    sign_in_as(browser)
    out = browser.post("/logout", headers=SAME_ORIGIN, follow_redirects=False)
    assert list(cookies_set(out)) == ["__Host-cairn_session"]


# Lowering --session-days: serve caps what is stored when it starts

YEAR = 365 * 86_400


def ends(store, token) -> datetime:
    return datetime.fromisoformat(expiry(store, token))


def keyed_session(store, signed) -> tuple[str, str]:
    """A key the person made, and a year-long session opened with it."""
    view = store.for_account(signed.account_id, signed.user.id)
    key = view.create_key("laptop")
    return key, view.acting_as("key", "laptop", store.identify_key(key)[1]).create_session(YEAR)


def test_capping_web_sessions_shortens_a_persons_sessions_and_never_lengthens_one(store, clock):
    """Store.cap_web_sessions(seconds): every person's web session not opened with a key
    ends at most `seconds` from now. A shorter one keeps its expiry; a session opened with a
    key and the key itself are not touched. Only the store that opened the database."""
    long_one = store.sign_in(
        "github", "1", "ada@example.test", "Ada", issuer="https://github.com",
        join="personal", ttl_seconds=YEAR,
    )  # fmt: skip
    short_one = person(store, 2, "brook@example.test")  # 30 days
    key, keyed = keyed_session(store, long_one)
    keyed_ends, short_ends = ends(store, keyed), ends(store, short_one.token)
    clock.now = START + HOUR

    assert store.cap_web_sessions(N) == 1

    assert ends(store, long_one.token) == START + HOUR + timedelta(seconds=N)
    assert ends(store, short_one.token) == short_ends  # never lengthened
    assert ends(store, keyed) == keyed_ends and store.resolve_key(key) is not None
    with pytest.raises(PermissionError):
        store.for_account(long_one.account_id).cap_web_sessions(N)


def test_capping_with_nothing_to_cap_writes_nothing(store, clock, monkeypatch):
    person(store)
    writes = []
    real_write = store._write

    @contextlib.contextmanager
    def counted(**options):
        writes.append(options)
        with real_write(**options) as con:
            yield con

    monkeypatch.setattr(store, "_write", counted)
    assert store.cap_web_sessions(YEAR) == 0
    assert writes == []


@pytest.mark.parametrize(
    ("argv", "most"), [(["--session-days", "7"], 7 * DAY), ([], 12 * HOUR)], ids=["7", "none"]
)
def test_serve_caps_stored_sign_ins_at_the_length_now_in_force(
    tmp_path, monkeypatch, capsys, argv, most
):
    """Started again with a lower --session-days, or without it, `serve` caps a sign-in made
    under the longer length at the length now in force, from its start. A key's session is
    not touched."""
    import uvicorn

    db = tmp_path / "kb.db"
    made = Store(db)  # the real clock: serve opens the database with it
    try:
        signed = made.sign_in(
            "github", "1", "ada@example.test", "Ada", issuer="https://github.com",
            join="personal", ttl_seconds=YEAR,
        )  # fmt: skip
        key, keyed = keyed_session(made, signed)
        keyed_ends = ends(made, keyed)
    finally:
        made.close()
    monkeypatch.setenv("CAIRN_GITHUB_CLIENT_ID", "gh-id")
    monkeypatch.setenv("CAIRN_GITHUB_CLIENT_SECRET", "made-up")
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: None)
    started = datetime.now(UTC)

    code = server.main(
        ["serve", "--db", str(db), "--public-url", BASE, "--sign-in", "github", *argv]
    )

    assert code == 0
    after = Store(db)
    try:
        assert started + most <= ends(after, signed.token) <= datetime.now(UTC) + most
        assert ends(after, keyed) == keyed_ends and after.resolve_key(key) is not None
    finally:
        after.close()
    capsys.readouterr()


# session-end: the address a provider vouched for at the sign-in

OLD, NEW = "ada@example.test", "ada.quill@example.test"


def signed_in_again_as(store, email, ttl=N):
    """Person 1 signs in once more, the provider now vouching for `email`."""
    return store.sign_in("github", "1", email, "Ada", issuer="https://github.com",
                         join="personal", ttl_seconds=ttl)  # fmt: skip


def test_session_end_ends_the_sessions_of_a_sign_in_vouched_with_that_address(store):
    """A person's stored address is the one they first signed in with; the allowlist checks
    the one the provider vouches for now. Ending NEW ends every session made by a sign-in
    vouched with NEW, and those it led to, counted once each; the stored address is kept."""
    first = person(store)  # vouched OLD: the person's stored address
    again = signed_in_again_as(store, NEW)
    assert again.user.id == first.user.id and again.user.email == OLD
    brook = person(store, 2, "brook@example.test")
    invite = store.for_account(brook.account_id, brook.user.id).invite(OLD)
    store.for_account(brook.account_id).accept_invite(invite.token, first.user.id)

    ended = store.end_person_sessions(f"  {NEW.upper()} ")

    assert ended == {first.account_id: 1}
    assert store.resolve_session(again.token) is None
    assert store.resolve_session(first.token) is not None  # vouched OLD: not this address
    assert store.get_user(first.user.id).email == OLD  # what Store.sign_in links by
    (event,) = [
        e for e in store.for_account(first.account_id).list_audit() if e.action == "sessions.ended"
    ]
    assert event.detail == {"member": first.user.id, "sessions": 1}
    # OLD is still the stored address: both of its sessions end, and NEW's is not counted again.
    third = signed_in_again_as(store, NEW)
    assert store.end_person_sessions(OLD) == {first.account_id: 2}
    assert store.resolve_session(third.token) is None


def test_switching_workspace_and_taking_an_invitation_carry_the_vouched_address(store, clock):
    """The sessions a web sign-in leads to (switching workspace, taking an invitation) carry
    the address that sign-in was vouched with, so session-end of that address ends them."""
    first = store.sign_in("github", "7", "ada.old@example.test", "Ada",
                          issuer="https://github.com", join="personal")  # fmt: skip
    brook = person(store, 2, "brook@example.test")
    invite = store.for_account(brook.account_id, brook.user.id).invite("ada.old@example.test")
    browser = TestClient(app(store, clock), base_url=BASE)
    _, token, _ = signed_in(browser)  # GitHubFake vouches ada@example.test for subject 7
    assert store.resolve_session(token).user_id == first.user.id
    joined = browser.post(f"/join/{invite.token}", headers=SAME_ORIGIN, follow_redirects=False)
    _, there, _ = the_session_cookie(joined)
    switched = browser.post(
        f"/workspaces/{first.account_id}/switch", headers=SAME_ORIGIN, follow_redirects=False
    )
    _, back, _ = the_session_cookie(switched)

    ended = store.end_person_sessions("ada@example.test")

    assert ended == {first.account_id: 2, brook.account_id: 1}
    for gone in (token, there, back):
        assert store.resolve_session(gone) is None
    assert store.resolve_session(first.token) is not None
