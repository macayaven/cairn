"""Where a filed Slack message's `*`, `_` and `~` are emphasis (the tester's second attack,
finding 2: every `*x*` was written `**x**`, so `2 * 3 * 4` showed as `2 ** 3 ** 4`).

Slack's page on message text gives `*bold*`, `_italic_` and `~strike~` and says nothing of
where a mark opens or closes. The rule here is the tester's model where the page is silent,
with the decision's "not inside a word": a mark opens where no blank and no mark follows it
and no letter or digit stands before it, and closes at the first same mark on its line, one
character on at least, with no blank before it and no letter or digit after it; the marks
between are characters. Only those are written as emphasis, bold as `**x**`, italic as
`_x_`, struck text as typed; every other mark is the character typed, with a backslash where
Markdown could read it as emphasis or a list's mark. Every message is made up."""

import pytest
from markdown_it import MarkdownIt

from cairn.slack.text import note_from_message

READER = MarkdownIt("commonmark", {"html": False}).enable("table")  # as the web view reads
STYLES = {"em_open", "strong_open", "bullet_list_open", "hr"}


def filed(text: str):
    message = {"type": "message", "user": "U0CEDAR01", "ts": "1900000000.000100", "text": text}
    return note_from_message(
        message, team_id="T0FABLE01", team_domain="fable-works", conversation_id="C0LIBRARY1",
        author="Cedar Quill", names={},
    )  # fmt: skip


def body_of(draft) -> str:
    """The note's lines between its heading and its source link."""
    return draft.text.split("\n\n", 1)[1].rsplit("\n\n", 1)[0]


def page_reads(body: str) -> tuple[str, list[str]]:
    """What a page displays of `body`: its characters, and the styles it reads, in order."""
    tokens = [t for block in READER.parse(body) for t in [block, *(block.children or [])]]
    shown = {"text": None, "softbreak": "\n"}
    return "".join(shown[t.type] or t.content for t in tokens if t.type in shown), [
        t.type for t in tokens if t.type in STYLES
    ]


@pytest.mark.parametrize(
    ("text", "body", "shown"),
    [
        ("2 * 3 * 4 is 24", "2 * 3 * 4 is 24", "2 * 3 * 4 is 24"),
        ("* item", "\\* item", "* item"),
        ("snake_case_name", "snake_case_name", "snake_case_name"),
        ("~/path/to/file", "~/path/to/file", "~/path/to/file"),
        ("a*b*c", "a\\*b\\*c", "a*b*c"),
        ("* spaced *", "\\* spaced \\*", "* spaced *"),
        ("*not closed", "\\*not closed", "*not closed"),
        ("*a\nb*", "\\*a\nb\\*", "*a\nb*"),  # a mark does not close on another line
        ("* * *", "\\* * \\*", "* * *"),  # no rule either
    ],
)
def test_a_mark_slack_shows_as_a_character_is_the_character_typed(text, body, shown):
    draft = filed(text)
    assert body_of(draft) == body
    assert page_reads(body) == (shown, [])
    assert draft.title == body.split("\n")[0]
    assert draft.shown == shown.split("\n")[0]


@pytest.mark.parametrize(
    ("text", "body", "shown", "styles"),
    [
        ("*bold*", "**bold**", "bold", ["strong_open"]),
        ("_slanted_", "_slanted_", "slanted", ["em_open"]),
        ("~gone~", "~gone~", "~gone~", []),  # a page cannot strike: the tildes show
        ("See *the* end.", "See **the** end.", "See the end.", ["strong_open"]),
        ("(*a*) and _b_.", "\\(**a**\\) and _b_.", "(a) and b.", ["strong_open", "em_open"]),
        ("*a *b*", "**a \\*b**", "a *b", ["strong_open"]),  # the marks between are characters
        ("*a*b c*", "**a\\*b c**", "a*b c", ["strong_open"]),  # not closed inside a word
        ("_a_b_", "_a_b_", "a_b", ["em_open"]),
        ("__init__", "\\__init_\\_", "_init_", ["em_open"]),  # the first mark has one after it
    ],
)
def test_emphasis_slack_shows_is_written_as_emphasis(text, body, shown, styles):
    draft = filed(text)
    assert body_of(draft) == body
    assert page_reads(body) == (shown, styles)
