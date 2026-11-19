"""Unit tests for sign-in limited to named people (`SignIn.allowed_emails`,
CAIRN_ALLOWED_EMAILS). They pin what tests/acceptance/test_h8_allowed_emails.py leaves open."""

import base64
import json
import time

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

from cairn import server
from cairn.identity import OIDC, GitHub
from cairn.server import SignIn, create_app
from cairn.store import Store

BASE = "https://cairn.example.test"
ADA = "ada@example.test"


def github(email: str):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == "https://github.com/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_token"})
        if url == "https://api.github.com/user":
            return httpx.Response(200, json={"id": 7, "login": "someone", "name": "Someone"})
        if url == "https://api.github.com/user/emails":
            return httpx.Response(200, json=[{"email": email, "primary": True, "verified": True}])
        return httpx.Response(404)

    return handler


def app_for(store: Store, email: str, **options):
    http = httpx.AsyncClient(transport=httpx.MockTransport(github(email)))
    sign_in = SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=http, **options)
    return create_app(store, public_url=BASE, sign_in=sign_in)


def sign_in(client: TestClient) -> httpx.Response:
    started = client.get("/auth/github", follow_redirects=False)
    state = httpx.URL(started.headers["location"]).params["state"]
    return client.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


def test_a_refusal_by_the_list_alone_says_the_operator_lists_who_gets_in(store):
    app = app_for(store, "brook@example.test", allowed_emails=(ADA,))
    with TestClient(app, base_url=BASE) as client:
        r = sign_in(client)
    assert r.status_code == 403
    assert "This install admits only the people its operator has listed." in r.text
    assert "domain" not in r.text.lower()


def test_a_refusal_with_domains_keeps_the_domain_sentence(store):
    app = app_for(
        store, "brook@example.test", allowed_emails=(ADA,), allowed_email_domains=("acme.example",)
    )
    with TestClient(app, base_url=BASE) as client:
        r = sign_in(client)
    assert r.status_code == 403
    assert "only admits accounts of its organisation" in r.text


def test_the_same_address_twice_in_different_case_is_one_entry(store):
    app = app_for(store, ADA, join="personal", allowed_emails=(ADA, " ADA@Example.TEST"))
    with TestClient(app, base_url=BASE) as client:
        assert sign_in(client).status_code == 200


def test_a_bad_entry_is_named_by_its_position_not_its_text(store):
    with pytest.raises(ValueError) as refusal:
        app_for(store, ADA, allowed_emails=(ADA, "nobody-at-all"))
    message = str(refusal.value)
    assert "entry 2" in message and "nobody" not in message


def test_the_list_is_not_in_the_repr_of_the_sign_in(store):
    sign_in_config = SignIn(
        providers=(GitHub("gh-id", "gh-secret"),), http=httpx.AsyncClient(), allowed_emails=(ADA,)
    )
    assert ADA not in repr(sign_in_config)


OIDC_ENV = {
    "CAIRN_OIDC_ISSUER": "https://id.acme.example",
    "CAIRN_OIDC_CLIENT_ID": "acme-id-from-env",
    "CAIRN_OIDC_CLIENT_SECRET": "acme-secret-from-env",
}


def serve(monkeypatch, tmp_path, argv, env):
    monkeypatch.delenv("CAIRN_ALLOWED_EMAILS", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    db = ["--db", str(tmp_path / "cairn.db"), "--public-url", BASE]
    return server.main(["serve", *db, *argv]), apps


def test_a_list_closes_an_oidc_shared_workspace(tmp_path, monkeypatch, capsys):
    argv = ["--sign-in", "oidc", "--join", "default"]
    code, apps = serve(monkeypatch, tmp_path, argv, OIDC_ENV)
    assert code == 2 and apps == []  # the control: nobody closed it
    capsys.readouterr()
    code, apps = serve(monkeypatch, tmp_path, argv, {**OIDC_ENV, "CAIRN_ALLOWED_EMAILS": ADA})
    assert code == 0 and len(apps) == 1
    said = capsys.readouterr()
    assert ADA not in said.out and ADA not in said.err


def test_serve_accepts_the_same_address_twice_in_different_case(tmp_path, monkeypatch):
    argv = ["--sign-in", "oidc", "--join", "default"]  # only a list closes it here
    env = {**OIDC_ENV, "CAIRN_ALLOWED_EMAILS": f"{ADA}, ADA@EXAMPLE.TEST"}
    code, apps = serve(monkeypatch, tmp_path, argv, env)
    assert code == 0 and len(apps) == 1


# With an identity provider's groups (`OIDC(allowed_groups=...)`), both rules apply.

ISSUER = "https://id.acme.example/pool-1"


def sso_app(store: Store, person: dict, sent: dict, **options):
    """An app behind an OIDC provider that admits the group "staff" only; `person` is who is
    at the browser, and `sent` holds the nonce cairn sent them to the provider with."""

    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == f"{ISSUER}/.well-known/openid-configuration":
            names = ("authorization", "token", "userinfo")
            document = {f"{name}_endpoint": f"{ISSUER}/{name}" for name in names}
            return httpx.Response(200, json={"issuer": ISSUER, **document})
        if url == f"{ISSUER}/token":
            now = int(time.time())
            claims = {"iss": ISSUER, "aud": "acme-id", "sub": person["sub"], "iat": now}
            claims |= {"exp": now + 300, "nonce": sent["nonce"], "groups": person["groups"]}
            id_token = f"{part({'alg': 'RS256'})}.{part(claims)}.c2ln"
            answer = {"access_token": "at-1", "token_type": "bearer", "id_token": id_token}
            return httpx.Response(200, json=answer)
        if url == f"{ISSUER}/userinfo":
            claims = {k: person[k] for k in ("sub", "name", "email")}
            return httpx.Response(200, json={**claims, "email_verified": True})
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OIDC(
        "sso", "Acme SSO", ISSUER, "acme-id", "acme-secret",
        groups_claim="groups", allowed_groups=("staff",),
    )  # fmt: skip
    return create_app(
        store, public_url=BASE, sign_in=SignIn(providers=(provider,), http=http, **options)
    )


@pytest.mark.parametrize(
    ("email", "groups", "status"),
    [
        (ADA, ["staff"], 200),  # listed, and in the group
        (ADA, ["guests"], 403),  # listed, but the provider's group rule still refuses
        ("brook@example.test", ["staff"], 403),  # in the group, but not listed
    ],
)
def test_a_providers_groups_and_the_list_both_apply(store, email, groups, status):
    person = {"sub": "sub-1", "name": "Someone", "email": email, "groups": groups}
    sent: dict = {}
    app = sso_app(store, person, sent, join="personal", allowed_emails=(ADA,))
    with TestClient(app, base_url=BASE) as client:
        started = client.get("/auth/sso", follow_redirects=False)
        asked = httpx.URL(started.headers["location"]).params
        sent["nonce"] = asked["nonce"]
        r = client.get(f"/auth/sso/callback?code=c&state={asked['state']}", follow_redirects=False)
    assert r.status_code == status
    assert ("__Host-cairn_session" in client.cookies) is (status == 200)
    assert email not in r.text
