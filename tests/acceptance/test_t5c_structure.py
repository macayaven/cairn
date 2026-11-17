"""T5c acceptance tests (frozen): the verbatim gate keeps a span's structure, not only its words.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstring of src/cairn/gate.py.

A second adversarial review passed claims that kept every word of their source and still
changed what the page says or how it renders: indented code read out as a sentence, a
sentence cut in the middle of inline code or a link, a table or a rule flattened onto one
line, a line break added or removed, and a claim that was nothing but a list marker. T5's
and T5b's frozen tests still hold; this file tightens the same gate once more.

`cairn.gate.verbatim_gate`, in addition to T5's and T5b's rules (since T5d a claim is one
passage, whole: tests/acceptance/test_t5d_a_claim_is_a_passage_whole.py)
- A prose span (paragraph, bullet, quote) that holds anything Markdown reads as structure
  is taken whole or not at all, exactly as a code block is: every line, in order, as it
  is. Structure means a line indented four columns or more, a table row, a rule or a
  heading underline (a line of only "-", "=", "*" or "_", three or more), a hard line
  break (two spaces or a backslash at the end of a line), a line that opens an HTML tag,
  and a link-reference or footnote definition.
- Any other prose span is taken whole too, its white space aside: a sentence cut from it,
  inside inline code or a link or anywhere else, is refused. And a claim's line never ends
  in a hard line break.
- A claim made of a line that is only a list or quote marker is refused.
- The work is linear in the text: a claim and a span of thousands of short sentences are
  checked in well under a second.
- Each refusal is the same violation as before, one per claim:
  "grounding.not_verbatim: <claim id>: ..." without the claim's or the span's text.
"""

import time

import pytest

from cairn.compiler import CONTENT_KINDS, CandidatePage, render_body
from cairn.gate import verbatim_gate
from cairn.store import Claim, Store


def content_spans(tmp_path, note: str) -> list:
    store = Store(tmp_path / "cairn.db")
    try:
        project = store.create_project("alpha")
        stored, _ = store.add_note(project.id, note)
        return [s for s in store.note_spans(stored.id) if s.kind in CONTENT_KINDS]
    finally:
        store.close()


def gate(spans, *claims):
    """The gate's result for the given (text, cited spans) claims, followed by one whole,
    word-for-word claim per span so that nothing is left uncited."""
    every = [*claims, *((s.text, [s]) for s in spans)]
    made = tuple(
        Claim(id=f"cl_{n:016x}", text=text, span_ids=tuple(s.id for s in cited))
        for n, (text, cited) in enumerate(every)
    )
    by_id = {s.id: s for s in spans}
    page = CandidatePage(slug="p", title="P", body=render_body("P", made, by_id), claims=made)
    return verbatim_gate(page, by_id)


def refused(result, count: int = 1) -> bool:
    found = [v for v in result.violations if v.startswith("grounding.not_verbatim: ")]
    return (not result.passed) and len(found) == count


BROKEN = {
    "indented code read out as a sentence": (
        "# P\n\n    dangerous()\n    safe().\n",
        "dangerous() safe().",
    ),
    "one indented line taken alone": ("# P\n\n    dangerous()\n    safe().\n", "safe()."),
    "a sentence cut inside inline code": ("# P\n\n`Danger. Safe.`\n", "`Danger."),
    "a sentence cut inside a link": (
        "# P\n\n[Danger. Safe.](https://example.com)\n",
        "[Danger.",
    ),
    "a sentence cut inside a link's address": (
        "# P\n\nSee [the runbook](https://example.test/a. b) now.\n",
        "See [the runbook](https://example.test/a.",
    ),
    "a heading underline flattened": ("# P\n\nIntro.\n\nTitle\n---\n", "Title ---"),
    "a rule flattened": ("# P\n\nBefore.\n---\nAfter.\n", "Before. --- After."),
    "a sentence taken from beside a rule": ("# P\n\nBefore.\n---\nAfter.\n", "After."),
    "a quoted table flattened": (
        "# P\n\n> | A | B |\n> |---|---|\n> | x | y |\n",
        "> | A | B | |---|---| | x | y |",
    ),
    "a quoted table row alone": ("# P\n\n> | A | B |\n> |---|---|\n> | x | y |\n", "> | x | y |"),
    "a hard line break removed": ("# P\n\nAlpha.  \nBeta.\n", "Alpha. Beta."),
    "a backslash line break removed": ("# P\n\nAlpha.\\\nBeta.\n", "Alpha. Beta."),
    "a hard line break added": ("# P\n\nAlpha. Beta.\n", "Alpha.  \nBeta."),
    "a quote marker alone": ("# P\n\n>\n> Hello.\n", ">"),
    "a list marker alone": ("# P\n\n- \n  Hello.\n", "-"),
    "html left open": ("# P\n\n<details>\nOpen. Closed.\n</details>\n", "Open."),
    "a link definition taken apart": (
        "# P\n\nSee [the docs][d]. Then stop.\n[d]: https://example.test/docs\n",
        "See [the docs][d].",
    ),
}


@pytest.mark.parametrize("name", list(BROKEN))
def test_a_claim_that_changes_a_spans_structure_is_refused(tmp_path, name):
    note, claim = BROKEN[name]
    spans = content_spans(tmp_path, note)
    assert gate(spans).passed, "the note's own spans, word for word, must pass"
    result = gate(spans, (claim, [spans[-1]]))
    assert refused(result), (name, result.violations)
    (violation,) = [v for v in result.violations if v.startswith("grounding.not_verbatim: ")]
    assert "Danger" not in violation and "dangerous" not in violation


def test_passages_with_inline_code_and_links_pass_whole_and_not_cut(tmp_path):
    note = (
        "# P\n\nRun `quill sync --src ./a`. See [the runbook](https://example.test/a.b) first.\n"
        "It prints e.g. `ok. done` when it ends.\n\n- Keep **--delete** off. Ask first.\n"
    )
    paragraph, bullet = content_spans(tmp_path, note)
    result = gate([paragraph, bullet], (paragraph.text, [paragraph]), (bullet.text, [bullet]))
    assert result.passed, result.violations
    for claim in (
        ("See [the runbook](https://example.test/a.b) first.", [paragraph]),
        ("Run `quill sync --src ./a`.\nIt prints e.g. `ok. done` when it ends.", [paragraph]),
        ("Ask first.", [bullet]),
    ):
        assert refused(gate([paragraph, bullet], claim)), claim[0]


def test_a_span_with_structure_passes_whole_and_exact(tmp_path):
    note = (
        "# P\n\nAlpha.  \nBeta.\n\n    dangerous()\n    safe().\n\n"
        "> | A | B |\n> |---|---|\n> | x | y |\n"
    )
    spans = content_spans(tmp_path, note)
    assert gate(spans, *((s.text, [s]) for s in spans)).passed
    for span in spans:
        if "\n" in span.text:
            first_line = span.text.splitlines()[0]
            assert refused(gate(spans, (first_line, [span]))), span.kind


def test_thousands_of_short_sentences_are_checked_quickly(tmp_path):
    text = " ".join(["Word."] * 8_000)
    (span,) = content_spans(tmp_path, f"# P\n\n{text}\n")
    started = time.perf_counter()
    result = gate([span], (text, [span]))
    elapsed = time.perf_counter() - started
    assert result.passed, result.violations[:2]
    assert elapsed < 5, f"took {elapsed:.1f} s: the check is not linear"
