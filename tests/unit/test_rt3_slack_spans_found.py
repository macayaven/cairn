"""What the spans harness (test_rt3_slack_spans_held) found on release/0.1.0 at 6fb9ee9b, one
step past the fix of the second reviewer's seventh report (must-fix 3), worst first. Each is
pinned as failing (strict), so the file passes as committed and a fix removes the mark.

Placement (tests 1, 3, 4): _known_code places a span the rich text names at the first place
in the text where a backtick, its code and a backtick stand, after the span before. For a span
of backticks alone that place can start one or two backticks early: on the closing backtick
of a span the rich text does not name, on an empty span's two backticks, on a backtick Slack
displayed as a character. With the span placed where it is (_known_code replaced by the true
places, in a scratch run), tests 1, 3 and 4 pass. While the held harness keeps its 1,200
messages, its grammar makes 68 placed so (it leaves them out), each a span the text bounds
before a named one; 47 of the 68 show a page that differs from what Slack displayed.

Reading (test 2): backticks the rich text gives as its own text, displayed as characters,
are read as code where they pair; the rich text's text is not read. Placing spans right
does not clear it.

Blanks (test 5): a span of blanks alone right after another, at the end of a line, is written
as its plain characters (the guide says so), and a page shows nothing of them.

What these rest on, beyond Slack's documents: Slack's page escapes only "&", "<" and ">" in a
message's text, so a backtick displayed as a character is assumed sent bare; and a span the
rich text does not name (an element of a shape cairn does not read) is assumed displayed as
code, as the model reads the text's backticks. Every message is made up."""

import pytest
from markdown_it import MarkdownIt

from cairn.slack.text import note_from_message

READER = MarkdownIt("commonmark", {"html": False})


def code(text: str) -> dict:
    return {"type": "text", "text": text, "style": {"code": True}}


def prose(text: str) -> dict:
    return {"type": "text", "text": text}


def shown(text: str, elements: list[dict]) -> list[tuple[str, str]]:
    """What the page displays of the message's lines: ("code", ...) or ("text", ...)."""
    section = {"type": "rich_text_section", "elements": elements}
    sent = {
        "type": "message", "user": "U0CEDAR01", "ts": "1900000000.000100", "text": text,
        "blocks": [{"type": "rich_text", "elements": [section]}],
    }  # fmt: skip
    draft = note_from_message(
        sent, team_id="T0FABLE01", team_domain="fable-works", conversation_id="C0LIBRARY1",
        author="Cedar Quill", names={},
    )  # fmt: skip
    body = draft.text.split("\n\n", 1)[1].rsplit("\n\n", 1)[0]
    out: list[tuple[str, str]] = []
    for token in READER.parse(body):
        for child in token.children or []:
            kind = "code" if child.type == "code_inline" else "text"
            if out and kind == out[-1][0] == "text":
                out[-1] = ("text", out[-1][1] + child.content)
            else:
                out.append((kind, child.content))
    return out


def test_1_a_named_span_of_backticks_after_a_span_the_rich_text_leaves_out():
    """A span the rich text does not name, then right after it a named span holding one
    backtick. Slack displayed "k" as code and "`" as code (the guide allows the second as its
    characters). The named span is placed on the first span's closing backtick: the page
    shows "`k" as text, then "`" as code, then a backtick."""
    page = shown("`k````", [code("`")])
    assert page[0] == ("code", "k")
    assert "".join(chars for _, chars in page) == "k`"


def test_2_backticks_the_rich_text_shows_as_characters_are_not_code():
    """Slack displayed "`x` or " as characters, then the code span "x". The page shows two
    code spans and no backtick."""
    assert shown("`x` or `x`", [prose("`x` or "), code("x")]) == [
        ("text", "`x` or "),
        ("code", "x"),
    ]


def test_3_a_named_span_of_backticks_after_an_empty_one_stays_after_it():
    """An empty span the rich text names, whose two backticks the text holds, then a span of
    one backtick. The empty span's backticks are shown as typed (the fix's own test: "the
    text's two backticks, as typed"), then the code "`". The page shows the code first and
    the two backticks after it."""
    assert shown("`````", [code(""), code("`")]) == [("text", "``"), ("code", "`")]


def test_4_a_literal_backtick_before_a_named_span_of_backticks_stays_before_it():
    """Slack displayed "x`" as characters, then the code span "`". The page shows the same
    characters, but "x", the code span, and then the literal backtick."""
    assert shown("x````", [prose("x`"), code("`")]) == [("text", "x`"), ("code", "`")]


def test_5_a_span_of_blanks_right_after_another_at_a_lines_end_shows_its_blanks():
    """Slack displayed the code "a" and then a code span of two blanks. Written as plain
    characters at the end of the line, the blanks are dropped: the page shows "a" alone."""
    assert "".join(chars for _, chars in shown("`a``  `", [code("a"), code("  ")])) == "a  "
