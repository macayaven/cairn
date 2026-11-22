"""Shared test helpers extracted from test_slack_shortcuts_unit."""

import asyncio
import json
from urllib.parse import urlencode

import pytest

from cairn.slack import dispatch
from support.slack.slack_commands import ADA_SLACK, REPLY, TEAM, Bench, said

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


def said_reason(bench: Bench, message: dict | None = None) -> str:
    text = replied(bench, handle(bench, message))
    assert bench.notes() == []
    assert text.startswith("Cannot do that: ")
    return text.removeprefix("Cannot do that: ")
