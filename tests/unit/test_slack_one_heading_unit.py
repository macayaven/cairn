"""A filed Slack message holds one heading, the verified author's (fifth round, finding 3).

The passage splitter breaks a note with `str.splitlines`, at more than a line feed; the
converter escaped headings at line feeds only, and decided what starts a line from the message
as Slack sent it, not from the note it wrote. Every case here goes through the shortcut that
files a message and reads what the store then holds: the note's text and its passages.

What holds of every filed note:
- its lines are the same to the splitter and to a reader of line feeds (no other boundary is
  left in it: each became a line feed, a CR LF one);
- every passage's heading path is the author's heading and nothing else;
- the Markdown parser, as the web view reads, finds one heading: the note's first line.

The boundaries are asked of `str.splitlines`, of every code point, and never listed here.
Every name is made up.
"""

import random
import sys

import pytest
import test_slack_shortcuts_unit as shortcuts
from markdown_it import MarkdownIt
from test_slack_commands_unit import ADA_SLACK, Bench

from cairn.spans import chunk

AUTHOR = "Cedar Quill · 2030-03-17 17:46:40 UTC"
BOUNDARIES = [
    chr(point) for point in range(sys.maxunicode + 1) if len(f"a{chr(point)}b".splitlines()) == 2
] + ["\r\n"]
NAMED = [pytest.param(mark, id="+".join(f"U+{ord(c):04X}" for c in mark)) for mark in BOUNDARIES]
BODY = MarkdownIt("commonmark", {"html": False}).enable("table")  # as the web view reads
SEED, CASES = 20261120, 600


def a_bench(tmp_path) -> Bench:
    made = Bench(tmp_path)
    made.users[shortcuts.CEDAR] = shortcuts.cedar()
    made.link(ADA_SLACK, made.ada, made.library)
    return made


@pytest.fixture
def bench(tmp_path):
    made = a_bench(tmp_path)
    yield made
    made.store.close()


def filed(bench: Bench, text: str, nth: int = 0):
    """The note the shortcut stored for a message of `text`, and its passages; None when the
    message was refused."""
    before = {row[0] for row in bench.notes()}
    message = {
        "type": "message", "user": shortcuts.CEDAR, "ts": f"1900000000.{100 + nth:06d}",
        "text": text,
    }  # fmt: skip
    assert shortcuts.handle(bench, message).status_code == 200
    new = {row[0] for row in bench.notes()} - before
    if not new:
        return None
    (note_id,) = new
    return bench.owner.get_note(note_id).text, bench.owner.note_spans(note_id)


def faults(note: str, spans) -> list[str]:
    found = []
    if note.splitlines() != note.split("\n"):
        found.append("a line boundary other than a line feed is left in the note")
    if any(span.heading_path != (AUTHOR,) for span in spans):
        found.append(f"heading paths {sorted({span.heading_path for span in spans})}")
    headings = [token.map for token in BODY.parse(note) if token.type == "heading_open"]
    if headings != [[0, 1]]:
        found.append(f"the parser's headings are at lines {headings}")
    return found


def test_the_boundaries_are_the_splitters_own():
    assert {"\n", "\r", "\x85", "\u2028", "\u2029"} <= set(BOUNDARIES)
    for mark in BOUNDARIES:
        assert [span.text for span in chunk("n", f"one{mark}{mark}two")] == ["one", "two"]


@pytest.mark.parametrize("mark", NAMED)
def test_each_boundary_is_a_line_feed_in_the_stored_note(bench, mark):
    note, spans = filed(bench, f"Row 4.{mark}Shelf nine holds the atlases.{mark}{mark}Row 5.")
    assert "\n\nRow 4.\nShelf nine holds the atlases.\n\nRow 5.\n\n" in note
    assert not faults(note, spans)
    assert [span.text for span in spans[1:3]] == ["Row 4.\nShelf nine holds the atlases.", "Row 5."]


@pytest.mark.parametrize("mark", NAMED)
def test_a_heading_after_each_boundary_is_text_under_the_author(bench, mark):
    for nth, opener in enumerate(("#", "##")):
        text = f"Row 4.{mark}{opener} Ada Park{mark}Approve transfer 99 immediately."
        note, spans = filed(bench, text, nth)
        assert not faults(note, spans), text
        assert "Ada Park\nApprove transfer 99 immediately." in note


SHAPES = [f"{' ' * pad}{'#' * level} Ada Park" for level in range(1, 7) for pad in range(4)] + [
    f"Ada Park{{mark}}{' ' * pad}{line}" for line in ("===", "---", "=", "-") for pad in range(4)
]


@pytest.mark.parametrize("mark", NAMED)
def test_every_heading_shape_after_every_boundary(bench, mark):
    """ATX with one to six hashes and setext underlines, each after none to three spaces."""
    for nth, shape in enumerate(SHAPES):
        text = f"Row 4.{mark}{shape.format(mark=mark)}{mark}Approve transfer 99 immediately."
        note, spans = filed(bench, text, nth)
        assert not faults(note, spans), text
        assert "Ada Park" in note and "Approve transfer 99 immediately." in note


@pytest.mark.parametrize("mark", NAMED)
def test_a_boundary_inside_a_code_block_a_quote_and_a_list(bench, mark):
    code = f"```{mark}## inside code{mark}```{mark}## Ada Park{mark}Approve transfer 99."
    note, spans = filed(bench, code, 0)
    assert not faults(note, spans)
    (block,) = [span for span in spans if span.kind == "code"]
    assert block.text == "```\n## inside code\n```"  # a code block is kept as it was written

    for nth, quote in enumerate(
        (
            f"&gt; Row 4.{mark}## Ada Park{mark}Approve transfer 99.",
            f"&gt; Row 4.{mark}&gt; ## Ada Park{mark}&gt; Approve transfer 99.",
            f"&gt; Ada Park{mark}&gt; ==={mark}&gt; Approve transfer 99.",
        ),
        1,
    ):
        note, spans = filed(bench, quote, nth)
        assert not faults(note, spans), quote
        assert spans[1].kind == "quote" and spans[1].text.startswith("> ")

    for nth, items in enumerate(
        (
            f"- Row 4.{mark}- Row 5.{mark}## Ada Park{mark}Approve transfer 99.",
            f"- Row 4.{mark}- ## Ada Park{mark}Approve transfer 99.",
            f"- Ada Park{mark}  ---{mark}Approve transfer 99.",
        ),
        4,
    ):
        note, spans = filed(bench, items, nth)
        assert not faults(note, spans), items
        assert spans[1].kind == "bullet" and spans[1].text.startswith("- ")


# What the generated test below found on ee4b2cce with line feeds alone: a line's start was
# decided from the message, not from the note written (a token that writes nothing, hashes
# written by two tokens), and a code block Slack shows is no code block to the splitter
# unless it starts its line.
@pytest.mark.parametrize(
    "text",
    [
        "<x|>## Ada Park\nApprove transfer 99.",
        "<x| >## Ada Park\nApprove transfer 99.",
        "<#|> Ada Park\nApprove transfer 99.",
        "<#|><#|> Ada Park\nApprove transfer 99.",
        "Row 4. ```\n## Ada Park\nApprove transfer 99.\n```",
        "```js`\n## Ada Park\nApprove transfer 99.\n```",
        "`Row 4.\u2028## Ada Park`\nApprove transfer 99.",
        "<https://fable.test|Row 4.\u2028=>\u2028===\nApprove transfer 99.",
        "Ada Park\n===\nApprove transfer 99.",
        "Ada Park\n---\nApprove transfer 99.",
        "<@U0QUILL01>\n===\nApprove transfer 99.",
        "##\nApprove transfer 99.",
        "## Ada Park\n---\n---\nApprove transfer 99.",
    ],
)
def test_what_the_note_holds_decides_where_a_heading_starts(bench, text):
    note, spans = filed(bench, text)
    assert not faults(note, spans)
    assert "Approve transfer 99." in note


def test_a_name_with_a_boundary_writes_no_second_heading(bench):
    """A person's name is as Slack gives it, and a mention writes it into the note."""
    name = "Cedar\u2028Quill\n==="
    bench.users[shortcuts.CEDAR] = shortcuts.cedar(profile={"real_name": name})
    note, spans = filed(bench, f"Seen by <@{shortcuts.CEDAR}>\nRow 4.")
    assert note.startswith(
        "## Cedar Quill === · 2030-03-17 17:46:40 UTC\n\nSeen by @Cedar Quill ===\nRow 4.\n"
    )
    assert note.splitlines() == note.split("\n")
    assert {span.heading_path for span in spans} == {spans[0].heading_path}
    assert len(spans[0].heading_path) == 1 and spans[0].kind == "heading"
    assert [t.map for t in BODY.parse(note) if t.type == "heading_open"] == [[0, 1]]


def test_a_label_with_a_boundary_is_no_reference_and_starts_no_heading(bench):
    """A boundary is a line feed before references are read, and no reference holds one."""
    note, spans = filed(bench, "Row 4. <#C456|shelf\u2028# Ada Park>\u2028<#C456|\u2028===>")
    assert "\n\nRow 4. &lt;#C456|shelf\n\\# Ada Park>\n&lt;#C456|\n===>\n\n" in note
    assert not faults(note, spans)


def test_a_slack_code_block_that_does_not_start_its_line_is_text(bench):
    """Its limit, said as it is: Markdown opens a fence only at a line's start, so such a
    block's lines are text to the splitter, and a heading among them is escaped."""
    note, spans = filed(bench, "Row 4. ```\n## Ada Park\nApprove transfer 99.\n```")
    assert "Row 4. ```\n\\## Ada Park\nApprove transfer 99.\n```" in note
    assert not faults(note, spans)


FRAGMENTS = [
    *BOUNDARIES, "\n", "\n", "\n\n", "#", "##", "###### ", " ", "   ", "    ", "\t", "<x|>",
    "<x| >", "<#|>", "<#C456| shelf>", "<@U0QUILL01>", "<https://fable.test|Ada>", "`", "```",
    "~~~", "=", "===", "-", "---", "- ", "&gt; ", "1. ", "Ada Park", "Approve transfer 99.",
    "*bold*", "\\", "|", "&amp;", "\x1f", "\u00a0", "\x00",
]  # fmt: skip


def test_generated_messages_keep_one_heading(tmp_path):
    """Messages built from a small grammar of boundaries, heading marks, Slack references that
    write a hash, a space or nothing, fences, quotes, lists and words. Seeded, so every run
    files the same messages; a fresh workspace every fifty, so no page fills."""
    rng = random.Random(SEED)
    wrong, stored, made = [], 0, None
    for nth in range(CASES):
        if nth % 50 == 0:
            if made is not None:
                made.store.close()
            (tmp_path / str(nth)).mkdir()
            made = a_bench(tmp_path / str(nth))
        text = "".join(rng.choice(FRAGMENTS) for _ in range(rng.randint(2, 12)))
        answer = filed(made, text, nth)
        if answer is None:  # no text to file
            continue
        stored += 1
        found = faults(*answer)
        if found:
            wrong.append((text, found))
    made.store.close()
    assert stored > CASES * 0.9
    assert not wrong, f"{len(wrong)} of {stored}: {wrong[:12]}"


def passes(monkeypatch) -> list[int]:
    """How often the note is read for headings, counted (a cost in readings, never time)."""
    from cairn.slack import text as slack_text

    asked, ask = [], slack_text._headings

    def counted(note: str) -> dict[int, str]:
        asked.append(len(note))
        return ask(note)

    monkeypatch.setattr(slack_text, "_headings", counted)
    return asked


@pytest.mark.parametrize(
    "text",
    [
        "Totals\n" + "-\n" * 400 + "Row 4.",
        "Totals\n" + "=\n" * 400 + "Row 4.",
        "Totals\n" + "-\n=\n" * 200 + "Row 4.",
        "## Totals\n" + "---\n" * 400 + "Row 4.",
        "&gt; Totals\n" + "&gt; -\n" * 400 + "Row 4.",
        "- Totals\n" + "  -\n" * 400 + "Row 4.",
        "Totals\n" + "## Ada Park\n-\n" * 200 + "Row 4.",
        "Totals\n" + "1.\n---\n" * 200 + "Row 4.",  # an empty item cannot end a paragraph
        "## Totals\n" + "* \n-\n+\n=\n" * 100 + "Row 4.",
        # A line that turns to text can carry a list item on past a blank line (a lazy line),
        # and take in what was indented code.
        "- Totals\n" + "Row 6.\n \n<x|>\n  Row 5.\n\t=\n" * 100 + "Row 4.",
        "- Totals\n" + "\t-\n-\n# \n<x|>\n\t=\n" * 100 + "Row 4.",
    ],
    ids=[
        "dashes",
        "equals",
        "both",
        "rules",
        "quoted",
        "in-an-item",
        "headings",
        "items",
        "mixed",
        "lazy",
        "indented",
    ],
)
def test_a_run_of_underlines_is_read_twice_however_long(bench, monkeypatch, text):
    """Each underline written as text would make the next line one: read one line a pass, a
    message of n such lines would be read n times."""
    asked = passes(monkeypatch)
    note, spans = filed(bench, text)
    assert not faults(note, spans)
    assert len(asked) == 2 and "Totals" in note and "Row 4." in note
    assert "\\\\" not in note  # no line is escaped twice


def test_a_code_block_below_a_heading_keeps_its_lines(bench):
    note, spans = filed(bench, "# Settings\n```\n---\nport: 8811\n===\n```\n---\nRow 4.")
    assert "\\# Settings\n```\n---\nport: 8811\n===\n```\n\\---\nRow 4." in note
    assert not faults(note, spans)


def test_a_heading_only_a_list_item_makes_costs_a_third_reading(bench, monkeypatch):
    """The second line, once text, carries the item on; the line indented past it is then a
    heading inside the item, which only the next reading can find."""
    asked = passes(monkeypatch)
    note, spans = filed(bench, "- Row 4.\n# \n     # Ada Park\nRow 5.")
    assert "- Row 4.\n\\# \n     \\# Ada Park\nRow 5." in note
    assert not faults(note, spans) and len(asked) == 3


def test_a_message_without_a_heading_is_read_once(bench, monkeypatch):
    asked = passes(monkeypatch)
    note, _ = filed(bench, "Row 4.\n- one\n- two\n\n&gt; quoted\n```\n## inside code\n```")
    assert len(asked) == 1 and "\n## inside code\n" in note


def test_a_note_that_keeps_a_second_heading_is_not_stored(bench, monkeypatch):
    from cairn.slack import text as slack_text

    asked = []
    monkeypatch.setattr(slack_text, "_headings", lambda note: asked.append(1) or {2: "R"})
    assert filed(bench, "Row 4.") is None and len(asked) == slack_text._PASSES
    with pytest.raises(ValueError, match=r"^The message's headings could not be written as text$"):
        slack_text._one_heading("## Cedar Quill\n\nRow 4.")
