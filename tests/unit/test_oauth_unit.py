"""Unit tests for cairn's OAuth authorization server, beyond the frozen H3 contract.

They pin what tests/acceptance/test_h3_oauth.py leaves open: the redirect-URI policy, the
bounds on unauthenticated registration, the signed consent request (tampered, expired, used
twice, moved to another account), expiry seen over HTTP, and the pages a person meets on the
way. The app runs over the real store; both clocks are the test's to set.
"""

import base64
import hashlib
import json
import logging
import re
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from linear_cost import assert_near_linear
from starlette.testclient import TestClient

import cairn.store
from cairn import oauth
from cairn.server import create_app
from cairn.store import Store
from cairn.store.grants import ROTATION_WINDOW, ROTATIONS

BASE = "http://127.0.0.1:8765"
RESOURCE = f"{BASE}/mcp"
REDIRECT = "http://127.0.0.1:33418/callback"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
# Ahead of the wall clock: the SDK's token handler also reads a code's expiry, on real time,
# and must not be the one that refuses in a test about the store's clock.
T0 = datetime(2040, 1, 1, 12, 0, tzinfo=UTC)


# When OAuth is on


@pytest.mark.parametrize(
    ("public_url", "issuer"),
    [
        ("https://cairn.example.net", "https://cairn.example.net"),
        ("https://cairn.example.net/", "https://cairn.example.net"),
        ("HTTPS://Cairn.Example.NET:443", "https://cairn.example.net"),
        ("https://cairn.example.net:8443", "https://cairn.example.net:8443"),
        ("http://127.0.0.1:8765", "http://127.0.0.1:8765"),
        ("http://localhost:8765/", "http://localhost:8765"),
        ("http://[::1]:8765", "http://[::1]:8765"),
        (None, None),
        ("", None),
        ("http://cairn.example.net", None),  # plain http off loopback
        ("http://localhost.example.net", None),
        ("http://127.0.0.1.example.net", None),
        ("http://localhost@cairn.example.net", None),
        ("https://user@cairn.example.net", None),
        ("https://cairn.example.net/prefix", None),  # the endpoints live at the root
        ("https://cairn.example.net/?x=1", None),
        ("https://cairn.example.net/#x", None),
        ("ftp://cairn.example.net", None),
        ("cairn.example.net", None),
        ("https://", None),
        ("https://cairn.example.net:port", None),
    ],
)
def test_oauth_is_on_for_https_and_for_http_on_loopback(public_url, issuer):
    assert oauth.issuer(public_url) == issuer


# Redirect URIs


@pytest.mark.parametrize(
    "uri",
    [
        "https://claude.ai/api/mcp/auth_callback",
        "https://app.example/cb?tenant=1&x=%20",
        "https://app.example",
        "https://app.example:8443/cb",
        "HTTPS://App.Example/cb",  # scheme and host compare without case
        "https://xn--bcher-kva.example/cb",
        "https://203.0.113.7/cb",
        "https://[2001:db8::1]:8443/cb",
        "http://localhost:8787/callback",
        "http://LOCALHOST/cb",
        "http://127.0.0.1:33418",
        "http://127.0.0.1/",
        "http://[::1]:9999/cb",
        "https://a.example/" + "x" * 237,  # 255 characters
    ],
)
def test_redirect_uris_that_are_accepted(uri):
    assert oauth.redirect_uri_problem(uri) is None


@pytest.mark.parametrize(
    "uri",
    [
        # not https, and not http on a loopback host
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html,x",
        "myapp://callback",
        "cursor://anysphere.cursor-mcp/oauth/callback",
        "ftp://app.example/cb",
        "http://evil.example/callback",
        "HTTP://evil.example/callback",
        "http://localhost.evil.example/callback",
        "http://127.0.0.1.evil.example/callback",
        "http://evil.example/localhost",
        "http://evil.example/?localhost",
        # loopback spelled any other way
        "http://localhost./cb",
        "http://127.0.0.1./cb",
        "http://127.1/cb",
        "http://127.0.0.2/cb",
        "http://2130706433/cb",
        "http://0x7f.0.0.1/cb",
        "http://0.0.0.0/cb",
        "http://[::]/cb",
        "http://[::ffff:127.0.0.1]/cb",
        "http://[0:0:0:0:0:0:0:1]/cb",
        "http://[::1%25lo0]/cb",
        # userinfo: the host is what follows the last "@"
        "http://localhost@evil.example/cb",
        "http://127.0.0.1:80@evil.example/cb",
        "http://localhost:pw@evil.example/cb",
        "https://user@app.example/cb",
        "https://user:pw@app.example/cb",
        "https://@app.example/cb",
        # fragments
        "https://app.example/cb#fragment",
        "https://app.example/cb#",
        "http://127.0.0.1/cb#x",
        # no host, or not a URL at all
        "https:///cb",
        "https://",
        "https:app.example/cb",
        "//app.example/cb",
        "/cb",
        "app.example/cb",
        "",
        # hosts and ports that do not parse
        "https://app.example./cb",
        "https://app..example/cb",
        "https://.app.example/cb",
        "https://-app.example/cb",
        "https://app_host.example/cb",
        "https://app.example;evil.example/cb",
        "https://app.example,evil.example/cb",
        "https://app.example:99999/cb",
        "https://app.example:port/cb",
        "https://app.example:/cb",
        "https://[::1/cb",
        "https://[not-an-address]/cb",
        "https://bücher.example/cb",
        # characters a browser reads differently, or drops, or that end a header
        "https://app.example\\@evil.example/cb",
        "https://app.example/cb\\..\\x",
        "https://app.example/c b",
        " https://app.example/cb",
        "https://app.example/cb\n",
        "https://app.example/cb\r\nSet-Cookie: x=1",
        "https://app.exa\tmple/cb",
        "https://app.example/cb\x00",
        "https://app.example/cb\x7f",
        "https://app.example/ü",
        "https://a.example/" + "x" * 238,  # 256 characters
    ],
)
def test_redirect_uris_that_are_refused(uri):
    assert oauth.redirect_uri_problem(uri)


@pytest.mark.parametrize("value", [None, 7, ["https://app.example/cb"], {"uri": "x"}])
def test_a_redirect_uri_must_be_a_string(value):
    assert oauth.redirect_uri_problem(value)


@pytest.mark.parametrize(
    ("uri", "origin"),
    [
        ("https://claude.ai/api/mcp/auth_callback", "https://claude.ai"),
        ("HTTPS://App.Example:443/cb?x=1", "https://app.example"),
        ("https://app.example:8443/cb", "https://app.example:8443"),
        ("http://127.0.0.1:33418/callback", "http://127.0.0.1:33418"),
        ("http://localhost:80/cb", "http://localhost"),
        ("http://[::1]:9999/cb", "http://[::1]:9999"),
    ],
)
def test_the_origin_of_a_redirect_uri(uri, origin):
    assert oauth.origin_of(uri) == origin


@pytest.mark.parametrize(
    ("registered", "answer", "expected"),
    [
        (
            "https://client.example/callback",
            {"code": "c 1", "state": "s&1"},
            "https://client.example/callback?code=c+1&state=s%261",
        ),
        (  # the registered query is kept exactly, blank values and order included
            "https://client.example/callback?keep=&other=1",
            {"code": "abc", "state": "xyz"},
            "https://client.example/callback?keep=&other=1&code=abc&state=xyz",
        ),
        (
            "http://127.0.0.1:33418/?a=%20b",
            {"error": "access_denied", "state": "s"},
            "http://127.0.0.1:33418/?a=%20b&error=access_denied&state=s",
        ),
        (
            "https://client.example/cb?x=1",
            {"code": "abc", "state": None},
            "https://client.example/cb?x=1&code=abc",
        ),
    ],
)
def test_the_answer_is_appended_to_the_redirect_uri_as_registered(registered, answer, expected):
    assert oauth.answer_uri(registered, **answer) == expected


# The registration rate limit


def test_the_rate_limit_allows_a_fixed_number_per_address_per_window():
    now = [1000.0]
    limit = oauth.RateLimit(limit=3, window=60, clock=lambda: now[0])
    assert [limit.allow("203.0.113.9") for _ in range(4)] == [True, True, True, False]
    assert limit.allow("203.0.113.10") is True  # another address has its own count
    now[0] += 59.9
    assert limit.allow("203.0.113.9") is False
    now[0] += 0.1  # the first three are now a whole window old
    assert [limit.allow("203.0.113.9") for _ in range(4)] == [True, True, True, False]


def test_the_rate_limit_slides_with_each_request():
    now = [0.0]
    limit = oauth.RateLimit(limit=2, window=60, clock=lambda: now[0])
    assert limit.allow("a") is True
    now[0] = 30
    assert limit.allow("a") is True
    now[0] = 60  # only the first has left the window
    assert [limit.allow("a"), limit.allow("a")] == [True, False]
    now[0] = 90
    assert [limit.allow("a"), limit.allow("a")] == [True, False]


def test_a_refused_request_does_not_extend_the_wait():
    now = [0.0]
    limit = oauth.RateLimit(limit=1, window=60, clock=lambda: now[0])
    assert limit.allow("a") is True
    for second in range(1, 60):
        now[0] = second
        assert limit.allow("a") is False
    now[0] = 60
    assert limit.allow("a") is True


def test_one_ipv6_network_is_one_address_to_the_rate_limit():
    limit = oauth.RateLimit(limit=2, window=60, clock=lambda: 0.0)
    assert limit.allow("2001:db8:1:2::1") and limit.allow("2001:db8:1:2:ffff:ffff:ffff:ffff")
    assert limit.allow("2001:db8:1:2::3") is False  # the same /64: one subscriber
    assert limit.allow("2001:db8:1:3::1") is True
    assert limit.allow("::ffff:203.0.113.9") and limit.allow("203.0.113.9")
    assert limit.allow("203.0.113.9") is False  # an IPv4-mapped address is that IPv4 address
    for unknown in (None, "", "testclient"):  # no usable address: one shared count
        limit.allow(unknown)
    assert limit.allow("not-an-address") is False


def test_a_full_rate_limit_forgets_the_address_seen_longest_ago_and_admits_a_new_one():
    now = [0.0]
    limit = oauth.RateLimit(limit=2, window=60, clock=lambda: now[0], max_addresses=100)
    for n in range(100):  # the table fills with addresses whose counts are all live
        assert limit.allow(f"203.0.113.{n}") is True
    assert limit.allow("203.0.113.1") is True  # seen again, so now the most recent
    # A new address is admitted and counted; 203.0.113.0, seen longest ago, is forgotten.
    assert [limit.allow("198.51.100.1") for _ in range(3)] == [True, True, False]
    assert limit.tracked == 100  # never past its cap
    # The forgotten address starts a fresh count: two more, not one.
    assert [limit.allow("203.0.113.0") for _ in range(3)] == [True, True, False]
    # An address still in the table keeps its count.
    assert limit.allow("203.0.113.1") is False
    assert limit.tracked == 100


# The signed consent request


@pytest.fixture
def clock(monkeypatch):
    now = SimpleNamespace(value=5000.0)
    monkeypatch.setattr(oauth, "_clock", lambda: now.value)
    return now


def test_a_sealed_request_opens_to_what_was_sealed(clock):
    requests = oauth.SealedRequests()
    fields = {"client_id": "client_1", "state": "a b&c=ü", "explicit": True, "none": None}
    sealed = requests.seal(fields, "consent", ttl=600)
    assert re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", sealed)  # safe in a URL and a form
    opened = requests.open(sealed, "consent")
    assert {name: opened[name] for name in fields} == fields
    assert requests.open(sealed, "authorize") is None  # sealed for one step, not the other
    assert requests.seal(fields, "consent", ttl=600) != sealed  # each sealing is its own request


def test_a_sealed_request_expires(clock):
    requests = oauth.SealedRequests()
    sealed = requests.seal({"x": 1}, "authorize", ttl=600)
    clock.value += 599.999
    assert requests.open(sealed, "authorize") is not None
    clock.value += 0.001
    assert requests.open(sealed, "authorize") is None


def test_resealing_for_the_next_step_keeps_the_identity_and_the_deadline(clock):
    requests = oauth.SealedRequests()
    first = requests.open(requests.seal({"x": 1}, "authorize", ttl=600), "authorize")
    clock.value += 500
    again = requests.reseal(first, "consent", account="acct_1")
    opened = requests.open(again, "consent")
    assert (opened["x"], opened["account"]) == (1, "acct_1")
    clock.value += 100  # 600 seconds after the first sealing, not after the second
    assert requests.open(again, "consent") is None


def test_a_tampered_or_foreign_sealed_request_does_not_open(clock):
    requests = oauth.SealedRequests()
    sealed = requests.seal({"client_id": "client_1", "account": "acct_1"}, "consent", ttl=600)
    body, _, signature = sealed.partition(".")
    padded = body + "=" * (-len(body) % 4)
    fields = json.loads(base64.urlsafe_b64decode(padded))
    forged = dict(fields, account="acct_2")
    forged_body = base64.urlsafe_b64encode(json.dumps(forged).encode()).rstrip(b"=").decode()
    for bad in (
        f"{forged_body}.{signature}",  # another payload under the old signature
        f"{body}.{signature[:-1]}{'A' if signature[-1] != 'A' else 'B'}",
        f"{body}.",
        body,
        f".{signature}",
        f"{body}.{signature}.{signature}",
        sealed + "=",
        sealed.upper(),
        "",
        "not a sealed request",
        "ü.ü",
        None,
        7,
    ):
        assert requests.open(bad, "consent") is None, bad
    assert oauth.SealedRequests().open(sealed, "consent") is None  # another process's secret
    assert requests.open(sealed, "consent") is not None


def test_a_settled_request_is_refused_again_until_its_deadline(clock):
    requests = oauth.SealedRequests()
    request = {"id": "r1", "exp": clock.value + 600}
    assert requests.settle(request) and not requests.settle(request)
    clock.value += 599
    assert not requests.settle(request)
    clock.value += 1
    assert requests.settle({"id": "r2", "exp": clock.value + 600})
    assert requests.settled == 1  # r1 left at its deadline


def test_the_settled_table_is_capped_and_drops_the_nearest_deadline_first(clock, caplog):
    caplog.set_level(logging.WARNING, logger="cairn.oauth")
    assert oauth.SETTLED_MAX == 50_000
    requests = oauth.SealedRequests()
    for n in range(60_000):
        assert requests.settle({"id": f"r{n}", "exp": clock.value + 600 + n / 100})
    assert requests.settled == oauth.SETTLED_MAX
    assert not requests.settle({"id": "r59999", "exp": clock.value + 600 + 599.99})  # still held
    assert requests.settle({"id": "r0", "exp": clock.value + 600})  # dropped: once more, at most
    assert requests.settled == oauth.SETTLED_MAX
    warned = [r for r in caplog.records if r.name == "cairn.oauth"]
    assert len(warned) == 1 and warned[0].levelno == logging.WARNING  # one a minute at most
    clock.value += 60
    requests.settle({"id": "late", "exp": clock.value + 600})
    assert len([r for r in caplog.records if r.name == "cairn.oauth"]) == 2


def test_settling_costs_the_same_however_full_the_table(clock):
    """A scan of the table per call makes filling it grow with the square of its size; by
    the heap, eight times the requests take about eight times as long (linear_cost)."""

    def filling(n: int) -> Callable[[], None]:
        def fill() -> None:  # from an empty table each run
            requests, clock.value = oauth.SealedRequests(), 5000.0
            for k in range(n):
                assert requests.settle({"id": f"r{k}", "exp": clock.value + 600})
                clock.value += 0.01
            assert requests.settled == n

        return fill

    assert_near_linear(filling, 2_500)


def test_a_request_is_settled_once_until_it_expires(clock):
    requests = oauth.SealedRequests()
    first = requests.open(requests.seal({"x": 1}, "authorize", ttl=600), "authorize")
    in_another_tab = requests.open(requests.reseal(first, "consent"), "consent")
    other = requests.open(requests.seal({"x": 1}, "authorize", ttl=600), "authorize")
    assert requests.settle(first) is True
    assert requests.settle(first) is False
    assert requests.settle(in_another_tab) is False  # the same request, rendered twice
    assert requests.settle(other) is True
    assert requests.settled == 2
    clock.value += 600  # both have expired: they cannot open, so nothing needs remembering
    assert requests.settle(requests.open(requests.seal({}, "authorize", ttl=1), "authorize"))
    assert requests.settled == 1


# Over HTTP, against the real store


class World:
    """One app over a real store with two accounts. `raw` carries no credentials; a person's
    browser is its own client with its own cookie jar."""

    def __init__(self, tmp_path, public_url: str | None = BASE) -> None:
        self.db = tmp_path / "cairn.db"
        self.store = Store(self.db)
        self.ada, self.brook = self.tenant("Ada"), self.tenant("Brook")
        self.app = create_app(self.store, public_url=public_url)
        self.base = public_url or BASE  # where a browser finds it: a Secure cookie needs https

    def tenant(self, name: str) -> SimpleNamespace:
        view = self.store.for_account(self.store.create_account(name).id)
        view.create_project("core")
        return SimpleNamespace(name=name, view=view, key=view.create_key("laptop"))

    def browser(self, tenant: SimpleNamespace | None = None) -> TestClient:
        client = TestClient(self.app, base_url=self.base, follow_redirects=False)
        if tenant is not None:
            signed_in = client.post("/login", data={"key": tenant.key}, headers=SAME_ORIGIN)
            assert signed_in.status_code == 303
        return client

    def count(self, table: str) -> int:
        con = cairn.store.sqlite3.connect(self.db)
        try:
            return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        finally:
            con.close()


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    with TestClient(w.app, base_url=BASE, follow_redirects=False) as raw:
        w.raw = raw
        yield w
    w.store.close()


def at(monkeypatch, moment: datetime) -> None:
    monkeypatch.setattr(cairn.store.base, "_clock", lambda: moment)


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def register(client, name="unit-agent", uris=(REDIRECT,), **more):
    body = {"client_name": name, "redirect_uris": list(uris), **more}
    return client.post("/register", json={k: v for k, v in body.items() if v is not None})


def registered(world, name="unit-agent", uris=(REDIRECT,)) -> str:
    r = register(world.raw, name, uris)
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def authorize(client, client_id: str, challenge: str, **overrides):
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "state-123",
        "resource": RESOURCE,
        **overrides,
    }
    return client.get("/authorize?" + urlencode({k: v for k, v in params.items() if v is not None}))


def answer(response) -> dict[str, str]:
    """The query of the URL a response redirects to."""
    query = parse_qs(urlsplit(response.headers.get("location", "")).query)
    return {name: values[0] for name, values in query.items()}


class Hidden(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("type") == "hidden":
            self.fields[attributes["name"]] = attributes.get("value") or ""


def form_of(page) -> dict[str, str]:
    assert page.status_code == 200, page.text
    parser = Hidden()
    parser.feed(page.text)
    assert set(parser.fields) == {"request"}
    return parser.fields


def consent_url(browser, client_id: str, challenge: str, **overrides) -> str:
    r = authorize(browser, client_id, challenge, **overrides)
    assert r.status_code == 302 and urlsplit(r.headers["location"]).path == "/oauth/consent", r.text
    return r.headers["location"]


def decide(browser, fields: dict[str, str], decision: str | None = "approve"):
    data = fields if decision is None else {**fields, "decision": decision}
    return browser.post("/oauth/consent", data=data, headers=SAME_ORIGIN)


def exchange(client, client_id: str, code: str, verifier: str):
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    return client.post("/token", data=data)


def refresh(client, client_id: str, refresh_token: str):
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
    return client.post("/token", data=data)


def approved(world, browser, client_id: str) -> tuple[str, str]:
    """(code, verifier) after the person approved the client."""
    verifier, challenge = pkce()
    page = browser.get(consent_url(browser, client_id, challenge))
    done = decide(browser, form_of(page))
    assert done.status_code == 303 and done.headers["location"].startswith(REDIRECT + "?")
    return answer(done)["code"], verifier


def connected(world, tenant, name="unit-agent") -> SimpleNamespace:
    client_id = registered(world, name)
    code, verifier = approved(world, world.browser(tenant), client_id)
    tokens = exchange(world.raw, client_id, code, verifier)
    assert tokens.status_code == 200, tokens.text
    body = tokens.json()
    return SimpleNamespace(
        client_id=client_id, access=body["access_token"], refresh=body["refresh_token"], body=body
    )


def mcp(client, **headers):
    return client.post("/mcp", headers={**MCP_HEADERS, **headers}, json=TOOLS_LIST)


def bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


# Registration


@pytest.mark.parametrize(
    "method", [None, "none", "client_secret_post", "client_secret_basic", "private_key_jwt"]
)
def test_every_registered_client_is_a_public_client(world, method):
    r = register(world.raw, token_endpoint_auth_method=method, scope="anything at all")
    assert r.status_code == 201, r.text
    body = r.json()
    assert re.fullmatch(r"client_[0-9a-f]{32}", body["client_id"])
    assert body["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in body and "client_secret_expires_at" not in body
    assert body["grant_types"] == ["authorization_code", "refresh_token"]
    assert (body["response_types"], body["scope"]) == (["code"], "cairn")
    assert (body["client_name"], body["redirect_uris"]) == ("unit-agent", [REDIRECT])
    assert r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (b"not json", "invalid_client_metadata"),
        (b"[]", "invalid_client_metadata"),
        (b'"https://app.example/cb"', "invalid_client_metadata"),
        (json.dumps({"client_name": "x"}), "invalid_redirect_uri"),
        (json.dumps({"redirect_uris": REDIRECT}), "invalid_redirect_uri"),
        (json.dumps({"redirect_uris": [REDIRECT] * 11}), "invalid_redirect_uri"),
        (json.dumps({"redirect_uris": [REDIRECT, 7]}), "invalid_client_metadata"),
        (json.dumps({"redirect_uris": [REDIRECT, None]}), "invalid_client_metadata"),
        (json.dumps({"redirect_uris": ["https://a.example/" + "x" * 238]}), "invalid_redirect_uri"),
        # A browser reads this as loopback, and so does the SDK's URL type: refused as written.
        (json.dumps({"redirect_uris": ["http://127.1/cb"]}), "invalid_redirect_uri"),
        (
            json.dumps({"redirect_uris": ["http://localhost\\@evil.example/"]}),
            "invalid_redirect_uri",
        ),
        (json.dumps({"redirect_uris": [REDIRECT], "client_name": 7}), "invalid_client_metadata"),
        (
            json.dumps({"redirect_uris": [REDIRECT], "grant_types": ["client_credentials"]}),
            "invalid_client_metadata",
        ),
        (
            json.dumps({"redirect_uris": [REDIRECT], "response_types": ["token"]}),
            "invalid_client_metadata",
        ),
    ],
)
def test_registration_refuses_what_it_cannot_serve_and_stores_nothing(world, body, error):
    r = world.raw.post("/register", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 400, r.text
    assert r.json()["error"] == error and r.json()["error_description"]
    assert world.count("oauth_clients") == 0


def test_registration_at_its_limits(world):
    longest = "https://a.example/" + "x" * 237
    r = register(world.raw, "n" * 80, [longest] * 10)
    assert r.status_code == 201 and r.json()["redirect_uris"] == [longest] * 10
    assert world.raw.post("/register", content=b" " * (64 * 1024 + 1)).status_code == 413
    assert world.count("oauth_clients") == 1


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        ("  Claude \n Code\t(laptop) ", "Claude Code (laptop)"),
        ("evil‮edoC edualC", "eviledoC edualC"),  # a right-to-left override, removed
        ("zero​width\x00null\x1b[31m", "zerowidthnull[31m"),
        ("x" * 200, "x" * 80),
        ("a" * 79 + " tail", "a" * 79),
        ("", "Unnamed client"),
        (" ​\n", "Unnamed client"),
        (None, "Unnamed client"),
    ],
)
def test_a_client_name_is_cleaned_and_cut_before_it_is_kept(world, given, kept):
    r = register(world.raw, given)
    assert r.status_code == 201 and r.json()["client_name"] == kept
    assert world.store.get_client(r.json()["client_id"]).name == kept


def test_a_client_name_is_shown_escaped(world):
    client_id = registered(world, "<script>alert(1)</script>")
    browser = world.browser(world.ada)
    page = browser.get(consent_url(browser, client_id, pkce()[1]))
    assert "<script>" not in page.text and "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text
    code, verifier = approved(world, browser, client_id)
    assert exchange(world.raw, client_id, code, verifier).status_code == 200
    assert "<script>" not in browser.get("/agents").text


def test_registration_is_limited_per_client_address(world):
    here = TestClient(world.app, base_url=BASE, client=("203.0.113.9", 40000))
    for _ in range(5):  # refused registrations store nothing, and do not count
        assert register(here, uris=["http://evil.example/cb"]).status_code == 400
    for n in range(20):
        assert register(here, f"agent-{n}").status_code == 201, n
    cross = {"origin": "https://app.example"}
    limited = here.post("/register", headers=cross, json={"redirect_uris": [REDIRECT]})
    assert limited.status_code == 429 and limited.json()["error"] == "too_many_requests"
    assert int(limited.headers["retry-after"]) > 0
    assert limited.headers["access-control-allow-origin"] == "*"  # a browser client can read it
    same_address_other_port = TestClient(world.app, base_url=BASE, client=("203.0.113.9", 40001))
    assert register(same_address_other_port).status_code == 429
    elsewhere = TestClient(world.app, base_url=BASE, client=("203.0.113.10", 40000))
    assert register(elsewhere).status_code == 201
    assert world.count("oauth_clients") == 21


def test_a_redirect_uri_without_a_path_matches_the_request_that_names_it(world):
    """An empty path and "/" are one URL. The SDK keeps the two spellings apart when it reads
    a registration, and joins them when it reads an authorization request."""
    client_id = registered(world, uris=["http://127.0.0.1:33418"])
    browser = world.browser(world.ada)
    for spelling in ("http://127.0.0.1:33418", "http://127.0.0.1:33418/"):
        r = authorize(browser, client_id, pkce()[1], redirect_uri=spelling)
        assert r.status_code == 302 and urlsplit(r.headers["location"]).path == "/oauth/consent"
    other_port = authorize(browser, client_id, pkce()[1], redirect_uri="http://127.0.0.1:33419/")
    assert other_port.status_code == 400 and "location" not in other_port.headers


def test_a_redirect_uri_is_checked_again_in_the_form_it_is_kept_in(world, monkeypatch):
    """The policy is applied to what the client wrote and to what the URL library made of it,
    which is what cairn stores and later redirects to. Here the library misbehaves."""

    class Rewriting:
        def validate_python(self, uri):
            return "http://evil.example/cb"

    monkeypatch.setattr(oauth, "_ANY_URL", Rewriting())
    r = register(world.raw)
    assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri"
    assert world.count("oauth_clients") == 0


# Authorization requests


def test_authorize_checks_the_request_before_anyone_is_asked(world):
    client_id = registered(world)
    _, challenge = pkce()
    unknown = authorize(world.raw, "client_" + "0" * 32, challenge)
    assert unknown.status_code == 400 and "location" not in unknown.headers
    elsewhere = authorize(world.raw, client_id, challenge, redirect_uri="https://evil.example/cb")
    assert elsewhere.status_code == 400 and "location" not in elsewhere.headers
    for overrides, error in (
        ({"code_challenge": None}, "invalid_request"),
        ({"code_challenge": "too-short"}, "invalid_request"),
        ({"code_challenge": challenge + "="}, "invalid_request"),
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"response_type": "token"}, "unsupported_response_type"),
        ({"resource": "https://elsewhere.example/mcp"}, "invalid_target"),
        ({"resource": f"{BASE}/api"}, "invalid_target"),
        ({"resource": f"{RESOURCE}?x=1"}, "invalid_target"),
        ({"resource": "not a url"}, "invalid_target"),
    ):
        r = authorize(world.raw, client_id, challenge, **overrides)
        assert r.status_code == 302 and r.headers["location"].startswith(REDIRECT + "?"), overrides
        assert (answer(r)["error"], answer(r)["state"]) == (error, "state-123"), overrides
    assert world.raw.post("/authorize", data={"client_id": client_id}).status_code == 405


@pytest.mark.parametrize(
    "resource",
    [None, RESOURCE, RESOURCE + "/", "HTTP://127.0.0.1:8765/mcp", "http://127.0.0.1:8765/MCP"],
)
def test_authorize_accepts_this_servers_mcp_url_however_it_is_spelled(world, resource):
    if resource is not None and resource.endswith("/MCP"):  # a path is case-sensitive
        r = authorize(world.raw, registered(world), pkce()[1], resource=resource)
        assert answer(r)["error"] == "invalid_target"
        return
    client_id = registered(world)
    browser = world.browser(world.ada)
    verifier, challenge = pkce()
    page = browser.get(consent_url(browser, client_id, challenge, resource=resource))
    code = answer(decide(browser, form_of(page)))["code"]
    tokens = exchange(world.raw, client_id, code, verifier).json()
    assert mcp(world.raw, **bearer(tokens["access_token"])).status_code == 200


def test_whatever_scope_a_client_asks_for_it_is_granted_the_one_scope(world):
    client_id = registered(world)
    browser = world.browser(world.ada)
    verifier, challenge = pkce()
    url = consent_url(browser, client_id, challenge, scope="claudeai profile offline_access")
    code = answer(decide(browser, form_of(browser.get(url))))["code"]
    tokens = exchange(world.raw, client_id, code, verifier).json()
    assert tokens["scope"] == "cairn" and tokens["expires_in"] == 3600
    assert tokens["token_type"] == "Bearer"
    again = refresh(world.raw, client_id, tokens["refresh_token"]).json()
    assert again["scope"] == "cairn"


# The consent step


def test_the_consent_page_allows_its_form_to_reach_one_origin_only(world):
    client_id = registered(world, uris=["https://Claude.AI:443/api/mcp/auth_callback", REDIRECT])
    browser = world.browser(world.ada)
    url = consent_url(
        browser, client_id, pkce()[1], redirect_uri="https://claude.ai/api/mcp/auth_callback"
    )
    page = browser.get(url)
    directives = [d.strip() for d in page.headers["content-security-policy"].split(";")]
    assert "form-action 'self' https://claude.ai" in directives
    assert len([d for d in directives if d.startswith("form-action")]) == 1
    assert {"default-src 'none'", "frame-ancestors 'none'", "base-uri 'none'"} <= set(directives)
    assert page.headers["cache-control"] == "no-store"
    assert "https://claude.ai/api/mcp/auth_callback" in page.text
    assert browser.get("/").headers["content-security-policy"].count("claude.ai") == 0


def test_a_tampered_or_missing_consent_request_shows_no_form(world):
    client_id = registered(world)
    browser = world.browser(world.ada)
    url = consent_url(browser, client_id, pkce()[1])
    sealed = parse_qs(urlsplit(url).query)["request"][0]
    body, _, signature = sealed.partition(".")
    flipped = ("A" if body[10] != "A" else "B").join((body[:10], body[11:]))
    for bad in (
        f"/oauth/consent?request={flipped}.{signature}",
        f"/oauth/consent?request={body}.{signature[::-1]}",
        f"/oauth/consent?request={body}",
        "/oauth/consent?request=",
        "/oauth/consent?request=x.y",
        "/oauth/consent",
    ):
        page = browser.get(bad)
        assert page.status_code == 400, bad
        assert (
            "decision" not in page.text and "<form" not in page.text.split('<main id="main">')[1]
        ), bad
    assert browser.get(url).status_code == 200  # the untouched one still opens


def test_a_decision_needs_the_form_the_consent_page_made(world):
    client_id = registered(world)
    browser = world.browser(world.ada)
    url = consent_url(browser, client_id, pkce()[1])
    from_the_url = parse_qs(urlsplit(url).query)["request"][0]
    fields = form_of(browser.get(url))
    assert fields["request"] != from_the_url
    body, _, signature = fields["request"].partition(".")
    changed = ("A" if signature[0] != "A" else "B") + signature[1:]
    for forged in (from_the_url, f"{body}.{changed}", "", "x.y"):
        r = decide(browser, {"request": forged})
        assert r.status_code == 400 and "location" not in r.headers, forged
    assert decide(browser, {}).status_code == 400
    assert world.count("oauth_codes") == 0
    assert decide(browser, fields).status_code == 303  # none of that used the real one up


def test_a_decision_only_counts_from_the_account_that_was_shown_the_page(world):
    client_id = registered(world)
    ada, brook = world.browser(world.ada), world.browser(world.brook)
    fields = form_of(ada.get(consent_url(ada, client_id, pkce()[1])))
    stolen = decide(brook, fields)
    assert stolen.status_code == 400 and "location" not in stolen.headers
    assert world.count("oauth_codes") == 0
    signed_out = decide(world.browser(), fields)
    assert (signed_out.status_code, signed_out.headers["location"]) == (303, "/login")
    assert decide(ada, fields).status_code == 303
    assert world.brook.view.list_grants() == [] and world.count("oauth_codes") == 1


def test_a_request_is_decided_once_however_often_its_page_was_opened(world):
    client_id = registered(world)
    browser = world.browser(world.ada)
    url = consent_url(browser, client_id, pkce()[1])
    first_tab, second_tab = form_of(browser.get(url)), form_of(browser.get(url))
    assert "code" in answer(decide(browser, first_tab))
    for fields, decision in ((first_tab, "approve"), (second_tab, "approve"), (second_tab, "deny")):
        again = decide(browser, fields, decision)
        assert again.status_code == 400 and "location" not in again.headers
    assert world.count("oauth_codes") == 1
    assert browser.get(url).status_code == 200  # showing a page writes and settles nothing


def test_an_unknown_decision_decides_nothing(world):
    client_id = registered(world)
    browser = world.browser(world.ada)
    fields = form_of(browser.get(consent_url(browser, client_id, pkce()[1])))
    for decision in ("maybe", "", "APPROVE", None):
        r = decide(browser, fields, decision)
        assert r.status_code == 400 and "location" not in r.headers, decision
    assert answer(decide(browser, fields, "deny")) == {
        "error": "access_denied",
        "state": "state-123",
    }


def test_a_consent_request_expires_after_ten_minutes(world, clock):
    client_id = registered(world)
    browser = world.browser(world.ada)
    url = consent_url(browser, client_id, pkce()[1])
    clock.value += 599.9
    fields = form_of(browser.get(url))  # opened in time
    clock.value += 0.1
    assert browser.get(url).status_code == 400
    late = decide(browser, fields)  # the form does not outlive the request it was made from
    assert late.status_code == 400 and "location" not in late.headers
    assert world.count("oauth_codes") == 0


def test_a_restart_only_makes_a_pending_approval_fail(world, tmp_path):
    client_id = registered(world)
    browser = world.browser(world.ada)
    url = consent_url(browser, client_id, pkce()[1])
    fields = form_of(browser.get(url))
    restarted = create_app(world.store, public_url=BASE)  # a new process: a new secret
    again = TestClient(restarted, base_url=BASE, follow_redirects=False)
    again.post("/login", data={"key": world.ada.key}, headers=SAME_ORIGIN)
    assert again.get(url).status_code == 400
    assert decide(again, fields).status_code == 400
    fresh = consent_url(again, client_id, pkce()[1])  # the registration itself survived
    assert decide(again, form_of(again.get(fresh))).status_code == 303


def test_approving_a_client_that_was_removed_meanwhile_fails_cleanly(world, monkeypatch):
    client_id = registered(world)
    browser = world.browser(world.ada)
    fields = form_of(browser.get(consent_url(browser, client_id, pkce()[1])))
    con = cairn.store.sqlite3.connect(world.db)
    con.execute("DELETE FROM oauth_clients")
    con.commit()
    con.close()
    r = decide(browser, fields)
    assert r.status_code == 400 and "location" not in r.headers
    assert world.count("oauth_codes") == 0


def test_arriving_at_the_consent_page_signed_out(world):
    client_id = registered(world)
    url = consent_url(world.raw, client_id, pkce()[1])
    path = urlsplit(url).path + "?" + urlsplit(url).query
    cross_site = {"sec-fetch-site": "cross-site"}

    bounced = world.raw.get(url, headers=cross_site)
    assert bounced.status_code == 200 and f'content="0; url={path}"' in bounced.text
    assert bounced.headers["cache-control"] == "no-store" and "<form" not in bounced.text
    for site in ("same-origin", "same-site", "none"):  # the cookie would have come along
        r = world.raw.get(url, headers={"sec-fetch-site": site})
        assert r.status_code == 303 and answer(r) == {"next": path}, site
    # A query that is not plainly a sealed request is never written into a page.
    odd = world.raw.get("/oauth/consent?request=%22%3E&x=';url=//evil", headers=cross_site)
    assert odd.status_code == 303 and odd.headers["location"].startswith("/login?next=")
    bare = world.raw.get("/oauth/consent", headers=cross_site)
    assert (bare.status_code, bare.headers["location"]) == (303, "/login?next=%2Foauth%2Fconsent")
    # Only a page view comes back after signing in; a post just needs a session.
    posted = world.raw.post(url, data={"decision": "approve"}, headers=SAME_ORIGIN)
    assert (posted.status_code, posted.headers["location"]) == (303, "/login")
    # Signed in, even a cross-site arrival sees the page at once.
    browser = world.browser(world.ada)
    assert "decision" in browser.get(url, headers=cross_site).text


def test_other_ui_pages_do_not_come_back_after_signing_in(world):
    for path in ("/", "/agents", "/setup", "/search?q=x"):
        r = world.raw.get(path, headers={"sec-fetch-site": "cross-site"})
        assert (r.status_code, r.headers["location"]) == (303, "/login"), path
    near = world.raw.get("/oauth/consentx?request=a.b", headers={"sec-fetch-site": "cross-site"})
    assert near.status_code == 404 and "location" not in near.headers  # no page: nowhere to go


# Signing in on the way


@pytest.mark.parametrize(
    ("target", "kept"),
    [
        ("/oauth/consent?request=abc.def", "/oauth/consent?request=abc.def"),
        ("/agents", "/agents"),
        ("/", "/"),
        (None, "/"),
        ("", "/"),
        ("agents", "/"),
        ("https://evil.example/", "/"),
        ("//evil.example/", "/"),
        ("/\\evil.example/", "/"),
        ("/\\/evil.example/", "/"),
        ("/\t/evil.example/", "/"),
        ("/\n/evil.example/", "/"),
        ("/ /evil.example", "/"),
        ("/agents\r\nSet-Cookie: x=1", "/"),
        ("/agents\x00", "/"),
        ("/ünï", "/"),
        ("javascript:alert(1)", "/"),
    ],
)
def test_sign_in_only_goes_on_to_a_path_on_this_server(world, target, kept):
    shown = world.raw.get("/login", params=None if target is None else {"next": target})
    assert shown.status_code == 200
    hidden = Hidden()
    hidden.feed(shown.text)
    assert hidden.fields == {"next": kept}

    data = {"key": world.ada.key} | ({} if target is None else {"next": target})
    signed_in = world.browser().post("/login", data=data, headers=SAME_ORIGIN)
    assert (signed_in.status_code, signed_in.headers["location"]) == (303, kept)

    refused = world.browser().post("/login", data={**data, "key": "wrong"}, headers=SAME_ORIGIN)
    again = Hidden()
    again.feed(refused.text)
    assert refused.status_code == 401 and again.fields == {"next": kept}  # kept for the retry


# Tokens on /mcp, and their expiry as a client sees it


def test_the_challenge_says_whether_a_credential_was_refused(world):
    metadata = f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"'
    nothing = mcp(world.raw)
    assert nothing.status_code == 401
    assert nothing.headers["www-authenticate"] == f"Bearer {metadata}"
    for headers in (bearer("cairn.at.never-issued"), bearer("a-key"), {"x-api-key": "a-key"}):
        refused = mcp(world.raw, **headers)
        assert refused.status_code == 401
        assert refused.headers["www-authenticate"] == f'Bearer error="invalid_token", {metadata}'
        assert "never-issued" not in refused.text and "a-key" not in refused.text
    assert "www-authenticate" not in world.raw.get("/api/projects").headers  # keys only: no OAuth


def test_an_access_token_is_a_bearer_credential_for_mcp_and_nothing_else(world):
    grant = connected(world, world.ada)
    assert mcp(world.raw, **bearer(grant.access)).status_code == 200
    assert mcp(world.raw, **{"x-api-key": grant.access}).status_code == 401
    assert mcp(world.raw, **bearer(grant.refresh)).status_code == 401
    # A key in x-api-key still decides, whatever the Authorization header holds.
    both = {"x-api-key": world.brook.key, **bearer(grant.access)}
    assert mcp(world.raw, **both).status_code == 200
    for headers in (bearer(grant.access), {"x-api-key": grant.access}):
        assert world.raw.get("/api/projects", headers=headers).status_code == 401
    browser = world.browser()
    browser.cookies.set("cairn_session", grant.access)
    assert browser.get("/").status_code == 303


def test_with_oauth_off_a_token_issued_earlier_is_not_a_credential(world):
    grant = connected(world, world.ada)
    off = TestClient(create_app(world.store, public_url=None), base_url=BASE)
    refused = mcp(off, **bearer(grant.access))
    assert refused.status_code == 401 and "www-authenticate" not in refused.headers
    assert mcp(world.raw, **bearer(grant.access)).status_code == 200  # still fine where it is on


def test_a_code_expires_two_minutes_after_the_approval(world, monkeypatch):
    at(monkeypatch, T0)
    client_id = registered(world)
    browser = world.browser(world.ada)
    code, verifier = approved(world, browser, client_id)
    late_code, late_verifier = approved(world, browser, client_id)
    at(monkeypatch, T0 + timedelta(seconds=119))
    assert exchange(world.raw, client_id, code, verifier).status_code == 200
    at(monkeypatch, T0 + timedelta(seconds=120))
    late = exchange(world.raw, client_id, late_code, late_verifier)
    assert late.status_code == 400 and late.json()["error"] == "invalid_grant"
    assert len(world.ada.view.list_grants()) == 1


def test_replaying_a_code_ends_the_grant_it_made(world):
    client_id = registered(world)
    code, verifier = approved(world, world.browser(world.ada), client_id)
    tokens = exchange(world.raw, client_id, code, verifier).json()
    assert mcp(world.raw, **bearer(tokens["access_token"])).status_code == 200
    replay = exchange(world.raw, client_id, code, verifier)
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
    assert mcp(world.raw, **bearer(tokens["access_token"])).status_code == 401
    assert refresh(world.raw, client_id, tokens["refresh_token"]).status_code == 400


def test_an_access_token_expires_after_an_hour_and_a_refresh_brings_the_next(world, monkeypatch):
    at(monkeypatch, T0)
    grant = connected(world, world.ada)
    at(monkeypatch, T0 + timedelta(minutes=59, seconds=59))
    assert mcp(world.raw, **bearer(grant.access)).status_code == 200
    at(monkeypatch, T0 + timedelta(hours=1))
    expired = mcp(world.raw, **bearer(grant.access))
    assert (
        expired.status_code == 401
        and 'error="invalid_token"' in expired.headers["www-authenticate"]
    )
    renewed = refresh(world.raw, grant.client_id, grant.refresh)
    assert renewed.status_code == 200
    assert mcp(world.raw, **bearer(renewed.json()["access_token"])).status_code == 200


def test_a_grant_ends_30_days_after_its_last_refresh(world, monkeypatch):
    at(monkeypatch, T0)
    grant = connected(world, world.ada)
    at(monkeypatch, T0 + timedelta(days=29, hours=23))
    renewed = refresh(world.raw, grant.client_id, grant.refresh).json()
    at(monkeypatch, T0 + timedelta(days=59, hours=22))
    assert "unit-agent" in world.browser(world.ada).get("/agents").text
    at(monkeypatch, T0 + timedelta(days=59, hours=23))
    assert "unit-agent" not in world.browser(world.ada).get("/agents").text  # before any write
    late = refresh(world.raw, grant.client_id, renewed["refresh_token"])
    assert late.status_code == 400 and late.json()["error"] == "invalid_grant"


def test_a_refresh_token_belongs_to_its_client(world):
    grant = connected(world, world.ada)
    other = registered(world, "another-agent")
    stolen = refresh(world.raw, other, grant.refresh)
    assert stolen.status_code == 400 and stolen.json()["error"] == "invalid_grant"
    unknown = refresh(world.raw, "client_" + "0" * 32, grant.refresh)
    assert unknown.status_code == 401 and unknown.json()["error"] == "invalid_client"
    assert refresh(world.raw, grant.client_id, grant.refresh).status_code == 200  # untouched


def dump(db) -> str:
    con = cairn.store.sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def test_moving_the_public_url_ends_the_grants_made_at_the_old_one(world):
    grant = connected(world, world.ada)
    moved = "http://localhost:8765"
    there = TestClient(create_app(world.store, public_url=moved), base_url=moved)
    refused = mcp(there, **bearer(grant.access))
    assert refused.status_code == 401
    assert 'error="invalid_token"' in refused.headers["www-authenticate"]
    before = dump(world.db)
    late = refresh(there, grant.client_id, grant.refresh)
    assert late.status_code == 400 and late.json()["error"] == "invalid_grant"
    assert dump(world.db) == before
    # Nothing was ended: at the address it was approved for, the grant still works.
    assert mcp(world.raw, **bearer(grant.access)).status_code == 200
    assert refresh(world.raw, grant.client_id, grant.refresh).status_code == 200


def test_a_code_approved_at_one_address_is_not_redeemed_at_another(world):
    client_id = registered(world)
    code, verifier = approved(world, world.browser(world.ada), client_id)
    moved = "http://localhost:8765"
    there = TestClient(create_app(world.store, public_url=moved), base_url=moved)
    refused = exchange(there, client_id, code, verifier)
    assert refused.status_code == 400 and refused.json()["error"] == "invalid_grant"
    assert exchange(world.raw, client_id, code, verifier).status_code == 200  # not burned


@pytest.mark.parametrize(
    "resource",
    [
        "https://elsewhere.example/mcp",
        f"{BASE}/api",
        f"{BASE}/mcp?x=1",
        "",
        "not a url",
    ],
)
def test_a_token_request_for_another_resource_is_refused_before_anything_is_written(
    world, resource
):
    client_id = registered(world)
    code, verifier = approved(world, world.browser(world.ada), client_id)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    before = dump(world.db)
    for named in ([resource], [RESOURCE, resource], [resource, RESOURCE]):
        refused = world.raw.post("/token", data={**data, "resource": named})
        assert refused.status_code == 400 and refused.json()["error"] == "invalid_target"
        assert refused.headers["cache-control"] == "no-store"
    assert dump(world.db) == before  # the code was not spent
    tokens = world.raw.post("/token", data={**data, "resource": RESOURCE}).json()

    before = dump(world.db)
    renewal = {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": client_id,
    }
    refused = world.raw.post("/token", data={**renewal, "resource": resource})
    assert refused.status_code == 400 and refused.json()["error"] == "invalid_target"
    assert dump(world.db) == before  # not rotated
    assert mcp(world.raw, **bearer(tokens["access_token"])).status_code == 200


@pytest.mark.parametrize("resource", [RESOURCE, RESOURCE + "/", "HTTP://127.0.0.1:8765/mcp"])
def test_a_token_request_may_name_this_servers_mcp_url_however_it_is_spelled(world, resource):
    client_id = registered(world)
    code, verifier = approved(world, world.browser(world.ada), client_id)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "client_id": client_id,
        "code_verifier": verifier,
        "resource": resource,
    }
    tokens = world.raw.post("/token", data=data)
    assert tokens.status_code == 200, tokens.text
    renewal = {
        "grant_type": "refresh_token",
        "refresh_token": tokens.json()["refresh_token"],
        "client_id": client_id,
        "resource": resource,
    }
    assert world.raw.post("/token", data=renewal).status_code == 200


def test_a_thief_who_rotates_twice_ends_the_grant_when_the_client_refreshes(world):
    """The reviewer's sequence over HTTP: the thief copied the current refresh token and
    rotates twice; the client then refreshes with the token it holds, two rotations back."""
    grant = connected(world, world.ada)
    thief = refresh(world.raw, grant.client_id, grant.refresh).json()
    thief = refresh(world.raw, grant.client_id, thief["refresh_token"]).json()
    assert mcp(world.raw, **bearer(thief["access_token"])).status_code == 200
    caught = refresh(world.raw, grant.client_id, grant.refresh)
    assert caught.status_code == 400 and caught.json()["error"] == "invalid_grant"
    assert world.ada.view.list_grants() == []
    assert mcp(world.raw, **bearer(thief["access_token"])).status_code == 401
    assert refresh(world.raw, grant.client_id, thief["refresh_token"]).status_code == 400


def test_a_grant_rotates_at_most_6_times_an_hour_however_often_the_server_restarts(
    world, monkeypatch
):
    start = datetime.now(UTC)
    at(monkeypatch, start)
    grant, other = connected(world, world.ada), connected(world, world.brook, "other-agent")
    latest = grant.refresh
    for minutes in range(ROTATIONS):
        at(monkeypatch, start + timedelta(minutes=minutes))
        renewed = refresh(world.raw, grant.client_id, latest)
        assert renewed.status_code == 200, renewed.text
        latest, access = renewed.json()["refresh_token"], renewed.json()["access_token"]
    before = dump(world.db)
    refused = refresh(world.raw, grant.client_id, latest)
    assert refused.status_code == 429 and refused.json()["error"] == "too_many_requests"
    assert refused.headers["retry-after"] == str(ROTATION_WINDOW)
    assert refused.headers["cache-control"] == "no-store"
    assert dump(world.db) == before  # nothing written: the tokens in hand still work
    assert mcp(world.raw, **bearer(access)).status_code == 200
    # The count is in the database: a server rebuilt on it, with a store opened anew, counts
    # the same six.
    store = Store(world.db)
    try:
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as rebuilt:
            again = refresh(rebuilt, grant.client_id, latest)
            assert again.status_code == 429 and dump(world.db) == before
            # Another grant has its own count.
            assert refresh(rebuilt, other.client_id, other.refresh).status_code == 200
            # An hour after the first of them, one more is allowed, with the same token.
            at(monkeypatch, start + timedelta(hours=1))
            assert refresh(rebuilt, grant.client_id, latest).status_code == 200
    finally:
        store.close()


def test_replaying_a_rotated_token_ends_the_grant_even_past_the_rate(world):
    grant = connected(world, world.ada)
    latest = grant.refresh
    for _ in range(ROTATIONS):
        latest = refresh(world.raw, grant.client_id, latest).json()["refresh_token"]
    assert refresh(world.raw, grant.client_id, latest).status_code == 429
    replay = refresh(world.raw, grant.client_id, grant.refresh)  # six rotations back
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
    assert world.ada.view.list_grants() == []


def test_revocation_for_public_clients(world):
    grant = connected(world, world.ada)
    other = registered(world, "another-agent")
    revoke = lambda **data: world.raw.post("/revoke", data=data)  # noqa: E731

    assert revoke(token="cairn.at.never-issued", client_id=grant.client_id).status_code == 200
    assert revoke(token=grant.access, client_id=other).status_code == 200  # not theirs: no effect
    assert mcp(world.raw, **bearer(grant.access)).status_code == 200
    assert revoke(token=grant.access, client_id="client_" + "0" * 32).status_code == 401
    assert revoke(token=grant.access).status_code == 400
    assert revoke(client_id=grant.client_id).status_code == 400
    assert mcp(world.raw, **bearer(grant.access)).status_code == 200

    renewed = refresh(world.raw, grant.client_id, grant.refresh).json()
    done = revoke(token=renewed["refresh_token"], client_id=grant.client_id, token_type_hint="x")
    assert done.status_code == 200 and done.headers["cache-control"] == "no-store"
    assert mcp(world.raw, **bearer(renewed["access_token"])).status_code == 401
    assert world.ada.view.list_grants() == []


def test_the_whole_flow_behind_an_https_public_url(tmp_path):
    """What a deployment runs: https, a public host name, a Secure cookie, a web client."""
    issuer, redirect = "https://cairn.example.net", "https://app.example/oauth/callback"
    world = World(tmp_path, issuer + "/")
    with TestClient(world.app, base_url=issuer, follow_redirects=False) as raw:
        refused = mcp(raw)
        prm_url = f"{issuer}/.well-known/oauth-protected-resource/mcp"
        assert refused.headers["www-authenticate"] == f'Bearer resource_metadata="{prm_url}"'
        prm = raw.get("/.well-known/oauth-protected-resource/mcp").json()
        meta = raw.get("/.well-known/oauth-authorization-server").json()
        assert (prm["resource"], prm["authorization_servers"]) == (f"{issuer}/mcp", [issuer])
        assert (meta["issuer"], meta["token_endpoint"]) == (issuer, f"{issuer}/token")

        registered_ = register(raw, "web-agent", [redirect])
        assert registered_.status_code == 201, registered_.text
        client_id = registered_.json()["client_id"]
        verifier, challenge = pkce()
        target = {"redirect_uri": redirect, "resource": prm["resource"]}
        url = consent_url(raw, client_id, challenge, **target)

        browser = TestClient(world.app, base_url=issuer, follow_redirects=False)
        signed_in = browser.post(
            "/login", data={"key": world.ada.key, "next": url}, headers={"origin": issuer}
        )
        assert signed_in.headers["location"] == url
        assert "; secure" in signed_in.headers["set-cookie"].lower()
        page = browser.get(url)
        policy = [d.strip() for d in page.headers["content-security-policy"].split(";")]
        assert "form-action 'self' https://app.example" in policy
        done = browser.post("/oauth/consent", data=form_of(page) | {"decision": "approve"})
        assert done.status_code == 303 and done.headers["location"].startswith(redirect + "?code=")
        tokens = raw.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": answer(done)["code"],
                "redirect_uri": redirect,
                "client_id": client_id,
                "code_verifier": verifier,
                "resource": prm["resource"],
            },
            headers={"origin": "https://app.example"},
        )
        assert tokens.status_code == 200 and tokens.headers["access-control-allow-origin"] == "*"
        assert mcp(raw, **bearer(tokens.json()["access_token"])).status_code == 200
        assert "web-agent" in browser.get("/agents").text
    world.store.close()


# Discovery and the pages


def test_the_metadata_describes_what_cairn_does(world):
    cross = {"origin": "https://app.example"}
    meta = world.raw.get("/.well-known/oauth-authorization-server", headers=cross)
    assert meta.headers["access-control-allow-origin"] == "*"
    body = meta.json()
    assert body["token_endpoint_auth_methods_supported"] == ["none"]
    assert body["revocation_endpoint_auth_methods_supported"] == ["none"]
    assert body["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert (body["response_types_supported"], body["scopes_supported"]) == (["code"], ["cairn"])
    prm = world.raw.get("/.well-known/oauth-protected-resource/mcp", headers=cross)
    assert prm.headers["access-control-allow-origin"] == "*"
    assert prm.json()["scopes_supported"] == ["cairn"]
    assert prm.json()["bearer_methods_supported"] == ["header"]


@pytest.mark.parametrize("public_url", [BASE, None])
def test_an_unknown_well_known_path_is_a_404_not_a_login_page(tmp_path, public_url):
    world = World(tmp_path, public_url)
    client = TestClient(world.app, base_url=BASE, follow_redirects=False)
    try:
        for path in (
            "/.well-known/openid-configuration",
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-authorization-server/mcp",
        ):
            assert client.get(path).status_code == 404, path
        consent = client.get("/oauth/consent").status_code  # a UI page when OAuth is on
        assert consent == (303 if public_url else 404)
        assert client.get("/authorized").status_code == 404  # no page, signed in or not
    finally:
        world.store.close()


def test_the_agents_page(world):
    browser = world.browser(world.ada)
    empty = browser.get("/agents")
    assert empty.status_code == 200 and "No agent is connected" in empty.text
    assert 'href="/workspace"' in browser.get("/").text  # in the navigation
    assert 'href="/agents"' in browser.get("/workspace").text  # on the workspace page
    grant = connected(world, world.ada)
    page = browser.get("/agents")
    (action,) = re.findall(r'href="(/agents/[^"/]+/revoke)"', page.text)
    assert "unit-agent" in page.text and grant.access not in page.text
    asked = browser.get(action)  # the page before it: its one form, and nothing ended
    assert asked.status_code == 200 and f'action="{action}"' in asked.text
    cross = browser.post(action, headers={"sec-fetch-site": "cross-site"})
    assert cross.status_code == 403 and mcp(world.raw, **bearer(grant.access)).status_code == 200
    unknown = browser.post("/agents/grant_000000000000/revoke", headers=SAME_ORIGIN)
    foreign = world.browser(world.brook).post(action, headers=SAME_ORIGIN)
    assert unknown.status_code == foreign.status_code == 404
    strip = lambda r, i: r.text.replace(i, "ID")  # noqa: E731
    assert strip(unknown, "grant_000000000000") == strip(foreign, action.split("/")[2])
    done = browser.post(action, headers=SAME_ORIGIN)
    assert (done.status_code, done.headers["location"]) == (303, "/agents")
    assert mcp(world.raw, **bearer(grant.access)).status_code == 401
    assert browser.post(action, headers=SAME_ORIGIN).status_code == 404


@pytest.mark.parametrize(
    ("public_url", "by_url"),
    [(BASE, True), ("https://cairn.example.net", True), ("http://cairn.example.net", False)],
)
def test_setup_explains_connecting_by_url_when_it_is_possible(tmp_path, public_url, by_url):
    world = World(tmp_path, public_url)
    try:
        text = world.browser(world.ada).get("/setup").text
    finally:
        world.store.close()
    assert f"{public_url}/mcp" in text and "With only the URL" in text
    assert ("approve" in text) is by_url
    assert ("--public-url" in text) is not by_url
    assert world.ada.key not in text and "cairn.env" in text
