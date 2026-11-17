"""How cairn gets a Google access token on a server, for whatever scopes it is asked for.

Contract (frozen by tests/acceptance/test_c4_google_meet.py; Google's request and response
shapes are recorded in docs/google-auth.md, with what Google's pages do not state marked as
such and what cairn decided marked **[cairn]**). Nothing here names an API or a scope: a
source passes its scopes, and every Google source signs in through this module.

The seam
- `load_credential(variable, env)`: the credential held in the environment variable
  `variable` of `env` (a mapping): the text of one JSON document, never a path to a file.
  Its `type` says which kind it is:
  - "service_account" (ServiceAccount): Google's key file as downloaded. cairn reads
    `private_key` (an unencrypted RSA key in PEM), `client_email`, `private_key_id` (the
    JWT's `kid`, when the file has one) and `token_uri`, and ignores the other fields.
  - "authorized_user" (AuthorizedUser): `client_id`, `client_secret` and `refresh_token`,
    one person's own authorisation.
  A missing or blank variable, text that is not a JSON object, another type, a field that
  is missing, empty or not text, or a key that does not load: SourceError that names the
  variable and what is wrong, never a field's value.
- `await access_token(credential, *, scopes, subject, http, clock)`: a new access token for
  `scopes` (a list of scope strings, in the order given), asked for through `http` at the
  time on `clock`. `subject` is the address of the person a service account acts for
  (domain-wide delegation: the JWT's `sub`), or None: a service account then acts as itself,
  and a refresh token always acts as the person who authorised it (a subject given with one
  is a SourceError, and nothing is sent). Scopes that are not a non-empty list of strings
  without spaces are a ValueError, and nothing is sent.

Where a token request goes
- A refresh token's request goes to GOOGLE_TOKEN_URL. A service account's goes to where its
  `token_uri` leads: GOOGLE_TOKEN_URL whether the file names it or the older address Google's
  Console still writes (KEY_FILE_TOKEN_URIS), exactly as written; a `token_uri` on this
  machine (http or https, host 127.0.0.1 or localhost, any port, no user, query or fragment)
  as it is, for tests and stand-ins. **[cairn]** A key file without `token_uri` goes to
  GOOGLE_TOKEN_URL. Any other `token_uri`, look-alikes included, is refused when the
  credential is loaded, so before anything is signed or sent.

The requests (docs/google-auth.md, A.3 to A.5 and B.2)
- A service account: POST a form (application/x-www-form-urlencoded) of exactly `grant_type`
  (JWT_GRANT) and `assertion`, a JWT signed RS256 (RSASSA-PKCS1-v1_5 with SHA-256) with the
  key: header `alg` "RS256", `typ` "JWT" and `kid` the `private_key_id` when there is one;
  claims `iss` the `client_email`, `sub` the subject when there is one, `scope` the scopes
  joined by spaces, `aud` GOOGLE_TOKEN_URL whatever the `token_uri` (the notes' rule, not
  the older values of its examples), `iat` the clock's time in whole seconds rounded down
  and `exp` LIFETIME after it. **[cairn]** Each part is base64url without padding (the error
  table asks for none; the header example has one), and `exp` is 3,600 seconds after `iat`,
  within both of the page's limits (60 and 65 minutes).
- A refresh token: POST a form of exactly `client_id`, `client_secret`, `grant_type`
  ("refresh_token") and `refresh_token`, whatever scopes are asked for: the token carries
  what the person authorised. **[cairn]** `client_secret` is always sent, since the notes
  disagree on whether it is optional (QUESTION 6), and a refresh response's `refresh_token`,
  if Google ever sends one (QUESTION 10), is not kept: the credential is the variable.
- Both ask for JSON and follow no redirect. The answer is read to at most
  MAX_RESPONSE_BYTES. A 200 is the token when it is a JSON object whose `access_token` is
  text of 16 to 4,096 characters without a space (Google's are at most 2,048 bytes); the
  other fields are not read.

Secrets
- `credential.secrets` is what nothing cairn stores may hold, for the runner's
  `holds_secret`: a key file's PEM and each line of its body (so a line of the key quoted
  alone is caught too), or the client secret and the refresh token. Never the client's
  email or id, the key's id or a subject: those are not secret, and a short value would
  withhold innocent drafts. The access token is the caller's to add (it exists only once
  asked for). None of them is ever in a URL, an exception message or a log line, and a
  credential's repr shows only its variable.

When the token endpoint says no
- The answer's `error` code (only that field is read, and only one of the codes below; never
  `error_description`, `error_uri` or the rest of the body) chooses a sentence an operator can
  act on, whatever the HTTP status, beginning "Google refused the token request for
  <variable>: ". For a service account (docs/google-auth.md, A.6): `unauthorized_client`
  and `access_denied` say the domain-wide delegation was not granted (the client, or the
  scope), `invalid_grant` that the subject is not a user of the domain, the clock is wrong
  or the key was deleted or disabled, and `admin_policy_enforced`, `invalid_client`,
  `deleted_client`, `disabled_client`, `org_internal` and `invalid_scope` each say theirs.
  For a refresh token (B.4, B.5): `invalid_grant` says it was revoked or has expired and to
  authorise again, and `invalid_client`, `deleted_client`, `admin_policy_enforced` and
  `org_internal` theirs.
- Any other code, or none: "Google refused the token request for <variable> (HTTP n)" and
  what to check. A 5xx without a known code: a fault on Google's side, for the next run. A
  redirect, a network error (by its class name), an answer past its bound, or a 200 that is
  not the documented shape: a SourceError that never holds the body.
"""

import json
import math
import re
from base64 import urlsafe_b64encode
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from cairn.sources import Clock, SourceError
from cairn.sources.google_api import TooLarge, Unreachable, fetch

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
KEY_FILE_TOKEN_URIS = frozenset({GOOGLE_TOKEN_URL, "https://accounts.google.com/o/oauth2/token"})
JWT_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
LIFETIME = 3600  # seconds from `iat` to `exp`
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
MAX_RESPONSE_BYTES = 64 * 1024
TOKEN = re.compile(r"[\x21-\x7e]{16,4096}")
SCOPE = re.compile(r"\S+")
SECRET_LINE = 16  # the shortest line of a key's body that counts as a secret on its own


@dataclass(frozen=True)
class ServiceAccount:
    """A service account's key file, as cairn uses it."""

    variable: str
    client_email: str
    key_id: str | None
    token_url: str  # where its token request goes
    key: rsa.RSAPrivateKey = field(repr=False)
    pem: str = field(repr=False)

    @property
    def secrets(self) -> tuple[str, ...]:
        lines = [line.strip() for line in self.pem.splitlines()]
        body = [line for line in lines if len(line) >= SECRET_LINE and not line.startswith("-")]
        return (self.pem.strip(), *body)


@dataclass(frozen=True)
class AuthorizedUser:
    """One person's own authorisation: an OAuth client and the refresh token it was given."""

    variable: str
    client_id: str
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)

    @property
    def secrets(self) -> tuple[str, ...]:
        return (self.client_secret, self.refresh_token)


Credential = ServiceAccount | AuthorizedUser


# Loading a credential


def load_credential(variable: str, env: Mapping[str, str]) -> Credential:
    """The credential in `env[variable]`, or SourceError naming the variable and what is
    wrong, never a value."""
    text = env.get(variable)
    if text is None:
        raise SourceError(
            f"{variable} is not set in the environment of the process that syncs: it holds"
            " the Google credential (a service account's key file, or an authorized_user"
            " document), as text"
        )
    if not text.strip():
        raise SourceError(
            f"{variable} is empty: it holds the Google credential (a service account's key"
            " file, or an authorized_user document), as text"
        )
    try:
        doc = json.loads(text)
    except (ValueError, RecursionError):
        raise SourceError(
            f"{variable} does not hold JSON: it holds the credential's text (the key file's"
            " contents, on one line), not a path to it"
        ) from None
    if not isinstance(doc, dict):
        raise SourceError(
            f"{variable} holds JSON that is not an object: it holds one credential document"
        )
    kind = doc.get("type")
    if kind is None:
        raise SourceError(
            f'{variable} holds a credential without a "type": this server takes "service_account"'
            ' (a key file) or "authorized_user" (a refresh token)'
        )
    if kind == "service_account":
        return _service_account(variable, doc)
    if kind == "authorized_user":
        return _authorized_user(variable, doc)
    raise SourceError(
        f'{variable} holds a credential of another type: this server takes "service_account" (a key'
        ' file) or "authorized_user" (a refresh token)'
    )


def _text(doc: dict[str, Any], name: str) -> str | None:
    value = doc.get(name)
    return value if isinstance(value, str) and value.strip() else None


def _service_account(variable: str, doc: dict[str, Any]) -> ServiceAccount:
    def wrong(what: str) -> SourceError:
        return SourceError(f"{variable} holds a service_account key file {what}")

    for name in ("private_key", "client_email"):
        if _text(doc, name) is None:
            raise wrong(f"without {name}: download a new key file and put its text there")
    for name in ("private_key_id", "token_uri"):
        if name in doc and not isinstance(doc[name], str):
            raise wrong(f"whose {name} is not text")
    token_url = _token_url(doc.get("token_uri"))
    if token_url is None:
        raise wrong(
            "whose token_uri is not Google's token endpoint: this server sends a key's assertion"
            f" only to {GOOGLE_TOKEN_URL} (or, for a test, to this machine)"
        )
    pem = doc["private_key"]
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError, UnicodeError, UnsupportedAlgorithm):
        key = None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise wrong(
            "whose private_key does not load as an unencrypted RSA key in PEM: download a new"
            " key file"
        )
    return ServiceAccount(
        variable=variable,
        client_email=doc["client_email"],
        key_id=doc.get("private_key_id") or None,
        token_url=token_url,
        key=key,
        pem=pem,
    )


def _token_url(token_uri: str | None) -> str | None:
    """Where a key file's assertion goes, or None when its `token_uri` is not allowed."""
    if token_uri is None or token_uri in KEY_FILE_TOKEN_URIS:
        return GOOGLE_TOKEN_URL
    try:
        parts = urlsplit(token_uri)
        host = parts.hostname
    except ValueError:
        return None
    local = parts.scheme in ("http", "https") and host in LOCAL_HOSTS
    if not local or "@" in parts.netloc or parts.query or parts.fragment:
        return None
    if "?" in token_uri or "#" in token_uri or any(c.isspace() for c in token_uri):
        return None
    return token_uri


def _authorized_user(variable: str, doc: dict[str, Any]) -> AuthorizedUser:
    for name in ("client_id", "client_secret", "refresh_token"):
        if _text(doc, name) is None:
            raise SourceError(
                f"{variable} holds an authorized_user without {name}: authorise again and put"
                " the new document there"
            )
    return AuthorizedUser(
        variable=variable,
        client_id=doc["client_id"],
        client_secret=doc["client_secret"],
        refresh_token=doc["refresh_token"],
    )


# The token request


def _b64(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _segment(value: dict[str, Any]) -> str:
    return _b64(json.dumps(value, separators=(",", ":")).encode("utf-8"))


def assertion(credential: ServiceAccount, scopes: list[str], subject: str | None, now: int) -> str:
    """The signed JWT a service account sends for `scopes`, at `now` (Unix seconds)."""
    header = {"alg": "RS256", "typ": "JWT"}
    if credential.key_id:
        header["kid"] = credential.key_id
    claims: dict[str, Any] = {"iss": credential.client_email}
    if subject is not None:
        claims["sub"] = subject
    claims |= {"scope": " ".join(scopes), "aud": GOOGLE_TOKEN_URL, "iat": now}
    claims["exp"] = now + LIFETIME
    signed = f"{_segment(header)}.{_segment(claims)}"
    signature = credential.key.sign(signed.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signed}.{_b64(signature)}"


def _check_scopes(scopes: object) -> list[str]:
    if (
        not isinstance(scopes, list)
        or not scopes
        or not all(isinstance(s, str) and SCOPE.fullmatch(s) for s in scopes)
    ):
        raise ValueError("scopes is a non-empty list of scope strings, each without a space")
    return scopes


async def access_token(
    credential: Credential,
    *,
    scopes: list[str],
    subject: str | None,
    http: httpx.AsyncClient,
    clock: Clock,
) -> str:
    """A new access token for `scopes` (see the module docstring), or SourceError."""
    scopes = _check_scopes(scopes)
    if isinstance(credential, ServiceAccount):
        now = math.floor(clock().timestamp())
        url = credential.token_url
        form = {"grant_type": JWT_GRANT, "assertion": assertion(credential, scopes, subject, now)}
    else:
        if subject is not None:
            raise SourceError(
                f"a subject is only for a service account: the refresh token in"
                f" {credential.variable} reads as the person who authorised it"
            )
        url = GOOGLE_TOKEN_URL
        form = {
            "client_id": credential.client_id,
            "client_secret": credential.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": credential.refresh_token,
        }
    try:
        status, body = await fetch(
            http, "POST", url, data=form, headers={"Accept": "application/json"},
            limit=MAX_RESPONSE_BYTES,
        )  # fmt: skip
    except Unreachable as error:
        raise SourceError(f"cannot reach Google's token endpoint: {error}") from None
    except TooLarge:
        raise SourceError("the token endpoint's answer is larger than this server reads") from None
    parsed = _parsed(body)
    if status == 200:
        token = parsed.get("access_token") if isinstance(parsed, dict) else None
        if isinstance(token, str) and TOKEN.fullmatch(token):
            return token
        if not isinstance(parsed, dict):
            raise SourceError("the token endpoint's answer is not the documented shape")
        raise SourceError("the token endpoint answered with no access token in its JSON")
    raise SourceError(refusal(credential, status, parsed))


def _parsed(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (ValueError, RecursionError):
        return None


# What a refusal says


_SERVICE_ACCOUNT_SAID = {
    "unauthorized_client": (
        "the service account is not authorised for domain-wide delegation. A Workspace super"
        " admin adds its client ID (the number, not its email) with the scope this server asks for"
        " under Security, Access and data control, API controls, Manage Domain Wide"
        " Delegation; a change can take up to 24 hours"
    ),
    "access_denied": (
        "the domain-wide delegation does not include the scope this server asks for. A Workspace"
        " super admin adds that scope to the service account's entry under Manage Domain Wide"
        " Delegation; a change can take up to 24 hours"
    ),
    "invalid_grant": (
        "the subject is not a user of the domain that granted the delegation, this server's"
        " clock is wrong (a JWT is accepted within an hour of Google's time), or the key was"
        " deleted, disabled or has expired. Check subject, the clock, and the key in the"
        " Cloud console"
    ),
    "admin_policy_enforced": (
        "a Google Workspace administrator's policy does not allow the scope this server asks for"
    ),
    "invalid_client": ("the service account's key or client is not valid: download a new key file"),
    "deleted_client": (
        "the service account was deleted; it can be restored within 30 days in the Cloud"
        " console, or make a new one"
    ),
    "disabled_client": "the key used to sign the request is disabled: enable it or make a new one",
    "org_internal": (
        "the Cloud project is limited to users within its Cloud organization, and the"
        " subject is not one"
    ),
    "invalid_scope": "Google does not accept the scope this server asks for",
}
_AUTHORIZED_USER_SAID = {
    "invalid_grant": (
        "the refresh token was revoked or has expired (access removed by the person, unused"
        " for six months, or issued by an app in Testing, whose tokens last 7 days):"
        " authorise again and put the new document in the variable"
    ),
    "invalid_client": "the OAuth client is not valid, or its secret is wrong",
    "deleted_client": "the OAuth client was deleted in the Cloud console",
    "admin_policy_enforced": (
        "a Google Workspace administrator's policy does not allow the scope this server asks for"
    ),
    "org_internal": "the OAuth client is limited to users within its Cloud organization",
}


def refusal(credential: Credential, status: int, parsed: Any) -> str:
    """A refusal's message: chosen by the `error` code alone, whatever the status."""
    said = (
        _SERVICE_ACCOUNT_SAID if isinstance(credential, ServiceAccount) else _AUTHORIZED_USER_SAID
    )
    code = parsed.get("error") if isinstance(parsed, dict) else None
    variable = credential.variable
    if isinstance(code, str) and code in said:
        return f"Google refused the token request for {variable}: {said[code]}"
    if 300 <= status < 400:
        return (
            f"the token endpoint answered with a redirect (HTTP {status}), which this server does"
            " not follow"
        )
    if status >= 500:
        return (
            f"Google's token endpoint answered HTTP {status}, a fault on its side: the next"
            " run tries again"
        )
    return (
        f"Google refused the token request for {variable} (HTTP {status}): check the"
        " credential it holds"
    )
