"""The sentences of the run guide's "A private test instance" (`serve --test-instance`), each
pinned here so the guide cannot go false unnoticed. Each test finds its sentence in the
section, then asks the app or the command line. The app runs in process over a real store in
a temporary file; the identity provider is a stand-in; nothing leaves the process."""

import re
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

from cairn import server
from cairn.identity import OIDC, GitHub
from cairn.server import SignIn, create_app
from cairn.store import Store

RUN = files("cairn.guides").joinpath("run.md").read_text(encoding="utf-8")
BASE = "https://cairn.example.test"
ISSUER = "https://id.example.test"
TESTERS = "Sign-in is limited to approved testers."
UNLISTED = "This install admits only the people its operator has listed."
ENV = {
    "CAIRN_GITHUB_CLIENT_ID": "github-client-id",
    "CAIRN_GITHUB_CLIENT_SECRET": "made-up-secret",
    "CAIRN_OIDC_ISSUER": ISSUER,
    "CAIRN_OIDC_CLIENT_ID": "acme-client-id",
    "CAIRN_OIDC_CLIENT_SECRET": "made-up-oidc-secret",
}
GATES = ("CAIRN_ALLOWED_EMAILS", "CAIRN_OIDC_ALLOWED_GROUPS", "CAIRN_OIDC_GROUPS_CLAIM")


def flat(text: str) -> str:
    return " ".join(text.split())


def section(text: str, heading: str) -> str:
    """The text under `heading` up to the next heading of the same or a higher level."""
    level = len(heading.split(" ", 1)[0])
    lines = text[text.index(f"\n{heading}\n") + len(heading) + 2 :].splitlines()
    kept = []
    for line in lines:
        if re.match(rf"#{{1,{level}}} ", line):
            break
        kept.append(line)
    return "\n".join(kept)


THE_SECTION = section(RUN, "## A private test instance")


def says(sentence: str) -> None:
    assert flat(sentence) in flat(THE_SECTION), sentence


class GitHubFake:
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


def app(store, fake=None, *, join="personal", **settings):
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake or GitHubFake()))
    sign_in = SignIn(
        (GitHub("id", "secret"),), http, join=join, allowed_emails=("ada@example.test",)
    )
    return create_app(store, public_url=BASE, sign_in=sign_in, **settings)


def sign_in(client, next_path=None):
    path = "/auth/github" + (f"?next={next_path}" if next_path else "")
    started = client.get(path, follow_redirects=False)
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    return client.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)


def serve(monkeypatch, tmp_path, argv, env=None):
    for name in GATES:
        monkeypatch.delenv(name, raising=False)
    for name, value in {**ENV, **(env or {})}.items():
        monkeypatch.setenv(name, value)
    seen = {}

    def run(built, **options):
        with TestClient(built, base_url=BASE) as client:
            seen["login"] = client.get("/login").text

    monkeypatch.setattr(uvicorn, "run", run)
    db = tmp_path / "cairn.db"
    return server.main(["serve", "--db", str(db), "--public-url", BASE, *argv]), seen, db


def test_the_option_tells_visitors_the_install_is_a_private_test_instance(store):
    """ "`--test-instance` tells people who are not signed in that this install is a private
    test instance.\""""
    says(
        "`--test-instance` tells people who are not signed in that this install is a private"
        " test instance."
    )
    with TestClient(app(store, test_instance=True), base_url=BASE) as client:
        for path in ("/", "/login"):
            assert "This is a private test instance." in client.get(path).text, path
        assert sign_in(client).status_code == 200
        assert "test instance" not in client.get("/docs").text  # signed in: not told


@pytest.mark.parametrize(
    ("argv", "env"),
    [
        (["--sign-in", "github"], {"CAIRN_ALLOWED_EMAILS": "ada@example.test"}),
        (["--sign-in", "github", "--allowed-email-domain", "example.test"], {}),
        (["--sign-in", "oidc"],
         {"CAIRN_OIDC_GROUPS_CLAIM": "groups", "CAIRN_OIDC_ALLOWED_GROUPS": "testers"}),
        (["--sign-in", "oidc", "--closed-directory"], {}),
    ],
)  # fmt: skip
def test_any_one_of_the_four_limits_is_enough(tmp_path, monkeypatch, argv, env):
    """ "It needs `--sign-in` and one of four limits: `CAIRN_ALLOWED_EMAILS`,
    `--allowed-email-domain`, or, when your own identity provider is the only one,
    `CAIRN_OIDC_ALLOWED_GROUPS` or `--closed-directory`.\""""
    says(
        "It needs `--sign-in` and one of four limits: `CAIRN_ALLOWED_EMAILS`,"
        " `--allowed-email-domain`, or, when your own identity provider is the only one,"
        " `CAIRN_OIDC_ALLOWED_GROUPS` or `--closed-directory`."
    )
    code, seen, _ = serve(monkeypatch, tmp_path, [*argv, "--test-instance"], env)
    assert code == 0 and TESTERS in seen["login"]


@pytest.mark.parametrize(
    "argv",
    [
        ["--test-instance"],  # no --sign-in
        ["--sign-in", "github", "--test-instance"],  # anyone can get an account there
        ["--sign-in", "oidc", "--test-instance"],  # your own provider, not closed
        # closed, but not the only one: GitHub is still open to anyone
        ["--sign-in", "oidc", "--sign-in", "github", "--closed-directory", "--test-instance"],
    ],
)
def test_without_a_limit_serve_stops_at_start_and_names_the_option(
    tmp_path, monkeypatch, capsys, argv
):
    """ "Without one, `serve` stops at start, and the last line it prints names the option.\""""
    says("Without one, `serve` stops at start, and the last line it prints names the option.")
    code, seen, db = serve(monkeypatch, tmp_path, argv)
    assert code == 2 and seen == {} and not db.exists()
    assert "--test-instance" in capsys.readouterr().err.strip().splitlines()[-1]


def test_the_sign_in_page_says_so_and_links_to_this_guide_before_the_buttons(store):
    """ "The sign-in page says that sign-in is limited to approved testers, and links to this
    guide before the sign-in buttons.\""""
    says(
        "The sign-in page says that sign-in is limited to approved testers, and links to this"
        " guide before the sign-in buttons."
    )
    with TestClient(app(store, test_instance=True), base_url=BASE) as client:
        page = client.get("/login").text
        assert client.get("/docs/run").status_code == 200
    assert TESTERS in page
    assert page.index('href="/docs/run"') < page.index('href="/auth/github"')


@pytest.mark.parametrize("join", ["personal", "default"])
def test_so_does_the_landing_page_shown_without_join_default(store, join):
    """ "So does the landing page, which an install without `--join default` shows to
    visitors.\""""
    says("So does the landing page, which an install without `--join default` shows to visitors.")
    with TestClient(app(store, join=join, test_instance=True), base_url=BASE) as client:
        home = client.get("/", follow_redirects=False)
    if join == "default":
        assert home.status_code == 303 and home.headers["location"].startswith("/login")
        return
    assert home.status_code == 200 and TESTERS in home.text
    first = home.text.index('class="button" href="')
    assert home.text[first:].startswith('class="button" href="/docs/run"')
    assert home.text.index('href="/docs/run"') < home.text.index('href="/login"', first)


def test_in_the_guides_the_link_at_the_top_reads_tester_sign_in(store):
    """ "In the guides, the link at the top reads **Tester sign-in**.\""""
    says("In the guides, the link at the top reads **Tester sign-in**.")
    with TestClient(app(store, test_instance=True), base_url=BASE) as client:
        for path in ("/docs", "/docs/run", "/docs/connect", "/docs/privacy"):
            top = client.get(path).text.split("</header>", 1)[0]
            assert '<a href="/login">Tester sign-in</a>' in top, path
            assert ">Sign in</a>" not in top, path


def test_with_the_wall_the_sign_in_page_links_to_no_guide(store):
    """ "With `--wall`, the sign-in page links to no guide.\""""
    says("With `--wall`, the sign-in page links to no guide.")
    with TestClient(app(store, test_instance=True, wall=True), base_url=BASE) as client:
        page = client.get("/login").text
    assert TESTERS in page and 'href="/docs' not in page


@pytest.mark.parametrize("wall", [False, True])
def test_under_a_brand_a_visitor_sees_the_same_pages_as_without_the_option(tmp_path, wall):
    """ "Under a brand, a visitor sees the same pages as without the option.\""""
    says("Under a brand, a visitor sees the same pages as without the option.")

    def shown(test_instance: bool) -> dict[str, tuple[int, bytes]]:
        store = Store(tmp_path / f"{test_instance}.db")
        built = app(store, GitHubFake("outsider@elsewhere.test"), test_instance=test_instance,
                    wall=wall, brand_name="Juniper")  # fmt: skip
        try:
            with TestClient(built, base_url=BASE) as client:
                answers = {
                    path: client.get(path, follow_redirects=False)
                    for path in ("/", "/login", "/docs", "/docs/run", "/nowhere")
                }
                answers["refused"] = sign_in(client)
        finally:
            store.close()
        return {path: (r.status_code, r.content) for path, r in answers.items()}

    assert shown(True) == shown(False)


@pytest.mark.parametrize("test_instance", [False, True])
def test_the_option_changes_what_visitors_are_told_not_who_can_sign_in(store, test_instance):
    """ "The option changes what visitors are told, not who can sign in.\""""
    says("The option changes what visitors are told, not who can sign in.")
    with TestClient(app(store, test_instance=test_instance), base_url=BASE) as client:
        assert sign_in(client).status_code == 200
    outsider = GitHubFake("brook@elsewhere.test")
    with TestClient(app(store, outsider, test_instance=test_instance), base_url=BASE) as client:
        refused = sign_in(client)
    assert refused.status_code == 403 and f"Refused. {UNLISTED}" in refused.text


@pytest.mark.parametrize(
    "limits",
    [{"allowed_emails": ("ada@example.test",)}, {"allowed_email_domains": ("example.test",)}],
)
def test_an_invitation_does_not_let_in_an_address_the_limits_leave_out(store, limits):
    """ "An invitation does not let in an address that `CAIRN_ALLOWED_EMAILS` and
    `--allowed-email-domain` leave out, so list a tester's address before you invite
    them.\""""
    says(
        "An invitation does not let in an address that `CAIRN_ALLOWED_EMAILS` and"
        " `--allowed-email-domain` leave out, so list a tester's address before you invite them."
    )
    team = store.for_account(store.create_account("Fieldwork").id)
    invitation = team.invite("brook@elsewhere.test")
    http = httpx.AsyncClient(transport=httpx.MockTransport(GitHubFake("brook@elsewhere.test")))
    sign_in_config = SignIn((GitHub("id", "secret"),), http, **limits)
    built = create_app(store, public_url=BASE, sign_in=sign_in_config, test_instance=True)
    with TestClient(built, base_url=BASE) as client:
        refused = sign_in(client, next_path=f"/join/{invitation.token}")
    assert refused.status_code == 403
    assert [open_one.email for open_one in team.list_invites()] == ["brook@elsewhere.test"]
    listed = ("ada@example.test", "brook@elsewhere.test")
    http = httpx.AsyncClient(transport=httpx.MockTransport(GitHubFake("brook@elsewhere.test")))
    sign_in_config = SignIn((GitHub("id", "secret"),), http, allowed_emails=listed)
    built = create_app(store, public_url=BASE, sign_in=sign_in_config, test_instance=True)
    with TestClient(built, base_url=BASE) as client:  # listed: the invitation is taken
        assert sign_in(client, next_path=f"/join/{invitation.token}").status_code == 200
        taken = client.post(
            f"/join/{invitation.token}",
            headers={"sec-fetch-site": "same-origin"},
            follow_redirects=False,
        )
    assert taken.status_code == 303 and team.list_invites() == []


def test_every_sentence_of_the_section_is_pinned_here():
    """No sentence is added to the section without a test above that asks for it."""
    pinned = flat(
        " ".join(
            test.__doc__ or ""
            for name, test in globals().items()
            if name.startswith("test_")
            and name != "test_every_sentence_of_the_section_is_pinned_here"
        )
    )
    for sentence in re.split(r"(?<=\.)\s+", flat(THE_SECTION)):
        assert sentence in pinned, sentence


def test_an_oidc_provider_alone_is_the_only_one_closed_by_groups_or_word(store):
    """`create_app`'s side of "when your own identity provider is the only one": with GitHub
    beside it, the flag is refused whatever closes the OIDC provider."""
    http = httpx.AsyncClient(transport=httpx.MockTransport(GitHubFake()))
    oidc = OIDC("sso", "Acme SSO", ISSUER, "id", "secret")
    create_app(store, public_url=BASE, sign_in=SignIn((oidc,), http), test_instance=True)
    both = SignIn((oidc, GitHub("id", "secret")), http)
    with pytest.raises(ValueError, match="--test-instance"):
        create_app(store, public_url=BASE, sign_in=both, test_instance=True)
