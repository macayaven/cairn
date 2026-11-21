"""What the spans harness (test_rt3_slack_spans_held) found on release/0.1.0 at 08747d92, one
step past the placing of named spans (_before, _known_code, _ticks), worst first. Each is
pinned as failing (strict), so the file passes as committed and a fix removes the mark.

Placement (tests 1 and 2): a span the rich text names whose backticks and code first stand on
a backtick of a span the rich text does not read (a code-styled element of another shape: an
emoji here), its own place later. A later place is looked for no further than the length of
the text the rich text shows before the span, and here the later place is further: past
that unread span's closing backtick, or past a whole unread span holding the same code. The
span is read on the unread span's backticks, and the text's backticks decide the rest: two
backticks Slack did not display show, in code or as characters. Nothing Slack displayed
literally becomes a link, emphasis or a heading with these shapes; the held harness's
stand-in for an unread element (text that holds `*bold*`) did show bold. In the held run,
11 of 1,200 messages are of this class (103 have a named span whose code first stands
earlier, and the other 92 now show what Slack displayed). The guide's sentence "Where Slack's
rich text gives the text that stands right before a span it names, a backtick in that text
is written as a character, and the span after it" is not true of test 2's message.

Blanks (test 3): a span written as plain characters that ends in a no-break space, at the end
of the message, shows without it (the parser strips the paragraph's Unicode blanks). The
implementer stated that only a space or a tab is kept; the guide says "keeps the blanks that
end it".

What these rest on, beyond Slack's documents: a code-styled emoji is sent in the message's
text as its name between colons, between backticks, and Slack displays it as code. Every
message is made up."""

import pytest
from test_rt3_slack_spans_found import code, prose, shown


def emoji(name: str) -> dict:
    return {"type": "emoji", "name": name, "style": {"code": True}}


@pytest.mark.xfail(strict=True, reason="a named span is placed on an unread span's backtick")
def test_1_a_named_span_after_an_unread_span_and_its_own_text_stays_after_that_text():
    """Slack displayed the emoji ":x:" as code, then "y", then the code "y". The span is read
    from the emoji's closing backtick: the page shows "`:x:" as text, the code "y", and "y`"."""
    assert shown("`:x:`y`y`", [emoji("x"), prose("y"), code("y")]) == [
        ("code", ":x:"),
        ("text", "y"),
        ("code", "y"),
    ]


@pytest.mark.xfail(strict=True, reason="a named span is placed on an unread span of its code")
def test_2_a_named_span_after_an_unread_span_of_the_same_code_stays_its_own():
    """Slack displayed the emoji ":x:" as code, " - ", the code ":x:", and the emoji ":y:" as
    code. The named span is read on the first emoji, though " - " stands right before its
    own place: the page shows the code ":x:``:y:", two backticks Slack did not display."""
    page = shown("`:x:` - `:x:``:y:`", [emoji("x"), prose(" - "), code(":x:"), emoji("y")])
    assert page[:2] == [("code", ":x:"), ("text", " - ")]
    assert "``" not in "".join(chars for _, chars in page)


def test_3_a_span_written_as_characters_keeps_a_no_break_space_that_ends_the_message():
    nbsp = "\N{NO-BREAK SPACE}"
    page = shown(f"`a``x{nbsp}`", [code("a"), code("x" + nbsp)])
    assert "".join(chars for _, chars in page) == "ax" + nbsp
