"""Changing a key that seals stored secrets (`CAIRN_GOOGLE_CONNECT_KEY`, `CAIRN_SLACK_KEY`)
without losing what it sealed: earlier keys beside the current one (`<VARIABLE>_PREVIOUS`),
the `reseal` command, and what `serve` refuses.

Every key, token and secret here is made up. No assertion compares a key, a token or a sealed
value in a way that would print it when it fails: each comparison is made into a bool first.
"""

import base64
import json

import pytest
import uvicorn

from cairn import server
from cairn.store.hosted import Sealer, associated
from cairn.web import google_connect
from cairn.web import slack as web_slack

CURRENT = bytes(range(32))
EARLIER = bytes(range(1, 33))
OLDEST = bytes(range(2, 34))
STRANGER = bytes(range(3, 35))
TOKEN = "1//rotation-refresh-token-ada-0123456789"
TIED = associated("acct_fable", "gmail.ada.0123456789", "user_ada")
BASE = "https://cairn.example.test"
CLIENT_SECRET = "rotation-web-secret-0123456789"
CLIENT_JSON = json.dumps(
    {"web": {"client_id": "rotation-client.apps.example.test", "client_secret": CLIENT_SECRET}}
)
SIGN_IN_ENV = {"CAIRN_GITHUB_CLIENT_ID": "gh-id", "CAIRN_GITHUB_CLIENT_SECRET": "gh-secret"}
GOOGLE = "CAIRN_GOOGLE_CONNECT_KEY"
SLACK = "CAIRN_SLACK_KEY"
VARIABLES = (GOOGLE, f"{GOOGLE}_PREVIOUS", SLACK, f"{SLACK}_PREVIOUS")


def b64(key: bytes) -> str:
    return base64.b64encode(key).decode()


# Sealer: earlier keys open, the current key seals


def test_a_value_sealed_under_an_earlier_key_opens_beside_the_current_one():
    for earlier in (EARLIER, OLDEST):
        sealed = Sealer(earlier).seal(TOKEN, TIED)
        opened = Sealer(CURRENT, (EARLIER, OLDEST)).open(sealed, TIED)
        assert (opened == TOKEN) is True


def test_what_is_sealed_from_now_on_is_sealed_under_the_current_key():
    sealed = Sealer(CURRENT, (EARLIER,)).seal(TOKEN, TIED)
    assert (Sealer(CURRENT).open(sealed, TIED) == TOKEN) is True
    assert Sealer(EARLIER).open(sealed, TIED) is None


def test_a_value_of_no_key_given_does_not_open():
    sealed = Sealer(STRANGER).seal(TOKEN, TIED)
    assert Sealer(CURRENT, (EARLIER, OLDEST)).open(sealed, TIED) is None


def test_a_sealed_value_carries_nothing_that_says_which_key_sealed_it():
    """Trial in a fixed order: the form of a value is the same under every key and the same as
    before earlier keys could be given (base64url of a 12-byte nonce and the sealed text)."""
    now = Sealer(CURRENT, (EARLIER,)).seal(TOKEN, TIED)
    before = Sealer(EARLIER).seal(TOKEN, TIED)
    assert len(now) == len(before)
    assert len(base64.urlsafe_b64decode(now)) == 12 + len(TOKEN.encode()) + 16


def test_the_sealer_says_whether_the_current_key_opens_a_value():
    sealer = Sealer(CURRENT, (EARLIER,))
    assert sealer.current(sealer.seal(TOKEN, TIED), TIED) is True
    assert sealer.current(Sealer(EARLIER).seal(TOKEN, TIED), TIED) is False
    assert sealer.current(Sealer(STRANGER).seal(TOKEN, TIED), TIED) is False
    assert sealer.current(None, TIED) is False


@pytest.mark.parametrize(
    "previous",
    [(CURRENT,), (EARLIER, CURRENT), (bytes(16),), (b"",)],
    ids=["the-current-key", "the-current-key-second", "16-bytes", "empty"],
)
def test_an_earlier_key_equal_to_the_current_one_or_not_32_bytes_is_refused(previous):
    with pytest.raises(ValueError) as refused:
        Sealer(CURRENT, previous)
    said = str(refused.value)
    leaked = any(k.hex() in said or b64(k) in said for k in (CURRENT, *previous) if k)
    assert leaked is False


def test_no_repr_of_a_sealer_with_earlier_keys_shows_a_key():
    shown = repr(Sealer(CURRENT, (EARLIER, OLDEST)))
    leaked = any(k.hex() in shown or b64(k) in shown for k in (CURRENT, EARLIER, OLDEST))
    assert leaked is False and shown == "Sealer()"


# The variables, and what serve refuses


def b64s(*keys: bytes) -> str:
    return ",".join(b64(key) for key in keys)


def google_env(**more: str) -> dict[str, str]:
    return {"CAIRN_GOOGLE_CONNECT_CLIENT": CLIENT_JSON, GOOGLE: b64(CURRENT), **more}


def slack_env(**more: str) -> dict[str, str]:
    return {SLACK: b64(CURRENT), **more}


def serve(monkeypatch, tmp_path, switch: str, env: dict[str, str]):
    for name in (*VARIABLES, "CAIRN_GOOGLE_CONNECT_CLIENT"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {**SIGN_IN_ENV, **env}.items():
        monkeypatch.setenv(name, value)
    apps: list = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    db = tmp_path / "data" / "cairn.db"
    argv = ["serve", "--db", str(db), "--public-url", BASE, "--sign-in", "github", switch]
    return server.main(argv), apps, db


def opens_earlier(sealer: Sealer, *earlier: bytes) -> bool:
    return all(sealer.open(Sealer(key).seal(TOKEN, TIED), TIED) == TOKEN for key in earlier)


@pytest.mark.parametrize(
    "given",
    [b64s(EARLIER), b64s(EARLIER, OLDEST), f" {b64(EARLIER)} , {b64(OLDEST)} "],
    ids=["one", "two", "two-with-spaces"],
)
def test_earlier_keys_beside_each_key_open_what_they_sealed(given):
    google = google_connect.read_switch(google_env(**{f"{GOOGLE}_PREVIOUS": given}))
    slack = web_slack.read_switch(slack_env(**{f"{SLACK}_PREVIOUS": given}))
    earlier = (EARLIER, OLDEST) if "," in given else (EARLIER,)
    for sealer in (google.custody.sealer, slack.sealer()):
        assert opens_earlier(sealer, *earlier) is True
        assert (Sealer(CURRENT).open(sealer.seal(TOKEN, TIED), TIED) == TOKEN) is True


def test_with_no_earlier_key_or_a_blank_one_nothing_earlier_opens():
    for more in ({}, {f"{GOOGLE}_PREVIOUS": "  ", f"{SLACK}_PREVIOUS": ""}):
        google = google_connect.read_switch(google_env(**more))
        slack = web_slack.read_switch(slack_env(**more))
        for sealer in (google.custody.sealer, slack.sealer()):
            assert opens_earlier(sealer, EARLIER) is False
            assert opens_earlier(sealer, CURRENT) is True


@pytest.mark.parametrize("switch", ["--google-connect", "--slack"])
def test_serve_starts_with_earlier_keys_and_says_none(tmp_path, monkeypatch, capsys, switch):
    env = google_env(**{f"{GOOGLE}_PREVIOUS": b64s(EARLIER, OLDEST)})
    if switch == "--slack":
        env = slack_env(**{f"{SLACK}_PREVIOUS": b64s(EARLIER, OLDEST)})
    code, apps, _db = serve(monkeypatch, tmp_path, switch, env)
    said = capsys.readouterr()
    shown = said.out + said.err
    leaked = any(b64(k) in shown or k.hex() in shown for k in (CURRENT, EARLIER, OLDEST))
    assert code == 0 and len(apps) == 1 and leaked is False


MALFORMED = {
    "not-base64": "not base64 at all!" + b64(EARLIER),
    "16-bytes": base64.b64encode(bytes(16)).decode(),
    "an-empty-item": f"{b64(EARLIER)},,{b64(OLDEST)}",
    "a-trailing-comma": f"{b64(EARLIER)},",
    "the-current-key": b64(CURRENT),
    "the-current-key-second": b64s(EARLIER, CURRENT),
}


@pytest.mark.parametrize("switch", ["--google-connect", "--slack"])
@pytest.mark.parametrize("value", list(MALFORMED.values()), ids=list(MALFORMED))
def test_serve_stops_at_start_on_a_malformed_or_repeated_earlier_key(
    tmp_path, monkeypatch, capsys, switch, value
):
    variable = GOOGLE if switch == "--google-connect" else SLACK
    env = google_env() if switch == "--google-connect" else slack_env()
    env[f"{variable}_PREVIOUS"] = value
    code, apps, db = serve(monkeypatch, tmp_path, switch, env)
    said = capsys.readouterr()
    lines = said.err.strip().splitlines()
    assert code == 2 and apps == [] and not db.parent.exists() and len(lines) == 1
    assert f"{variable}_PREVIOUS" in lines[0]
    shown = said.out + said.err
    leaked = any(part.strip() and part.strip() in shown for part in value.split(","))
    leaked = leaked or any(b64(k) in shown or k.hex() in shown for k in (CURRENT, EARLIER))
    assert leaked is False
