"""How a request Slack sends is checked and read (cairn.slack.verify): the body's cap, the
timestamp and the signature, and the envelope a handler is given. The inner loop under
tests/acceptance/test_k1_slack.py; the Slack facts are in docs/slack-api.md (Verifying and
acknowledging inbound requests). Every id and secret here is made up."""

import asyncio
import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest
from starlette.datastructures import Headers

from cairn.slack.verify import (
    BODY_MAX,
    SKEW,
    Envelope,
    NotSlack,
    OtherTeam,
    TooLarge,
    envelope,
    read_body,
    signature,
    verified,
)
from cairn.store.slack import SlackInstall

SECRET = "unit-signing-" + "ab" * 10
NOW = 1_900_000_000
INSTALL = SlackInstall(
    id="sl_0123456789ab", account_id="acct_0123456789ab", state="on", team_id="T0FABLE01",
    enterprise_id=None, team_domain="fable", bot_user_id="U0BOT0001", command="/cairn",
    made_by=None, made_at="2031-03-04T09:00:00.000000+00:00", removed_at=None,
    holds_secrets=True,
)  # fmt: skip


def headers(body: bytes, at: int = NOW, secret: str = SECRET, **more) -> Headers:
    digest = hmac.new(secret.encode(), b"v0:%d:" % at + body, hashlib.sha256).hexdigest()
    raw = {"x-slack-request-timestamp": str(at), "x-slack-signature": f"v0={digest}", **more}
    return Headers(raw)


def test_the_signature_is_slacks_base_string_under_the_secret():
    body = b"token=unused&team_id=T0FABLE01"
    expected = hmac.new(SECRET.encode(), b"v0:12345:" + body, hashlib.sha256).hexdigest()
    assert signature(SECRET, "12345", body) == b"v0=" + expected.encode()


def test_verified_takes_slacks_own_request_and_nothing_else():
    body = b'{"type":"event_callback"}'
    assert verified(SECRET, headers(body), body, NOW)
    assert not verified("another-secret", headers(body), body, NOW)
    assert not verified(SECRET, headers(body), body + b" ", NOW)
    assert verified(SECRET, headers(body, at=NOW - SKEW), body, NOW)
    assert not verified(SECRET, headers(body, at=NOW - SKEW - 1), body, NOW)
    assert not verified(SECRET, headers(body, at=NOW + SKEW + 1), body, NOW)
    good = headers(body)
    for name, value in (
        ("x-slack-request-timestamp", ""),
        ("x-slack-request-timestamp", "1.9e9"),
        ("x-slack-request-timestamp", " " + str(NOW)),
        ("x-slack-request-timestamp", "9" * 40),
        ("x-slack-signature", ""),
        ("x-slack-signature", good["x-slack-signature"][3:]),
        ("x-slack-signature", "v0=" + "é" * 64),
    ):
        changed = Headers({**dict(good), name: value})
        assert not verified(SECRET, changed, body, NOW), (name, value)
    assert not verified(SECRET, Headers({}), body, NOW)
    assert SKEW == 300


def test_the_comparison_is_of_bytes_in_constant_time(monkeypatch):
    seen = []
    real = hmac.compare_digest
    monkeypatch.setattr(
        "cairn.slack.verify.hmac.compare_digest", lambda a, b: seen.append((a, b)) or real(a, b)
    )
    body = b"x=1"
    verified(SECRET, headers(body), body, NOW)
    verified(SECRET, headers(body, secret="other"), body, NOW)
    assert len(seen) == 2 and all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in seen)


class Request:
    def __init__(self, chunks: list[bytes], length: str | None = None) -> None:
        self.headers = Headers({"content-length": length} if length is not None else {})
        self._chunks = chunks
        self.read = 0

    async def stream(self):
        for chunk in self._chunks:
            self.read += 1
            yield chunk


def test_the_body_is_read_with_a_limit():
    assert BODY_MAX == 1024 * 1024
    whole = Request([b"a" * 1000, b"b" * 24])
    assert asyncio.run(read_body(whole)) == b"a" * 1000 + b"b" * 24
    over = Request([b"a" * BODY_MAX, b"b", b"never read"])
    with pytest.raises(TooLarge):
        asyncio.run(read_body(over))
    assert over.read == 2  # it stops at the first byte past the cap
    said = Request([b"a"], length=str(BODY_MAX + 1))
    with pytest.raises(TooLarge):
        asyncio.run(read_body(said))
    assert said.read == 0  # a length over the cap is refused before anything is read


def command(**fields) -> bytes:
    return urlencode(
        {"team_id": "T0FABLE01", "user_id": "U0ADA0001", "command": "/cairn",
         "text": "find Tide  tables", **fields}
    ).encode()  # fmt: skip


def test_a_command_is_named_by_its_first_word():
    found = envelope("commands", INSTALL, command(), Headers({}))
    assert isinstance(found, Envelope)
    assert (found.kind, found.name, found.team_id, found.user_id) == (
        "commands", "find", "T0FABLE01", "U0ADA0001",
    )  # fmt: skip
    assert found.payload["text"] == "find Tide  tables"
    assert found.install_id == INSTALL.id and found.account_id == INSTALL.account_id
    assert envelope("commands", INSTALL, command(text="  "), Headers({})).name == ""
    assert envelope("commands", INSTALL, command(text="HELP me"), Headers({})).name == "help"
    assert "Tide" not in repr(found)  # what a person wrote is not in a repr


def test_an_action_is_named_by_its_callback_or_its_first_actions_id():
    payload = {"type": "message_action", "callback_id": "file_in_cairn",
               "team": {"id": "T0FABLE01"}, "user": {"id": "U0ADA0001"}}  # fmt: skip
    body = urlencode({"payload": json.dumps(payload)}).encode()
    found = envelope("actions", INSTALL, body, Headers({}))
    assert (found.name, found.user_id, found.type) == (
        "file_in_cairn",
        "U0ADA0001",
        "message_action",
    )
    payload = {"type": "block_actions", "team": {"id": "T0FABLE01"}, "user": {"id": "U0ADA0001"},
               "actions": [{"action_id": "fable_more"}]}  # fmt: skip
    body = urlencode({"payload": json.dumps(payload)}).encode()
    assert envelope("actions", INSTALL, body, Headers({})).name == "fable_more"


def test_an_event_carries_its_id_and_retry_number():
    body = json.dumps({"type": "event_callback", "team_id": "T0FABLE01", "event_id": "Ev01",
                       "event": {"type": "message", "user": "U0ADA0001"}}).encode()  # fmt: skip
    found = envelope("events", INSTALL, body, Headers({"x-slack-retry-num": "2"}))
    assert (found.name, found.event_id, found.retry, found.user_id) == (
        "message", "Ev01", 2, "U0ADA0001",
    )  # fmt: skip
    assert envelope("events", INSTALL, body, Headers({})).retry is None
    challenge = json.dumps({"type": "url_verification", "challenge": "c"}).encode()
    assert envelope("events", INSTALL, challenge, Headers({})).type == "url_verification"


@pytest.mark.parametrize(
    "kind, body",
    [
        ("commands", command(team_id="T0QUILL01")),
        ("commands", b"user_id=U0ADA0001&text=find"),  # no team
        (
            "events",
            json.dumps(
                {"type": "event_callback", "team_id": "T0QUILL01", "event": {"type": "message"}}
            ).encode(),
        ),
        ("events", json.dumps({"type": "event_callback", "event": {"type": "x"}}).encode()),
        (
            "actions",
            urlencode(
                {"payload": json.dumps({"type": "shortcut", "team": {"id": "T0QUILL01"}})}
            ).encode(),
        ),
    ],
)
def test_a_body_whose_team_is_not_the_installs_is_another_teams(kind, body):
    with pytest.raises(OtherTeam):
        envelope(kind, INSTALL, body, Headers({}))


@pytest.mark.parametrize(
    "kind, body",
    [
        ("events", b"not json"),
        ("events", b"[1, 2]"),
        ("events", b"\xff\xfe"),
        ("events", b"[" * 100_000),
        ("events", json.dumps({"type": "event_callback", "team_id": "T0FABLE01"}).encode()),
        ("commands", b"\xff=1"),
        ("commands", command() + b"&team_id=T0FABLE01"),  # a field twice
        ("actions", b"payload=not+json"),
        ("actions", b"nothing=here"),
        ("commands", b"&".join(b"f%d=1" % n for n in range(2000))),
    ],
)
def test_a_body_that_is_not_slacks_is_refused_and_says_nothing_of_it(kind, body):
    with pytest.raises(NotSlack) as raised:
        envelope(kind, INSTALL, body, Headers({}))
    assert str(raised.value) == NotSlack.SAID and repr(body[:8]) not in str(raised.value)
