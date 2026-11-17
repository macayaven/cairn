"""G1: spans.chunk returns exactly what it returned before its fence pattern was made linear.

tests/unit/reference/spans_before_g1.py holds spans.py byte for byte as it was at commit
e8b3f04 (under a three-line header). Each case chunks the same text with the reference and the
current module and asserts that the spans are equal, field by field and in order: kind, ids,
heading paths, line numbers and text. Objects of the two copies are different classes, so
their dumps are compared. When a case differs, the reference is right by definition: a span id
is a permanent citation.

Inputs: every string literal of the test suite, the fixtures and the gold file; hostile shapes
around fences (a long line of backticks among them) at sizes the old code finishes quickly;
seeded generated Markdown with fences of every length, open and closed, nested in list items
and block quotes, with tildes and with info strings (the same every run).
"""

import ast
import importlib.util
import random
import sys
from pathlib import Path

import pytest

from cairn import spans as new_spans

TESTS = Path(__file__).resolve().parents[1]
REFERENCE = Path(__file__).resolve().parent / "reference" / "spans_before_g1.py"


def _load():
    spec = importlib.util.spec_from_file_location("g1_reference_spans", REFERENCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


old_spans = _load()
COUNT = {"texts": 0, "spans": 0}  # cases compared, printed by the last test


def same(text: str, source_id: str = "src") -> None:
    COUNT["texts"] += 1
    old = [s.model_dump() for s in old_spans.chunk(source_id, text)]
    new = [s.model_dump() for s in new_spans.chunk(source_id, text)]
    COUNT["spans"] += len(new)
    assert new == old, text[:300]


# The test suite's own strings, the fixtures and the gold file


def literals() -> list[str]:
    """Every string literal of every test module, the reference copies left out."""
    out = set()
    for path in sorted(TESTS.rglob("*.py")):
        if "reference" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.add(node.value)
    return sorted(out)


def test_every_string_literal_of_the_test_suite_chunks_the_same():
    texts = literals()
    assert len(texts) > 10_000  # the whole suite, not a corner of it
    for text in texts:
        same(text)


def test_the_fixtures_and_the_gold_file_chunk_the_same():
    paths = sorted((TESTS / "fixtures").rglob("*")) + sorted((TESTS / "gold").rglob("*"))
    files = [p for p in paths if p.is_file()]
    assert files
    for path in files:
        text = path.read_text(encoding="utf-8")
        same(text, path.stem)
        for line in text.splitlines():  # each line alone, the gold file's notes among them
            same(line)


# Hostile shapes, kept to sizes the old code finishes quickly


def tick_runs(n: int) -> str:
    return "a".join("`" * r for r in range(1, n // 8 + 2))


HOSTILE = {
    "backtick_line": lambda n: "`" * n,
    "backtick_line_then_one": lambda n: "`" * n + " x`",
    "backtick_line_then_fence": lambda n: "`" * n + " x`\n```\ncode\n```",
    "backtick_line_in_item": lambda n: "- " + "`" * n + " x`\n  more",
    "backtick_line_in_quote": lambda n: "> " + "`" * n + " x`",
    "backtick_line_indented": lambda n: "   " + "`" * n + "`x",
    "backtick_lines": lambda n: "\n".join("`" * (3 + i % 9) + " a`" * (i % 3) for i in range(n)),
    "tilde_line": lambda n: "~" * n + " x~`",
    "runs_of_every_length": tick_runs,
    "closers_of_every_length": lambda n: "\n".join(
        ["`" * 5] + ["`" * (i % 7) for i in range(n)] + ["`" * 6]
    ),
    "unclosed_fences": lambda n: "\n\n".join(f"{'`' * (3 + i % 4)}info{i}\nx" for i in range(n)),
    "fences_in_items": lambda n: "\n".join(
        f"{'  ' * (i % 3)}- {'`' * (3 + i % 2)}\n{'  ' * (i % 3)}  y{i}" for i in range(n)
    ),
    "headings_with_spaces": lambda n: "# a" + " " * n + "b",
    "heading_with_hashes": lambda n: "## a " + "# " * n + "#",
}


@pytest.mark.parametrize("shape", sorted(HOSTILE))
def test_the_hostile_shapes_chunk_the_same(shape):
    for n in (0, 1, 2, 3, 4, 7, 40, 200, 2_000):
        text = HOSTILE[shape](n)
        same(text)
        same("# Notes\n\n" + text + "\n\nAfter.\n")


# Seeded generated Markdown


def fence_line(rng: random.Random) -> str:
    """An opening or closing fence, or something close to one."""
    char = rng.choice("``````~~~")
    run = char * rng.choice([1, 2, 3, 3, 3, 4, 4, 5, 6, 8, 12, rng.randint(3, 40)])
    indent = " " * rng.choice([0, 0, 0, 1, 2, 3, 4, 5, 6])
    info = rng.choice(["", "", "", "sh", " python", "a`b", "`", "~", " x ", "``` x", "\t"])
    return indent + run + info + rng.choice(["", "", " ", "  ", "\t"])


def prose(rng: random.Random) -> str:
    words = ["the", "run", "`a`", "``b``", "x", "1.", "-", "#", "##", "|", ">", "~", "*"]
    words += ["`", "``", "```", "~~~", "\t", "a#", "# #"]
    return " ".join(rng.choice(words) for _ in range(rng.randint(0, 8)))


def item_marker(rng: random.Random) -> str:
    return rng.choice(["-", "*", "+", "1.", "2)", "10.", "123456789."]) + rng.choice(
        [" ", "  ", "   ", "\t", "     "]
    )


def gen_block(rng: random.Random, depth: int = 0) -> str:
    kind = rng.randrange(12)
    if kind <= 3:  # a fenced block, closed or not, with a body that may hold fence-like lines
        opener = fence_line(rng)
        body = [
            rng.choice([prose(rng), fence_line(rng), "", "  " + prose(rng)])
            for _ in range(rng.randint(0, 5))
        ]
        closer = rng.choice([[], [opener.strip()], [fence_line(rng)], [opener.split()[0] * 2]])
        return "\n".join([opener, *body, *closer])
    if kind == 4 and depth < 3:  # a list item holding a fence, maybe nested, maybe lazy
        marker = item_marker(rng)
        lead = " " * rng.choice([0, 0, 1, 2, 3, 4])
        column = len(lead) + len(marker)
        inner = gen_block(rng, depth + 1).split("\n")
        indent = " " * rng.choice([column, column, column - 1, column + 2, 0])
        first = lead + marker + rng.choice([inner[0], fence_line(rng), prose(rng)])
        rest = [(indent + line) if line and rng.random() < 0.8 else line for line in inner[1:]]
        if rng.random() < 0.3:
            rest.insert(rng.randint(0, len(rest)), "")
        return "\n".join([first, *rest])
    if kind == 5:  # a block quote with a fence in it
        inner = gen_block(rng, depth + 1).split("\n")
        return "\n".join(rng.choice([">", "> ", ">  ", " > "]) + line for line in inner)
    if kind == 6:
        return "#" * rng.randint(1, 7) + rng.choice([" ", "\t", ""]) + prose(rng)
    if kind == 7:
        return "\n".join(f"| {prose(rng)} |" for _ in range(rng.randint(1, 3)))
    if kind == 8:
        return "---\n" + prose(rng) + "\n---"
    return "\n".join(prose(rng) for _ in range(rng.randint(1, 3)))


def gen_document(rng: random.Random) -> str:
    seps = ["\n", "\n\n", "\n\n", "\n\n\n", "\n \n"]
    blocks = [gen_block(rng) for _ in range(rng.randint(0, 8))]
    text = "".join(block + rng.choice(seps) for block in blocks)
    return rng.choice(["", "---\ntitle: x\n---\n"]) + text


def test_generated_markdown_with_fences_chunks_the_same():
    rng = random.Random(20261114)
    for _ in range(6_000):
        same(gen_document(rng))


def test_every_short_string_of_fence_characters_chunks_the_same():
    """Every string of up to six characters over a backtick, a tilde, a space, a newline, a
    list marker and a letter: every way a fence can open, close or fail to within a few."""
    alphabet = "`~ \n-x"
    strings = [""]
    for _ in range(6):
        strings = [s + c for s in strings for c in alphabet]
        for text in strings:
            same(text)


def test_every_short_string_of_heading_characters_chunks_the_same():
    """Every string of up to seven characters over "#", a space, a tab, a letter and a newline:
    every way a heading's "#" run, its blanks, its text and a closing run can meet. (G1 also
    took the heading pattern's lazy title out of spans.py: the cost test says why.)"""
    alphabet = "# \tx\n"
    strings = [""]
    for _ in range(7):
        strings = [s + c for s in strings for c in alphabet]
        for text in strings:
            same(text)
            same(" " + text)


def test_zz_the_count_of_cases_compared():
    """Runs last in this file; the count goes in the G1 report."""
    print(f"\nG1 spans differential cases: {COUNT['texts']} texts, {COUNT['spans']} spans")
    assert COUNT["texts"] > 50_000 or COUNT["texts"] == 0  # 0 when run alone with -k
