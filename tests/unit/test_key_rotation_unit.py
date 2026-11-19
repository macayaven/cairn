"""Changing a key that seals stored secrets (`CAIRN_GOOGLE_CONNECT_KEY`, `CAIRN_SLACK_KEY`)
without losing what it sealed: earlier keys beside the current one (`<VARIABLE>_PREVIOUS`),
the `reseal` command, and what `serve` refuses.

Every key, token and secret here is made up. No assertion compares a key, a token or a sealed
value in a way that would print it when it fails: each comparison is made into a bool first.
"""

import base64
import json
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import uvicorn

from cairn import server
from cairn.store import Store
from cairn.store.hosted import GoogleCustody, Sealer, associated
from cairn.store.reseal import Resealed, reseal_google, reseal_slack
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


# The stored values under a changed key, and re-sealing them

ISSUER = "https://sso.example.test"
NOW = datetime(2099, 11, 17, 9, tzinfo=UTC)
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SIGNING = "slack-signing-" + "0e" * 8
BOT = "xoxb-" + "rotation-" + "0123456789"


def dump(db) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def under(store: Store, key: bytes, *previous: bytes) -> None:
    """The keys this process holds from now on, as a restart with them would give."""
    store.google_custody = GoogleCustody(
        Sealer(key, previous), client_id="rotation-client.apps.example.test",
        client_secret=CLIENT_SECRET,
    )  # fmt: skip
    store.slack_sealer = Sealer(key, previous)


@pytest.fixture
def world(tmp_path):
    db = tmp_path / "cairn.db"
    store = Store(db, clock=lambda: NOW)
    under(store, EARLIER)
    ada = store.sign_in("sso", "sub-ada", "ada@example.test", "Ada", issuer=ISSUER, join="personal")
    w = SimpleNamespace(store=store, db=db, account=ada.account_id, ada=ada.user.id)
    w.view = lambda: store.for_account(w.account, w.ada)  # a view copies the keys held now
    yield w
    store.close()


def connect(w, address: str, token: str = TOKEN) -> str:
    subject = "1000" + str(sum(map(ord, address)))
    return w.view().connect_google("gmail", address, [GMAIL_SCOPE], token, subject=subject).name


def slack_ready(w) -> str:
    view = w.view()
    made = view.make_slack_install()
    view.ready_slack_install(
        made.id, signing_secret=SIGNING, bot_token=BOT, team_id="T0FABLE01",
        team_domain="fable", bot_user_id="U0BOT0001",
    )  # fmt: skip
    return made.id


def google_opens(w) -> dict[str, bool]:
    view = w.view()
    found = {}
    for account in view.google_accounts():
        try:
            found[account.name] = view.google_token(account.name) == TOKEN
        except Exception:  # TokenUnusable: it does not open
            found[account.name] = False
    for revocation, _subject, token in w.store.pending_google_revocations():
        found[revocation] = token == TOKEN
    return found


def test_a_connected_account_and_a_slack_install_sealed_under_an_earlier_key_still_work(world):
    name = connect(world, "ada.mail@example.test")
    install = slack_ready(world)
    under(world.store, CURRENT, EARLIER)
    assert google_opens(world) == {name: True}
    secret = world.store.slack_inbound(install).signing_secret
    assert (secret == SIGNING) is True
    assert (world.view().slack_bot_token(install) == BOT) is True
    under(world.store, CURRENT)  # without the earlier key, as before: nothing opens
    assert google_opens(world) == {name: False}
    assert world.store.slack_inbound(install).signing_secret is None


def test_reseal_seals_every_stored_value_under_the_current_key_and_counts_what_it_could_not_open(
    world,
):
    kept = connect(world, "ada.mail@example.test")
    dropped = connect(world, "ada.old@example.test")
    gone = world.view().disconnect_google(dropped)  # its token waits, sealed, to be revoked
    world.store.release_google_revocation(gone.pending, gone.claim)  # its send did not settle
    install = slack_ready(world)
    under(world.store, STRANGER)  # a key no one gives again: what it sealed stays shut
    lost = connect(world, "ada.lost@example.test")
    under(world.store, CURRENT, EARLIER)
    fresh = connect(world, "ada.new@example.test")
    shut = "SELECT sealed FROM credential_tokens WHERE credential = ?"
    before = sqlite3.connect(world.db).execute(shut, (lost,)).fetchone()
    google = reseal_google(world.store, world.store.google_custody.sealer)
    slack = reseal_slack(world.store, world.store.slack_sealer)
    assert google == Resealed(resealed=2, unopened=1, current=1)
    assert slack == Resealed(resealed=2, unopened=0, current=0)
    after = sqlite3.connect(world.db).execute(shut, (lost,)).fetchone()
    assert (after == before) is True  # what did not open is left as it was
    under(world.store, CURRENT)  # the earlier key removed
    opens = google_opens(world)
    assert opens.pop(lost) is False and set(opens.values()) == {True}
    assert {kept, fresh} <= set(opens) and len(opens) == 3  # the waiting token too
    assert (world.store.slack_inbound(install).signing_secret == SIGNING) is True
    assert (world.view().slack_bot_token(install) == BOT) is True


def test_reseal_is_safe_to_run_twice_and_with_no_earlier_key_changes_nothing(world):
    connect(world, "ada.mail@example.test")
    slack_ready(world)
    under(world.store, CURRENT, EARLIER)
    reseal_google(world.store, world.store.google_custody.sealer)
    reseal_slack(world.store, world.store.slack_sealer)
    before = dump(world.db)
    again = (
        reseal_google(world.store, world.store.google_custody.sealer),
        reseal_slack(world.store, world.store.slack_sealer),
    )
    assert again == (Resealed(0, 0, 1), Resealed(0, 0, 2))
    assert (dump(world.db) == before) is True
    under(world.store, CURRENT)
    assert reseal_google(world.store, world.store.google_custody.sealer) == Resealed(0, 0, 1)
    assert (dump(world.db) == before) is True


def test_a_pending_slack_install_holds_no_secret_and_is_not_counted(world):
    world.view().make_slack_install()
    assert reseal_slack(world.store, Sealer(CURRENT, (EARLIER,))) == Resealed(0, 0, 0)


# The command: reseal


def reseal(monkeypatch, db, *options: str, **env: str) -> int:
    for name in (*VARIABLES, "CAIRN_GOOGLE_CONNECT_CLIENT"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return server.main(["reseal", "--db", str(db), *options])


BOTH_ROTATED = {
    GOOGLE: b64(CURRENT), f"{GOOGLE}_PREVIOUS": b64(EARLIER),
    SLACK: b64(CURRENT), f"{SLACK}_PREVIOUS": b64(EARLIER),
}  # fmt: skip


def leaks(shown: str) -> bool:
    keys = any(b64(k) in shown or k.hex() in shown for k in (CURRENT, EARLIER, STRANGER))
    return keys or any(value in shown for value in (TOKEN, SIGNING, BOT, CLIENT_SECRET))


def test_reseal_both_keys_says_what_it_did_for_each_and_shows_nothing_secret(
    world, monkeypatch, capsys
):
    connect(world, "ada.mail@example.test")
    install = slack_ready(world)
    under(world.store, STRANGER)
    connect(world, "ada.lost@example.test")
    code = reseal(monkeypatch, world.db, "--google-connect", "--slack", **BOTH_ROTATED)
    said = capsys.readouterr()
    assert code == 0 and said.err == "" and leaks(said.out) is False
    assert said.out.splitlines() == [
        f"{GOOGLE}: 1 value sealed again under the current key, 1 that no key given opens left"
        " as it was, 0 already under it",
        f"{SLACK}: 2 values sealed again under the current key, 0 that no key given opens left"
        " as it was, 0 already under it",
    ]
    under(world.store, CURRENT)  # restarted without the earlier keys
    assert (world.store.slack_inbound(install).signing_secret == SIGNING) is True
    assert sorted(google_opens(world).values()) == [False, True]


def test_reseal_one_key_leaves_the_other_and_a_second_run_writes_nothing(
    world, monkeypatch, capsys
):
    connect(world, "ada.mail@example.test")
    slack_ready(world)
    before = dump(world.db)
    assert reseal(monkeypatch, world.db, "--slack", **BOTH_ROTATED) == 0
    assert capsys.readouterr().out.startswith(f"{SLACK}: 2 values sealed again")
    after = dump(world.db)
    google_rows = [line for line in after.splitlines() if "credential_tokens" in line]
    assert all(line in before for line in google_rows) is True  # the Google token untouched
    assert reseal(monkeypatch, world.db, "--slack", **BOTH_ROTATED) == 0
    assert capsys.readouterr().out.startswith(f"{SLACK}: 0 values sealed again")
    assert (dump(world.db) == after) is True


def test_reseal_needs_a_key_to_name(world, monkeypatch, capsys):
    before = dump(world.db)
    assert reseal(monkeypatch, world.db, **BOTH_ROTATED) == 2
    said = capsys.readouterr()
    assert "--google-connect" in said.err and "--slack" in said.err
    assert len(said.err.strip().splitlines()) == 1 and (dump(world.db) == before) is True


@pytest.mark.parametrize("value", list(MALFORMED.values()), ids=list(MALFORMED))
@pytest.mark.parametrize("variable", [GOOGLE, SLACK])
def test_reseal_refuses_a_malformed_or_repeated_earlier_key_and_writes_nothing(
    world, monkeypatch, capsys, variable, value
):
    connect(world, "ada.mail@example.test")
    slack_ready(world)
    before = dump(world.db)
    env = {**BOTH_ROTATED, f"{variable}_PREVIOUS": value}
    code = reseal(monkeypatch, world.db, "--google-connect", "--slack", **env)
    said = capsys.readouterr()
    lines = said.err.strip().splitlines()
    assert code == 2 and said.out == "" and len(lines) == 1
    assert f"{variable}_PREVIOUS" in lines[0]
    leaked = any(part.strip() and part.strip() in said.err for part in value.split(","))
    assert (leaked or leaks(said.err)) is False
    assert (dump(world.db) == before) is True


def test_reseal_without_the_current_key_says_which_variable(world, monkeypatch, capsys):
    env = {k: v for k, v in BOTH_ROTATED.items() if k != SLACK}
    assert reseal(monkeypatch, world.db, "--slack", **env) == 2
    err = capsys.readouterr().err
    assert SLACK in err and len(err.strip().splitlines()) == 1 and leaks(err) is False


def test_reseal_needs_no_google_client_and_no_database_is_refused(tmp_path, monkeypatch, capsys):
    env = {GOOGLE: b64(CURRENT)}
    assert reseal(monkeypatch, tmp_path / "none.db", "--google-connect", **env) == 1
    assert capsys.readouterr().err.strip() == f"no database at {tmp_path / 'none.db'}"
    assert not (tmp_path / "none.db").exists()
