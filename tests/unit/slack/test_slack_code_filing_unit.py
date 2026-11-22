"""Read the shortcut's stored note with both the passage splitter and CommonMark."""

import pytest

import support.slack.slack_shortcuts as shortcuts
from support.slack.reviewer6_slack_code import AUTHOR, READER
from support.slack.slack_commands import ADA_SLACK

slack_bench = shortcuts.bench


def filed(bench, text):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    response = shortcuts.handle(
        bench, {"type": "message", "user": shortcuts.CEDAR, "ts": "1900000000.000100", "text": text}
    )
    assert response.status_code == 200
    ((note_id, project, note, via),) = bench.notes()
    assert (project, via) == (bench.library.id, "slack")
    spans = bench.owner.note_spans(note_id)
    assert [s.heading_path for s in spans if s.kind == "heading"] == [(AUTHOR,)]
    assert {s.heading_path for s in spans} == {(AUTHOR,)}
    tokens = READER.parse(note)
    assert [
        (t.map, tokens[i + 1].content) for i, t in enumerate(tokens) if t.type == "heading_open"
    ] == [([0, 1], AUTHOR)]
    return note, spans, tokens


@pytest.mark.parametrize(
    ("lead", "mark", "pad"),
    [
        ("# Settings\n\n", "---", "    "),
        ("# Settings\n\nA paragraph.\n\n", "---", "    "),
        ("## Settings\n\nA paragraph.\n\n", "===", "\t"),
    ],
    ids=["below-heading", "below-paragraph", "tab-and-equals"],
)
def test_literal_indented_code_keeps_its_bytes(slack_bench, lead, mark, pad):
    lines = f"{pad}{mark}\n{pad}port: 8811"
    note, _, tokens = filed(slack_bench, f"{lead}{lines}\n\nRow 4.")
    assert [t.content for t in tokens if t.type == "code_block"] == [f"{mark}\nport: 8811\n"]
    assert f"\n\n{lines}\n\n" in note


@pytest.mark.parametrize(
    "br",
    ["\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"],
    ids=["lf", "cr", "crlf", "vt", "ff", "fs", "gs", "rs", "nel", "line-sep", "para-sep"],
)
def test_line_boundaries_keep_the_author_and_separate_passages(slack_bench, br):
    _, spans, _ = filed(
        slack_bench, (br * 2).join(("First entry.", "## Field notes", "The ledger is blue."))
    )
    assert {"First entry.", "The ledger is blue."} <= {s.text for s in spans}
    assert any("Field notes" in s.text for s in spans)


def test_heading_from_a_reference_keeps_the_author(slack_bench):
    _, spans, _ = filed(slack_bench, "First entry.\u2028<#C456| shelf>\u2028The ledger is blue.")
    assert any("The ledger is blue." in s.text for s in spans)


@pytest.mark.parametrize("space", ["\t", "\x1f", "\u00a0"], ids=["tab", "unit-sep", "nbsp"])
def test_other_whitespace_keeps_words_on_one_line(slack_bench, space):
    _, spans, _ = filed(slack_bench, f"First entry.{space}## Field notes{space}on this shelf.")
    assert any(
        "First entry." in line and "on this shelf." in line
        for s in spans
        for line in s.text.splitlines()
    )


@pytest.mark.parametrize(
    "message",
    ["Title\n---\n---", "- Title\n  ---", "- Title\n# \n     # Other", "&gt; Title\n&gt; ==="],
    ids=["setext-run", "list-underline", "list-heading", "quoted-underline"],
)
def test_contextual_headings_stay_under_the_author(slack_bench, message):
    note, _, _ = filed(slack_bench, f"{message}\nRow 4.")
    assert "Title" in note and "Row 4." in note


def test_fenced_code_next_to_a_real_underline_keeps_its_bytes(slack_bench):
    block = "```\n---\nport: 8811\n===\n```"
    note, _, tokens = filed(slack_bench, f"# Settings\n{block}\n---\nRow 4.")
    assert block in note
    assert [t.content for t in tokens if t.type == "fence"] == ["---\nport: 8811\n===\n"]
