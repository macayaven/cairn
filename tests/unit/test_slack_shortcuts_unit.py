"""The message shortcut "File in cairn" (cairn.slack.shortcuts), its handler called as
cairn.slack.dispatch calls it: the author asked of Slack once, within its time, the answers
kept by the clock, the note written before the answer and the reply sent after it. Every name
is made up."""

import asyncio
import json
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from test_slack_commands_unit import ADA_SLACK, REPLY, START, TEAM, Bench, said

from cairn.slack import dispatch, people, shortcuts
from cairn.slack.client import SlackClient

CEDAR = "U0CEDAR01"
CONVERSATION = "C0LIBRARY1"


def cedar(**marks) -> dict:
    return {"id": CEDAR, "team_id": TEAM, "real_name": "Cedar Quill", "is_bot": False,
            "profile": {"real_name": "Cedar Quill", "display_name": "Cedar"}, **marks}  # fmt: skip


@pytest.fixture
def bench(tmp_path):
    made = Bench(tmp_path)
    made.users[CEDAR] = cedar()
    yield made
    made.store.close()


def payload(message: dict | None = None, user: str = ADA_SLACK, **fields) -> bytes:
    if message is None:
        message = {"type": "message", "user": CEDAR, "ts": "1900000000.000100", "text": "Row 4."}
    body = {
        "type": "message_action", "callback_id": "file_in_cairn",
        "team": {"id": TEAM, "domain": "fable-works"}, "user": {"id": user},
        "channel": {"id": CONVERSATION}, "message": message, "response_url": REPLY, **fields,
    }  # fmt: skip
    return urlencode({"payload": json.dumps(body)}).encode()


def handle(bench: Bench, message: dict | None = None, **fields):
    found = bench.envelope("actions", payload(message, **fields))
    return asyncio.run(dispatch.dispatch(found, bench.context))


def replied(bench: Bench, response) -> str:
    assert response.status_code == 200 and response.body == b""
    asyncio.run(response.background())
    (body,) = [body for where, body in bench.calls if where == REPLY]
    assert body["text"] in said(body)
    return body["text"]  # the plain fallback: what each block says, once


def test_the_shortcut_is_this_modules():
    assert dispatch.ACTIONS["file_in_cairn"].__module__ == shortcuts.__name__


def test_the_note_is_written_before_the_answer_and_the_reply_follows(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    response = handle(bench)
    ((note_id, project_id, text, via),) = bench.notes()
    assert project_id == bench.library.id and via == "slack"
    assert text.startswith("## Cedar Quill · 2030-03-17 17:46:40 UTC\n\nRow 4.")
    filed = bench.store._con.execute(
        "SELECT how, conversation_id, message_time FROM slack_notes WHERE note_id = ?", (note_id,)
    ).fetchall()
    assert filed == [("filed", CONVERSATION, "1900000000.000100")]
    assert [where for where, _ in bench.calls] == ["users.info"]  # no reply yet
    assert replied(bench, response).startswith("Filed: ")


def test_slack_is_asked_who_the_author_is_once_within_its_time(bench, monkeypatch):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    asked = []
    real = SlackClient.user

    async def spying(self, token, user_id, *, within=None):
        asked.append((user_id, within))
        return await real(self, token, user_id, within=within)

    monkeypatch.setattr(SlackClient, "user", spying)
    handle(bench)
    assert asked == [(CEDAR, people.AUTHOR_WITHIN)] and people.AUTHOR_WITHIN == 1.5


def test_a_known_author_is_not_asked_again_until_the_time_is_up(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    for n, later in enumerate((timedelta(0), people.KNOWN_FOR - timedelta(seconds=1))):
        bench.clock.now += later
        message = {"type": "message", "user": CEDAR, "ts": f"190000000{n}.000100", "text": "Row."}
        handle(bench, message)
    assert [where for where, _ in bench.calls] == ["users.info"]
    bench.clock.now += timedelta(seconds=1)
    handle(bench, {"type": "message", "user": CEDAR, "ts": "1900000009.000100", "text": "Row."})
    assert [where for where, _ in bench.calls] == ["users.info", "users.info"]


def test_a_refused_author_is_not_kept(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    bench.users[CEDAR] = cedar(is_stranger=True)
    assert people.OTHER_ORGANISATION in replied(bench, handle(bench))
    bench.users[CEDAR] = cedar()
    bench.calls.clear()
    assert replied(bench, handle(bench)).startswith("Filed: ")
    assert len(people.AUTHORS) == 1


def test_the_answers_kept_are_bounded_the_oldest_going_first():
    authors = people.Authors(most=2, keep=timedelta(minutes=1))
    now = START
    for author in ("U1", "U2", "U3"):
        authors.remember("sl_000000000001", author, author.lower(), now)
    assert authors.get("sl_000000000001", "U1", now) is None
    assert authors.get("sl_000000000001", "U3", now) == ("u3",)
    assert authors.get("sl_000000000002", "U3", now) is None  # per install
    assert authors.get("sl_000000000001", "U3", now + timedelta(minutes=1)) is None


@pytest.mark.parametrize(
    "marks, reason",
    [
        ({"team_id": "T0QUILL01"}, people.OTHER_ORGANISATION),
        ({"is_stranger": True}, people.OTHER_ORGANISATION),
        ({"team_id": None}, people.OTHER_ORGANISATION),
        ({"is_bot": True}, people.NO_PERSON),
        ({"deleted": True}, people.NO_PERSON),
    ],
)
def test_an_author_not_shown_to_be_of_the_team_is_refused_and_nothing_filed(bench, marks, reason):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    bench.users[CEDAR] = cedar(**marks)
    assert said_reason(bench) == reason


def said_reason(bench: Bench, message: dict | None = None) -> str:
    text = replied(bench, handle(bench, message))
    assert bench.notes() == []
    assert text.startswith("Cannot do that: ")
    return text.removeprefix("Cannot do that: ")


def test_no_text_is_refused_before_slack_is_asked(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    message = {"type": "message", "user": CEDAR, "ts": "1900000000.000100", "text": " "}
    assert said_reason(bench, message) == people.NO_TEXT
    assert [where for where, _ in bench.calls] == [REPLY]


def test_no_project_is_refused_before_slack_is_asked(bench):
    bench.link(ADA_SLACK, bench.ada)
    assert said_reason(bench) == people.NO_PROJECT.format(command="/cairn").replace(
        "<name>", "&lt;name&gt;"
    )
    assert [where for where, _ in bench.calls] == [REPLY]


def test_a_message_with_no_time_or_conversation_is_refused(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    message = {"type": "message", "user": CEDAR, "text": "Row 4."}
    assert said_reason(bench, message) == people.NOT_A_MESSAGE
    bench.calls.clear()
    assert (
        replied(bench, handle(bench, None, channel={"id": "not a conversation"}))
        == "Cannot do that: " + people.NOT_A_MESSAGE
    )


def test_a_message_filed_already_is_answered_before_slack_is_asked(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    handle(bench)
    bench.calls.clear()
    people.AUTHORS.clear()
    text = replied(bench, handle(bench))
    assert text.startswith("Already filed: ") and [where for where, _ in bench.calls] == [REPLY]


def test_an_unlinked_user_is_replied_the_one_time_address(bench):
    text = replied(bench, handle(bench))
    assert "/integrations/slack/link?t=" in text and bench.notes() == []
    assert [where for where, _ in bench.calls] == [REPLY]


def test_a_payload_without_a_reply_address_is_acknowledged_and_nothing_done(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    response = handle(bench, response_url=None)
    assert response.status_code == 200 and response.background is None
    assert bench.notes() == [] and bench.calls == []


def test_a_persons_message_of_another_subtype_is_filed(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    message = {"type": "message", "subtype": "file_share", "user": CEDAR,
               "ts": "1900000000.000100", "text": "Row 4."}  # fmt: skip
    assert replied(bench, handle(bench, message)).startswith("Filed: ")
