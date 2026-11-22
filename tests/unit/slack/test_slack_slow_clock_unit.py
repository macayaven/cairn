"""A delayed first uninstall delivery with the stand-in and server on separate clocks."""

from datetime import datetime, timedelta

import pytest
from starlette.testclient import TestClient

from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.web.slack import Slack
from support.guides.guide_facts_slack import PRIVACY, RUN, says
from support.slack.slack_commands import BASE
from support.slack.slack_standin import fixture_module
from support.slack.slack_store import KEY, TOKEN, a_note, ready, world  # noqa: F401


@pytest.mark.parametrize(
    ("lag_ms", "sent_ms", "setup_ms", "setup_second", "status", "action"),
    [
        pytest.param(2000, 10_000, 11_000, 9, 200, "slack.install_removed", id="reviewer"),
        pytest.param(500, 10_000, 10_250, 9, 200, "slack.install_removed", id="subsecond-earlier"),
        pytest.param(500, 10_750, 11_000, 10, 200, "slack.lifecycle_ignored", id="subsecond-tie"),
        pytest.param(302_000, 10_000, 11_000, -291, 200, "slack.install_removed", id="at-window"),
        pytest.param(302_001, 10_000, 11_000, -292, 401, None, id="past-window"),
    ],
)
def test_privacy_and_run_slack_removal_with_a_slow_clock(
    world,  # noqa: F811
    monkeypatch,
    lag_ms,
    sent_ms,
    setup_ms,
    setup_second,
    status,
    action,
):
    """privacy.md and run.md describe when a slow clock lets an earlier uninstall through."""
    start = slack_now = world.clock.now
    slack = fixture_module().SlackStandIn(clock=lambda: slack_now.timestamp())
    lag = timedelta(milliseconds=lag_ms)

    def advance(milliseconds):
        nonlocal slack_now
        slack_now = start + timedelta(milliseconds=milliseconds)
        world.clock.now = slack_now - lag

    def hold(_url, body, *, retry_num=None):
        return body, slack.signed_headers(body)

    def set_up(token):
        return ready(world, team=slack.team_id, secret=slack.signing_secret, token=token)

    def no_http():
        raise AssertionError("unexpected outbound HTTP")

    monkeypatch.setattr(slack, "_send", hold)
    advance(0)
    previous = set_up(TOKEN)
    world.store.slack_install_verified(previous.id)
    url = f"/integrations/slack/{previous.id}/events"

    # Hold the original signed request until its first delivery at :12.
    advance(sent_ms)
    body, headers = slack.send_event(url, {"type": "app_uninstalled"})
    advance(setup_ms)
    world.view.remove_slack_install(previous.id)
    made = set_up(slack.bot_token)
    replacement = world.store.slack_install_verified(made.id)
    assert replacement.id == previous.id and replacement.team_id == previous.team_id
    assert replacement.state == "on" and replacement.holds_secrets
    assert int(datetime.fromisoformat(replacement.made_at).timestamp()) == (
        int(start.timestamp()) + setup_second
    )
    world.view.link_slack_user(replacement.id, slack.user_id, world.owner)
    typed, filed = a_note(world), a_note(world, "# Moon\n\nThe moon is full.\n")
    world.view.record_slack_note(replacement.id, typed, how="typed", brought_by=world.owner)
    world.view.record_slack_note(
        replacement.id, filed, how="filed", brought_by=world.owner,
        conversation_id=slack.conversation_id, message_time="1900000000.000100",
    )  # fmt: skip
    assert world.view.slack_notes_to_forget() is None
    prior = world.view.list_audit(limit=1)[0].n

    app = create_app(
        world.store,
        public_url=BASE,
        sign_in=SignIn(providers=(GitHub("gh-id", "gh-secret"),), http=no_http),
        clock=world.clock,
        slack=Slack(key=KEY, http=no_http),
    )
    client = TestClient(app, base_url=BASE)
    advance(12_000)
    reply = client.post(url, content=body, headers=headers)
    assert reply.status_code == status

    audit = [event for event in world.view.list_audit() if event.n > prior]
    assert [(event.action, event.actor_kind) for event in audit] == (
        [(action, "system")] if action is not None else []
    )
    after = world.view.get_slack_install()
    if action == "slack.install_removed":
        assert after.state == "removed" and not after.holds_secrets
        assert world.view.slack_person(replacement.id, slack.user_id) is None
        assert audit[0].detail == {"install": replacement.id, "notes": 2}
        due = world.view.slack_notes_to_forget()
        assert (due.notes, due.at) == (2, world.clock.now + timedelta(days=10))
        assert all(world.view.get_note(note) is not None for note in (typed, filed))

        # Correct the clock and set up the same Slack workspace just before the deadline.
        world.clock.now = slack_now = due.at - timedelta(seconds=1)
        restored = set_up(slack.bot_token)
        assert restored.id == replacement.id
        challenge, challenge_headers = slack.verify_url(url)
        verified = client.post(url, content=challenge, headers=challenge_headers)
        assert verified.status_code == 200
        assert world.view.get_slack_install().state == "on"
        world.view.link_slack_user(restored.id, slack.user_id, world.owner)
        assert world.view.slack_person(restored.id, slack.user_id) is not None
        assert world.view.slack_notes_to_forget() is None
        world.clock.now = slack_now = due.at + timedelta(seconds=1)
        _, forgotten = app.state.slack_keeper.run_once()
        assert forgotten == 0
        assert all(world.view.get_note(note) is not None for note in (typed, filed))
    else:
        if action == "slack.lifecycle_ignored":
            assert audit[0].detail == {
                "install": replacement.id,
                "event": "app_uninstalled",
                "why": "at_or_before_setup",
            }
        assert after == replacement
        assert world.view.slack_person(replacement.id, slack.user_id) is not None
        assert world.view.slack_notes_to_forget() is None

    says(
        *PRIVACY,
        "A slow server clock can let an earlier installation's uninstall remove a replacement"
        " of the same Slack app with the same signing secret when the replacement was set up"
        " within that clock difference after dispatch and stamped in an earlier second than"
        " Slack's dispatch time.",
        "The workspace audit log records `slack.install_removed` with the installation ID and"
        " the number of notes newly scheduled for forgetting.",
        "Once the clock is accurate, set Slack up again for the same Slack workspace and"
        " relink people.",
        "Setting it up before the 10-day deadline keeps the notes Slack created.",
    )
    says(
        *RUN,
        "A slow clock can let an earlier uninstall remove a replacement of the same app using"
        " the same signing secret if the server stamps set-up before Slack's dispatch second"
        " and the signed request timestamp is within 300 seconds of the server's time on receipt.",
    )
