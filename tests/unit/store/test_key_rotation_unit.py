"""Changing a key that seals stored secrets (`CAIRN_GOOGLE_CONNECT_KEY`, `CAIRN_SLACK_KEY`)
without losing what it sealed: earlier keys beside the current one (`<VARIABLE>_PREVIOUS`),
the `reseal` command, and what `serve` refuses.

Every key, token and secret here is made up. No check prints a key, a token or a sealed value
when it fails: `holds` fails with the line of the test that failed, never the values pytest's
assertion rewriting would show.
"""

import base64
import sqlite3

import pytest
import uvicorn

from cairn import server
from cairn.store.hosted import Sealer
from cairn.store.reseal import Resealed, reseal_google, reseal_slack
from cairn.web import google_connect
from cairn.web import slack as web_slack
from support.store.key_rotation import (
    BOT,
    BOTH_ROTATED,
    CLIENT_SECRET,
    CURRENT,
    EARLIER,
    GOOGLE,
    OLDEST,
    SIGNING,
    SLACK,
    TIED,
    TOKEN,
    VARIABLES,
    b64,
    b64s,
    connect,
    google_env,
    google_opens,
    holds,
    opens_earlier,
    reseal,
    slack_env,
    slack_ready,
    under,
)
from support.store.key_rotation import world as world

STRANGER = bytes(range(3, 35))
BASE = "https://cairn.example.test"
SIGN_IN_ENV = {"CAIRN_GITHUB_CLIENT_ID": "gh-id", "CAIRN_GITHUB_CLIENT_SECRET": "gh-secret"}


# Sealer: earlier keys open, the current key seals


def test_a_value_sealed_under_an_earlier_key_opens_beside_the_current_one():
    for earlier in (EARLIER, OLDEST):
        sealed = Sealer(earlier).seal(TOKEN, TIED)
        opened = Sealer(CURRENT, (EARLIER, OLDEST)).open(sealed, TIED)
        holds((opened == TOKEN) is True)


def test_what_is_sealed_from_now_on_is_sealed_under_the_current_key():
    sealed = Sealer(CURRENT, (EARLIER,)).seal(TOKEN, TIED)
    holds((Sealer(CURRENT).open(sealed, TIED) == TOKEN) is True)
    holds(Sealer(EARLIER).open(sealed, TIED) is None)


def test_a_value_of_no_key_given_does_not_open():
    sealed = Sealer(STRANGER).seal(TOKEN, TIED)
    holds(Sealer(CURRENT, (EARLIER, OLDEST)).open(sealed, TIED) is None)


def test_a_sealed_value_carries_nothing_that_says_which_key_sealed_it():
    """Trial in a fixed order: the form of a value is the same under every key and the same as
    before earlier keys could be given (base64url of a 12-byte nonce and the sealed text)."""
    now = Sealer(CURRENT, (EARLIER,)).seal(TOKEN, TIED)
    before = Sealer(EARLIER).seal(TOKEN, TIED)
    holds(len(now) == len(before))
    holds(len(base64.urlsafe_b64decode(now)) == 12 + len(TOKEN.encode()) + 16)


def test_the_sealer_says_whether_the_current_key_opens_a_value():
    sealer = Sealer(CURRENT, (EARLIER,))
    holds(sealer.current(sealer.seal(TOKEN, TIED), TIED) is True)
    holds(sealer.current(Sealer(EARLIER).seal(TOKEN, TIED), TIED) is False)
    holds(sealer.current(Sealer(STRANGER).seal(TOKEN, TIED), TIED) is False)
    holds(sealer.current(None, TIED) is False)


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
    holds(leaked is False)


def test_no_repr_of_a_sealer_with_earlier_keys_shows_a_key():
    shown = repr(Sealer(CURRENT, (EARLIER, OLDEST)))
    leaked = any(k.hex() in shown or b64(k) in shown for k in (CURRENT, EARLIER, OLDEST))
    holds(leaked is False and shown == "Sealer()")


# The variables, and what serve refuses


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
        holds(opens_earlier(sealer, *earlier) is True)
        holds((Sealer(CURRENT).open(sealer.seal(TOKEN, TIED), TIED) == TOKEN) is True)


def test_with_no_earlier_key_or_a_blank_one_nothing_earlier_opens():
    for more in ({}, {f"{GOOGLE}_PREVIOUS": "  ", f"{SLACK}_PREVIOUS": ""}):
        google = google_connect.read_switch(google_env(**more))
        slack = web_slack.read_switch(slack_env(**more))
        for sealer in (google.custody.sealer, slack.sealer()):
            holds(opens_earlier(sealer, EARLIER) is False)
            holds(opens_earlier(sealer, CURRENT) is True)


@pytest.mark.parametrize("switch", ["--google-connect", "--slack"])
def test_serve_starts_with_earlier_keys_and_says_none(tmp_path, monkeypatch, capsys, switch):
    env = google_env(**{f"{GOOGLE}_PREVIOUS": b64s(EARLIER, OLDEST)})
    if switch == "--slack":
        env = slack_env(**{f"{SLACK}_PREVIOUS": b64s(EARLIER, OLDEST)})
    code, apps, _db = serve(monkeypatch, tmp_path, switch, env)
    said = capsys.readouterr()
    shown = said.out + said.err
    leaked = any(b64(k) in shown or k.hex() in shown for k in (CURRENT, EARLIER, OLDEST))
    holds(code == 0 and len(apps) == 1 and leaked is False)


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
    holds(code == 2 and apps == [] and not db.parent.exists() and len(lines) == 1)
    holds(f"{variable}_PREVIOUS" in lines[0])
    shown = said.out + said.err
    leaked = any(part.strip() and part.strip() in shown for part in value.split(","))
    leaked = leaked or any(b64(k) in shown or k.hex() in shown for k in (CURRENT, EARLIER))
    holds(leaked is False)


# The stored values under a changed key, and re-sealing them


def dump(db) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def one_row(db, sql: str, *params: object) -> tuple | None:
    con = sqlite3.connect(db)
    try:
        return con.execute(sql, params).fetchone()
    finally:
        con.close()


def test_a_connected_account_and_a_slack_install_sealed_under_an_earlier_key_still_work(world):
    name = connect(world, "ada.mail@example.test")
    install = slack_ready(world)
    under(world.store, CURRENT, EARLIER)
    holds(google_opens(world) == {name: True})
    secret = world.store.slack_inbound(install).signing_secret
    holds((secret == SIGNING) is True)
    holds((world.view().slack_bot_token(install) == BOT) is True)
    under(world.store, CURRENT)  # without the earlier key, as before: nothing opens
    holds(google_opens(world) == {name: False})
    holds(world.store.slack_inbound(install).signing_secret is None)


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
    before = one_row(world.db, shut, lost)
    google = reseal_google(world.store, world.store.google_custody.sealer)
    slack = reseal_slack(world.store, world.store.slack_sealer)
    holds(google == Resealed(resealed=2, unopened=1, current=1))
    holds(slack == Resealed(resealed=2, unopened=0, current=0))
    after = one_row(world.db, shut, lost)
    holds((after == before) is True)  # what did not open is left as it was
    under(world.store, CURRENT)  # the earlier key removed
    opens = google_opens(world)
    holds(opens.pop(lost) is False and set(opens.values()) == {True})
    holds({kept, fresh} <= set(opens) and len(opens) == 3)  # the waiting token too
    holds((world.store.slack_inbound(install).signing_secret == SIGNING) is True)
    holds((world.view().slack_bot_token(install) == BOT) is True)


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
    holds(again == (Resealed(0, 0, 1), Resealed(0, 0, 2)))
    holds((dump(world.db) == before) is True)
    under(world.store, CURRENT)
    holds(reseal_google(world.store, world.store.google_custody.sealer) == Resealed(0, 0, 1))
    holds((dump(world.db) == before) is True)


def test_a_pending_slack_install_holds_no_secret_and_is_not_counted(world):
    world.view().make_slack_install()
    holds(reseal_slack(world.store, Sealer(CURRENT, (EARLIER,))) == Resealed(0, 0, 0))


# The command: reseal


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
    holds(code == 0 and said.err == "" and leaks(said.out) is False)
    assert said.out.splitlines() == [
        f"{GOOGLE}: 1 value sealed again under the current key, 1 that no key given opens left"
        " as it was, 0 already under it",
        f"{SLACK}: 2 values sealed again under the current key, 0 that no key given opens left"
        " as it was, 0 already under it",
    ]
    under(world.store, CURRENT)  # restarted without the earlier keys
    holds((world.store.slack_inbound(install).signing_secret == SIGNING) is True)
    holds(sorted(google_opens(world).values()) == [False, True])


def test_reseal_one_key_leaves_the_other_and_a_second_run_writes_nothing(
    world, monkeypatch, capsys
):
    connect(world, "ada.mail@example.test")
    slack_ready(world)
    before = dump(world.db)
    holds(reseal(monkeypatch, world.db, "--slack", **BOTH_ROTATED) == 0)
    holds(capsys.readouterr().out.startswith(f"{SLACK}: 2 values sealed again"))
    after = dump(world.db)
    google_rows = [line for line in after.splitlines() if "credential_tokens" in line]
    holds(all(line in before for line in google_rows) is True)  # the Google token untouched
    holds(reseal(monkeypatch, world.db, "--slack", **BOTH_ROTATED) == 0)
    holds(capsys.readouterr().out.startswith(f"{SLACK}: 0 values sealed again"))
    holds((dump(world.db) == after) is True)


def test_reseal_needs_a_key_to_name(world, monkeypatch, capsys):
    before = dump(world.db)
    holds(reseal(monkeypatch, world.db, **BOTH_ROTATED) == 2)
    said = capsys.readouterr()
    holds("--google-connect" in said.err and "--slack" in said.err)
    holds(len(said.err.strip().splitlines()) == 1 and (dump(world.db) == before) is True)


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
    holds(code == 2 and said.out == "" and len(lines) == 1)
    holds(f"{variable}_PREVIOUS" in lines[0])
    leaked = any(part.strip() and part.strip() in said.err for part in value.split(","))
    holds((leaked or leaks(said.err)) is False)
    holds((dump(world.db) == before) is True)


def test_reseal_without_the_current_key_says_which_variable(world, monkeypatch, capsys):
    env = {k: v for k, v in BOTH_ROTATED.items() if k != SLACK}
    holds(reseal(monkeypatch, world.db, "--slack", **env) == 2)
    err = capsys.readouterr().err
    holds(SLACK in err and len(err.strip().splitlines()) == 1 and leaks(err) is False)


def test_reseal_needs_no_google_client_and_no_database_is_refused(tmp_path, monkeypatch, capsys):
    env = {GOOGLE: b64(CURRENT)}
    holds(reseal(monkeypatch, tmp_path / "none.db", "--google-connect", **env) == 1)
    holds(capsys.readouterr().err.strip() == f"no database at {tmp_path / 'none.db'}")
    holds(not (tmp_path / "none.db").exists())


def test_no_log_line_of_a_reseal_or_of_a_refusal_shows_a_key_or_a_secret(
    world, monkeypatch, capsys, caplog
):
    connect(world, "ada.mail@example.test")
    slack_ready(world)
    caplog.set_level("DEBUG")
    reseal(monkeypatch, world.db, "--google-connect", "--slack", **BOTH_ROTATED)
    bad = {**BOTH_ROTATED, f"{GOOGLE}_PREVIOUS": b64(CURRENT)}
    reseal(monkeypatch, world.db, "--google-connect", **bad)
    capsys.readouterr()
    logged = "\n".join(record.getMessage() for record in caplog.records)
    holds(leaks(logged) is False)


def test_no_repr_of_the_slack_switch_or_the_google_custody_shows_a_key():
    slack = web_slack.read_switch(slack_env(**{f"{SLACK}_PREVIOUS": b64s(EARLIER, OLDEST)}))
    google = google_connect.read_switch(google_env(**{f"{GOOGLE}_PREVIOUS": b64(EARLIER)}))
    shown = repr(slack) + repr(google) + repr(google.custody)
    leaked = any(repr(k) in shown or k.hex() in shown for k in (CURRENT, EARLIER, OLDEST))
    holds(leaked is False and leaks(shown) is False)


@pytest.mark.parametrize(
    "previous",
    [(CURRENT,), (EARLIER, CURRENT), (bytes(16),)],
    ids=["the-current-key", "the-current-key-second", "16-bytes"],
)
def test_create_app_refuses_a_slack_switch_whose_earlier_key_is_wrong_naming_the_variable(
    previous,
):
    switch = web_slack.Slack(key=CURRENT, previous=previous)
    with pytest.raises(ValueError) as refused:
        web_slack.check(switch, sign_in=object(), public_url=BASE)
    said = str(refused.value)
    leaked = any(k and (k.hex() in said or b64(k) in said) for k in (CURRENT, *previous))
    holds(f"{SLACK}_PREVIOUS" in said and leaked is False)
