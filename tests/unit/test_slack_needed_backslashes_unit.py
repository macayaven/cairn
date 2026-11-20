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
- a line the parser reads as code, fenced or indented, holds what Slack displayed for it:
  its entities read, a name, a mention and a link in the converter's words, an email address
  removed, and no backslash and no doubled mark of the prose rules;
- every other line is as the prose rules wrote it on 22f498b6 (their text for these messages
  is pinned by its digest), but for the backslashes that keep one heading;
- when the message has fewer than eight lines that hold a "#" or are only "=" or "-" under a
  line that is not blank, every one of those backslashes is needed: taken out alone, a
  reader finds a second heading;
- with eight or more such lines the backslash is written as the fifth round wrote it (the
  limit, pinned below), except in code: there too only where a reader needs it.

The messages are generated from a seed, so every run reads the same ones, each line with
what Slack displayed for it written beside it by hand, never worked out by a second
converter. What they found is in the tables. Every name is made up.
"""

import hashlib
import random
import re

import pytest
from markdown_it import MarkdownIt
from test_slack_one_heading_unit import bench, faults, filed, passes  # noqa: F401  (a fixture)

from cairn.slack import text as slack_text
from cairn.spans import chunk

READER = MarkdownIt("commonmark", {"html": False}).enable("table")  # as the web view reads
ONLY_MARKS = re.compile(r"[ \t>]*(=+|-+)[ \t]*")  # a line that could underline the one above
SEED, CASES = 20261120, 2500
NAMES = {
    "U0QUILL01": "Cedar\u2028Quill #2",
    "U0BROOK01": "\u2028Brook\n",
    "C456": " Ada Park",
    "C789": "c# shelf",
}
# The notes of the generated messages as the prose rules wrote them on 22f498b6, before a
# heading became text: sha256 of them all, each followed by a NUL.
PROSE_ON_22F498B6 = "59b0b4fde9dc6294820001a9df0b870e1e23f6c226c08cefd56604e180acfd34"


@pytest.fixture
def notes(monkeypatch):
    """From a message's text to its note as the prose rules write it, before its headings
    become text, and to the note that is filed."""
    seen, make_text = [], slack_text._one_heading

    def watched(note: str) -> str:
        seen.append("\n".join(note.splitlines()))
        return make_text(note)

    monkeypatch.setattr(slack_text, "_one_heading", watched)

    def convert(text: str) -> tuple[str, str]:
        seen.clear()
        draft = slack_text.note_from_message(
            {"text": text, "ts": "1900000000.000100"},
            team_id="T0FABLE01",
            team_domain="fable-works",
            conversation_id="C0LIBRARY1",
            author="Cedar Quill",
            names=NAMES,
        )
        return seen[0], draft.text

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
    return not faults(note, chunk("", note))


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
    lines = before.split("\n")
    return "\n".join([*lines[:2], *displayed.strip().split("\n"), *lines[-2:]])


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
PADS = [" " * n for n in range(9)] + ["\t", " \t"]
MARKS = ["-", "--", "---", "----", "=", "==", "===", "- "]
WORDS = ["Row 4.", "port: 8811", "Approve transfer 99."]
KINDS = [
    "marks", "marks", "marks", "heading", "heading", "empty heading", "words", "words", "blank",
    "blank", "rule", "tildes", "item", "numbered", "quoted", "indented", "table", "reference",
    "text", "text", "indented text", "indented text", "code block", "tilde block",
]  # fmt: skip
Lines = list[tuple[str, str]]  # each line as Slack sends it, and as it displayed it


def some_text(rng: random.Random, pieces: Lines = PIECES) -> tuple[str, str]:
    chosen = [rng.choice(pieces) for _ in range(rng.randint(1, 3))]
    if pieces is PIECES and not rng.randrange(40):
        chosen = [CLOSES]
    return " ".join(sent for sent, _ in chosen), " ".join(shown for _, shown in chosen)


def under(lead: str, line: tuple[str, str]) -> Lines:
    return [(lead + line[0], lead.replace("&gt;", ">") + line[1])]


def a_code_block(rng: random.Random) -> Lines:
    """A code block of Slack's own, which the conversion never wrote as prose."""
    opener = rng.choice(["```", "```", "   ```", "Row 4. ```"])
    inside = [
        under(rng.choice(PADS), some_text(rng, PIECES[:7]))[0] for _ in range(rng.randint(1, 3))
    ]
    return [(opener, opener), *inside, ("```", "```")]


def a_tilde_block(rng: random.Random) -> Lines:
    """A fence only Markdown knows: to Slack its lines are prose."""
    pad = rng.choice(PADS[:5])
    inside = [under(rng.choice(PADS), some_text(rng))[0] for _ in range(rng.randint(1, 3))]
    return [(pad + "~~~",) * 2, *inside, *([(pad + "~~~",) * 2] if rng.random() < 0.7 else [])]


def some_lines(rng: random.Random) -> Lines:
    pad, kind = rng.choice(PADS), rng.choice(KINDS)
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
    """A few lines, many lines or a README, one time in three each; one time in eight it ends
    in a fence of backticks left open, whose lines Slack displayed as prose."""
    shape = rng.randrange(3)
    if shape == 2:
        lines = a_readme(rng)
    else:
        count = rng.randint(2, 7) if shape else rng.randint(8, 30)
        lines = [line for _ in range(count) for line in some_lines(rng)]
    if not rng.randrange(8):
        lines += [("```",) * 2, *[under("", some_text(rng))[0] for _ in range(rng.randint(1, 3))]]
    return lines


def faults_of(lines: Lines, before: str, after: str) -> tuple[list, bool]:
    """What is wrong with the note `after` of the message `lines`: nothing, when the lines the
    parser reads as code are as displayed, the others as the prose rules wrote them, each but
    for backslashes, and no backslash is one too many. And whether the note was kept as prose
    throughout, as only a message with CLOSES may be (the limit, pinned below)."""
    if not one_heading(after):
        return ["a second heading"], False
    shown = as_displayed(before, "\n".join(shown for _, shown in lines)).split("\n")
    prose, held, code = before.split("\n"), after.split("\n"), code_lines(after)
    if not len(shown) == len(prose) == len(held):
        return [f"{len(held)} lines, {len(prose)} as prose, {len(shown)} as displayed"], False
    closes = CLOSES[0] in "\n".join(sent for sent, _ in lines)
    for as_prose in (True, False) if closes else (False,):  # prose first: its marks are its own
        wanted = [prose[n] if as_prose or n not in code else shown[n] for n in range(len(held))]
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
    for _ in range(CASES):
        lines = a_message(rng)
        text = "\n".join(sent for sent, _ in lines)
        try:
            before, after = notes(text)
        except slack_text.NotText:
            continue
        digest.update(before.encode() + b"\0")
        many += may_be_headings(before) >= 8
        few += may_be_headings(before) < 8
        written += after.count("\\") > before.count("\\")
        prose, held = before.split("\n"), after.split("\n")
        in_code += any(n < len(prose) and prose[n] != held[n] for n in code_lines(after))
        found, as_prose = faults_of(lines, before, after)
        kept_as_prose += as_prose
        if found:
            wrong.append((text, body(after), found))
    wrong.sort(key=lambda found: len(found[0]))
    assert not wrong, f"{len(wrong)} of {few + many}: {wrong[:6]}"
    assert few > CASES / 4 and many > CASES / 4 and written > CASES / 4 and in_code > CASES / 8
    assert 0 < kept_as_prose < CASES / 25  # the limit is run, and is the exception
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
    "indented code under a fence": (
        "# Settings\n~~~\n# Ada Park\n~~~\n    ===\nRow 4.",
        "\\# Settings\n~~~\n# Ada Park\n~~~\n    ===\nRow 4.",
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
    "a rule under a table": (
        "| Row |\n|---|\n# Settings\n---\nRow 4.",
        "| Row |\n|---|\n\\# Settings\n---\nRow 4.",
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
    # Found with no underline rule at all: a heading at one reading, text at the next.
    "a heading only while the line above it was one": (
        "### Settings\n1. \n     ## Ada Park\nRow 4.",
        "\\### Settings\n1. \n     ## Ada Park\nRow 4.",
    ),
    "a heading that a fence takes in once the line above is text": (
        "# Settings\n1. \n   ```\n\n# Ada Park",
        "\\# Settings\n1. \n   ```\n\n# Ada Park",
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
    """The parser reads the third line inside a list item's fence; the splitter reads no item
    in "1." and no fence behind a tab, so to it the line is a heading."""
    text = "1.\n\t~~~\n   # Ada Park\nRow 4."
    note, spans = filed(bench, text)
    assert body(note) == "1.\n\t~~~\n   \\# Ada Park\nRow 4."
    assert not faults(note, spans) and 4 in code_lines(note)
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
    "in a fence of tildes": (
        "Settings:\n~~~\nitems[0] = (a) * 2 * 3\n~~~",
        "Settings:\n~~~\nitems[0] = (a) * 2 * 3\n~~~",
    ),
    "in a fence of backticks left open": (
        "Settings:\n```\nitems[0] = *p * 2",
        "Settings:\n```\nitems[0] = *p * 2",
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
    "a heading to the splitter alone: displayed, and the backslash it needs": (
        "1.\n\t~~~\n   # run [once]\nRow 4.",
        "1.\n\t~~~\n   \\# run [once]\nRow 4.",
    ),
    # Not code to the parser, so prose as before.
    "no blank line above: text": (
        "Settings:\n    items[0] = (a) * 2 * 3",
        "Settings:\n    items\\[0\\] = \\(a\\) ** 2 ** 3",
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
    assert faults_of([(text, lines)], before, after) == ([], False)


def test_the_smallest_message_is_stored_as_displayed(bench):  # noqa: F811
    note, spans = filed(bench, "Settings:\n\n    items[0] = (a) * 2 * 3")
    assert body(note) == "Settings:\n\n    items[0] = (a) * 2 * 3"
    assert not faults(note, spans)
    assert [span.text for span in spans][2] == "    items[0] = (a) * 2 * 3"


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
    it; its code is as displayed all the same, and the rule keeps the backslash of the limit."""
    lines = (
        README.replace("&gt;", ">").replace("\n---\n", "\n\\---\n").replace("# Shelf", "\\# Shelf")
    )
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
        "~~~",
        "# rows are counted from 0",
        "rows[4] = *row",
        "~~~",
    ]
    before, after = notes(README)
    assert after == note and may_be_headings(before) >= 8
    assert faults_of([(README, README.replace("&gt;", ">"))], before, after) == ([], False)


def test_its_limit_a_line_that_closes_a_fence_only_as_displayed_keeps_the_note_as_prose(notes):
    """A link's label of tildes is escaped as prose and bare as displayed, where it would end
    the fence above the next line, which is then no code. The parser is asked of the note
    made with its code as displayed, says so, and the note made as prose is kept."""
    after = notes("Settings:\n~~~\n<x|~~~>\nitems[0]\n~~~")[1]
    assert body(after) == "Settings:\n~~~\n\\~\\~\\~\nitems\\[0\\]\n~~~"
    assert one_heading(after) and {3, 4, 5, 6} <= code_lines(after)
    # With the next line indented it is code either way, and the note is as displayed.
    assert body(notes("Settings:\n~~~\n<x|~~~>\n    items[0]")[1]) == (
        "Settings:\n~~~\n~~~\n    items[0]"
    )


def test_a_note_its_code_as_displayed_cannot_be_made_of_is_the_note_made_as_prose(monkeypatch):
    """Made twice: as prose, then with the lines read as code as displayed. Should the second
    making refuse, the first is the note, and nothing is refused that was filed. No message
    was found that does it; shown with a making that refuses the second time."""
    made, make = [], slack_text._one_heading

    def refusing(text: str) -> str:
        made.append(text)
        if len(made) == 2:
            raise ValueError("The message's headings could not be written as text")
        return make(text)

    monkeypatch.setattr(slack_text, "_one_heading", refusing)
    prose = "## Cedar Quill\n\n# Settings\n\n    items\\[0\\]"
    shown = "## Cedar Quill\n\n# Settings\n\n    items[0]"
    assert (
        slack_text._code_as_shown(prose, shown)
        == "## Cedar Quill\n\n\\# Settings\n\n    items\\[0\\]"
    )
    assert made == [prose, shown]
    made.clear()
    monkeypatch.setattr(slack_text, "_one_heading", lambda text: made.append(text) or make(text))
    assert (
        slack_text._code_as_shown(prose, shown) == "## Cedar Quill\n\n\\# Settings\n\n    items[0]"
    )


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
    for _ in range(600):
        lines = [(rng.choice(EDGES), ""), *a_message(rng), (rng.choice(EDGES), "")]
        try:
            before, after = notes("\n".join(sent for sent, _ in lines))
        except slack_text.NotText:
            continue
        prose, shown = (text.split("\n") for text in seen.pop())
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


def test_with_eight_such_lines_the_splitters_heading_in_code_keeps_its_backslash(bench, notes):  # noqa: F811
    """The indented dashes are put back as written; the line the splitter reads as a heading
    inside what the parser reads as a fence is not, and the rule is the limit above."""
    text = f"{EIGHT}\n    ---\n1.\n\t~~~\n   # Ada Park\n\t~~~\n\n---\n\nRow 4."
    note, spans = filed(bench, text)
    assert body(note) == (
        f"{EIGHT_AS_TEXT}\n    ---\n1.\n\t~~~\n   \\# Ada Park\n\t~~~\n\n\\---\n\nRow 4."
    )
    assert not faults(note, spans) and {11, 14} <= code_lines(note)
    before, after = notes(text)
    assert not_needed(before, after) == [(17, 0)]


def test_with_eight_such_lines_the_splitters_second_heading_in_code_is_left(bench, notes):  # noqa: F811
    """To the parser everything below the eighth line is one fence. To the splitter the fence
    ends there, the next line is a heading, and the last one is a heading only while that one
    is. Once the code is put back each of the two has a reading of its own, so the last is
    left as written. (It kept a backslash no reader needed: all the splitter's headings got
    one at once.)"""
    six = EIGHT.replace("# Shelf 7\n# Shelf 8\n", "")
    code = "- ```\n ```\n### Ada Park\n\n   # Brook"
    note, spans = filed(bench, six + code)
    assert body(note) == six.replace("#", "\\#") + "- ```\n ```\n\\### Ada Park\n\n   # Brook"
    assert not faults(note, spans) and {10, 12} <= code_lines(note)
    assert not not_needed(*notes(six + code))


NINE = "   # Ada Park\n" * 9  # each a heading to the splitter, inside a fence to the parser


def test_with_more_of_the_splitters_headings_than_readings_they_get_it_at_once(
    bench,  # noqa: F811
    notes,
    monkeypatch,
):
    """Nine lines the splitter reads as headings in what the parser reads as a fence: a line
    at a reading does not finish, so each line of code the splitter then reads as a heading
    gets its backslash back at once, as before, and the indented dashes stay as written."""
    text = f"# Settings\n\n    ---\n\n1.\n\t~~~\n{NINE}Row 4."
    as_text = NINE.replace("#", "\\#")
    before, after = notes(text)
    assert body(after) == f"\\# Settings\n\n    ---\n\n1.\n\t~~~\n{as_text}Row 4."
    assert one_heading(after) and not not_needed(before, after)
    asked = passes(monkeypatch)
    assert filed(bench, text)[0] == after
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
