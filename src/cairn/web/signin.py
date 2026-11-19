"""Sign-in through identity providers: the `SignIn` configuration, its checks, and the routes
/login, /auth/<provider> and /auth/<provider>/callback.

The contract is in cairn/server.py ("Sign-in"). The providers (cairn.identity) turn a code
into a verified profile; this module holds what is the same for every provider: the state,
the install's rules (who may join, and where), and the session.

The state of a sign-in travels in a cookie (`cairn_signin`, Path=/auth, HttpOnly,
SameSite=Lax so that it comes back on the provider's redirect, Secure on https, ten
minutes), sealed with a secret made when the process starts (`oauth.SealedRequests`): one
process serves cairn, and a restart only makes a sign-in in progress fail. A state holds the
`state` the provider echoes, the OIDC `nonce` and where to go next, and is bound to one
provider.

B2: several sign-ins can be under way in one browser (two tabs, or two people taking turns at
one machine). Each start adds its state to the cookie and replaces nothing; a callback is
matched to the state whose `state` it carries, in whatever order they come back. The cookie
holds at most UNDER_WAY states, oldest first, each sealed on its own exactly as one state was
sealed before B2, joined by "~" (a character the seal never uses and a cookie may hold).
Sealed one by one, each is still bound to its provider and its own ten minutes, the server
takes one out without sealing the others again, and a cookie written before B2 is a list of
one that reads as it did. Each write keeps only the states this server can still open
(intact, unexpired, of a provider it has); a start then adds the new one last and drops the
oldest while there are more than UNDER_WAY or the value is over COOKIE_MAX characters, so the
cookie with its name and attributes stays under 4,000 bytes. The newest always fits alone:
its `next` is at most NEXT_MAX printable characters and a provider's name at most 32. A
finished, refused or cancelled sign-in takes only its own state out, and the cookie is
cleared when none is left, so with one sign-in under way at a time every response is as it
was before B2 but for the cookie's opaque value. The cookie's name and attributes never
change. Two starts sent at the very same instant from one browser may still lose one of the
two: each reads the cookie before the other has answered, and the later answer's cookie
replaces the earlier one's. Nothing is built for that; starting that sign-in again works.

A callback whose state the cookie does not hold (unknown, used, expired, dropped, or started
in another browser) is refused with _STALE, 400, whether or not the browser is signed in: a
real return from a provider comes from another site, so it arrives without the
SameSite=Strict session cookie, and a rule for signed-in browsers would hold only in tests. A
person signed in on several browsers has a session in each (auth.Sessions): signing in, out
or being refused in one touches no other.

A state is settled once, in memory, before any call to the provider, and only
by a callback that carries a `code`: one without (the provider reports an error, or someone
just calls it) settles nothing and calls no provider, so it adds nothing to the table of
settled states (`oauth.SealedRequests`, bounded). Starting a sign-in writes nothing; the
callback is the one GET that writes.

Starts and callbacks are each limited per client address (SIGN_INS in any SIGN_IN_WINDOW
seconds, with the limiter agent registration uses, `oauth.RateLimit`: an IPv6 /64 is one
address, and at most 10,000 addresses are tracked: when full, the one seen longest ago is
forgotten, never a new one refused). A sign-in is one start and one callback,
so 30 a minute lets an office behind one address sign in 30 people a minute, while one
address can make cairn call a provider at most 30 times a minute. Over the limit: 429 with
Retry-After, on the sign-in page, before anything else is done: nothing is settled and no
provider is called. The address is the peer's, or what a trusted proxy says it is
(`serve --trusted-proxy`). Whoever asks, cairn exchanges at most PROVIDER_CALLS codes with
the providers in any PROVIDER_WINDOW seconds (`Ceiling`): a callback past that is answered
the same way, 429 on the sign-in page with nothing settled and no provider called, and a
warning is logged at most once a minute. A flood from many addresses can therefore refuse
new sign-ins while it lasts; it cannot make cairn flood a provider, and people already
signed in are not affected.

A way back too long for the cookie (over 1024 characters: an agent's consent page with a long
`state` or redirect URI) is held whole in this process's memory (`Continuations`) behind a
short random handle, which the cookie carries instead. It is never written to the database.

B1 (the contract is in cairn/server.py, "A wall, a brand"). Under a brand on https the state
cookie is `__Host-signin` at Path=/ (the prefix allows no other path) and the session cookie
`__Host-session` (auth.Sessions), so no other host under the same parent domain can plant
either (auth.branded_cookie says why); over plain http they are `signin` (Path=/auth) and
`session`. Only the name set is read. The sign-in page, a refusal on it, the page over the
limit and the page that moves on after a sign-in are brand_signin.html and
brand_signed_in.html, which name the brand and nothing else; and no sentence names cairn
(_REPLACED says "this server"). The session a sign-in
makes lasts as long as auth.Sessions says: 12 hours, or `serve --session-days` days.
"""

import hmac
import logging
import secrets
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from cairn import oauth
from cairn.identity import IdentityProvider, Profile, SignInRefused
from cairn.store import JOINS, ProviderChanged, Removed
from cairn.web.auth import HOST_ONLY, RENEW_AFTER, Accounts, Sessions, branded_cookie
from cairn.web.brand import Brand, server_name
from cairn.web.templating import _page
from cairn.web.ui import _local_path, welcome_path

STATE_COOKIE = "cairn_signin"
BRANDED_STATE_COOKIE = "signin"  # its name under a brand (B1): no cookie names cairn
STATE_PATH = "/auth"
STATE_TTL = 600  # seconds a person has to finish at the provider
NEXT_MAX = 1024  # characters of `next` the state cookie carries (browsers cap a cookie at 4 KB)
UNDER_WAY = 5  # sign-ins under way in one browser at once (B2); a sixth start drops the oldest
COOKIE_MAX = 3800  # characters of the state cookie's value; with its name and attributes < 4,000
SEPARATOR = "~"  # between the sealed states in the cookie: never in a seal, allowed in a cookie
HELD_MAX = 4096  # longer ways back held in memory at once; the oldest is dropped first
HELD_SIZE = 8192  # characters of one held way back; a longer one keeps only its path
# Sign-in starts, and separately callbacks, per client address (oauth.RateLimit) ...
SIGN_INS = 30
SIGN_IN_WINDOW = 60  # ... in any minute
# Token exchanges cairn asks of the sign-in providers in any minute, whoever is asking: ten a
# second is far above any real sign-in rate and far below what a provider tolerates.
PROVIDER_CALLS = 600
PROVIDER_WINDOW = 60
_BUSY = "Too many sign-ins from your network in the last minute. Wait a minute and try again."
_ALL_BUSY = "Too many people are signing in right now. Wait a minute and try again."
log = logging.getLogger("cairn.signin")
# What ?error= on /login may say: a fixed vocabulary, never text from the URL.
LOGIN_ERRORS = {"cancelled": "The sign-in was cancelled or refused at the provider."}
# What a return that is not valid any more is told. The causes are the usual ones, not the
# only ones (a sign-in pushed out as the oldest of six, or started before a restart, reaches
# this too), and the last sentence is for whoever reads a refusal as "I cannot be signed in
# twice": every sign-in they already have stays.
_STALE = (
    "This sign-in is not valid any more: usually it took too long, was used already, or was"
    " started in another browser. Start again. Nothing you are already signed in to is"
    " affected."
)
_OUTSIDE = "This install only admits accounts of its organisation's email domains."
_UNLISTED = "This install admits only the people its operator has listed."
_REPLACED = (  # {server}: brand.server_name, which is cairn's without a brand
    "This sign-in provider has changed since this account last signed in through it, so"
    " {server} cannot tell whether it is the same person. An operator of this install must"
    " act before this account can sign in."
)


class Ceiling:
    """At most `limit` allowed calls in any `window` seconds, whoever makes them: the times of
    the allowed ones, at most `limit` of them, so memory is bounded. A refusal is logged as a
    warning at most once a window. Called on the event loop only."""

    def __init__(
        self, limit: int, window: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit, self._window, self._clock = limit, window, clock
        self._calls: deque[float] = deque()
        self._warned: float | None = None

    def allow(self) -> bool:
        now = self._clock()
        while self._calls and self._calls[0] <= now - self._window:
            self._calls.popleft()
        if len(self._calls) < self._limit:
            self._calls.append(now)
            return True
        if self._warned is None or now - self._warned >= self._window:
            self._warned = now
            log.warning(
                "%d sign-in token exchanges in the last %g s, the most cairn asks of the"
                " providers: callbacks are refused (429) until the count falls",
                self._limit,
                self._window,
            )
        return False


@dataclass(frozen=True)
class SignIn:
    """People sign in through `providers` (cairn.identity), which call out through `http`.
    `join`: where a new person lands, "personal" (a workspace of their own) or "default"
    (the default workspace). `allowed_email_domains`: when not empty, only people whose
    verified email is in one of them get in. `allowed_emails`: when not empty, only people
    whose verified email is one of these addresses get in (the whole address, ignoring case
    and the spaces around an entry; `email_entries`). With both, either admits. The list is
    left out of `repr()`, so nothing that prints this configuration prints an address."""

    providers: Sequence[IdentityProvider]
    http: httpx.AsyncClient
    join: str = "personal"
    allowed_email_domains: Sequence[str] = ()
    allowed_emails: Sequence[str] = field(default=(), repr=False)


class Continuations:
    """Ways back held in memory behind a short random handle: at most `limit` at once (the
    oldest dropped first when full), each at most `size` characters, each dropped once taken
    or `ttl` seconds after it was held. A handle that is unknown, used or expired gives "/".
    Nothing is written anywhere: a restart forgets them, as it forgets a sign-in in progress."""

    def __init__(
        self,
        limit: int = HELD_MAX,
        size: int = HELD_SIZE,
        ttl: float = STATE_TTL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit, self._size, self._ttl, self._clock = limit, size, ttl, clock
        self._held: OrderedDict[str, tuple[float, str]] = OrderedDict()  # oldest first
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            self._expire(self._clock())
            return len(self._held)

    def hold(self, path: str) -> str | None:
        """A new handle for `path`; None when it is longer than `size`."""
        if len(path) > self._size:
            return None
        handle, now = secrets.token_urlsafe(16), self._clock()
        with self._lock:
            self._expire(now)
            while len(self._held) >= self._limit:
                self._held.popitem(last=False)
            self._held[handle] = (now + self._ttl, path)
        return handle

    def take(self, handle: object) -> str:
        """The way back held under `handle`, once; "/" when there is none."""
        if not isinstance(handle, str):
            return "/"
        with self._lock:
            self._expire(self._clock())
            held = self._held.pop(handle, None)
        return held[1] if held else "/"

    def _expire(self, now: float) -> None:
        while self._held and next(iter(self._held.values()))[0] <= now:
            self._held.popitem(last=False)


def email_entries(entries: Sequence[str]) -> frozenset[str]:
    """The allowed addresses, stripped and in lowercase; the same address twice is one entry.
    ValueError for an entry that is not one address (blank, no "@" or more than one, nothing
    before or after it, a space inside): the message names its position, never its text."""
    kept = set()
    for position, entry in enumerate(entries, start=1):
        address = entry.strip().lower() if isinstance(entry, str) else ""
        local, at, domain = address.partition("@")
        if not (at and local and domain) or "@" in domain or any(c.isspace() for c in address):
            raise ValueError(
                f"allowed email entry {position} is not one address: each entry is a whole"
                " address, like name@example.org, with no spaces inside"
            )
        kept.add(address)
    return frozenset(kept)


def check(sign_in: SignIn, public_url: str | None) -> tuple[str, tuple[str, ...], frozenset[str]]:
    """The origin the providers send people back to, the allowed domains in lowercase and the
    allowed addresses (`email_entries`). ValueError for a configuration that cannot work or
    would leave a workspace open."""
    issuer = oauth.issuer(public_url)
    if issuer is None:
        raise ValueError(
            "sign-in needs public_url: the origin cairn is served at, over https"
            " (or http on localhost, 127.0.0.1 or [::1])"
        )
    names = [provider.name for provider in sign_in.providers]
    if not names:
        raise ValueError("sign-in needs at least one provider")
    if len(set(names)) != len(names):
        raise ValueError(f"two providers share a name: {names}")
    if sign_in.join not in JOINS:
        raise ValueError(f"join is one of {', '.join(JOINS)}: {sign_in.join!r}")
    domains = tuple(domain.strip().lower() for domain in sign_in.allowed_email_domains)
    if not all(domains):
        raise ValueError("an allowed email domain cannot be blank")
    emails = email_entries(sign_in.allowed_emails)
    public = [p.name for p in sign_in.providers if p.open_to_anyone]
    if sign_in.join == "default" and public and not domains and not emails:
        raise ValueError(
            f"anyone can sign in through {', '.join(public)}: a shared workspace behind it"
            " needs allowed_email_domains or allowed_emails"
        )
    return issuer, domains, emails


def _lasts(sessions: Sessions) -> str:
    """How long a sign-in lasts, as the sign-in page says it: "12 hours" without `serve
    --session-days` (today's words), else the days and when a visit renews them. One day is
    never renewed (a use renews only more than RENEW_AFTER after the last renewal, when a
    one-day session has run out), so the page says nothing of renewal then."""
    if not sessions.renews:
        return f"{sessions.seconds // 3600} hours"
    days = sessions.seconds // 86_400
    if sessions.seconds <= RENEW_AFTER:
        return f"{days} day"
    return (
        f"{days} days; a visit more than a day after it was set or last renewed starts the"
        f" {days} days again"
    )


def signin_routes(
    sign_in: SignIn,
    accounts: Accounts,
    public_url: str | None,
    brand: Brand | None = None,
    *,
    test_instance: bool = False,
    wall: bool = False,
) -> list[Route]:
    """/login, /auth/<provider> and its callback. B1, `brand`: the sign-in page, a refusal on
    it and the page that moves on after signing in are pages of their own (brand_signin.html,
    brand_signed_in.html): the brand's name as their one heading and their whole title, its
    logo, one link per provider and, after a refusal, its one sentence; no navigation, no
    footer, no form, no script. The state cookie is auth.branded_cookie(BRANDED_STATE_COOKIE),
    at Path=/ when it carries the __Host- prefix, and no sentence names cairn
    (brand.server_name). `test_instance` (`serve --test-instance`): signin.html says the
    install is a private test instance for approved testers and, unless `wall`, links to the
    run guide before the providers and to the privacy guide after them (with `wall`, its
    footer links to no guide either); a refusal says what
    it says without it, and brand_signin.html does not change. Nobody's admission changes."""
    issuer, domains, emails = check(sign_in, public_url)
    secure = issuer.startswith("https://")
    state_cookie = branded_cookie(BRANDED_STATE_COOKIE if brand else STATE_COOKIE, secure)
    state_path = "/" if state_cookie.startswith(HOST_ONLY) else STATE_PATH  # the prefix's rule
    replaced = _REPLACED.format(server=server_name(brand))
    lasts = _lasts(accounts.sessions)  # what signin.html says of the cookie it sets
    providers = {provider.name: provider for provider in sign_in.providers}
    states = oauth.SealedRequests()
    held = Continuations()
    starts = oauth.RateLimit(SIGN_INS, SIGN_IN_WINDOW)
    callbacks = oauth.RateLimit(SIGN_INS, SIGN_IN_WINDOW)
    exchanges = Ceiling(PROVIDER_CALLS, PROVIDER_WINDOW)

    def over(limit: oauth.RateLimit, request: Request) -> Response | None:
        """The sign-in page with 429 when this client address is over `limit`; else None."""
        if limit.allow(request.client.host if request.client else None):
            return None
        busy = login_page("/", _BUSY, 429)
        busy.headers.update({"retry-after": str(SIGN_IN_WINDOW), "cache-control": "no-store"})
        return busy

    def provider_of(request: Request) -> IdentityProvider:
        provider = providers.get(request.path_params["provider"])
        if provider is None:
            raise HTTPException(404, "Not Found")
        return provider

    def callback_url(provider: IdentityProvider) -> str:
        return f"{issuer}/auth/{provider.name}/callback"

    def login_page(next_path: str, error: str | None, status_code: int = 200) -> Response:
        query = "" if next_path == "/" else "?" + urlencode({"next": next_path})
        links = [
            {"href": f"/auth/{name}{query}", "label": provider.label}
            for name, provider in providers.items()
        ]
        shown = "brand_signin.html" if brand else "signin.html"
        return _page(
            shown,
            status_code,
            brand=brand,
            providers=links,
            error=error,
            lasts=lasts,
            test_instance=test_instance,
            wall=wall,
        )

    def login_form(request: Request) -> Response:
        error = LOGIN_ERRORS.get(request.query_params.get("error", ""))
        return login_page(_local_path(request.query_params.get("next")), error)

    def no_key_login(request: Request) -> Response:
        raise HTTPException(404, "Not Found")

    def sealed_states(request: Request) -> list[str]:
        """Each sealed state the browser's cookie holds, oldest first, opened or not."""
        value = request.cookies.get(state_cookie)
        return [part for part in value.split(SEPARATOR) if part] if value else []

    def under_way(request: Request) -> list[str]:
        """The sealed states of this browser that this server can still open: intact,
        unexpired and of one of its providers. The others are dropped at the next write."""
        return [
            part
            for part in sealed_states(request)
            if any(states.open(part, f"sign-in:{name}") is not None for name in providers)
        ]

    def keep(response: Response, kept: list[str]) -> Response:
        """Set the state cookie to the sealed states `kept`; clear it when there are none."""
        if not kept:
            response.delete_cookie(
                state_cookie, path=state_path, secure=secure, httponly=True, samesite="lax"
            )
            return response
        response.set_cookie(
            state_cookie,
            SEPARATOR.join(kept),
            max_age=STATE_TTL,
            path=state_path,
            secure=secure,
            httponly=True,
            samesite="lax",
        )
        return response

    async def start(request: Request) -> Response:
        """To the provider. Nothing is written: the state goes into the cookie, after the
        sign-ins already under way in this browser (B2)."""
        provider = provider_of(request)
        if busy := over(starts, request):
            return busy
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        try:
            url = await provider.authorize_url(sign_in.http, callback_url(provider), state, nonce)
        except SignInRefused as refusal:
            return login_page("/", str(refusal), refusal.status)
        onward, handle = _local_path(request.query_params.get("next")), None
        if len(onward) > NEXT_MAX:  # a cookie over 4 KB is dropped: hold it here instead
            handle = held.hold(onward)
            onward = "/" if handle else onward.partition("?")[0][:NEXT_MAX] or "/"
        sealed = states.seal(
            {"state": state, "nonce": nonce, "next": onward, "held": handle},
            f"sign-in:{provider.name}",
            ttl=STATE_TTL,
        )
        kept = [*under_way(request), sealed][-UNDER_WAY:]
        while len(SEPARATOR.join(kept)) > COOKIE_MAX and len(kept) > 1:
            kept.pop(0)  # the oldest first; the newest always fits alone
        response = RedirectResponse(url, status_code=302, headers={"cache-control": "no-store"})
        return keep(response, kept)

    def opened(request: Request, provider: IdentityProvider) -> tuple[dict[str, Any], str] | None:
        """The sign-in this browser started with this provider whose `state` the provider
        sent back, and its sealed form in the cookie. Nothing is settled here."""
        given = request.query_params.get("state", "").encode()
        for part in sealed_states(request):
            pending = states.open(part, f"sign-in:{provider.name}")
            if pending is not None and hmac.compare_digest(given, pending["state"].encode()):
                return pending, part
        return None

    def onward(pending: dict[str, Any]) -> str:
        """Its `next`: the whole way back, taken from memory when it was held there."""
        return held.take(pending["held"]) if pending.get("held") else pending["next"]

    def admitted(provider: IdentityProvider, profile: Profile) -> bool:
        """The install's rule for who signs in: none, or a domain or a listed address (either
        admits). Checked at every sign-in, before anything is written."""
        if not domains and not emails:
            return True
        if emails and profile.email.strip().lower() in emails:
            return True
        return bool(domains) and provider.within(profile, domains)

    def ended(response: Response, request: Request, part: str) -> Response:
        """The sign-in sealed as `part` is over: only its state leaves the cookie (B2)."""
        keep(response, [other for other in under_way(request) if other != part])
        response.headers["cache-control"] = "no-store"
        return response

    async def callback(request: Request) -> Response:
        provider = provider_of(request)
        if busy := over(callbacks, request):
            return busy
        found = opened(request, provider)
        if found is None:
            return login_page("/", _STALE, 400)
        pending, part = found
        code = request.query_params.get("code", "")
        if "error" in request.query_params:  # the person said no, or the provider did
            back = {"error": "cancelled"}
            if (way := onward(pending)) != "/":
                back["next"] = way
            return ended(
                RedirectResponse(f"/login?{urlencode(back)}", status_code=303), request, part
            )
        if not code:
            return ended(login_page(onward(pending), _STALE, 400), request, part)
        if not exchanges.allow():  # whoever asks: before anything is settled
            busy = login_page("/", _ALL_BUSY, 429)
            busy.headers.update({"retry-after": str(PROVIDER_WINDOW), "cache-control": "no-store"})
            return busy
        if not states.settle(pending):  # once, before the one call to the provider
            return login_page("/", _STALE, 400)
        pending["next"] = onward(pending)
        try:
            profile = await provider.finish(
                sign_in.http, code, callback_url(provider), pending["nonce"]
            )
            if not admitted(provider, profile):
                raise SignInRefused(403, _OUTSIDE if domains else _UNLISTED)
        except SignInRefused as refusal:
            return ended(login_page(pending["next"], str(refusal), refusal.status), request, part)
        try:
            signed = await run_in_threadpool(
                accounts.sign_in, provider.name, provider.issuer, profile, sign_in.join
            )
        except ProviderChanged:
            return ended(login_page(pending["next"], replaced, 403), request, part)
        except Removed as removed:  # join "default": a removal sticks until an invitation
            return ended(login_page(pending["next"], str(removed), 403), request, part)
        if signed.invited:  # the way back (its /join link, say) took a used invitation
            target = "/"
        else:
            target = welcome_path(pending["next"]) if signed.created else pending["next"]
        moving_on = "brand_signed_in.html" if brand else "signed_in.html"
        response = _page(moving_on, brand=brand, url=target)
        accounts.sessions.set(response, signed.token)
        return ended(response, request, part)

    return [
        Route("/login", login_form, methods=["GET"]),
        Route("/login", no_key_login, methods=["POST"]),
        Route("/auth/{provider}", start, methods=["GET"]),
        Route("/auth/{provider}/callback", callback, methods=["GET"]),
    ]
