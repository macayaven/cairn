"""The file-message shortcut keeps the directory author's heading on every stored passage."""

import pytest

import support.slack.slack_shortcuts as shortcuts
from support.slack.slack_commands import ADA_SLACK

slack_bench = shortcuts.bench
AUTHOR = "Cedar Quill · 2030-03-17 17:46:40 UTC"
BREAKS = [
    pytest.param("\n", id="lf"),
    pytest.param("\r", id="cr"),
    pytest.param("\r\n", id="crlf"),
    pytest.param("\v", id="vt"),
    pytest.param("\f", id="ff"),
    pytest.param("\x1c", id="fs"),
    pytest.param("\x1d", id="gs"),
    pytest.param("\x1e", id="rs"),
    pytest.param("\x85", id="nel"),
    pytest.param("\u2028", id="line-separator"),
    pytest.param("\u2029", id="paragraph-separator"),
]


def file_passages(bench, text):
    bench.link(ADA_SLACK, bench.ada, bench.library)
    response = shortcuts.handle(
        bench, {"type": "message", "user": shortcuts.CEDAR, "ts": "1900000000.000100", "text": text}
    )
    assert response.status_code == 200
    ((note_id, project_id, _, via),) = bench.notes()
    assert (project_id, via) == (bench.library.id, "slack")
    return bench.owner.note_spans(note_id)


@pytest.mark.parametrize("boundary", BREAKS)
def test_each_line_boundary_preserves_the_author_and_the_passages(slack_bench, boundary):
    opening, heading, evidence = "First entry.", "## Field notes", "The ledger is blue."
    spans = file_passages(slack_bench, (boundary * 2).join((opening, heading, evidence)))
    assert all(AUTHOR in span.heading_path for span in spans), [s.heading_path for s in spans]
    # Dropping a control byte also keeps the heading but glues two passages together.
    assert {opening, evidence} <= {s.text for s in spans}
    assert any("Field notes" in s.text for s in spans)


def test_heading_from_a_slack_reference_keeps_the_author(slack_bench):
    spans = file_passages(slack_bench, "First entry.\u2028<#C456| shelf>\u2028The ledger is blue.")
    assert any("The ledger is blue." in s.text for s in spans)
    assert all(AUTHOR in span.heading_path for span in spans), [s.heading_path for s in spans]


@pytest.mark.parametrize("space", ["\t", "\x1f", "\u00a0"], ids=["tab", "unit-separator", "nbsp"])
def test_other_whitespace_stays_inside_a_line(slack_bench, space):
    spans = file_passages(slack_bench, f"First entry.{space}## Field notes{space}on this shelf.")
    assert all(AUTHOR in span.heading_path for span in spans)
    assert any(
        "First entry." in line and "on this shelf." in line
        for span in spans
        for line in span.text.splitlines()
    )
