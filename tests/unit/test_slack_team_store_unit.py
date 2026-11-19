"""An install that is ready, on or off belongs to its Slack team (cairn.store.slack): secrets
of another team are refused and change nothing; every note row says which team it came from;
and setting an install up again cancels the forgetting of that team's notes alone. Every name,
id and secret here is made up."""

import pytest
from test_slack_store_unit import (  # noqa: F401
    OTHER_TEAM,
    SECRET,
    TEAM,
    TOKEN,
    a_note,
    ready,
    world,
)
from test_store_unit import rows

from cairn.store.slack import FORGET_AFTER, OtherTeam

QUILL_SECRET = "slack-signing-" + "4b" * 8
QUILL_TOKEN = "xoxb-" + "unit-" + "9876543210"
CONVERSATION = "C0LIB0001"


def everything(world) -> dict:  # noqa: F811
    """Every Slack row as stored, the sealed secrets as their sealed text."""
    return {
        table: rows(world.db, f"SELECT * FROM {table} ORDER BY 1")
        for table in ("slack_installs", "slack_people", "slack_link_offers", "slack_notes")
    }


def typed(world, install, text: str) -> str:  # noqa: F811
    note = a_note(world, text)
    world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)
    return note


def paste(world, install, team=OTHER_TEAM, secret=QUILL_SECRET, token=QUILL_TOKEN):  # noqa: F811
    return world.view.ready_slack_install(
        install.id, signing_secret=secret, bot_token=token, team_id=team,
        team_domain="quill", bot_user_id="U0BOT0002",
    )  # fmt: skip


# 1. Secrets of another team are refused while the install holds its team


@pytest.mark.parametrize("state", ["ready", "on", "off"])
def test_another_teams_secrets_are_refused_and_nothing_changes(world, state):  # noqa: F811
    install = ready(world)
    world.view.link_slack_user(install.id, "U0ADA0001", world.owner)
    world.view.offer_slack_link(install.id, "U0BOB0001")
    typed(world, install, "# Tides\n\nThe tide turns.\n")
    if state == "on":
        world.store.slack_install_verified(install.id)
    if state == "off":
        world.view.switch_slack_install(install.id, on=False)
    before = everything(world)
    with pytest.raises(OtherTeam) as refused:
        paste(world, install)
    assert "remove Slack first" in str(refused.value)
    assert QUILL_TOKEN not in str(refused.value) and OTHER_TEAM not in str(refused.value)
    assert everything(world) == before  # the team, both sealed secrets, links, notes, days
    assert world.view.get_slack_install().team_id == TEAM


def test_the_same_teams_secrets_are_kept_as_before(world):  # noqa: F811
    install = ready(world)
    world.view.link_slack_user(install.id, "U0ADA0001", world.owner)
    again = paste(world, install, team=TEAM, secret=SECRET, token="xoxb-" + "unit-" + "5555")
    assert again.team_id == TEAM and again.state == "ready" and again.holds_secrets
    assert world.view.slack_person(install.id, "U0ADA0001") is not None


def test_a_pending_install_takes_any_team(world):  # noqa: F811
    made = world.view.make_slack_install()
    assert paste(world, made).team_id == OTHER_TEAM


# 2. Every note row says which team it came from


def test_every_row_holds_the_team_of_its_install_when_it_was_made(world):  # noqa: F811
    install = ready(world)
    note = typed(world, install, "# Tides\n\nThe tide turns.\n")
    world.view.claim_slack_message(install.id, CONVERSATION, "1900000000.000100", brought_by=None)
    filed = a_note(world, "# Jars\n\nRow 4.\n")
    # a filed message whose claim was swept meanwhile: the record makes its row again
    world.view.record_slack_note(
        install.id, filed, how="filed", brought_by=None, conversation_id=CONVERSATION,
        message_time="1900000001.000100",
    )  # fmt: skip
    world.view.remove_slack_install(install.id)
    world.view.make_slack_install()
    paste(world, install)
    later = typed(world, install, "# Kettles\n\nThe kettle sings.\n")
    found = rows(world.db, "SELECT note_id, message_time, team_id FROM slack_notes ORDER BY id")
    assert found == [
        (note, None, TEAM),
        (None, "1900000000.000100", TEAM),
        (filed, "1900000001.000100", TEAM),
        (later, None, OTHER_TEAM),
    ]


# 3. Setting up again cancels the forgetting of the team being set up, and only that team's


def test_a_then_b_set_up_then_bs_secrets_again_leaves_as_notes_due(world):  # noqa: F811
    install = ready(world)
    first = [typed(world, install, f"# A{n}\n\nFact {n}.\n") for n in range(2)]
    world.view.remove_slack_install(install.id)
    world.view.make_slack_install()
    paste(world, install)  # team B set up
    paste(world, install)  # B's secrets pasted again, as a routine change at Slack
    due = rows(world.db, "SELECT note_id FROM slack_notes WHERE forget_at IS NOT NULL ORDER BY 1")
    assert due == sorted((note,) for note in first)
    world.clock.now += FORGET_AFTER
    assert sorted(world.store.slack_notes_due()) == sorted((world.account, n) for n in first)


def test_bs_own_notes_are_kept_when_b_is_set_up_again(world):  # noqa: F811
    install = ready(world)
    a = typed(world, install, "# A\n\nFact A.\n")
    world.view.remove_slack_install(install.id)
    world.view.make_slack_install()
    paste(world, install)
    b = typed(world, install, "# B\n\nFact B.\n")
    world.view.remove_slack_install(install.id)  # both due now
    world.view.make_slack_install()
    paste(world, install)  # B again: B's notes are kept, A's stay due
    due = rows(world.db, "SELECT note_id FROM slack_notes WHERE forget_at IS NOT NULL")
    assert due == [(a,)]
    assert b != a


def test_the_first_team_set_up_again_later_does_not_cancel_its_old_notes(world):  # noqa: F811
    """Once another team was set up, the first team's notes are forgotten on their day
    whatever happens to the install afterwards: the first team coming back included."""
    install = ready(world)
    a = typed(world, install, "# A\n\nFact A.\n")
    world.view.remove_slack_install(install.id)
    world.view.make_slack_install()
    paste(world, install)
    world.view.remove_slack_install(install.id)
    world.view.make_slack_install()
    ready(world)  # team A again
    assert rows(world.db, "SELECT note_id FROM slack_notes WHERE forget_at IS NOT NULL") == [(a,)]


# 4. The count, the day and "forget them now", whichever team the notes came from


def test_the_count_the_day_and_forget_now_take_every_due_note_whatever_its_team(world):  # noqa: F811
    install = ready(world)
    a = typed(world, install, "# A\n\nFact A.\n")
    world.view.remove_slack_install(install.id)
    first_day = world.view.slack_notes_to_forget().at
    world.view.make_slack_install()
    paste(world, install)
    shown = world.view.slack_notes_to_forget()  # B ready, A's note still due: still said
    assert (shown.notes, shown.at) == (1, first_day)
    b = typed(world, install, "# B\n\nFact B.\n")
    world.clock.now += FORGET_AFTER / 2
    world.view.remove_slack_install(install.id)
    shown = world.view.slack_notes_to_forget()
    assert (shown.notes, shown.at) == (2, first_day)
    assert world.view.forget_slack_notes_now(install.id) == 2
    assert world.view.get_note(a) is None and world.view.get_note(b) is None
    assert world.view.slack_notes_to_forget() is None
