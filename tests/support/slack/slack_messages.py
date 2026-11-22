"""Shared test helpers extracted from test_slack_messages_unit."""

import asyncio
import json

import pytest

from cairn.slack import dispatch
from support.slack.slack_commands import ADA_SLACK, Bench

DIRECT = "D0ADA0001"


@pytest.fixture
def bench(tmp_path):
    made = Bench(tmp_path)
    yield made
    made.store.close()


def event_body(text: str, **fields) -> bytes:
    event = {"type": "message", "channel": DIRECT, "channel_type": "im", "user": ADA_SLACK,
             "text": text, "ts": "1900000001.000200", **fields}  # fmt: skip
    return json.dumps(
        {"type": "event_callback", "team_id": "T0FABLE01", "event": event, "event_id": "Ev0A1"}
    ).encode()


def handle(bench: Bench, text: str, **fields):
    """The response to the event, with its background task not yet run."""
    found = bench.envelope("events", event_body(text, **fields))
    return asyncio.run(dispatch.dispatch(found, bench.context))
