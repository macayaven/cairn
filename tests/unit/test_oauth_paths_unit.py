"""Unit tests for the failure paths of cairn's OAuth server that the frozen tests leave open.

Each one names the branch it pins: a row of the review of src/cairn/oauth.py and
src/cairn/store/grants.py, or a branch of the MCP SDK's token handler as cairn mounts it.
The app runs over the real store, driven through Starlette's TestClient (helpers from
test_oauth_unit); the store tests that need a waiting write hold the database's write lock
from a second connection.
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest
from starlette.testclient import TestClient
from test_oauth_unit import (
    BASE,
    REDIRECT,
    RESOURCE,
    World,
    answer,
    approved,
    bearer,
    connected,
    consent_url,
    decide,
    dump,
    exchange,
    form_of,
    mcp,
    pkce,
    refresh,
    register,
    registered,
)

import cairn.store
from cairn import oauth
from cairn.store import Store
from wait_helpers import while_database_locked

MAX_BODY = 64 * 1024
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    with TestClient(w.app, base_url=BASE, follow_redirects=False) as raw:
        w.raw = raw
        yield w
    w.store.close()


# Row 16: origin_of is only for a URI that passed the policy


@pytest.mark.parametrize(
    "uri", ["javascript:alert(1)", "https://", "https://user@app.example/cb", "not a url", ""]
)
def test_the_origin_of_something_that_is_not_a_web_url_is_a_value_error(uri):
    with pytest.raises(ValueError) as raised:
        oauth.origin_of(uri)
    assert uri not in str(raised.value) or not uri  # the message does not repeat the input


# Row 19: callers without a usable address share one count


def test_callers_without_a_usable_address_share_one_count():
    limit = oauth.RateLimit(3, 60, clock=lambda: 0.0)
    assert limit.allow(None) and limit.allow("") and limit.allow("not-an-address")
    assert limit.allow("testclient") is False  # the fourth caller of the shared count
    assert limit.allow("203.0.113.9") is True  # an address has its own count
    assert limit.tracked == 2


def test_over_http_callers_without_a_usable_address_share_the_registration_count(world):
    nameless = TestClient(world.app, base_url=BASE, client=("testclient", 40000))
    garbled = TestClient(world.app, base_url=BASE, client=("not an address", 40001))
    for n in range(oauth.REGISTRATIONS):
        assert register(nameless, f"agent-{n}").status_code == 201, n
    assert register(garbled).status_code == 429
    assert world.count("oauth_clients") == oauth.REGISTRATIONS


# Row 37: the store refuses a registration the route let through


def test_a_registration_the_store_refuses_is_400_and_stores_nothing(world, monkeypatch):
    def refuse(name, redirect_uris):
        raise ValueError("a client name has 1 to 80 characters")

    monkeypatch.setattr(world.store, "register_client", refuse)
    r = register(world.raw, "an agent")
    assert r.status_code == 400 and r.headers["cache-control"] == "no-store"
    assert r.json() == {
        "error": "invalid_client_metadata",
        "error_description": "a client name has 1 to 80 characters",
    }
    assert world.count("oauth_clients") == 0


# Row 42: the account is gone between /authorize and the consent page


def test_a_consent_page_for_an_account_that_is_gone_shows_no_form(world, monkeypatch):
    client_id = registered(world)
    browser = world.browser(world.ada)
    url = consent_url(browser, client_id, pkce()[1])
    monkeypatch.setattr(cairn.store.Store, "get_account", lambda self, account_id: None)
    page = browser.get(url)
    assert page.status_code == 400 and "location" not in page.headers
    assert "<form" not in page.text.split('<main id="main">')[1] and "decision" not in page.text
    assert "not valid any more" in page.text
    assert world.count("oauth_codes") == 0


def test_a_deleted_account_never_reaches_its_consent_page(world):
    client_id = registered(world)
    browser = world.browser(world.ada)
    url = consent_url(browser, client_id, pkce()[1])
    fields = form_of(browser.get(url))
    world.store.delete_account(world.ada.view.account_id)
    # The session ended with the account: the browser is sent to sign in, not to the page.
    page = browser.get(url)
    assert page.status_code == 303 and page.headers["location"].startswith("/login?next=")
    decided = decide(browser, fields)
    assert (decided.status_code, decided.headers["location"]) == (303, "/login")
    assert world.count("oauth_codes") == 0


# Row 45: each invalid choice of reach


@pytest.mark.parametrize(
    "choice",
    [
        {"scope": "selected"},  # nothing ticked
        {"scope": "all", "projects": "PROJECT"},  # ticked, but All projects left chosen
        {"projects": "PROJECT"},  # the same, with All projects as the default
        {"scope": "some"},
        {"access": "admin"},
        {"scope": "selected", "projects": "PROJECT", "access": "all"},
    ],
)
def test_an_invalid_choice_of_reach_grants_nothing_and_the_page_can_still_be_answered(
    world, choice
):
    project = world.ada.view.list_projects()[0].id
    chosen = {name: project if value == "PROJECT" else value for name, value in choice.items()}
    client_id = registered(world)
    browser = world.browser(world.ada)
    fields = form_of(browser.get(consent_url(browser, client_id, pkce()[1])))
    r = decide(browser, {**fields, **chosen})
    assert r.status_code == 400 and "location" not in r.headers
    assert world.count("oauth_codes") == 0
    again = decide(browser, {**fields, "scope": "selected", "projects": project})
    assert again.status_code == 303 and "code" in answer(again)


# Row 46: a project that is not the workspace's, or no longer visible


def test_a_project_of_another_workspace_or_one_deleted_meanwhile_grants_nothing(world):
    foreign = world.brook.view.list_projects()[0]
    gone = world.ada.view.create_project("soon-gone")
    client_id = registered(world)
    browser = world.browser(world.ada)
    fields = form_of(browser.get(consent_url(browser, client_id, pkce()[1])))
    assert world.ada.view.delete_project(gone.id)
    for ticked in (foreign.id, gone.id, "project_" + "0" * 12):
        r = decide(browser, {**fields, "scope": "selected", "projects": ticked})
        assert r.status_code == 400 and "location" not in r.headers, ticked
        assert ticked not in r.text and foreign.name not in r.text
    assert world.count("oauth_codes") == 0
    mine = world.ada.view.list_projects()[0].id
    assert decide(browser, {**fields, "scope": "selected", "projects": mine}).status_code == 303


# Row 48: a project ticked and checked, then removed before the code is written


def test_a_project_removed_between_the_check_and_the_code_writes_nothing(world, monkeypatch):
    project = world.ada.view.create_project("going")
    client_id = registered(world)
    browser = world.browser(world.ada)
    fields = form_of(browser.get(consent_url(browser, client_id, pkce()[1])))
    checked = oauth._reach_ids

    def check_then_remove(view):
        known = checked(view)
        world.ada.view.delete_project(project.id)  # removed after the check, before create_code
        return known

    monkeypatch.setattr(oauth, "_reach_ids", check_then_remove)
    before = world.count("oauth_codes"), world.count("oauth_grants")
    r = decide(browser, {**fields, "scope": "selected", "projects": project.id})
    assert r.status_code == 400 and "location" not in r.headers
    assert "not valid any more" in r.text
    assert (world.count("oauth_codes"), world.count("oauth_grants")) == before == (0, 0)
    monkeypatch.setattr(oauth, "_reach_ids", checked)
    # The request was settled before the write that failed: the person connects again.
    assert decide(browser, fields).status_code == 400


# The store's _disconnect when the grant is already gone


def test_ending_a_grant_that_is_already_gone_records_nothing(world):
    view = world.ada.view
    before = len(view.list_audit())
    with view._write() as con:
        view._disconnect(con, "grant_000000000000", by_agent=True, reason="code replayed")
        view._disconnect(con, "grant_000000000000", by_agent=False)
    assert len(view.list_audit()) == before


# /authorize: resource may repeat, and every value names this server's /mcp


def test_authorize_with_several_resources_needs_every_one_to_be_ours(world):
    client_id = registered(world)
    browser = world.browser(world.ada)
    _, challenge = pkce()
    url = "/authorize?" + urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "state-123",
        }
    )
    for values in (
        ["https://elsewhere.example/mcp", RESOURCE],
        [RESOURCE, "https://elsewhere.example/mcp"],
        [RESOURCE, f"{BASE}/api"],
    ):
        r = browser.get(f"{url}&{urlencode([('resource', v) for v in values])}")
        assert r.status_code == 400 and "location" not in r.headers, values
        assert r.json()["error"] == "invalid_target"
    both = browser.get(f"{url}&{urlencode([('resource', RESOURCE), ('resource', RESOURCE)])}")
    assert both.status_code == 302 and "/oauth/consent" in both.headers["location"]


# /authorize: what is answered without a redirect says nothing the client sent


def authorization_query(**fields: str | None) -> str:
    _, challenge = pkce()
    query = {
        "response_type": "code",
        "redirect_uri": REDIRECT,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "state<b>sent</b>",
        "resource": RESOURCE,
        **fields,
    }
    return "/authorize?" + urlencode({k: v for k, v in query.items() if v is not None})


def test_authorize_answers_a_refusal_it_cannot_redirect_in_its_own_words(world):
    client_id = registered(world)
    unknown = "client_<i>made-up</i>"
    refusals = [
        world.raw.get(authorization_query(client_id=unknown)),
        world.raw.get(
            authorization_query(client_id=client_id, redirect_uri="https://e.example/x<y>")
        ),
        world.raw.get(authorization_query(client_id=client_id, redirect_uri="not a url <z>")),
        world.raw.get(authorization_query(client_id=None)),
    ]
    for r in refusals:
        assert r.status_code == 400 and "location" not in r.headers, r.request.url
        assert r.headers["cache-control"] == "no-store"
        for sent in ("made-up", "e.example", "not a url", "state", "sent", "<"):
            assert sent not in r.text, (sent, r.text)
        assert set(r.json()) == {"error", "error_description"}
    assert refusals[0].json()["error"] == "invalid_request"
    assert refusals[0].json() == refusals[1].json()  # unknown client, URI not the client's
    other = world.raw.get(authorization_query(client_id=unknown, response_type="token<x>"))
    assert other.status_code == 400 and "token" not in other.text and "state" not in other.text
    assert world.count("oauth_codes") == 0


def test_a_refusal_sent_to_the_verified_redirect_uri_still_carries_error_and_state(world):
    client_id = registered(world)
    for fields, error in (
        ({"response_type": "token"}, "unsupported_response_type"),
        ({"code_challenge": "too-short"}, "invalid_request"),
        ({"resource": "https://elsewhere.example/mcp"}, "invalid_target"),
    ):
        r = world.raw.get(authorization_query(client_id=client_id, **fields))
        assert r.status_code == 302 and r.headers["location"].startswith(REDIRECT + "?"), fields
        assert answer(r)["error"] == error and answer(r)["state"] == "state<b>sent</b>"


# /token: what the SDK's handler refuses, as cairn answers it


def test_an_unknown_grant_type_is_400_invalid_request_and_not_repeated_back(world):
    client_id = registered(world)
    sent = "<b>urn:example:mine</b>"
    r = world.raw.post("/token", data={"grant_type": sent, "client_id": client_id})
    assert r.status_code == 400 and r.json()["error"] == "invalid_request"
    assert "urn:example:mine" not in r.text and "<b>" not in r.text
    assert r.headers["cache-control"] == "no-store" and r.headers["pragma"] == "no-cache"


def test_a_grant_type_the_client_may_not_use_is_400_unsupported_grant_type(world):
    client_id = registered(world)
    jwt = {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": "eyJhbGciOiJub25lIn0.e30.",
        "client_id": client_id,
    }
    r = world.raw.post("/token", data=jwt)
    assert r.status_code == 400 and r.json()["error"] == "unsupported_grant_type"
    assert "eyJ" not in r.text
    assert world.count("oauth_grants") == 0


def test_a_refresh_asking_for_a_scope_it_does_not_have_is_400_invalid_scope(world):
    grant = connected(world, world.ada)
    asked = "cairn admin<script>"
    data = {
        "grant_type": "refresh_token",
        "refresh_token": grant.refresh,
        "client_id": grant.client_id,
        "scope": asked,
    }
    r = world.raw.post("/token", data=data)
    assert r.status_code == 400 and r.json()["error"] == "invalid_scope"
    assert "admin" not in r.text and "<script>" not in r.text
    assert refresh(world.raw, grant.client_id, grant.refresh).status_code == 200  # unchanged


def test_a_secret_sent_by_a_public_client_is_ignored_and_never_stored(world):
    client_id = registered(world)
    browser = world.browser(world.ada)
    secret = "s3cret-the-client-made-up-" + "q" * 20
    code, verifier = approved(world, browser, client_id)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "client_id": client_id,
        "code_verifier": verifier,
        "client_secret": secret,
    }
    r = world.raw.post("/token", data=data)
    assert r.status_code == 200, r.text
    basic = refresh(world.raw, client_id, r.json()["refresh_token"])
    assert basic.status_code == 200
    assert secret not in dump(world.db) and secret not in r.text


def test_every_token_and_revocation_answer_is_no_store(world):
    grant = connected(world, world.ada)
    other = registered(world, "another-agent")
    answers = [
        refresh(world.raw, grant.client_id, grant.refresh),  # 200
        refresh(world.raw, grant.client_id, "cairn.rt.unknown"),  # 400 invalid_grant
        refresh(world.raw, "client_" + "0" * 32, grant.refresh),  # 401 invalid_client
        world.raw.post("/token", data={"grant_type": "nope", "client_id": other}),  # 400
        world.raw.post("/token", data={"grant_type": "x", "resource": "https://e.example/"}),
        world.raw.post("/revoke", data={"token": "cairn.at.x", "client_id": other}),  # 200
        world.raw.post("/revoke", data={"client_id": other}),  # 400
        world.raw.post("/revoke", data={"token": "x", "client_id": "client_" + "0" * 32}),  # 401
    ]
    assert [r.status_code for r in answers] == [200, 400, 401, 400, 400, 200, 400, 401]
    for r in answers:
        assert r.headers["cache-control"] == "no-store", r.request.url


def test_a_successful_exchange_is_no_store(world):
    client_id = registered(world)
    code, verifier = approved(world, world.browser(world.ada), client_id)
    r = exchange(world.raw, client_id, code, verifier)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store" and r.headers["pragma"] == "no-cache"


# Bodies: size, encoding, type, method


def chunks(size: int):
    """`size` bytes in 1 KiB pieces, sent without a Content-Length."""
    for start in range(0, size, 1024):
        yield b"x" * min(1024, size - start)


@pytest.mark.parametrize("path", ["/token", "/revoke", "/register"])
def test_a_body_over_64_kib_without_a_length_is_413_and_stores_nothing(world, path):
    grant = connected(world, world.ada)
    before = dump(world.db)
    r = world.raw.post(
        path,
        content=chunks(MAX_BODY + 1),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert "content-length" not in r.request.headers
    assert r.status_code == 413
    assert dump(world.db) == before
    assert mcp(world.raw, **bearer(grant.access)).status_code == 200


def padded(fields: dict[str, str], size: int) -> bytes:
    """The fields form-encoded, with a `pad` field that makes the body exactly `size` bytes."""
    body = urlencode({**fields, "pad": ""}).encode()
    return body + b"p" * (size - len(body))


def test_a_body_of_exactly_64_kib_is_admitted_and_judged_by_the_handler(world):
    client_id = registered(world)
    code, verifier = approved(world, world.browser(world.ada), client_id)
    form = {"content-type": "application/x-www-form-urlencoded"}
    fields = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    body = padded(fields, MAX_BODY)
    assert len(body) == MAX_BODY
    tokens = world.raw.post("/token", content=body, headers=form)
    assert tokens.status_code == 200, tokens.text
    revoked = world.raw.post(
        "/revoke",
        content=padded({"token": tokens.json()["access_token"], "client_id": client_id}, MAX_BODY),
        headers=form,
    )
    assert revoked.status_code == 200 and world.ada.view.list_grants() == []
    json_body = f'{{"redirect_uris": ["{REDIRECT}"]}}'.encode()
    registration = world.raw.post(
        "/register",
        content=json_body + b" " * (MAX_BODY - len(json_body)),
        headers={"content-type": "application/json"},
    )
    assert registration.status_code == 201
    one_more = world.raw.post(
        "/register", content=json_body + b" " * (MAX_BODY - len(json_body) + 1)
    )
    assert one_more.status_code == 413


def test_a_body_that_is_not_utf_8_is_refused_and_never_a_500(world):
    client_id = registered(world)
    code, verifier = approved(world, world.browser(world.ada), client_id)
    form = {"content-type": "application/x-www-form-urlencoded"}
    clients = world.count("oauth_clients")

    registration = world.raw.post(
        "/register", content=b'{"redirect_uris": ["\xff\xfe"]}', headers=form
    )
    assert registration.status_code == 400
    assert registration.json()["error"] == "invalid_client_metadata"
    assert world.count("oauth_clients") == clients

    exchange_fields = urlencode(
        {"grant_type": "authorization_code", "redirect_uri": REDIRECT, "client_id": client_id}
    ).encode()
    raw_byte = b"&code=" + code.encode() + b"&code_verifier=\xff" + b"k" * 50
    bad_verifier = world.raw.post("/token", content=exchange_fields + raw_byte, headers=form)
    assert bad_verifier.status_code == 400 and bad_verifier.json()["error"] == "invalid_request"
    bad_code = world.raw.post(
        "/token",
        content=exchange_fields + b"&code=%FF\xfe&code_verifier=" + verifier.encode(),
        headers=form,
    )
    assert bad_code.status_code == 400 and bad_code.json()["error"] == "invalid_grant"
    garbage = world.raw.post("/token", content=b"\xff\xfe\xfd=\x80", headers=form)
    assert garbage.status_code == 401 and garbage.json()["error"] == "invalid_client"
    revoked = world.raw.post("/revoke", content=b"\xff\xfe\xfd=\x80", headers=form)
    assert revoked.status_code == 400 and revoked.json()["error"] == "invalid_request"
    assert world.count("oauth_grants") == 0
    assert exchange(world.raw, client_id, code, verifier).status_code == 200  # still good


def test_json_sent_to_token_is_read_as_an_empty_form(world):
    """The token endpoint takes a form (RFC 6749, 3.2). JSON is not one: the form is empty,
    so there is no client_id, and the answer is the SDK's 401 invalid_client."""
    client_id = registered(world)
    code, verifier = approved(world, world.browser(world.ada), client_id)
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    r = world.raw.post("/token", json=body)
    assert r.status_code == 401 and r.json()["error"] == "invalid_client"
    assert "access_token" not in r.text and world.count("oauth_grants") == 0
    assert exchange(world.raw, client_id, code, verifier).status_code == 200


def test_the_wrong_method_is_405(world):
    assert world.raw.get("/token").status_code == 405
    assert world.raw.put("/register", json={"redirect_uris": [REDIRECT]}).status_code == 405
    assert world.raw.get("/revoke").status_code == 405
    assert world.count("oauth_clients") == 0


# Expiry is judged when the write happens: the store's other OAuth writes


@pytest.fixture
def late(tmp_path):
    """A store whose clock the test moves forward: (store, view of an account, shift)."""
    shift = [timedelta(0)]
    store = Store(tmp_path / "cairn.db", clock=lambda: datetime.now(UTC) + shift[0])
    view = store.for_account(store.create_account("Ada").id)
    view.create_project("core")
    yield store, view, shift
    store.close()


def test_a_code_made_while_its_write_waits_lasts_two_minutes_from_the_write(tmp_path, late):
    store, view, shift = late
    client = store.register_client("agent", [REDIRECT])
    code = while_database_locked(
        tmp_path / "cairn.db",
        store,
        lambda: view.create_code(client.id, REDIRECT, CHALLENGE, audience=RESOURCE),
        lambda: shift.__setitem__(0, timedelta(minutes=10)),
    )
    found = store.find_code(code)
    assert found is not None
    assert found.expires_at > datetime.now(UTC) + timedelta(minutes=11)


def test_a_token_that_expires_while_its_revocation_waits_is_not_revoked(tmp_path, late):
    store, view, shift = late
    client = store.register_client("agent", [REDIRECT])
    code = view.create_code(client.id, REDIRECT, CHALLENGE, audience=RESOURCE)
    pair = store.redeem_code(code, client.id, audience=RESOURCE)
    revoked = while_database_locked(
        tmp_path / "cairn.db",
        store,
        lambda: store.revoke_token(pair.access_token, client.id),
        lambda: shift.__setitem__(0, timedelta(hours=2)),  # an access token lives an hour
    )
    assert revoked is False
    assert [g.client_id for g in view.list_grants()] == [client.id]  # the refresh token lives


def test_a_client_registered_while_its_write_waits_is_stamped_at_the_write(tmp_path, late):
    store, _, shift = late
    client = while_database_locked(
        tmp_path / "cairn.db",
        store,
        lambda: store.register_client("agent", [REDIRECT]),
        lambda: shift.__setitem__(0, timedelta(days=3)),
    )
    assert datetime.fromisoformat(client.created_at) > datetime.now(UTC) + timedelta(days=2)
    assert store.get_client(client.id).created_at == client.created_at
