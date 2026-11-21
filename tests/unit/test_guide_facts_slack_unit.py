"""Sentences of the guides about Slack (K1: tests/acceptance/test_k1_slack.py,
test_k1b_slack_setup.py and test_k1c_slack_use.py), each pinned here so the guides cannot go
false unnoticed: the sentence is in the test's docstring and in the guide as a person reads it
(guide_source: the product's name filled in), and the test asks the running code for it. Every
call to Slack goes to an httpx.MockTransport (test_slack_commands_unit.Bench); nothing leaves
the process. Every name, id and secret is made up."""

import argparse
import asyncio
import base64
import inspect
import json
import logging
import re
import shutil
import sqlite3
from datetime import datetime, timedelta
from importlib.resources import files

import httpx
import pytest
import uvicorn
from guide_source import guide_source
from markdown_it import MarkdownIt
from starlette.testclient import TestClient
from test_slack_commands_unit import ADA_SLACK, BASE, BROOK_SLACK, REPLY, TEAM, Bench, ask, said
from test_slack_shortcuts_unit import CEDAR, CONVERSATION, cedar, handle, replied
from test_slack_store_unit import KEY, SECRET, TOKEN, a_note, ready, world  # noqa: F401

import cairn.store.slack as store_slack
from cairn import server, sources
from cairn.arrange import Arranger
from cairn.compiler import compile_note, extractive
from cairn.identity import GitHub
from cairn.llm import FakeModel
from cairn.memory import Memory
from cairn.server import SignIn, create_app
from cairn.slack import (
    blocks,
    commands,
    dispatch,
    link,
    manifest,
    messages,
    people,
    setup,
    shortcuts,
)
from cairn.slack.client import SlackClient
from cairn.slack.text import note_from_message
from cairn.slack.verify import BODY_MAX, SKEW, OtherTeam, envelope, signature, verified
from cairn.spans import chunk
from cairn.store import Store
from cairn.store.hosted import Sealer
from cairn.store.memory import TITLE_MAX
from cairn.store.slack import (
    ACCEPTING,
    CHECK_EVERY,
    COMMAND,
    FORGET_AFTER,
    OFFER_TTL,
    TRIES,
)
from cairn.web import cli_arrange
from cairn.web import slack as web_slack
from cairn.web.brand import product_of
from cairn.web.markdown import render
from cookie_names import session_cookie

PRODUCT = product_of(None)
RULE = "github_token"  # the note guard's rule, as cairn names it
SIGN_IN_ENV = {"CAIRN_GITHUB_CLIENT_ID": "gh-id", "CAIRN_GITHUB_CLIENT_SECRET": "gh-secret"}
KEY_B64 = base64.b64encode(KEY).decode()


def flat(text: str) -> str:
    return " ".join(text.split())


def section(guide: str, heading: str) -> str:
    """The section `heading` of the guide as a person reads it, to the next section."""
    raw = guide_source(guide)
    start = raw.index(f"\n{heading}\n")
    end = raw.find("\n## ", start + len(heading) + 2)
    return raw[start : end if end != -1 else None]


def says(guide: str, heading: str, *sentences: str) -> None:
    """Each sentence is in that section of the guide, as a person reads it, once."""
    text = flat(section(guide, heading))
    for sentence in sentences:
        assert flat(sentence) in text, sentence
        assert flat(guide_source(guide)).count(flat(sentence)) == 1, sentence


def serve(monkeypatch, tmp_path, argv, env):
    """`serve` as the command line runs it, with uvicorn kept from listening."""
    for name in (web_slack.KEY_VARIABLE, *SIGN_IN_ENV):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    db = tmp_path / "data" / "cairn.db"
    return server.main(["serve", "--db", str(db), *argv]), apps, db


@pytest.fixture
def bench(tmp_path):
    made = Bench(tmp_path)
    yield made
    made.store.close()


# The run guide, "Slack": for the operator

RUN = ("run", "## Slack")


def test_run_slack_lets_each_workspace_set_up_an_app_of_its_own():
    """run.md: "`serve --slack` lets each workspace set up a Slack app of its own: people then
    file a Slack message as a note, write a note, and ask a bot to find something, each as the
    person of the workspace they linked their Slack user to. The [connect
    guide](connect.md#from-slack) says how an owner sets it up and what people do, and the
    [privacy guide](privacy.md#slack) says what it keeps.\""""
    says(
        *RUN,
        "`serve --slack` lets each workspace set up a Slack app of its own: people then file a"
        " Slack message as a note, write a note, and ask a bot to find something, each as the"
        " person of the workspace they linked their Slack user to. The [connect"
        " guide](connect.md#from-slack) says how an owner sets it up and what people do, and the"
        " [privacy guide](privacy.md#slack) says what it keeps.",
    )
    assert "\n## From Slack\n" in guide_source("connect")
    assert "\n## Slack\n" in guide_source("privacy")
    assert set(dispatch.COMMANDS) >= {"find", "note"}
    assert dispatch.ACTIONS["file_in_cairn"] is shortcuts.file_in_cairn
    assert "message" in dispatch.EVENTS


def test_run_slack_needs_sign_in_an_https_public_url_and_the_key(tmp_path, monkeypatch, capsys):
    """run.md: "`--slack` needs `--sign-in`, since a Slack user is linked to a person who signs
    in, and an `https://` `--public-url`, since Slack calls the server at `<public
    url>/integrations/slack/<install>/events`, `/commands` and `/actions`. It also needs
    `CAIRN_SLACK_KEY`, 32 random bytes in base64, the key every workspace's Slack secrets are
    sealed with: make it with `openssl rand -base64 32`. Without one of the three, `serve`
    stops at start, before it opens the database, and the last line it prints names what is
    missing, never the key.\""""
    says(
        *RUN,
        "`--slack` needs `--sign-in`, since a Slack user is linked to a person who signs in, and"
        " an `https://` `--public-url`, since Slack calls the server at `<public"
        " url>/integrations/slack/<install>/events`, `/commands` and `/actions`.",
        "It also needs `CAIRN_SLACK_KEY`, 32 random bytes in base64, the key every workspace's"
        " Slack secrets are sealed with: make it with `openssl rand -base64 32`.",
        "Without one of the three, `serve` stops at start, before it opens the database, and the"
        " last line it prints names what is missing, never the key.",
    )
    assert web_slack.KEY_VARIABLE == "CAIRN_SLACK_KEY"
    assert "openssl rand -base64 32" in web_slack.NO_KEY and "32 random bytes" in web_slack.NO_KEY
    assert "/integrations/slack/<install>/events, /commands and /actions" in web_slack.NEEDS_HTTPS
    made = base64.b64encode(bytes(range(32, 64))).decode() + "\n"  # as openssl prints it
    assert web_slack.read_switch({"CAIRN_SLACK_KEY": made}).key == bytes(range(32, 64))
    cases = [
        (["--public-url", BASE], {"CAIRN_SLACK_KEY": KEY_B64}, "--sign-in"),
        (
            ["--public-url", "http://cairn.example.test", "--sign-in", "github"],
            {**SIGN_IN_ENV, "CAIRN_SLACK_KEY": KEY_B64},
            "https://",
        ),
        (["--public-url", BASE, "--sign-in", "github"], SIGN_IN_ENV, "CAIRN_SLACK_KEY"),
    ]
    for n, (argv, env, named) in enumerate(cases):
        code, apps, db = serve(monkeypatch, tmp_path / str(n), [*argv, "--slack"], env)
        assert code == 2 and apps == [] and not db.parent.exists()
        last = capsys.readouterr().err.strip().splitlines()[-1]
        assert named in last and KEY_B64 not in last
    argv = ["--public-url", BASE, "--sign-in", "github", "--slack"]
    env = {**SIGN_IN_ENV, "CAIRN_SLACK_KEY": KEY_B64}
    code, apps, _ = serve(monkeypatch, tmp_path / "on", argv, env)
    assert code == 0 and len(apps) == 1


def test_run_slack_is_not_offered_under_a_brand(tmp_path, monkeypatch, capsys):
    """run.md: "It is not offered under a brand yet: with `--brand-name`, `serve` stops the
    same way, since what the bot says, the shortcut's name and the command's name would name
    the software.\""""
    says(
        *RUN,
        "It is not offered under a brand yet: with `--brand-name`, `serve` stops the same way,"
        " since what the bot says, the shortcut's name and the command's name would name the"
        " software.",
    )
    argv = ["--public-url", BASE, "--sign-in", "github", "--brand-name", "Fable Notes", "--slack"]
    code, apps, db = serve(monkeypatch, tmp_path, argv, {**SIGN_IN_ENV, "CAIRN_SLACK_KEY": KEY_B64})
    assert code == 2 and apps == [] and not db.parent.exists()
    assert web_slack.UNDER_A_BRAND in capsys.readouterr().err.strip().splitlines()[-1]
    assert "the shortcut's name and the command's name" in web_slack.UNDER_A_BRAND


def test_run_slack_needs_a_public_address():
    """run.md: "Slack cannot reach a local install that listens on 127.0.0.1, as the commands
    above start it, so this version is for an install with a public address, behind a reverse
    proxy that ends TLS.\""""
    says(
        *RUN,
        "Slack cannot reach a local install that listens on 127.0.0.1, as the commands above"
        " start it, so this version is for an install with a public address, behind a reverse"
        " proxy that ends TLS.",
    )
    assert "-p 127.0.0.1:8765:8765" in section("run", "## Start it")
    for local in ("http://127.0.0.1:8765", "http://localhost:8765"):
        options = argparse.Namespace(
            slack=True, brand_name=None, sign_in="github", public_url=local
        )
        with pytest.raises(ValueError) as refused:
            web_slack.serve_switch(options, {"CAIRN_SLACK_KEY": KEY_B64})
        assert str(refused.value) == web_slack.NEEDS_HTTPS


def test_run_slack_not_tried_with_enterprise_grid_or_govslack():
    """run.md: "This version was not tried with an Enterprise Grid organisation or with
    GovSlack.\""""
    says(
        *RUN,
        "This version was not tried with an Enterprise Grid organisation or with GovSlack.",
    )


def test_run_slack_keep_the_key_apart_and_losing_it_means_setting_up_again(world):  # noqa: F811
    """run.md: "Keep the key apart from the database and its backups, as you keep the
    environment file: a copy of the database holds each workspace's signing secret and bot
    token sealed, and without the key it opens neither. When the key is lost, or changed
    without the old one kept in `CAIRN_SLACK_KEY_PREVIOUS` ([below](#change-a-sealing-key)), no
    secret sealed with the old one opens, and Slack's requests to the server are refused.
    To keep the same Slack workspace and its links and notes, paste both secrets again on the
    **Sources** page. If the page only asks for the bot token and cannot open the signing
    secret, remove Slack and set it up again as a last resort.\""""
    says(
        *RUN,
        "Keep the key apart from the database and its backups, as you keep the environment"
        " file: a copy of the database holds each workspace's signing secret and bot token"
        " sealed, and without the key it opens neither.",
        "When the key is lost, or changed without the old one kept in"
        " `CAIRN_SLACK_KEY_PREVIOUS` ([below](#change-a-sealing-key)), no secret sealed with the"
        " old one opens, and Slack's requests to the server are refused.",
        "To keep the same Slack workspace and its links and notes, paste both secrets again on"
        " the **Sources** page.",
        "If the page only asks for the bot token and cannot open the signing secret, remove"
        " Slack and set it up again as a last resort.",
    )
    install = ready(world)
    world.view.link_slack_user(install.id, ADA_SLACK, world.owner)
    note = a_note(world)
    world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)
    held = "\n".join(sqlite3.connect(world.db).iterdump())
    assert SECRET not in held and TOKEN not in held
    assert world.store.slack_inbound(install.id).signing_secret == SECRET
    world.store.slack_sealer = Sealer(bytes(range(1, 33)))  # another key
    assert world.store.slack_inbound(install.id).signing_secret is None
    assert "secret is None or not verified(" in inspect.getsource(web_slack.slack_routes)  # 401
    assert "remove Slack and set it up again" in setup.REFUSALS["unopened"]
    view = world.store.for_account(world.account, world.owner)  # a view under the new key
    again = ready(world, view=view)
    assert again.id == install.id
    same_secret = world.store.slack_inbound(install.id).signing_secret == SECRET
    same_token = view.slack_bot_token(install.id) == TOKEN
    assert same_secret and same_token
    assert view.slack_person(install.id, ADA_SLACK).user_id == world.owner
    assert view.get_note(note) is not None and view.slack_notes_to_forget() is None


def test_run_slack_a_backup_taken_before_a_removal_still_holds_what_came_through(world):  # noqa: F811
    """run.md: "A backup taken before Slack is removed still holds what came through it: its
    notes, for as long as the backup is kept, and both secrets, sealed, which the key still
    opens.\""""
    says(
        *RUN,
        "A backup taken before Slack is removed still holds what came through it: its notes,"
        " for as long as the backup is kept, and both secrets, sealed, which the key still"
        " opens.",
    )
    install = ready(world)
    note = a_note(world, "# Tides\n\nThe tide turns at nine.\n")
    world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)
    backup = world.db.with_name("backup.db")
    world.store._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy(world.db, backup)
    world.view.remove_slack_install(install.id)
    world.view.forget_slack_notes_now(install.id)
    assert world.view.get_note(note) is None
    copy = Store(backup)
    try:
        copy.slack_sealer = Sealer(KEY)
        assert copy.slack_inbound(install.id).signing_secret == SECRET
        assert "The tide turns at nine." in copy.for_account(world.account).get_note(note).text
    finally:
        copy.close()


def test_run_a_future_slack_setup_stamp_is_visible(world, caplog):  # noqa: F811
    """run.md: show the inherited time during credential saves, including after removal."""
    actual_time = world.clock.now
    world.clock.now += timedelta(hours=2)
    first = ready(world)
    world.clock.now = actual_time
    caplog.set_level(logging.WARNING, logger="cairn.slack")

    replacement = ready(world, token=TOKEN + "-new")
    first_warning = world.view.list_audit(action="slack.install_ready")[0]
    ahead = datetime.fromisoformat(replacement.made_at) - actual_time
    assert first_warning.detail.get("ahead_seconds") == ahead.total_seconds()
    repeated = ready(world, token=TOKEN + "-new")
    assert repeated.made_at == replacement.made_at
    world.view.remove_slack_install(replacement.id)
    reinstalled = ready(world, token=TOKEN + "-new")
    assert reinstalled.id == first.id
    assert first.made_at < replacement.made_at < reinstalled.made_at
    stamps = [reinstalled.made_at, repeated.made_at, replacement.made_at]
    seconds = [(datetime.fromisoformat(stamp) - actual_time).total_seconds() for stamp in stamps]
    trail = world.view.list_audit(action="slack.install_ready")
    assert [entry.detail.get("ahead_seconds") for entry in trail] == [*seconds, None]
    warnings = [record.getMessage() for record in caplog.records if record.name == "cairn.slack"]
    assert warnings == [
        f"slack: set-up stamp for {first.id} is {ahead:.6f} seconds ahead of the server clock"
        for ahead in reversed(seconds)
    ]
    says(
        *RUN,
        "If saving credentials carries forward a set-up stamp ahead of the server's clock,"
        " the server warns and `slack.install_ready` records how many seconds ahead it is in"
        " `ahead_seconds`.",
        "Removing the installation and setting it up again while the server's clock is still"
        " behind that stamp carries it forward.",
    )


# The connect guide, "From Slack": for a workspace's owner and its people

CONNECT = ("connect", "## From Slack")


def test_connect_the_table_points_at_the_section():
    """connect.md: "| Slack, once the operator switched it on | [From Slack](#from-slack) |\""""
    raw = guide_source("connect")
    table = raw[: raw.index("\n## ")]
    assert "| Slack, once the operator switched it on | [From Slack](#from-slack) |" in table


def test_connect_slack_is_there_only_with_the_switch(tmp_path):
    """connect.md: "Slack is there only when the operator started cairn with `serve --slack`.
    One Slack app serves one workspace, and an owner or an admin of the workspace sets it up
    under **Workspace**, then **Sources**:\""""
    says(
        *CONNECT,
        f"Slack is there only when the operator started {PRODUCT.name} with `serve --slack`."
        " One Slack app serves one workspace, and an owner or an admin of the workspace sets it"
        " up under **Workspace**, then **Sources**:",
    )
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("desk")
    try:
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            assert client.post("/login", data={"key": key}).status_code in (200, 303)
            for path in (setup.SETUP_PATH, link.LINK_PATH):
                assert client.post(path).status_code == 404, path
            assert "Slack" not in client.get("/sources").text
    finally:
        store.close()
    assert "Only an owner or an admin of this workspace can set Slack up" in setup.NOT_A_MANAGER
    assert setup.ANCHOR == "/sources#slack"


def test_connect_set_up_gives_the_link_the_manifest_and_what_each_permission_is_for():
    """connect.md: "1. Press **Set up Slack**. The page then gives a link that opens Slack's
    page for creating an app with the app's manifest filled in, and the same manifest as text
    to paste at Slack instead. It says what each of the app's four permissions is for.\""""
    says(
        *CONNECT,
        "1. Press **Set up Slack**. The page then gives a link that opens Slack's page for"
        " creating an app with the app's manifest filled in, and the same manifest as text to"
        " paste at Slack instead. It says what each of the app's four permissions is for.",
    )
    page = (files("cairn") / "templates" / "_slack.html").read_text(encoding="utf-8")
    assert ">Set up Slack</button>" in page
    assert "Create the app at Slack, with its manifest filled in" in page
    assert "Or create it from a manifest at Slack and paste this" in page
    made = manifest.build_manifest(public_url=BASE, install="sl_0123456789ab")
    assert manifest.manifest_link(made).startswith("https://api.slack.com/apps?new_app=1&")
    assert tuple(setup.PERMISSIONS) == manifest.SCOPES and len(manifest.SCOPES) == 4


def test_connect_the_two_secrets_are_copied_from_slack_and_kept_sealed():
    """connect.md: "2. At Slack, create the app and install it to your Slack workspace. Copy its
    signing secret, from the app's Basic Information, and its bot token, from its OAuth &
    Permissions page once it is installed, into the two fields, and press **Keep them**.
    3. cairn asks Slack whose token it is, and keeps both secrets sealed: neither is shown
    again. A token Slack does not accept, a Slack that does not answer, and a Slack workspace
    that another workspace on this server set up already are each refused in one sentence, and
    nothing is kept.\""""
    says(
        *CONNECT,
        "2. At Slack, create the app and install it to your Slack workspace. Copy its signing"
        " secret, from the app's Basic Information, and its bot token, from its OAuth &"
        " Permissions page once it is installed, into the two fields, and press **Keep them**.",
        f"3. {PRODUCT.name} asks Slack whose token it is, and keeps both secrets sealed: neither"
        " is shown again. A token Slack does not accept, a Slack that does not answer, and a"
        " Slack workspace that another workspace on this server set up already are each refused"
        " in one sentence, and nothing is kept.",
    )
    page = (files("cairn") / "templates" / "_slack.html").read_text(encoding="utf-8")
    assert "At Slack, in the app's Basic Information." in page
    assert "At Slack, in the app's OAuth &amp; Permissions, once it is installed" in page
    assert ">Keep them</button>" in page
    fields = re.findall(r'<input type="password"[^>]*>', page)
    assert len(fields) == 2 and not any("value=" in field for field in fields)
    assert "auth.test" in inspect.getsource(setup.setup_routes)
    for code, words in (
        ("not_accepted", "Slack did not accept this bot token"),
        ("unreachable", "Slack could not be reached"),
        ("taken", "Another workspace on this server has Slack set up for this Slack workspace"),
    ):
        assert setup.REFUSALS[code].startswith(words) and "nothing was kept" in setup.REFUSALS[code]


def test_connect_ready_says_the_signing_secret_is_not_proven_until_slack_verifies_the_address(
    world,  # noqa: F811
):
    """connect.md: "4. Slack is then ready: Slack accepted the bot token, but nothing proves
    the signing secret yet; only a request that Slack signed can. At Slack, have Slack verify
    the events address the page shows, on the page where Slack lists events. If Slack reports
    that the address did not answer, either Slack cannot reach this server or the signing
    secret you pasted is not this app's: check the address, then paste the secret again.
    Once Slack has verified the address, Slack is on: a request signed with this app's
    signing secret was verified, and the slash command, the message shortcut and direct
    messages to the bot are answered.\""""
    says(
        *CONNECT,
        "4. Slack is then ready: Slack accepted the bot token, but nothing proves the signing"
        " secret yet; only a request that Slack signed can. At Slack, have Slack verify the"
        " events address the page shows, on the page where Slack lists events. If Slack reports"
        " that the address did not answer, either Slack cannot reach this server or the signing"
        " secret you pasted is not this app's: check the address, then paste the secret again."
        " Once Slack has verified the address, Slack is on: a request signed with this app's"
        " signing secret was verified, and the slash command, the message shortcut and direct"
        " messages to the bot are answered.",
    )
    ready_says = setup.STATES["ready"]
    assert ready_says.startswith("Ready: Slack accepted the bot token.")
    assert "The signing secret is not proven yet" in ready_says
    assert "only a request that Slack signed can prove it" in ready_says
    assert "have Slack verify the events address below" in ready_says
    assert "If Slack reports that the address did not answer" in ready_says
    assert "either Slack cannot reach this server" in ready_says
    assert "the signing secret pasted here is not this app's" in ready_says
    assert "check the address, then paste the secret again" in ready_says
    assert "are answered" not in ready_says and "shortcut" not in ready_says
    assert "a request signed with this app's signing secret was verified" in setup.STATES["on"]
    assert "direct messages to the bot are answered" in setup.STATES["on"]
    for text in (ready_says, setup.STATES["on"]):
        assert not set(text) & set('&<>"')  # shown as it is, in the page
    assert ACCEPTING == ("ready", "on")
    # the state machine: ready becomes on only when Slack's signed challenge was verified
    install = ready(world)
    assert install.state == "ready"
    # a signing secret pasted again while ready is the one requests are verified with
    again = "9a" + "c0ffee11" * 3 + "b2c3d4"
    assert ready(world, secret=again).state == "ready"
    assert world.store.slack_inbound(install.id).signing_secret == again
    assert world.store.slack_install_verified(install.id).state == "on"
    made = manifest.build_manifest(public_url=BASE, install="sl_0123456789ab")
    events = made["settings"]["event_subscriptions"]["request_url"]
    assert events == f"{BASE}/integrations/slack/sl_0123456789ab/events"


def test_connect_the_page_says_the_state_switch_off_and_on():
    """connect.md: "The page says which state Slack is in and what is left to do: set up
    started, ready, on, off, or off because Slack revoked the bot's token. **Switch off** stops
    the bot and deletes nothing: the slash command and the shortcut are refused, and a direct
    message is not answered. **Switch on** needs nothing pasted, unless Slack revoked the bot's
    token: then the page asks for a new one, and keeps the signing secret. When the signing
    secret or the bot token is changed at Slack, paste both on the page again, whatever state
    Slack is in: a signing secret other than the one kept makes Slack ready again, until Slack
    verifies the events address.\""""
    says(
        *CONNECT,
        "When the signing secret or the bot token is changed at Slack, paste both on the page"
        " again, whatever state Slack is in: a signing secret other than the one kept makes"
        " Slack ready again, until Slack verifies the events address.",
        "The page says which state Slack is in and what is left to do: set up started, ready,"
        " on, off, or off because Slack revoked the bot's token.",
        "**Switch off** stops the bot and deletes nothing: the slash command and the shortcut"
        " are refused, and a direct message is not answered. **Switch on** needs nothing pasted,"
        " unless Slack revoked the bot's token: then the page asks for a new one, and keeps the"
        " signing secret.",
    )
    assert set(setup.STATES) == {"pending", "ready", "on", "off", "revoked", "removed"}
    assert setup.STATES["pending"].startswith("Set up started.")
    assert (
        "nothing is deleted" in setup.STATES["off"]
        and "nothing needs to be pasted" in (setup.STATES["off"])
    )
    assert "the signing secret is kept" in setup.STATES["revoked"]
    assert "off" not in ACCEPTING  # its commands and actions addresses are refused
    assert web_slack.OFF_EVENTS == ("app_uninstalled", "tokens_revoked")  # no message


def test_connect_secrets_pasted_again_are_of_the_same_slack_workspace(world):  # noqa: F811
    """connect.md: "Secrets pasted again must be of the same Slack workspace: to connect
    another Slack workspace, remove Slack first, and the first workspace's notes are then
    forgotten on their day, even once the other one is set up.\""""
    says(
        *CONNECT,
        "Secrets pasted again must be of the same Slack workspace: to connect another Slack"
        " workspace, remove Slack first, and the first workspace's notes are then forgotten on"
        " their day, even once the other one is set up.",
    )
    assert "remove Slack first" in setup.REFUSALS["other_team"]
    install = ready(world)
    note = a_note(world)
    world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)
    with pytest.raises(store_slack.NotItsTeam):
        ready(world, team="T0QUILL01")
    assert world.view.get_slack_install().team_id == TEAM
    world.view.remove_slack_install(install.id)
    ready(world, team="T0QUILL01")
    ready(world, team="T0QUILL01")  # its secrets pasted again
    world.clock.now += FORGET_AFTER
    assert world.store.slack_notes_due() == [(world.account, note)]


def test_connect_remove_deletes_the_secrets_and_links_and_forgets_10_days_later(world):  # noqa: F811
    """connect.md: "**Remove Slack**, once you type the workspace's name, deletes both secrets
    and every link between a Slack user and a person at once, and every note Slack created is
    forgotten 10 days later. Removing the app at Slack does the same. Until then the
    page says how many notes and on which day, and offers an owner **Forget them now**; setting
    Slack up again for the same Slack workspace before that day keeps them.\""""
    says(
        *CONNECT,
        "**Remove Slack**, once you type the workspace's name, deletes both secrets and every"
        " link between a Slack user and a person at once, and every note Slack created is"
        " forgotten 10 days later. Removing the app at Slack does the same. Until then the"
        " page says how many notes and on which day, and offers an owner **Forget them now**;"
        " setting Slack up again for the same Slack workspace before that day keeps them.",
    )
    assert timedelta(days=10) == FORGET_AFTER
    assert "Type the workspace's name to remove Slack" in setup.REFUSALS["confirm"]
    assert dispatch.EVENTS["app_uninstalled"] is web_slack._uninstalled
    assert "remove_slack_install" in inspect.getsource(web_slack._uninstalled)
    install = ready(world)
    world.view.link_slack_user(install.id, "U0ADA0001", world.owner)
    note = a_note(world)
    world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)
    world.view.remove_slack_install(install.id)
    assert world.store.slack_inbound(install.id).signing_secret is None
    assert world.view.slack_person(install.id, "U0ADA0001") is None
    due = world.view.slack_notes_to_forget()
    assert due.notes == 1 and due.at == world.clock.now + FORGET_AFTER
    ready(world)  # the same Slack workspace, set up again before the day
    assert world.view.slack_notes_to_forget() is None and world.view.get_note(note) is not None


def yes_and_code(bench, slack_user: str, user_id: str) -> str:
    """The Slack user asks for the address with `/cairn link`; the person `user_id` opens it
    signed in and says yes. The code they are shown."""
    (value,) = set(re.findall(r"link\?t=([A-Za-z0-9_-]+)", said(ask(bench, "link", slack_user))))
    return bench.store.for_account(bench.account, user_id).claim_slack_offer(value)


LINK_PAGE = (files("cairn") / "templates" / "slack_link.html").read_text(encoding="utf-8")
CODE_PAGE = (files("cairn") / "templates" / "slack_link_code.html").read_text(encoding="utf-8")


def test_connect_a_person_links_once_in_two_steps(bench):
    """connect.md: "Each person links their Slack user to themselves once, in two steps within
    10 minutes. The first time they use the bot, or with `/cairn link`, the bot answers them
    alone with an address that works once. Opened signed in to cairn, it shows the Slack
    user's id and the Slack workspace first, then the name that Slack user set themself, and
    asks for a yes; if someone sent you the address, close the page. The yes shows a code,
    once, and links nothing yet. The same Slack user then types `/cairn link <code>` in
    Slack, and is linked. From then on, what they do through the bot acts as them, with what
    they reach.\""""
    says(
        *CONNECT,
        "Each person links their Slack user to themselves once, in two steps within 10"
        f" minutes. The first time they use the bot, or with `/{PRODUCT.slug} link`, the bot"
        " answers them alone with an address that works once. Opened signed in to"
        f" {PRODUCT.name}, it shows the Slack user's id and the Slack workspace first, then the"
        " name that Slack user set themself, and asks for a yes; if someone sent you the"
        " address, close the page. The yes shows a code, once, and links nothing yet. The same"
        f" Slack user then types `/{PRODUCT.slug} link <code>` in Slack, and is linked. From"
        " then on, what they do through the bot acts as them, with what they reach.",
    )
    assert timedelta(minutes=10) == OFFER_TTL and f"/{PRODUCT.slug}" == COMMAND
    for text in ("find jars", "link"):
        assert f"{BASE}/integrations/slack/link?t=" in said(ask(bench, text))  # ephemeral
    assert "<h1>Link this Slack user to you?</h1>" in LINK_PAGE
    assert LINK_PAGE.index("{{ slack_user }}") < LINK_PAGE.index("{{ slack_name }}")
    assert LINK_PAGE.index("{{ team_domain }}") < LINK_PAGE.index("{{ slack_name }}")
    assert "a name this Slack user set themself" in LINK_PAGE
    assert "If someone sent you this address, close this page." in LINK_PAGE
    assert "with what you reach" in LINK_PAGE and "{{ code" not in LINK_PAGE
    assert "{{ command }} link {{ code }}" in CODE_PAGE
    code = yes_and_code(bench, ADA_SLACK, bench.ada)
    assert bench.owner.slack_person(bench.install.id, ADA_SLACK) is None  # nothing yet
    assert said(ask(bench, f"link {code}")) == said(blocks.linked("Ada"))
    assert bench.owner.slack_person(bench.install.id, ADA_SLACK).user_id == bench.ada


def test_connect_the_slash_commands_words(bench):
    """connect.md: the table of the command's words, and "The slash command is `/cairn`, unless
    the owner named it otherwise in the app's manifest:\""""
    says(
        *CONNECT,
        f"The slash command is `/{PRODUCT.slug}`, unless the owner named it otherwise in the"
        " app's manifest:",
        "| `find <words>` | Up to 5 claims that match, each with its page's title as a link to"
        " the claim, its project and the page's version, and *outside text* when a source wrote"
        " it; with more, how many there were and the address of the search page; or Nothing"
        " found. |",
        "| `note <text>` | Writes a note in your Slack project, with `via` `slack`, and links its"
        " page. |",
        "| `project` | Says which project is your Slack project and which projects you can write"
        " to. `project <name>` makes another of those your Slack project. |",
        "| `link` | Says who your Slack user is linked to; if it is linked to nobody, gives a"
        " new address to link it. `link <code>` links it with the code that address showed"
        " you. |",
        "| `unlink` | Undoes your link, and says to use `link` to link again. |",
        "| `help`, or no word | Lists what there is. |",
    )
    made = manifest.build_manifest(public_url=BASE, install="sl_0123456789ab", command="/notes")
    assert made["features"]["slash_commands"][0]["command"] == "/notes"
    assert people.FOUND_AT_ONCE == 5
    hit = {"project": "library", "page": "jars", "title": "Jars", "claim_id": "cl_jar0001",
           "text": "Row 4 holds the quince jars.", "version": 3}  # fmt: skip
    shown = said(blocks.found([hit] * 7, query="quince", base_url=BASE))
    assert shown.count(f"<{BASE}/b/library/p/jars#cl_jar0001|Jars>") == 5
    assert "Project: library · Version: 3" in shown
    assert "5 of 7 claims shown." in shown and f"{BASE}/search?q=quince" in shown  # with more
    outside = said(blocks.found([{**hit, "outside": True}], query="quince", base_url=BASE))
    assert "Version: 3 · outside text" in outside and "outside text" not in shown
    assert said(blocks.nothing_found("quince")).startswith("Nothing found")
    bench.link(ADA_SLACK, bench.ada, bench.library)
    written = said(ask(bench, "note Row 4 holds the quince jars."))
    assert written.startswith("Filed: ") and f"{BASE}/b/library/p/" in written
    assert [via for *_, via in bench.notes()] == ["slack"]
    assert said(ask(bench, "project")) == said(blocks.project_is("library", writable=["library"]))
    assert said(ask(bench, "link")) == said(blocks.linked("Ada"))
    unlinked = said(ask(bench, "unlink"))
    assert "You are unlinked. The bot no longer acts as you here." in unlinked
    assert "`/cairn link`" in unlinked
    assert "/integrations/slack/link?t=" not in unlinked
    assert bench.owner.slack_person(bench.install.id, ADA_SLACK) is None
    assert bench.store._con.execute("SELECT * FROM slack_link_offers").fetchall() == []
    assert f"{BASE}/integrations/slack/link?t=" in said(ask(bench, "link"))  # linked to nobody
    assert bench.store._con.execute("SELECT slack_user_id FROM slack_link_offers").fetchall() == [
        (ADA_SLACK,)
    ]
    for word in ("help", ""):
        assert said(ask(bench, word)) == said(blocks.help_text("/cairn"))


def test_connect_a_direct_message_works_the_same_way():
    """connect.md: "A direct message to the bot works the same way: plain words are a search,
    `note:` and text writes a note, and `help` lists what there is.\""""
    says(
        *CONNECT,
        "A direct message to the bot works the same way: plain words are a search, `note:` and"
        " text writes a note, and `help` lists what there is.",
    )
    assert messages.NOTE == "note:"
    source = inspect.getsource(messages._answer)
    assert "people.search" in source and "people.written" in source and '"help"' in source


def test_connect_the_message_shortcut_files_a_message_once():
    """connect.md: "The message shortcut **File in cairn**, used on a message, files it as a note
    in your Slack project: its text, a heading with who wrote it and when, and the message's
    own link as its `source_ref`. A message is filed once: filing it again answers with the
    note it already is.\""""
    says(
        *CONNECT,
        f"The message shortcut **File in {PRODUCT.name}**, used on a message, files it as a note"
        " in your Slack project: its text, a heading with who wrote it and when, and the"
        " message's own link as its `source_ref`. A message is filed once: filing it again"
        " answers with the note it already is.",
    )
    (shortcut,) = manifest.build_manifest(public_url=BASE, install="sl_0123456789ab")["features"][
        "shortcuts"
    ]
    assert shortcut["name"] == f"File in {PRODUCT.name}" and shortcut["type"] == "message"
    message = {"type": "message", "user": "U0CEDAR01", "ts": "1900000000.000100", "text": "Row 4."}
    draft = note_from_message(
        message, team_id=TEAM, team_domain="fable-works", conversation_id="C0LIBRARY1",
        author="Cedar Quill", names={},
    )  # fmt: skip
    assert draft.text.startswith("## Cedar Quill · 2030-03-17 17:46:40 UTC\n\nRow 4.")
    assert draft.source_ref == "https://fable-works.slack.com/archives/C0LIBRARY1/p1900000000000100"
    assert said(blocks.filed(title="T", page_url=BASE, project="p", already=True)).startswith(
        "Already filed"
    )


def test_connect_the_title_is_the_first_line_the_message_displays():
    """connect.md: "The note's title is the first line the message displays, or the first line
    of a code block it opens with, as text: nothing in it is code or a link, and it is cut to
    80 characters.\" (The third tester's findings 1, 2 and 4; every cut is
    test_slack_title_unit's.)"""
    says(
        *CONNECT,
        "The note's title is the first line the message displays, or the first line of a code"
        " block it opens with, as text: nothing in it is code or a link, and it is cut to 80"
        " characters.",
    )

    def title(text: str) -> str:
        return note_from_message(
            {"type": "message", "user": "U0CEDAR01", "ts": "1900000000.000100", "text": text},
            team_id=TEAM, team_domain="fable-works", conversation_id="C0LIBRARY1",
            author="Cedar Quill", names={},
        ).title  # fmt: skip

    assert title("```\nSELECT 1;\n```\nLedger.") == "SELECT 1;"
    shown = title("See `[manual](https://fable.test/m)` and <https://fable.test/l|the ledger>")
    heading = MarkdownIt("commonmark", {"html": False}).parse(f"# {shown}")[1].children
    assert [token.type for token in heading] == ["text"]
    assert heading[0].content == "See `[manual](https://fable.test/m)` and the ledger"
    assert len(title("Row " * 30)) <= 80 == TITLE_MAX
    # The tester's second attack found it untrue of a special mention, shown "[link omitted]",
    # and of asterisks Slack did not pair, shown doubled (findings 1 and 2).
    for sent, displayed in (
        ("<!here> standup at *ten*", "@here standup at ten"),
        ("2 * 3 * 4 is 24", "2 * 3 * 4 is 24"),
    ):
        heading = MarkdownIt("commonmark", {"html": False}).parse(f"# {title(sent)}")[1]
        assert "".join(token.content for token in heading.children) == displayed


def test_connect_the_reply_names_the_note_as_slack_displayed_it(tmp_path):
    """connect.md: "The reply in Slack names the note by its title as Slack displayed it, in
    plain text: ...\" (The third tester's finding 7; and the second attack's finding 3: the
    title was mrkdwn, so marks Slack displayed as characters, a code block's or a name's,
    came back styled.)"""
    says(
        *CONNECT,
        "The reply in Slack names the note by its title as Slack displayed it, in plain text:"
        " nothing of it is bold, a mention or a link there, and code within a line shows the"
        " backticks about it, as the title writes them.",
    )
    bench = Bench(tmp_path)
    try:
        bench.users[CEDAR] = cedar()
        bench.link(ADA_SLACK, bench.ada, bench.library)
        message = {"type": "message", "user": CEDAR, "ts": "1900000000.000100"}
        reply = replied(bench, handle(bench, {**message, "text": "Q&amp;A [notes] *now*"}))
        assert reply.startswith("Filed: Q&amp;A [notes] now\n")  # Slack shows "Q&A [notes] now"
        # The second reviewer's seventh report: Slack showed `status` in a code face, and the
        # reply its backticks (the third tester's model reads the title's line with them).
        status = {**message, "text": "`status` ok", "ts": "3.000001"}
        asyncio.run(handle(bench, status).background())
        named = [body for where, body in bench.calls if where == REPLY][-1]["blocks"][-1]
        assert named["text"] == {"type": "plain_text", "text": "`status` ok"}
        asyncio.run(
            handle(
                bench, {**message, "text": "```*draft* <!here>```", "ts": "2.000001"}
            ).background()
        )
        *words, named = [body for where, body in bench.calls if where == REPLY][-1]["blocks"]
        assert named["text"] == {"type": "plain_text", "text": "*draft* &lt;!here&gt;"}
        # The reply's own words, mrkdwn, name it not (its page's address holds its slug).
        assert all("*draft*" not in block["text"]["text"] for block in words)
    finally:
        bench.store.close()


def test_connect_the_page_shows_the_title_and_the_author_as_the_note_writes_them(tmp_path):
    """connect.md: "Its page shows that title, and the heading with the author's name, as the
    note writes them for Markdown: ...\" A limit (the third tester, finding 7): showing what Slack
    displayed there would take the web view reading every page's title and section headings
    as Markdown, whatever wrote them, which it does not."""
    says(
        *CONNECT,
        "Its page shows that title, and the heading with the author's name, as the note writes "
        "them for Markdown: where they hold a character Markdown reads, such as `[` or `&`, the"
        " backslash or entity that keeps it text shows there, and bold text shows its"
        " asterisks.",
    )
    bench = Bench(tmp_path)
    try:
        bench.users[CEDAR] = cedar(
            real_name="Ana & Bo [x]", profile={"real_name": "Ana & Bo [x]", "display_name": "A"}
        )
        bench.link(ADA_SLACK, bench.ada, bench.library)
        message = {"type": "message", "user": CEDAR, "ts": "1900000000.000100"}
        asyncio.run(handle(bench, {**message, "text": "Q&amp;A [notes] *now*"}).background())
        (listed,) = bench.owner.list_pages(bench.library.id)
        assert listed.title == "Q&amp;A \\[notes\\] **now**"
        session = bench.store.sign_in(
            "sso", "sub-ada", "ada@example.test", "Ada", issuer="https://sso.example.test",
            join="personal",
        )  # fmt: skip
        app = create_app(bench.store, public_url=BASE, clock=bench.clock)
        with TestClient(app, base_url=BASE) as client:
            client.cookies.set(session_cookie("https"), session.token)
            html = client.get(f"/b/library/p/{listed.slug}").text
        assert "Q&amp;amp;A \\[notes\\] **now**" in html and "Ana &amp;amp; Bo \\[x\\]" in html
    finally:
        bench.store.close()


def test_connect_a_mention_of_anyone_but_the_author_is_their_member_id(tmp_path):
    """connect.md: "In the note a mention of the message's author is their name, and a mention
    of anyone else is their member ID: ...\" (The third tester's finding 8: the guide said
    every mention was the person's name.)"""
    says(
        *CONNECT,
        "In the note a mention of the message's author is their name, and a mention of anyone "
        "else is their member ID: the shortcut asks Slack for the author's name alone.",
    )
    bench = Bench(tmp_path)
    try:
        bench.users[CEDAR] = cedar()
        bench.link(ADA_SLACK, bench.ada, bench.library)
        text = f"<@{CEDAR}> asks <@U0BIRCH01> on Monday"
        message = {"type": "message", "user": CEDAR, "ts": "1900000000.000100", "text": text}
        asyncio.run(handle(bench, message).background())
        ((_, _, note, _),) = bench.notes()
        assert "\n\n@Cedar Quill asks @U0BIRCH01 on Monday\n\n" in note
        assert [where for where, _ in bench.calls if where != "users.info"] == [REPLY]
        assert [where for where, _ in bench.calls].count("users.info") == 1
    finally:
        bench.store.close()


def test_connect_a_special_mention_is_written_as_slack_displays_it():
    """connect.md: "Slack's special mentions are written as Slack displays them, ...\" (The
    tester's second attack, finding 1: they were written "[link omitted]"; every form is
    test_slack_special_mentions_unit's.)"""
    says(
        *CONNECT,
        "Slack's special mentions are written as Slack displays them, `@here`, `@channel` and "
        "`@everyone`, and a mention of a user group or a date as the fallback text the message "
        "gives for it, else as `[user group]` or `[date]`: none of them is a link.",
    )
    lines, _, _ = as_read(
        a_filed_note(
            "<!here> <!channel> <!everyone> <!subteam^S0DESIGN1|@designers>"
            " <!date^1900000000^{date}^https://fable.test/cal|Mar 17>"
            " <!subteam^S0DESIGN1> <!date^1900000000^{date}>"
        )
    )
    assert lines == "@here @channel @everyone @designers Mar 17 \\[user group\\] \\[date\\]"


def test_connect_a_line_read_as_code_in_its_passage_alone_shows_what_writes_it_as_text():
    """connect.md: "A page shows each passage of the note alone, and a line the note reads as
    text can read as code in its passage alone: ...\" A limit (the third tester, finding 5):
    the note cannot write such a line both ways, and the note whole is what the readers agree
    on."""
    says(
        *CONNECT,
        "A page shows each passage of the note alone, and a line the note reads as text can read "
        "as code in its passage alone, a line indented with a tab under a quotation for instance: "
        "there it shows the backslashes and entities that write it as text, and not the blanks "
        "that end it; and it shows the rest of the Markdown that writes it too, the doubled"
        " asterisks of bold, the backticks about code within a line, a link as"
        " `[its words](its address)`.",
    )
    for sent, code in (  # the last three: the tester's second attack, finding 5
        ("&gt; Readings\n\t[x] &amp; y", "\\[x\\] &amp; y\n"),
        ("&gt; Readings\n\tnorth\tblue\t", "north\tblue\n"),
        ("&gt; Readings\n\t*north* blue", "**north** blue\n"),
        ("&gt; Readings\n\t`port` blue", "`port` blue\n"),
        (
            "&gt; Readings\n\t<https://fable.test/r|rows> blue",
            "[rows](https://fable.test/r) blue\n",
        ),
    ):
        note = a_filed_note(sent)
        assert as_read(note)[2] == []  # text to the note whole
        alone = [span.text for span in chunk("", note)][2]
        assert [(t.type, t.content) for t in MarkdownIt("commonmark").parse(alone)] == [
            ("code_block", code)
        ]


def a_filed_note(text: str) -> str:
    return note_from_message(
        {"type": "message", "user": "U0CEDAR01", "ts": "1900000000.000100", "text": text},
        team_id=TEAM, team_domain="fable-works", conversation_id="C0LIBRARY1",
        author="Cedar Quill", names={"U0QUILL01": "Brook Quill"},
    ).text  # fmt: skip


def as_read(note: str) -> tuple[str, list[int], list[tuple[str, str]]]:
    """The lines `note` holds between the author's heading and the link, the lines either
    reader takes for a heading, and the rules and code the Markdown parser reads."""
    tokens = MarkdownIt("commonmark", {"html": False}).enable("table").parse(note)
    headings = {span.start_line - 1 for span in chunk("", note) if span.kind == "heading"}
    headings |= {token.map[0] for token in tokens if token.type == "heading_open"}
    marked = [(t.type, t.content) for t in tokens if t.type in ("hr", "fence", "code_block")]
    lines = note.split("UTC\n\n", 1)[1].split("\n\n[Source in Slack]")[0]
    return lines, sorted(headings), marked


def test_connect_a_line_that_would_read_as_a_heading_gets_a_backslash():
    """connect.md: "The note keeps that one heading: where the message has a line that would
    read as another, in the note or in a passage of it shown alone, a backslash is written
    before the `#` that starts it, or before the first `=` or `-` of the line under it that
    makes it one, and the line reads as text.\""""
    says(
        *CONNECT,
        "The note keeps that one heading: where the message has a line that would read as"
        " another, in the note or in a passage of it shown alone, a backslash is written before"
        " the `#` that starts it, or before the first `=` or `-` of the line under it that makes"
        " it one, and the line reads as text.",
    )
    # In a passage shown alone: the dashes are a rule to the note, which reads the line above
    # them into the quotation, and underline that line where its passage stands alone.
    note = a_filed_note("&gt; Row 4.\nRow 5.\n---")
    assert as_read(note) == ("> Row 4.\nRow 5.\n\\---", [0], [])
    alone = [span.text for span in chunk("", note.replace("\\---", "---"))][2]
    assert alone == "Row 5.\n---"
    assert MarkdownIt("commonmark", {"html": False}).parse(alone)[0].type == "heading_open"
    for text, lines in (
        ("## Ada Park\nRow 4.", "\\## Ada Park\nRow 4."),
        ("Ada Park\n===\nRow 4.", "Ada Park\n\\===\nRow 4."),
        ("Ada Park\n---\nRow 4.", "Ada Park\n\\---\nRow 4."),
    ):
        note = a_filed_note(text)
        assert as_read(note) == (lines, [0], [])
        assert len(as_read(note.replace(lines, text))[1]) == 2  # the heading it would be


def test_connect_a_rule_gets_no_backslash():
    """connect.md: "A line that would not read as a heading gets none, so a rule stays a
    rule.\""""
    says(*CONNECT, "A line that would not read as a heading gets none, so a rule stays a rule.")
    text = "# Settings\n\n---\n\n```\n---\n```\n\n    ---\n\nRow 4."
    assert as_read(a_filed_note(text)) == (
        "\\" + text,
        [0],
        [("hr", ""), ("fence", "---\n"), ("code_block", "---\n")],
    )


def test_connect_a_line_read_as_code_holds_what_slack_displayed():
    """connect.md: "A line holds what Slack displayed where both the note and its passage read
    it as indented code: none of the backslashes and doubled asterisks that keep the other
    lines from reading as Markdown are written in it, while a mention is written as in the rest
    of the note, a link is still `[its words](its address)`, and an email address is still
    removed.\" The passage is the line's own, as the note is split into them: what a page
    shows of a claim. (It said "fenced or indented" until the third tester's finding 3: a
    fence is now a code block of Slack's own, the sentence on those.)"""
    says(
        *CONNECT,
        "A line holds what Slack displayed where both the note and its passage read it as"
        " indented code: none of the backslashes and doubled asterisks that keep the other"
        " lines from reading as Markdown are written in it, while a mention is written as in"
        " the rest of the note, a link is still `[its words](its address)`, and an email"
        " address is still removed.",
    )
    # Slack's bold, `*now*`, gives the prose its doubled asterisks; the asterisks of `* 2 *`
    # Slack shows as characters, and the prose writes them so (the second attack, finding 2).
    sent = (
        "items[0] = (a) * 2 * 3 *now* <@U0QUILL01> <https://fable.test/a_b|Issue #4>"
        " quill@fable.test"
    )
    shown = (
        "items[0] = (a) * 2 * 3 *now* @Brook Quill [Issue #4](https://fable.test/a_b)"
        " [email removed]"
    )
    prose = (
        "items\\[0\\] = \\(a\\) * 2 * 3 **now** @Brook Quill [Issue \\#4](https://fable.test/a_b)"
        " \\[email removed\\]"
    )
    assert as_read(a_filed_note(f"Settings:\n\n    {sent}")) == (
        f"Settings:\n\n    {shown}",
        [0],
        [("code_block", shown + "\n")],
    )
    assert as_read(a_filed_note(f"Settings:\n    {sent}")) == (f"Settings:\n    {prose}", [0], [])
    # Code to its passage alone: behind a tab the note reads the line into the quotation; the
    # line is a passage of its own, and alone the parser reads it as indented code. Prose.
    note = a_filed_note(f"&gt; Row 4.\n\t{sent}")
    assert as_read(note) == (f"> Row 4.\n\t{prose}", [0], [])
    passage = [span.text for span in chunk("", note)][2]
    assert MarkdownIt("commonmark").parse(passage)[0].type == "code_block"


def test_connect_the_two_limits_of_what_a_filed_message_holds():
    """connect.md: "Only a message with eight or more lines that read as headings or could
    underline one can get a backslash it does not need, on a rule below its first heading for
    instance; and only a message in which a line would open or end a code fence as Slack
    displayed it, and not as prose, keeps the marks of prose where both read code.\" That no
    other message does either is test_slack_needed_backslashes_unit's, on generated messages."""
    says(
        *CONNECT,
        "Only a message with eight or more lines that read as headings or could underline one"
        " can get a backslash it does not need, on a rule below its first heading for instance;"
        " and only a message in which a line would open or end a code fence as Slack displayed"
        " it, and not as prose, keeps the marks of prose where both read code.",
    )
    seven = "".join(f"# Shelf {n}\n" for n in range(1, 8))
    below = "\n---\n\n    items[0]\n\nRow 4."
    as_text = seven.replace("#", "\\#")
    code = [("code_block", "items[0]\n")]
    assert as_read(a_filed_note(seven + below)) == (as_text + below, [0], [("hr", ""), *code])
    note = a_filed_note(seven + "# Shelf 8\n" + below)
    assert as_read(note) == (as_text + "\\# Shelf 8\n\n\\---\n\n    items[0]\n\nRow 4.", [0], code)
    assert as_read(note.replace("\\---", "---"))[1:] == ([0], [("hr", ""), *code])

    # A link's words: "~~~" as displayed, with backslashes as prose. In a list item's indented
    # code the passages read it as a fence the item keeps whole, so as displayed they would
    # read the rest of the note as code. (The example was a fence of tildes the message opened
    # until the third tester's finding 3.)
    opens = "- Row 4.\n\n      <nowhere|~~~>\n      items[0]\n\nRow 5."
    assert as_read(a_filed_note(opens)) == (
        "- Row 4.\n\n      \\~\\~\\~\n      items\\[0\\]\n\nRow 5.",
        [0],
        [("code_block", "\\~\\~\\~\nitems\\[0\\]\n")],
    )
    assert as_read(a_filed_note(opens.replace("<nowhere|~~~>", "rows"))) == (
        "- Row 4.\n\n      rows\n      items[0]\n\nRow 5.",
        [0],
        [("code_block", "rows\nitems[0]\n")],
    )


def test_connect_a_code_block_of_slacks_own_is_a_fence_on_lines_of_its_own():
    """connect.md: "A code block of Slack's own is a fence on lines of its own in the note,
    whatever stood beside it on its lines in the message, and holds what Slack displayed, the
    white space that ends a line included, but that a code block in a quotation is written
    without the quotation's mark that starts each of its lines, and only that: ...\" The
    exception is the tester's second attack, finding 4, a limit; its last finding on SL8 was
    the `>` after a block's opening backticks taken for that mark (`&gt; ```&gt; x```` held
    `x`), and it is the block's text."""
    says(
        *CONNECT,
        "A code block of Slack's own is a fence on lines of its own in the note, whatever stood"
        " beside it on its lines in the message, and holds what Slack displayed, the white space"
        " that ends a line included, but that a code block in a quotation is written without"
        " the quotation's mark that starts each of its lines, and only that: what stands after"
        " its opening backticks is its text, a `>` included, and of each later line only a"
        " `>` that starts it, with the blanks before it and one after, is the quotation's,"
        " Slack's page saying nothing of how those lines are marked; that white space is kept"
        " at the end of the message too, where the last line is code.",
    )
    for sent, block in (
        ("&gt; ```&gt; x```", ["> x"]),
        ("&gt; ```\n&gt; x\n&gt; y```", ["x", "y"]),
        ("&gt; ```&gt; x\n&gt; &gt; y```", ["> x", "> y"]),
    ):
        lines = as_read(a_filed_note(sent))[0].split("\n")
        assert lines[1:] == ["```", *block, "```"], sent
    row = "[manual](https://fable.test/docs) a &lt; b\t"
    shown = "[manual](https://fable.test/docs) a < b\t"
    for sent, before in (
        (f"Rows: ```{row}``` and so on", "Rows: "),  # the blank before the block ends its line
        (f"Rows:\n   ```\n{row}\n   ```\nand so on", "Rows:"),
    ):
        note = a_filed_note(sent)
        assert as_read(note) == (
            f"{before}\n```\n{shown}\n```\nand so on",
            [0],
            [("fence", shown + "\n")],
        )
        assert [(span.kind, span.text) for span in chunk("", note)][2] == (
            "code",
            f"```\n{shown}\n```",
        )
    assert as_read(a_filed_note("Rows:\n\n    5\tplum\t \n"))[0] == "Rows:\n\n    5\tplum\t "
    assert as_read(a_filed_note("Rows:\n5\tplum\t \n"))[0] == "Rows:\n5\tplum"


def test_connect_a_line_of_tildes_is_text():
    """connect.md: "Slack has no fence of tildes: where a line of the message would open one, a
    backslash before its first tilde keeps it text, as Slack displayed it.\" It replaced the
    sentence on a block that could not be one fence because a fence of tildes stood about it
    (the third tester's finding 3); every place a line of tildes can stand is
    test_slack_tildes_unit's."""
    says(
        *CONNECT,
        "Slack has no fence of tildes: where a line of the message would open one, a backslash"
        " before its first tilde keeps it text, as Slack displayed it.",
    )
    for sent, held in (
        ("Steps\n~~~\nsee the ledger", "Steps\n\\~~~\nsee the ledger"),
        ("1. ~~~ python\n   rows", "1. \\~~~ python\n   rows"),
        ("&gt; ~~~~\nsee the ledger", "> \\~~~~\nsee the ledger"),
    ):
        note = a_filed_note(sent)
        assert as_read(note) == (held, [0], [])
        assert "~~~" in str(render(note)) and "\\~" not in str(render(note))
    # In a code block of Slack's own, a line of tildes is the code's and stays as it is.
    manual = "[manual](https://fable.test/docs)"
    assert as_read(a_filed_note(f"~~~\n```\n~~~\n{manual}\n```\nRow 4.")) == (
        f"\\~~~\n```\n~~~\n{manual}\n```\nRow 4.",
        [0],
        [("fence", f"~~~\n{manual}\n")],
    )


def test_connect_emphasis_is_written_only_where_slack_shows_it():
    """connect.md: "Bold, italic and struck text are Slack's only where its marks make them:
    ...\" (The tester's second attack, finding 2: every `*x*` was doubled, so `2 * 3 * 4` was
    `2 ** 3 ** 4`; each case is test_slack_emphasis_unit's. Trying the fix found the last
    three: a span a page read otherwise showed its asterisks doubled, a `*` after a list's
    mark began a list, and a line's start was taken to be where the message's text resumed
    after a mention or a link's words.)"""
    says(
        *CONNECT,
        "Bold, italic and struck text are Slack's only where its marks make them: a `*`, `_` or"
        " `~` with neither a blank nor a mark after it and neither a letter nor a digit before"
        " it, up to the next same mark on its line with no blank before it and neither a letter"
        " nor a digit after it, and no link, mention or code between them; Slack's page does not"
        f" say where they open and close, and this is {PRODUCT.name}'s reading of it."
        " There bold is written `**bold**` and italic `_italic_`, but where a page would not"
        " read them so, as where a mark has punctuation on its inner side and a letter or a"
        " digit on its outer one, `*(b)*` right before a date's words for instance, its marks"
        " are typed. Every other `*` and `_` is the character typed, with a backslash before"
        " it unless it is a `*` between two blanks past the blanks and the marks of a"
        " quotation or a list that start its line in the note, or a `_` between two letters or"
        " digits, so `2 * 3 * 4` and `snake_case` are written as typed.",
    )
    for sent, written in (
        ("*bold* _italic_ ~struck~", "**bold** _italic_ ~struck~"),
        ("2 * 3 * 4 and snake_case", "2 * 3 * 4 and snake_case"),
        ("* item a*b*c *open _x", "\\* item a\\*b\\*c \\*open \\_x"),
        ("*a\nb* *c *d*", "\\*a\nb\\* **c \\*d**"),
        ("*see <https://fable.test/x|docs>*", "\\*see [docs](https://fable.test/x)\\*"),
        ("*(b)*<!date^1900000000^{date}|17 Mar>", "\\*\\(b\\)\\*17 Mar"),
        ("&gt; * a and 2 * 3\n- * b", "> \\* a and 2 * 3\n- \\* b"),
        ("<@U0BIRCH01> * 3\n<x|12>. * 4", "@U0BIRCH01 * 3\n12. \\* 4"),
    ):
        assert as_read(a_filed_note(sent))[0] == written, sent


def test_connect_struck_text_is_written_as_typed():
    """connect.md: "Struck text is written as it was typed, `~done~`: a page cannot strike, so
    it shows the tildes, one on each side.\" (The third tester's finding 6: it was doubled.)"""
    says(
        *CONNECT,
        "Struck text is written as it was typed, `~done~`: a page cannot strike, so it shows the"
        " tildes, one on each side.",
    )
    note = a_filed_note("Ledger ~done~ and ~kept~")
    assert as_read(note) == ("Ledger ~done~ and ~kept~", [0], [])
    page = str(render(note))
    assert "Ledger ~done~ and ~kept~" in page and "<s>" not in page and "<del>" not in page


def test_connect_code_within_a_line_stays_between_its_backticks():
    """connect.md: "Code within a line stays between its backticks, as many as its own code
    needs, ...\" (The second reviewer's seventh report, must-fix 3: one span's run set every
    span's; each case is test_slack_code_spans_unit's.)"""
    says(
        *CONNECT,
        "Code within a line stays between its backticks, as many as its own code needs, and a"
        " backtick that Slack paired with no other gets a backslash; where Slack's rich text"
        " names a span right after another, that span's code is written as plain characters,"
        " since the backticks of the two would make one run;",
    )
    says(
        *CONNECT,
        "in a message that could hold a table, code within a line that holds a `|` is written as"
        " plain characters, backticks and all, since a table's row is cut at its bars before its"
        " code is read.",
    )
    code = {"type": "text", "style": {"code": True}}
    section = {"type": "rich_text_section", "elements": [
        {**code, "text": "a``b"}, {"type": "text", "text": " and "}, {**code, "text": "c"},
        {**code, "text": "d*"},
    ]}  # fmt: skip
    note = note_from_message(
        {"type": "message", "user": "U0CEDAR01", "ts": "1900000000.000100",
         "text": "`a``b` and `c``d*`", "blocks": [{"type": "rich_text", "elements": [section]}]},
        team_id=TEAM, team_domain="fable-works", conversation_id="C0LIBRARY1",
        author="Cedar Quill", names={},
    ).text  # fmt: skip
    assert as_read(note)[0] == "```a``b``` and `c`d\\*"
    code = "`[manual](https://fable.test/docs)|Row 4`"
    assert as_read(a_filed_note(f"Row 4 `\nRow 5 {code}")) == (
        f"Row 4 \\`\nRow 5 {code}",
        [0],
        [],
    )
    plain = "\\`\\[manual\\]\\(https://fable.test/docs\\)|Row 4\\`"
    held, headings, _ = as_read(a_filed_note(f"| Row |\n|---|\n| {code} |"))
    assert (held, headings) == (f"| Row |\n|---|\n| {plain} |", [0])
    parsed = MarkdownIt("commonmark", {"html": False}).enable("table").parse(held)
    assert [token.type for token in parsed].count("table_open") == 1
    assert not [t for block in parsed for t in block.children or [] if t.type == "link_open"]


def test_connect_two_code_spans_that_touch_in_plain_text_are_read_as_one():
    """connect.md: "in a message sent as plain text, two code spans that touch with nothing
    between them, ... are read as one span, since the text alone does not say where one ends;
    rich text, which names its spans, is read span by span;" A limit, ruled on SL8 and left so
    plain-text messages give what they gave."""
    says(
        *CONNECT,
        "in a message sent as plain text, two code spans that touch with nothing between them,"
        " ``` `a``b` ```, are read as one span, since the text alone does not say where one"
        " ends; rich text, which names its spans, is read span by span;",
    )

    def read(note: str) -> list[tuple[str, str]]:
        body = as_read(note)[0]
        children = [t for b in MarkdownIt("commonmark").parse(body) for t in b.children or []]
        return [(t.type, t.content) for t in children if t.type in ("code_inline", "text")]

    assert read(a_filed_note("`a``b`")) == [("code_inline", "a``b")]
    code = {"type": "text", "style": {"code": True}}
    section = {
        "type": "rich_text_section",
        "elements": [{**code, "text": "a"}, {**code, "text": "b"}],
    }
    rich = note_from_message(
        {"type": "message", "user": "U0CEDAR01", "ts": "1900000000.000100", "text": "`a``b`",
         "blocks": [{"type": "rich_text", "elements": [section]}]},
        team_id=TEAM, team_domain="fable-works", conversation_id="C0LIBRARY1",
        author="Cedar Quill", names={},
    ).text  # fmt: skip
    assert read(rich) == [("code_inline", "a"), ("text", "b")]  # the second its plain characters


def test_connect_the_rich_text_tells_the_blanks_and_the_text_about_a_span():
    """connect.md: "A span written as plain characters keeps the blanks that end it, ...\" and
    "Where Slack's rich text gives the text that stands right before a span it names, ...\"
    (The Slack tester's second attack on spans, cases 5 and 2, each in
    test_rt3_slack_spans_found; the limit is run here.)"""
    says(
        *CONNECT,
        "A span written as plain characters keeps the blanks that end it, at the end of a line"
        " too: the last of them is written as a character reference, `&#32;` for a space and"
        " `&#160;` for a no-break space.",
        "Where Slack's rich text gives the text that stands right before a span it names, a"
        " backtick in that text is written as a character, and the span after it, unless an"
        " element of the rich text styled as code that is not text, such as an emoji, stands"
        " before the span on its line: the span can then be read on that element's backticks,"
        " and the page shows backticks Slack did not display; elsewhere a backtick Slack"
        " displayed as a character is read as code where it pairs with another, after the last"
        " span the rich text names for instance.",
    )

    def read(text: str, elements: list[dict]) -> tuple[str, list[tuple[str, str]]]:
        section = {"type": "rich_text_section", "elements": elements}
        note = note_from_message(
            {"type": "message", "user": "U0CEDAR01", "ts": "1900000000.000100", "text": text,
             "blocks": [{"type": "rich_text", "elements": [section]}]},
            team_id=TEAM, team_domain="fable-works", conversation_id="C0LIBRARY1",
            author="Cedar Quill", names={},
        ).text  # fmt: skip
        body = as_read(note)[0]
        children = [t for b in MarkdownIt("commonmark").parse(body) for t in b.children or []]
        return body, [(t.type, t.content) for t in children if t.type in ("code_inline", "text")]

    def code(text: str) -> dict:
        return {"type": "text", "text": text, "style": {"code": True}}

    def text(text: str) -> dict:
        return {"type": "text", "text": text}

    assert read("`a``  `", [code("a"), code("  ")]) == (
        "`a` &#32;",
        [("code_inline", "a"), ("text", "  ")],
    )
    assert read("`a``x\N{NO-BREAK SPACE}`", [code("a"), code("x\N{NO-BREAK SPACE}")]) == (
        "`a`x&#160;",
        [("code_inline", "a"), ("text", "x\N{NO-BREAK SPACE}")],
    )
    assert read("`x` or `x`", [text("`x` or "), code("x")]) == (
        "\\`x\\` or `x`",
        [("text", "`x` or "), ("code_inline", "x")],
    )
    emoji = {"type": "emoji", "name": "x", "style": {"code": True}}  # the exception
    assert read("`:x:`y`y`", [emoji, text("y"), code("y")])[1] == [
        ("text", "`:x:"),
        ("code_inline", "y"),
        ("text", "y`"),
    ]
    assert read("`x` or `x`", [code("x"), text(" or `x`")])[1] == [
        ("code_inline", "x"),
        ("text", " or "),
        ("code_inline", "x"),
    ]


def test_connect_every_answer_is_for_you_alone():
    """connect.md: "Every answer is for you alone: the slash command's and the shortcut's are
    shown only to you, and the bot answers a direct message in that direct conversation.\""""
    says(
        *CONNECT,
        "Every answer is for you alone: the slash command's and the shortcut's are shown only to"
        " you, and the bot answers a direct message in that direct conversation.",
    )
    assert '"response_type": "ephemeral"' in inspect.getsource(commands._answer)
    assert inspect.signature(SlackClient.reply).parameters["private"].default is True
    assert "private=" not in inspect.getsource(people)  # every reply private
    assert 'event["channel"]' in inspect.getsource(messages.direct_message)


def test_connect_each_refusal_says_why(bench):
    """connect.md: "When the bot refuses, it says why, to you alone, and stores nothing:" and
    its list."""
    name = PRODUCT.name
    says(
        *CONNECT,
        "When the bot refuses, it says why, to you alone, and stores nothing:",
        "- no project is chosen for your notes, or yours is out of your reach or gone: choose"
        " one with `project <name>`;",
        "- the note guard refused the note, and the answer names its rule;",
        "- the workspace is over its cap, or the page is full;",
        "- the message's author is not of this Slack workspace: only a message written there is"
        " filed;",
        "- an app, a bot or a deactivated account wrote the message, so it has no person as its"
        " author;",
        f"- Slack did not say in time who wrote the message, or is limiting how often {name}"
        " asks: try again;",
        "- the message has no text;",
        f"- {name} is compiling as many notes as it can at once: try again in a moment.",
    )
    assert "No project is chosen" in people.NO_PROJECT and "project <name>" in people.NO_PROJECT
    assert "out of your reach or no longer exists" in people.OUT_OF_REACH
    assert "is not of this Slack workspace" in people.OTHER_ORGANISATION
    assert "an app, a bot or a deactivated account" in people.NO_PERSON
    assert "did not say in time" in people.NOT_IN_TIME and "Try again" in people.NOT_IN_TIME
    assert "limiting how often cairn asks" in people.RATE_LIMITED
    assert "no text to file" in people.NO_TEXT
    assert "compiling as many notes as it can at once" in people.BUSY
    bench.link(ADA_SLACK, bench.ada, bench.library)
    leaked = "ghp_" + "Zq7xR2" * 6  # a made-up token in a token's shape
    refused = said(ask(bench, f"note The deploy token is {leaked} today."))
    assert refused.startswith("Cannot do that:") and RULE in refused and leaked not in refused
    assert bench.notes() == []


def test_connect_what_the_bot_cannot_do_in_this_version():
    """connect.md: "In this version the bot reads no thread and no whole conversation, does not
    answer a mention, and cannot share an answer with a conversation.\""""
    says(
        *CONNECT,
        "In this version the bot reads no thread and no whole conversation, does not answer a"
        " mention, and cannot share an answer with a conversation.",
    )
    assert manifest.SCOPES == ("chat:write", "commands", "im:history", "users:read")
    assert manifest.BOT_EVENTS == ("app_uninstalled", "message.im", "tokens_revoked")
    assert "app_mention" not in dispatch.EVENTS
    calls = {name for name, _ in inspect.getmembers(SlackClient) if not name.startswith("_")}
    assert calls == {"auth_test", "call", "post", "reply", "user"}
    assert "in_channel" not in inspect.getsource(commands) + inspect.getsource(people)


# The privacy guide, "Slack"

PRIVACY = ("privacy", "## Slack")


def test_privacy_slack_sends_only_what_a_person_hands_over():
    """privacy.md: "Only when the operator starts cairn with `serve --slack` and an owner or an
    admin of the workspace sets up its Slack app. Slack then sends cairn only what a person
    hands it, when they hand it: the words they type after the slash command, the one message
    they file with the message shortcut, and the direct messages people send the bot. Nothing is
    read from a conversation among people: the app has no permission that would let it, and
    Slack tells the bot of no message but those sent to it directly.\""""
    name = PRODUCT.name
    says(
        *PRIVACY,
        f"Only when the operator starts {name} with `serve --slack` and an owner or an admin of"
        f" the workspace sets up its Slack app. Slack then sends {name} only what a person hands"
        " it, when they hand it: the words they type after the slash command, the one message"
        " they file with the message shortcut, and the direct messages people send the bot.",
        "Nothing is read from a conversation among people: the app has no permission that would"
        " let it, and Slack tells the bot of no message but those sent to it directly.",
    )
    assert [scope for scope in manifest.SCOPES if "history" in scope] == ["im:history"]
    assert [event for event in manifest.BOT_EVENTS if event.startswith("message")] == ["message.im"]
    assert '"im"' in inspect.getsource(messages._acted_on)


def test_privacy_what_cairn_keeps_of_slack(world):  # noqa: F811
    """privacy.md: "cairn keeps the note, as any note, with who brought it and that it came
    through Slack; for a filed message, also its conversation's id and its time, so that it is
    filed once. It keeps each link between a Slack user and a person, with the project their
    notes go to, and the install's Slack workspace, with its signing secret and bot token
    sealed under the operator's key.\""""
    says(
        *PRIVACY,
        f"{PRODUCT.name} keeps the note, as any note, with who brought it and that it came"
        " through Slack; for a filed message, also its conversation's id and its time, so that"
        " it is filed once. It keeps each link between a Slack user and a person, with the"
        " project their notes go to, and the install's Slack workspace, with its signing secret"
        " and bot token sealed under the operator's key.",
    )

    def columns(table: str) -> set[str]:
        return {row[1] for row in world.store._con.execute(f"PRAGMA table_info({table})")}

    assert {"install_id", "how", "brought_by", "conversation_id", "message_time"} <= columns(
        "slack_notes"
    )
    assert {"install_id", "slack_user_id", "user_id", "project_id"} <= columns("slack_people")
    assert {"team_id", "team_domain", "signing_secret", "bot_token"} <= columns("slack_installs")
    install = ready(world)
    note = a_note(world)
    kept = world.view.record_slack_note(
        install.id, note, how="filed", brought_by=world.owner, conversation_id="C0TIDES01",
        message_time="1900000000.000100",
    )  # fmt: skip
    other = a_note(world, "# Moon\n\nThe moon is full.\n")
    again = world.view.record_slack_note(
        install.id, other, how="filed", brought_by=world.owner, conversation_id="C0TIDES01",
        message_time="1900000000.000100",
    )  # fmt: skip
    assert kept == note and again == note  # the same message: the note it already is


def test_privacy_only_an_author_of_the_same_slack_workspace_is_filed(bench):
    """privacy.md: "Only a message whose author Slack shows to be of the same Slack workspace is
    filed: cairn asks Slack who wrote it, and files nothing when the author is of another
    organisation, is a stranger to the workspace, is an app or a bot, or when Slack does not
    say.\""""
    says(
        *PRIVACY,
        "Only a message whose author Slack shows to be of the same Slack workspace is filed:"
        f" {PRODUCT.name} asks Slack who wrote it, and files nothing when the author is of"
        " another organisation, is a stranger to the workspace, is an app or a bot, or when"
        " Slack does not say.",
    )
    base = {"team_id": TEAM, "real_name": "Cedar Quill", "is_bot": False}
    cases = {
        "U0OUTSIDE1": ({**base, "team_id": "T0OTHER01"}, people.OTHER_ORGANISATION),
        "U0STRANGE1": ({**base, "is_stranger": True}, people.OTHER_ORGANISATION),
        "U0BOTUSER1": ({**base, "is_bot": True}, people.NO_PERSON),
        "U0UNKNOWN1": (None, people.SLACK_SAID.format(code="user_not_found")),
    }
    for user, (found, reason) in cases.items():
        if found is not None:
            bench.users[user] = {"id": user, **found}
        with pytest.raises(people.NotFiled) as refused:
            asyncio.run(people.author(bench.context, {"user": user, "text": "Row 4."}))
        assert str(refused.value) == reason
    with pytest.raises(people.NotFiled):
        asyncio.run(people.author(bench.context, {"bot_id": "B0APP0001", "text": "Row 4."}))
    bench.users["U0CEDAR01"] = {"id": "U0CEDAR01", **base}
    assert asyncio.run(people.author(bench.context, {"user": "U0CEDAR01"})) == "Cedar Quill"


def test_privacy_when_the_app_is_removed(world):  # noqa: F811
    """privacy.md: "When the server removes the installation, both secrets and every link
    are deleted at once, and every note Slack created, typed or filed, is
    forgotten 10 days later, as a note is forgotten (below). Until then the **Sources** page
    says how many notes will go and when, and an owner can forget them at once with **Forget
    them now**; setting Slack up again for the same Slack workspace before that day keeps
    them.\""""
    says(
        *PRIVACY,
        "When the server removes the installation, both secrets and every link are"
        " deleted at once, and every note Slack created, typed or filed, is forgotten"
        " 10 days later, as a note is forgotten (below). Until then the **Sources** page says"
        " how many notes will go and when, and an owner can forget them at once with **Forget"
        " them now**; setting Slack up again for the same Slack workspace before that day keeps"
        " them.",
    )
    install = ready(world)
    typed, filed = a_note(world), a_note(world, "# Moon\n\nThe moon is full.\n")
    world.view.record_slack_note(install.id, typed, how="typed", brought_by=world.owner)
    world.view.record_slack_note(
        install.id, filed, how="filed", brought_by=world.owner, conversation_id="C0TIDES01",
        message_time="1900000000.000100",
    )  # fmt: skip
    system = world.store.for_account(world.account).acting_as("system", None)
    system.remove_slack_install(install.id)  # Slack's app_uninstalled
    assert world.view.slack_notes_to_forget().notes == 2
    world.clock.now += FORGET_AFTER - timedelta(minutes=1)
    assert world.store.slack_notes_due() == []
    world.clock.now += timedelta(minutes=2)
    assert sorted(note for _, note in world.store.slack_notes_due()) == sorted([typed, filed])
    world.view.forget_slack_notes_now(install.id)
    assert world.view.get_note(typed) is None and world.view.get_note(filed) is None


@pytest.mark.parametrize("ahead", [0, 30])
def test_privacy_and_run_slack_removal_uses_two_clocks(world, ahead):  # noqa: F811
    """privacy.md and run.md: what happens when Slack's removal time precedes set-up."""
    slack_now = world.clock.now
    world.clock.now += timedelta(seconds=ahead)
    install = ready(world)
    world.view.link_slack_user(install.id, ADA_SLACK, world.owner)
    note = a_note(world)
    world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)

    def http():
        return httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"ok": False, "error": "token_revoked"})
            )
        )

    sign_in = SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=http)
    app = create_app(
        world.store, public_url=BASE, sign_in=sign_in, clock=world.clock,
        slack=web_slack.Slack(key=KEY, http=http),
    )  # fmt: skip
    client = TestClient(app, base_url=BASE)
    slack_now += timedelta(seconds=1)
    world.clock.now += timedelta(seconds=1)
    stamp = str(int(slack_now.timestamp()))
    body = json.dumps({
        "type": "event_callback", "team_id": install.team_id, "event_id": "Ev0FABLE01",
        "event_time": int(stamp), "event": {"type": "app_uninstalled"},
    }).encode()  # fmt: skip
    reply = client.post(
        f"/integrations/slack/{install.id}/events",
        content=body,
        headers={
            "x-slack-request-timestamp": stamp,
            "x-slack-signature": signature(SECRET, stamp, body).decode(),
        },
    )
    assert (reply.status_code, reply.content) == (200, b"")
    ignored = world.view.list_audit(action="slack.lifecycle_ignored")
    if ahead:
        assert [entry.detail for entry in ignored] == [
            {"install": install.id, "event": "app_uninstalled", "why": "at_or_before_setup"}
        ]
        assert world.view.get_slack_install() == install
        assert world.view.slack_person(install.id, ADA_SLACK) is not None
        assert world.view.slack_notes_to_forget() is None
        assert world.store.list_audit(action="slack.lifecycle_ignored") == []
        world.view.switch_slack_install(install.id, on=False)
        keeper = app.state.slack_keeper
        world.clock.now += CHECK_EVERY
        assert asyncio.run(keeper.check()) == 1
        assert world.view.get_slack_install().holds_bot_token
        world.clock.now += CHECK_EVERY
        assert asyncio.run(keeper.check()) == 1
        assert world.view.get_slack_install().state == "off"
        world.clock.now += timedelta(days=3, seconds=1)
        keeper.run_once()
    else:
        assert ignored == []
    removed = world.view.get_slack_install()
    assert removed.state == "removed" and not removed.holds_secrets
    assert world.view.slack_person(install.id, ADA_SLACK) is None
    assert world.view.slack_notes_to_forget().at == world.clock.now + FORGET_AFTER
    says(
        *PRIVACY,
        "The server acts on an app removal from Slack only when Slack's dispatch time is in"
        " a later second than the installation's set-up on the server's clock.",
        "It ignores removals dated at or before that second or with no valid dispatch time"
        " and records them in the workspace audit log.",
        "Checking the bot token can reveal a missed removal.",
    )
    says(
        *RUN,
        "Keep the server's clock accurate so it can recognize removal events Slack dispatches"
        " after set-up.",
        "The `slack.lifecycle_ignored` audit entry names the event and gives the reason.",
        "The reason is `at_or_before_setup` for an uninstall or revocation dated at or before"
        " the server's set-up second, or `invalid_time` for a missing or invalid dispatch time.",
    )


def test_privacy_what_was_there_before_slack_is_not_slacks_to_forget(bench):
    """privacy.md: "When the project already holds a note with the same text and
    `source_ref`, typing it again stores no new note or Slack record. Filing a matching message
    records a tie to the existing note. Removing Slack deletes the tie and leaves the note in
    place.\""""
    says(
        *PRIVACY,
        "When the project already holds a note with the same text and `source_ref`, typing it"
        " again stores no new note or Slack record.",
        "Filing a matching message records a tie to the existing note.",
        "Removing Slack deletes the tie and leaves the note in place.",
    )
    bench.link(ADA_SLACK, bench.ada, bench.library)
    bench.owner.add_note(bench.library.id, "Row 4 holds the quince jars.", via="web")
    assert said(ask(bench, "note Row 4 holds the quince jars.")).startswith("Filed: ")
    assert len(bench.notes()) == 1
    assert bench.store._con.execute("SELECT COUNT(*) FROM slack_notes").fetchone() == (0,)
    message = {"type": "message", "user": CEDAR, "ts": "1900000000.000100", "text": "Row 4."}
    bench.users[CEDAR] = cedar()
    draft = note_from_message(
        message, team_id=TEAM, team_domain="fable-works", conversation_id=CONVERSATION,
        author="Cedar Quill", names={CEDAR: "Cedar Quill"},
    )  # fmt: skip
    different, _ = bench.owner.add_note(
        bench.library.id, draft.text, title=draft.title, source_ref="doc:fable"
    )
    matching, _ = bench.owner.add_note(
        bench.library.id, draft.text, title=draft.title, source_ref=draft.source_ref
    )
    handle(bench, message)
    assert len(bench.notes()) == 3
    tie = bench.store._con.execute("SELECT note_id, owns, forget_at FROM slack_notes").fetchall()
    assert tie == [(matching.id, 0, None)]
    bench.owner.remove_slack_install(bench.install.id)
    assert bench.store._con.execute("SELECT COUNT(*) FROM slack_notes").fetchone() == (0,)
    assert bench.owner.slack_notes_to_forget() is None
    assert all(bench.owner.get_note(note.id) is not None for note in (different, matching))


@pytest.mark.parametrize(
    "polling", ["on", "off", "timeout", "rate limit", "5xx", "connection", "works"]
)
def test_privacy_a_token_slack_ended_is_removed_three_days_later(world, polling):  # noqa: F811
    """privacy.md: when the server checks, how it recognizes an ended token, what can delay
    discovery and when it removes the installation and forgets its notes."""
    install = ready(world)
    note = a_note(world)
    world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)
    ended = httpx.Response(200, json={"ok": False, "error": "token_revoked"})
    answer = ended
    checks = []

    def auth_test(request):
        checks.append(world.clock.now)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def http():
        return httpx.AsyncClient(transport=httpx.MockTransport(auth_test))

    sign_in = SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=http)
    slack = web_slack.Slack(key=KEY, http=http) if polling != "off" else None
    app = create_app(world.store, public_url=BASE, sign_in=sign_in, clock=world.clock, slack=slack)
    keeper = app.state.slack_keeper
    removed_at_slack = world.clock.now
    world.clock.now += timedelta(hours=6)
    if polling == "off":
        assert asyncio.run(keeper.check()) == 0
        world.clock.now += CHECK_EVERY
        assert asyncio.run(keeper.check()) == 0
        assert not checks and world.view.get_slack_install().state == "ready"
        world.view.remove_slack_install(install.id)
    else:
        assert asyncio.run(keeper.check()) == 1
        assert world.view.get_slack_install().holds_bot_token
        world.clock.now += timedelta(hours=6, seconds=-1)
        assert asyncio.run(keeper.check()) == 0
        world.clock.now += timedelta(seconds=1)
        if polling != "on":
            answer = {
                "timeout": httpx.ReadTimeout("slow"),
                "rate limit": httpx.Response(429, headers={"retry-after": "30"}),
                "5xx": httpx.Response(503),
                "connection": httpx.ConnectError("unreachable"),
                "works": httpx.Response(200, json={"ok": True}),
            }[polling]
            assert asyncio.run(keeper.check()) == 1
            assert world.clock.now - removed_at_slack == timedelta(hours=12)
            assert world.view.get_slack_install().holds_bot_token
            world.clock.now += CHECK_EVERY
            answer = ended
            if polling == "works":
                assert asyncio.run(keeper.check()) == 1
                assert world.view.get_slack_install().holds_bot_token
                world.clock.now += CHECK_EVERY
        assert asyncio.run(keeper.check()) == 1
        found = world.view.get_slack_install()
        assert found.state == "off" and not found.holds_bot_token
        world.clock.now += timedelta(days=3)
        assert keeper.run_once() == (0, 0)
        assert world.view.get_slack_install().state == "off"
        world.clock.now += timedelta(minutes=1)
        assert keeper.run_once() == (0, 0)
        assert world.view.get_slack_install().state == "removed"
    assert world.view.slack_notes_to_forget().at == world.clock.now + FORGET_AFTER
    world.clock.now += FORGET_AFTER
    assert keeper.run_once() == (0, 1)
    assert world.view.get_note(note) is None
    says(
        *PRIVACY,
        "The server switches the installation off and deletes the bot token when it acts on a"
        " revocation of that token or when two checks say the token has ended.",
        "After 3 days with an ended bot token, the server removes the installation unless a"
        " new bot token was pasted on the **Sources** page.",
        "These checks run at most once every 6 hours while the server runs with `serve --slack`.",
        "Any successful check resets the count.",
        "Timeouts, rate limits, HTTP 5xx responses and failed connections can delay discovery.",
        "The server forgets notes that are due even when it runs without `--slack`.",
    )


def test_privacy_backups_hold_the_notes_until_they_expire():
    """privacy.md: "Backups made before the forgetting hold those notes until they expire, as
    they hold every forgotten note.\""""
    says(
        *PRIVACY,
        "Backups made before the forgetting hold those notes until they expire, as they hold"
        " every forgotten note.",
    )
    deletion = flat(section("privacy", "## Deletion"))
    assert "**Backups** made before the deletion keep what was deleted until they expire." in (
        deletion
    )


def test_privacy_the_slack_secrets_are_credentials_the_database_can_hold():
    """privacy.md: "A connected Google account's refresh token, and a Slack app's signing secret
    and bot token, each stored sealed (above), are the only credentials of other services the
    database can hold.\""""
    raw = flat(guide_source("privacy"))
    sentence = (
        "A connected Google account's refresh token, and a Slack app's signing secret and bot"
        " token, each stored sealed (above), are the only credentials of other services the"
        " database can hold."
    )
    assert raw.count(sentence) == 1
    assert "is the one credential of another service" not in raw


def test_privacy_where_a_note_from_slack_can_go(bench, monkeypatch):
    """privacy.md: "cairn compiles a note that came through Slack itself, with no model. A
    page that holds one is not sent to a model the operator connects to arrange pages unless
    the operator sets `<PREFIX>_SLACK_NOTES=send`, the one setting that names Slack, and then
    not in a project its people keep from every model; cairn tidies that page itself
    instead.\""""
    name = PRODUCT.name
    says(
        *PRIVACY,
        f"{name} compiles a note that came through Slack itself, with no model.",
        "A page that holds one is not sent to a model the operator connects to arrange pages"
        " unless the operator sets `<PREFIX>_SLACK_NOTES=send`, the one setting that names"
        " Slack, and then not in a project its people keep from every model;"
        f" {name} tidies that page itself instead.",
    )
    # compiled with no model: Memory compiles with compile_note's own default, the extractive
    assert inspect.signature(compile_note).parameters["compose"].default is extractive
    assert "compose" not in inspect.getsource(Memory.note)
    # held unless the setting that names Slack says send; SOURCE_NOTES=send does not
    environ = {"ARRANGER_SOURCE_NOTES": "send"}
    assert not cli_arrange.sends_slack_notes("ARRANGER", environ)
    assert cli_arrange.sends_slack_notes("ARRANGER", {"ARRANGER_SLACK_NOTES": "send"})
    bench.link(ADA_SLACK, bench.ada, bench.library)
    ask(bench, "note Kiln log\n\nThe kiln is fired on Tuesdays.")
    model = FakeModel([])
    arranger = Arranger(model, price=None, view_for=bench.store.for_account, withhold=())
    arranger.queue(bench.account, bench.library.id, "kiln-log")
    (done,) = asyncio.run(arranger.run_once())
    assert done.status == "withheld" and model.requests == []


def test_privacy_the_list_of_what_a_model_is_not_sent_names_slack():
    """privacy.md, **Models.**: "A page that holds a note any source the server reads brought
    (...), or a note that came through Slack, is not sent unless the operator allows it"
    (Privacy 4: every source's notes, and Slack's) and "A page that holds a note that came
    through Slack is sent only with `<PREFIX>_SLACK_NOTES=send`, except in such a project.\""""
    (line,) = [line for line in guide_source("privacy").splitlines() if "**Models.**" in line]
    assert (
        "A page that holds a note any source the server reads brought (Langfuse, Granola, Google"
        " Meet, Google Calendar, Gmail, Google Drive, Linear, GitHub or a connector of your own),"
        " or a note that came through Slack, is not sent unless the operator allows it;"
    ) in flat(line)
    assert (
        "A page that holds a note that came through Slack is sent only with"
        " `<PREFIX>_SLACK_NOTES=send`, except in such a project."
    ) in flat(line)
    assert frozenset({"slack"}) == sources.HELD_VIA
    assert "slack" not in sources.withheld_via() and "slack" not in sources.KINDS


# The security guide, "Slack"

SECURITY = ("security", "## Slack")


def test_security_the_three_addresses_slack_calls_and_their_signature(bench):
    """security.md: "Off unless the operator starts the server with `--slack`. Slack calls
    three addresses of each workspace's install: `/integrations/slack/<install>/events`,
    `/commands` and `/actions`. They take no session and no key. A request is acted on only
    when its signature holds: an HMAC-SHA256 of its timestamp and its body under that install's
    signing secret, compared in constant time, with a timestamp within 5 minutes of the
    server's clock.\""""
    says(
        *SECURITY,
        "Off unless the operator starts the server with `--slack`. Slack calls three addresses"
        " of each workspace's install: `/integrations/slack/<install>/events`, `/commands` and"
        " `/actions`. They take no session and no key.",
        "A request is acted on only when its signature holds: an HMAC-SHA256 of its timestamp"
        " and its body under that install's signing secret, compared in constant time, with a"
        " timestamp within 5 minutes of the server's clock.",
    )
    assert web_slack.OPEN_PATH.pattern.endswith("/(?:events|commands|actions)")
    assert SKEW == 300 and "compare_digest" in inspect.getsource(verified)
    secret, body, now = "8f" + "e1c0ffee" * 3, b"token=unused", 1_900_000_000
    sign = {"x-slack-request-timestamp": str(now),
            "x-slack-signature": signature(secret, str(now), body).decode()}  # fmt: skip
    assert verified(secret, sign, body, now + SKEW)
    assert not verified(secret, sign, body, now + SKEW + 1)
    assert not verified("another-" + secret, sign, body, now)


def test_security_what_is_refused_before_and_after_the_signature(bench):
    """security.md: "Before the signature is checked, a body over 1 MiB is refused, and so is
    an install that does not exist or is not set up. After it, a request of another Slack
    workspace is refused. The signature is computed for every request that gets this far, and
    a request whose signature holds is never refused because of requests whose signature did
    not. Those that fail it are counted for each install and client address: after 20 in a
    minute, a further one from that address to that install is refused until the oldest of
    them is a minute old. An event is acted on once, whatever Slack's retries say.\""""
    says(
        *SECURITY,
        "Before the signature is checked, a body over 1 MiB is refused, and so is an install"
        " that does not exist or is not set up. After it, a request of another Slack workspace"
        " is refused. The signature is computed for every request that gets this far, and a"
        " request whose signature holds is never refused because of requests whose signature"
        " did not. Those that fail it are counted for each install and client address: after"
        " 20 in a minute, a further one from that address to that install is refused until the"
        " oldest of them is a minute old. An event is acted on once, whatever Slack's retries"
        " say.",
    )
    assert BODY_MAX == 1024 * 1024 and "pending" not in ACCEPTING
    assert (web_slack.SIGNATURE_FAILURES, web_slack.SIGNATURE_WINDOW) == (20, 60)
    now = [1_900_000_000.0]
    failures = web_slack.signature_failures(lambda: now[0])
    here, fable, quill = "203.0.113.5", "sl_0123456789ab", "sl_ba9876543210"
    assert [failures.failed(fable, here) for _ in range(20)] == [0] * 20
    assert failures.failed(fable, here) > 0  # refused
    assert failures.failed(quill, here) == 0 and failures.failed(fable, "198.51.100.7") == 0
    now[0] += 60
    assert failures.failed(fable, here) == 0
    receive = inspect.getsource(web_slack.slack_routes)
    assert receive.index("verified(") < receive.index("failures.failed(")
    other = b'{"type": "event_callback", "team_id": "T0OTHER01", "event": {"type": "x"}}'
    with pytest.raises(OtherTeam):
        envelope("events", bench.install, other, {})
    assert bench.store.slack_event_seen(bench.install.id, "Ev0RETRY01") is True
    assert bench.store.slack_event_seen(bench.install.id, "Ev0RETRY01") is False


def test_security_the_four_permissions():
    """security.md: "The app asks Slack for four permissions: `chat:write`, to post the bot's
    answers; `commands`, for the slash command and the message shortcut; `users:read`, to know a
    Slack user's name and whether a message's author is of this Slack workspace; and
    `im:history`, which lets the bot read the direct messages people send it, and nothing among
    people.\""""
    says(
        *SECURITY,
        "The app asks Slack for four permissions: `chat:write`, to post the bot's answers;"
        " `commands`, for the slash command and the message shortcut; `users:read`, to know a"
        " Slack user's name and whether a message's author is of this Slack workspace; and"
        " `im:history`, which lets the bot read the direct messages people send it, and nothing"
        " among people.",
    )
    assert sorted(manifest.SCOPES) == sorted(setup.PERMISSIONS)
    assert setup.PERMISSIONS["chat:write"].startswith("Post the bot's answers")
    assert "slash command and the message shortcut" in setup.PERMISSIONS["commands"]
    assert (
        "whether a message's author is of this Slack workspace" in (setup.PERMISSIONS["users:read"])
    )
    assert "the direct messages people send the bot" in setup.PERMISSIONS["im:history"]
    assert "nothing among people" in setup.PERMISSIONS["im:history"]


def test_security_answers_are_private_and_built_from_the_askers_reach(bench):
    """security.md: "Answers are private: the slash command's and the shortcut's are shown only
    to the person who asked, and a direct message is answered in that direct conversation. An
    answer is built from the asker's reach, as a search of theirs: a project they do not reach
    gives nothing, not even a count.\""""
    says(
        *SECURITY,
        "Answers are private: the slash command's and the shortcut's are shown only to the"
        " person who asked, and a direct message is answered in that direct conversation. An"
        " answer is built from the asker's reach, as a search of theirs: a project they do not"
        " reach gives nothing, not even a count.",
    )
    cellar = bench.owner.create_project("cellar", everyone="none")
    bench.owner.add_note(cellar.id, "# Cellar\n\nThe quince jars are in the cellar.\n")
    bench.link(ADA_SLACK, bench.ada, bench.library)
    bench.link(BROOK_SLACK, bench.brook, bench.library)
    assert "quince" in said(ask(bench, "find quince"))  # Ada reaches the cellar
    brooks = said(ask(bench, "find quince", BROOK_SLACK))  # ephemeral, checked by ask
    assert brooks == said(blocks.nothing_found("quince")) and "cellar" not in brooks


def test_security_what_links_a_slack_user_to_a_person(bench, monkeypatch):
    """security.md: "A Slack user is linked to a person in two steps, once they have asked the
    bot for an address, which is theirs alone. The person opens it signed in and says
    yes, and is shown a code, once. The same Slack user types that code with the command
    within 10 minutes. So a Slack user is linked to a person only when that Slack user knew a
    code shown only to that person: someone who sends their address to another person gains
    nothing from that person's yes. A wrong code counts against the Slack user's offer, and
    the fifth deletes it; a code typed by any other Slack user is dead.\""""
    says(
        *SECURITY,
        "A Slack user is linked to a person in two steps, once they have asked the bot for an"
        " address, which is theirs alone. The person opens it signed in and says yes, and is"
        " shown a code, once. The same Slack user types that code with the command within 10"
        " minutes. So a Slack user is linked to a person only when that Slack user knew a code"
        " shown only to that person: someone who sends their address to another person gains"
        " nothing from that person's yes. A wrong code counts against the Slack user's offer,"
        " and the fifth deletes it; a code typed by any other Slack user is dead.",
    )
    monkeypatch.setattr(store_slack, "_new_code", lambda: "KM7QR2XZ")  # never AAAA-AAAA
    assert timedelta(minutes=10) == OFFER_TTL and TRIES == 5
    code = yes_and_code(bench, BROOK_SLACK, bench.ada)  # Brook's Slack user's address, Ada's yes
    assert bench.owner.slack_person(bench.install.id, BROOK_SLACK) is None
    assert "link?t=" in said(ask(bench, "find jars", BROOK_SLACK))  # still nobody's
    code = yes_and_code(bench, BROOK_SLACK, bench.ada)
    said(ask(bench, "link", ADA_SLACK))
    assert "links nothing" in said(ask(bench, f"link {code}", ADA_SLACK))  # another's: dead
    assert "links nothing" in said(ask(bench, f"link {code}", BROOK_SLACK))
    code = yes_and_code(bench, BROOK_SLACK, bench.brook)
    for _ in range(TRIES):
        assert "links nothing" in said(ask(bench, "link AAAA-AAAA", BROOK_SLACK))
    assert "links nothing" in said(ask(bench, f"link {code}", BROOK_SLACK))  # deleted
    assert bench.owner.slack_person(bench.install.id, BROOK_SLACK) is None


def test_security_a_link_is_never_moved_and_the_limit_of_the_code(bench):
    """security.md: "A link is never moved to another person: a Slack user linked to someone
    stays linked to them until it is undone. The limit is said plainly: someone who talks you
    into sending them your code acts as you, which is why the page that shows it says to give
    it to no one.\""""
    says(
        *SECURITY,
        "A link is never moved to another person: a Slack user linked to someone stays linked"
        " to them until it is undone. The limit is said plainly: someone who talks you into"
        " sending them your code acts as you, which is why the page that shows it says to give"
        " it to no one.",
    )
    bench.link(ADA_SLACK, bench.ada)
    with pytest.raises(ValueError):
        bench.owner.link_slack_user(bench.install.id, ADA_SLACK, bench.brook)
    assert bench.owner.slack_person(bench.install.id, ADA_SLACK).user_id == bench.ada
    assert "Give this code to no one: whoever types it in Slack acts as you here." in CODE_PAGE


def test_security_the_client_reads_no_conversations_history():
    """security.md: "The signing secret and the bot token are sealed under the operator's key,
    `CAIRN_SLACK_KEY`, so a copy of the database opens neither. cairn's Slack client calls
    nothing at Slack that reads a conversation's history: it asks whose a token is and who a
    user is, posts the bot's answers, and answers through the address a signed request gave.\""""
    says(
        *SECURITY,
        "The signing secret and the bot token are sealed under the operator's key,"
        " `CAIRN_SLACK_KEY`, so a copy of the database opens neither.",
        f"{PRODUCT.name}'s Slack client calls nothing at Slack that reads a conversation's"
        " history: it asks whose a token is and who a user is, posts the bot's answers, and"
        " answers through the address a signed request gave.",
    )
    source = inspect.getsource(SlackClient)
    for method in ("auth.test", "users.info", "chat.postMessage"):
        assert f'"{method}"' in source
    assert "history" not in source.replace("reads a conversation's history", "")
    assert "conversations." not in source


def test_security_the_one_credential_sentences_name_slack_too():
    """security.md: "The credentials of other services it can hold, a connected Google
    account's refresh token and a Slack app's signing secret and bot token, are sealed: see
    [below](#google-accounts-connected-in-the-browser) and [Slack](#slack)." and "The database
    never holds a third party's key, except the sealed tokens of Google accounts connected in
    the browser and the sealed secrets of a Slack app, when the operator switches either on.\""""
    raw = flat(guide_source("security"))
    for sentence in (
        "The credentials of other services it can hold, a connected Google account's refresh"
        " token and a Slack app's signing secret and bot token, are sealed: see"
        " [below](#google-accounts-connected-in-the-browser) and [Slack](#slack).",
        "The database never holds a third party's key, except the sealed tokens of Google"
        " accounts connected in the browser and the sealed secrets of a Slack app, when the"
        " operator switches either on",
    ):
        assert raw.count(sentence) == 1, sentence


def test_run_says_which_setting_sends_slacks_notes_to_the_arranging_model():
    """run.md: "With a model connected to arrange pages, a page that holds a note that came
    through Slack is sent to it only if the environment also says
    `ARRANGER_SLACK_NOTES=send`; `ARRANGER_SOURCE_NOTES=send` does not send it.\""""
    says(
        *RUN,
        "With a model connected to arrange pages, a page that holds a note that came through"
        " Slack is sent to it only if the environment also says `ARRANGER_SLACK_NOTES=send`;"
        " `ARRANGER_SOURCE_NOTES=send` does not send it.",
    )
    assert cli_arrange.sends_slack_notes("ARRANGER", {"ARRANGER_SLACK_NOTES": "send"}) is True
    assert cli_arrange.sends_slack_notes("ARRANGER", {"ARRANGER_SOURCE_NOTES": "send"}) is False
    assert cli_arrange.sends_slack_notes("ARRANGER", {}) is False
