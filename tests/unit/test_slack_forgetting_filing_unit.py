"""Filing a message claims it first (cairn.slack.shortcuts, cairn.store.slack): the claim
comes before Slack is asked who wrote it and before anything is written, a claim held by
someone else is answered "being filed right now", and every way a filing ends without a note
stored releases the claim, so the message can be filed again. Every name is made up."""

import asyncio
import contextlib
import dataclasses

import pytest
from test_slack_commands_unit import ADA_SLACK, BROOK_SLACK, Bench
from test_slack_shortcuts_unit import CEDAR, CONVERSATION, cedar, handle, replied

from cairn.slack import people
from cairn.store.slack import CLAIMED, FILED, FILING

TIME = "1900000000.000100"
LOOKS_SECRET = "ghp_" + "Fable" * 7 + "0"  # the note guard's github_token rule


@pytest.fixture
def bench(tmp_path):
    made = Bench(tmp_path)
    made.users[CEDAR] = cedar()
    yield made
    made.store.close()


def rows(bench: Bench) -> list[tuple]:
    return bench.store._con.execute(
        "SELECT note_id, conversation_id, message_time FROM slack_notes ORDER BY id"
    ).fetchall()


def claim(bench: Bench):
    return bench.view.claim_slack_message(bench.install.id, CONVERSATION, TIME, brought_by=None)


def test_the_claim_comes_before_the_author_is_asked_and_before_the_write(bench, monkeypatch):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    seen = []
    real_author = people.author

    async def author(context, message):
        seen.append(("author", rows(bench), bench.notes()))
        return await real_author(context, message)

    monkeypatch.setattr(people, "author", author)
    response = handle(bench)
    assert seen == [("author", [(None, CONVERSATION, TIME)], [])]
    ((note_id, *_),) = bench.notes()
    assert rows(bench) == [(note_id, CONVERSATION, TIME)]
    assert replied(bench, response).startswith("Filed: ")


def test_a_message_someone_else_is_filing_is_said_and_nothing_is_asked_or_written(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    assert claim(bench) == (CLAIMED, None)
    response = handle(bench)
    assert replied(bench, response) == people.blocks.refused(people.BEING_FILED)["text"]
    assert [where for where, _ in bench.calls if where == "users.info"] == []
    assert bench.notes() == [] and rows(bench) == [(None, CONVERSATION, TIME)]  # theirs stays


def test_a_message_filed_already_is_answered_with_its_note(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    handle(bench)
    bench.calls.clear()
    bench.link(BROOK_SLACK, bench.brook, bench.library)
    ((note_id, *_),) = bench.notes()
    assert claim(bench) == (FILED, note_id)
    assert replied(bench, handle(bench, user=BROOK_SLACK)).startswith("Already filed")


def refused_then_filed(bench: Bench, response, reason: str) -> None:
    assert reason in replied(bench, response)
    assert bench.notes() == [] and rows(bench) == []  # released
    assert claim(bench) == (CLAIMED, None)  # so it can be filed again
    bench.view.release_slack_claim(bench.install.id, CONVERSATION, TIME)


def test_no_project_chosen_releases_the_claim(bench):
    bench.link(ADA_SLACK, bench.ada)
    refused_then_filed(bench, handle(bench), "No project is chosen")


def test_an_author_not_of_this_team_releases_the_claim(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    bench.users[CEDAR] = cedar(team_id="T0QUILL01")
    refused_then_filed(bench, handle(bench), "not of this Slack workspace")


def test_a_server_too_busy_to_write_releases_the_claim(bench, monkeypatch):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    monkeypatch.setattr(people, "TURN_WITHIN", 0)

    @contextlib.asynccontextmanager
    async def never():
        await asyncio.Event().wait()  # every turn is held
        yield

    bench.context = dataclasses.replace(bench.context, turn=never)
    refused_then_filed(bench, handle(bench), "nothing was stored")


def test_a_write_the_note_guard_refuses_releases_the_claim(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    message = {"type": "message", "user": CEDAR, "ts": TIME, "text": f"Key {LOOKS_SECRET}"}
    refused_then_filed(bench, handle(bench, message), "github_token")


def test_a_handler_that_raises_releases_the_claim(bench, monkeypatch):
    bench.link(ADA_SLACK, bench.ada, bench.library)

    def broken(*args, **kwargs):
        raise RuntimeError("the store went away")

    monkeypatch.setattr(people, "write", broken)
    with pytest.raises(RuntimeError):
        handle(bench)
    assert rows(bench) == [] and claim(bench) == (CLAIMED, None)


def test_the_answer_for_someone_else_filing_names_nothing_of_the_message(bench):
    assert "right now" in people.BEING_FILED and "nothing" in people.BEING_FILED
    assert FILING == "filing"
