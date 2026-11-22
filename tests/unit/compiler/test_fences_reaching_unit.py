"""Whether a fence would hold the line that follows its claim: the parser's answer, and
what of it a forget may act on (CORE9A; spans.fences_reaching, erasure._before_it).

What is leant on is one relation of the installed parser's fence token: its `map` is the
line it opens on and the line after the last line it takes, so a line written after a
blank one is the fence's when it lies inside the map. Pinned first, as a table: a closed
fence, one ended by its list item, one ended by the end of the text, with and without
blank lines at the end. Whether a fence has its closing line is never read from it.

Then the question itself, and the one thing about it a caller must know: the answers
after the first No are a guess. Made-up text."""

import io
import itertools
import random
from bisect import bisect_right
from contextlib import closing

import pytest
from markdown_it import MarkdownIt

from cairn import compiler
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.spans import CLOSERS_MAX, _leaving_fence, asked_line, fences_reaching
from cairn.store import Store, erasure
from support.compiler.generated_forget_fences import KINDS, MARKERS, after_it, before_it

READER = MarkdownIt("commonmark", {"html": False}).enable("table")  # as the package reads

MAPS = {
    "closed, the text ends": ("~~~\ncode\n~~~\n", [0, 3], None),
    "closed, a line after a blank": ("~~~\ncode\n~~~\n\nnext\n", [0, 3], False),
    "closed in a list item, a line of the item after": (
        "- ~~~\n  x\n  ~~~\n\n  n\n",
        [0, 3],
        False,
    ),
    "ended by its list item: a line outside it": ("- ~~~\n  code\n\nnext\n", [0, 3], False),
    "ended by its list item: the next item": ("- ~~~\n  code\n\n- next\n", [0, 3], False),
    "not ended: a line of its item after a blank": ("- ~~~\n  code\n\n  next\n", [0, 4], True),
    "not ended: a line of its item after two blanks": ("- ~~~\n  code\n\n\n  next\n", [0, 5], True),
    "its closing line after a blank": ("- ~~~\n  code\n\n  ~~~\n", [0, 4], True),
    "in a list item, ended by the end of the text": ("- ~~~\n  code\n", [0, 2], None),
    "in a list item, the text ends in blank lines": ("- ~~~\n  code\n\n\n", [0, 4], None),
    "at the margin, ended by the end of the text": ("~~~\ncode\n", [0, 2], None),
    "at the margin, the text ends in blank lines": ("~~~\ncode\n\n\n", [0, 4], None),
    "at the margin, a line after a blank": ("~~~\ncode\n\nnext\n", [0, 4], True),
}


@pytest.mark.parametrize("name", list(MAPS))
def test_a_fence_tokens_map_ends_after_the_last_line_the_fence_takes(name):
    """The table. The third value: whether the last line of the text, written after a blank
    one, is inside the map (None where the text has no such line)."""
    text, mapped, takes = MAPS[name]
    (fence,) = [token for token in READER.parse(text) if token.type == "fence"]
    assert fence.map == mapped
    if takes is not None:
        last = len(text.split("\n")) - 2  # the text ends with a line break
        assert (fence.map[0] < last < fence.map[1]) is takes


ASKED = [
    # (the block, the line written after it and a blank line, whether its fence takes it)
    ("~~~\ncode\n~~~", "next", False),
    ("- ~~~\n  code\n  ~~~", "  next", False),  # closed: the item holds the line, no fence does
    ("- ~~~\n  code", "  next", True),
    ("- ~~~\n  code", "next", False),
    ("- ~~~\n  code", "- next", False),
    ("- ~~~\n  code", "  ~~~", True),  # its closing line
    ("-   ~~~\n    code", "  next", False),  # the item's text starts further in
    ("-   ~~~\n    code", "    next", True),
    ("1. ~~~\n   code", "  next", False),
    ("1. ~~~\n   code", "   next", True),
    ("- a\n\n    - ~~~\n      code", "  next", False),  # the outer item's line
    ("- a\n\n    - ~~~\n      code", "      next", True),
    ("- ~~~\n  code\n\n", "  next", True),  # blank lines at the block's end
    ("- ~~~\n  code\n\n", "next", False),
    ("- `code`", "  next", False),  # no fence
    ("words", "    next", False),
]


@pytest.mark.parametrize(("block", "line", "taken"), ASKED)
def test_whether_a_fence_of_the_block_takes_the_line_after_it(block, line, taken):
    assert fences_reaching("# Ops\n\n", [block], line) == [taken]


def test_the_blocks_are_asked_in_one_parse_and_each_as_the_body_has_it(monkeypatch):
    """Three sibling items under one list item: each holds the line, and a block is read
    after the blocks before it, as the body reads it (alone, each is indented text)."""
    before = "# Ops\n\n- parent\n\n"
    siblings = [f"    - ~~~\n      sample {n}" for n in range(3)]
    parses = []
    parse = MarkdownIt.parse
    monkeypatch.setattr(
        MarkdownIt,
        "parse",
        lambda reader, source, *a: parses.append(1) or parse(reader, source, *a),
    )
    assert fences_reaching(before, siblings, "      tail") == [True, True, True]
    assert fences_reaching(before, siblings, "    tail") == [False, False, False]
    assert len(parses) == 2
    assert fences_reaching("# Ops\n\n", siblings, "      tail") == [False, False, False]
    assert fences_reaching(before, [], "      tail") == []


BEFORE = "# Ops\n\n- parent\n\n    - mid\n\n"
INNER, UNDER_IT = "      - `x`", "        ~~~\n        y"  # an item in `mid`, a fence in that item
LINE = "      tail"  # held by `mid`, not by the item in it


def test_an_answer_after_the_first_no_is_a_guess():
    """Why a forget asks again. The line is not in the inner item, so written after it the
    line ends that item, and the fence after it is then read in `mid`, which does hold the
    line: Yes. In the body the fence stands in the inner item, which does not: No."""
    assert fences_reaching(BEFORE, [INNER, UNDER_IT], LINE) == [False, True]
    assert fences_reaching(f"{BEFORE}{INNER}\n\n", [UNDER_IT], LINE) == [False]


def test_a_forget_does_not_act_on_the_guess():
    """That page, filed: sixteen closers spent, then the items, a sibling of `mid` whose
    fence covers the tail once the paragraph between is forgotten. The sibling is named
    and leaves alone, as in the plain loop; the fence in the inner item, which the first
    question said would hold the tail, is asked again and stays."""
    spent = [f"    ~~~\n    first {n}" for n in range(CLOSERS_MAX)]
    named = "    - ~~~\n      Quill owns the log."
    with closing(Store(":memory:")) as store:
        store.create_project("alpha")
        memory, steps = Memory(store, lint_gate), "## Steps\n\n"
        notes = [
            "- item",
            "\n\n".join([*spent, "  End of first."]),
            f"{steps}- parent",
            f"{steps}    - mid",
            f"{steps}{INNER}",
            f"{steps}{UNDER_IT}\n\n{named}",
            f"{steps}Between.",
            f"{steps}{LINE}",
        ]
        ids = [memory.note("alpha", text, title="Ops", topic="Ops") for text in notes]
        assert {answer["status"] for answer in ids} == {"saved"}
        asked, ask = [], erasure.fences_reaching
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(erasure, "fences_reaching", lambda *a: asked.append(ask(*a)) or asked[-1])
            store.forget_note(ids[6]["note_id"])
        page = store.get_page(store.get_project("alpha").id, "ops")
        assert [claim.text for claim in page.claims] == [
            "- item",
            *spent,
            "  End of first.",
            "- parent",
            "    - mid",
            INNER,
            UNDER_IT,
            LINE,
        ]
        assert asked == [[False, True], [False]]
        spans = store.get_spans(i for claim in page.claims for i in claim.span_ids)
        assert compiler.left_to_run("Ops", page.claims, spans)[1] is None
        archive = io.BytesIO()
        store.export(archive)
        with closing(Store(":memory:")) as target:
            target.import_export(io.BytesIO(archive.getvalue()))


def test_the_answers_up_to_the_first_no_are_those_of_each_block_asked_in_its_place():
    """Generated, 400 runs of blocks as test_generated_forget_fences_unit writes them under
    a list item: asked together, every answer up to the first No, that one included, is
    the answer when the block is asked alone after the blocks before it, with the line
    after it and after no other. What a forget takes rests on this and on nothing else."""
    rng = random.Random(20261123)
    yes = no = wrong_after = 0
    for _ in range(400):
        numbers = itertools.count()
        head = rng.choice(MARKERS)
        blocks = before_it(
            rng, numbers, len(head), rng.choice((2, 3, 5, 8, 12)), 16, rng.choice(KINDS)
        )
        line = after_it(rng, numbers, 1, "tail")[0].split("\n")[0]
        before = f"# Ops\n\n{head}list\n\n"
        together = fences_reaching(before, blocks, line)
        for n, block in enumerate(blocks):
            alone = fences_reaching(before + "".join(f"{b}\n\n" for b in blocks[:n]), [block], line)
            if False in together[:n]:
                wrong_after += together[n] != alone[0]
                continue
            assert together[n] == alone[0], (blocks[: n + 1], line)
            yes, no = yes + alone[0], no + (not alone[0])
    # both answers, many times; and of the answers after a first No, which are not
    # compared, three were not the block's own: the reason a forget asks again
    assert (yes, no, wrong_after) == (773, 293, 3)


# The line asked about is written cut when it is longer than the claim it follows (RT1: a
# long line after many claims let only a few be asked in a question of twice the body).
# What the parser's answer depends on, asked of the parser: a table, then generated blocks.


def whole_line(before: str, blocks: list[str], line: str) -> list[bool]:
    """fences_reaching as it was before the cut: `line` written whole after each block."""
    parts, first, asked, count = [before], [], [], before.count("\n")
    for block in blocks:
        first.append(count)
        count += f"{block}\n\n".count("\n")
        asked.append(count)  # where `line` stands after this block
        count += 2
        parts.append(f"{block}\n\n{line}\n\n")
    taken = [False] * len(first)
    for token in READER.parse("".join(parts)):
        if token.type == "fence" and token.map:
            at = bisect_right(first, token.map[0]) - 1
            if at >= 0 and token.map[0] < asked[at] < token.map[1]:
                taken[at] = True
    return taken


SIBLING = "    - ~~~\n      sample"  # its item's text starts at column six
CUT = [
    # (the line, what is written after SIBLING in its place)
    ("      tail", "      tail"),  # no longer than the claim's longest line: as it is
    ("        " + "tail " * 150, "        t"),
    ("                              ", ""),  # blank: nothing
    ("\t\t" + "x" * 40, "\t\tx"),
    (" " * 3000 + "x", "\t\t\tx"),  # deeper than the claim reaches: as deep, twelve columns
    ("~" * 400, "~"),
    ("      " + "`" * 30 + " " * 50 + "x", "      `"),
    ("  > " + "quoted " * 9, "  >"),
    ("      - " + "item " * 9, "      -"),
]


@pytest.mark.parametrize(("line", "written"), CUT)
def test_a_line_longer_than_the_claim_is_cut_to_its_indentation_and_first_character(line, written):
    assert asked_line(line, SIBLING) == written


LINES = [
    *(
        f"{pad}{char * n}{end}"
        for pad in ("", "  ", "      ", "        ", "\t", "  \t")
        for char in "`~"
        for n in (3, 4, 9, 300)
        for end in ("", "   ", " x")
    ),
    *(
        f"{pad}{mark} {'word ' * 40}"
        for pad in ("", "  ", "      ", "        ")
        for mark in (">", "-", "1.", "10)", "*", "#", "|")
    ),
    *(f"{pad}{char * 5}{' ' * 200}x" for pad in ("", "      ") for char in "`~"),
    "\t\t" + "tail " * 60,
    " \t " + "tail " * 60,
    " " * 5000 + "deep",
    " " * 300,
    "      " + "tail " * 8000,
]
HOLDERS = [  # blocks whose fence may hold what follows, each after `- parent` and a blank
    SIBLING,
    "    - item\n      ~~~\n      sample",
    "    -   ~~~\n        wide",
    "    1. ```text\n       numbered",
    "    - - ~~~\n          nested",
    "    -\t~~~\n    \ttabbed",
    "\t- ~~~\n\t  tab before",
    "    > ~~~\n    > quoted",
    "  ~~~\n  in the parent",
    "    - ~~~\n      closed\n      ~~~",
    "      plain",
]


@pytest.mark.parametrize("line", LINES, ids=lambda line: repr(line[:24]))
def test_the_cut_line_gets_the_whole_lines_answer_for_every_block(line):
    """The table: each block alone after a list item, then all of them in a run in which
    no fence runs from one block into the next (as the claims a forget asks of stand), with
    the line whole and as fences_reaching writes it. The same answers, up to the first No
    and that one included: what a forget acts on."""
    before = "# Ops\n\n- parent\n\n"
    for block in HOLDERS:
        assert fences_reaching(before, [block], line) == whole_line(before, [block], line)
    for run in itertools.permutations(HOLDERS[:5] + HOLDERS[9:], 3):
        if _leaving_fence([before.rstrip("\n"), *run]) is not None:
            continue
        whole, cut = whole_line(before, list(run), line), fences_reaching(before, list(run), line)
        upto = whole.index(False) + 1 if False in whole else len(whole)
        assert cut[:upto] == whole[:upto], (run, line)


def test_generated_runs_get_the_whole_lines_answers_up_to_the_first_no():
    """Generated: 3,000 runs of blocks as test_generated_forget_fences_unit writes them
    under a list item, each followed by a line of one of the shapes above or a tail. In
    every run where no fence runs from one block into the next, the answers up to the first
    No, that one included, are the whole line's; after it, where the forget asks again, a
    few are not (pinned: the cut line is not a fence opener, a list item or a quotation)."""
    rng = random.Random(20261127)
    shapes = [*LINES, *(f"{' ' * n}tail {'x' * k}" for n in range(0, 13) for k in (1, 60, 900))]
    runs = compared = guesses_differ = 0
    for _ in range(3000):
        numbers = itertools.count()
        head = rng.choice(MARKERS)
        blocks = before_it(
            rng, numbers, len(head), rng.choice((1, 2, 3, 5, 8)), 16, rng.choice(KINDS)
        )
        if rng.random() < 0.3:
            blocks.append(rng.choice(HOLDERS))
        line = rng.choice(shapes)
        before = f"# Ops\n\n{head}list\n\n"
        if _leaving_fence([before.rstrip("\n"), *blocks]) is not None:
            continue
        whole, cut = whole_line(before, blocks, line), fences_reaching(before, blocks, line)
        upto = whole.index(False) + 1 if False in whole else len(whole)
        assert cut[:upto] == whole[:upto], (blocks, line)
        runs, compared = runs + 1, compared + upto
        guesses_differ += cut[upto:] != whole[upto:]
    assert (runs, compared, guesses_differ) == (2235, 3436, 23)
