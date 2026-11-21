"""Where a span Slack's rich text names is placed in the message's text: where the rich text
gives the element right before it, that element agrees with the place, one step past the
cases of test_rt3_slack_spans_found. Every message is made up."""

import pytest
from test_rt3_slack_spans_found import READER, code, prose, shown

from cairn.slack.text import note_from_message
from cairn.spans import chunk


def test_text_before_a_span_is_read_with_its_entities():
    """The text element right before the span is held with "&" as an entity, as the message's
    text holds it: "a &`" then the code "`", whose backticks the first place would split."""
    assert shown("a &amp;````", [prose("a &`"), code("`")]) == [("text", "a &`"), ("code", "`")]


def test_a_later_place_is_not_taken_where_the_rich_text_is_silent_between():
    """The rich text leaves the span "x" out, so the text "y" before the first "a" stands
    earlier than its place: the first place is kept, not the later one that "y" stands
    right before (which would make "x``a" one span)."""
    sent = "y`x``a`y`a`"
    page = shown(sent, [prose("y"), code("a"), prose("y"), code("a")])
    assert page == [("text", "y"), ("code", "x"), ("text", "ay"), ("code", "a")]


def test_a_backtick_that_closes_a_span_is_not_taken_where_the_text_before_is_not_there():
    """The text "x " the rich text gives before the span of one backtick does not stand right
    before any place it could take (the span "k" it leaves out is between), so the rich text
    tells nothing there: one backtick before the run on its line is unpaired, the run's first
    closes that span, and the code starts one later."""
    page = shown("x `k````", [prose("x "), code("`")])
    assert page == [("text", "x "), ("code", "k"), ("text", "`")]


def test_a_named_span_before_one_the_rich_text_leaves_out_tells_nothing_of_the_next():
    """Case 1 one step on: the span "a" the rich text names, then "x" it leaves out, then a
    named span of one backtick. The backtick that closes "x" is not the named span's: "x",
    right after "a", shows its characters, and the backtick its own span."""
    page = shown("`a``x````", [code("a"), code("`")])
    assert page == [("code", "a"), ("text", "x"), ("code", "`")]


def test_a_backtick_before_a_link_does_not_make_the_link_code():
    """A link stands right before the span: the rich text says no span the text bounds ends
    there, so the backtick Slack displayed as a character before the link pairs with none."""
    link = {"type": "link", "url": "https://fable.test/x", "text": "x"}
    page = shown("x`<https://fable.test/x|x>````", [prose("x`"), link, code("`"), prose("`")])
    assert page == [("text", "x`x"), ("code", "`"), ("text", "`")]


def test_the_text_of_the_section_before_tells_nothing_a_stated_limit():
    """A stated limit: what the rich text displayed before a span is read in the span's own
    section only. Here "a`" ends the section before, on the same line, and the span of one
    backtick is placed on the backtick Slack displayed as a character, as before this fix."""
    sections = [[prose("a`")], [code("`")]]
    blocks = [{"type": "rich_text", "elements": [
        {"type": "rich_text_section", "elements": elements} for elements in sections
    ]}]  # fmt: skip
    note = note_from_message(
        {"type": "message", "user": "U0CEDAR01", "ts": "1900000000.000100", "text": "a````",
         "blocks": blocks},
        team_id="T0FABLE01", team_domain="fable-works", conversation_id="C0LIBRARY1",
        author="Cedar Quill", names={},
    ).text  # fmt: skip
    body = note.split("\n\n", 1)[1].rsplit("\n\n", 1)[0]
    children = [t for block in READER.parse(body) for t in block.children or []]
    assert [(t.type, t.content) for t in children] == [
        ("text", "a"),
        ("code_inline", "`"),
        ("text", "`"),
    ]


BLANKS = ["\N{NO-BREAK SPACE}", "\N{EM SPACE}", "\N{NARROW NO-BREAK SPACE}", " ", "\t"]


def first_line(markdown: str) -> str:
    """What the parser displays of the first line of `markdown`'s first paragraph."""
    inline = next(token for token in READER.parse(markdown) if token.type == "inline")
    shown = []
    for child in inline.children or []:
        if child.type in ("softbreak", "hardbreak"):
            break
        shown.append(child.content)
    return "".join(shown)


@pytest.mark.parametrize("blank", BLANKS)
@pytest.mark.parametrize("after", ["", "\nnext"], ids=["message-end", "line-end"])
def test_a_span_written_as_characters_keeps_any_blank_that_ends_it(blank, after):
    """Whatever blank ends a span written as its plain characters stays, at the end of a line
    and of the message, in the note and in its passage shown alone."""
    elements = [code("a"), code("x" + blank), *([prose(after)] if after else [])]
    section = {"type": "rich_text_section", "elements": elements}
    note = note_from_message(
        {"type": "message", "user": "U0CEDAR01", "ts": "1900000000.000100",
         "text": f"`a``x{blank}`{after}", "blocks": [{"type": "rich_text", "elements": [section]}]},
        team_id="T0FABLE01", team_domain="fable-works", conversation_id="C0LIBRARY1",
        author="Cedar Quill", names={},
    ).text  # fmt: skip
    assert first_line(note.split("\n\n", 1)[1]) == "ax" + blank
    passage = next(span.text for span in chunk("", note) if "`a`" in span.text)
    assert first_line(passage[passage.index("`a`") :]) == "ax" + blank
