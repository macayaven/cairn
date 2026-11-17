"""Acceptance tests for T4's coverage guard: strict_lint_gate fails a draft with an uncited span.

Contract (frozen; the maintainer decided on 2026-11-10 that the LLM path rejects uncited spans):
- `cairn.gate.strict_lint_gate(page, spans)` runs lint_gate's guards against every span it is
  given, cited or not. A content span that no claim cites fails with the spec's
  `coverage.passage_unaccounted`: one violation per span,
  "coverage.passage_unaccounted: -: <message>", and the message names the span's kind, note
  and lines. Heading spans need no claim.
- The compiler gives a gate the spans the page already cites plus the note's new content
  spans. Under strict_lint_gate a draft that leaves any new content span uncited is rejected:
  nothing is saved and the candidate is kept.
- A draft that cites every new span (a merged claim may cite several) is saved, and the strict
  gate's result equals lint_gate's.
- On an update the draft must cite the new note's spans. The page's existing claims already
  cite the old ones, and citing an old span instead of a new one does not count.
- The extractive default is untouched: compile_note composes extractively unless told
  otherwise, and the extractive draft passes strict_lint_gate on every synthetic fixture.
"""

import inspect
import json
from pathlib import Path

import pytest

from cairn.compiler import CONTENT_KINDS, compile_note, extractive
from cairn.gate import lint_gate, strict_lint_gate
from cairn.llm import FakeModel
from cairn.llm_compiler import LLMComposer
from cairn.store import Store

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
NOTE = """\
# Drift sync

Drift 1.8.2 mirrors two buckets; checked on 2026-05-04.

- **Run:** `drift sync --src ./a --dst ./b`.
- **Dry run:** add --dry-run to list changes first.
- **Limit:** at most 500 files per batch, about 2 GB.

```bash
drift sync --src ./a --dst ./b --dry-run
drift sync --src ./a --dst ./b
```

> Keep `--delete` off until the dry run is clean.
"""
UPDATE = "# Drift sync\n\n- **Retry:** pass `--retries 3` on flaky links.\n"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


def note_of(store, text, project="alpha"):
    b = store.get_project(project) or store.create_project(project)
    note, _ = store.add_note(b.id, text)
    return b, note, [s for s in store.note_spans(note.id) if s.kind in CONTENT_KINDS]


def model_writing(*claims) -> LLMComposer:
    """A composer whose model replies once, with these (text, cited spans) claims."""
    reply = {
        "claims": [{"text": text, "span_ids": [s.id for s in cited]} for text, cited in claims]
    }
    return LLMComposer(FakeModel([json.dumps(reply)]))


def synthesized(spans) -> list:
    """Reworded prose, two spans merged into one claim, every fact kept verbatim."""
    intro, run, dry, limit, code, quote = spans
    return [
        ("Drift 1.8.2 copies one bucket into another (verified 2026-05-04).", [intro]),
        (
            "Sync with `drift sync --src ./a --dst ./b`; pass --dry-run first to preview "
            "the changes.",
            [run, dry],
        ),
        ("Batches hold up to 500 files, roughly 2 GB.", [limit]),
        (
            "Preview, then apply:\n\n```bash\ndrift sync --src ./a --dst ./b --dry-run\n"
            "drift sync --src ./a --dst ./b\n```",
            [code],
        ),
        ("Leave `--delete` off until a dry run comes back clean.", [quote]),
    ]


def located(span, note) -> str:
    return f"{span.kind} passage at {note.id} lines {span.start_line}-{span.end_line}"


def codes(result) -> list[str]:
    return [v.split(": ", 1)[0] for v in result.gate.violations]


def test_a_draft_that_leaves_a_new_span_uncited_is_rejected_and_nothing_is_saved(store):
    project, note, spans = note_of(store, NOTE)
    claims = synthesized(spans)[:-1]  # no claim cites the quote
    result = compile_note(store, note, gate=strict_lint_gate, compose=model_writing(*claims))
    assert (result.status, result.version) == ("rejected", None)
    assert store.get_page(project.id, "drift-sync") is None
    assert codes(result) == ["coverage.passage_unaccounted"]
    assert result.gate.violations[0].startswith("coverage.passage_unaccounted: -: ")
    assert located(spans[-1], note) in result.gate.violations[0]
    assert [c.text for c in result.candidate.claims] == [text for text, _ in claims]


def test_each_uncited_span_is_its_own_violation(store):
    _, note, spans = note_of(store, NOTE)
    limit, quote = spans[3], spans[5]
    claims = [c for c in synthesized(spans) if c[1] not in ([limit], [quote])]
    result = compile_note(store, note, gate=strict_lint_gate, compose=model_writing(*claims))
    assert result.status == "rejected"
    assert codes(result) == ["coverage.passage_unaccounted"] * 2
    for span in (limit, quote):
        assert sum(located(span, note) in v for v in result.gate.violations) == 1


def test_a_draft_that_cites_every_new_span_is_saved_as_under_the_lint_gate(store):
    project, note, spans = note_of(store, NOTE)
    claims = synthesized(spans)
    result = compile_note(store, note, gate=strict_lint_gate, compose=model_writing(*claims))
    assert (result.status, result.version) == ("saved", 1), result.gate.violations
    offered = {s.id: s for s in spans}
    assert strict_lint_gate(result.candidate, offered) == lint_gate(result.candidate, offered)
    m = store.get_page(project.id, "drift-sync").metrics
    assert (m["claims"], m["spans_cited"], m["retention"], m["span_coverage"]) == (5, 6, 1.0, 1.0)

    every = {s.id: s for s in store.note_spans(note.id)}
    assert any(s.kind == "heading" for s in every.values())
    assert strict_lint_gate(result.candidate, every).passed  # a heading needs no claim


def test_an_update_must_cite_its_new_spans_and_an_old_span_does_not_count(store):
    project, first, old = note_of(store, NOTE)
    assert compile_note(store, first, gate=strict_lint_gate).status == "saved"
    _, second, (new,) = note_of(store, UPDATE)

    restated = ("Keep `--delete` off until the dry run is clean.", [old[-1]])
    result = compile_note(store, second, gate=strict_lint_gate, compose=model_writing(restated))
    assert result.status == "rejected"
    assert codes(result) == ["coverage.passage_unaccounted"]
    assert located(new, second) in result.gate.violations[0]
    assert store.page_versions(project.id, "drift-sync") == [1]

    retry = ("Pass `--retries 3` when links are flaky.", [new])
    result = compile_note(store, second, gate=strict_lint_gate, compose=model_writing(retry))
    assert (result.status, result.version) == ("saved", 2), result.gate.violations


def test_the_extractive_default_is_untouched_and_passes_the_strict_gate(store):
    assert inspect.signature(compile_note).parameters["compose"].default is extractive
    paths = sorted(FIXTURES.glob("*.md"))
    assert len(paths) >= 5
    for path in paths:
        project = store.create_project(path.stem.replace("_", "-"))
        note, _ = store.add_note(project.id, path.read_text(encoding="utf-8"))
        result = compile_note(store, note, gate=strict_lint_gate)
        assert result.status == "saved", (path.name, result.gate.violations)
