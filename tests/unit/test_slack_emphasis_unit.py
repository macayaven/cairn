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
        ("* * *", "\\* \\* \\*", "* * *"),  # no rule either: each a mark a list could start
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
        # Two spans that touch are written as one: Markdown reads `**a****b**` otherwise.
        ("*approve**not bold*", "**approvenot bold**", "approvenot bold", ["strong_open"]),
        ("*a**b**c* and _d__e_", "**abc** and _de_", "abc and de", ["strong_open", "em_open"]),
    ],
)
def test_emphasis_slack_shows_is_written_as_emphasis(text, body, shown, styles):
    draft = filed(text)
    assert body_of(draft) == body
    assert page_reads(body) == (shown, styles)


def test_a_reference_that_shows_nothing_is_as_if_it_were_not_there():
    """`<x|>` shows nothing, so the marks about it touch: `*a*<x|>*b*` was written
    `**a****b**`, whose asterisks a page shows (found trying the fix)."""
    body = body_of(filed("*a*<x|>*b* and 2 <x|> * 3"))
    assert body == "**ab** and 2  * 3"
    assert page_reads(body) == ("ab and 2  * 3", ["strong_open"])


@pytest.mark.parametrize(
    ("text", "body"),
    [
        ("*see <https://fable.test/x|docs>*", "\\*see [docs](https://fable.test/x)\\*"),
        ("_from <@U0BIRCH01>_", "\\_from @U0BIRCH01\\_"),
        ("*run `make` now*", "\\*run `make` now\\*"),
    ],
)
def test_a_mark_whose_partner_is_past_a_link_a_mention_or_code_is_typed(text, body):
    """The limit: marks are paired between Slack's links, mentions and code, not across
    them, so these show as typed, where Slack shows the span styled (the guide says so)."""
    assert body_of(filed(text)) == body


@pytest.mark.parametrize(
    ("text", "body"),
    [
        ("*(b)*<!date^1900000000^{date}|17 Mar>", "\\*\\(b\\)\\*17 Mar"),
        ("<x|label>*(b)*", "label\\*\\(b\\)\\*"),
        ("*[WIP]*<https://fable.test/x|PR 12>", "**\\[WIP\\]**[PR 12](https://fable.test/x)"),
        ("&gt; * item", "> \\* item"),
        ("- * item", "- \\* item"),
        ("1. * item", "1. \\* item"),
    ],
)
def test_a_span_markdown_cannot_read_as_slack_did_is_its_marks_typed(text, body):
    """Found trying the fix: a span whose marks touch a link's words or a date's on the
    outside and punctuation on the inside is one Markdown does not read, and showed its
    asterisks doubled; and a `*` after a quotation's or a list's mark began a list. Each
    is now the characters typed."""
    assert body_of(filed(text)) == body
    assert "**" not in page_reads(body)[0]


@pytest.mark.parametrize(
    ("text", "body"),
    [
        ("<@U0BIRCH01> * 3", "@U0BIRCH01 * 3"),
        ("`x` * 3", "`x` * 3"),
        ("<https://fable.test/x|docs> * 3", "[docs](https://fable.test/x) * 3"),
        ("<!date^1900000000^{date}|17> * 3", "17 * 3"),
        ("- <@U0BIRCH01> * 3", "- @U0BIRCH01 * 3"),
        ("<x|12>. * 3", "12. \\* 3"),
        ("<x|1>2. * 3", "12. \\* 3"),
        ("<x|- > * 3", "-  \\* 3"),
    ],
)
def test_a_line_starts_with_the_marks_written_on_it_whatever_wrote_them(text, body):
    """Found trying the fix: a `*` between blanks after a mention, code or a link was given a
    backslash, as if it started its line, and `<x|12>. * 3` was written `12. * 3`, a list in
    a list, as if `. * 3` did. What starts a line is what the note writes on it, a reference's
    words included."""
    assert body_of(filed(text)) == body
    assert page_reads(body)[0].count("*") == 1  # the page shows it, and no list's bullet


def test_the_title_writes_a_span_as_markdown_reads_it():
    title = filed("*[WIP]*<https://fable.test/x|PR 12>").title  # the link is its words alone
    assert title == "\\*\\[WIP\\]\\*PR 12"


PARTS = ["*", "_", "a", " ", "(", "\N{GRINNING FACE}", "<x|b>", "<@U0CEDAR01>"]


def test_no_page_shows_a_mark_slack_did_not_display():
    """Every message of up to four of these parts: the page shows no more `*` or `_` than
    the message holds, in the note's body and in the heading of its title."""
    messages = [""]
    for _ in range(4):
        messages = [m + part for m in messages for part in PARTS if len(m) < 60]
        for text in messages:
            if not text.strip():
                continue
            draft = filed(text)
            heading = READER.parse(f"# {draft.title}")[1].children or []
            for shown in (page_reads(body_of(draft))[0], "".join(t.content for t in heading)):
                for mark in "*_":
                    assert shown.count(mark) <= text.count(mark), (text, body_of(draft), shown)
