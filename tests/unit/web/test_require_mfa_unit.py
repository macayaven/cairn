"""`serve --require-mfa`: only with sign-in providers that state how the person signed in
(OpenID Connect's `amr` or `acr` in the ID token). A sign-in whose ID token shows no second
factor is refused in one sentence, and no session, person or cookie is made. With a provider
that does not state it (GitHub, Google as cairn reads it), the server does not start.
Every name is made up."""

import base64
import json

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

from cairn import identity, server
from cairn.identity import OIDC, GitHub, Google
from cairn.server import SignIn, create_app
from cairn.store import Store
from cairn.web.signin import NO_SECOND_FACTOR
from support.cookie_names import session_cookie

BASE = "https://cairn.example.test"
ISSUER = "https://id.fable.example/pool-1"
NOW = 4_102_444_800  # 2100-01-01, what the provider's clock and cairn's read
PAPE_MFA = "http://schemas.openid.net/pape/policies/2007/06/multi-factor"


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(identity, "_clock", lambda: NOW)


@pytest.fixture
def store(tmp_path):
    made = Store(tmp_path / "cairn.db")
    yield made
    made.close()


def part(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


def sso(id_claims: dict, userinfo: dict | None = None):
    """An OIDC provider whose ID token carries `id_claims` besides the usual ones."""
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == f"{ISSUER}/.well-known/openid-configuration":
            names = ("authorization", "token", "userinfo")
            document = {f"{name}_endpoint": f"{ISSUER}/{name}" for name in names}
            return httpx.Response(200, json={"issuer": ISSUER, **document})
        if url == f"{ISSUER}/token":
            claims = {"iss": ISSUER, "aud": "fable-id", "sub": "sub-ada", "exp": NOW + 300}
            claims |= {"nonce": sent["nonce"], **id_claims}
            id_token = f"{part({'alg': 'RS256'})}.{part(claims)}.c2ln"
            return httpx.Response(200, json={"access_token": "at-1", "id_token": id_token})
        if url == f"{ISSUER}/userinfo":
            claims = {"sub": "sub-ada", "name": "Ada", "email": "ada@fable.example"}
            return httpx.Response(200, json={**claims, "email_verified": True, **(userinfo or {})})
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OIDC("sso", "Fable SSO", ISSUER, "fable-id", "fable-secret"), http, sent


def sign_in(store, id_claims: dict, userinfo: dict | None = None, **options):
    provider, http, sent = sso(id_claims, userinfo)
    app = create_app(
        store, public_url=BASE, sign_in=SignIn(providers=(provider,), http=http, **options)
    )
    client = TestClient(app, base_url=BASE)
    started = client.get("/auth/sso", follow_redirects=False)
    asked = httpx.URL(started.headers["location"]).params
    sent["nonce"] = asked["nonce"]
    back = client.get(f"/auth/sso/callback?code=c&state={asked['state']}", follow_redirects=False)
    return back


def rows(store, table: str) -> int:
    with store._read() as con:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def session_cookies(response) -> list[str]:
    return [c for c in response.headers.get_list("set-cookie") if f"{session_cookie()}=" in c]


@pytest.mark.parametrize(
    "claims",
    [{"amr": ["pwd", "mfa"]}, {"amr": ["mfa"]}, {"acr": PAPE_MFA}],
    ids=["pwd-and-mfa", "mfa", "pape-multi-factor"],
)
def test_a_sign_in_with_a_second_factor_goes_through(store, claims):
    back = sign_in(store, claims, require_mfa=True)
    assert back.status_code == 200, back.text
    assert session_cookies(back)


@pytest.mark.parametrize(
    "claims",
    [
        {},
        {"amr": ["pwd"]},
        {"amr": "mfa"},  # amr is an array; a bare string is not read
        {"amr": ["pwd", "MFA "]},  # exact values only
        {"amr": ["pwd", "otp-mfa"]},
        {"acr": "1"},
        {"acr": "urn:fable:loa:2fa"},  # an acr only the provider knows the meaning of
        {"acr": ["http://schemas.openid.net/pape/policies/2007/06/multi-factor"]},  # not a string
    ],
    ids=["none", "pwd", "string", "case", "substring", "acr-1", "acr-own", "acr-list"],
)
def test_a_sign_in_without_a_second_factor_is_refused_and_makes_nothing(store, claims):
    before = (rows(store, "users"), rows(store, "sessions"))
    back = sign_in(store, claims, require_mfa=True)
    assert back.status_code == 403
    assert NO_SECOND_FACTOR in back.text and "@" not in NO_SECOND_FACTOR
    assert NO_SECOND_FACTOR.count(". ") == 0 and NO_SECOND_FACTOR.endswith(".")
    assert session_cookies(back) == []
    assert (rows(store, "users"), rows(store, "sessions")) == before


def test_a_second_factor_only_userinfo_states_does_not_count(store):
    back = sign_in(store, {"amr": ["pwd"]}, userinfo={"amr": ["pwd", "mfa"], "acr": PAPE_MFA},
                   require_mfa=True)  # fmt: skip
    assert back.status_code == 403 and NO_SECOND_FACTOR in back.text


def test_without_the_option_nothing_is_asked_of_the_token(store):
    assert sign_in(store, {"amr": ["pwd"]}).status_code == 200


def test_the_profile_says_whether_the_id_token_shows_a_second_factor():
    assert identity.second_factor({"amr": ["pwd", "mfa"]}) is True
    assert identity.second_factor({"acr": PAPE_MFA}) is True
    assert identity.second_factor({"amr": ["pwd"], "acr": "0"}) is False
    assert identity.second_factor({}) is False


@pytest.mark.parametrize(
    "providers",
    [
        (GitHub("gh-id", "gh-secret"),),
        (Google("g-id", "g-secret"),),
        (OIDC("sso", "Fable SSO", ISSUER, "fable-id", "fable-secret"), GitHub("a", "b")),
    ],
    ids=["github", "google", "oidc-and-github"],
)
def test_a_provider_that_does_not_state_it_stops_the_start(store, providers):
    sign_in_config = SignIn(providers=providers, http=httpx.AsyncClient(), require_mfa=True)
    with pytest.raises(ValueError, match="require-mfa") as refused:
        create_app(store, public_url=BASE, sign_in=sign_in_config)
    said = str(refused.value)
    for provider in providers:
        assert (provider.name in said) is (not isinstance(provider, OIDC))


def test_serve_refuses_the_option_with_github_and_names_why(tmp_path, monkeypatch, capsys):
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    monkeypatch.setenv("CAIRN_GITHUB_CLIENT_ID", "gh-id")
    monkeypatch.setenv("CAIRN_GITHUB_CLIENT_SECRET", "gh-made-up")
    argv = ["serve", "--db", str(tmp_path / "cairn.db"), "--public-url", BASE]
    assert server.main([*argv, "--sign-in", "github", "--require-mfa"]) == 2
    err = capsys.readouterr().err
    assert "--require-mfa" in err and "github" in err and "second factor" in err
    assert apps == []


def test_serve_refuses_the_option_without_sign_in(tmp_path, capsys):
    argv = ["serve", "--db", str(tmp_path / "cairn.db"), "--require-mfa"]
    assert server.main(argv) == 2
    assert "--require-mfa needs --sign-in" in capsys.readouterr().err


def test_serve_takes_the_option_with_oidc(tmp_path, monkeypatch):
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    for name, value in {"CAIRN_OIDC_ISSUER": ISSUER, "CAIRN_OIDC_CLIENT_ID": "fable-id",
                        "CAIRN_OIDC_CLIENT_SECRET": "fable-made-up"}.items():  # fmt: skip
        monkeypatch.setenv(name, value)
    argv = ["serve", "--db", str(tmp_path / "cairn.db"), "--public-url", BASE]
    assert server.main([*argv, "--sign-in", "oidc", "--require-mfa"]) == 0
    assert len(apps) == 1
