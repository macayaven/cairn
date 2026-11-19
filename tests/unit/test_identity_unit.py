"""Unit tests for the sign-in providers (cairn.identity), against mocked HTTP. They pin what
tests/acceptance/test_h4_signin.py leaves open: how each provider fails, and the ID-token and
discovery checks the acceptance tests do not forge."""

import asyncio
import base64
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from cairn import identity
from cairn.identity import OIDC, GitHub, Google, Profile, SignInRefused

REDIRECT = "https://cairn.example.test/auth/x/callback"
ISSUER = "https://id.acme.example/pool-1"
DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/authorize",
    "token_endpoint": f"{ISSUER}/token",
    "userinfo_endpoint": f"{ISSUER}/userinfo",
}


def run(provider, handler, step="finish", **kwargs):
    """Call one step of a provider with every request going to `handler`."""

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            if step == "start":
                return await provider.authorize_url(http, REDIRECT, "the-state", "the-nonce")
            return await provider.finish(http, kwargs.get("code", "c"), REDIRECT, "the-nonce")

    return asyncio.run(go())


def refused(provider, handler) -> int:
    with pytest.raises(SignInRefused) as caught:
        run(provider, handler)
    return caught.value.status


def query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


# GitHub


def github_handler(user=None, emails=None, token_answer=None):
    user = {"id": 42, "login": "ada", "name": "Ada"} if user is None else user
    if emails is None:
        emails = [{"email": "ada@example.test", "primary": True, "verified": True}]
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        url = str(request.url).split("?")[0]
        if url == "https://github.com/login/oauth/access_token":
            return token_answer or httpx.Response(200, json={"access_token": "gho_x"})
        if request.headers.get("authorization") != "Bearer gho_x":
            return httpx.Response(401)
        if url == "https://api.github.com/user":
            return httpx.Response(200, json=user)
        if url == "https://api.github.com/user/emails":
            return httpx.Response(200, json=emails)
        return httpx.Response(404)

    handler.seen = seen
    return handler


def test_github_starts_with_its_authorize_url_and_never_the_secret():
    url = run(GitHub("gh-id", "gh-secret"), github_handler(), "start")
    assert url.startswith("https://github.com/login/oauth/authorize?")
    sent = query(url)
    assert sent == {
        "client_id": "gh-id",
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": "read:user user:email",
        "state": "the-state",
    }
    assert "gh-secret" not in url and "gh-secret" not in repr(GitHub("gh-id", "gh-secret"))


def test_github_reads_the_primary_verified_email_and_falls_back_to_the_login():
    handler = github_handler(
        user={"id": 42, "login": "ada", "name": None},
        emails=[
            {"email": "old@example.test", "primary": False, "verified": True},
            {"email": "ada@example.test", "primary": True, "verified": True},
        ],
    )
    profile = run(GitHub("gh-id", "gh-secret"), handler)
    assert profile == Profile(subject="42", email="ada@example.test", name="ada")
    token_request = handler.seen[0]
    assert token_request.headers["accept"] == "application/json"
    assert form(token_request) == {
        "grant_type": "authorization_code",
        "code": "c",
        "redirect_uri": REDIRECT,
        "client_id": "gh-id",
        "client_secret": "gh-secret",
    }


@pytest.mark.parametrize(
    ("emails", "status"),
    [
        ([{"email": "ada@example.test", "primary": True, "verified": False}], 403),
        ([{"email": "ada@example.test", "primary": False, "verified": True}], 403),
        ([], 403),
        ({"not": "a list"}, 502),
    ],
)
def test_github_refuses_without_a_verified_primary_email(emails, status):
    assert refused(GitHub("gh-id", "gh-secret"), github_handler(emails=emails)) == status


def test_github_answering_an_error_with_200_is_a_failed_sign_in():
    error = httpx.Response(200, json={"error": "bad_verification_code"})
    assert refused(GitHub("gh-id", "gh-secret"), github_handler(token_answer=error)) == 502


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(500, json={}),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(200, json={"access_token": 7}),
    ],
)
def test_a_provider_that_answers_badly_is_a_failed_sign_in_not_a_crash(answer):
    assert refused(GitHub("gh-id", "gh-secret"), github_handler(token_answer=answer)) == 502


def test_a_provider_that_cannot_be_reached_is_a_failed_sign_in():
    def handler(request):
        raise httpx.ConnectError("unreachable", request=request)

    assert refused(GitHub("gh-id", "gh-secret"), handler) == 502


def test_github_needs_a_numeric_user_id():
    handler = github_handler(user={"id": True, "login": "ada", "name": "Ada"})
    assert refused(GitHub("gh-id", "gh-secret"), handler) == 502


# Google


def google_handler(claims):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == "https://oauth2.googleapis.com/token":
            return httpx.Response(200, json={"access_token": "ya29"})
        if url == "https://openidconnect.googleapis.com/v1/userinfo":
            return httpx.Response(200, json=claims)
        return httpx.Response(404)

    return handler


def test_google_reads_userinfo_and_the_hosted_domain():
    google = Google("g-id", "g-secret")
    claims = {"sub": "1", "email": "c@acme.example", "email_verified": "true", "hd": "acme.example"}
    profile = run(google, google_handler({**claims, "name": "Clara"}))
    assert profile == Profile(
        subject="1", email="c@acme.example", name="Clara", organisation="acme.example"
    )
    assert google.within(profile, ("acme.example",))
    assert not google.within(profile.model_copy(update={"organisation": None}), ("acme.example",))
    assert not google.within(profile, ("other.example",))
    start = query(run(google, google_handler(claims), "start"))
    assert start["scope"] == "openid email profile" and "g-secret" not in str(start)


@pytest.mark.parametrize("flag", [False, "false", None, "yes", 1])
def test_google_refuses_an_email_it_has_not_verified(flag):
    claims = {"sub": "1", "email": "c@acme.example", "name": "Clara"}
    if flag is not None:
        claims["email_verified"] = flag
    assert refused(Google("g-id", "g-secret"), google_handler(claims)) == 403


def test_a_profile_without_an_email_or_subject_is_refused():
    assert refused(Google("g-id", "g-secret"), google_handler({"email_verified": True})) == 502


# OIDC


def jwt(claims: dict) -> str:
    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256'})}.{part(claims)}.c2ln"


def id_claims(**changes):
    claims = {
        "iss": ISSUER,
        "aud": "acme-id",
        "sub": "u-1",
        "exp": int(time.time()) + 300,
        "nonce": "the-nonce",
    }
    return {**claims, **changes}


def oidc_handler(claims=None, userinfo=None, discovery=None, id_token=...):
    calls = []
    claims = id_claims() if claims is None else claims
    if userinfo is None:
        userinfo = {"sub": "u-1", "email": "c@acme.example", "email_verified": True, "name": "C"}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        calls.append(url)
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return httpx.Response(200, json=discovery or DISCOVERY)
        if url == DISCOVERY["token_endpoint"]:
            answer = {"access_token": "at"}
            if id_token is not None:
                answer["id_token"] = jwt(claims) if id_token is ... else id_token
            return httpx.Response(200, json=answer)
        if url == DISCOVERY["userinfo_endpoint"]:
            return httpx.Response(200, json=userinfo)
        return httpx.Response(404)

    handler.calls = calls
    return handler


def acme(**options):
    return OIDC("acme", "Acme SSO", ISSUER, "acme-id", "acme-secret", **options)


def test_oidc_fetches_discovery_once_when_first_used_and_sends_a_nonce():
    provider, handler = acme(), oidc_handler()
    assert handler.calls == []  # making a provider calls nothing
    sent = query(run(provider, handler, "start"))
    assert sent["nonce"] == "the-nonce" and sent["scope"] == "openid email profile"
    profile = run(provider, handler)
    assert profile == Profile(subject="u-1", email="c@acme.example", name="C", second_factor=False)
    run(provider, handler)
    assert handler.calls.count(f"{ISSUER}/.well-known/openid-configuration") == 1
    assert "acme-secret" not in repr(provider)


@pytest.mark.parametrize(
    "discovery",
    [
        {**DISCOVERY, "issuer": "https://id.evil.example/pool-1"},
        {k: v for k, v in DISCOVERY.items() if k != "token_endpoint"},
        {**DISCOVERY, "token_endpoint": "http://id.acme.example/token"},
        # an https issuer never sends its secrets or tokens over plain http, loopback or not
        {**DISCOVERY, "token_endpoint": "http://127.0.0.1:9000/token"},
        {**DISCOVERY, "userinfo_endpoint": "http://localhost/userinfo"},
        {**DISCOVERY, "authorization_endpoint": "http://[::1]:8080/authorize"},
    ],
)
def test_oidc_refuses_discovery_that_is_not_its_issuers(discovery):
    with pytest.raises(SignInRefused) as caught:
        run(acme(), oidc_handler(discovery=discovery), "start")
    assert caught.value.status == 502


def test_oidc_accepts_its_client_among_several_audiences():
    claims = id_claims(aud=["other", "acme-id"], azp="acme-id")
    assert run(acme(), oidc_handler(claims)).subject == "u-1"


@pytest.mark.parametrize(
    "token",
    [
        None,  # no ID token at all
        "not-a-jwt",
        "a.!!!.c",
        jwt(id_claims(aud=["other"])),
        jwt(id_claims(aud=["other", "acme-id"], azp="other")),
        jwt(id_claims(exp="soon")),
        jwt({k: v for k, v in id_claims().items() if k != "nonce"}),
        jwt(id_claims(sub="u-2")),  # userinfo speaks for someone else
    ],
)
def test_oidc_refuses_an_id_token_that_is_not_this_sign_ins_own(token):
    assert refused(acme(), oidc_handler(id_token=token)) == 400


def test_oidc_reads_a_string_email_verified_flag_and_groups_from_either_place():
    staff = acme(groups_claim="groups", allowed_groups=("staff",))
    userinfo = {
        "sub": "u-1",
        "email": "c@acme.example",
        "email_verified": "true",
        "groups": "staff",
    }
    assert run(staff, oidc_handler(userinfo=userinfo)).email == "c@acme.example"
    token_only = {k: v for k, v in userinfo.items() if k != "groups"}
    handler = oidc_handler(claims=id_claims(groups=["x", "staff"]), userinfo=token_only)
    assert run(staff, handler).subject == "u-1"
    outsider = {**token_only, "groups": ["students"]}
    assert refused(staff, oidc_handler(userinfo=outsider)) == 403
    unverified = {**userinfo, "email_verified": "false"}
    assert refused(staff, oidc_handler(userinfo=unverified)) == 403


@pytest.mark.parametrize(
    ("args", "options"),
    [
        (("acme", "Acme", "http://id.acme.example", "id", "s"), {}),
        (("acme", "Acme", "not a url", "id", "s"), {}),
        (("Not A Name", "Acme", ISSUER, "id", "s"), {}),
        (("acme", "Acme", ISSUER, "id", "s"), {"allowed_groups": ("staff",)}),
        (("acme", "", ISSUER, "id", "s"), {}),
    ],
)
def test_an_oidc_provider_is_refused_when_its_settings_cannot_work(args, options):
    with pytest.raises(ValueError):
        OIDC(*args, **options)


def test_an_oidc_issuer_may_be_http_on_this_machine_for_development():
    assert OIDC("dev", "Dev", "http://127.0.0.1:8080/realms/x", "id", "s").name == "dev"


def test_an_http_loopback_issuer_may_name_http_loopback_endpoints():
    local = "http://127.0.0.1:8080/realms/x"
    discovery = {
        "issuer": local,
        "authorization_endpoint": f"{local}/authorize",
        "token_endpoint": f"{local}/token",
        "userinfo_endpoint": "https://id.acme.example/userinfo",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == f"{local}/.well-known/openid-configuration":
            return httpx.Response(200, json=discovery)
        return httpx.Response(404)

    url = run(OIDC("dev", "Dev", local, "id", "s"), handler, "start")
    assert url.startswith(f"{local}/authorize?")


def test_only_the_public_providers_are_open_to_anyone():
    assert GitHub("i", "s").open_to_anyone and Google("i", "s").open_to_anyone
    assert not acme().open_to_anyone
    assert [p.name for p in (GitHub("i", "s"), Google("i", "s"), acme())] == [
        "github",
        "google",
        "acme",
    ]
    assert identity.email_domain("A@Example.TEST") == "example.test"


# An email counts only where the same answer verifies it


@pytest.mark.parametrize(
    ("in_token", "in_userinfo"),
    [
        # the ID token verifies its own flag, userinfo brings an address it never verified
        ({"email_verified": True}, {"email": "c@acme.example"}),
        # the ID token carries an address, userinfo carries only the flag
        ({"email": "c@acme.example"}, {"email_verified": True}),
        # each carries what the other lacks, as strings
        ({"email": "c@acme.example", "email_verified": "false"}, {"email_verified": "true"}),
    ],
)
def test_oidc_never_pairs_one_answers_email_with_the_others_flag(in_token, in_userinfo):
    claims = id_claims(**in_token)
    userinfo = {"sub": "u-1", "name": "C", **in_userinfo}
    assert refused(acme(), oidc_handler(claims=claims, userinfo=userinfo)) == 403


def test_oidc_refuses_two_answers_that_name_different_addresses():
    claims = id_claims(email="c@acme.example", email_verified=True)
    userinfo = {"sub": "u-1", "email": "mallory@evil.example", "email_verified": True}
    assert refused(acme(), oidc_handler(claims=claims, userinfo=userinfo)) == 400
    unverified = {"sub": "u-1", "email": "mallory@evil.example"}
    assert refused(acme(), oidc_handler(claims=claims, userinfo=unverified)) == 400


def test_oidc_takes_the_address_from_whichever_answer_verified_it():
    token_verified = id_claims(email="C@Acme.example", email_verified=True)
    same_unverified = {"sub": "u-1", "email": "c@acme.example", "name": "C"}
    profile = run(acme(), oidc_handler(claims=token_verified, userinfo=same_unverified))
    assert profile.email == "C@Acme.example"  # the one the ID token verified
    only_token = {"sub": "u-1", "name": "C"}
    assert run(acme(), oidc_handler(claims=token_verified, userinfo=only_token)).email == (
        "C@Acme.example"
    )
    both = {"sub": "u-1", "email": "c@acme.example", "email_verified": "true"}
    assert run(acme(), oidc_handler(claims=token_verified, userinfo=both)).email == (
        "c@acme.example"
    )


def test_google_reads_the_email_and_its_flag_from_userinfo_alone():
    """Google's path has no mix-up to make: an ID token beside the access token is ignored,
    so its claims can never vouch for userinfo's address."""
    verified_token = jwt({"sub": "1", "email": "c@acme.example", "email_verified": True})

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == "https://oauth2.googleapis.com/token":
            return httpx.Response(200, json={"access_token": "ya29", "id_token": verified_token})
        if url == "https://openidconnect.googleapis.com/v1/userinfo":
            return httpx.Response(200, json={"sub": "1", "email": "c@acme.example"})
        return httpx.Response(404)

    assert refused(Google("g-id", "g-secret"), handler) == 403
