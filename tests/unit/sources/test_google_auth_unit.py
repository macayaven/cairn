"""Unit tests for cairn.sources.google_auth: how cairn gets a Google access token on a server,
for whatever scopes it is asked for. What tests/acceptance/test_c4_google_meet.py does not
pin. HTTP goes through httpx.MockTransport and the clock is injected. The key pairs are made
when the tests run; every other value is made up."""

import asyncio
import json
import logging
from base64 import urlsafe_b64decode
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding

from cairn.sources import SourceError, holds_secret, source_client
from cairn.sources import google_auth as auth
from support.sources.google_auth import (
    CLIENT_ID,
    EMAIL,
    GOOGLE,
    KEY_ID,
    REFRESH,
    SA_CLIENT_ID,
    SECRET,
    key_file,
    user,
)
from support.sources.google_auth import keys as keys

VAR = "GOOGLE_UNIT_CREDENTIAL"
OLDER = "https://accounts.google.com/o/oauth2/token"
JWT_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
TOKEN = "ya29." + "unit" + "MadeUpAccess" + "Token77"
SUBJECT = "ada@example.test"
CANARY = "unit-" + "token-body-" + "canary"
SCOPES = ["https://www.googleapis.com/auth/calendar.events.readonly"]
NOW = datetime(2026, 11, 13, 12, 0, 30, 750000, tzinfo=UTC)
SOURCES = Path(auth.__file__).parent


def env(doc: object) -> dict[str, str]:
    return {VAR: doc if isinstance(doc, str) else json.dumps(doc)}


def refused(given: dict[str, str]) -> str:
    with pytest.raises(SourceError) as failed:
        auth.load_credential(VAR, given)
    return str(failed.value)


def unb64(segment: str) -> bytes:
    return urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


# Loading a credential


def test_a_key_file_loads_as_a_service_account(keys):
    credential = auth.load_credential(VAR, env(key_file(keys[1])))
    assert isinstance(credential, auth.ServiceAccount)
    assert (credential.variable, credential.client_email, credential.key_id) == (
        VAR,
        EMAIL,
        KEY_ID,
    )
    assert credential.token_url == GOOGLE


def test_an_authorized_user_loads_as_one_persons_refresh_token():
    credential = auth.load_credential(VAR, env(user()))
    assert isinstance(credential, auth.AuthorizedUser)
    assert (credential.variable, credential.client_id) == (VAR, CLIENT_ID)


def test_surrounding_whitespace_in_the_variable_is_ignored():
    credential = auth.load_credential(VAR, {VAR: "\n  " + json.dumps(user()) + "\n"})
    assert isinstance(credential, auth.AuthorizedUser)


def test_a_credential_shows_no_secret_in_its_repr(keys):
    for doc in (key_file(keys[1]), user()):
        shown = repr(auth.load_credential(VAR, env(doc)))
        assert VAR in shown
        for secret in (SECRET, REFRESH, *keys[1].splitlines()[1:-1]):
            assert secret not in shown


def test_the_secrets_are_the_key_and_its_lines_or_the_client_secret_and_refresh_token(keys):
    sa = auth.load_credential(VAR, env(key_file(keys[1])))
    lines = [line for line in keys[1].splitlines() if not line.startswith("-----")]
    assert keys[1].strip() in sa.secrets
    assert all(holds_secret(f"said {line} here", sa.secrets) for line in lines if len(line) >= 16)
    assert all(len(secret) >= 16 for secret in sa.secrets)
    assert not holds_secret(f"{EMAIL} {SA_CLIENT_ID} {KEY_ID}", sa.secrets)
    au = auth.load_credential(VAR, env(user()))
    assert set(au.secrets) == {SECRET, REFRESH}
    assert not holds_secret(CLIENT_ID, au.secrets)


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ({}, f"{VAR} is not set"),
        ({VAR: " \n"}, f"{VAR} is empty"),
        ({VAR: "/etc/cairn/key.json"}, f"{VAR} does not hold JSON"),
        (env([user()]), f"{VAR} holds JSON that is not an object"),
        (env(user(type=None)), f'{VAR} holds a credential without a "type"'),
        (env(user(type="external_account")), f"{VAR} holds a credential of another type"),
        (env(user(type=7)), f"{VAR} holds a credential of another type"),
        (env(user(client_id=None)), f"{VAR} holds an authorized_user without client_id"),
        (env(user(client_secret="")), f"{VAR} holds an authorized_user without client_secret"),
        (env(user(refresh_token=7)), f"{VAR} holds an authorized_user without refresh_token"),
    ],
)
def test_a_credential_that_is_not_one_is_refused_naming_the_variable(given, said):
    message = refused(given)
    assert message.startswith(said), message
    for value in (SECRET, REFRESH, CLIENT_ID, "/etc/cairn", "external_account"):
        assert value not in message


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        ({"private_key": None}, "without private_key"),
        ({"client_email": None}, "without client_email"),
        ({"client_email": ""}, "without client_email"),
        ({"private_key_id": 7}, "whose private_key_id is not text"),
        ({"token_uri": 7}, "whose token_uri is not text"),
    ],
)
def test_a_key_file_without_what_it_needs_is_refused(keys, changes, said):
    message = refused(env(key_file(keys[1], **changes)))
    assert message.startswith(f"{VAR} holds a service_account key file {said}"), message
    assert EMAIL not in message and KEY_ID not in message


@pytest.mark.parametrize("bad", ["broken", "encrypted", "not rsa"])
def test_a_private_key_that_does_not_load_as_an_rsa_key_is_refused(keys, bad):
    if bad == "broken":
        label = "PRIVATE" + " KEY"
        pem = f"-----BEGIN {label}-----\n" + "unitNotAKey" * 6 + f"\n-----END {label}-----\n"
    elif bad == "encrypted":
        pem = (
            keys[0]
            .private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(b"unit-passphrase"),
            )
            .decode()
        )
    else:
        pem = (
            ec.generate_private_key(ec.SECP256R1())
            .private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            .decode()
        )
    message = refused(env(key_file(pem)))
    assert message.startswith(f"{VAR} holds a service_account key file whose private_key")
    assert "unitNotAKey" not in message and "ENCRYPTED" not in message
    assert not any(line in message for line in pem.splitlines()[1:-1])


# Where a token request may go


@pytest.mark.parametrize(
    ("token_uri", "goes_to"),
    [
        (GOOGLE, GOOGLE),
        (OLDER, GOOGLE),
        (None, GOOGLE),  # absent: Google's endpoint, where every assertion goes
        ("http://127.0.0.1:8765/token", "http://127.0.0.1:8765/token"),
        ("https://localhost/token", "https://localhost/token"),
        ("http://localhost:9/o/oauth2/token", "http://localhost:9/o/oauth2/token"),
    ],
)
def test_a_token_uri_leads_to_googles_endpoint_or_to_this_machine(keys, token_uri, goes_to):
    credential = auth.load_credential(VAR, env(key_file(keys[1], token_uri=token_uri)))
    assert credential.token_url == goes_to


@pytest.mark.parametrize(
    "token_uri",
    [
        "https://oauth2.googleapis.com/token/",
        "https://OAUTH2.googleapis.com/token",
        "https://oauth2.googleapis.com/token?x=1",
        "https://oauth2.googleapis.com:443/token",
        "https://oauth2.googleapis.com.example.test/token",
        "https://user@127.0.0.1/token",
        "http://127.0.0.1:8765/token?next=https://example.test",
        "http://127.0.0.1:8765/token#x",
        "ftp://127.0.0.1/token",
        "http://[::1]:8765/token",
        "http://127.0.0.2/token",
        "",
    ],
)
def test_any_other_token_uri_is_refused_without_repeating_it(keys, token_uri):
    message = refused(env(key_file(keys[1], token_uri=token_uri)))
    assert message.startswith(f"{VAR} holds a service_account key file whose token_uri")
    assert token_uri not in message or not token_uri


# The token request


class Stand:
    """A token endpoint that answers what it is told and keeps every request."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers) or [
            httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3599})
        ]
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def ask(self, credential, scopes=SCOPES, subject=None, http=None):
        async def asking():
            client = http or httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
            return await auth.access_token(
                credential, scopes=scopes, subject=subject, http=client, clock=lambda: NOW
            )

        return asyncio.run(asking())

    def form(self) -> dict[str, str]:
        (request,) = self.requests
        return dict(parse_qsl(request.content.decode(), strict_parsing=True))


def test_a_service_account_posts_a_signed_jwt_and_nothing_else(keys):
    stand = Stand()
    credential = auth.load_credential(VAR, env(key_file(keys[1])))
    scopes = [*SCOPES, "https://www.googleapis.com/auth/gmail.readonly"]
    assert stand.ask(credential, scopes, SUBJECT) == TOKEN
    (request,) = stand.requests
    assert (request.method, str(request.url)) == ("POST", GOOGLE)
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert request.headers["accept"] == "application/json"
    assert "authorization" not in request.headers
    form = stand.form()
    assert set(form) == {"grant_type", "assertion"} and form["grant_type"] == JWT_GRANT

    head, body, signature = form["assertion"].split(".")
    assert "=" not in form["assertion"]  # base64url without padding
    assert json.loads(unb64(head)) == {"alg": "RS256", "typ": "JWT", "kid": KEY_ID}
    iat = int(NOW.timestamp())
    assert json.loads(unb64(body)) == {
        "iss": EMAIL,
        "sub": SUBJECT,
        "scope": " ".join(scopes),
        "aud": GOOGLE,
        "iat": iat,
        "exp": iat + 3600,
    }
    keys[0].public_key().verify(
        unb64(signature), f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )


def test_without_a_subject_the_account_acts_as_itself_and_without_a_key_id_no_kid(keys):
    stand = Stand()
    credential = auth.load_credential(VAR, env(key_file(keys[1], private_key_id=None)))
    stand.ask(credential, SCOPES, None)
    head, body, _ = stand.form()["assertion"].split(".")
    assert json.loads(unb64(head)) == {"alg": "RS256", "typ": "JWT"}
    assert "sub" not in json.loads(unb64(body))


def test_a_key_file_naming_the_older_address_posts_to_googles_endpoint(keys):
    stand = Stand()
    credential = auth.load_credential(VAR, env(key_file(keys[1], token_uri=OLDER)))
    stand.ask(credential, SCOPES, SUBJECT)
    (request,) = stand.requests
    assert str(request.url) == GOOGLE
    assert json.loads(unb64(stand.form()["assertion"].split(".")[1]))["aud"] == GOOGLE


def test_a_refresh_token_posts_its_four_fields_to_googles_endpoint_whatever_the_scopes():
    for scopes in (SCOPES, ["https://www.googleapis.com/auth/documents.readonly"] * 2):
        stand = Stand()
        assert stand.ask(auth.load_credential(VAR, env(user())), scopes) == TOKEN
        (request,) = stand.requests
        assert (request.method, str(request.url)) == ("POST", GOOGLE)
        assert stand.form() == {
            "client_id": CLIENT_ID,
            "client_secret": SECRET,
            "grant_type": "refresh_token",
            "refresh_token": REFRESH,
        }


def test_a_subject_with_a_refresh_token_is_refused_before_anything_is_sent():
    stand = Stand()
    with pytest.raises(SourceError, match="subject") as failed:
        stand.ask(auth.load_credential(VAR, env(user())), SCOPES, SUBJECT)
    assert stand.requests == [] and SUBJECT not in str(failed.value)


@pytest.mark.parametrize(
    "scopes", [[], "https://www.googleapis.com/auth/x", ["a b"], [""], [7], ("x",)]
)
def test_scopes_are_a_list_of_scope_strings_or_nothing_is_sent(keys, scopes):
    stand = Stand()
    with pytest.raises(ValueError, match="scopes"):
        stand.ask(auth.load_credential(VAR, env(key_file(keys[1]))), scopes)
    assert stand.requests == []


def test_google_auth_holds_no_scope_and_nothing_about_one_api():
    text = (SOURCES / "google_auth.py").read_text()
    text = text.replace("tests/acceptance/test_c4_google_meet.py", "")  # the file it is frozen by
    assert "googleapis.com/auth/" not in text
    assert "meet" not in text.lower() and "calendar" not in text.lower()


# When the token endpoint says no


def refusal(stand: Stand, credential) -> str:
    with pytest.raises(SourceError) as failed:
        stand.ask(
            credential, SCOPES, SUBJECT if isinstance(credential, auth.ServiceAccount) else None
        )
    return str(failed.value)


def answered(status: int, error: str) -> httpx.Response:
    body = {"error": error, "error_description": CANARY, "error_uri": f"https://{CANARY}.test/"}
    return httpx.Response(status, json=body)


SA_SAID = {
    "unauthorized_client": ("delegation", "client ID"),
    "access_denied": ("delegation", "scope"),
    "invalid_grant": ("clock", "subject", "key"),
    "admin_policy_enforced": ("policy",),
    "invalid_client": ("client",),
    "deleted_client": ("deleted", "30 days"),
    "disabled_client": ("disabled",),
    "org_internal": ("organization",),
    "invalid_scope": ("scope",),
}
USER_SAID = {
    "invalid_grant": ("revoked", "expired", "authorise again"),
    "invalid_client": ("client",),
    "deleted_client": ("deleted",),
    "admin_policy_enforced": ("policy",),
    "org_internal": ("organization",),
}


@pytest.mark.parametrize("status", [400, 401, 403])
def test_each_code_from_the_notes_has_its_own_sentence_for_a_service_account(keys, status):
    credential = auth.load_credential(VAR, env(key_file(keys[1])))
    said = {}
    for error, words in SA_SAID.items():
        message = refusal(Stand(answered(status, error)), credential)
        assert message.startswith(f"Google refused the token request for {VAR}: "), message
        assert all(word in message for word in words), (error, message)
        assert CANARY not in message and EMAIL not in message and SUBJECT not in message
        said[error] = message
    assert len(set(said.values())) == len(said)


@pytest.mark.parametrize("status", [400, 401])
def test_each_code_from_the_notes_has_its_own_sentence_for_a_refresh_token(status):
    credential = auth.load_credential(VAR, env(user()))
    said = {}
    for error, words in USER_SAID.items():
        message = refusal(Stand(answered(status, error)), credential)
        assert message.startswith(f"Google refused the token request for {VAR}: "), message
        assert all(word in message for word in words), (error, message)
        assert CANARY not in message and CLIENT_ID not in message
        said[error] = message
    assert len(set(said.values())) == len(said)


def test_the_three_refusals_an_operator_must_tell_apart_differ(keys):
    sa = auth.load_credential(VAR, env(key_file(keys[1])))
    au = auth.load_credential(VAR, env(user()))
    said = {
        refusal(Stand(answered(400, "access_denied")), sa),
        refusal(Stand(answered(400, "invalid_grant")), sa),
        refusal(Stand(answered(400, "invalid_grant")), au),
    }
    assert len(said) == 3


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        (answered(400, "server_error"), "Google refused the token request for {var} (HTTP 400)"),
        (answered(400, CANARY), "Google refused the token request for {var} (HTTP 400)"),
        (httpx.Response(400, text=CANARY), "Google refused the token request for {var} (HTTP 400)"),
        (httpx.Response(503, text=CANARY), "Google's token endpoint answered HTTP 503"),
        (httpx.Response(302, headers={"location": f"https://{CANARY}.test/"}), "HTTP 302"),
        (httpx.Response(200, text=f"<p>{CANARY}</p>"), "not the documented shape"),
        (httpx.Response(200, json=[CANARY]), "not the documented shape"),
        (httpx.Response(200, json={"token_type": "Bearer", "x": CANARY}), "no access token"),
        (httpx.Response(200, json={"access_token": 7}), "no access token"),
        (httpx.Response(200, json={"access_token": "short"}), "no access token"),
        (httpx.Response(200, json={"access_token": "ya29. two words here"}), "no access token"),
        (httpx.ReadTimeout("timed out"), "cannot reach Google's token endpoint: ReadTimeout"),
        (httpx.ConnectError(CANARY), "cannot reach Google's token endpoint: ConnectError"),
    ],
)
def test_any_other_answer_fails_in_words_without_the_body(answer, said):
    stand = Stand(answer)
    message = refusal(stand, auth.load_credential(VAR, env(user())))
    assert said.format(var=VAR) in message, message
    assert CANARY not in message and "short" not in message
    assert len(stand.requests) == 1  # a redirect is not followed


def test_an_answer_past_its_bound_is_not_read():
    stand = Stand(httpx.Response(200, json={"access_token": TOKEN, "pad": "x" * 70_000}))
    message = refusal(stand, auth.load_credential(VAR, env(user())))
    assert "larger than this server reads" in message and TOKEN not in message


def test_no_log_line_holds_the_key_the_assertion_or_a_token(keys, caplog):
    stand = Stand()
    root = logging.getLogger()
    level = root.level
    root.setLevel(logging.DEBUG)
    try:
        with caplog.at_level(logging.DEBUG):
            http = source_client(transport=httpx.MockTransport(stand.handler))
            stand.ask(auth.load_credential(VAR, env(key_file(keys[1]))), SCOPES, SUBJECT, http)
            stand.requests.clear()
            stand.ask(auth.load_credential(VAR, env(user())), SCOPES, None, http)
    finally:
        root.setLevel(level)
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (TOKEN, SECRET, REFRESH, "eyJ", *keys[1].splitlines()[1:-1]):
        assert secret not in logged
