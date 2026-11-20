"""Sentences of the connect and security guides on what an agent reads (review 8.1, 8.3, 8.5;
9.2 and the Slack half of 9.4), each pinned to what cairn does. Real store in a temporary
file, made-up names."""

import asyncio
from datetime import UTC, datetime

import pytest
from guide_source import guide_source
from test_slack_commands_unit import ADA_SLACK, Bench, ask
from test_slack_shortcuts_unit import CEDAR, cedar, handle

from cairn.compiler import provenance_gate
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Store
from cairn.web.mcp import build_mcp

WHEN = datetime(2099, 3, 2, 9, 0, tzinfo=UTC)


def flat(text: str) -> str:
    return " ".join(text.split())


def says(guide: str, *sentences: str) -> None:
    text = flat(guide_source(guide))
    for sentence in sentences:
        assert flat(sentence) in text, sentence


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db", clock=lambda: WHEN)
    yield s
    s.close()


# connect.md: the tools


def test_the_tools_are_the_ones_the_server_lists():
    says(
        "connect",
        "The tools are `projects`, `new_project`, `note`, `recall`, `page`, `evidence`,"
        " `overview`, `graph` and `related`.",
    )
    names = [tool.name for tool in asyncio.run(build_mcp().list_tools())]
    assert names == [
        "projects", "new_project", "note", "recall", "page", "evidence", "overview", "graph",
        "related",
    ]  # fmt: skip


def test_the_graph_tool_is_the_resource_for_clients_that_only_call_tools():
    says(
        "connect",
        "A client that only calls tools calls `graph` with the project and a limit (20 when"
        " left out) and gets the same answer.",
    )
    tools = {tool.name: tool for tool in asyncio.run(build_mcp().list_tools())}
    schema = tools["graph"].input_schema
    assert schema["required"] == ["project"] and schema["properties"]["limit"]["default"] == 20


# security.md: who wrote what an agent reads, and when


def test_each_hit_claim_and_passage_names_its_writer_and_when(store):
    says(
        "security",
        "Each hit of `recall`, each claim of `page` and each passage of `evidence` says who"
        " wrote the notes behind it",
        "Each also says when its note was written (`written_at`); a hit or a claim that cites"
        " several notes, when the newest was.",
    )
    project = store.create_project("ops")
    view = store.acting_as("session", "Cy <cy@example.test>")
    note, _ = view.add_note(project.id, "# Queue\n\nUse the blue queue.\n")
    memory = Memory(store, lint_gate)
    memory._file(note, record_errors=True)
    (hit,) = memory.recall("blue queue")["hits"]
    (claim,) = memory.page("ops", hit["page"])["claims"]
    (passage,) = memory.evidence("ops", hit["page"], claim["id"])["passages"]
    assert [(e["kind"], e["name"]) for e in claim["written_by"]] == [("person", "Cy")]
    assert hit["written_at"] == claim["written_at"] == passage["written_at"] == note.created_at


@pytest.fixture
def bench(tmp_path):
    made = Bench(tmp_path)
    made.users[CEDAR] = cedar()
    made.link(ADA_SLACK, made.ada, made.library)
    yield made
    made.store.close()


def test_a_message_filed_from_slack_is_outside_and_a_typed_note_is_not(bench):
    says(
        "security",
        "In what `recall`, `page` and `evidence` answer, a message filed from Slack is marked"
        " `outside` too: its writer is the person who filed it, its `filed_message` says so,"
        " and its words are the message's author's, whom the note's heading names. A note"
        " typed with the Slack command is the person's own.",
    )
    handle(bench)  # Ada files Cedar's message
    ask(bench, "note Shelf nine holds the atlases.")
    memory = Memory(bench.owner, provenance_gate)
    (filed,) = [h for h in memory.recall("row")["hits"] if h["text"] == "Row 4."]
    (typed,) = memory.recall("atlases")["hits"]
    assert filed["outside"] is True and "filed_message" in filed["written_by"][0]
    assert typed["outside"] is False and "filed_message" not in typed["written_by"][0]
    (row_note,) = [row for row in bench.notes() if "Row 4." in row[2]]
    assert row_note[2].startswith("## Cedar Quill")
