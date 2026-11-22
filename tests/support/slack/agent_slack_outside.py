"""Shared test helpers extracted from test_agent_slack_outside_unit."""

import pytest

from cairn.compiler import provenance_gate
from cairn.memory import Memory
from support.slack.slack_commands import ADA_SLACK, Bench
from support.slack.slack_shortcuts import CEDAR, cedar, handle


@pytest.fixture
def bench(tmp_path):
    made = Bench(tmp_path)
    made.users[CEDAR] = cedar()
    made.link(ADA_SLACK, made.ada, made.library)
    yield made
    made.store.close()


def memory(bench: Bench) -> Memory:
    return Memory(bench.owner, provenance_gate)


def filed(bench: Bench) -> str:
    handle(bench)  # Ada files Cedar's message "Row 4."
    ((note_id, *_),) = [row for row in bench.notes() if "Row 4." in row[2]]
    return note_id


def row_hit(bench: Bench) -> dict:
    """The hit of the message's own line (its link is a claim of its own)."""
    (hit,) = [hit for hit in memory(bench).recall("row")["hits"] if hit["text"] == "Row 4."]
    return hit
