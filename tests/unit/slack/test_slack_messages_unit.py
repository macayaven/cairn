"""A direct message to the bot (cairn.slack.messages), its handler called as
cairn.slack.dispatch calls it: what is acted on, what is written before the answer, and the
reply sent after it. Every name is made up."""

import asyncio

import httpx
import pytest

from cairn.slack import blocks, dispatch, messages, people
from cairn.slack.client import SlackClient
from support.slack.slack_commands import ADA_SLACK, BOT, said
from support.slack.slack_messages import DIRECT, handle
from support.slack.slack_messages import bench as bench


def test_the_message_event_is_this_modules():
    assert dispatch.EVENTS["message"].__module__ == messages.__name__


@pytest.mark.parametrize(
    "fields",
    [
        {"user": BOT},
        {"bot_id": "B0HELPER01"},
        {"subtype": "message_changed"},
        {"subtype": "message_deleted"},
        {"subtype": "file_share"},
        {"channel_type": "channel"},
        {"channel_type": "mpim"},
        {"user": None},
    ],
)
def test_only_a_persons_own_new_message_in_a_direct_conversation_is_acted_on(bench, fields):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    response = handle(bench, "note: Row 4.", **fields)
    assert response.status_code == 200 and response.body == b"" and response.background is None
    assert bench.notes() == [] and bench.calls == []


def test_a_note_is_written_before_the_answer_and_the_reply_is_posted_after_it(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    response = handle(bench, "  NOTE:  Row 4 holds the quince jars. ")
    assert response.status_code == 200 and response.body == b""
    ((note_id, _, text, via),) = bench.notes()
    assert (text, via) == ("Row 4 holds the quince jars.", "slack")
    how = bench.store._con.execute("SELECT how FROM slack_notes WHERE note_id = ?", (note_id,))
    assert how.fetchall() == [("typed",)]
    assert bench.calls == []  # nothing is sent before the answer has left
    asyncio.run(response.background())
    ((where, body),) = bench.calls
    assert where == "chat.postMessage" and body["channel"] == DIRECT
    assert said(body).startswith("Filed: ")


def test_plain_words_are_a_search_and_help_lists_what_there_is(bench):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    asyncio.run(handle(bench, "quince   jars").background())
    asyncio.run(handle(bench, " Help ").background())
    (_, searched), (_, helped) = bench.calls
    assert said(searched) == said(blocks.nothing_found("quince jars"))
    assert said(helped) == said(blocks.help_text("/cairn"))


def test_an_unlinked_user_is_posted_the_one_time_address_and_help_without_it(bench):
    asyncio.run(handle(bench, "note: Row 4.").background())
    asyncio.run(handle(bench, "help").background())
    (_, prompt), (_, helped) = bench.calls
    assert "/integrations/slack/link?t=" in said(prompt)
    assert said(helped) == said(blocks.help_text("/cairn"))
    assert bench.notes() == []


def test_the_reply_is_sent_once_with_no_wait_and_a_failure_is_logged_by_its_class(bench, caplog):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    bench.answers["chat.postMessage"] = httpx.Response(429, headers={"retry-after": "30"})
    asyncio.run(handle(bench, "note: The fig tree leans east.").background())
    assert len(bench.calls) == 1 and len(bench.notes()) == 1
    (record,) = [r for r in caplog.records if r.name == "cairn.slack"]
    assert record.getMessage() == "slack: a reply was not sent: SlackError"


def test_the_reply_waits_at_most_its_time(bench, monkeypatch):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    asked = []

    async def call(self, method, token, fields=None, *, within=None):
        asked.append((method, within))
        return {"ok": True}

    monkeypatch.setattr(SlackClient, "call", call)
    asyncio.run(handle(bench, "fig").background())
    assert asked == [("chat.postMessage", people.REPLY_WITHIN)]
