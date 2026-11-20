"""The title of a filed Slack message (the third tester, findings 1, 2 and 4).

The title is the first line the message displays: its first line of text, or the first line
of a code block it opens with, never a line the note writes for itself (a fence). The page
writes it as its heading (`# {title}`), where nothing is code, so the title is written as
prose is, whatever line it comes from: escaped to read as the characters Slack displayed,
code within a line as plain characters, backticks and all, and a link as its words. It is
cut to the store's 80 characters (store.memory.TITLE_MAX) where no escape is cut in two.
Every name and message is made up."""

import asyncio

import pytest
import test_slack_shortcuts_unit as shortcuts
from markdown_it import MarkdownIt
from test_slack_commands_unit import ADA_SLACK, Bench

from cairn.slack.text import note_from_message
from cairn.store.memory import TITLE_MAX

READER = MarkdownIt("commonmark", {"html": False}).enable("table")  # as the web view reads
MARKUP = {"link_open", "image", "code_inline", "em_open", "strong_open", "html_inline"}


def title_of(text: str, names: dict | None = None) -> str:
    message = {"type": "message", "user": "U0CEDAR01", "ts": "1900000000.000100", "text": text}
    return note_from_message(
        message, team_id="T0FABLE01", team_domain="fable-works", conversation_id="C0LIBRARY1",
        author="Cedar Quill", names=names or {},
    ).title  # fmt: skip


def heading_shows(title: str) -> tuple[str, set[str]]:
    """What the page's heading of `title` displays, and the markup read in it."""
    tokens = READER.parse(f"# {title}")
    assert [t.type for t in tokens] == ["heading_open", "inline", "heading_close"], title
    children = tokens[1].children or []
    return "".join(t.content for t in children), {t.type for t in children} & MARKUP


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("-     [docs](https://evil.test/x)", "-     [docs](https://evil.test/x)"),
        ("&gt;     [docs](https://evil.test/x)", ">     [docs](https://evil.test/x)"),
        ("1.\t\t![chart](https://evil.test/x.png)", "1.\t\t![chart](https://evil.test/x.png)"),
        ("Settings:\n\n    [docs](https://evil.test/x)", "Settings:"),
        ("Q&amp;A &lt;b&gt; *now* _soon_ ~done~ #4", "Q&A <b> now soon ~done~ #4"),
        ("Run `[manual](https://evil.test/m)` today", "Run `[manual](https://evil.test/m)` today"),
        (
            "Ask <@U0BROOK01> about <https://fable.test/l|the ledger>",
            "Ask @Brook Park about the ledger",
        ),
        ("Ask about #", "Ask about #"),
        ("##", "##"),
        ("# Settings\nRow 4.", "# Settings"),
        ("a\\b `x` \\`", "a\\b `x` \\`"),
    ],
    ids=[
        "list item code",
        "quoted code",
        "image",
        "indented code below",
        "prose",
        "code within the line",
        "a mention and a link",
        "a closing hash",
        "only hashes",
        "a heading",
        "backslashes",
    ],
)
def test_the_title_reads_as_the_characters_slack_displayed(text, shown):
    title = title_of(text, {"U0BROOK01": "Brook Park"})
    displayed, markup = heading_shows(title)
    assert displayed == shown and markup <= {"em_open", "strong_open"}, title


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("```port 8811```\nShelf.", "port 8811"),
        ("```\nSELECT 1;\n```\nLedger.", "SELECT 1;"),
        ("```\n\n  [x](https://evil.test/y) *a*\n```", "[x](https://evil.test/y) *a*"),
        ("&gt; ```north\tblue```", "north\tblue"),
    ],
    ids=["a block on one line", "a fence on its own line", "a blank line first", "quoted"],
)
def test_a_message_that_opens_with_a_code_block_is_titled_by_its_first_line(text, shown):
    displayed, markup = heading_shows(title_of(text))
    assert (displayed, markup) == (shown, set())


def test_messages_that_open_with_different_blocks_are_put_on_pages_of_their_own(tmp_path):
    bench = Bench(tmp_path)
    try:
        bench.users[shortcuts.CEDAR] = shortcuts.cedar()
        bench.link(ADA_SLACK, bench.ada, bench.library)
        for n, text in enumerate(("```port 8811```\nShelf.", "```\nSELECT 1;\n```\nLedger.")):
            message = {"type": "message", "user": shortcuts.CEDAR, "text": text}
            response = shortcuts.handle(bench, {**message, "ts": f"19000000{n:02d}.000100"})
            asyncio.run(response.background())
        titles = sorted(page.title for page in bench.owner.list_pages(bench.library.id))
        assert titles == ["SELECT 1;", "port 8811"]
    finally:
        bench.store.close()


# Escapes, entities, backticks, a link and a closing hash, and Slack's emphasis.
HARD = (
    "\\[a\\](b) &amp; &lt;c&gt; `d|e` <https://fable.test/l|the [ledger]> ! \\ x #\t"
    "*bold words* _slanted_ ~struck~ #"
)
SHOWN = "\\[a\\](b) & <c> `d|e` the [ledger] ! \\ x #\tbold words slanted ~struck~ #"


@pytest.mark.parametrize("lead", range(81))
def test_the_cut_never_leaves_half_of_an_escape(lead):
    """The cut falls at every place of the hard part: the title is at most TITLE_MAX
    characters, and its heading displays the start of what the whole line displays, no
    markup but Slack's emphasis, and no character Slack did not display."""
    title = title_of("w" * lead + " " + HARD)
    assert len(title) <= TITLE_MAX and title == title.strip() and title
    displayed, markup = heading_shows(title)
    assert ("w" * lead + " " + SHOWN).lstrip().startswith(displayed), (title, displayed)
    assert markup <= {"em_open", "strong_open"}, title


def test_a_title_cut_short_is_the_one_the_page_is_given(tmp_path):
    bench = Bench(tmp_path)
    try:
        bench.users[shortcuts.CEDAR] = shortcuts.cedar()
        bench.link(ADA_SLACK, bench.ada, bench.library)
        text = "w" * 70 + " " + HARD
        message = {"type": "message", "user": shortcuts.CEDAR, "text": text}
        asyncio.run(shortcuts.handle(bench, {**message, "ts": "1900000000.000100"}).background())
        (page,) = bench.owner.list_pages(bench.library.id)
        assert page.title == title_of(text)
    finally:
        bench.store.close()
