"""Sentences of the guides about changing a key that seals stored secrets
(`CAIRN_GOOGLE_CONNECT_KEY`, `CAIRN_SLACK_KEY`), each pinned here so the guides cannot go false
unnoticed: the sentence is in the test's docstring and in the guide as a person reads it, and
the test asks the running code for it (tests/unit/test_key_rotation_unit.py has the rest).
Every key and secret is made up; no assertion prints one when it fails."""

import argparse

from test_guide_facts_slack_unit import says
from test_key_rotation_unit import (  # noqa: F401
    BOTH_ROTATED,
    CURRENT,
    EARLIER,
    GOOGLE,
    OLDEST,
    SIGNING,
    SLACK,
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
    world,
)

from cairn.store.hosted import PREVIOUS, PREVIOUS_SEPARATOR, Sealer
from cairn.web import cli, google_connect
from cairn.web import slack as web_slack

CHANGE = ("run", "## Change a sealing key")


def test_run_either_key_can_be_changed_in_four_steps(world, monkeypatch, capsys):  # noqa: F811
    """run.md: "`CAIRN_GOOGLE_CONNECT_KEY` seals the tokens of the Google accounts connected in
    the browser, and `CAIRN_SLACK_KEY` seals each workspace's Slack signing secret and bot
    token. Either key can be changed without anyone connecting again, in four steps:" then
    the four steps, each pinned below."""
    says(
        *CHANGE,
        "`CAIRN_GOOGLE_CONNECT_KEY` seals the tokens of the Google accounts connected in the"
        " browser, and `CAIRN_SLACK_KEY` seals each workspace's Slack signing secret and bot"
        " token. Either key can be changed without anyone connecting again, in four steps:",
    )
    holds((google_connect.KEY_VARIABLE, web_slack.KEY_VARIABLE) == (GOOGLE, SLACK))
    connect(world, "ada.mail@example.test")
    install = slack_ready(world)  # sealed under EARLIER, the old key
    holds(reseal(monkeypatch, world.db, "--google-connect", "--slack", **BOTH_ROTATED) == 0)
    capsys.readouterr()
    under(world.store, CURRENT)  # step 4: the earlier key removed
    holds(google_opens(world) == {name: True for name in google_opens(world)})
    holds((world.store.slack_inbound(install).signing_secret == SIGNING) is True)


def test_run_step_one_the_old_key_beside_the_new_one():
    """run.md: "Put the new key in the variable, and the old one in a variable of the same name
    followed by `_PREVIOUS`: `CAIRN_GOOGLE_CONNECT_KEY_PREVIOUS` or
    `CAIRN_SLACK_KEY_PREVIOUS`.\""""
    says(
        *CHANGE,
        "Put the new key in the variable, and the old one in a variable of the same name"
        " followed by `_PREVIOUS`: `CAIRN_GOOGLE_CONNECT_KEY_PREVIOUS` or"
        " `CAIRN_SLACK_KEY_PREVIOUS`.",
    )
    holds(PREVIOUS == "_PREVIOUS")
    google = google_connect.read_switch(google_env(**{GOOGLE + PREVIOUS: b64(EARLIER)}))
    slack = web_slack.read_switch(slack_env(**{SLACK + PREVIOUS: b64(EARLIER)}))
    for sealer in (google.custody.sealer, slack.sealer()):
        holds(opens_earlier(sealer, EARLIER) is True)


def test_run_step_two_restart_and_new_values_are_sealed_under_the_new_key():
    """run.md: "Restart the server. What the old key sealed still opens, and everything sealed
    from now on is sealed under the new key.\""""
    says(
        *CHANGE,
        "Restart the server. What the old key sealed still opens, and everything sealed from"
        " now on is sealed under the new key.",
    )
    sealer = web_slack.read_switch(slack_env(**{SLACK + PREVIOUS: b64(EARLIER)})).sealer()
    sealed = sealer.seal("x", b"t")
    holds(Sealer(CURRENT).open(sealed, b"t") == "x" and Sealer(EARLIER).open(sealed, b"t") is None)


def test_run_step_three_reseal_and_what_it_prints(world, monkeypatch, capsys):  # noqa: F811
    """run.md: "Run `docker exec cairn python -m cairn.server reseal --db /data/cairn.db
    --google-connect --slack`, or with only the option of the key you changed. It seals every
    stored value again under the new key and prints, for each key, how many it sealed again,
    how many no key given opens, which it leaves as they were, and how many were under the new
    key already. Running it again changes nothing.\""""
    says(
        *CHANGE,
        "Run `docker exec cairn python -m cairn.server reseal --db /data/cairn.db"
        " --google-connect --slack`, or with only the option of the key you changed. It seals"
        " every stored value again under the new key and prints, for each key, how many it"
        " sealed again, how many no key given opens, which it leaves as they were, and how many"
        " were under the new key already. Running it again changes nothing.",
    )
    args = cli._parser().parse_args(["reseal", "--db", "/data/cairn.db", "--slack"])
    holds(isinstance(args, argparse.Namespace) and args.slack and not args.google_connect)
    slack_ready(world)
    for again in (2, 0):
        holds(reseal(monkeypatch, world.db, "--slack", **BOTH_ROTATED) == 0)
        (line,) = capsys.readouterr().out.splitlines()
        holds(line.startswith(f"{SLACK}: {again} values sealed again under the current key"))
        holds("that no key given opens left as it was" in line and "already under it" in line)


def test_run_step_four_remove_the_previous_variable():
    """run.md: "Remove the `_PREVIOUS` variable and restart the server.\""""
    says(*CHANGE, "Remove the `_PREVIOUS` variable and restart the server.")
    google = google_connect.read_switch(google_env())
    holds(opens_earlier(google.custody.sealer, EARLIER) is False)


def test_run_several_earlier_keys_and_what_serve_refuses():
    """run.md: "A `_PREVIOUS` variable may hold several earlier keys, separated by commas. A key
    that is not 32 bytes in base64, or an earlier key equal to the current one, stops `serve`
    at start with one line that names the variable, never a key.\""""
    says(
        *CHANGE,
        "A `_PREVIOUS` variable may hold several earlier keys, separated by commas. A key that"
        " is not 32 bytes in base64, or an earlier key equal to the current one, stops `serve` at"
        " start with one line that names the variable, never a key.",
    )
    holds(PREVIOUS_SEPARATOR == ",")
    sealer = web_slack.read_switch(slack_env(**{SLACK + PREVIOUS: b64s(EARLIER, OLDEST)})).sealer()
    holds(opens_earlier(sealer, EARLIER, OLDEST) is True)
    # serve's refusals: test_key_rotation_unit.test_serve_stops_at_start_on_a_malformed_...


def test_run_a_backup_taken_before_reseal():
    """run.md: "A sealed value does not say which key sealed it: the server tries the current
    key, then each earlier one in the order given. A backup taken before step 3 holds values
    sealed under the old key: to restore it after step 4, give the old key as `_PREVIOUS`
    again, restart and run `reseal`.\""""
    says(
        *CHANGE,
        "A sealed value does not say which key sealed it: the server tries the current key,"
        " then each earlier one in the order given.",
        "A backup taken before step 3 holds values sealed under the old key: to restore it"
        " after step 4, give the old key as `_PREVIOUS` again, restart and run `reseal`.",
    )
    old = Sealer(EARLIER).seal("x", b"t")
    holds(len(old) == len(Sealer(CURRENT, (EARLIER,)).seal("x", b"t")))
    holds(Sealer(CURRENT).open(old, b"t") is None)
    holds(Sealer(CURRENT, (EARLIER,)).open(old, b"t") == "x")


def test_run_slack_a_key_lost_or_changed_without_the_old_one():
    """run.md: "When the key is lost, or changed without the old one kept in
    `CAIRN_SLACK_KEY_PREVIOUS` ([below](#change-a-sealing-key)), no secret sealed with the old
    one opens, and Slack's requests to the server are refused: each workspace's owner removes
    Slack on the **Sources** page, sets it up again and pastes both secrets.\""""
    says(
        "run",
        "## Slack",
        "When the key is lost, or changed without the old one kept in"
        " `CAIRN_SLACK_KEY_PREVIOUS` ([below](#change-a-sealing-key)), no secret sealed with the"
        " old one opens, and Slack's requests to the server are refused: each workspace's owner"
        " removes Slack on the **Sources** page, sets it up again and pastes both secrets.",
    )
    sealed = Sealer(EARLIER).seal(SIGNING, b"t")
    holds(web_slack.read_switch(slack_env()).sealer().open(sealed, b"t") is None)


SECURITY_GOOGLE = ("security", "## Google accounts connected in the browser")


def test_security_google_how_the_key_is_changed_and_when_that_is_not_enough():
    """security.md: "Changing `CAIRN_GOOGLE_CONNECT_KEY` without keeping the old key in
    `CAIRN_GOOGLE_CONNECT_KEY_PREVIOUS` makes every connected account reconnect, ..." and the
    four steps, what a copy of the database tells, and the order when the old key leaked."""
    says(
        *SECURITY_GOOGLE,
        "Changing `CAIRN_GOOGLE_CONNECT_KEY` without keeping the old key in"
        " `CAIRN_GOOGLE_CONNECT_KEY_PREVIOUS` makes every connected account reconnect, and"
        " leaves the permissions Google gave for the old tokens in place, with nothing here able"
        " to end them.",
        "To change it with every account staying connected: put the new key in"
        " `CAIRN_GOOGLE_CONNECT_KEY` and the old one in `CAIRN_GOOGLE_CONNECT_KEY_PREVIOUS`;"
        " restart the server; run the `reseal` command with `--google-connect`, which seals"
        " every stored token again under the new key; then remove"
        " `CAIRN_GOOGLE_CONNECT_KEY_PREVIOUS` and restart.",
        "A sealed token does not say which key sealed it, so a copy of the database does not"
        " tell which tokens an earlier key sealed.",
        "When the old key may have been seen by someone else, changing it is not enough, since"
        " a copy of the database made before then still opens with it: disconnect every"
        " connected account first, wait until Google has answered each revocation (the log"
        " says when one was not ended), then change the key.",
    )
    held = Sealer(EARLIER).seal("refresh", b"t")
    custody = google_connect.read_switch(google_env(**{GOOGLE + PREVIOUS: b64(EARLIER)})).custody
    holds(custody.sealer.open(held, b"t") == "refresh")
    renewed = custody.sealer.seal("refresh", b"t")
    holds(len(renewed) == len(held))  # nothing in a value names its key
    without = google_connect.read_switch(google_env()).custody.sealer
    holds(without.open(held, b"t") is None)  # without the old key: reconnect


def test_security_slack_how_the_key_is_changed():
    """security.md: "To change it with every install staying set up: put the new key in
    `CAIRN_SLACK_KEY` and the old one in `CAIRN_SLACK_KEY_PREVIOUS`; restart the server; run
    the `reseal` command with `--slack`; then remove `CAIRN_SLACK_KEY_PREVIOUS` and
    restart.\""""
    says(
        "security",
        "## Slack",
        "To change it with every install staying set up: put the new key in `CAIRN_SLACK_KEY`"
        " and the old one in `CAIRN_SLACK_KEY_PREVIOUS`; restart the server; run the `reseal`"
        " command with `--slack`; then remove `CAIRN_SLACK_KEY_PREVIOUS` and restart.",
    )
    args = cli._parser().parse_args(["reseal", "--db", "cairn.db", "--slack"])
    holds(args.command == "reseal" and args.slack is True)
