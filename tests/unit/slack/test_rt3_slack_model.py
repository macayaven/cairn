"""An independent model of what Slack displays for a message's `text`, and a seeded grammar
of Slack text to generate messages from. Written from Slack's own page on message text
(docs.slack.dev/messaging/formatting-message-text, read 2026-11-20), not from cairn's
converter; the tests at the bottom hold the model to that page's own examples.

What the page says, and so what the model holds as certain:
- `&`, `<` and `>` are control characters; only `&amp;`, `&lt;` and `&gt;` are decoded for
  display, everywhere.
- Inline code is text between backticks; "Text within inline code blocks will not use any
  other formatting", so a `*`, a `<url|label>` or a `[x](y)` inside it is shown as written.
- A code block is text between three backticks before and after it; its own example puts
  text on the same line as the backticks: "```This is a code block\\nAnd it's multi-line```".
- `<url>` and `<url|label>` are links to `url`, showing `label` (else the url); `<@U..>` is
  a user shown by their name, `<#C..|name>` a conversation by its name, `<!here>` and the
  other special mentions are shown as their words, `<!date^..|fallback>` as a date.
- `*bold*`, `_italic_`, `~strike~`; `>` at the start of a line quotes it; there is no list
  syntax, so `- item` and `1. item` are text.

What the page does not say is marked unsure (`Piece.sure` False) and a check may accept
either reading of it: a run of backticks with no closer, an inline code span across a line
break, a code block's empty first or last line, whether emphasis needs word boundaries, the
date a `<!date>` shows, a raw `<` without its `>`, and line breaks other than LF and CR LF.
"""

import random

from support.rt3_slack_model import NAMES, a_message, display

# Line boundaries Python's str.splitlines knows besides LF; Slack's page names only "\n".


# The grammar. Each message is lines; a line is a lead (indentation, a quote, a list marker
# that Slack shows as text) and pieces. Code blocks open and close anywhere.


# The model held to Slack's page


def test_inline_code_uses_no_other_formatting():
    shown = display("This is a sentence with some `inline *code*` in it.")
    assert [p.kind for p in shown.pieces] == ["text", "code", "text"]
    assert shown.inline_code == ["inline *code*"]


def test_a_code_block_may_start_on_the_line_of_its_backticks():
    shown = display("```This is a code block\nAnd it's multi-line```")
    assert shown.code_blocks == [["This is a code block", "And it's multi-line"]]
    assert not shown.unsure


def test_the_three_entities_are_decoded_and_nothing_else():
    assert display("&lt;b&gt; &amp;amp; &#106;").text() == "<b> &amp; &#106;"


def test_links_show_their_label_or_their_address():
    shown = display("<https://fable.test/a|the ledger> and <https://fable.test/b>")
    assert shown.links == [("https://fable.test/a", "the ledger"), ("https://fable.test/b",) * 2]
    assert shown.text() == "the ledger and https://fable.test/b"


def test_mentions_show_names_and_unknown_ones_are_unsure():
    shown = display("<@U0CEDAR01> <@U0BIRCH01> <#C0SHELF1|shelf> <!here>", NAMES)
    assert shown.text() == "@Cedar Quill @U0BIRCH01 #shelf @here"
    assert [p.sure for p in shown.pieces if p.kind != "text"] == [True, False, True, True]


def test_what_the_page_leaves_open_is_unsure():
    for text in ("a ` b", "a ``` b", "a\u2028b", "a < b", "``"):
        assert display(text).unsure, text
    assert not display("a `b` c\r\nd").unsure


def test_the_guide_lines_write_links_as_markdown():
    shown = display("x <https://fable.test/a|the ledger>\n```code &lt;y&gt;```")
    assert shown.guide_lines({}) == ["x [the ledger](https://fable.test/a)", "code <y>"]


def test_the_grammar_is_seeded():
    one = [a_message(random.Random(seed)) for seed in range(50)]
    assert one == [a_message(random.Random(seed)) for seed in range(50)]
    assert len(set(one)) > 45
