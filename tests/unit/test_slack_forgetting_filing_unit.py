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
from cairn.store.slack import CLAIMED, FILED, FILING, FORGET_AFTER, SlackClaim

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


def test_a_claim_taken_over_and_filed_meanwhile_stores_nothing_and_says_filed_already(
    bench, monkeypatch
):
    """A filing whose claim was taken over after CLAIM_FOR finds, inside its write, that the
    message is another note's already: the write stores nothing, so a filed message is one
    note for good, and the second person is told it is filed already."""
    bench.link(BROOK_SLACK, bench.brook, bench.library)
    handle(bench, user=BROOK_SLACK)  # the one who took the claim over, and filed
    ((first, *_),) = bench.notes()
    bench.calls.clear()
    other = bench.owner.create_project("inbox")
    bench.link(ADA_SLACK, bench.ada, other)
    taken_over = type(bench.view).claim_slack_message
    monkeypatch.setattr(  # Ada's filing made its claim long ago and still acts on it
        type(bench.view), "claim_slack_message", lambda self, *a, **k: SlackClaim(CLAIMED, None)
    )
    assert replied(bench, handle(bench)).startswith("Already filed")
    monkeypatch.setattr(type(bench.view), "claim_slack_message", taken_over)
    assert [note_id for note_id, *_ in bench.notes()] == [first]  # nothing more stored
    assert rows(bench) == [(first, CONVERSATION, TIME)]


def test_a_message_whose_text_the_project_already_held_is_not_made_slacks_to_forget(bench):
    """The project already holds a note with exactly this text, and it is not one Slack
    created. Filing the message stores nothing new and answers with that note. The message
    is tied to the note, so that it is filed once whatever project is chosen next (D9), but
    the note is not Slack's: removing Slack never forgets a note Slack did not create."""
    bench.link(ADA_SLACK, bench.ada, bench.library)
    handle(bench)
    ((held, *_),) = bench.notes()
    bench.store._con.execute("DELETE FROM slack_notes")  # an ordinary note, as if typed here
    bench.store._con.commit()
    bench.calls.clear()
    assert replied(bench, handle(bench)).startswith("Filed: ")
    assert [note_id for note_id, *_ in bench.notes()] == [held]  # nothing new stored
    tied = bench.store._con.execute(
        "SELECT note_id, conversation_id, message_time, owns, forget_at FROM slack_notes"
    ).fetchall()
    assert tied == [(held, CONVERSATION, TIME, 0, None)]  # tied to the message, not owned
    # the same message again, with another project chosen: answered from the tie, no new note
    other = bench.owner.create_project("inbox")
    bench.link(BROOK_SLACK, bench.brook, other)
    bench.calls.clear()
    assert replied(bench, handle(bench, user=BROOK_SLACK)).startswith("Already filed")
    assert [note_id for note_id, *_ in bench.notes()] == [held]
    bench.owner.remove_slack_install(bench.install.id)
    assert bench.owner.slack_notes_to_forget() is None  # nothing of Slack's to forget
    assert bench.owner.get_note(held) is not None
    assert rows(bench) == []  # the tie went with the install


def test_a_tie_made_after_slack_was_removed_never_makes_the_note_due(bench):
    """A filing is in hand when Slack is removed: its claim takes the removal's day, as a
    note Slack creates would. It then finishes on a note the project already held. The
    tie owns nothing, so it must not keep that day: nothing ever forgets the note."""
    bench.link(ADA_SLACK, bench.ada, bench.library)
    handle(bench)
    ((held, *_),) = bench.notes()
    bench.store._con.execute("DELETE FROM slack_notes")  # an ordinary note, as if typed here
    bench.store._con.commit()
    assert claim(bench) == (CLAIMED, None)  # the second filing, in hand
    bench.owner.remove_slack_install(bench.install.id)
    (due,) = bench.store._con.execute("SELECT forget_at FROM slack_notes").fetchone()
    assert due is not None  # the claim took the removal's day
    bench.view.record_slack_note(
        bench.install.id, held, how="filed", brought_by=bench.ada, conversation_id=CONVERSATION,
        message_time=TIME, owns=False,
    )  # fmt: skip
    assert bench.store._con.execute(
        "SELECT note_id, owns, forget_at FROM slack_notes"
    ).fetchall() == [(held, 0, None)]
    assert bench.owner.slack_notes_to_forget() is None
    bench.clock.now += FORGET_AFTER * 2
    assert bench.store.slack_notes_due() == []
    assert bench.owner.forget_slack_notes_now(bench.install.id) == 0
    assert bench.owner.get_note(held) is not None


def test_a_tie_is_never_due_even_if_a_day_is_on_its_row(bench):
    """Belt and braces: whatever wrote a day onto a tie's row, no query that drives
    forgetting takes a row that owns nothing."""
    bench.link(ADA_SLACK, bench.ada, bench.library)
    handle(bench)
    ((held, *_),) = bench.notes()
    bench.owner.remove_slack_install(bench.install.id)
    bench.store._con.execute("UPDATE slack_notes SET owns = 0")  # the day stays on the row
    bench.store._con.commit()
    bench.clock.now += FORGET_AFTER * 2
    assert bench.owner.slack_notes_to_forget() is None
    assert bench.store.slack_notes_due() == []
    assert bench.owner.forget_slack_notes_now(bench.install.id) == 0
    assert bench.owner.get_note(held) is not None


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
