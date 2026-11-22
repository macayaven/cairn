"""Unit tests for `serve --test-instance` (create_app's `test_instance`): what a visitor who is
not signed in is told on an install whose sign-in is limited to approved people. Off, every
page is as it was; on, the landing page, the sign-in page and the header's link say the
install is for approved testers and lead with the self-host guide. Nobody's admission
changes. Under a brand the flag shows a visitor nothing new. The identity provider is a
stand-in; nothing leaves the process."""

from urllib.parse import parse_qs, quote, urlsplit

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

from cairn import server
from cairn.identity import OIDC, GitHub
from cairn.server import SignIn, create_app
from cairn.store import Store
from cairn.web import cli

BASE = "https://cairn.example.test"
ISSUER = "https://id.example.test"
NOTICE = "This is a private test instance. Sign-in is limited to approved testers."
# Every phrase the flag adds to a page: each is shown with it, and none without it.
LANDING_PHRASES = (
    NOTICE,
    "This instance is for approved testers.",
    "Run your own instance to create a workspace for yourself or your team.",
    'href="/login">Tester sign-in</a>',
)
SIGNIN_PHRASES = (
    NOTICE,
    "<h1>Sign in to the test instance</h1>",
    'class="button" href="/docs/run">Run it yourself</a>',
    'Read the <a href="/docs/privacy">privacy guide</a> before signing in.',
)
PHRASES = (*LANDING_PHRASES, *SIGNIN_PHRASES)
UNLISTED = "This install admits only the people its operator has listed."


class GitHubFake:
    """GitHub's endpoints for one person, whose address the test sets."""

    def __init__(self, email: str = "ada@example.test") -> None:
        self.email = email

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == "https://github.com/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "made-up-token"})
        if url == "https://api.github.com/user":
            return httpx.Response(200, json={"id": 7, "login": "ada", "name": "Ada Fields"})
        if url == "https://api.github.com/user/emails":
            return httpx.Response(
                200, json=[{"email": self.email, "primary": True, "verified": True}]
            )
        return httpx.Response(404)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


@pytest.fixture
def fake():
    return GitHubFake()


def app(store, fake, *, providers=None, sign_in_options=None, **settings):
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    options = {"allowed_emails": ("ada@example.test",)} if sign_in_options is None else {}
    options.update(sign_in_options or {})
    sign_in = SignIn(providers or (GitHub("id", "secret"),), http, **options)
    return create_app(store, public_url=BASE, sign_in=sign_in, **settings)


def sign_in(client, next_path=None):
    path = "/auth/github" + (f"?next={quote(next_path, safe='')}" if next_path else "")
    started = client.get(path, follow_redirects=False)
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    return client.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)


# Off: nothing changes


@pytest.mark.parametrize("given", [{}, {"test_instance": False}])
@pytest.mark.parametrize("join", ["personal", "default"])
def test_without_the_flag_no_page_says_any_of_its_words(store, fake, given, join):
    options = {"join": join, "allowed_emails": ("ada@example.test",)}
    with TestClient(app(store, fake, sign_in_options=options, **given), base_url=BASE) as c:
        for path in ("/", "/login", "/login?error=cancelled", "/docs", "/docs/run",
                     "/docs/privacy", "/no-such-page"):  # fmt: skip
            shown = c.get(path, follow_redirects=False).text
            for phrase in PHRASES:
                assert phrase not in shown, (path, phrase)
        fake.email = "outsider@elsewhere.test"
        refused = sign_in(c)
        assert refused.status_code == 403
        assert not [phrase for phrase in PHRASES if phrase in refused.text]


def test_with_the_flag_each_phrase_is_shown_where_it_belongs(store, fake):
    """The list the test above looks for is the one the flag shows: it cannot go stale."""
    with TestClient(app(store, fake, test_instance=True), base_url=BASE) as client:
        landing, login = client.get("/").text, client.get("/login").text
    for phrase in LANDING_PHRASES:
        assert phrase in landing, phrase
    for phrase in SIGNIN_PHRASES:
        assert phrase in login, phrase


# On: what a visitor is told


@pytest.mark.parametrize(
    ("providers", "options"),
    [
        (None, {"allowed_emails": ("ada@example.test",)}),
        (None, {"allowed_email_domains": ("example.test",)}),
        ((OIDC("sso", "Acme SSO", ISSUER, "id", "secret"),), {}),
    ],
)
def test_the_landing_page_leads_with_the_self_host_guide(store, fake, providers, options):
    built = app(store, fake, providers=providers, sign_in_options=options, test_instance=True)
    with TestClient(built, base_url=BASE) as client:
        landing = client.get("/", follow_redirects=False)
    assert landing.status_code == 200
    shown = landing.text
    assert NOTICE in shown
    buttons = [part.split('"', 1)[0] for part in shown.split('class="button" href="')[1:]]
    assert buttons[0] == "/docs/run"  # the first button: run it yourself
    assert "/login" not in buttons  # signing in is a plain link, for the testers
    assert 'href="/login">Tester sign-in</a>' in shown
    assert "git clone https://github.com/macayaven/cairn" in shown  # the run section stays
    assert "Sign in to connect an agent" not in shown


def test_the_sign_in_page_says_so_and_offers_the_guide_before_the_providers(store, fake):
    with TestClient(app(store, fake, test_instance=True, session_days=30), base_url=BASE) as c:
        page = c.get("/login?next=/workspace&error=cancelled").text
    assert page.count("<h1>") == 1 and "<h1>Sign in to the test instance</h1>" in page
    assert NOTICE in page
    assert page.index('href="/docs/run"') < page.index('class="providers"')
    assert page.index('class="providers"') < page.index("session cookie, valid for 30 days")
    assert "cairn keeps no password." in page
    assert 'role="alert"' in page and "Refused." in page  # a refusal is shown as before
    assert 'href="/auth/github?next=%2Fworkspace"' in page  # the way back as before


@pytest.mark.parametrize("path", ["/", "/docs", "/docs/run", "/docs/privacy", "/docs/security"])
def test_the_header_link_says_tester_sign_in_on_the_landing_page_and_the_guides(store, fake, path):
    with TestClient(app(store, fake, test_instance=True), base_url=BASE) as client:
        shown = client.get(path, follow_redirects=False)
    assert shown.status_code == 200
    nav = shown.text.split('<nav class="nav" aria-label="Main">', 1)[1].split("</nav>", 1)[0]
    assert 'href="/login">Tester sign-in</a>' in nav
    assert ">Sign in</a>" not in nav


def test_signed_in_the_pages_are_as_without_the_flag(store, fake, tmp_path):
    def pages(test_instance: bool) -> dict[str, bytes]:
        built = app(store, fake, test_instance=test_instance, wall=True)
        with TestClient(built, base_url=BASE) as client:
            assert sign_in(client).status_code == 200
            return {p: client.get(p).content for p in ("/docs", "/docs/run", "/workspace")}

    assert pages(True) == pages(False)


def test_behind_the_wall_the_sign_in_page_links_to_no_guide(store, fake):
    with TestClient(app(store, fake, test_instance=True, wall=True), base_url=BASE) as client:
        assert client.get("/", follow_redirects=False).status_code == 303
        assert client.get("/docs/run", follow_redirects=False).status_code == 303
        page = client.get("/login").text
    assert "<h1>Sign in to the test instance</h1>" in page and NOTICE in page
    assert 'href="/docs' not in page  # neither the run guide, the privacy guide nor the footer's
    assert 'href="/auth/github"' in page
    assert "session cookie, valid for 12 hours" in page


@pytest.mark.parametrize("wall", [False, True])
def test_under_a_brand_a_visitor_is_shown_exactly_what_they_are_shown_without_it(tmp_path, wall):
    def answers(test_instance: bool) -> dict[str, tuple[int, bytes]]:
        store = Store(tmp_path / f"{test_instance}.db")
        fake = GitHubFake("outsider@elsewhere.test")
        built = app(store, fake, test_instance=test_instance, wall=wall, brand_name="Juniper")
        try:
            with TestClient(built, base_url=BASE) as client:
                shown = {
                    path: client.get(path, follow_redirects=False)
                    for path in ("/", "/login", "/login?error=cancelled", "/docs/run", "/nowhere")
                }
                shown["refused"] = sign_in(client)
            return {path: (r.status_code, r.content) for path, r in shown.items()}
        finally:
            store.close()

    with_flag = answers(True)
    assert with_flag == answers(False)
    assert with_flag["refused"][0] == 403
    assert with_flag["/login"][0] == 200 and b"Juniper" in with_flag["/login"][1]


# Admission is the same with the flag


@pytest.mark.parametrize("test_instance", [False, True])
def test_the_flag_changes_nobody_s_admission(store, fake, test_instance):
    with TestClient(app(store, fake, test_instance=test_instance), base_url=BASE) as client:
        assert sign_in(client).status_code == 200  # on the list
    fake.email = "outsider@elsewhere.test"
    with TestClient(app(store, fake, test_instance=test_instance), base_url=BASE) as client:
        refused = sign_in(client)
    assert refused.status_code == 403
    assert f"Refused. {UNLISTED}" in refused.text


def test_an_invitation_does_not_admit_an_address_the_list_leaves_out(store, fake):
    team = store.create_account("Fieldwork")
    invitation = store.for_account(team.id).invite("brook@example.test")
    fake.email = "brook@example.test"
    with TestClient(app(store, fake, test_instance=True), base_url=BASE) as client:
        refused = sign_in(client, next_path=f"/join/{invitation.token}")
    assert refused.status_code == 403 and UNLISTED in refused.text
    assert [i.email for i in store.for_account(team.id).list_invites()] == ["brook@example.test"]


# Refused at start unless sign-in is limited


def test_create_app_refuses_the_flag_without_sign_in(store):
    with pytest.raises(ValueError, match="--test-instance needs --sign-in"):
        create_app(store, public_url=BASE, test_instance=True)


@pytest.mark.parametrize(
    "options",
    [{}, {"join": "default"}, {"allowed_emails": ()}, {"allowed_email_domains": ()}],
)
def test_create_app_refuses_the_flag_with_a_provider_anyone_can_join_and_no_list(
    store, fake, options
):
    with pytest.raises(ValueError, match="--test-instance needs sign-in limited"):
        app(store, fake, sign_in_options=options, test_instance=True)


def test_create_app_refuses_the_flag_when_one_of_two_providers_is_open(store, fake):
    providers = (GitHub("id", "secret"), OIDC("sso", "Acme SSO", ISSUER, "id", "secret"))
    with pytest.raises(ValueError, match="--test-instance needs sign-in limited"):
        app(store, fake, providers=providers, sign_in_options={}, test_instance=True)


SERVE_ENV = {
    "CAIRN_GITHUB_CLIENT_ID": "github-client-id",
    "CAIRN_GITHUB_CLIENT_SECRET": "made-up-secret",
    "CAIRN_OIDC_ISSUER": ISSUER,
    "CAIRN_OIDC_CLIENT_ID": "acme-client-id",
    "CAIRN_OIDC_CLIENT_SECRET": "made-up-oidc-secret",
}
GATES = ("CAIRN_ALLOWED_EMAILS", "CAIRN_OIDC_ALLOWED_GROUPS", "CAIRN_OIDC_GROUPS_CLAIM")


def serve(monkeypatch, tmp_path, argv, env=None):
    """Run `serve` with uvicorn replaced; (exit code, the sign-in page, the database path)."""
    for name in GATES:
        monkeypatch.delenv(name, raising=False)
    for name, value in {**SERVE_ENV, **(env or {})}.items():
        monkeypatch.setenv(name, value)
    seen = {}

    def run(built, **options):
        with TestClient(built, base_url=BASE) as client:
            seen["login"] = client.get("/login").text

    monkeypatch.setattr(uvicorn, "run", run)
    db = tmp_path / "cairn.db"
    code = server.main(["serve", "--db", str(db), "--public-url", BASE, *argv])
    return code, seen.get("login"), db


@pytest.mark.parametrize(
    ("argv", "env"),
    [
        (["--test-instance"], {}),
        (["--sign-in", "github", "--test-instance"], {}),
        (["--sign-in", "github", "--join", "default", "--test-instance"], {}),
        (["--sign-in", "google", "--test-instance"], {
            "CAIRN_GOOGLE_CLIENT_ID": "google-client-id",
            "CAIRN_GOOGLE_CLIENT_SECRET": "made-up-google-secret",
        }),
        (["--sign-in", "oidc", "--test-instance"], {}),
        (["--sign-in", "oidc", "--join", "default", "--test-instance"], {}),
        (["--sign-in", "github", "--sign-in", "oidc", "--closed-directory", "--test-instance"],
         {}),
    ],
)  # fmt: skip
def test_serve_refuses_the_flag_unless_sign_in_is_limited(tmp_path, monkeypatch, capsys, argv, env):
    code, login, db = serve(monkeypatch, tmp_path, argv, env)
    err = capsys.readouterr().err.strip().splitlines()
    assert code == 2 and login is None and not db.exists()
    assert "--test-instance" in err[-1]
    assert not any("made-up" in line for line in err)


@pytest.mark.parametrize(
    ("argv", "env"),
    [
        (["--sign-in", "github", "--allowed-email-domain", "example.test"], {}),
        (["--sign-in", "github"], {"CAIRN_ALLOWED_EMAILS": "ada@example.test"}),
        (["--sign-in", "oidc", "--closed-directory"], {}),
        (["--sign-in", "oidc"],
         {"CAIRN_OIDC_GROUPS_CLAIM": "groups", "CAIRN_OIDC_ALLOWED_GROUPS": "testers"}),
        (["--sign-in", "oidc", "--join", "default", "--closed-directory"], {}),
    ],
)  # fmt: skip
def test_serve_takes_the_flag_with_sign_in_limited_one_of_four_ways(
    tmp_path, monkeypatch, argv, env
):
    code, login, _ = serve(monkeypatch, tmp_path, [*argv, "--test-instance"], env)
    assert code == 0
    assert NOTICE in login and "<h1>Sign in to the test instance</h1>" in login


def test_serve_without_the_flag_shows_the_sign_in_page_as_before(tmp_path, monkeypatch):
    argv = ["--sign-in", "github", "--allowed-email-domain", "example.test"]
    code, login, _ = serve(monkeypatch, tmp_path, argv)
    assert code == 0 and "<h1>Sign in</h1>" in login
    assert not [phrase for phrase in PHRASES if phrase in login]


def test_the_help_of_serve_names_the_flag(capsys):
    with pytest.raises(SystemExit):
        cli.main(["serve", "--help"])
    assert "--test-instance" in capsys.readouterr().out
