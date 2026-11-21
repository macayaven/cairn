"""What is kept about a message's author (cairn.slack.people.AUTHORS) is kept for the Slack
team too: a decision made for one team's author is never reused for another team under the
same install id. Every name is made up."""

import asyncio
import dataclasses
from datetime import timedelta

import pytest
from test_slack_commands_unit import ADA_SLACK, START, Bench, ask, said
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
    # the same install id, later holding another team: what was kept for the first is not used
    other = dataclasses.replace(
        bench.context, install=dataclasses.replace(bench.install, team_id=OTHER_TEAM)
    )
    message = {"type": "message", "user": CEDAR, "ts": "1900000005.000100", "text": "Row 5."}
    with pytest.raises(people.NotFiled) as refused:
        asyncio.run(people.author(other, message))
    assert [where for where, _ in bench.calls] == ["users.info"]  # asked again
    assert str(refused.value) == people.OTHER_ORGANISATION  # Cedar is of the first team
    assert len(bench.notes()) == 1


def test_the_answers_are_kept_by_install_team_and_author():
    authors = people.Authors(most=4, keep=timedelta(minutes=1))
    authors.remember("sl_000000000001", "T0FABLE01", "U1", "u1", START)
    assert authors.get("sl_000000000001", "T0FABLE01", "U1", START) == ("u1",)
    assert authors.get("sl_000000000001", OTHER_TEAM, "U1", START) is None
    assert authors.get("sl_000000000002", "T0FABLE01", "U1", START) is None


def test_a_request_for_a_team_the_install_no_longer_holds_stores_nothing(bench):
    """The request was Slack's for a team that was replaced under the same install id while
    it was in hand: a typed note and a filed message are both refused in one sentence, and
    no note, claim or record is left."""
    bench.link(ADA_SLACK, bench.ada, bench.library)
    bench.context = dataclasses.replace(
        bench.context, install=dataclasses.replace(bench.install, team_id=OTHER_TEAM)
    )
    refused = said(people.blocks.refused(people.TEAM_CHANGED))
    assert said(ask(bench, "note A late thought.")) == refused
    assert people.TEAM_CHANGED in replied(bench, handle(bench))
    assert [where for where, _ in bench.calls if where == "users.info"] == []  # not even asked
    assert bench.notes() == []
    assert bench.store._con.execute("SELECT COUNT(*) FROM slack_notes").fetchone() == (0,)
