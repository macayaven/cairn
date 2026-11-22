"""Shared test helpers extracted from test_slack_one_heading_unit."""

import pytest
from markdown_it import MarkdownIt

import support.slack.slack_shortcuts as shortcuts
from support.slack.slack_commands import ADA_SLACK, Bench

AUTHOR = "Cedar Quill · 2030-03-17 17:46:40 UTC"

BODY = MarkdownIt("commonmark", {"html": False}).enable("table")  # as the web view reads


def a_bench(tmp_path) -> Bench:
    made = Bench(tmp_path)
    made.users[shortcuts.CEDAR] = shortcuts.cedar()
    made.link(ADA_SLACK, made.ada, made.library)
    return made


@pytest.fixture
def bench(tmp_path):
    made = a_bench(tmp_path)
    yield made
    made.store.close()


def filed(bench: Bench, text: str, nth: int = 0):
    """The note the shortcut stored for a message of `text`, and its passages; None when the
    message was refused."""
    before = {row[0] for row in bench.notes()}
    message = {
        "type": "message", "user": shortcuts.CEDAR, "ts": f"1900000000.{100 + nth:06d}",
        "text": text,
    }  # fmt: skip
    assert shortcuts.handle(bench, message).status_code == 200
    new = {row[0] for row in bench.notes()} - before
    if not new:
        return None
    (note_id,) = new
    return bench.owner.get_note(note_id).text, bench.owner.note_spans(note_id)


def faults(note: str, spans) -> list[str]:
    found = []
    if note.splitlines() != note.split("\n"):
        found.append("a line boundary other than a line feed is left in the note")
    if any(span.heading_path != (AUTHOR,) for span in spans):
        found.append(f"heading paths {sorted({span.heading_path for span in spans})}")
    headings = [token.map for token in BODY.parse(note) if token.type == "heading_open"]
    if headings != [[0, 1]]:
        found.append(f"the parser's headings are at lines {headings}")
    return found


def passes(monkeypatch) -> list[int]:
    """How often the note is read for headings, counted (a cost in readings, never time)."""
    from cairn.slack import text as slack_text

    asked, ask = [], slack_text._headings

    def counted(note: str) -> dict[int, str]:
        asked.append(len(note))
        return ask(note)

    monkeypatch.setattr(slack_text, "_headings", counted)
    return asked
