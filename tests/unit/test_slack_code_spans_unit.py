"""Code within a line that Slack's rich text names: each span is written with the backticks its
own text needs (the second reviewer's seventh report, must-fix 3: one span holding a run of
backticks set the delimiter of every span in the message, so 16 and 32 short spans gave
drafts of 823 and 2,535 characters for texts of 164 and 308).

A span is written between backticks one more than the longest run it holds, with a space
inside each where its text begins or ends with a backtick; where it cannot be a span, empty
or right after another, it is written as the plain characters it displayed. Every message is
made up."""

import html
import re

import pytest
from markdown_it import MarkdownIt

from cairn.slack.text import note_from_message
from cairn.spans import chunk
from cairn.web.markdown import render

READER = MarkdownIt("commonmark", {"html": False})


def message(codes: list[str], between: str = " and ", rich: bool = True) -> dict:
    """A message of inline code, `between` each two, as Slack sends it: its text with each span
    between single backticks, and its rich text naming each span's code."""
    held = [code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") for code in codes]
    text = between.join(f"`{code}`" for code in held)
    sent = {"type": "message", "user": "U0CEDAR01", "ts": "1900000000.000100", "text": text}
    if rich:
        elements = []
        for at, code in enumerate(codes):
            elements += [{"type": "text", "text": between}] if at and between else []
            elements.append({"type": "text", "text": code, "style": {"code": True}})
        section = {"type": "rich_text_section", "elements": elements}
        sent["blocks"] = [{"type": "rich_text", "elements": [section]}]
    return sent


def filed(sent: dict):
    return note_from_message(
        sent, team_id="T0FABLE01", team_domain="fable-works", conversation_id="C0LIBRARY1",
        author="Cedar Quill", names={},
    )  # fmt: skip


def body_of(draft) -> str:
    return draft.text.split("\n\n", 1)[1].rsplit("\n\n", 1)[0]


def read(body: str) -> tuple[list[str], str]:
    """The code within a line the parser reads in `body`, and all it displays."""
    children = [t for block in READER.parse(body) for t in block.children or []]
    code = [t.content for t in children if t.type == "code_inline"]
    return code, "".join(t.content for t in children if t.type in ("text", "code_inline"))


def passages(draft) -> list[str]:
    """What a page shows of each passage of the note alone: the claim's own render."""
    return [html.unescape(re.sub(r"<[^>]+>", "", str(render(span.text)))).strip("\n")
            for span in chunk("", draft.text)]  # fmt: skip


@pytest.mark.parametrize("spans", [16, 32])
def test_a_span_with_a_run_of_backticks_does_not_lengthen_the_others(spans):
    """The bound: each span is written in at most three times the characters its text takes
    (its code, a delimiter one longer than its longest run of backticks each side, a space
    each side at most), so the body is at most three times the message's text."""
    codes = [f"k{n}" for n in range(spans)]
    codes[0] = "a" + "`" * spans + "b"
    sent = message(codes)
    body = body_of(filed(sent))
    assert len(body) <= 3 * len(sent["text"]), (len(sent["text"]), len(body))
    assert read(body)[0] == codes
    assert body.count("`") == 2 * (spans + 1) + spans + 2 * (spans - 1)


@pytest.mark.parametrize(
    "codes",
    [
        ["``"],
        ["`"],
        ["`a`"],
        ["`a", "b`"],
        ["a`b", "c``d", "e", "f```g"],
        [" a ", "  ", "a "],
    ],
    ids=["only-backticks", "one-backtick", "begins-and-ends", "begins-or-ends", "runs", "blanks"],
)
@pytest.mark.parametrize("between", [" and ", ""], ids=["apart", "side-by-side"])
def test_every_span_displays_its_own_text_under_both_readers(codes, between):
    draft = filed(message(codes, between))
    body = body_of(draft)
    code, shown = read(body)
    assert shown == between.join(codes), body
    if between:  # each its own span
        assert code == codes, body
    else:  # one right after a span is its plain characters, so the next is a span again
        assert code == codes[::2], body
    assert passages(draft)[1:-1] == [shown], passages(draft)


@pytest.mark.parametrize(
    ("sent", "code", "shown"),
    [
        (message(["", "a`b"]), ["a`b"], "`` and a`b"),  # the text's two backticks, as typed
        ({**message(["", "x``y"]), "text": "`x``y`"}, ["x``y"], "x``y"),  # not in the text
    ],
    ids=["held by the text", "not held"],
)
def test_an_empty_span_is_passed_over_and_the_rest_is_still_the_rich_texts(sent, code, shown):
    """An empty span cannot be one in Markdown, and Slack's text need not hold it: the rich
    text's spans after it are still its (it stopped there before, and the text's backticks
    decided the rest)."""
    assert read(body_of(filed(sent))) == (code, shown)


def test_an_empty_span_before_a_block_leaves_the_block_a_block():
    empty = {"type": "text", "text": "", "style": {"code": True}}
    block = {"type": "rich_text_preformatted", "elements": [{"type": "text", "text": "y"}]}
    section = {"type": "rich_text_section", "elements": [empty]}
    sent = {**message([]), "text": "```y```"}
    sent["blocks"] = [{"type": "rich_text", "elements": [section, block]}]
    assert [t.content for t in READER.parse(filed(sent).text) if t.type == "fence"] == ["y\n"]


@pytest.mark.parametrize("code", ["&amp;", "<b>*x*", "a\\_b_ [c](d)"])
def test_a_span_right_after_another_shows_its_code_as_its_characters(code):
    """Written as plain characters, the code Slack displayed, never decoded or read again."""
    draft = filed(message(["a", code], between=""))
    assert read(body_of(draft)) == (["a"], "a" + code)


@pytest.mark.parametrize(
    ("text", "body"),
    [
        ("`a` and `b`", "`a` and `b`"),
        ("Run `make` then `make test`", "Run `make` then `make test`"),
        ("``", "\\`\\`"),
        ("` a `", "`  a  `"),
        ("a `b` c ` d", "a `b` c \\` d"),
        # Two the text's backticks alone bound, side by side: written as they were, and read
        # as one span (a fault of the text's own, left as it was; the rich text says better).
        ("`a``b`", "`a``b`"),
    ],
)
def test_a_plain_text_message_gives_what_it_gave(text, body):
    sent = {"type": "message", "user": "U0CEDAR01", "ts": "1900000000.000100", "text": text}
    assert body_of(filed(sent)) == body
