"""What the spans harness (test_rt3_slack_spans_held) found on release/0.1.0 at 6fb9ee9b, one
step past the fix of the second reviewer's seventh report (must-fix 3). Each is pinned as
failing (strict), so the file passes as committed and a fix removes the mark.

One root: _known_code places a span the rich text names at the first place in the text where
a backtick, its code and a backtick stand, after the span before. That place can be made of
backticks Slack displayed as characters (the rich text's own text), or of the closing backtick
of a span the rich text does not name; the span is then read there, and what lies before it
by the text's backticks. Slack's page escapes only "&", "<" and ">" in a message's text, so a
backtick Slack displayed as a character is sent bare. While the held harness keeps its 1,200
messages, its grammar makes 68 placed so (it leaves them out), each a span the text bounds
before a named one; 47 of the 68 show a page that differs from what Slack displayed. Every
message is made up."""

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


@pytest.mark.xfail(strict=True, reason="a named span is read across literal backticks")
def test_1_a_named_span_is_not_read_across_backticks_slack_showed_as_characters():
    """Slack displayed "`a` `b`" as characters, then a code span of one blank. The page
    shows "`a", a code span of one blank made of the backticks around the first blank, and
    "b`` `": its characters are not Slack's."""
    assert shown("`a` `b`` `", [prose("`a` `b`"), code(" ")]) == [
        ("text", "`a` `b`"),
        ("code", " "),
    ]


@pytest.mark.xfail(strict=True, reason="a named span is placed on earlier literal backticks")
def test_2_a_named_span_is_not_placed_on_the_same_code_shown_as_characters_before_it():
    """Slack displayed "`x` or " as characters, then the code span "x". The page shows two
    code spans and no backtick: the first is the literal text, read as the named span, and
    the second the text's own backticks."""
    assert shown("`x` or `x`", [prose("`x` or "), code("x")]) == [
        ("text", "`x` or "),
        ("code", "x"),
    ]


@pytest.mark.xfail(strict=True, reason="a span of backticks is placed one backtick early")
def test_3_a_named_span_of_backticks_after_a_span_the_rich_text_leaves_out():
    """A span the rich text does not name (an element of another shape: cairn reads only a
    code-styled "text") and right after it a named span holding one backtick. Slack displayed
    "k" as code and "`" as code (the model reads the text's backticks so; the guide allows
    the second as its characters). The named span is placed on the first span's closing
    backtick: the page shows "`k" as text, then "`" as code, then a backtick."""
    page = shown("`k````", [code("`")])
    assert page[0] == ("code", "k")
    assert "".join(chars for _, chars in page) == "k`"


@pytest.mark.xfail(strict=True, reason="a span of backticks swaps places with a literal one")
def test_4_a_literal_backtick_before_a_named_span_of_backticks_stays_before_it():
    """Slack displayed "x`" as characters, then the code span "`". The page shows the same
    characters, but "x", the code span, and then the literal backtick: the two have changed
    places."""
    assert shown("x````", [prose("x`"), code("`")]) == [("text", "x`"), ("code", "`")]
