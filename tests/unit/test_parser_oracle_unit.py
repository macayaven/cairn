"""cairn.spans.code_spans against the Markdown parser's own placing (markdown_oracle).

The two share the parser and nothing else: cairn finds each line of a block's text at the end
of its source line; the oracle records where the parser's getLines took each character from.
Seeded texts from a small alphabet of Markdown's sharp edges (quotes, lists, headings, fences,
tables, raw HTML, tabs, CR, no-break spaces), the same every run."""

import random

import pytest
from markdown_oracle import code_places

from cairn.spans import code_spans

ATOMS = [
    "`", "``", "a", " ", "  ", "\t", "\n", "\n\n", "> ", ">", "- ", "  - ", "1. ", "# ", "|",
    "\\", "<b>", "<", "~~~", "```", "\r\n", "\r", "\u00a0", "x y", "    ", "---", "=",
]  # fmt: skip
# Where the oracle first found cairn's placing wrong or too cautious.
FOUND = {
    "code across a quote's lines": "> a `b\n> c` d",
    "code across a list item's lines": "- x\n  - Keep `a\n  b`",
    "a first line of no-break spaces the parser strips": "\n\n\u00a0\t\n```aa```x y\\    > \t",
    "a setext heading": "``\r\n<\t``<~~~\r- \t\n\n",
    "a split tab on a line with no code": "- x y`~~~`|\r\n\t<~~~  - ",
}


def places(text: str) -> set[int] | None:
    found = code_spans(text)
    return None if found is None else {n for left, right in found for n in range(left, right)}


@pytest.mark.parametrize("text", FOUND.values(), ids=list(FOUND))
def test_the_shapes_the_oracle_found(text):
    assert places(text) == code_places(text)


@pytest.mark.parametrize("seed", range(8))
def test_seeded_texts_place_code_as_the_parser_does(seed):
    rng = random.Random(seed)
    for _ in range(500):
        text = "".join(rng.choice(ATOMS) for _ in range(rng.randint(1, 14)))
        assert places(text) == code_places(text), repr(text)


def test_code_across_a_quotes_lines_leaves_the_markers_out():
    text = "> a `b\n>  c` d"
    assert [text[a:b] for a, b in code_spans(text)] == ["`b\n", " c`"]
