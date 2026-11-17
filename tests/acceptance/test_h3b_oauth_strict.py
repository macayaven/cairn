"""H3b acceptance tests (frozen): the OAuth endpoints refuse what the RFCs say to refuse.

Written by the orchestrator before implementation, after a review that read every failure
path of src/cairn/oauth.py and src/cairn/store/grants.py. Implementers must not edit this
file. tests/acceptance/test_h3_oauth.py stays as it is and still passes: in particular a
wrong verifier does not use up a code.

The verifier (RFC 7636, 4.1)
- `code_verifier` is 43 to 128 characters of A-Z a-z 0-9 - . _ ~. POST /token with any other
  verifier answers 400 `invalid_request` before anything is looked up: the answer is the
  same whether the code exists or not, and the code is still good for its real verifier. So
  a client that derived its challenge from a short verifier can never redeem a code.

One answer for a code that cannot be redeemed
- An unknown code, a code with the wrong verifier and a code issued to another client get
  the same status and the same JSON body from POST /token, description included: the answer
  does not say which it was.

A parameter is given once (RFC 6749, 3.1)
- GET /authorize naming any parameter twice, other than `resource`, answers 400: no
  redirect, no consent page, whatever the two values are.
- POST /token and POST /revoke naming a field twice, other than `resource`, answer 400
  `invalid_request` and do nothing: the code still works, the grant is still there.
- `resource` may be given more than once (RFC 8707); every value must name this server's
  /mcp, as before.

Where the answer goes
- The redirect URI of a request is matched as a URL, not as a string: another spelling of
  the registered URI (upper-case host, the default port) is the same URI. The browser is
  always sent to the URI as it was registered. A URI that is another address after
  normalising ("/cb/../other") is not the client's: 400 and no redirect.

Two refreshes at once
- When one refresh token is presented twice at the same moment, over two connections, one
  call gets a new pair and the other gets nothing, the second is a replay, and the grant
  ends: the new pair does not work either. Nothing is left half done.

Expiry is judged when the write happens
- A code or a refresh token that expires while its exchange waits for the database's write
  lock is expired: the time is read once the lock is held, not before the wait. Nothing is
  issued and nothing is rotated.
"""

import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest
from test_h3_oauth import (
    BASE,
    REDIRECT,
    RESOURCE,
    Person,
    authorization_url,
    connect,
    exchange,
    pkce,
    play,
    query,
    refresh,
    register,
)

from cairn.store import Store

UNRESERVED_64 = "aB3-._~" + "k" * 57


async def code_for(world, transport, raw, challenge: str, name: str = "strict-agent"):
    """(client id, a fresh code) for a client that asked with `challenge`."""
    person = Person(transport, world.ada.key)
    client_id = (await register(raw, name=name)).json()["client_id"]
    location = await person.approve(await authorization_url(raw, client_id, challenge))
    return client_id, query(location)["code"]


# The verifier


@pytest.mark.parametrize(
    "verifier",
    [
        "x",
        "k" * 42,
        "k" * 129,
        "k" * 42 + "!",
        "k" * 42 + " ",
        "k" * 42 + "é",
        "k" * 21 + "/" + "k" * 21,
        "",
    ],
    ids=["one", "42", "129", "bang", "space", "accent", "slash", "empty"],
)
def test_a_verifier_that_is_not_one_is_refused_before_the_code_is_looked_at(tmp_path, verifier):
    async def scenario(world, transport, raw):
        real, challenge = pkce()
        client_id, code = await code_for(world, transport, raw, challenge)

        with_code = await exchange(raw, client_id, code, verifier)
        without = await exchange(raw, client_id, "no-such-code", verifier)

        assert with_code.status_code == 400 and with_code.json()["error"] == "invalid_request"
        assert (without.status_code, without.json()) == (with_code.status_code, with_code.json())
        assert "access_token" not in with_code.text
        assert (await exchange(raw, client_id, code, real)).status_code == 200

    play(tmp_path, scenario)


def test_a_challenge_made_from_a_short_verifier_never_becomes_a_token(tmp_path):
    import base64
    import hashlib

    async def scenario(world, transport, raw):
        weak = "x"
        digest = hashlib.sha256(weak.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        client_id, code = await code_for(world, transport, raw, challenge)

        answer = await exchange(raw, client_id, code, weak)

        assert answer.status_code == 400 and "access_token" not in answer.text

    play(tmp_path, scenario)


def test_every_unreserved_character_is_accepted_in_a_verifier(tmp_path):
    import base64
    import hashlib

    async def scenario(world, transport, raw):
        for verifier in (UNRESERVED_64, "a" * 43, "Z" * 128):
            digest = hashlib.sha256(verifier.encode()).digest()
            challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            client_id, code = await code_for(world, transport, raw, challenge, name=verifier[:8])
            assert (await exchange(raw, client_id, code, verifier)).status_code == 200

    play(tmp_path, scenario)


# One answer for a code that cannot be redeemed


def test_the_answer_does_not_say_why_a_code_cannot_be_redeemed(tmp_path):
    async def scenario(world, transport, raw):
        real, challenge = pkce()
        client_id, code = await code_for(world, transport, raw, challenge)
        other = (await register(raw, name="another-agent")).json()["client_id"]

        unknown = await exchange(raw, client_id, "no-such-code", real)
        wrong = await exchange(raw, client_id, code, "w" * 64)
        stolen = await exchange(raw, other, code, real)

        assert unknown.status_code == 400 and unknown.json()["error"] == "invalid_grant"
        assert (wrong.status_code, wrong.json()) == (400, unknown.json())
        assert (stolen.status_code, stolen.json()) == (400, unknown.json())
        assert (await exchange(raw, client_id, code, real)).status_code == 200

    play(tmp_path, scenario)


# A parameter is given once


@pytest.mark.parametrize(
    "name", ["response_type", "client_id", "redirect_uri", "code_challenge", "state", "scope"]
)
def test_authorize_refuses_a_parameter_named_twice(tmp_path, name):
    async def scenario(world, transport, raw):
        person = Person(transport, world.ada.key)
        await person.sign_in()
        client_id = (await register(raw)).json()["client_id"]
        _, challenge = pkce()
        url = await authorization_url(raw, client_id, challenge)
        value = query(url).get(name, "cairn")
        for second in (value, "something-else"):
            twice = f"{url}&{urlencode({name: second})}"

            answer = await person.http.get(twice)

            assert answer.status_code == 400, (name, second, answer.status_code)
            assert "location" not in answer.headers
        assert (await person.http.get(url)).status_code == 302  # once is still fine

    play(tmp_path, scenario)


@pytest.mark.parametrize(
    "name", ["grant_type", "code", "code_verifier", "client_id", "redirect_uri"]
)
def test_token_refuses_a_field_named_twice_and_the_code_still_works(tmp_path, name):
    async def scenario(world, transport, raw):
        real, challenge = pkce()
        client_id, code = await code_for(world, transport, raw, challenge)
        fields = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": client_id,
            "code_verifier": real,
            "resource": RESOURCE,
        }
        for second in (fields[name], "something-else"):
            twice = await raw.post("/token", data={**fields, name: [fields[name], second]})

            assert twice.status_code == 400 and twice.json()["error"] == "invalid_request"
            assert "access_token" not in twice.text
        assert (await exchange(raw, client_id, code, real)).status_code == 200

    play(tmp_path, scenario)


def test_a_refresh_or_a_revocation_naming_a_field_twice_changes_nothing(tmp_path):
    async def scenario(world, transport, raw):
        grant = await connect(raw, Person(transport, world.ada.key))
        refresh_fields = {
            "grant_type": "refresh_token",
            "refresh_token": [grant.refresh, grant.refresh],
            "client_id": grant.client_id,
        }
        refreshed = await raw.post("/token", data=refresh_fields)
        assert refreshed.status_code == 400 and refreshed.json()["error"] == "invalid_request"

        for name in ("token", "client_id"):
            fields = {"token": grant.access, "client_id": grant.client_id}
            revoked = await raw.post("/revoke", data={**fields, name: [fields[name]] * 2})
            assert revoked.status_code == 400 and revoked.json()["error"] == "invalid_request"

        assert world.store.resolve_token(grant.access) is not None  # the grant is still there
        assert (await refresh(raw, grant.client_id, grant.refresh)).status_code == 200

    play(tmp_path, scenario)


def test_resource_may_be_given_more_than_once(tmp_path):
    async def scenario(world, transport, raw):
        real, challenge = pkce()
        client_id, code = await code_for(world, transport, raw, challenge)
        fields = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": client_id,
            "code_verifier": real,
        }
        elsewhere = [RESOURCE, "https://elsewhere.example/mcp"]
        refused = await raw.post("/token", data={**fields, "resource": elsewhere})
        assert refused.status_code == 400 and refused.json()["error"] == "invalid_target"

        accepted = await raw.post("/token", data={**fields, "resource": [RESOURCE, RESOURCE]})
        assert accepted.status_code == 200 and accepted.json()["access_token"]

    play(tmp_path, scenario)


# Where the answer goes


def test_the_browser_is_sent_to_the_uri_as_registered_whatever_the_request_spelled(tmp_path):
    registered = "https://client.example/cb"

    async def scenario(world, transport, raw):
        person = Person(transport, world.ada.key)
        client_id = (await register(raw, redirect_uris=[registered])).json()["client_id"]
        _, challenge = pkce()

        for spelled in ("https://CLIENT.example/cb", "https://client.example:443/cb"):
            url = await authorization_url(raw, client_id, challenge, redirect_uri=spelled)
            location = await person.approve(url)
            assert location.startswith(registered + "?"), location

        for elsewhere in (
            "https://client.example/cb/../other",
            "https://client.example/cb/",
            "https://client.example.evil.example/cb",
            "https://client.example@evil.example/cb",
        ):
            url = await authorization_url(raw, client_id, challenge, redirect_uri=elsewhere)
            answer = await person.http.get(url)
            assert answer.status_code == 400, (elsewhere, answer.status_code)
            assert "location" not in answer.headers

    play(tmp_path, scenario)


# Two refreshes at once


def test_two_refreshes_of_one_token_at_once_end_the_grant(tmp_path):
    async def scenario(world, transport, raw):
        return await connect(raw, Person(transport, world.ada.key))

    grant = play(tmp_path, scenario)
    first, second = Store(tmp_path / "cairn.db"), Store(tmp_path / "cairn.db")
    together = threading.Barrier(2)
    pairs: list[object] = []

    def rotate(store: Store) -> None:
        together.wait(timeout=10)
        pairs.append(store.rotate_refresh_token(grant.refresh, grant.client_id, audience=RESOURCE))

    try:
        threads = [threading.Thread(target=rotate, args=(store,)) for store in (first, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert len(pairs) == 2
        winners = [pair for pair in pairs if pair is not None]
        assert len(winners) == 1  # one rotation, and the other call was its replay
        winner = winners[0]
        assert first.resolve_token(winner.access_token) is None  # the replay ended the grant
        assert first.resolve_token(grant.access) is None
        assert (
            first.rotate_refresh_token(winner.refresh_token, grant.client_id, audience=RESOURCE)
            is None
        )
    finally:
        first.close()
        second.close()


# Expiry is judged when the write happens


def while_the_database_is_locked(db, call, then):
    """Start `call` on a thread while another connection holds the write lock, run `then`
    half a second into its wait, release the lock, and return what `call` returned."""
    blocker = sqlite3.connect(db, isolation_level=None)
    result: list[object] = []
    try:
        blocker.execute("BEGIN IMMEDIATE")
        thread = threading.Thread(target=lambda: result.append(call()))
        thread.start()
        time.sleep(0.5)  # the call has started and is waiting for the lock
        then()
        blocker.execute("COMMIT")
        thread.join(timeout=30)
    finally:
        blocker.close()
    assert len(result) == 1, "the call did not return"
    return result[0]


def test_a_code_that_expires_while_its_redemption_waits_is_not_redeemed(tmp_path):
    async def scenario(world, transport, raw):
        _, challenge = pkce()
        return await code_for(world, transport, raw, challenge)

    client_id, code = play(tmp_path, scenario)
    later = [timedelta(0)]
    store = Store(tmp_path / "cairn.db", clock=lambda: datetime.now(UTC) + later[0])
    try:
        pair = while_the_database_is_locked(
            tmp_path / "cairn.db",
            lambda: store.redeem_code(code, client_id, audience=RESOURCE),
            lambda: later.__setitem__(0, timedelta(minutes=10)),  # a code lives 2 minutes
        )
        assert pair is None
        con = sqlite3.connect(tmp_path / "cairn.db")
        try:
            assert con.execute("SELECT count(*) FROM oauth_grants").fetchone()[0] == 0
        finally:
            con.close()
    finally:
        store.close()


def test_a_refresh_token_that_expires_while_its_rotation_waits_is_not_rotated(tmp_path):
    async def scenario(world, transport, raw):
        return await connect(raw, Person(transport, world.ada.key))

    grant = play(tmp_path, scenario)
    later = [timedelta(0)]
    store = Store(tmp_path / "cairn.db", clock=lambda: datetime.now(UTC) + later[0])
    try:
        pair = while_the_database_is_locked(
            tmp_path / "cairn.db",
            lambda: store.rotate_refresh_token(grant.refresh, grant.client_id, audience=RESOURCE),
            lambda: later.__setitem__(0, timedelta(days=400)),  # past any refresh token's life
        )
        assert pair is None
    finally:
        store.close()


def test_base_is_the_issuer_these_tests_assume():
    assert f"{BASE}/mcp" == RESOURCE
