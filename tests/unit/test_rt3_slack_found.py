"""What the generated messages found (test_rt3_slack_pages), each shrunk and pinned: the
page must show what Slack displayed. Each test states what is right, so it fails while the
fault stands. Found on 8aa0073d (core9c/0.1.0 at 1f84be95 merged) and still standing on
65e1a416 (core9c/0.1.0 at af365c3f merged), unless it says it held."""

import asyncio
import re
import time

import pytest
import test_slack_shortcuts_unit as shortcuts
from markdown_it import MarkdownIt
from starlette.testclient import TestClient
from test_rt3_slack_pages import SOURCE, Filer, Shown, faults
from test_slack_commands_unit import ADA_SLACK, BASE, Bench

from cairn.server import create_app
from cairn.slack.text import note_from_message
from cairn.web.markdown import render
from cookie_names import session_cookie

READER = MarkdownIt("commonmark", {"html": False}).enable("table")


@pytest.fixture
def filer(tmp_path):
    return Filer(tmp_path)


def links(markdown: str) -> list[tuple[str, str]]:
    return Shown(READER.render(markdown)).links + Shown(str(render(markdown))).links


# Links and images: the page's title is the message's first line


@pytest.mark.parametrize(
    "message",
    [
        "-     [docs](https://evil.test/x)",
        "&gt;     [docs](https://evil.test/x)",
        "1.\t\t![chart](https://evil.test/x.png)",
    ],
    ids=["list-item-code", "quoted-code", "image"],
)
def test_a_first_line_read_as_code_links_nothing_in_the_body(filer, message):
    """Slack displays these characters as text, so the note writes them as displayed (both
    readers read the line as code); the line is also the page's title, and the body writes
    the title as a heading, where nothing is code."""
    filed = filer(message)
    assert filed.body is not None
    assert not [href for href, _ in links(filed.body) if "evil.test" in href]
    assert "<img" not in READER.render(filed.body)


def test_a_code_span_the_title_cuts_links_nothing_in_the_body(filer):
    """A title is cut to 80 characters (store.memory.TITLE_MAX): the code span's closing
    backtick falls past the cut, and its text is read as Markdown in the body's title."""
    lead = "Read the setup notes before the Monday review: see"
    assert len(lead) == 50
    filed = filer(lead + "`[manual](https://evil.test/m)` today.")
    assert filed.body is not None
    assert not [href for href, _ in links(filed.body) if "evil.test" in href]


# The source link


@pytest.mark.parametrize("rule", ["~~~", "~~~~~~~~~~", "  ~~~"])
def test_a_line_of_tildes_keeps_the_source_link_a_link(filer, rule):
    """Slack displays a line of tildes as text; the note leaves it bare, it opens a fence,
    and the rest of the message and the note's own source link are code on the page."""
    filed = filer(f"Steps for Monday\n{rule}\nsee the ledger")
    shown = [link for claim in filed.claims for link in Shown(str(render(claim))).links]
    assert (SOURCE, "Source in Slack") in shown
    assert not any(c for c in filed.claims if "~~~" in c and "Source in Slack" in c)


# One page per message


@pytest.mark.parametrize(
    "texts",
    [
        ("```port 8811```\nShelf.", "```SELECT 1;```\nLedger."),
        ("```\nport 8811\n```\nShelf.", "```\nSELECT 1;\n```\nLedger."),
    ],
    ids=["block-on-one-line", "fence-on-its-own-line"],
)
def test_messages_that_start_with_a_code_block_are_not_put_on_one_page(tmp_path, texts):
    """The title is the note's first line, which is now the fence of a Slack code block:
    every such message gets the title "```", and so the same page. On 0d4e71a8 a block on
    one line gave titles of their own ("```port 8811```"): that form is new with the fix;
    a fence on its own line shared the page already."""
    bench = Bench(tmp_path)
    try:
        bench.users[shortcuts.CEDAR] = shortcuts.cedar()
        bench.link(ADA_SLACK, bench.ada, bench.library)
        for n, text in enumerate(texts):
            message = {"type": "message", "user": shortcuts.CEDAR, "text": text}
            response = shortcuts.handle(bench, {**message, "ts": f"19000000{n:02d}.000100"})
            asyncio.run(response.background())
        titles = [page.title for page in bench.owner.list_pages(bench.library.id)]
        assert len(titles) == 2, titles
        assert "```" not in titles
    finally:
        bench.store.close()


# Text as displayed


def test_a_mention_of_someone_else_shows_their_name(tmp_path):
    """Slack displays <@U...> as the person's name, and the guide says a mention is still the
    person's name; the shortcut passes names={author: name} only (shortcuts._write), so any
    other person shows as their member ID, in prose and in code."""
    birch = "U0BIRCH01"
    bench = Bench(tmp_path)
    try:
        bench.users[shortcuts.CEDAR] = shortcuts.cedar()
        bench.users[birch] = shortcuts.cedar(
            id=birch, real_name="Birch Fennel", profile={"real_name": "Birch Fennel"}
        )
        bench.link(ADA_SLACK, bench.ada, bench.library)
        text = f"Ask <@{birch}> on Monday\n\n    ping <@{birch}>"
        message = {"type": "message", "user": shortcuts.CEDAR, "text": text}
        asyncio.run(shortcuts.handle(bench, {**message, "ts": "1900000000.000100"}).background())
        (listed,) = bench.owner.list_pages(bench.library.id)
        body = bench.owner.get_page(bench.library.id, listed.slug).body
        assert birch not in body, body
        assert body.count("Birch Fennel") == 2, body
    finally:
        bench.store.close()


def test_a_line_read_as_code_only_in_its_claim_shows_no_escape(filer):
    """A quoted line, then a tab-indented one: the note reads the second as the quotation's
    text going on, its claim alone as code, where the backslashes written for prose show."""
    filed = filer("&gt; Readings\n\t[x] &amp; y")
    code = [text for claim in filed.claims for _, text in Shown(str(render(claim))).code]
    assert code, filed.claims
    assert all("\\" not in text and "&amp;" not in text for text in code), code


def test_a_line_read_as_code_only_in_its_claim_keeps_its_last_tab(filer):
    """The same shape, last in the message: Slack displays the tab that ends it, the note
    (which reads the line as text) drops it, and the claim alone shows the line as code."""
    filed = filer("&gt; Readings\n\tnorth\tblue\t")
    code = [text for claim in filed.claims for _, text in Shown(str(render(claim))).code]
    assert code == ["north\tblue\t\n"], (filed.claims, code)


def test_strikethrough_is_not_shown_as_doubled_tildes(filer):
    """Slack's ~done~ is written ~~done~~, which CommonMark (no strikethrough) shows as is."""
    filed = filer("Ledger ~done~")
    shown = "".join(text for claim in filed.claims for text in Shown(str(render(claim))).text)
    assert "done" in shown, filed.claims
    assert "~~" not in shown, shown


def served(tmp_path, author: str, text: str) -> str:
    bench = Bench(tmp_path)
    try:
        bench.users[shortcuts.CEDAR] = shortcuts.cedar(
            real_name=author, profile={"real_name": author, "display_name": "C"}
        )
        bench.link(ADA_SLACK, bench.ada, bench.library)
        message = {"type": "message", "user": shortcuts.CEDAR, "ts": "1900000000.000100"}
        asyncio.run(shortcuts.handle(bench, {**message, "text": text}).background())
        (listed,) = bench.owner.list_pages(bench.library.id)
        session = bench.store.sign_in(
            "sso", "sub-ada", "ada@example.test", "Ada", issuer="https://sso.example.test",
            join="personal",
        )  # fmt: skip
        app = create_app(bench.store, public_url=BASE, clock=bench.clock)
        with TestClient(app, base_url=BASE) as client:
            client.cookies.set(session_cookie("https"), session.token)
            return client.get(f"/b/library/p/{listed.slug}").text
    finally:
        bench.store.close()


def test_the_page_title_and_the_author_heading_show_no_escape(tmp_path):
    """The page's h1 is the note's first line and its section heading the author line, both
    as the note writes them for Markdown, shown as plain text: escapes and entities show."""
    html = served(tmp_path, "Ana & Bo_Quill [x]", "Q&A [notes] for Monday")
    (title,) = [Shown(h).text for h in re.findall(r"<h1[^>]*>.*?</h1>", html, re.S)]
    heading = next(
        "".join(Shown(h).text) for h in re.findall(r'<h2 class="section">.*?</h2>', html, re.S)
    )
    assert "".join(title).strip() == "Q&A [notes] for Monday"
    assert heading.startswith("Ana & Bo_Quill [x] · ")


# Work


@pytest.mark.parametrize("run", ["a", "<"], ids=["address-pattern", "angle-token"])
def test_a_long_message_converts_in_time_linear_in_its_length(run):
    """A run of word characters backtracks in _EMAIL (text.py:31), a run of "<" in _TOKENS'
    `<[^>\\n]+>` (text.py:32): each start reads to the run's end. note_from_message runs on
    the server's event loop (shortcuts._write calls it directly), so every request waits."""
    spent = {}
    for size in (5_000, 20_000):
        message = {"text": run * size, "ts": "1900000000.000100", "user": "U1"}
        start = time.perf_counter()
        note_from_message(
            message, team_id="T", team_domain="fable-works", conversation_id="C0LIB",
            author="Cedar Quill", names={},
        )  # fmt: skip
        spent[size] = time.perf_counter() - start
    assert spent[20_000] < 1.0, spent


# What held on the fix (each failed on 0d4e71a8)


@pytest.mark.parametrize(
    "message",
    [
        "a `\nb `[x](https://evil.test/p)` c",  # a lone backtick paired across a line
        "```This is a code block\nAnd it's multi-line```",  # Slack's own example
        "&gt; x\na\n=",  # a heading in a claim alone and in the body
        "1.\n\t```\n\n   [manual](https://evil.test/m)\n   ```",  # the seventh report's
        "Readings.\n\n    unit\tcolour\tcomment\n    north\tblue\t",  # the seventh report's
    ],
    ids=["lone-backtick", "slack-example", "quoted-underline", "report-link", "report-tab"],
)
def test_held_on_the_fix(filer, message):
    assert [
        f for f in faults(filer(message)) if f[0] in ("link", "heading", "code", "source")
    ] == []
