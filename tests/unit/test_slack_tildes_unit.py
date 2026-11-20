"""Tildes in a filed Slack message (the third tester, findings 3 and 6).

Slack has no fence of tildes and shows a line of them as the characters, so no line of
prose opens a code fence in the note: not in the note read whole, not in a passage of it
read alone (what a page shows of a claim), not in the page's body; at any indentation and in
any quotation or list item a reader accepts. And struck text (`~done~`) is written as the
person typed it: the page cannot strike, so it shows the tildes, never doubled ones and
never the words unmarked. Every name and message is made up."""

import re

import pytest
import test_slack_shortcuts_unit as shortcuts
from markdown_it import MarkdownIt
from test_slack_commands_unit import ADA_SLACK

from cairn.spans import chunk
from cairn.web.markdown import render

READER = MarkdownIt("commonmark", {"html": False}).enable("table")  # as the web view reads
SOURCE = "https://fable-works.slack.com/archives/C0LIBRARY1/p1900000000000100"


@pytest.fixture
def bench(tmp_path):
    made = shortcuts.Bench(tmp_path)
    made.users[shortcuts.CEDAR] = shortcuts.cedar()
    made.link(ADA_SLACK, made.ada, made.library)
    yield made
    made.store.close()


def filed(bench, text: str) -> tuple[str, list[str], str]:
    """The note, its claims and its page's body, the message filed through the shortcut."""
    message = {"type": "message", "user": shortcuts.CEDAR, "ts": "1900000000.000100"}
    shortcuts.handle(bench, {**message, "text": text})
    ((_, project, note, _),) = bench.notes()
    (listed,) = bench.owner.list_pages(project)
    page = bench.owner.get_page(project, listed.slug)
    return note, [claim.text for claim in page.claims], page.body


def links(html: str) -> list[tuple[str, str]]:
    return re.findall(r'<a href="([^"]*)"[^>]*>([^<]*)</a>', html)


def code_read(markdown: str) -> list[str]:
    return [t.type for t in READER.parse(markdown) if t.type in ("fence", "code_block")]


RULES = [
    "~~~",
    "~~~~~~~~~~",
    "  ~~~",
    "   ~~~",
    "~~~e",
    "~~~ python",
    "\t~~~",
    "- ~~~",
    "1. ~~~",
    "1986. ~~~",
    "&gt; ~~~",
    "&gt;~~~",
    "- &gt; ~~~",
    "&gt; - ~~~",
    "* * ~~~",
    "+\t~~~",
]


@pytest.mark.parametrize("rule", RULES)
@pytest.mark.parametrize("where", ["between", "first", "last"])
def test_no_line_of_tildes_opens_a_fence_anywhere(bench, rule, where):
    text = {
        "between": f"Steps for Monday\n{rule}\nsee the ledger",
        "first": f"{rule}\nsee the ledger",
        "last": f"Steps for Monday\n{rule}",
    }[where]
    note, claims, body = filed(bench, text)
    assert [span.kind for span in chunk("", note) if span.kind == "code"] == [], note
    for markdown in (note, body, *claims):
        assert code_read(markdown) == [], markdown
    assert any(
        (href, words) == (SOURCE, "Source in Slack")
        for claim in claims
        for href, words in links(str(render(claim)))
    ), claims
    shown = "".join(str(render(claim)) for claim in claims)
    assert "~~~" in shown and "\\~" not in shown


def test_a_code_block_of_slacks_own_is_still_the_one_fence(bench):
    note, _, body = filed(bench, "Steps\n~~~\n```\n~~~\nrun\n```\n~~~\nsee the ledger")
    for markdown in (note, body):
        assert code_read(markdown) == ["fence"], markdown
    assert [t.content for t in READER.parse(note) if t.type == "fence"] == ["~~~\nrun\n"]
