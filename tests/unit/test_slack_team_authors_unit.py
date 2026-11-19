"""What is kept about a message's author (cairn.slack.people.AUTHORS) is kept for the Slack
team too: a decision made for one team's author is never reused for another team under the
same install id. Every name is made up."""

import dataclasses
from datetime import timedelta

import pytest
from test_slack_commands_unit import ADA_SLACK, REPLY, START, Bench
from test_slack_shortcuts_unit import CEDAR, cedar, handle, replied

from cairn.slack import people

OTHER_TEAM = "T0QUILL01"


@pytest.fixture
def bench(tmp_path):
    made = Bench(tmp_path)
    made.users[CEDAR] = cedar()
    yield made
    made.store.close()


def test_an_author_known_for_one_team_is_asked_again_for_another_under_the_same_install(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    assert replied(bench, handle(bench)).startswith("Filed: ")
    assert len(people.AUTHORS) == 1
    bench.calls.clear()
    # the same install id, now holding another team (set up again after a removal)
    bench.context = dataclasses.replace(
        bench.context, install=dataclasses.replace(bench.install, team_id=OTHER_TEAM)
    )
    message = {"type": "message", "user": CEDAR, "ts": "1900000005.000100", "text": "Row 5."}
    text = replied(bench, handle(bench, message))
    assert [where for where, _ in bench.calls] == ["users.info", REPLY]  # asked again
    assert people.OTHER_ORGANISATION in text  # Cedar is of the first team, not this one
    assert len(bench.notes()) == 1


def test_the_answers_are_kept_by_install_team_and_author():
    authors = people.Authors(most=4, keep=timedelta(minutes=1))
    authors.remember("sl_000000000001", "T0FABLE01", "U1", "u1", START)
    assert authors.get("sl_000000000001", "T0FABLE01", "U1", START) == ("u1",)
    assert authors.get("sl_000000000001", OTHER_TEAM, "U1", START) is None
    assert authors.get("sl_000000000002", "T0FABLE01", "U1", START) is None
