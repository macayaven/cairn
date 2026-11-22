"""A code block opened on a quoted line: only the mark that quotes a line is the quotation's.

`&gt; ```&gt; x```` was filed with `x` in its block: the `>` that is the block's own text, after
its opening backticks, was taken for a quotation's mark (the tester's last finding on SL8).
What stands after the opening backticks is the block's text, a `>` included; on each later
line the first mark is the quotation's, as before (Slack's page on message text does not say
how a quoted block's later lines are marked, and that reading is unchanged). Every message is
made up."""

import pytest
from markdown_it import MarkdownIt

from cairn.slack.text import note_from_message

READER = MarkdownIt("commonmark", {"html": False})


def filed(text: str, rich: str | None = None):
    message = {"type": "message", "user": "U0CEDAR01", "ts": "1900000000.000100", "text": text}
    if rich is not None:  # Slack's own account of it: the block as it displayed it
        block = {"type": "rich_text_preformatted", "elements": [{"type": "text", "text": rich}]}
        message["blocks"] = [{"type": "rich_text", "elements": [block]}]
    return note_from_message(
        message, team_id="T0FABLE01", team_domain="fable-works", conversation_id="C0LIBRARY1",
        author="Cedar Quill", names={},
    )  # fmt: skip


def blocks(draft) -> list[str]:
    return [token.content for token in READER.parse(draft.text) if token.type == "fence"]


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("&gt; ```&gt; x```", "> x"),
        ("&gt; ```&gt;```", ">"),
        ("&gt; ```&gt;x```", ">x"),
        ("&gt;```&gt; x```", "> x"),
    ],
)
def test_what_follows_the_opening_backticks_is_the_blocks_text(text, shown):
    for draft in (filed(text), filed(text, rich=shown)):
        assert blocks(draft) == [shown + "\n"], draft.text
    assert filed(text).title == filed(text, rich=shown).title


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("&gt; ```&gt; a\n&gt; &gt; b\n&gt; &gt; c```", "> a\n> b\n> c\n"),
        ("&gt; ```&gt; a\n&gt; &gt;```", "> a\n>\n"),
    ],
)
def test_each_later_line_loses_only_the_quotations_own_mark(text, shown):
    assert blocks(filed(text)) == [shown]


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("&gt; ```\n&gt; ls\n&gt; ```", "ls\n"),
        ("&gt; ```ls\n&gt; &gt; out```", "ls\n> out\n"),
        ("&gt; a ```&gt; x```", "> x\n"),  # not opened where only marks stand: no quotation's
        ("```&gt; x```", "> x\n"),
    ],
)
def test_what_was_so_before_is_so_still(text, shown):
    assert blocks(filed(text)) == [shown]
