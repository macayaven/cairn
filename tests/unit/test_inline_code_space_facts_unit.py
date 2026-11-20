"""The fact check reads white space inside inline code as the gate's text reading does: it is
the code's own, so two file names that differ by spaces inside backticks are two facts.

One finder of code spans (cairn.spans.code_spans) serves the gate's reading and lint's
normalize. It pairs backticks within a block, as CommonMark does, so a stray backtick in one
paragraph never moves the code of the next, and a fenced block is code lines, not a span."""

import random

import pytest

from cairn import facts, gate, spans
from cairn.compiler import CONTENT_KINDS, CandidatePage, extractive, render_body
from cairn.facts import extract_facts, normalize
from cairn.gate import lint_gate
from cairn.spans import Span, chunk
from cairn.store import Claim

SPACED = "Open `a  b.txt` first."
ONE_SPACE = "Open `a b.txt` first."


def passages(text: str) -> dict[str, Span]:
    return {s.id: s for s in chunk("cap_00000000000a", text)}


def verbatim(held: dict[str, Span]) -> list[Claim]:
    return [Claim(id=f"cl_{n}", text=s.text, span_ids=(s.id,)) for n, s in enumerate(held.values())]


def page(*claims: Claim) -> CandidatePage:
    body = "# Files\n\n" + "\n\n".join(c.text for c in claims) + "\n"
    return CandidatePage(slug="files", title="Files", body=body, claims=claims)


def test_a_claim_that_respaces_inline_code_is_refused_by_lint_gate():
    held = passages(SPACED)
    (span_id,) = held
    claim = Claim(id="cl_a", text=ONE_SPACE, span_ids=(span_id,))
    result = lint_gate(page(claim), held)
    assert not result.passed
    codes = [v.split(": ", 1)[0] for v in result.violations]
    assert "grounding.fact_not_in_cited_passages" in codes
    assert "retention.fact_dropped" in codes


def test_the_claim_that_keeps_the_code_as_written_passes_lint_gate():
    held = passages(SPACED)
    (span_id,) = held
    result = lint_gate(page(Claim(id="cl_a", text=SPACED, span_ids=(span_id,))), held)
    assert (result.passed, result.violations) == (True, [])


def test_two_names_that_differ_by_spaces_in_backticks_are_two_facts():
    (one,) = passages(SPACED).values()
    (two,) = passages(ONE_SPACE).values()
    norms = {f.norm for s in (one, two) for f in extract_facts(s) if f.kind == "inline_code"}
    assert norms == {"a  b.txt", "a b.txt"}


def test_normalize_keeps_code_white_space_and_collapses_the_rest():
    assert normalize("Open   `a  b.txt`\n now ") == "open `a  b.txt` now"
    assert normalize("No  code\there.") == "no code here."


def test_the_gate_and_the_fact_check_find_code_with_one_function():
    assert gate.code_spans is spans.code_spans
    assert facts.code_spans is spans.code_spans


def test_a_stray_backtick_in_one_paragraph_does_not_move_the_next_paragraphs_code():
    text = "It`s odd.\n\n" + SPACED
    assert "`a  b.txt`" in normalize(text)
    held = passages(text)
    result = lint_gate(page(*verbatim(held)), held)
    assert (result.passed, result.violations) == (True, [])


def test_code_spans_pair_backticks_within_a_block_only():
    text = "Intro.\n\na `b\n\nc d `e  f`"
    assert spans.code_spans(text) == [(text.index("`e"), len(text))]
    # A backtick left as text in the first paragraph, which a quote before the passage may
    # continue: the passage is read exactly (spans.code_spans gives None).
    assert spans.code_spans("a `b\n\nc d `e  f`") is None


def test_a_fenced_block_with_aligned_spaces_still_keeps_its_code_lines():
    text = "```\nx  = 1   # one\ny  = 22  # two\n```"
    held = passages(text)
    result = lint_gate(page(*verbatim(held)), held)
    assert (result.passed, result.violations) == (True, [])
    assert result.metrics["code_line_retention"] == 1.0


def test_a_body_after_a_tab_closer_reads_its_code_as_its_passages_do():
    # The renderer closes a fence Markdown leaves open after a tab then backticks; the fact
    # check reads the body's fences with Markdown's tab stops too, so what follows is prose.
    texts = ["```\nsample text\n\t```", SPACED, "```\nx  = 1   # one\n```", "Then `c  d` too."]
    held, claims = {}, []
    for n, text in enumerate(texts):
        found = chunk(f"cap_{n:012x}", text)
        held.update({s.id: s for s in found})
        claims.extend(extractive("Files", [s for s in found if s.kind in CONTENT_KINDS]).claims)
    body = render_body("Files", claims, held)
    assert "\t```\n```\n" in body  # the renderer's own closer
    result = lint_gate(CandidatePage(slug="f", title="Files", body=body, claims=claims), held)
    assert (result.passed, result.violations) == (True, [])


# Fences Markdown and the fact reader may read apart: open in a quote, opened on a list
# marker's line, indented four spaces (text to Markdown), left open, tildes. Whatever the
# shape, the note after it keeps its inline code on a page of verbatim claims.
SHAPES = [
    "```\nsample text\n\t```",
    "> ```\n> x = 1",
    "- ```sh\n  make deploy\n  ```",
    "- ```sh\n  make deploy",
    "Run:\n    ```\nthen stop.",
    "~~~\nsample text\n    ~~~",
    "```python\nx = 1",
    "```sh\necho `date  +%s`\n```",
    "- Steps:\n\n  ```sh\n  make  deploy\n  ```",
]


def verbatim_page(texts: list[str]) -> tuple[CandidatePage, dict[str, Span]]:
    held, claims = {}, []
    for n, text in enumerate(texts):
        found = chunk(f"cap_{n:012x}", text)
        held.update({s.id: s for s in found})
        claims.extend(extractive("Files", [s for s in found if s.kind in CONTENT_KINDS]).claims)
    body = render_body("Files", claims, held)
    return CandidatePage(slug="f", title="Files", body=body, claims=claims), held


@pytest.mark.parametrize("shape", SHAPES)
def test_a_note_after_any_fence_keeps_its_inline_code_on_the_page(shape):
    candidate, held = verbatim_page([shape, SPACED, "Then `c  d` too."])
    result = lint_gate(candidate, held)
    assert (result.passed, result.violations) == (True, [])


@pytest.mark.parametrize("seed", range(40))
def test_pages_of_notes_in_any_order_pass_the_fact_check(seed):
    rng = random.Random(seed)
    texts = rng.sample([*SHAPES, SPACED, ONE_SPACE, "Use `x\ty` and `p  q`."], rng.randint(2, 6))
    candidate, held = verbatim_page(texts)
    result = lint_gate(candidate, held)
    assert (result.passed, result.violations) == (True, []), texts
