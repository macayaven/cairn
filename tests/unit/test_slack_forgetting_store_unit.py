"""Every note Slack created is forgotten when Slack goes (cairn.store.slack): a filed message
claimed before it is written, the bot token checked with Slack, an install whose token Slack
ended removed, and one Slack team for one workspace whatever races. Every name, id and secret
here is made up."""

import json
from datetime import timedelta

import pytest
from test_slack_store_unit import (  # noqa: F401
    NOW,
    OTHER_TEAM,
    SECRET,
    TEAM,
    TOKEN,
    a_note,
    ready,
    workspaces,
    world,
)
from test_store_unit import rows

from cairn.store import reach
from cairn.store.base import _iso
from cairn.store.slack import (
    CHECK_EVERY,
    CHECKS_AT_ONCE,
    CLAIM_FOR,
    CLAIMED,
    ENDED,
    FILED,
    FILING,
    FORGET_AFTER,
    GONE_AFTER,
    GONE_DAYS,
    TeamHeld,
)

CONVERSATION, TIME = "C0LIB0001", "1900000000.000100"


def claim(world, install, time=TIME):  # noqa: F811
    return world.view.claim_slack_message(install.id, CONVERSATION, time, brought_by=world.owner)


def filed(world, install, note, time=TIME):  # noqa: F811
    return world.view.record_slack_note(
        install.id, note, how="filed", brought_by=world.owner, conversation_id=CONVERSATION,
        message_time=time,
    )  # fmt: skip


def claims(world):  # noqa: F811
    return rows(world.db, "SELECT note_id, forget_at FROM slack_notes ORDER BY id")


# One message, one note (S1)


def test_a_claim_is_claimed_now_being_filed_or_filed_with_its_note(world):  # noqa: F811
    install = ready(world)
    assert claim(world, install) == (CLAIMED, None)
    assert claim(world, install) == (FILING, None)  # someone else, between claim and write
    assert world.view.slack_note_for(install.id, CONVERSATION, TIME) is None
    note = a_note(world)
    assert filed(world, install, note) == note
    assert claim(world, install) == (FILED, note)
    assert claims(world) == [(note, None)]


def test_a_released_claim_can_be_claimed_again_and_a_filed_one_is_not_released(world):  # noqa: F811
    install = ready(world)
    claim(world, install)
    assert world.view.release_slack_claim(install.id, CONVERSATION, TIME) is True
    assert claims(world) == []
    assert claim(world, install) == (CLAIMED, None)
    note = a_note(world)
    filed(world, install, note)
    assert world.view.release_slack_claim(install.id, CONVERSATION, TIME) is False
    assert claim(world, install) == (FILED, note)


def test_a_claim_older_than_ten_minutes_with_no_note_is_swept(world):  # noqa: F811
    install = ready(world)
    claim(world, install)
    note = a_note(world)
    claim(world, install, "1900000001.000100")
    filed(world, install, note, "1900000001.000100")
    world.clock.now += CLAIM_FOR - timedelta(seconds=1)
    world.store.sweep_slack()
    assert len(claims(world)) == 2
    world.clock.now += timedelta(seconds=1)
    assert world.store.sweep_slack() == 1
    assert claims(world) == [(note, None)]
    assert timedelta(minutes=10) == CLAIM_FOR


def test_a_claim_with_no_note_is_never_due_nor_counted(world):  # noqa: F811
    install = ready(world)
    note = a_note(world)
    world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)
    claim(world, install)
    world.view.remove_slack_install(install.id)
    assert world.view.slack_notes_to_forget().notes == 1
    world.clock.now += FORGET_AFTER
    assert world.store.slack_notes_due() == [(world.account, note)]
    assert world.view.forget_slack_notes_now(install.id) == 1


def test_removed_between_claim_and_write_the_note_is_due_like_the_others(world):  # noqa: F811
    install = ready(world)
    claim(world, install)
    world.clock.now += timedelta(minutes=1)
    world.store.for_account(world.account).acting_as("system", None).remove_slack_install(
        install.id
    )
    world.clock.now += timedelta(seconds=2)
    note = a_note(world)
    filed(world, install, note)
    assert claims(world) == [(note, _iso(NOW + timedelta(minutes=1) + FORGET_AFTER))]
    world.clock.now = NOW + timedelta(minutes=1) + FORGET_AFTER
    assert world.store.slack_notes_due() == [(world.account, note)]


def test_a_typed_note_recorded_after_its_install_was_removed_is_due_too(world):  # noqa: F811
    install = ready(world)
    world.view.remove_slack_install(install.id)
    world.clock.now += timedelta(seconds=3)
    note = a_note(world)
    world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)
    assert claims(world) == [(note, _iso(NOW + FORGET_AFTER))]


def test_a_filed_note_whose_claim_was_swept_is_still_recorded(world):  # noqa: F811
    install = ready(world)
    claim(world, install)
    world.clock.now += CLAIM_FOR
    world.store.sweep_slack()
    note = a_note(world)
    assert filed(world, install, note) == note
    assert claims(world) == [(note, None)]


def test_claiming_names_its_install_here_and_a_message_as_slack_writes_it(world):  # noqa: F811
    install = ready(world)
    with pytest.raises(KeyError):
        world.view.claim_slack_message("sl_000000000000", CONVERSATION, TIME, brought_by=None)
    with pytest.raises(ValueError):
        world.view.claim_slack_message(install.id, "C0 1", TIME, brought_by=None)
    with pytest.raises(ValueError):
        world.view.claim_slack_message(install.id, CONVERSATION, "soon", brought_by=None)


# One Slack team, one workspace, atomically


def test_a_team_another_workspace_holds_is_refused_by_the_store_itself(world):  # noqa: F811
    (other,) = workspaces(world, 1)
    with pytest.raises(TeamHeld):
        ready(world, team=other.team_id)
    assert world.view.get_slack_install().state == "pending"
    theirs = world.store.for_account(other.account_id)
    theirs.switch_slack_install(other.id, on=False)  # off still holds its team
    with pytest.raises(TeamHeld):
        ready(world, team=other.team_id)
    assert world.store.slack_team_held(other.team_id, world.account) is True


def test_a_pending_or_removed_install_holds_no_team(world):  # noqa: F811
    (other,) = workspaces(world, 1)
    theirs = world.store.for_account(other.account_id)
    theirs.remove_slack_install(other.id)
    theirs.make_slack_install()  # pending again, its old team kept on the row
    assert world.store.slack_team_held(other.team_id, world.account) is False
    assert ready(world, team=other.team_id).team_id == other.team_id
    with pytest.raises(TeamHeld):
        theirs.ready_slack_install(
            other.id, signing_secret=SECRET, bot_token=TOKEN, team_id=other.team_id,
            team_domain="fable", bot_user_id="U0BOT0001",
        )  # fmt: skip


def test_the_index_is_one_statement_of_the_step(world):  # noqa: F811
    (index,) = rows(world.db, "SELECT sql FROM sqlite_master WHERE name = 'slack_installs_team'")
    assert "UNIQUE" in index[0] and "'ready', 'on', 'off'" in index[0]


# The bot token checked with Slack (S2)


def test_the_installs_that_hold_a_bot_token_are_checked_once_every_six_hours(world):  # noqa: F811
    install = ready(world)
    assert world.store.take_slack_checks() == []  # Slack accepted it as it was made ready
    world.clock.now += CHECK_EVERY
    (check,) = world.store.take_slack_checks()
    assert (check.install_id, check.account_id, check.token) == (install.id, world.account, TOKEN)
    assert TOKEN not in repr(check)
    assert world.store.take_slack_checks() == []  # stamped when taken
    world.clock.now += CHECK_EVERY - timedelta(seconds=1)
    assert world.store.take_slack_checks() == []
    world.clock.now += timedelta(seconds=1)
    assert [c.install_id for c in world.store.take_slack_checks()] == [install.id]
    assert timedelta(hours=6) == CHECK_EVERY


def test_switched_off_is_checked_and_pending_revoked_or_removed_is_not(world):  # noqa: F811
    install = ready(world)
    world.view.switch_slack_install(install.id, on=False)
    world.clock.now += CHECK_EVERY
    assert len(world.store.take_slack_checks()) == 1
    world.clock.now += CHECK_EVERY
    world.view.revoke_slack_bot(install.id)
    assert world.store.take_slack_checks() == []
    world.view.remove_slack_install(install.id)
    world.view.make_slack_install()
    world.clock.now += CHECK_EVERY
    assert world.store.take_slack_checks() == []


def test_at_most_twenty_installs_in_one_pass_the_longest_unchecked_first(world):  # noqa: F811
    made = workspaces(world, CHECKS_AT_ONCE + 1)
    world.clock.now += CHECK_EVERY
    first = world.store.take_slack_checks()
    assert len(first) == CHECKS_AT_ONCE == 20
    (rest,) = world.store.take_slack_checks()
    assert {c.install_id for c in first} | {rest.install_id} == {m.id for m in made}


def test_two_ended_answers_in_a_row_do_what_revoking_does(world):  # noqa: F811
    install = ready(world)
    world.clock.now += CHECK_EVERY
    world.view.link_slack_user(install.id, "U0ADA0001", world.owner)
    (check,) = world.store.take_slack_checks()
    found = world.store.slack_token_checked(check, "ended")
    assert found.state == "ready" and found.holds_bot_token and found.bot_gone_at is None
    world.clock.now += CHECK_EVERY
    (check,) = world.store.take_slack_checks()
    found = world.store.slack_token_checked(check, "ended")
    assert found.state == "off" and not found.holds_bot_token and found.needs_bot_token
    assert found.bot_gone_at == _iso(NOW + 2 * CHECK_EVERY)
    assert world.store.slack_inbound(install.id).signing_secret == SECRET
    assert world.view.slack_person(install.id, "U0ADA0001") is not None
    assert frozenset({"invalid_auth", "account_inactive", "token_revoked"}) == ENDED


def test_a_good_answer_in_between_starts_the_count_again_and_an_unclear_one_does_not(world):  # noqa: F811
    install = ready(world)
    world.clock.now += CHECK_EVERY
    for answer in ("ended", "works", "ended", "unclear"):
        (check,) = world.store.take_slack_checks()
        assert world.store.slack_token_checked(check, answer).holds_bot_token
        world.clock.now += CHECK_EVERY
    (check,) = world.store.take_slack_checks()
    assert not world.store.slack_token_checked(check, "ended").holds_bot_token
    assert rows(world.db, "SELECT dead_checks FROM slack_installs WHERE id = ?", install.id) == [
        (2,)
    ]


def test_an_answer_about_a_token_replaced_since_changes_nothing(world):  # noqa: F811
    install = ready(world)
    world.clock.now += CHECK_EVERY
    (check,) = world.store.take_slack_checks()
    world.store.slack_token_checked(check, "ended")
    world.clock.now += CHECK_EVERY
    (check,) = world.store.take_slack_checks()
    ready(world, token=TOKEN + "new")  # pasted while Slack was asked about the old one
    found = world.store.slack_token_checked(check, "ended")
    assert found.holds_bot_token and found.state == "ready"
    assert world.view.slack_bot_token(install.id) == TOKEN + "new"


def test_revoking_sets_since_when_and_a_new_token_clears_it(world):  # noqa: F811
    install = ready(world)
    world.clock.now += CHECK_EVERY
    (check,) = world.store.take_slack_checks()
    world.store.slack_token_checked(check, "ended")
    since = _iso(world.clock.now)
    assert world.view.revoke_slack_bot(install.id).bot_gone_at == since
    world.clock.now += timedelta(hours=1)
    assert world.view.revoke_slack_bot(install.id).bot_gone_at == since  # the first
    again = ready(world, token=TOKEN + "new")
    assert again.bot_gone_at is None
    assert rows(world.db, "SELECT dead_checks FROM slack_installs WHERE id = ?", install.id) == [
        (0,)
    ]


# An install whose token Slack ended, removed after GONE_DAYS


def audits(world, action):  # noqa: F811
    return rows(
        world.db,
        "SELECT actor_kind, actor, actor_id, detail FROM audit_events WHERE action = ?",
        action,
    )


def test_an_install_with_no_bot_token_for_three_days_is_removed_as_an_owner_does(world):  # noqa: F811
    install = ready(world)
    world.view.link_slack_user(install.id, "U0ADA0001", world.owner)
    note = a_note(world)
    world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)
    world.store.for_account(world.account).acting_as("system", None).revoke_slack_bot(install.id)
    world.clock.now += GONE_AFTER
    assert world.store.remove_gone_slack_installs() == []  # more than three days
    world.clock.now += timedelta(seconds=1)
    assert world.store.remove_gone_slack_installs() == [install.id]
    found = world.view.get_slack_install()
    assert found.state == "removed" and found.bot_gone_at is None
    assert world.store.slack_inbound(install.id).signing_secret is None
    assert world.view.slack_person(install.id, "U0ADA0001") is None
    due = world.view.slack_notes_to_forget()
    assert due.notes == 1 and due.at == world.clock.now + FORGET_AFTER
    ((kind, actor, actor_id, detail),) = audits(world, "slack.install_removed")
    assert (kind, actor, actor_id) == ("system", None, None)
    assert json.loads(detail) == {"install": install.id, "notes": 1, "why": "bot_token_ended"}
    assert world.store.remove_gone_slack_installs() == []
    assert GONE_DAYS == 3 and timedelta(days=3) == GONE_AFTER


def test_the_same_team_set_up_again_cancels_that_forgetting(world):  # noqa: F811
    install = ready(world)
    note = a_note(world)
    world.view.record_slack_note(install.id, note, how="typed", brought_by=world.owner)
    world.view.revoke_slack_bot(install.id)
    world.clock.now += GONE_AFTER + timedelta(seconds=1)
    world.store.remove_gone_slack_installs()
    world.view.make_slack_install()
    world.clock.now += GONE_AFTER * 2
    assert world.store.remove_gone_slack_installs() == []  # pending again is not gone again
    ready(world)
    assert world.view.slack_notes_to_forget() is None


def test_an_install_only_switched_off_is_never_removed_by_this(world):  # noqa: F811
    install = ready(world)
    world.view.switch_slack_install(install.id, on=False)
    world.clock.now += timedelta(days=400)
    assert world.store.remove_gone_slack_installs() == []
    assert world.view.get_slack_install().state == "off"


# Who may


def test_the_new_methods_are_classified_and_the_servers_refuse_a_person(world):  # noqa: F811
    for name in ("claim_slack_message", "release_slack_claim"):
        assert name in reach.MANAGEMENT and name not in reach.OTHER_WORKSPACES
    server = ("take_slack_checks", "slack_token_checked", "remove_gone_slack_installs")
    for name in server:
        assert name in reach.MANAGEMENT and name in reach.OTHER_WORKSPACES
    ready(world)
    for call in (
        lambda v: v.take_slack_checks(),
        lambda v: v.remove_gone_slack_installs(),
    ):
        with pytest.raises(PermissionError):
            call(world.view)
