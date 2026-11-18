"""A note written from Slack takes its turn among the server's compiles (G1, cairn.web.turns):
the slash command's `note`, a direct message's `note:` and the message shortcut each hold a
turn while the note is written, waiting for one on the event loop with no worker thread, and
for at most people.TURN_WITHIN seconds; past that nothing is stored and the person is told.
Nothing here sleeps or looks on a clock: the test knows the note waits when it joins the
queue, the wait ends when the test lets go of the turn it holds, and the bound is set to 0
to see what happens past it. Every name is made up."""

import asyncio
import dataclasses
import json
from collections import deque
from urllib.parse import urlencode

import pytest
from test_slack_commands_unit import ADA_SLACK, REPLY, TEAM, Bench, command_body, said

from cairn.slack import blocks, commands, dispatch, messages, people, shortcuts
from cairn.web.turns import Compiles

CEDAR = "U0CEDAR01"


@pytest.fixture
def bench(tmp_path):
    made = Bench(tmp_path)
    made.users[CEDAR] = {"id": CEDAR, "team_id": TEAM, "real_name": "Cedar Quill"}
    made.link(ADA_SLACK, made.ada, made.library)
    made.turns = Compiles(1)
    made.context = dataclasses.replace(made.context, turn=made.turns.turn)
    yield made
    made.store.close()


def the_command(bench: Bench):
    return bench.envelope("commands", command_body("note Row 4 holds the quince jars."))


def the_direct_message(bench: Bench):
    event = {"type": "message", "channel": "D0ADA0001", "channel_type": "im", "user": ADA_SLACK,
             "text": "note: Row 4 holds the quince jars.", "ts": "1900000001.000200"}  # fmt: skip
    body = {"type": "event_callback", "team_id": TEAM, "event": event, "event_id": "Ev0T1"}
    return bench.envelope("events", json.dumps(body).encode())


def the_shortcut(bench: Bench):
    message = {"type": "message", "user": CEDAR, "ts": "1900000000.000100", "text": "Row 4."}
    body = {
        "type": "message_action", "callback_id": "file_in_cairn",
        "team": {"id": TEAM, "domain": "fable-works"}, "user": {"id": ADA_SLACK},
        "channel": {"id": "C0LIBRARY1"}, "message": message, "response_url": REPLY,
    }  # fmt: skip
    return bench.envelope("actions", urlencode({"payload": json.dumps(body)}).encode())


WAYS = {"command": the_command, "direct message": the_direct_message, "shortcut": the_shortcut}


class Queue(deque):
    """The Compiles' queue of waiters, which says when one joins it: no look on a clock."""

    def __init__(self) -> None:
        super().__init__()
        self.joined = asyncio.Event()

    def append(self, waiter) -> None:
        super().append(waiter)
        self.joined.set()


async def answered(bench: Bench, found) -> str:
    """What the person is told: the command's answer, or the reply sent after the 200."""
    response = await dispatch.dispatch(found, bench.context)
    if response.background is not None:
        await response.background()
        where = REPLY if found.kind == "actions" else "chat.postMessage"
        (body,) = [body for at, body in bench.calls if at == where]
        return said(body)
    return said(json.loads(response.body))


@pytest.mark.parametrize("way", sorted(WAYS))
def test_a_note_waits_while_every_turn_is_held_and_is_written_once_one_is_free(
    bench, monkeypatch, way
):
    written = []
    real = people.write

    def writing(*args, **kwargs):
        written.append(bench.turns.held)  # the turn it holds while it writes
        return real(*args, **kwargs)

    monkeypatch.setattr(people, "write", writing)

    async def scenario() -> str:
        queue = bench.turns._waiting = Queue()
        holding = bench.turns.turn()
        await holding.__aenter__()  # a free turn is taken without waiting
        answering = asyncio.create_task(answered(bench, WAYS[way](bench)))
        await asyncio.wait_for(queue.joined.wait(), 10)  # bounds a failure, decides nothing
        assert bench.turns.waiting == 1 and written == [] and bench.notes() == []
        assert not answering.done()  # waiting, it holds no worker thread
        await holding.__aexit__(None, None, None)
        return await answering

    told = asyncio.run(scenario())
    assert told.startswith("Filed: ") and len(bench.notes()) == 1
    assert written == [1] and bench.turns.held == 0 and bench.turns.waiting == 0


@pytest.mark.parametrize("way", sorted(WAYS))
def test_a_note_that_finds_no_turn_in_time_is_not_stored_and_the_person_is_told(
    bench, monkeypatch, way
):
    monkeypatch.setattr(people, "TURN_WITHIN", 0)

    async def scenario() -> tuple[str, str]:
        holding = bench.turns.turn()
        await holding.__aenter__()
        refused = await answered(bench, WAYS[way](bench))
        assert (bench.turns.held, bench.turns.waiting) == (1, 0)  # as they were
        assert bench.notes() == []
        await holding.__aexit__(None, None, None)
        bench.calls.clear()
        later = await answered(bench, WAYS[way](bench))  # the next one gets a turn
        return refused, later

    refused, later = asyncio.run(scenario())
    assert refused == said(blocks.refused(people.BUSY))
    assert later.startswith("Filed: ") and len(bench.notes()) == 1
    assert bench.store._con.execute("SELECT COUNT(*) FROM slack_notes").fetchone() == (1,)


def test_the_three_ways_are_the_handlers_of_their_modules():
    assert dispatch.COMMANDS["note"].__module__ == commands.__name__
    assert dispatch.EVENTS["message"].__module__ == messages.__name__
    assert dispatch.ACTIONS["file_in_cairn"].__module__ == shortcuts.__name__


def test_the_busy_sentence_says_nothing_was_stored_and_to_try_again():
    assert "nothing was stored" in people.BUSY and "Try again" in people.BUSY
    assert people.TURN_WITHIN + people.AUTHOR_WITHIN < 3  # inside Slack's three seconds


def test_without_a_turn_handed_in_a_note_is_written_as_before(tmp_path):
    bench = Bench(tmp_path)
    try:
        bench.link(ADA_SLACK, bench.ada, bench.library)
        told = asyncio.run(answered(bench, the_command(bench)))
        assert told.startswith("Filed: ") and len(bench.notes()) == 1
    finally:
        bench.store.close()
