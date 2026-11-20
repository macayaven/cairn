"""A filed Slack message gets a backslash only where a reader asked for one, and its code
is what Slack displayed (sixth round, finding 3).

The note holds one heading, the author's: a line of the message that would read as another
gets a backslash and reads as text. The fifth round wrote that backslash on more lines than
that: below the first such line, on every line of only "=" or "-", a rule and a line of an
indented code block among them, though no reader read a heading there. And the rules that
write a message as prose (a backslash before a bracket, a doubled asterisk) were applied to
every line, the ones the note's Markdown reads as code too, where they are shown as they
are. The note says whose words it holds, so a character added for nothing is a false
quotation.

What holds, asked of the two readers themselves (the passage splitter and the Markdown parser
as the web view reads) and never of the converter:
- the note has one heading for both readers;
- a line the parser reads as code, fenced or indented, both in the note whole and in its own
  passage alone (as the splitter cut it, which is what a page shows of a claim), holds what
  Slack displayed for it: its entities read, a name, a mention and a link in the converter's
  words, an email address removed, and no backslash and no doubled mark of the prose rules;
- every other line is as the prose rules wrote it on 22f498b6 (their text for these messages
  is pinned by its digest), but for the backslashes that keep one heading; so a line only one
  of the two readings takes for code keeps its backslashes;
- no passage, read alone, holds a link, an image, emphasis, a heading or a reference that the
  note made of prose alone does not hold there: what Slack displayed as plain characters is
  never markup on a page;
- when the message has fewer than eight lines that hold a "#" or are only "=" or "-" under a
  line that is not blank, every one of those backslashes is needed: taken out alone, a
  reader finds a second heading;
- with eight or more such lines the backslash is written as the fifth round wrote it (the
  limit, pinned below), except in code: there too only where a reader needs it.

The messages are generated from a seed, so every run reads the same ones, each line with
what Slack displayed for it written beside it by hand, never worked out by a second
converter. What they found is in the tables. Every name is made up.
"""

import contextlib
import hashlib
import random
import re

import pytest
from markdown_it import MarkdownIt
from test_slack_one_heading_unit import bench, faults, filed, passes  # noqa: F401  (a fixture)

from cairn.compiler import claim_id, render_body
from cairn.slack import text as slack_text
from cairn.spans import chunk
from cairn.store import Claim
from cairn.web import markdown as web_markdown

READER = MarkdownIt("commonmark", {"html": False}).enable("table")  # as the web view reads
ONLY_MARKS = re.compile(r"[ \t>]*(=+|-+)[ \t]*")  # a line that could underline the one above
SEED, CASES = 20261120, 800
NAMES = {
    "U0QUILL01": "Cedar\u2028Quill #2",
    "U0BROOK01": "\u2028Brook\n",
    "C456": " Ada Park",
    "C789": "c# shelf",
}
# The notes of the generated messages that hold no backtick (and of every fourth of the
# others, without its lines that hold one), as the prose rules wrote them on 22f498b6, before
# a heading became text: sha256 of them all, each followed by a NUL. Changed on purpose by
# the third tester's finding 3: a run of three tildes that could open a fence gets a
# backslash before its first tilde; with those backslashes taken out the digest is
# ad3d2c518b9d1929204ce131048c065c5201df7fe8e4057253c8bf8550ef96c0, as on 22f498b6. And by
# finding 6: struck text is written as typed, so af92e9a5627fc9c37e3e68294cd35d0dbf39c7e18d17
# 93534299acf7ee51ee33 became c51e4820300f3fd9e332b6fb7193ca305acc7c7459c6b166366785de771cb9b2,
# every changed line of it a line of then with fewer tildes. And by the tester's second attack,
# finding 2: a `*` or `_` is emphasis only where Slack shows it, else the character typed, so
# that digest became this, every changed line of it (221 of 112 notes) a line of then with
# only its asterisks and the backslashes before `*` and `_` different.
PROSE_ON_22F498B6 = "3455dfd5af5f6fda1d263000ffc145e5bc2de1508cc8d4954c4ca82fa3636cc0"


@pytest.fixture
def notes(monkeypatch):
    """From a message's text to its note as the prose rules write it, before its headings
    become text, and to the note that is filed. `as_prose` is then the note made of that
    prose alone, as 8c502b3c filed it. (A message is written again when a code block of it
    is not one fence in the finished note: the prose is then that of the last writing.)"""
    seen, made, make_text = [], [], slack_text._one_heading
    written, put = [0], getattr(slack_text, "_code_as_shown", None)

    def watched(note: str) -> str:
        seen.append("\n".join(note.splitlines()))
        made.append(make_text(note))
        return made[-1]

    def putting(text: str, shown: str) -> str:
        written.append(len(seen))
        return put(text, shown)

    monkeypatch.setattr(slack_text, "_one_heading", watched)
    monkeypatch.setattr(slack_text, "_code_as_shown", putting, raising=False)

    def convert(text: str) -> tuple[str, str]:
        seen.clear()
        made.clear()
        written[:] = [0]
        draft = slack_text.note_from_message(
            {"text": text, "ts": "1900000000.000100"},
            team_id="T0FABLE01",
            team_domain="fable-works",
            conversation_id="C0LIBRARY1",
            author="Cedar Quill",
            names=NAMES,
        )
        convert.as_prose = made[written[-1]]  # the note made of the prose alone
        return seen[written[-1]], draft.text

    return convert


def backslashes(was: str, now: str) -> list[int] | None:
    """The columns of the backslashes the line `now` holds and `was` does not; None when the
    two differ in anything else."""
    at, columns = 0, []
    for column, character in enumerate(now):
        if at < len(was) and was[at] == character:
            at += 1
        elif character == "\\":
            columns.append(column)
        else:
            return None
    return columns if at == len(was) else None


def added(before: str, after: str) -> list[tuple[int, int]]:
    """The line and column of each backslash `after` holds and `before` does not. Nothing
    else may differ."""
    found = []
    for n, (was, now) in enumerate(zip(before.split("\n"), after.split("\n"), strict=True)):
        columns = backslashes(was, now)
        assert columns is not None, (was, now)
        found += [(n, column) for column in columns]
    return found


def one_heading(note: str) -> bool:
    """One heading, the author's, for the splitter and for the parser, in the note whole and
    in each of its passages read alone."""
    spans = chunk("", note)
    return not faults(note, spans) and not any(
        token.type == "heading_open" for span in spans[1:] for token in READER.parse(span.text)
    )


def spare(note: str, places: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Those of the backslashes at `places` the note could do without: taken out alone, both
    readers still find one heading."""
    lines, found = note.split("\n"), []
    for n, column in places:
        without = [*lines[:n], lines[n][:column] + lines[n][column + 1 :], *lines[n + 1 :]]
        if one_heading("\n".join(without)):
            found.append((n, column))
    return found


def not_needed(before: str, after: str) -> list[tuple[int, int]]:
    return spare(after, added(before, after))


def code_lines(note: str) -> set[int]:
    """The lines the Markdown parser reads as code, fenced or indented."""
    return {
        line
        for token in READER.parse(note)
        if token.type in ("fence", "code_block")
        for line in range(*token.map)
    }


def code_to_both(note: str) -> set[int]:
    """The lines the parser reads as code in the note whole and in their own passage alone,
    as the splitter cut it."""
    alone = {
        span.start_line - 1 + line for span in chunk("", note) for line in code_lines(span.text)
    }
    return code_lines(note) & alone


MARKUP = ("link_open", "image", "em_open", "strong_open", "heading_open")


def markup_of(note: str) -> list:
    """Each passage of the note, read alone as a page shows a claim: its lines, the links
    (with where they lead), images, emphasis and headings the parser makes of it, and the
    references it defines."""
    found = []
    for span in chunk("", note):
        defined: dict = {}
        marks = sorted(
            (token.type, token.attrGet("href") or token.attrGet("src") or "")
            for block in READER.parse(span.text, defined)
            for token in [block, *(block.children or [])]
            if token.type in MARKUP
        )
        found.append((span.start_line, span.end_line, marks, sorted(defined.get("references", {}))))
    return found


def may_be_headings(note: str) -> int:
    """How many lines below the author's hold a "#", or are only "=" or "-" under a line
    that is not blank: no other line can read as a heading."""
    lines = note.split("\n")
    return sum(
        "#" in line or bool(ONLY_MARKS.fullmatch(line) and lines[n - 1].strip())
        for n, line in enumerate(lines)
        if n
    )


def body(note: str) -> str:
    return note.split("UTC\n\n", 1)[1].split("\n\n[Source in Slack]")[0]


def as_displayed(before: str, displayed: str) -> str:
    """The note `before` with, between the author's heading and the link, the message as
    Slack displayed it."""
    lines, shown = before.split("\n"), displayed.lstrip().split("\n")
    while not shown[-1].strip():  # the blank lines that end a message go, as from its prose
        del shown[-1]
    return "\n".join([*lines[:2], *shown, *lines[-2:]])


# What Slack sends for a piece of a line, and what it displayed. The first seven hold no
# reference, so a code block of Slack's own displays them the same way.
PIECES = [
    ("items[0] = (a) * 2 * 3", "items[0] = (a) * 2 * 3"),
    ("*bold* _slanted_ ~gone~", "*bold* _slanted_ ~gone~"),
    ("a &lt; b &amp;&amp; c &gt; d", "a < b && c > d"),
    ("# a comment", "# a comment"),
    ("x | y", "x | y"),
    ("![chart](shelf.png) back\\slash", "![chart](shelf.png) back\\slash"),
    ("quill@fable.test", "[email removed]"),
    ("`tick[0]` and `~tock~`", "`tick[0]` and `~tock~`"),
    ("<@U0QUILL01>", "@Cedar Quill #2"),
    ("<@U0BROOK01>", "@ Brook"),
    ("<#C789|shelf>", "#c# shelf"),
    ("<#C999|*loud* shelf>", "#*loud* shelf"),
    ("<https://fable.test/a_b|Issue #4 [open]>", "[Issue #4 [open]](https://fable.test/a_b)"),
    ("<https://fable.test/a_b>", "[https://fable.test/a_b](https://fable.test/a_b)"),
    ("<shelf nine|a *label*>", "a *label*"),
    ("<nowhere>", "[link omitted]"),
    ("<x|>", ""),
]
CLOSES = ("<x|~~~>", "~~~")  # a label that ends a fence as displayed, and not as prose
HEADING_LIKE = [
    ("<#|> Ada Park", "# Ada Park"),
    ("<#C456|shelf>", "# Ada Park"),
    ("<https://fable.test|# Ada Park>", "[# Ada Park](https://fable.test)"),
    ("<https://fable.test|===>", "[===](https://fable.test)"),
    ("<x|>", ""),
]
MARKUP_LINES = [  # each is markup when it is read as prose as Slack displayed it
    ("[the ledger](https://fable.test/elsewhere)",) * 2,
    ("![chart](https://fable.test/p.png)",) * 2,
    ("*bold* and _slanted_",) * 2,
    ("# Ada Park",) * 2,
    ("&lt;https://fable.test/elsewhere&gt;", "<https://fable.test/elsewhere>"),
    ("[Ada]: https://fable.test/elsewhere",) * 2,
    ("Ask [Ada] about it.",) * 2,
]
LEFT_OPEN = [  # an opening fence, a line, and what closes it for one reader and not the other
    (" ~~~", "code", "    ~~~"),
    ("~~~~", "code", "~~~"),
    ("- ~~~", "  code", "~~~"),
    ("&gt; ~~~", "&gt; code", "~~~"),
]
# What Slack's own code may hold: each is markup when it is read as prose. "qzx" is written
# nowhere else, so whatever a reader makes of it but code or plain characters is a fault.
MARKED = [
    ("[qzx](https://fable.test/qzx)",) * 2,
    ("![qzx](https://fable.test/qzx.png)",) * 2,
    ("# qzx",) * 2,
    ("*qzx* _qzx_ **qzx**",) * 2,
    ("&lt;b&gt;qzx&lt;/b&gt;", "<b>qzx</b>"),
    ("&lt;https://fable.test/qzx&gt;", "<https://fable.test/qzx>"),
    ("[qzx]: https://fable.test/qzx",) * 2,
    ("qzx | [qzx](https://fable.test/qzx)",) * 2,
    ("qzx `` and ` qzx",) * 2,
    ("qzx\t",) * 2,
    ("qzx  ",) * 2,
    ("", ""),
]
WITHIN_A_LINE = [  # code within a line, and backticks Slack paired with no other
    ("Row 4 `[qzx](https://fable.test/qzx)` Row 5",) * 2,
    ("`*qzx*` and `_qzx_`",) * 2,
    ("`qzx|[qzx](https://fable.test/qzx)`",) * 2,
    ("`&lt;b&gt;qzx`", "`<b>qzx`"),
    ("`# qzx`",) * 2,
    ("`[qzx](https://fable.test/qzx)``Row 4`",) * 2,
    ("Row 4 `",) * 2,
    ("` Row 5",) * 2,
    ("``",) * 2,
    ("Row 4 `` Row 5 ` Row 6",) * 2,
]
PADS = [" " * n for n in range(9)] + ["\t", " \t"]
MARKS = ["-", "--", "---", "----", "=", "==", "===", "- "]
WORDS = ["Row 4.", "port: 8811", "Approve transfer 99."]
KINDS = [
    "marks", "marks", "marks", "heading", "heading", "empty heading", "words", "words", "blank",
    "blank", "rule", "tildes", "item", "numbered", "quoted", "indented", "table", "reference",
    "text", "text", "indented text", "indented text", "code block", "tilde block",
    "markup", "left open", "code block", "code block", "within a line", "within a line",
]  # fmt: skip
# Lines as Slack sends them, each with what it displayed beside it: for a code block of
# Slack's own, the fence on lines of its own that the note holds for it.
Lines = list[tuple[str, str]]


def some_text(rng: random.Random, pieces: Lines = PIECES) -> tuple[str, str]:
    chosen = [rng.choice(pieces) for _ in range(rng.randint(1, 3))]
    if pieces is PIECES and not rng.randrange(40):
        chosen = [CLOSES]
    return " ".join(sent for sent, _ in chosen), " ".join(shown for _, shown in chosen)


def under(lead: str, line: tuple[str, str]) -> Lines:
    return [(lead + line[0], lead.replace("&gt;", ">") + line[1])]


def a_code_block(rng: random.Random) -> Lines:
    """A code block of Slack's own, in one of the shapes a message gives it, and the fence
    the note holds for it: at the margin, on lines of its own, what Slack displayed between."""
    if not rng.randrange(25):  # a line of tildes in it would close the fence of tildes about it
        sent, shown = rng.choice(MARKED[:9])
        return [(f"~~~\n```\n~~~\n{sent}\n```\n~~~", f"~~~\n```\n~~~\n{shown}\n```\n~~~")]
    shape = rng.randrange(12)
    solid = shape in (3, 8, 9, 10, 11)  # its first and last lines touch the backticks
    inside = [
        under(rng.choice(PADS), rng.choice(MARKED[:9] if solid else MARKED))[0]
        if rng.random() < 0.7
        else under(rng.choice(PADS), some_text(rng, PIECES[:7]))[0]
        for _ in range(rng.randint(1, 3))
    ]
    sent, shown = ("\n".join(side) for side in zip(*inside, strict=True))
    fence = f"```\n{shown}\n```"
    if shape == 2:
        return [(f"   ```\n{sent}\n   ```", fence)]
    if shape == 3:
        return [(f"Row 4 ```{sent}``` Row 5", f"Row 4 \n{fence}\nRow 5")]
    if shape == 4:
        return [(f"Row 4. ```\n{sent}\n```", f"Row 4. \n{fence}")]
    if shape == 5:
        return [(f"&gt; ```\n{sent}\n```", f"> \n{fence}")]
    if shape == 6:
        return [(f"- Row 5.\n  ```\n{sent}\n  ```", f"- Row 5.\n{fence}")]
    if shape == 7:
        return [(f"```\n\n{sent}\n\n```", f"```\n\n{shown}\n\n```")]
    if shape == 8:
        return [(f"```\n{sent}```", fence)]
    if shape == 9:
        return [(f"```{sent}``````Row 6.```", f"{fence}\n```\nRow 6.\n```")]
    if shape == 10:  # right after code within a line, and after a mention
        return [(f"`Row 4````{sent}```", f"`Row 4`\n{fence}")]
    if shape == 11:
        return [(f"<@U0BROOK01>```{sent}```", f"@ Brook\n{fence}")]
    return [(f"```\n{sent}\n```", fence)]


def a_tilde_block(rng: random.Random) -> Lines:
    """A fence only Markdown knows: to Slack its lines are prose."""
    pad = rng.choice(PADS[:5])
    inside = [under(rng.choice(PADS), some_text(rng))[0] for _ in range(rng.randint(1, 3))]
    return [(pad + "~~~",) * 2, *inside, *([(pad + "~~~",) * 2] if rng.random() < 0.7 else [])]


def left_open(rng: random.Random, fence: tuple[str, str, str]) -> Lines:
    below = [under(rng.choice(PADS[:5]), rng.choice(MARKUP_LINES))[0] for _ in range(1, 3)]
    return [*[(line, line.replace("&gt;", ">")) for line in fence], *below]


def some_lines(rng: random.Random) -> Lines:
    pad, kind = rng.choice(PADS), rng.choice(KINDS)
    if kind == "markup":
        return under(pad, rng.choice(MARKUP_LINES))
    if kind == "left open":
        return left_open(rng, rng.choice(LEFT_OPEN))
    if kind == "within a line":
        return under(pad, rng.choice(WITHIN_A_LINE))
    if kind == "marks":
        return under(pad, (rng.choice(MARKS),) * 2)
    if kind == "heading":
        return under(pad + "#" * rng.randint(1, 3), (" Ada Park",) * 2)
    if kind == "empty heading":
        return under(pad, ("# ",) * 2)
    if kind == "words":
        return under(pad, (rng.choice(WORDS),) * 2)
    if kind == "rule":  # under a blank line: a thematic break, never an underline
        return [("", ""), (pad[:3] + "---",) * 2]
    if kind == "tildes":
        return under(pad, ("~~~",) * 2)
    if kind == "item":
        marker = rng.choice(["- ", "* ", "+ ", "1. ", "- - "])
        choices = [("Row 5.",) * 2, ("---",) * 2, ("# Ada Park",) * 2, ("", ""), some_text(rng)]
        return under(pad + marker, rng.choice(choices))
    if kind == "numbered":
        return under(pad, ("1.",) * 2)
    if kind == "quoted":
        choices = ["Row 6.", "---", "===", "# Ada Park", "- Row 7."]
        line = rng.choice([(rng.choice(choices),) * 2, under(rng.choice(PADS), some_text(rng))[0]])
        return under(pad + "&gt; ", line)
    if kind == "indented":
        return under("    ", (rng.choice(["---", "===", "# not a heading", "port: 8811"]),) * 2)
    if kind == "table":
        return [(rng.choice(["| Row |", "|---|"]),) * 2]
    if kind == "reference":
        return under(pad, rng.choice(HEADING_LIKE))
    if kind == "text":
        return under(pad, some_text(rng))
    if kind == "indented text":  # with a blank line above it or not
        return [*([("", "")] if rng.random() < 0.5 else []), *under("    " + pad, some_text(rng))]
    if kind == "code block":
        return a_code_block(rng)
    if kind == "tilde block":
        return a_tilde_block(rng)
    return [("", "")]


def a_readme(rng: random.Random) -> Lines:
    """Eight to forty headings, as a README pasted into Slack has them: under each, nothing,
    a sentence, a rule, or commands with their comments, indented, fenced or in a list."""
    lines: Lines = []
    for n in range(rng.randint(8, 40)):
        lines.append(("#" * rng.randint(1, 3) + f" Shelf {n}",) * 2)
        part = rng.randrange(8)
        if part == 1:
            lines += under("", some_text(rng))
        elif part == 2:
            lines += [("", ""), ("---",) * 2, ("", "")]
        elif part == 3:
            lines += [("", ""), ("    # as root",) * 2, *under("    ", some_text(rng)), ("", "")]
        elif part == 4:
            lines += [("```",) * 2, ("# a comment",) * 2, *under("", some_text(rng, PIECES[:7]))]
            lines += [("```",) * 2]
        elif part == 5:
            lines += [("~~~",) * 2, ("# a comment",) * 2, *under("", some_text(rng)), ("~~~",) * 2]
        elif part == 6:
            lines += [("- Row 5.",) * 2, ("", ""), *under("      ", some_text(rng)), ("", "")]
        elif part == 7:
            lines += [("Row 6.",) * 2, (rng.choice(["===", "---"]),) * 2]
    return lines


def a_message(rng: random.Random) -> Lines:
    """A few lines, many lines or a README, one time in three each; some end in backticks
    that nothing pairs with: three alone, a fence of tildes about them, or what is left of a
    code block that holds three to five."""
    shape = rng.randrange(3)
    if shape == 2:
        lines = a_readme(rng)
    else:
        count = rng.randint(2, 7) if shape else rng.randint(8, 30)
        lines = [line for _ in range(count) for line in some_lines(rng)]
    end = rng.randrange(14)  # a row of backticks that nothing pairs with comes last, if at all
    if end == 0:
        lines += [("```",) * 2, *[under("", some_text(rng))[0] for _ in range(rng.randint(1, 3))]]
    elif end < 3:
        lines += left_open(rng, ("```", "code", "~~~") if end == 1 else ("~~~", "code", "```"))
    elif end < 6:  # three to five backticks in a block: Slack ends the block at the first three
        left = {3: "Row 4", 4: "` Row 4", 5: "`` Row 4"}[end]
        lines += [(f"```\nqzx {'`' * end} Row 4\n```", f"```\nqzx \n```\n{left}\n```")]
    return lines


LINKED = {"https://fable.test/a_b", "https://fable.test"}  # where the messages' own links lead
FENCE = re.compile(r"[ \t>]*(`{3,}|~{3,})[ \t]*")  # a line that opens or closes a fence
WITH_HTML = MarkdownIt("commonmark", {"html": True}).enable("table")


def displayed_as_plain(text: str) -> list:
    """What a reader makes of `text` that Slack did not display so: a link or an image that
    leads where no link of the message does, and whatever it makes of "qzx", which the
    messages hold in Slack's own code alone, but code or plain characters."""
    found: list = []
    defined: dict = {}
    if not any(mark in text for mark in ("qzx", "](", "]:", "<")):
        return found  # no link without one of these, and nothing of Slack's own code
    tokens = READER.parse(text, defined)
    for n, block in enumerate(tokens):
        if block.type == "heading_open" and any(
            part.type == "text" and "qzx" in part.content for part in tokens[n + 1].children
        ):
            found.append("a heading")
        inside = 0
        for token in block.children or []:
            where = token.attrGet("href") or token.attrGet("src")
            if where and where not in LINKED and not where.startswith(SOURCE[:40]):
                found.append((token.type, where))
            inside += token.type in ("link_open", "em_open", "strong_open")
            inside -= token.type in ("link_close", "em_close", "strong_close")
            if inside and token.type == "text" and "qzx" in token.content:
                found.append(("marked up", token.content))
    found += [("defined", name) for name in defined.get("references", {}) if "QZX" in name]
    found += [
        ("html", token.content)
        for block in (WITH_HTML.parse(text) if "qzx" in text and "<" in text else [])
        for token in [block, *(block.children or [])]
        if token.type.startswith("html") and "qzx" in token.content
    ]
    return found


def page_body(note: str) -> str:
    """The body of the page a note makes alone: its passages as claims, in their order."""
    spans = chunk("", note)
    claims = [Claim(id=claim_id(s.id), text=s.text, span_ids=(s.id,)) for s in spans[1:]]
    return render_body("Shelves", claims, {s.id: s for s in spans})


def faults_of(lines: Lines, before: str, after: str, as_prose: str) -> tuple[list, bool]:
    """What is wrong with the note `after` of the message `lines`: nothing, when the lines both
    readings take for code are as displayed, the others as the prose rules wrote them, each but
    for backslashes, no backslash is one too many, no passage read alone holds markup that
    the note made of prose alone, `as_prose`, does not, and nothing Slack displayed as plain
    characters is markup in the note, in a passage of it read alone or in its page's body.
    And whether the note was kept as prose throughout, as only a message with a line that is
    a fence as displayed alone may be (the limit, pinned below)."""
    if not one_heading(after):
        return ["a second heading"], False
    for read in (after, page_body(after), *[span.text for span in chunk("", after)]):
        if made := displayed_as_plain(read):
            return ["markup of what Slack displayed as plain", made[:3], read[:200]], False
    if after != as_prose and markup_of(after) != markup_of(as_prose):
        made = [mark for mark in markup_of(after) if mark not in markup_of(as_prose)]
        return ["markup of what was displayed as plain characters", made[:3]], False
    shown = as_displayed(before, "\n".join(shown for _, shown in lines)).split("\n")
    prose, held, code = before.split("\n"), after.split("\n"), code_to_both(after)
    if not len(shown) == len(prose) == len(held):
        return [f"{len(held)} lines, {len(prose)} as prose, {len(shown)} as displayed"], False
    # The limit: a line that is a fence as displayed and not as prose (a label of tildes, or
    # three backticks that Slack paired with no others) may keep the note as prose throughout.
    closes = any(FENCE.fullmatch(shown[n]) and shown[n] != prose[n] for n in range(len(shown)))
    for as_prose in (True, False) if closes else (False,):  # prose first: its marks are its own
        wanted = [prose[n] if as_prose or n not in code else shown[n] for n in range(len(held))]
        for n in code:  # kept as prose, a line still ends with the white space displayed
            if shown[n].startswith(prose[n]) and not shown[n][len(prose[n]) :].strip():
                wanted[n] = shown[n]
        columns = [backslashes(was, now) for was, now in zip(wanted, held, strict=True)]
        if None not in columns:
            break
    else:
        at = columns.index(None)
        return [(at, "code" if at in code else "prose", held[at], shown[at], prose[at])], False
    as_prose = as_prose and any(prose[n] != shown[n] for n in code)
    places = [(n, column) for n, found in enumerate(columns) for column in found]
    if may_be_headings(before) >= 8:  # the limit: outside code, as the fifth round wrote it
        places = [place for place in places if place[0] in code]
    return spare(after, places), as_prose


def test_generated_messages_hold_code_as_displayed_and_only_the_backslashes_asked_for(notes):
    rng = random.Random(SEED)
    few = many = written = in_code = kept_as_prose = 0
    wrong, digest = [], hashlib.sha256()
    for nth in range(CASES):
        lines = a_message(rng)
        text = "\n".join(sent for sent, _ in lines)
        # A backtick alone, and a block of Slack's own, are written anew: the digest is of the
        # messages that hold none, and of every fourth without its lines that hold one.
        plain = "\n".join(sent for sent, _ in lines if "`" not in sent)
        if plain != text and not nth % 4:
            with contextlib.suppress(slack_text.NotText):
                digest.update(notes(plain)[0].encode() + b"\0")
        try:
            before, after = notes(text)
        except slack_text.NotText:
            continue
        if plain == text:
            digest.update(before.encode() + b"\0")
        many += may_be_headings(before) >= 8
        few += may_be_headings(before) < 8
        written += after.count("\\") > before.count("\\")
        prose, held = before.split("\n"), after.split("\n")
        in_code += any(n < len(prose) and prose[n] != held[n] for n in code_lines(after))
        found, as_prose = faults_of(lines, before, after, notes.as_prose)
        kept_as_prose += as_prose
        if found:
            wrong.append((text, body(after), found))
    wrong.sort(key=lambda found: len(found[0]))
    assert not wrong, f"{len(wrong)} of {few + many}: {wrong[:6]}"
    assert few > CASES / 4 and many > CASES / 4 and written > CASES / 4 and in_code > CASES / 8
    assert 0 < kept_as_prose < CASES / 6  # the limit is run, and is the exception
    assert digest.hexdigest() == PROSE_ON_22F498B6


# What the generated test found on 22f498b6, one message a kind, and what the note holds now.
# Each row: the message, and the note's lines between the author's heading and the link.
FOUND = {
    "indented code under a blank line": (
        "# Settings\n\n    ---\n    port: 8811\n\nRow 4.",
        "\\# Settings\n\n    ---\n    port: 8811\n\nRow 4.",
    ),
    "indented code in a list item": (
        "# Settings\n\n- Row 4.\n\n      ---\n\nRow 5.",
        "\\# Settings\n\n- Row 4.\n\n      ---\n\nRow 5.",
    ),
    # Since the third tester's finding 3 no line of prose opens a fence: the tildes are text,
    # and the line between them is a heading that gets its backslash.
    "indented code under a fence": (
        "# Settings\n~~~\n# Ada Park\n~~~\n    ===\nRow 4.",
        "\\# Settings\n\\~~~\n\\# Ada Park\n\\~~~\n    ===\nRow 4.",
    ),
    "a rule under a blank line": ("# Settings\n\n---\n\nRow 4.", "\\# Settings\n\n---\n\nRow 4."),
    "equals signs under a blank line": (
        "# Settings\n\n===\n\nRow 4.",
        "\\# Settings\n\n===\n\nRow 4.",
    ),
    "a rule under a list item": (
        "# Settings\nRow 4.\n- Row 5.\n---\nRow 6.",
        "\\# Settings\nRow 4.\n- Row 5.\n---\nRow 6.",
    ),
    "a line indented past an underline": (
        "# Settings\n      =\nRow 4.",
        "\\# Settings\n      =\nRow 4.",
    ),
    "a tab before the dashes": ("# Settings\n\t----\nRow 4.", "\\# Settings\n\t----\nRow 4."),
    "the second of two, indented past an underline": (
        "# Settings\n =\n    -\nRow 4.",
        "\\# Settings\n \\=\n    -\nRow 4.",
    ),
    "a rule under a quotation, not in it": (
        "&gt; Settings\n&gt; ---\n---\nRow 4.",
        "> Settings\n> \\---\n---\nRow 4.",
    ),
    "a rule under a heading a list item took in": (
        "- Row 4.\n# Settings\n---\nRow 5.",
        "- Row 4.\n\\# Settings\n---\nRow 5.",
    ),
    "equals signs under a nested item, not in it": (
        "- - # Settings\n ===\nRow 4.",
        "- - \\# Settings\n ===\nRow 4.",
    ),
}


@pytest.mark.parametrize("kind", FOUND)
def test_what_the_generated_messages_found(bench, notes, kind):  # noqa: F811
    text, lines = FOUND[kind]
    note, spans = filed(bench, text)
    assert body(note) == lines
    assert not faults(note, spans)
    before, after = notes(text)
    assert after == note and not not_needed(before, after)


@pytest.mark.parametrize(
    ("text", "lines"),
    [
        ("Settings\n---\n---\n---\nRow 4.", "Settings\n\\---\n\\---\n\\---\nRow 4."),
        ("Settings\n===\n   -\nRow 4.", "Settings\n\\===\n   \\-\nRow 4."),
        ("- Settings\n  ---\n  ===\nRow 4.", "- Settings\n  \\---\n  \\===\nRow 4."),
        ("&gt; Settings\n&gt; ===\n&gt; -\nRow 4.", "> Settings\n> \\===\n> \\-\nRow 4."),
        ("# Settings\n---\nRow 4.", "\\# Settings\n\\---\nRow 4."),
        ("- Row 4.\n# \n     # Ada Park\nRow 5.", "- Row 4.\n\\# \n     \\# Ada Park\nRow 5."),
    ],
    ids=["a run", "a run of both", "in an item", "quoted", "under a heading", "in an item, later"],
)
def test_a_line_that_would_underline_still_gets_its_backslash(bench, notes, text, lines):  # noqa: F811
    """Each of these is a heading once the line above it is text: none is spared."""
    note, spans = filed(bench, text)
    assert body(note) == lines
    assert not faults(note, spans)
    assert not not_needed(*notes(text))


def test_a_line_one_reader_takes_for_code_and_the_other_for_a_heading_gets_it(bench, notes):  # noqa: F811
    """The parser read the third line inside a list item's fence of tildes; the splitter reads
    no item in "1." and no fence behind a tab, so to it the line was a heading. Since the
    third tester's finding 3 no line of prose opens a fence: the tildes are text, the line is
    a heading to both, and it gets its backslash all the same."""
    text = "1.\n\t~~~\n   # Ada Park\nRow 4."
    note, spans = filed(bench, text)
    assert body(note) == "1.\n\t\\~~~\n   \\# Ada Park\nRow 4."
    assert not faults(note, spans) and not code_lines(note)
    before, after = notes(text)
    assert added(before, after) == [(4, 3)] and not not_needed(before, after)
    assert [span.kind for span in chunk("", after.replace("\\#", "#"))].count("heading") == 2


# A line the note reads as code holds what Slack displayed. Each row: the message, and the
# note's lines between the author's heading and the link.
DISPLAYED = {
    "indented under a blank line": (
        "Settings:\n\n    items[0] = (a) * 2 * 3\n\nRow 4.",
        "Settings:\n\n    items[0] = (a) * 2 * 3\n\nRow 4.",
    ),
    "indented in a list item": (
        "- Row 4.\n\n      items[0] = *p",
        "- Row 4.\n\n      items[0] = *p",
    ),
    "indented in a quotation": (
        "&gt; Row 4.\n&gt;\n&gt;     items[0] = *p",
        "> Row 4.\n>\n>     items[0] = *p",
    ),
    "behind a tab": (
        "Settings:\n\n\titems[0] = (a) * 2 * 3",
        "Settings:\n\n\titems[0] = (a) * 2 * 3",
    ),
    # Slack has no fence of tildes (the third tester's finding 3): its lines are text. A `*`
    # Slack shows as a character is the character typed (the second attack, finding 2): one
    # between blanks bare, one before a word with a backslash; neither doubled.
    "in a fence of tildes": (
        "Settings:\n~~~\nitems[0] = (a) * 2 * 3\n~~~",
        "Settings:\n\\~~~\nitems\\[0\\] = \\(a\\) * 2 * 3\n\\~~~",
    ),
    "in a fence of tildes left open": (
        "Settings:\n~~~\nitems[0] = *p * 2",
        "Settings:\n\\~~~\nitems\\[0\\] = \\*p * 2",
    ),
    # Three backticks that Slack paired with no others it displayed as they are: text, and no
    # fence over what follows.
    "under three backticks nothing pairs with: text": (
        "Settings:\n```\nitems[0] = *p * 2",
        "Settings:\n\\`\\`\\`\nitems\\[0\\] = \\*p * 2",
    ),
    "its entities read": (
        "Settings:\n\n    a &lt; b &amp;&amp; c &gt; d",
        "Settings:\n\n    a < b && c > d",
    ),
    "a mention, and a name with a hash": (
        "Settings:\n\n    <@U0QUILL01> owns <#C789|shelf>",
        "Settings:\n\n    @Cedar Quill #2 owns #c# shelf",
    ),
    "a link, in the converter's words": (
        "Settings:\n\n    see <https://fable.test/a_b|Issue #4 [open]>",
        "Settings:\n\n    see [Issue #4 [open]](https://fable.test/a_b)",
    ),
    "an email address, still removed": (
        "Settings:\n\n    mail quill@fable.test [now]",
        "Settings:\n\n    mail [email removed] [now]",
    ),
    # It was code to the parser alone, in a list item's fence of tildes: text now (finding 3).
    "a heading to the splitter alone: displayed, and the backslash it needs": (
        "1.\n\t~~~\n   # run [once]\nRow 4.",
        "1.\n\t\\~~~\n   \\# run \\[once\\]\nRow 4.",
    ),
    # Not code to the parser, so prose as before, its asterisks as Slack showed them.
    "no blank line above: text": (
        "Settings:\n    items[0] = (a) * 2 * 3",
        "Settings:\n    items\\[0\\] = \\(a\\) * 2 * 3",
    ),
    "three spaces: text": (
        "Settings:\n\n   items[0] = *p*",
        "Settings:\n\n   items\\[0\\] = **p**",
    ),
    # A code block of Slack's own was never written as prose, and is as it was.
    "Slack's own code block": (
        "```\nitems[0] = (a) * 2 &amp;&amp; <@U0QUILL01>\n```",
        "```\nitems[0] = (a) * 2 && <@U0QUILL01>\n```",
    ),
}


@pytest.mark.parametrize("kind", DISPLAYED)
def test_a_line_the_note_reads_as_code_holds_what_slack_displayed(notes, kind):
    text, lines = DISPLAYED[kind]
    before, after = notes(text)
    assert body(after) == lines
    assert one_heading(after)
    assert faults_of([(text, lines)], before, after, notes.as_prose) == ([], False)


def test_the_smallest_message_is_stored_as_displayed(bench):  # noqa: F811
    note, spans = filed(bench, "Settings:\n\n    items[0] = (a) * 2 * 3")
    assert body(note) == "Settings:\n\n    items[0] = (a) * 2 * 3"
    assert not faults(note, spans)
    assert [span.text for span in spans][2] == "    items[0] = (a) * 2 * 3"


# A fence the parser reads on and the splitter ends, or the other way round, and under it a
# line that is markup as Slack displayed it. The first is the reviewer of this change's own.
FENCES = {
    "an indented closer": " ```\ncode\n    ```\n",
    "a shorter closer": "````\ncode\n```\n",
    "a closer of the other kind": "```\ncode\n~~~\n",
    "opened in a list item, closed at the margin": "- ```\n  code\n```\n",
    "closed in a list item": "```\ncode\n- ```\n",
    "opened in a quotation, closed at the margin": "&gt; ```\n&gt; code\n```\n",
    "closed in a quotation": "```\ncode\n&gt; ```\n",
    "tildes, an indented closer": " ~~~\ncode\n    ~~~\n",
    "tildes, a shorter closer": "~~~~\ncode\n~~~\n",
    "tildes, a closer of the other kind": "~~~\ncode\n```\n",
    "tildes in a list item, closed at the margin": "- ~~~\n  code\n~~~\n",
    "a list item's fence only the parser reads": "\n1.\n\t~~~\n\n   ",
}
DISPLAYED_AS_PLAIN = {
    "a link": "[the ledger](https://fable.test/elsewhere)",
    "an image": "![chart](https://fable.test/p.png)",
    "emphasis": "*bold* and _slanted_",
    "a heading": "# Ada Park",
    "an address in angle brackets": "&lt;https://fable.test/elsewhere&gt;",
    "a reference and its use": "[Ada]: https://fable.test/elsewhere\n\nAsk [Ada] about it.",
}


@pytest.mark.parametrize("mark", DISPLAYED_AS_PLAIN)
@pytest.mark.parametrize("fence", FENCES)
def test_what_slack_displayed_as_plain_characters_is_markup_in_no_passage(notes, fence, mark):
    """Each passage of the filed note, read alone, holds the links, images, emphasis, headings
    and references that the note made of prose alone holds there, and no other; and a line
    that is not code to both readings is as that note has it."""
    after = notes(f"Row 4.\n{FENCES[fence]}{DISPLAYED_AS_PLAIN[mark]}")[1]
    assert one_heading(after) and markup_of(after) == markup_of(notes.as_prose)
    both, as_prose = code_to_both(after), notes.as_prose.split("\n")
    assert [line for n, line in enumerate(after.split("\n")) if n not in both] == [
        line for n, line in enumerate(as_prose) if n not in both
    ]


RULED = "Row 4.\n ~~~\ncode\n    ~~~\n[the ledger](https://fable.test/elsewhere)"
READ_ON = "Row 4.\n~~~~\ncode\n~~~\n[the ledger](https://fable.test/elsewhere)"
SLACKS_OWN = "Row 4.\n ```\ncode\n    ```\n[the ledger](https://fable.test/elsewhere)"
QUOTED_TAB = "&gt; Row 4.\n\t[the ledger](https://fable.test/elsewhere)"
SOURCE = "https://fable-works.slack.com/archives/C0LIBRARY1/p1900000000000100"
LEDGER = "\\[the ledger\\]\\(https://fable.test/elsewhere\\)"


def test_a_line_one_reading_alone_takes_for_code_keeps_the_marks_of_prose(notes):
    """A quoted line, then one behind a tab: the note reads the second as the quotation's
    text going on, its passage alone (what a page shows of a claim) as code. Put as displayed
    it would be a link in the note. (The sixth round found the shape with a fence of tildes
    whose closing line is indented four, which the parser reads on through and the splitter
    ends; since the third tester's finding 3 a line of tildes is text, and so is all of it.)"""
    after = notes(QUOTED_TAB)[1]
    assert body(after) == "> Row 4.\n\t" + LEDGER
    passages = chunk("", after)
    assert one_heading(after) and 3 not in code_lines(after) and code_lines(passages[2].text)
    assert after == notes.as_prose
    for fenced in (RULED, READ_ON):
        after = notes(fenced)[1]
        assert body(after) == fenced.replace("~~~", "\\~~~").replace(
            "[the ledger](https://fable.test/elsewhere)", LEDGER
        )
        assert one_heading(after) and not code_lines(after) and after == notes.as_prose
    slacks_own = notes(SLACKS_OWN)[1]
    assert body(slacks_own) == (
        "Row 4.\n```\ncode\n```\n\\[the ledger\\]\\(https://fable.test/elsewhere\\)"
    )
    assert code_lines(slacks_own) == code_to_both(slacks_own) == {3, 4, 5}


@pytest.mark.parametrize(
    ("text", "linked", "code"),
    [
        # Since the third tester's finding 3 the tildes are text: one paragraph, no code.
        (RULED, [[], [SOURCE]], [False, False]),
        (SLACKS_OWN, [[], [], [], [SOURCE]], [False, True, False, False]),
        (READ_ON, [[], [SOURCE]], [False, False]),
        (
            "Settings:\n\n    [the ledger](https://fable.test/elsewhere)",
            [[], [], [SOURCE]],
            [False, True, False],
        ),
        (QUOTED_TAB, [[], [], [SOURCE]], [False, True, False]),
    ],
    ids=[
        "tildes once code to the note alone",
        "Slack's own block",
        "tildes once code to both",
        "indented",
        "code to the passage alone",
    ],
)
def test_a_page_links_nothing_that_slack_displayed_as_brackets(bench, text, linked, code):  # noqa: F811
    """Asked of the product: the page the store made of the filed message, its body and each
    of its claims as the page view renders a claim, alone, through the web's own renderer.
    A claim is stored with its indentation, so a line both read as code is code there."""
    assert filed(bench, text) is not None
    (listed,) = bench.owner.list_pages(bench.library.id)
    page = bench.owner.get_page(bench.library.id, listed.slug)
    shown = [str(web_markdown.render(claim.text)) for claim in page.claims]
    links = re.compile(r'href="([^"]+)"')
    assert [links.findall(claim) for claim in shown] == linked
    assert ["<pre>" in claim for claim in shown] == code
    assert links.findall(str(web_markdown.render(page.body))) == [
        a for found in linked for a in found
    ]


# A code block of Slack's own (seventh round, finding 3): what it displays as it is. Each
# row: the message, and what each fence of the note then holds.
MANUAL = "[manual](https://fable.test/docs)"
SLACK_BLOCKS = {
    "on lines of its own": (f"Docs:\n```\n{MANUAL}\n```", [f"{MANUAL}\n"]),
    "on one line": (f"```{MANUAL}```", [f"{MANUAL}\n"]),
    "on one line, text before and after": (f"Docs ```{MANUAL}``` now", [f"{MANUAL}\n"]),
    "opened after text": (f"Docs ```\n{MANUAL}\n```", [f"{MANUAL}\n"]),
    "closed before text": (f"```\n{MANUAL}\n``` more", [f"{MANUAL}\n"]),
    "at the very end, closed on its last line": (f"Docs:\n```\n{MANUAL}```", [f"{MANUAL}\n"]),
    "text on its first and last lines": (
        f"```first\n{MANUAL}\nlast```",
        [f"first\n{MANUAL}\nlast\n"],
    ),
    "a blank line inside": (f"```\nfirst\n\n{MANUAL}\n```", [f"first\n\n{MANUAL}\n"]),
    "empty lines first and last": (f"```\n\n{MANUAL}\n\n```", [f"\n{MANUAL}\n\n"]),
    "tabs and spaces at its lines' ends": (
        f"```\n{MANUAL}\t\nRow 4.  \n```",
        [f"{MANUAL}\t\nRow 4.  \n"],
    ),
    "runs of backticks inside": (
        f"```\n{MANUAL} `` and ` \n```",
        [f"{MANUAL} `` and ` \n"],
    ),
    "four backticks about it": (f"````\n{MANUAL}\n````", [f"`\n{MANUAL}\n"]),
    "in a quotation": (f"&gt; ```\n{MANUAL}\n```", [f"{MANUAL}\n"]),
    "its lines quoted": (f"&gt; ```\n&gt; {MANUAL}\n&gt; ```", [f"{MANUAL}\n"]),
    "quoted, a quotation's mark in its code": (
        f"&gt; ```\n&gt; &gt; {MANUAL} &gt; Row 4\n&gt; ```",
        [f"> {MANUAL} > Row 4\n"],
    ),
    "after a list item": (f"- Row 4.\n```\n{MANUAL}\n```", [f"{MANUAL}\n"]),
    "indented under a list item": (
        f"1. Row 4.\n    ```\n{MANUAL}\n    ```",
        [f"{MANUAL}\n"],
    ),
    "in a numbered item, a blank line inside": (
        f"1.\n    ```\n\n   {MANUAL}\n    ```",
        [f"\n   {MANUAL}\n"],
    ),
    "behind a tab": (f"Docs:\n\n\t```\n{MANUAL}\n\t```", [f"{MANUAL}\n"]),
    "two blocks": (f"```{MANUAL}```\n```{MANUAL}```", [f"{MANUAL}\n", f"{MANUAL}\n"]),
    "two blocks on one line": (f"```{MANUAL}``````Row 4.```", [f"{MANUAL}\n", "Row 4.\n"]),
    "right after code within a line": (f"`Row 4````{MANUAL}```", [f"{MANUAL}\n"]),
    "right after a mention, and a backtick after it": (
        f"<@U0QUILL01>```{MANUAL}```` `{MANUAL}",
        [f"{MANUAL}\n"],
    ),
    "in a table's row": (f"| Row |\n|---|\n| ```{MANUAL}|Row 4``` |", [f"{MANUAL}|Row 4\n"]),
}


def shown_by_the_product(bench, text: str):  # noqa: F811
    """The filed note and its passages, and every address linked where the product shows the
    message: in the note read whole, in each claim of the stored page rendered alone as the
    page view renders it, and in the page's body."""
    note, spans = filed(bench, text)
    (listed,) = bench.owner.list_pages(bench.library.id)
    page = bench.owner.get_page(bench.library.id, listed.slug)
    linked = re.compile(r'href="([^"]+)"')
    links = [
        *linked.findall(str(web_markdown.render(note))),
        *[a for claim in page.claims for a in linked.findall(str(web_markdown.render(claim.text)))],
        *linked.findall(str(web_markdown.render(page.body))),
    ]
    return note, spans, sorted(set(links))


@pytest.mark.parametrize("kind", SLACK_BLOCKS)
def test_a_code_block_of_slacks_own_is_one_fence_to_both_readers(bench, kind):  # noqa: F811
    """Its opening and closing fences are on lines of their own, so the parser reads the block
    as a fence in the note whole, the splitter keeps it as one passage, and that passage read
    alone is the same fence: what Slack displayed as brackets is a link nowhere."""
    text, blocks = SLACK_BLOCKS[kind]
    note, spans, links = shown_by_the_product(bench, text)
    assert links == [SOURCE]
    assert not faults(note, spans)
    assert [token.content for token in READER.parse(note) if token.type == "fence"] == blocks
    passages = [span.text for span in spans if span.kind == "code"]
    assert [token.content for passage in passages for token in READER.parse(passage)] == blocks
    assert all(token.type == "fence" for passage in passages for token in READER.parse(passage))


# A code block of Slack's own in what was a fence of tildes, a line of tildes in it: that
# fence closed at the block's line of tildes and left the rest of the block outside any, so
# the block was written as plain characters. Since the third tester's finding 3 the tildes
# about it are text, and the block is one fence. Each row: the message, and the note's lines
# between the heading and the link.
NOT_ONE_FENCE = {
    "in a fence of tildes, a line of tildes in it": (
        f"~~~\n```\n~~~\n{MANUAL}\n```\n{MANUAL}",
        f"\\~~~\n```\n~~~\n{MANUAL}\n```\n\\[manual\\]\\(https://fable.test/docs\\)",
    ),
    "the same, and under the fence of tildes a second block": (
        f"~~~\n```\n~~~\n{MANUAL}\n```\n~~~\nRow 4.\n```{MANUAL}```",
        f"\\~~~\n```\n~~~\n{MANUAL}\n```\n\\~~~\nRow 4.\n```\n{MANUAL}\n```",
    ),
    "two such blocks": (
        f"~~~\n```\n~~~\n{MANUAL}\n```\n```\n~~~\n{MANUAL}\n```",
        f"\\~~~\n```\n~~~\n{MANUAL}\n```\n```\n~~~\n{MANUAL}\n```",
    ),
}


@pytest.mark.parametrize("kind", NOT_ONE_FENCE)
def test_a_code_block_in_what_was_a_fence_of_tildes_is_one_fence(bench, kind):  # noqa: F811
    text, lines = NOT_ONE_FENCE[kind]
    note, spans, links = shown_by_the_product(bench, text)
    assert body(note) == lines
    assert set(links) <= {SOURCE} and one_heading(note) and not faults(note, spans)
    fences = [token.content for token in READER.parse(note) if token.type == "fence"]
    assert fences == [f"~~~\n{MANUAL}\n"] * lines.count("```\n~~~") + [f"{MANUAL}\n"] * (
        "Row 4." in lines
    )


def test_a_code_block_that_cannot_be_one_fence_is_written_as_prose_is(monkeypatch):
    """Each block must be a fence to the parser and one passage of code to the splitter, line
    for line: asked of the message as written, before the note is made of it, and of the
    finished note. One that is not is written as plain characters, backticks and all, and
    nothing it holds is markup. No message is known to need it since the third tester's
    finding 3 (no line of prose opens a fence), so the answer is made up here: the first
    block is not one fence the first time it is asked."""
    asked, real = [], slack_text._not_one_fence

    def first_is_not(note, wrote):
        asked.append(len(wrote))
        return {0} if len(asked) == 1 and wrote else real(note, wrote)

    monkeypatch.setattr(slack_text, "_not_one_fence", first_is_not)
    text = f"```\n{MANUAL}\n```\nRow 4.\n```{MANUAL}```"
    assert note_of(text, None, False) == (
        "\\`\\`\\`\n\\[manual\\]\\(https://fable.test/docs\\)\n\\`\\`\\`\nRow 4.\n"
        f"```\n{MANUAL}\n```"
    )


@pytest.mark.parametrize(
    "text",
    [
        f"Docs: `{MANUAL}`",
        f"Row 4 `\n`{MANUAL}` Row 5",  # a backtick alone on the line above
        f"Row 4 `\nRow 5 `{MANUAL}` and ` again",
        f"| Row |\n|---|\n| `{MANUAL}|Row 4` |",  # a table's row is cut at the bar
        f"`Row 4``{MANUAL}`",
        f"`` `{MANUAL}` ``",
        f"Row 4 ``\n`{MANUAL}`\n`` Row 5",
    ],
    ids=["alone", "under a backtick", "between backticks", "in a table", "two", "in two", "runs"],
)
def test_code_within_a_line_is_a_link_nowhere(bench, text):  # noqa: F811
    note, spans, links = shown_by_the_product(bench, text)
    assert links == [SOURCE]
    assert not faults(note, spans) and "manual" in note


# A passage is read alone where a page shows a claim, and the body of the page is its claims
# one after another: a heading there is a heading over other people's words too. Each row: the
# message, and the note's lines between the author's heading and the link.
HEADINGS_OF_A_PASSAGE = {
    "dashes under a line that a quotation takes in": (
        "&gt; Row 4.\nRow 5.\n---\nRow 6.",
        "> Row 4.\nRow 5.\n\\---\nRow 6.",
    ),
    "dashes under a line under a table": (
        "| Row |\n|---|\nSettings\n---\nRow 4.",
        "| Row |\n|---|\nSettings\n\\---\nRow 4.",
    ),
    # The fences of tildes of the next three rows are text since the third tester's finding 3.
    "dashes in a fence the note reads on and its passages end": (
        "Row 3.\n ~~~\ncode\n    ~~~\nRow 4.\n===\nRow 5.",
        "Row 3.\n \\~~~\ncode\n    \\~~~\nRow 4.\n\\===\nRow 5.",
    ),
    "dashes under a heading under a table": (
        "| Row |\n|---|\n# Settings\n---\nRow 4.",
        "| Row |\n|---|\n\\# Settings\n\\---\nRow 4.",
    ),
    # Found with no underline rule at all: text to the note whole once the line above it is
    # text, and still a heading in its list item's passage read alone.
    "a heading in a list item that the note whole reads as text": (
        "### Settings\n1. \n     ## Ada Park\nRow 4.",
        "\\### Settings\n1. \n     \\## Ada Park\nRow 4.",
    ),
    "a heading under a list item's fence, which the note whole reads on": (
        "# Settings\n1. \n   ~~~\n\n# Ada Park",
        "\\# Settings\n1. \n   \\~~~\n\n\\# Ada Park",
    ),
    "a heading in a quotation's one line, in a fence the note reads on": (
        "Row 3.\n ~~~\ncode\n    ~~~\n&gt; # Ada Park\nRow 5.",
        "Row 3.\n \\~~~\ncode\n    \\~~~\n> \\# Ada Park\nRow 5.",
    ),
    # Under a blank line, or under the passage's first line alone, they underline nothing.
    "dashes that start their passage": (
        "&gt; Row 4.\n---\nRow 6.",
        "> Row 4.\n---\nRow 6.",
    ),
}


@pytest.mark.parametrize("kind", HEADINGS_OF_A_PASSAGE)
def test_no_passage_of_the_note_read_alone_holds_a_heading(bench, notes, kind):  # noqa: F811
    """The note read whole had one heading for the splitter and the parser, and its passage
    read alone, as the page's body has it, had another: a rule or code to the note whole."""
    text, lines = HEADINGS_OF_A_PASSAGE[kind]
    note, spans = filed(bench, text)
    assert body(note) == lines
    assert not faults(note, spans)
    (listed,) = bench.owner.list_pages(bench.library.id)
    page = bench.owner.get_page(bench.library.id, listed.slug)
    headings = [
        [token.map for token in READER.parse(read) if token.type == "heading_open"]
        for read in (page.body, *[claim.text for claim in page.claims])
    ]
    assert headings == [[[0, 1], [2, 3]], *[[] for _ in page.claims]]
    assert not not_needed(*notes(text))


def rich(*elements) -> list[dict]:
    """Slack's own account of a message: its rich text, which names what it displayed as code."""
    return [{"type": "rich_text", "elements": list(elements)}]


def words(*parts) -> dict:
    return {
        "type": "rich_text_section",
        "elements": [
            {"type": "text", "text": part} if isinstance(part, str) else part for part in parts
        ],
    }


def code(text: str) -> dict:
    return {"type": "text", "text": text, "style": {"code": True}}


def block_of(text: str) -> dict:
    return {"type": "rich_text_preformatted", "elements": [{"type": "text", "text": text}]}


def note_of(text: str, blocks: object, code_is_named: bool = True) -> str:
    draft = slack_text.note_from_message(
        {"text": text, "ts": "1900000000.000100", "blocks": blocks},
        team_id="T0FABLE01",
        team_domain="fable-works",
        conversation_id="C0LIBRARY1",
        author="Cedar Quill",
        names={},
    )
    assert one_heading(draft.text)
    if code_is_named:  # else "qzx" may be prose, by the backticks of the text
        assert not displayed_as_plain(draft.text)
        assert not any(displayed_as_plain(span.text) for span in chunk("", draft.text))
    return body(draft.text)


@pytest.mark.parametrize("run", [3, 4, 5])
def test_the_rich_text_says_which_backticks_a_code_block_displayed(run):
    """The text bounds a block by its backticks, so three inside it end it there; Slack's
    rich text names the block whole, and the fence is then longer than any run it holds."""
    shown = f"use {'`' * run} here & there\n[qzx](https://fable.test/qzx)  \t"
    text = f"Rows:\n```\n{shown.replace('&', '&amp;')}\n```\nRow 4."
    fence = "`" * (run + 1)
    assert note_of(text, rich(words("Rows:\n"), block_of(shown), words("Row 4."))) == (
        f"Rows:\n{fence}\n{shown}\n{fence}\nRow 4."
    )
    # Without it the backticks of the text decide: the block ends at the first three inside
    # it, and what follows them is prose, written as prose is.
    assert note_of(text, None, False).startswith("Rows:\n```\nuse \n```\n")
    assert "\\[qzx\\]\\(https://fable.test/qzx\\)  \t\n\\`\\`\\`\nRow 4." in note_of(
        text, None, False
    )


def test_the_rich_text_says_which_backticks_code_within_a_line_displayed():
    shown = "row`id [qzx](https://fable.test/qzx) *qzx*"
    text = f"Use `{shown}` and `Row 4` today."
    blocks = rich(words("Use ", code(shown), " and ", code("Row 4"), " today."))
    assert note_of(text, blocks) == f"Use ``{shown}`` and ``Row 4`` today."
    assert note_of(text, None, False).startswith("Use `row`id \\[qzx\\]")  # the text's pairing
    # A backtick at its edge: a space each side, which Markdown takes back.
    assert note_of("Use ``Row 4`` now.", rich(words("Use ", code("`Row 4`"), " now."))) == (
        "Use `` `Row 4` `` now."
    )


@pytest.mark.parametrize(
    "blocks",
    [
        rich(block_of("another block")),  # not what the text holds between its backticks
        rich(words(code("another"))),
        rich({"type": "rich_text_preformatted", "elements": [{"type": "text", "text": 4}]}),
        [{"type": "rich_text", "elements": "Row 4"}, 4, None, {"elements": {"style": 4}}],
        {"type": "rich_text"},
        "rich text",
    ],
    ids=["another block", "other code", "no text", "no shape", "no list", "no blocks"],
)
def test_rich_text_that_does_not_name_the_texts_code_is_passed_over(blocks):
    text = "Rows:\n```\n[qzx](https://fable.test/qzx)\n```\nUse `*qzx*` today."
    assert (
        note_of(text, blocks)
        == note_of(text, None)
        == ("Rows:\n```\n[qzx](https://fable.test/qzx)\n```\nUse `*qzx*` today.")
    )


def test_white_space_at_the_end_of_a_line_both_read_as_code_is_kept(bench, notes):  # noqa: F811
    """A last row of three fields, the third empty: its tab was cut with the message's end."""
    text = "Rows:\n\n    4\tquince\t\n    5\tplum\t"
    note, spans = filed(bench, text)
    assert body(note) == text
    assert [span.text for span in spans][2] == "    4\tquince\t\n    5\tplum\t"
    assert [t.content for t in READER.parse(note) if t.type == "code_block"] == [
        "4\tquince\t\n5\tplum\t\n"
    ]
    (listed,) = bench.owner.list_pages(bench.library.id)
    claims = bench.owner.get_page(bench.library.id, listed.slug).claims
    assert [claim.text for claim in claims][1] == "    4\tquince\t\n    5\tplum\t"
    # A code block of Slack's own on the row's line goes to lines of its own, and the white
    # space before it ends the row.
    assert body(notes("Rows:\n\n    5\tplum\t ```Row 4.```")[1]) == (
        "Rows:\n\n    5\tplum\t \n```\nRow 4.\n```"
    )
    # Where the note keeps the marks of prose in its code (a label of tildes that would open a
    # fence to the splitter as displayed, in a list item), the row still ends as displayed.
    assert body(notes("- Row 4.\n\n      <x|~~~>\n      5\tplum\t")[1]) == (
        "- Row 4.\n\n      \\~\\~\\~\n      5\tplum\t"
    )
    # Elsewhere the end of the message is cut as before: a last line of prose, and blank lines.
    assert body(notes("Row 4.\t \n \n")[1]) == "Row 4."
    assert body(notes("Rows:\n\n    5\tplum\t  \n\n \t\n")[1]) == "Rows:\n\n    5\tplum\t  "


# A README pasted into Slack: nine headings, commands indented and fenced, with comments.
README = """# Shelf service
Run it once.
## Install

    # as root
    make shelf[0] *.o

## Use
```
# list the shelves
shelf --all | sort &gt; rows[0]
```
## Rows
### Row 4
~~~
# rows are counted from 0
rows[4] = *row
~~~
### Row 5
## Notes
- Row 6.
## Limits

---

## Thanks"""


def test_a_readme_keeps_its_commands_and_their_comments(bench, notes):  # noqa: F811
    """More than eight lines read as headings, so the note is written as the fifth round wrote
    it; its code is as displayed all the same, and the rule keeps the backslash of the limit.
    Its lines of tildes are text, as Slack displays them, and so is what they held (changed by
    the third tester's finding 3: they were a fence)."""
    lines = (
        README.replace("&gt;", ">").replace("\n---\n", "\n\\---\n").replace("# Shelf", "\\# Shelf")
    )
    lines = lines.replace("~~~\n# rows", "\\~~~\n\\# rows").replace("[4]", "\\[4\\]")
    lines = lines.replace("*row\n~~~", "\\*row\n\\~~~")  # not emphasis: typed (finding 2)
    for heading in ("## Install", "## Use", "## Rows", "## Notes", "## Limits", "## Thanks"):
        lines = lines.replace("\n" + heading, "\n\\" + heading)
    lines = lines.replace("\n### Row", "\n\\### Row")
    note, spans = filed(bench, README)
    assert body(note) == lines
    assert not faults(note, spans)
    code = [note.split("\n")[n] for n in sorted(code_lines(note))]
    assert code == [
        "    # as root",
        "    make shelf[0] *.o",
        "```",
        "# list the shelves",
        "shelf --all | sort > rows[0]",
        "```",
    ]
    before, after = notes(README)
    assert after == note and may_be_headings(before) >= 8
    shown = README.replace("&gt;", ">")
    assert faults_of([(README, shown)], before, after, notes.as_prose) == ([], False)


def test_its_limit_a_line_that_is_a_fence_only_as_displayed_keeps_the_note_as_prose(notes):
    """A link's label of tildes in a list item's indented code is escaped as prose and bare as
    displayed, where the splitter reads it as a fence the item keeps whole, open to the end of
    the note. The readers are asked of the note made with its code as displayed, disagree, and
    the note made as prose is kept. (Until the third tester's finding 3 the example was a label
    that closed a fence of tildes the message opened; no line of prose opens one since.)"""
    after = notes("- Row 4.\n\n      <x|~~~>\n      items[0]\n\nRow 5.")[1]
    assert body(after) == "- Row 4.\n\n      \\~\\~\\~\n      items\\[0\\]\n\nRow 5."
    assert one_heading(after) and code_lines(after) == {4, 5}
    # A label of backticks ends no block of Slack's own: the block is a fence that is closed
    # where Slack closed it, and the label under it is prose.
    after = notes("Settings:\n```\ncode\n``` more\n<x|```>\nitems[0]")[1]
    assert body(after) == "Settings:\n```\ncode\n```\nmore\n\\`\\`\\`\nitems\\[0\\]"
    assert one_heading(after) and code_lines(after) == {3, 4, 5}
    # Two cases of this limit were under a fence of tildes, and are prose since finding 3.


def test_a_note_its_code_as_displayed_cannot_be_made_of_is_the_note_made_as_prose(monkeypatch):
    """Made twice: as prose, then with the lines read as code as displayed. Should the second
    making refuse, the first is the note, and nothing is refused that was filed; and so it is
    should the two texts not have the same lines. No message was found that does either:
    shown with a making that refuses the second time, and with a text a line longer."""
    made, make = [], slack_text._one_heading

    def refusing(text: str) -> str:
        made.append(text)
        if len(made) == 2:
            raise ValueError("The message's headings could not be written as text")
        return make(text)

    prose = "## Cedar Quill\n\n# Settings\n\n    items\\[0\\]"
    shown = "## Cedar Quill\n\n# Settings\n\n    items[0]"
    as_prose = "## Cedar Quill\n\n\\# Settings\n\n    items\\[0\\]"
    monkeypatch.setattr(slack_text, "_one_heading", refusing)
    assert slack_text._code_as_shown(prose, shown) == as_prose
    assert made == [prose, shown]

    made.clear()
    monkeypatch.setattr(slack_text, "_one_heading", lambda text: made.append(text) or make(text))
    assert slack_text._code_as_shown(prose, shown + "\nRow 4.") == as_prose
    assert made == [prose]
    assert slack_text._code_as_shown(prose, shown) == as_prose.replace("\\[0\\]", "[0]")
    assert made == [prose, prose, shown]


EDGES = [
    "<x|>",
    "<@U0BROOK01>",
    "<#C456|shelf>",
    "<nowhere>",
    "    <x|>",
    "<x|~~~>",
    "\t",
    "Row 1.",
]


def test_the_message_as_prose_and_as_displayed_have_the_same_lines(notes, monkeypatch):
    """The two are put together line by line, so they must agree on where each line is, with
    a reference that displays nothing or a name with a line boundary at either end too."""
    seen, put = [], slack_text._code_as_shown
    monkeypatch.setattr(
        slack_text, "_code_as_shown", lambda *texts: seen.append(texts) or put(*texts)
    )
    rng = random.Random(SEED)
    for _ in range(300):
        lines = [(rng.choice(EDGES), ""), *a_message(rng), (rng.choice(EDGES), "")]
        try:
            before, after = notes("\n".join(sent for sent, _ in lines))
        except slack_text.NotText:
            continue
        prose, shown = (text.split("\n") for text in seen[-1])  # the last writing's
        seen.clear()
        assert len(prose) == len(shown) == len(after.split("\n"))
        assert (prose[:2], prose[-2:]) == (shown[:2], shown[-2:]) and "\n".join(prose) == before
    assert not seen


def as_before(note: str) -> str | None:
    """_one_heading as the fifth round left it (22f498b6), kept as the reference: a backslash
    on every line _headings gives, eight readings; None where it raised."""
    text = "\n".join(note.splitlines())
    for _ in range(8):
        found = slack_text._headings(text)
        if not found:
            return text
        lines = text.split("\n")
        for index, mark in found.items():
            lines[index] = lines[index].replace(mark, "\\" + mark, 1)
        text = "\n".join(lines)
    return None


EIGHT = "".join(f"# Shelf {n}\n" for n in range(1, 9))  # eight lines that read as headings
EIGHT_AS_TEXT = EIGHT.replace("#", "\\#")


def with_bare_tildes(notes, text: str) -> str:
    """The note of `text` as the prose rules wrote it before the third tester's finding 3, its
    lines of tildes bare. No message makes a fence of tildes since, and none is known to make
    another fence the parser reads and the splitter does not; the readings of such a note are
    asked of _one_heading alone (filed, the note holds no fence: test_slack_tildes_unit)."""
    return notes(text)[0].replace("\\~~~", "~~~")


def test_a_heading_and_eight_rules_under_blank_lines_are_read_a_line_at_a_reading(
    bench,  # noqa: F811
    monkeypatch,
):
    """A line under a blank line underlines nothing, so it is not one of the eight."""
    asked = passes(monkeypatch)
    note, spans = filed(bench, "# Settings\n" + "\n---\n" * 8 + "\nRow 4.")
    assert body(note) == "\\# Settings\n" + "\n---\n" * 8 + "\nRow 4."
    assert not faults(note, spans) and len(asked) == 2


def test_its_limit_with_eight_such_lines_a_rule_gets_the_backslash(bench, notes):  # noqa: F811
    """Eight lines cannot each have a reading of their own, so the note is written as the
    fifth round wrote it: every line of only "=" or "-" below the first heading."""
    text = EIGHT + "\n---\n\nRow 4."
    note, spans = filed(bench, text)
    assert body(note) == EIGHT_AS_TEXT + "\n\\---\n\nRow 4."
    assert not faults(note, spans)
    before, after = notes(text)
    assert after == as_before(before) and not_needed(before, after) == [(11, 0)]
    seven = text.replace("# Shelf 8\n", "")  # one line fewer, and the rule is left
    assert body(filed(bench, seven, 1)[0]) == EIGHT_AS_TEXT.replace("\\# Shelf 8\n", "") + (
        "\n---\n\nRow 4."
    )


@pytest.mark.parametrize(
    "code",
    [
        "    ---\n    port: 8811",
        "\t===\n\tport: 8811",
        "```\n---\n```\n\n    ---",
        "- Row 5.\n\n      -",
    ],
    ids=["indented", "a tab", "fenced and indented", "in an item"],
)
def test_with_eight_such_lines_code_is_still_as_written(bench, notes, code):  # noqa: F811
    text = f"{EIGHT}\n{code}\n\nRow 4."
    note, spans = filed(bench, text)
    assert body(note) == f"{EIGHT_AS_TEXT}\n{code}\n\nRow 4."
    assert not faults(note, spans)
    before, after = notes(text)
    assert after != as_before(before) and not not_needed(before, after)


def test_with_eight_such_lines_the_splitters_heading_in_code_keeps_its_backslash(notes):
    """The indented dashes are put back as written; the line the splitter reads as a heading
    inside what the parser reads as a fence is not, and the rule is the limit above."""
    text = f"{EIGHT}\n    ---\n1.\n\t~~~\n   # Ada Park\n\t~~~\n\n---\n\nRow 4."
    before = with_bare_tildes(notes, text)
    after = slack_text._one_heading(before)
    assert body(after) == (
        f"{EIGHT_AS_TEXT}\n    ---\n1.\n\t~~~\n   \\# Ada Park\n\t~~~\n\n\\---\n\nRow 4."
    )
    assert one_heading(after) and {11, 14} <= code_lines(after)
    assert not_needed(before, after) == [(17, 0)]


def test_with_eight_such_lines_the_splitters_second_heading_in_code_is_left(notes):
    """To the parser everything below the eighth line is one fence. To the splitter the fence
    ends there, the next line is a heading, and the last one is a heading only while that one
    is. Once the code is put back each of the two has a reading of its own, so the last is
    left as written. (It kept a backslash no reader needed: all the splitter's headings got
    one at once.)"""
    six = EIGHT.replace("# Shelf 7\n# Shelf 8\n", "")
    before = with_bare_tildes(notes, six + "- ~~~\n ~~~\n### Ada Park\n\n   # Brook")
    after = slack_text._one_heading(before)
    assert body(after) == six.replace("#", "\\#") + "- ~~~\n ~~~\n\\### Ada Park\n\n   # Brook"
    assert one_heading(after) and {10, 12} <= code_lines(after)
    assert not not_needed(before, after)


NINE = "   # Ada Park\n" * 9  # each a heading to the splitter, inside a fence to the parser


def test_with_more_of_the_splitters_headings_than_readings_they_get_it_at_once(notes, monkeypatch):
    """Nine lines the splitter reads as headings in what the parser reads as a fence: a line
    at a reading does not finish, so each line of code the splitter then reads as a heading
    gets its backslash back at once, as before, and the indented dashes stay as written."""
    text = f"# Settings\n\n    ---\n\n1.\n\t~~~\n{NINE}Row 4."
    as_text = NINE.replace("#", "\\#")
    before = with_bare_tildes(notes, text)
    asked = passes(monkeypatch)
    after = slack_text._one_heading(before)
    assert body(after) == f"\\# Settings\n\n    ---\n\n1.\n\t~~~\n{as_text}Row 4."
    assert one_heading(after) and not not_needed(before, after)
    assert len(asked) == 2 + slack_text._PASSES + 1  # as before, a line at a reading, at once


def test_code_is_put_back_only_when_the_readers_then_find_one_heading(monkeypatch):
    """No generated message reached this (the generated test fails on one that does): shown
    with a reader that finds a heading whatever the note holds. The note is then kept as it
    was, with its one heading, and never the one not yet read."""
    text = "## Cedar Quill\n\n# Settings\n\n    ---"
    note = "## Cedar Quill\n\n\\# Settings\n\n    \\---"
    put_back = "## Cedar Quill\n\n\\# Settings\n\n    ---"
    assert slack_text._code_as_written(text, note) == put_back
    asked = []
    monkeypatch.setattr(slack_text, "_headings", lambda read: asked.append(read) or {4: "-"})
    assert slack_text._code_as_written(text, note) == note
    assert asked[0] == put_back and len(asked) == slack_text._PASSES + 1


def test_a_reading_a_line_and_twice_that_when_code_is_put_as_displayed(bench, monkeypatch):  # noqa: F811
    """What the changes cost, in readings (never time): a line that reads as a heading has a
    reading of its own, where every such line was written at one reading; and the note is
    made twice when a line it reads as code is not displayed as prose writes it."""
    asked = passes(monkeypatch)
    three = "# Shelf 1\nRow 4.\n# Shelf 2\nRow 5.\n# Shelf 3\nRow 6."
    assert "\\# Shelf 1\nRow 4.\n\\# Shelf 2\nRow 5.\n\\# Shelf 3\nRow 6." in filed(bench, three)[0]
    assert len(asked) == 4

    asked.clear()  # code that prose writes as displayed: the same four
    assert "\n\n    port: 8811\n\n" in filed(bench, three + "\n\n    port: 8811", 1)[0]
    assert len(asked) == 4

    asked.clear()  # code that prose writes otherwise: made twice
    assert "\n\n    items[0]\n\n" in filed(bench, three + "\n\n    items[0]", 2)[0]
    assert len(asked) == 8

    asked.clear()  # eight: as before, at one reading
    assert EIGHT_AS_TEXT in filed(bench, EIGHT + "Row 4.", 3)[0]
    assert len(asked) == 2

    asked.clear()  # and one reading more when code was put back as written
    assert "\n\n    ---\n\n" in filed(bench, EIGHT + "\n    ---\n\nRow 4.", 4)[0]
    assert len(asked) == 3


def test_a_message_a_line_at_a_reading_cannot_finish_is_written_as_before(
    bench,  # noqa: F811
    notes,
    monkeypatch,
):
    """Two lines are found at the first reading and eight more at the second, once the list
    item takes them in: a line at a reading runs out of readings, and the note is then
    written from the start as the fifth round wrote it. Filed then, filed now."""
    text = "- Row 4.\n# \n" + "     # Ada Park\n" * 8 + "Row 5."
    before, after = notes(text)
    assert after == as_before(before) and after.count("\\#") == 9
    asked = passes(monkeypatch)
    note, spans = filed(bench, text)
    assert note == after and not faults(note, spans)
    assert len(asked) == slack_text._PASSES + 2
