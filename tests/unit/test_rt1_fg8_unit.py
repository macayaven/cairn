"""RT1, second pass (fg8/0.1.0 at 14a03486): what held, kept as checks. The parser question
cut to a line's indentation and first character answers as the whole line does; the length
of the line after a run of siblings does not change how many leave together; past the cap
only the two stated classes leave. Made-up notes."""

import random
import sys

import pytest
from markdown_it import MarkdownIt
from test_forget_cost_unit import older
from test_rt1_harness import OBSERVED, Note, Plan, Run, Step

import cairn.spans as spans
from cairn.spans import fences_reaching

PARSER = MarkdownIt("commonmark", {"html": False}).enable("table")


def kept_apart(before: str, texts: list[str]) -> bool:
    """fences_reaching's precondition: no code fence runs from one block into the next."""
    body = before + "\n\n".join(texts) + "\n"
    firsts, at = [], before.count("\n")
    for text in texts:
        firsts.append(at)
        at += text.count("\n") + 2
    for token in PARSER.parse(body):
        if token.type == "fence" and token.map:
            start, stop = token.map
            if start < firsts[0]:
                if stop > firsts[0]:
                    return False
                continue
            n = max(i for i, first in enumerate(firsts) if first <= start)
            if n + 1 < len(texts) and stop > firsts[n + 1] - 1:
                return False
    return True


def a_block(rng: random.Random) -> tuple[str, str, int, int]:
    indent = rng.choice(["", " ", "  ", "   ", "    ", "      ", "\t", " \t", "\t\t"])
    marker = rng.choice(["-", "*", "+", "1.", "2)", ">", "- >", "> -"])
    gap = rng.choice([" ", "  ", "   ", "    ", "\t"])
    char = rng.choice("`~")
    run = char * rng.choice([3, 3, 4, 5])
    info = rng.choice(["", "text", "py"])
    column = len((indent + marker + gap).expandtabs(4))
    under = " " * column if ">" not in marker else indent + "> "
    rows = [f"{indent}{marker}{gap}{run}{info}"]
    rows += [
        under + rng.choice(["code", "x" * rng.randint(1, 20)]) for _ in range(rng.randint(1, 3))
    ]
    if rng.random() < 0.15:
        rows.append(under + run)
    return "\n".join(rows), char, len(run), column


def a_line(rng: random.Random, char: str, run: int, column: int, width: int) -> str:
    """Lines only a full reading tells apart: closers of each character and length, a run of
    fence characters then text, tabs against spaces, a quotation marker, deep indentation."""
    pad = rng.choice([" " * rng.randint(0, 12), "\t" * rng.randint(0, 3), " \t", "\t "])
    line = rng.choice(
        [
            pad + char * rng.randint(run, run + 40),
            pad + char * rng.randint(1, run + 3) + " " + "t" * 40,
            pad + rng.choice("`~") * rng.randint(3, 60),
            pad + ">" + rng.choice(["", " "]) + "q" * 50,
            " " * rng.randint(0, 80) + "deep " * 10,
            pad + rng.choice(["- ", "1. ", "* "]) + "item " * 10,
            " " * column + rng.choice(["", " ", "\t"]) + char * rng.randint(run, run + 30),
        ]
    )
    if rng.random() < 0.5 and len(line) <= width:
        line += " " * (width - len(line) + rng.randint(1, 30))
    return line


@pytest.mark.parametrize("seed", [1, 2])
def test_the_cut_question_answers_as_the_whole_line_up_to_its_first_no(monkeypatch, seed):
    rng, asked, compared = random.Random(seed), spans.asked_line, 0
    for _ in range(4000):
        made = [a_block(rng) for _ in range(rng.randint(1, 4))]
        texts = [text for text, *_ in made]
        width = max(len(row) for text in texts for row in text.split("\n"))
        line = a_line(rng, *made[-1][1:], width)
        before = rng.choice(["", "- parent\n\n", "# Ops\n\n- parent\n\n", "> quote\n\n"])
        if not kept_apart(before, texts):
            continue
        monkeypatch.setattr(spans, "asked_line", lambda line, block: line)
        whole = fences_reaching(before, texts, line)
        monkeypatch.setattr(spans, "asked_line", asked)
        cut = fences_reaching(before, texts, line)
        first = whole.index(False) if False in whole else len(whole) - 1
        assert cut[: first + 1] == whole[: first + 1], (before, texts, line)
        compared += 1
    assert compared > 2000


SHAPES = [
    "      " + "tail " * 400,
    " " * 60 + "deep " * 400,
    "\t  " + "x" * 2000,
    "      " + "~" * 2000,
    "      ~~~ " + "t" * 2000,
    "      > " + "q" * 2000,
]


def siblings(count: int, line: str, storage: str) -> Plan:
    items = [f"    - ~~~\n      sample {n}" for n in range(count)]
    if storage == "one":
        notes = [*older(), Note("run", "\n\n".join(items), "Ops")]
    else:
        notes = older()
        for n in range(count):
            notes = [Note(f"s{n}", items[n], "Ops", inner=notes)]
    tail = [Note("sep", "Between.", "Ops"), Note("tail", line, "Ops")]
    return Plan([*notes, *tail], [Step("forget", "sep")])


@pytest.mark.parametrize("storage", ["one", "reverse"])
@pytest.mark.parametrize("line", SHAPES, ids=lambda line: repr(line[:10]))
def test_how_long_the_line_after_the_siblings_is_does_not_change_how_many_leave(storage, line):
    """The guide: "How long the line after such claims is does not change how many leave
    together." Short (cut to the siblings' width) and long, the same renders, the plain
    loop's claims."""
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20_000))
    renders = []
    for text in (line[: line.index(line.lstrip(" \t")) + 3], line):
        run = Run(siblings(20, text, storage)).run()
        try:
            assert [f for f in run.faults if f[1] not in OBSERVED] == []
            renders.append(run.stats[0]["renders"])
        finally:
            run.close()
    assert renders[0] == renders[1]


AFTER = [
    "      Text with `inline` code.",
    "```\nat the margin\n```",
    "      ``code`` and ~~strike~~",
]
BEFORE = ["    - ~~~\n      closed before\n      ~~~", "    - `inline` before"]


def test_past_the_cap_only_the_stated_classes_leave():
    """Forty siblings stored in reverse, spelling the topic two ways in turn under a heading
    that says one (the page that reaches the cap), with claims before the run that are
    closed or hold inline code, and claims after the tail that cannot open a fence: the
    forget renders RENDERS_MAX + 2 times, and every claim it takes that the plain loop keeps
    is of a stated class (test_rt1_harness.declared)."""
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20_000))
    inner = [*older(), *(Note(f"b{n}", text, "Ops") for n, text in enumerate(BEFORE))]
    for n in range(40):
        inner = [Note(f"s{n}", f"    - ~~~\n      sample {n}", ("Ops", "OPS")[n % 2], inner=inner)]
    plan = Plan(
        [
            *inner,
            Note("sep", "Between.", "Ops"),
            Note("tail", "      End.", "Ops"),
            Note("aside", "## Ops\n\nAn aside.", "Ops"),
            *(Note(f"a{n}", text, "Ops") for n, text in enumerate(AFTER)),
        ],
        [Step("forget", "sep")],
    )
    run = Run(plan).run()
    try:
        assert [f for f in run.faults if f[1] not in OBSERVED] == []
        assert (run.stats[0]["renders"], run.stats[0]["plain"]) == (26, 41)  # RENDERS_MAX 24
        kept = {claim.text for claim in run.store.get_page(run.pid, "ops").claims}
        assert set(AFTER) | set(BEFORE) <= kept
    finally:
        run.close()
