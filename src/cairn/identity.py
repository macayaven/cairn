"""Sign-in providers: GitHub, Google and any OpenID Connect provider, behind one seam.

The contract is in cairn/server.py ("Sign-in"), frozen by tests/acceptance/test_h4_signin.py.

A provider does two things (`IdentityProvider`): it names the URL a browser is sent to
(`authorize_url`), and it turns the code the browser comes back with into a `Profile` whose
email the provider has verified (`finish`), or refuses with `SignInRefused`. The web layer
checks the state, applies the install's rules and signs the person in; it knows nothing
about any one provider. A provider keeps nothing between calls but OIDC discovery, and
stores and logs nothing: its client secret goes only to its own token endpoint, and the
tokens it gets back are used for the profile calls and dropped.

What is trusted of a provider, and nothing more
- Every call goes through the `httpx.AsyncClient` the caller passes in. An answer that is not
  a 200 with the JSON expected, or no answer, is a failed sign-in (502), never a crash.
- GitHub: the user's numeric id (the subject), name or login, and the primary email of
  /user/emails when GitHub marks it verified.
- Google: userinfo's `sub`, `email` when `email_verified` is true, `name`, and `hd`, the
  Google Workspace domain the account belongs to (a personal account has none).
- OIDC: endpoints from `<issuer>/.well-known/openid-configuration`, fetched when the
  provider is first used, whose `issuer` must be this issuer and whose endpoints must be
  https (or http on this machine, only when the issuer itself is). The ID token from the
  token endpoint must name this issuer, this client as audience (and as `azp` when it names
  several), the nonce of this sign-in, and an `exp` in the future (400 otherwise). Its
  signature is not checked: it comes over TLS straight from the issuer's token endpoint, in
  exchange for this client's secret (OpenID Connect Core, 3.1.3.7). Userinfo's `sub` must be
  the ID token's. An email counts only when the answer that carries it (the ID token or
  userinfo) also marks it verified, with `email_verified` true or the string "true" (403
  otherwise); when both carry one they must be the same address, ignoring case (400
  otherwise). With `allowed_groups`, the person must be in one of them under `groups_claim`,
  in the ID token or else in userinfo (403 otherwise).
- A second factor (`serve --require-mfa`): only OIDC states how a person signed in, and only
  its ID token is read for it (`second_factor`): `amr` an array holding "mfa" (RFC 8176,
  multiple-factor authentication), or `acr` one of SECOND_FACTOR_ACR, the OpenID Provider
  Authentication Policy Extension's multi-factor policies. Any other value, an `amr` that is
  not an array, and what userinfo says are not read as one. GitHub sends no ID token and
  Google is read from userinfo, so neither states it (`states_factors` False).
"""

import base64
import binascii
import hmac
import json
import re
import time
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict

from cairn.sources.url import inside

PROVIDER_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
# The `acr` values that mean a second factor whatever the provider: the multi-factor
# policies of the OpenID Provider Authentication Policy Extension 1.0.
SECOND_FACTOR_ACR = frozenset(
    {
        "http://schemas.openid.net/pape/policies/2007/06/multi-factor",
        "http://schemas.openid.net/pape/policies/2007/06/multi-factor-physical",
    }
)
OIDC_SCOPE = "openid email profile"
_LOOPBACK = ("localhost", "127.0.0.1", "::1")


def _clock() -> float:
    """Seconds since the epoch, as an ID token's `exp` counts them."""
    return time.time()


class SignInRefused(Exception):
    """A sign-in that does not go through. `status` is what the callback answers: 400 for a
    request or token that is not this sign-in's, 403 for a person the rules turn away, 502
    when the provider failed or answered what cairn cannot use. The message is cairn's own,
    never the provider's text."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class Profile(BaseModel):
    """A person as a provider vouches for them. The email is verified by that provider."""

    model_config = ConfigDict(frozen=True)

    subject: str  # the provider's stable id for the person
    email: str
    name: str
    organisation: str | None = None  # a domain the provider says the account belongs to
    # Whether the ID token shows a second factor (`second_factor`); None: the provider does
    # not say how the person signed in.
    second_factor: bool | None = None


class IdentityProvider(Protocol):
    """One way to sign in. `name` is the route's (/auth/<name>), `label` what the sign-in
    page shows, `issuer` the URL of who vouches for a subject (recorded with each identity, so
    a provider replaced under the same name cannot take over its people), and
    `open_to_anyone` whether anyone can get an account at the provider (so a shared workspace
    behind it needs an email-domain rule), and `states_factors` whether its profile says if
    the person signed in with a second factor (Profile.second_factor)."""

    name: str
    label: str
    issuer: str
    open_to_anyone: bool
    states_factors: bool

    async def authorize_url(
        self, http: httpx.AsyncClient, redirect_uri: str, state: str, nonce: str
    ) -> str: ...

    async def finish(
        self, http: httpx.AsyncClient, code: str, redirect_uri: str, nonce: str
    ) -> Profile: ...

    def within(self, profile: Profile, domains: tuple[str, ...]) -> bool: ...


def email_domain(email: str) -> str:
    return email.rpartition("@")[2].lower()


def _failed(what: str) -> SignInRefused:
    return SignInRefused(502, f"The sign-in did not complete: {what}.")


async def _json(http: httpx.AsyncClient, method: str, url: str, **request: Any) -> Any:
    """The JSON of a 200 answer; anything else is a failed sign-in."""
    try:
        answer = await http.request(method, url, **request)
    except httpx.HTTPError:
        raise _failed("the provider could not be reached") from None
    if answer.status_code != 200:
        raise _failed(f"the provider answered {answer.status_code}")
    try:
        return answer.json()
    except ValueError:
        raise _failed("the provider's answer was not JSON") from None


async def _object(http: httpx.AsyncClient, method: str, url: str, **request: Any) -> dict:
    found = await _json(http, method, url, **request)
    if not isinstance(found, dict):
        raise _failed("the provider's answer was not an object")
    return found


def _text(claims: dict, name: str) -> str:
    """A non-blank string claim; 502 when the provider left it out."""
    value = claims.get(name)
    if not isinstance(value, str) or not value.strip():
        raise _failed(f"the provider did not send {name!r}")
    return value.strip()


def _verified(flag: object) -> bool:
    """True or "true" (as some providers send it), and nothing else."""
    return flag is True or flag == "true"


def _unverified() -> SignInRefused:
    return SignInRefused(403, "The provider has not verified this account's email address.")


class _OAuthProvider:
    """The authorization code flow every provider here shares: a confidential client that
    sends its secret in the token request's body."""

    name: str
    label: str
    issuer: str
    open_to_anyone: bool
    scope: str
    sends_nonce = False
    states_factors = False

    def __init__(self, client_id: str, client_secret: str) -> None:
        if not client_id.strip() or not client_secret.strip():
            raise ValueError("a provider needs a client id and a client secret")
        self.client_id = client_id
        self._client_secret = client_secret

    def __repr__(self) -> str:  # never the secret
        return f"{type(self).__name__}(name={self.name!r}, client_id={self.client_id!r})"

    async def _authorize_endpoint(self, http: httpx.AsyncClient) -> str:
        raise NotImplementedError

    async def authorize_url(
        self, http: httpx.AsyncClient, redirect_uri: str, state: str, nonce: str
    ) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": self.scope,
            "state": state,
        }
        if self.sends_nonce:
            params["nonce"] = nonce
        return f"{await self._authorize_endpoint(http)}?{urlencode(params)}"

    async def _exchange(
        self, http: httpx.AsyncClient, token_endpoint: str, code: str, redirect_uri: str
    ) -> dict:
        """The token endpoint's answer to the code: it holds a string access token."""
        body = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
            "client_secret": self._client_secret,
        }
        headers = {"accept": "application/json"}
        tokens = await _object(http, "POST", token_endpoint, data=body, headers=headers)
        if not isinstance(tokens.get("access_token"), str) or not tokens["access_token"]:
            raise _failed("the provider did not accept the code")  # GitHub says so with a 200
        return tokens

    def within(self, profile: Profile, domains: tuple[str, ...]) -> bool:
        return email_domain(profile.email) in domains


def _bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}", "accept": "application/json"}


class GitHub(_OAuthProvider):
    name, label, open_to_anyone = "github", "GitHub", True
    issuer = "https://github.com"
    scope = "read:user user:email"
    AUTHORIZE = "https://github.com/login/oauth/authorize"
    TOKEN = "https://github.com/login/oauth/access_token"
    USER = "https://api.github.com/user"
    EMAILS = "https://api.github.com/user/emails"

    async def _authorize_endpoint(self, http: httpx.AsyncClient) -> str:
        return self.AUTHORIZE

    async def finish(
        self, http: httpx.AsyncClient, code: str, redirect_uri: str, nonce: str
    ) -> Profile:
        token = (await self._exchange(http, self.TOKEN, code, redirect_uri))["access_token"]
        headers = {**_bearer(token), "accept": "application/vnd.github+json"}
        user = await _object(http, "GET", self.USER, headers=headers)
        emails = await _json(http, "GET", self.EMAILS, headers=headers)
        user_id = user.get("id")
        if not isinstance(user_id, int) or isinstance(user_id, bool):
            raise _failed("GitHub did not send a user id")
        if not isinstance(emails, list):
            raise _failed("GitHub did not send the account's emails")
        primary = next(
            (e for e in emails if isinstance(e, dict) and e.get("primary") is True), None
        )
        if primary is None or primary.get("verified") is not True:
            raise _unverified()
        name = user.get("name") if isinstance(user.get("name"), str) else None
        return Profile(
            subject=str(user_id),
            email=_text(primary, "email"),
            name=(name or "").strip() or _text(user, "login"),
        )


class Google(_OAuthProvider):
    """Google's OpenID Connect, read from its userinfo endpoint."""

    name, label, open_to_anyone = "google", "Google", True
    issuer = "https://accounts.google.com"
    scope = OIDC_SCOPE
    AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN = "https://oauth2.googleapis.com/token"
    USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"

    async def _authorize_endpoint(self, http: httpx.AsyncClient) -> str:
        return self.AUTHORIZE

    async def finish(
        self, http: httpx.AsyncClient, code: str, redirect_uri: str, nonce: str
    ) -> Profile:
        token = (await self._exchange(http, self.TOKEN, code, redirect_uri))["access_token"]
        claims = await _object(http, "GET", self.USERINFO, headers=_bearer(token))
        subject, email = _text(claims, "sub"), _text(claims, "email")
        if not _verified(claims.get("email_verified")):
            raise _unverified()
        hosted = claims.get("hd")
        name = claims.get("name") if isinstance(claims.get("name"), str) else ""
        return Profile(
            subject=subject,
            email=email,
            name=name.strip() or email,
            organisation=hosted.lower() if isinstance(hosted, str) and hosted else None,
        )

    def within(self, profile: Profile, domains: tuple[str, ...]) -> bool:
        """The address's domain, and the Google Workspace the account belongs to."""
        return super().within(profile, domains) and profile.organisation in domains


def _https_or_loopback(url: object, loopback: bool = True) -> bool:
    """An absolute https URL, or (with `loopback`) http on this machine; printable ASCII, no
    userinfo."""
    if not isinstance(url, str) or any(not "!" <= char <= "~" for char in url):
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return False
    if not host or "@" in parts.netloc or inside(host):
        return False
    return parts.scheme == "https" or (loopback and parts.scheme == "http" and host in _LOOPBACK)


def _decode(token: object) -> dict:
    """The claims of a JWT, unverified (see the module docstring); 400 when it is not one."""
    bad = SignInRefused(400, "The provider's ID token is not valid for this sign-in.")
    if not isinstance(token, str) or token.count(".") != 2:
        raise bad
    payload = token.split(".")[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, binascii.Error):
        raise bad from None
    if not isinstance(claims, dict):
        raise bad
    return claims


def _verified_email(*answers: dict) -> str:
    """The address an answer carries and marks verified itself, never one answer's address on
    another's flag. Answers that both carry an address must name the same one, ignoring case
    (400 otherwise); one that is carried but verified nowhere is refused (403); none at all is
    a failed sign-in (502)."""
    carried = [
        (answer["email"].strip(), _verified(answer.get("email_verified")))
        for answer in answers
        if isinstance(answer.get("email"), str) and answer["email"].strip()
    ]
    if not carried:
        raise _failed("the provider did not send 'email'")
    if len({email.lower() for email, _ in carried}) > 1:
        raise SignInRefused(400, "The provider's answers name two different email addresses.")
    verified = [email for email, flag in carried if flag]
    if not verified:
        raise _unverified()
    return verified[-1]  # userinfo's when it verified it, else the ID token's


def second_factor(claims: dict) -> bool:
    """Whether an ID token's claims show a second factor: `amr` an array holding "mfa", or
    `acr` one of SECOND_FACTOR_ACR. Exact values only."""
    amr = claims.get("amr")
    if isinstance(amr, list) and "mfa" in amr:
        return True
    acr = claims.get("acr")
    return isinstance(acr, str) and acr in SECOND_FACTOR_ACR


def _groups(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {group for group in value if isinstance(group, str)}
    return set()


class OIDC(_OAuthProvider):
    """Any OpenID Connect provider: an organisation's own (Okta, Entra ID, Cognito, Keycloak,
    Authentik, ...). It is not open to anyone: its directory decides who has an account."""

    open_to_anyone = False
    scope = OIDC_SCOPE
    sends_nonce = True
    states_factors = True

    def __init__(
        self,
        name: str,
        label: str,
        issuer: str,
        client_id: str,
        client_secret: str,
        groups_claim: str | None = None,
        allowed_groups: tuple[str, ...] = (),
    ) -> None:
        super().__init__(client_id, client_secret)
        if not PROVIDER_NAME.fullmatch(name):
            raise ValueError(f"a provider name matches {PROVIDER_NAME.pattern}: {name!r}")
        if not label.strip():
            raise ValueError("a provider needs a label for the sign-in page")
        if not _https_or_loopback(issuer):
            raise ValueError("an issuer is an https URL (or http on this machine)")
        if allowed_groups and not groups_claim:
            raise ValueError("allowed_groups needs the groups_claim to read them from")
        self.name, self.label, self.issuer = name, label.strip(), issuer
        self.groups_claim = groups_claim
        self.allowed_groups = tuple(allowed_groups)
        self._endpoints: dict[str, str] | None = None

    async def _discover(self, http: httpx.AsyncClient) -> dict[str, str]:
        """The issuer's endpoints, fetched on first use and kept for the process's life."""
        if self._endpoints is None:
            url = self.issuer.rstrip("/") + "/.well-known/openid-configuration"
            found = await _object(http, "GET", url)
            if found.get("issuer") != self.issuer:
                raise _failed("the provider's discovery document names another issuer")
            names = ("authorization_endpoint", "token_endpoint", "userinfo_endpoint")
            local = self.issuer.startswith("http://")  # a development issuer on this machine
            if not all(_https_or_loopback(found.get(name), local) for name in names):
                raise _failed("the provider's discovery document lacks a usable endpoint")
            self._endpoints = {name: found[name] for name in names}
        return self._endpoints

    async def _authorize_endpoint(self, http: httpx.AsyncClient) -> str:
        return (await self._discover(http))["authorization_endpoint"]

    async def finish(
        self, http: httpx.AsyncClient, code: str, redirect_uri: str, nonce: str
    ) -> Profile:
        endpoints = await self._discover(http)
        tokens = await self._exchange(http, endpoints["token_endpoint"], code, redirect_uri)
        claims = self._id_claims(tokens.get("id_token"), nonce)
        userinfo = await _object(
            http, "GET", endpoints["userinfo_endpoint"], headers=_bearer(tokens["access_token"])
        )
        if userinfo.get("sub") != claims["sub"]:
            raise SignInRefused(400, "The provider's answers are about two different people.")
        email = _verified_email(claims, userinfo)
        merged = {**claims, **userinfo}  # for the name: userinfo's, else the ID token's
        if self.allowed_groups:
            assert self.groups_claim is not None
            groups = _groups(claims.get(self.groups_claim)) or _groups(
                userinfo.get(self.groups_claim)
            )
            if not groups & set(self.allowed_groups):
                raise SignInRefused(403, "This account is not in a group allowed to sign in.")
        name = next(
            (
                merged[key].strip()
                for key in ("name", "preferred_username")
                if isinstance(merged.get(key), str) and merged[key].strip()
            ),
            email,
        )
        return Profile(
            subject=claims["sub"], email=email, name=name, second_factor=second_factor(claims)
        )

    def _id_claims(self, id_token: object, nonce: str) -> dict:
        """The ID token's claims when it is this sign-in's own; 400 otherwise."""
        claims = _decode(id_token)
        audience = claims.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        expires = claims.get("exp")
        sent = claims.get("nonce")
        if (
            claims.get("iss") != self.issuer
            or self.client_id not in audiences
            or (len(audiences) > 1 and claims.get("azp") != self.client_id)
            or not isinstance(expires, int | float)
            or isinstance(expires, bool)
            or expires <= _clock()
            or not isinstance(sent, str)
            or not hmac.compare_digest(sent.encode(), nonce.encode())
            or not isinstance(claims.get("sub"), str)
            or not claims["sub"]
        ):
            raise SignInRefused(400, "The provider's ID token is not valid for this sign-in.")
        return claims
