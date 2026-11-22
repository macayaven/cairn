"""What the spans harness (test_rt3_slack_spans_held) found on release/0.1.0 at 6fb9ee9b, one
step past the fix of the second reviewer's seventh report (must-fix 3), worst first. All five
are fixed on 00740625 (the placing of Slack's code spans: _before, _known_code, _ticks, and the
plain-characters branch of _render) and stay as regression tests; what the next attack found
is in test_rt3_slack_spans_found2.

Placement (tests 1, 3, 4): _known_code placed a span the rich text names at the first place in
the text where a backtick, its code and a backtick stand, after the span before; for a span of
backticks alone that place could start one or two backticks early. It now takes the place the
text the rich text shows right before the span stands before, and else, where a span the text
bounds may end there, the place after an odd backtick on the line.

Reading (test 2): backticks the rich text gives as its own text, right before a span it names,
were read as code where they paired; they are now written as characters.

Blanks (test 5): a span of blanks alone right after another, at the end of a line, was dropped
by the page; the blank that ends it is now written as a character reference.

What these rest on, beyond Slack's documents: Slack's page escapes only "&", "<" and ">" in a
message's text, so a backtick displayed as a character is assumed sent bare; and a span the
rich text does not name (an element of a shape cairn does not read) is assumed displayed as
code, as the model reads the text's backticks. Every message is made up."""

from support.slack.rt3_slack_spans_found import code, prose, shown


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


# --- test_rt3_slack_spans_found2.py ---


def emoji(name: str) -> dict:
    return {"type": "emoji", "name": name, "style": {"code": True}}


def test_1_a_named_span_after_an_unread_span_and_its_own_text_stays_after_that_text():
    """A stated limit (the connect guide's exception: an element styled as code that is not
    text, standing before the span on its line). Slack displayed the emoji ":x:" as code,
    then "y", then the code "y". The span is read from the emoji's closing backtick: the page
    shows "`:x:" as text, the code "y", and "y`"."""
    assert shown("`:x:`y`y`", [emoji("x"), prose("y"), code("y")]) == [
        ("text", "`:x:"),
        ("code", "y"),
        ("text", "y`"),
    ]


def test_2_a_named_span_after_an_unread_span_of_the_same_code_stays_its_own():
    """A stated limit, as test 1. Slack displayed the emoji ":x:" as code, " - ", the code
    ":x:", and the emoji ":y:" as code. The named span is read on the first emoji, though
    " - " stands right before its own place: the page shows the code ":x:``:y:", two
    backticks Slack did not display."""
    page = shown("`:x:` - `:x:``:y:`", [emoji("x"), prose(" - "), code(":x:"), emoji("y")])
    assert page == [("code", ":x:"), ("text", " - "), ("code", ":x:``:y:")]


def test_3_a_span_written_as_characters_keeps_a_no_break_space_that_ends_the_message():
    nbsp = "\N{NO-BREAK SPACE}"
    page = shown(f"`a``x{nbsp}`", [code("a"), code("x" + nbsp)])
    assert "".join(chars for _, chars in page) == "ax" + nbsp
