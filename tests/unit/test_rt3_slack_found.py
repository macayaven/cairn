"""What the generated messages found (test_rt3_slack_pages), each shrunk and pinned, in three
parts:

- Fixed: the third tester's findings 1, 2, 3, 4, 6, 7 (the reply in Slack) and 9, ruled
  fixed on 9d1abc2f (each failed on 65e1a416); and what its second attack found, ruled fixed
  on 3b99dc2a (each failed on 9d1abc2f): special mentions, emphasis Slack does not show,
  the reply's title in plain text, rich text's code blocks in linear time.
- Stated limits: what the connect guide states, each test naming the guide's sentence and
  asserting what it says: findings 5 and its sentence completed, 7 (the page's own title and
  author heading) and 8, a code block in a quotation, emphasis across a link, a mention or
  code.
- Held since the seventh report's fix (each failed on 0d4e71a8).

Nobody has checked against Slack how it decodes entities in a plain-text element, or whether
it honours `"mrkdwn": false` on a reply sent to a `response_url`; the tests of the reply take
the reply as it is sent."""

import asyncio
import re
import time
from pathlib import Path

import pytest
import test_slack_shortcuts_unit as shortcuts
from markdown_it import MarkdownIt
from starlette.testclient import TestClient
from test_rt3_slack_model import decode
from test_rt3_slack_pages import (
    LIMIT_EMPHASIS,
    LIMIT_HEADINGS,
    LIMIT_MENTION,
    LIMIT_PASSAGE,
    LIMIT_PASSAGE_MARKS,
    LIMIT_QUOTED_BLOCK,
    SOURCE,
    Filer,
    Shown,
    faults,
)
from test_slack_commands_unit import ADA_SLACK, BASE, REPLY, Bench

import cairn
from cairn.server import create_app
from cairn.slack.text import note_from_message
from cairn.web.markdown import render
from cookie_names import session_cookie

READER = MarkdownIt("commonmark", {"html": False}).enable("table")
GUIDE = Path(cairn.__file__).parent / "guides" / "connect.md"
BIRCH = "U0BIRCH01"


@pytest.fixture
def filer(tmp_path):
    return Filer(tmp_path)


def links(markdown: str) -> list[tuple[str, str]]:
    return Shown(READER.render(markdown)).links + Shown(str(render(markdown))).links


def guide_says(sentence: str) -> bool:
    return " ".join(sentence.split()) in " ".join(GUIDE.read_text().split())


def claim_code(filed) -> list[str]:
    return [text for claim in filed.claims for _, text in Shown(str(render(claim))).code]


def claim_text(filed) -> str:
    return "".join(text for claim in filed.claims for text in Shown(str(render(claim))).text)


def filed_on_bench(tmp_path, *texts: str, users: dict | None = None):
    """The messages filed in turn on one workspace: its pages (title, body) and each reply
    in Slack, as sent."""
    bench = Bench(tmp_path)
    try:
        bench.users[shortcuts.CEDAR] = shortcuts.cedar()
        bench.users.update(users or {})
        bench.link(ADA_SLACK, bench.ada, bench.library)
        for n, text in enumerate(texts):
            message = {"type": "message", "user": shortcuts.CEDAR, "text": text}
            response = shortcuts.handle(bench, {**message, "ts": f"19000000{n:02d}.000100"})
            asyncio.run(response.background())
        pages = [
            bench.owner.get_page(bench.library.id, listed.slug)
            for listed in bench.owner.list_pages(bench.library.id)
        ]
        replies = [body for where, body in bench.calls if where == REPLY]
        return pages, replies
    finally:
        bench.store.close()


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


def sections(reply: dict) -> list[tuple[str, str]]:
    """A reply's sections, each as its text object's type and text."""
    return [(b["text"]["type"], b["text"]["text"]) for b in reply.get("blocks", []) if "text" in b]


def named(reply: dict) -> str:
    """The title a reply names, as its plain-text section reads (entities decoded, as the
    harness assumes; unchecked against Slack)."""
    plain = [text for kind, text in sections(reply) if kind == "plain_text"]
    assert reply.get("mrkdwn") is False and len(plain) == 1, reply
    return decode(plain[0])


# Fixed, and held as fixed (the third tester's findings, ruled fixed)


@pytest.mark.parametrize(
    "message",
    [
        "-     [docs](https://evil.test/x)",
        "&gt;     [docs](https://evil.test/x)",
        "1.\t\t![chart](https://evil.test/x.png)",
        "```[docs](https://evil.test/x) ![p](https://evil.test/p.png)```",
        "<https://evil.test/x|[docs](https://evil.test/y)>",
    ],
    ids=["list-item-code", "quoted-code", "image", "code-block", "link-words"],
)
def test_1_a_title_is_always_written_as_prose(filer, message):
    """Finding 1: the body's heading of the title links nothing, shows no image or code."""
    filed = filer(message)
    assert filed.body is not None
    heading = READER.render(filed.body.split("\n", 1)[0])
    assert "<img" not in heading and "<code" not in heading and "<a " not in heading, heading
    slack_own = {"https://evil.test/x"} if message.startswith("<https") else set()
    assert not [h for h, _ in links(filed.body) if "evil.test" in h and h not in slack_own]


@pytest.mark.parametrize(
    "tail",
    [
        "`[manual](https://evil.test/m)` today.",
        "[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[(https://evil.test/m)",
        "&amp;&amp;&amp;&amp;&amp;&amp;&amp;&amp;&amp;&amp;&amp;&amp;&amp;&amp;",
    ],
    ids=["code-span", "brackets", "entities"],
)
def test_2_the_cut_never_splits_an_escape(filer, tail):
    """Finding 2: a title cut at 80 characters links nothing and displays the start of the
    first line, whatever escape or entity stands at the cut."""
    lead = "Read the setup notes before the Monday review: see"
    filed = filer(lead + tail)
    assert filed.body is not None and filed.title is not None
    heading = READER.render(filed.body.split("\n", 1)[0])
    assert not [href for href, _ in links(filed.body) if "evil.test" in href]
    shows = "".join(Shown(heading).text).strip()
    assert shows.startswith(lead), shows
    assert "\\" not in shows and "&amp;" not in shows, shows


@pytest.mark.parametrize(
    "rule", ["~~~", "~~~~~~~~~~", "  ~~~", "- ~~~", "&gt; ~~~", "1) ~~~", "&gt; - ~~~", "~~~`"]
)
def test_3_no_prose_line_opens_a_fence(filer, rule):
    """Finding 3: a line of tildes after any lead is text, and the source link a link."""
    filed = filer(f"Steps for Monday\n{rule}\nsee the ledger")
    shown = [link for claim in filed.claims for link in Shown(str(render(claim))).links]
    assert (SOURCE, "Source in Slack") in shown
    assert not claim_code(filed), filed.claims


@pytest.mark.parametrize(
    "texts",
    [
        ("```port 8811```\nShelf.", "```SELECT 1;```\nLedger."),
        ("```\nport 8811\n```\nShelf.", "```\nSELECT 1;\n```\nLedger."),
    ],
    ids=["block-on-one-line", "fence-on-its-own-line"],
)
def test_4_the_title_is_the_first_line_slack_displays(tmp_path, texts):
    """Finding 4: a message that opens with a code block is titled by its first line."""
    pages, _ = filed_on_bench(tmp_path, *texts)
    assert sorted(page.title for page in pages) == ["SELECT 1;", "port 8811"]


def test_6_struck_text_is_written_as_typed(filer):
    """Finding 6: `~done~` is written as typed, and the page shows one tilde each side."""
    filed = filer("Ledger ~done~")
    assert "~done~" in claim_text(filed) and "~~" not in claim_text(filed), filed.claims


def test_7_the_reply_in_slack_shows_what_slack_displayed(tmp_path):
    """Finding 7, the reply: it names the note by its title as Slack displayed it, in a
    section of plain text of its own, the reply saying `"mrkdwn": false` so that its
    fallback text is plain too; no mrkdwn section holds the title, nor links it. (Slack's
    decoding of entities in plain text, and its honouring of `"mrkdwn": false` on a reply
    to a `response_url`, are unchecked.)"""
    _, (reply,) = filed_on_bench(tmp_path, "Q&amp;A [notes] for *Monday* <!here>")
    assert named(reply) == "Q&A [notes] for Monday @here", reply
    assert sections(reply)[-1][0] == "plain_text", reply
    assert not [text for kind, text in sections(reply) if kind == "mrkdwn" and "Q&" in text]
    assert decode(reply["text"]).startswith("Filed: Q&A [notes] for Monday @here\n"), reply


@pytest.mark.parametrize("run", ["a", "<", "`", "*a* ", "~~~\n", "- ~~~\n", "a@"])
def test_9_a_long_message_converts_in_time_linear_in_its_length(run):
    """Finding 9: 20,000 characters of any one shape within a second, here and in the
    title's path; the conversion runs off the event loop (shortcuts._write)."""
    spent = {}
    for size in (5_000, 20_000):
        text = (run * size)[:size]
        message = {"text": text, "ts": "1900000000.000100", "user": "U1"}
        start = time.perf_counter()
        note_from_message(
            message, team_id="T", team_domain="fable-works", conversation_id="C0LIB",
            author="Cedar Quill", names={},
        )  # fmt: skip
        spent[size] = time.perf_counter() - start
    assert spent[20_000] < 1.0, spent


# Stated limits: each asserts the limit as the connect guide states it


def test_limit_5_a_line_read_as_code_in_its_passage_alone_keeps_its_escaping(filer):
    """A quoted line, then a tab-indented one: the note reads the second as the quotation's
    text going on, its passage alone as code, which shows the escapes and entities that
    write it as text, and not the tab that ends it."""
    assert guide_says(LIMIT_PASSAGE)
    filed = filer("&gt; Readings\n\t[x] &amp; y\t")
    assert claim_code(filed) == ["\\[x\\] &amp; y\n"], (filed.claims, claim_code(filed))


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("&gt; Readings\n\t*north* blue", "**north** blue\n"),
        ("&gt; Readings\n\t`port` blue", "`port` blue\n"),
    ],
    ids=["bold", "code-span"],
)
def test_limit_5_the_passage_shows_the_rest_of_its_markdown(filer, message, code):
    """The same line, read as code in its passage alone, shows the rest of the Markdown that
    writes it too: bold's doubled asterisks, the backticks about code within a line."""
    assert guide_says(LIMIT_PASSAGE_MARKS)
    assert claim_code(filer(message)) == [code]


def test_limit_7_the_page_title_and_author_heading_show_their_escapes(tmp_path):
    """The page's h1 is the title and its section heading the author's line as the note
    writes them for Markdown, shown as plain text: escapes, entities and bold's asterisks."""
    assert guide_says(LIMIT_HEADINGS)
    html = served(tmp_path, "Ana & Bo_Quill [x]", "Q&amp;A [notes] for *Monday*")
    (title,) = ["".join(Shown(h).text) for h in re.findall(r"<h1[^>]*>.*?</h1>", html, re.S)]
    heading = next(
        "".join(Shown(h).text) for h in re.findall(r'<h2 class="section">.*?</h2>', html, re.S)
    )
    assert title.strip() == "Q&amp;A \\[notes\\] for **Monday**"
    assert heading.startswith("Ana &amp; Bo\\_Quill \\[x\\] · ")


def test_limit_8_a_mention_of_anyone_but_the_author_is_their_member_id(tmp_path):
    """The shortcut asks Slack for the author's name alone, so another person shows as their
    member ID, in prose and in code, though Slack knows the name."""
    assert guide_says(LIMIT_MENTION)
    birch = shortcuts.cedar(id=BIRCH, real_name="Birch Fennel", profile={"real_name": "B"})
    text = f"Ask <@{BIRCH}> on Monday\n\n    ping <@{BIRCH}>, <@{shortcuts.CEDAR}>"
    (page,), _ = filed_on_bench(tmp_path, text, users={BIRCH: birch})
    claims = "\n".join(claim.text for claim in page.claims)
    assert claims.count(f"@{BIRCH}") == 2 and "Birch" not in page.body, claims
    assert "@Cedar Quill" in claims


@pytest.mark.parametrize(
    ("text", "author", "title"),
    [
        ("```*draft* _v2_ ~old~\nport 8811```", "Cedar Quill", "*draft* _v2_ ~old~"),
        (f"<@{shortcuts.CEDAR}> standup", "*Cedar*", "@*Cedar* standup"),
    ],
    ids=["code-block", "author-name"],
)
def test_the_reply_shows_as_characters_what_slack_displayed_so(tmp_path, text, author, title):
    """Found on 9d1abc2f: the reply named the title in mrkdwn, which styled marks Slack had
    shown as characters (a code block's first line; a name). It is now plain text."""
    who = shortcuts.cedar(real_name=author, profile={"real_name": author, "display_name": author})
    _, (reply,) = filed_on_bench(tmp_path, text, users={shortcuts.CEDAR: who})
    assert named(reply) == title, reply


@pytest.mark.parametrize("special", ["here", "everyone"])
def test_a_special_mention_shows_as_slack_displayed_it(tmp_path, special):
    """Found on 9d1abc2f: Slack displays <!here> as "@here"; the note wrote "[link omitted]"
    in the claim, the title and the reply."""
    (page,), (reply,) = filed_on_bench(tmp_path, f"Standup <!{special}> at ten")
    shown = "".join(text for claim in page.claims for text in Shown(str(render(claim.text))).text)
    assert f"@{special}" in shown and "link omitted" not in shown, shown
    assert "link omitted" not in page.title, page.title
    assert named(reply) == f"Standup @{special} at ten", reply


def test_code_blocks_slack_names_in_rich_text_convert_in_linear_time():
    """Found on 9d1abc2f: each code block the rich text names was looked for to the text's
    end (text._known_code), so 40,000 characters of ```x``` took about 1.6 s against 0.14 s
    for 10,000."""
    spent = {}
    for size in (10_000, 40_000):  # the best of three, so a busy machine does not decide
        count = size // 7
        block = {"type": "rich_text_preformatted", "elements": [{"type": "text", "text": "x"}]}
        message = {
            "text": "```x```" * count, "ts": "1900000000.000100", "user": "U1",
            "blocks": [{"type": "rich_text", "elements": [block] * count}],
        }  # fmt: skip
        times = []
        for _ in range(3):
            start = time.perf_counter()
            note_from_message(
                message, team_id="T", team_domain="fable-works", conversation_id="C0LIB",
                author="Cedar Quill", names={},
            )  # fmt: skip
            times.append(time.perf_counter() - start)
        spent[size] = min(times)
    assert spent[40_000] < 6 * spent[10_000], spent


def test_asterisks_slack_did_not_pair_are_not_shown_doubled(tmp_path):
    """Found on 9d1abc2f: `2 * 3 * 4` showed as `2 ** 3 ** 4` in the claim, the page's
    heading of the title and the reply. Slack shows the two it was sent."""
    (page,), (reply,) = filed_on_bench(tmp_path, "2 * 3 * 4 is 24")
    shown = "".join(text for claim in page.claims for text in Shown(str(render(claim.text))).text)
    heading = "".join(Shown(READER.render(page.body.split("\n", 1)[0])).text)
    assert shown.count("*") <= 2, shown
    assert heading.count("*") <= 2, heading
    assert named(reply) == "2 * 3 * 4 is 24", reply


@pytest.mark.parametrize(
    ("message", "block"),
    [("&gt; ```\n&gt; ls\n&gt; ```", "ls\n"), ("&gt; ```ls\n&gt; &gt; out```", "ls\n> out\n")],
    ids=["fence-lines", "one-quoted-deeper"],
)
def test_limit_a_code_block_in_a_quotation_is_written_without_its_marks(filer, message, block):
    """A code block in a quotation loses the quotation's mark that starts each of its lines,
    and only that: a line quoted deeper keeps the marks past the first. What Slack shows in
    such a block is inferred (its page does not say)."""
    assert guide_says(LIMIT_QUOTED_BLOCK)
    assert claim_code(filer(message)) == [block]


@pytest.mark.parametrize(
    "message",
    [
        "*see <https://good.test/a|the notes>*",
        f"*ask <@{shortcuts.CEDAR}> now*",
        "*run `make` now*",
    ],
    ids=["link", "mention", "code"],
)
def test_limit_emphasis_across_a_link_a_mention_or_code_is_typed(filer, message):
    """Emphasis is Slack's only with no link, mention or code between its marks (cairn's
    reading of Slack, whose page does not say); across one, the marks show as typed."""
    assert guide_says(LIMIT_EMPHASIS)
    filed = filer(message)
    shown = "".join(Shown(str(render(filed.claims[0]))).text).strip()
    assert shown.startswith("*") and shown.endswith("*"), filed.claims


# Held since the seventh report's fix (each failed on 0d4e71a8)


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
