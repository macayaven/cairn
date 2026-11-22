"""Every line of a body the compiler writes is a claim's, a heading's or a fence's it wrote, or
blank (review blocker 1): the reverse of provenance.claim_not_in_body, run by every gate as
provenance.unclaimed_line (gate.unclaimed_line, by equality with compiler.render_body).

Over the repository's fixtures and seeded made-up notes: titles with and without line
breaks, notes filed in turn onto one page, deep headings, fences left open or closed too
deep; and the line it names when a sentence no claim holds is put in a body."""

import random

import pytest

from cairn.compiler import CONTENT_KINDS, CandidatePage, extractive, provenance_gate, render_body
from cairn.gate import lint_gate, unclaimed_line, verbatim_gate
from cairn.spans import chunk
from cairn.store import Claim
from support.paths import FIXTURES_ROOT

FIXTURES = FIXTURES_ROOT
NOTES = sorted(path.read_text() for path in FIXTURES.glob("*_synthetic.md"))
TITLES = [
    "Runbook",
    "Inspection\n\nThe inspection passed.",
    "Two\r\nlines\u2028and more",
    "## Not a heading",
    "",
]
SHAPES = [
    "~~~\nsample text\n    ~~~",
    "```python\nx = 1",
    "- Steps:\n\n  ```sh\n  make deploy",
    "".join(f"{'#' * level} H{level}\n\n" for level in range(1, 7)) + "Deep words.",
    "## A\n\n### B\n\nIn B.\n\n## C\n\nIn C.",
    "Plain words.\n\n> A quote.\n\n| a | b |\n|---|---|",
    "Keep `a  b.txt` here.",
]


def page_of(title: str, texts: list[str]) -> tuple[CandidatePage, dict]:
    spans = {}
    claims: list[Claim] = []
    for n, text in enumerate(texts):
        found = [s for s in chunk(f"cap_{n:012x}", text) if s.kind in CONTENT_KINDS]
        spans.update({s.id: s for s in chunk(f"cap_{n:012x}", text)})
        claims.extend(extractive(title, found).claims)
    body = render_body(title, claims, spans)
    return CandidatePage(slug="p", title=title, body=body, claims=tuple(claims)), spans


@pytest.mark.parametrize("title", TITLES)
def test_every_fixture_page_has_no_line_without_a_claim(title):
    for text in NOTES:
        page, spans = page_of(title, [text])
        assert unclaimed_line(page, spans) is None, (title, text[:40])


@pytest.mark.parametrize("seed", range(40))
def test_pages_of_several_notes_have_no_line_without_a_claim(seed):
    rng = random.Random(seed)
    texts = rng.sample(NOTES + SHAPES, rng.randint(2, 5))
    page, spans = page_of(rng.choice(TITLES), texts)
    assert unclaimed_line(page, spans) is None, texts
    for gate in (provenance_gate, lint_gate, verbatim_gate):
        assert not any("unclaimed_line" in v for v in gate(page, spans).violations)


def test_a_sentence_no_claim_holds_is_named_by_its_line():
    page, spans = page_of("Inspection", ["The inspection is pending."])
    added = page.body.replace("\n\n", "\n\nThe inspection passed.\n\n", 1)
    named = unclaimed_line(page.model_copy(update={"body": added}), spans)
    assert named == 3
    result = lint_gate(page.model_copy(update={"body": added}), spans)
    assert not result.passed
    assert result.violations == [
        "provenance.unclaimed_line: -: line 3 of the body is not in a claim, nor a heading or"
        " fence the body writes"
    ]


def test_the_title_written_as_given_is_refused():
    """The body render_body wrote before blocker 1: its title line is not the title."""
    title = "Inspection\n\nThe inspection passed."
    page, spans = page_of(title, ["The inspection is pending."])
    old = f"# {title}\n\nThe inspection is pending.\n"
    assert unclaimed_line(page.model_copy(update={"body": old}), spans) is not None


@pytest.mark.parametrize(
    "extra",
    ["## Invented", "```", "Words.", "# Another title"],
    ids=["heading-off-the-path", "fence-not-after-a-claim", "prose", "second-title"],
)
def test_lines_the_body_does_not_write_are_named(extra):
    page, spans = page_of("Runbook", ["## A\n\nIn A.", "Plain."])
    body = page.body.replace("\n\n", f"\n\n{extra}\n\n", 1)
    assert unclaimed_line(page.model_copy(update={"body": body}), spans) == 3
