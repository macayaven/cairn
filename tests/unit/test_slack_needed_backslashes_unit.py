"""A filed Slack message gets a backslash only where a reader asked for one (sixth round,
finding 3).

The note holds one heading, the author's: a line of the message that would read as another
gets a backslash and reads as text. The fifth round wrote that backslash on more lines than
that: below the first such line, on every line of only "=" or "-", a rule and a line of an
indented code block among them, though no reader read a heading there. The note says whose
words it holds, so a character added for nothing is a false quotation.

What holds, asked of the two readers themselves (the passage splitter and the Markdown parser
as the web view reads) and never of the converter:
- the note has one heading for both readers;
- when the message has fewer than eight lines that hold a "#" or are only "=" or "-" under a
  line that is not blank, every backslash is needed: taken out alone, a reader finds a second
  heading;
- with eight or more such lines the backslash is written as the fifth round wrote it (the
  limit, pinned below), and still not in a line the parser reads as code, unless the
  splitter reads a heading there once the code is as written.

The messages are generated from a seed, so every run reads the same ones, and what they found
on 22f498b6 is in the table. Every name is made up.
"""

import random
import re

import pytest
from markdown_it import MarkdownIt
from test_slack_one_heading_unit import bench, faults, filed, passes  # noqa: F401  (a fixture)

from cairn.slack import text as slack_text
from cairn.spans import chunk

READER = MarkdownIt("commonmark", {"html": False}).enable("table")  # as the web view reads
ONLY_MARKS = re.compile(r"[ \t>]*(=+|-+)[ \t]*")  # a line that could underline the one above
SEED, CASES = 20261120, 4000
NAMES = {"U0QUILL01": "Cedar\u2028Quill\n===", "C456": " Ada Park"}


@pytest.fixture
def notes(monkeypatch):
    """From a message's text to its note before and after its headings became text."""
    seen, make_text = [], slack_text._one_heading

    def watched(note: str) -> str:
        seen.append("\n".join(note.splitlines()))
        seen.append(make_text(note))
        return seen[-1]

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
        before, after = seen
        assert draft.text == after
        return before, after

    return convert


def added(before: str, after: str) -> list[tuple[int, int]]:
    """The line and column of each backslash `after` holds and `before` does not. Nothing
    else may differ."""
    found = []
    for n, (was, now) in enumerate(zip(before.split("\n"), after.split("\n"), strict=True)):
        at = 0
        for column, character in enumerate(now):
            if at < len(was) and was[at] == character:
                at += 1
            else:
                assert character == "\\", (was, now)
                found.append((n, column))
        assert at == len(was), (was, now)
    return found


def one_heading(note: str) -> bool:
    return not faults(note, chunk("", note))


def not_needed(before: str, after: str) -> list[tuple[int, int]]:
    """The added backslashes the note could do without: taken out alone, both readers still
    find one heading."""
    lines, spare = after.split("\n"), []
    for n, column in added(before, after):
        without = [*lines[:n], lines[n][:column] + lines[n][column + 1 :], *lines[n + 1 :]]
        if one_heading("\n".join(without)):
            spare.append((n, column))
    return spare


def code_lines(note: str) -> set[int]:
    """The lines the Markdown parser reads as code, fenced or indented."""
    return {
        line
        for token in READER.parse(note)
        if token.type in ("fence", "code_block")
        for line in range(*token.map)
    }


def changed_code(before: str, after: str) -> list[int]:
    """The lines of code `after` holds otherwise than `before`, but for those the splitter
    reads as headings once every line of code is as `before` has it."""
    was, now = before.split("\n"), after.split("\n")
    code = code_lines(after)
    as_written = [was[n] if n in code else line for n, line in enumerate(now)]
    headings = {s.start_line - 1 for s in chunk("", "\n".join(as_written)) if s.kind == "heading"}
    return sorted(n for n in code - headings if now[n] != was[n])


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


PADS = [" " * n for n in range(9)] + ["\t", " \t"]
MARKS = ["-", "--", "---", "----", "=", "==", "===", "- "]
WORDS = ["Row 4.", "port: 8811", "Approve transfer 99."]
REFERENCES = [
    "<@U0QUILL01>", "<#C456|shelf>", "<#C999| shelf>", "<#|> Ada Park", "<x|>",
    "<https://fable.test|# Ada Park>", "<https://fable.test|===>",
]  # fmt: skip
KINDS = [
    "marks", "marks", "marks", "heading", "heading", "empty heading", "words", "words", "blank",
    "blank", "rule", "backticks", "tildes", "item", "numbered", "quoted", "indented", "table",
    "reference",
]  # fmt: skip


def a_line(rng: random.Random) -> str:
    pad, kind = rng.choice(PADS), rng.choice(KINDS)
    if kind == "marks":
        return pad + rng.choice(MARKS)
    if kind == "heading":
        return pad + "#" * rng.randint(1, 3) + " Ada Park"
    if kind == "empty heading":
        return pad + "# "
    if kind == "words":
        return pad + rng.choice(WORDS)
    if kind == "rule":  # under a blank line: a thematic break, never an underline
        return "\n" + pad[:3] + "---"
    if kind == "backticks":
        return pad + "```"
    if kind == "tildes":
        return pad + "~~~"
    if kind == "item":
        marker = rng.choice(["- ", "* ", "+ ", "1. ", "- - "])
        return pad + marker + rng.choice(["Row 5.", "---", "# Ada Park", "", "```"])
    if kind == "numbered":
        return pad + "1."
    if kind == "quoted":
        return pad + "&gt; " + rng.choice(["Row 6.", "---", "===", "# Ada Park", "- Row 7."])
    if kind == "indented":
        return "    " + rng.choice(["---", "===", "# not a heading", "port: 8811"])
    if kind == "table":
        return rng.choice(["| Row |", "|---|"])
    if kind == "reference":
        return pad + rng.choice(REFERENCES)
    return ""


def a_message(rng: random.Random) -> str:
    """Two to seven lines, or eight to thirty one time in three."""
    count = rng.choice((rng.randint(2, 7), rng.randint(2, 7), rng.randint(8, 30)))
    return "\n".join(a_line(rng) for _ in range(count))


def test_generated_messages_get_only_the_backslashes_a_reader_asked_for(notes):
    rng = random.Random(SEED)
    few = many = written = 0
    wrong = []
    for _ in range(CASES):
        text = a_message(rng)
        try:
            before, after = notes(text)
        except slack_text.NotText:
            continue
        assert one_heading(after), text
        written += len(added(before, after))
        if may_be_headings(before) < 8:
            few += 1
            spare = not_needed(before, after)
        else:
            many += 1
            spare = changed_code(before, after)
        if spare:
            wrong.append((text, body(after), spare))
    assert few > CASES / 2 and many > CASES / 8 and written > CASES
    wrong.sort(key=lambda found: len(found[0]))
    assert not wrong, f"{len(wrong)} of {few + many}: {wrong[:10]}"


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


def test_its_limit_with_eight_such_lines_the_splitters_second_heading_in_code(bench, notes):  # noqa: F811
    """To the parser everything below the second line is one fence. To the splitter the fence
    ends there, the next line is a heading, and the last one is a heading too while that one
    is: both keep the backslash, though the last could do without once the other has it.
    With fewer than eight such lines it is left as written."""
    code = "- ```\n ```\n### Ada Park\n\n   # Brook"
    note, spans = filed(bench, EIGHT + code)
    assert body(note) == EIGHT_AS_TEXT + "- ```\n ```\n\\### Ada Park\n\n   \\# Brook"
    assert not faults(note, spans) and {12, 14} <= code_lines(note)
    before, after = notes(EIGHT + code)
    assert not_needed(before, after) == [(14, 3)] and not changed_code(before, after)
    assert body(filed(bench, code, 1)[0]) == "- ```\n ```\n\\### Ada Park\n\n   # Brook"
    assert not not_needed(*notes(code))


def test_code_is_put_back_only_when_the_readers_then_find_one_heading(monkeypatch):
    """No generated message reached this (the test above fails on one that does): shown with
    a reader that finds a heading once the code is as written. The note is then kept as it
    was, with its one heading, and never the one not yet read."""
    text = "## Cedar Quill\n\n# Settings\n\n    ---"
    note = "## Cedar Quill\n\n\\# Settings\n\n    \\---"
    put_back = "## Cedar Quill\n\n\\# Settings\n\n    ---"
    assert slack_text._code_as_written(text, note) == put_back
    asked = []
    monkeypatch.setattr(slack_text, "_headings", lambda read: asked.append(read) or {4: "-"})
    assert slack_text._code_as_written(text, note) == note and asked == [put_back]


def test_a_reading_a_line_and_no_more_readings_than_twice_the_bound(bench, monkeypatch):  # noqa: F811
    """What the change costs, in readings (never time): a line that reads as a heading has a
    reading of its own, where every such line was written at one reading."""
    asked = passes(monkeypatch)
    three = "# Shelf 1\nRow 4.\n# Shelf 2\nRow 5.\n# Shelf 3\nRow 6."
    assert "\\# Shelf 1\nRow 4.\n\\# Shelf 2\nRow 5.\n\\# Shelf 3\nRow 6." in filed(bench, three)[0]
    assert len(asked) == 4

    asked.clear()  # eight: as before, at one reading
    assert EIGHT_AS_TEXT in filed(bench, EIGHT + "Row 4.", 1)[0]
    assert len(asked) == 2

    asked.clear()  # and one reading more when code was put back as written
    assert "\n\n    ---\n\n" in filed(bench, EIGHT + "\n    ---\n\nRow 4.", 2)[0]
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
