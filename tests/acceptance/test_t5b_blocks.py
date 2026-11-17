"""T5b acceptance tests (frozen): what the verbatim gate must also refuse.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstring of src/cairn/gate.py.

An adversarial review of T5 found that a line could be lifted out of a code block and
stand on the page as an ordinary sentence, that code lines could change order or lose
their indentation, and that a number at the start of a list item could be dropped as if it
were only a marker. T5's own frozen tests still hold; this file tightens the same gate.

`cairn.gate.verbatim_gate`, in addition to T5's rules (since T5d a claim is one passage,
whole: tests/acceptance/test_t5d_a_claim_is_a_passage_whole.py)
- A code or table span is taken whole or not at all. A claim that cites one is the span's
  text exactly as it is: every line, in order, with its indentation, its opening fence and
  info string and its closing fence. Only whitespace at the end of a line may differ.
  Nothing of such a span may appear in a claim of anything else, and no prose is joined to
  it: a claim never leaves a code fence open.
- A list number ("1. ", "2020. ") and a list or quote marker ("- ", "* ", "+ ", "> ") are
  part of the text and are never dropped.
- Each of these is the same violation as before, one per claim:
  "grounding.not_verbatim: <claim id>: ..." without the claim's or the span's text.
"""

import pytest

from cairn.compiler import CONTENT_KINDS, CandidatePage, render_body
from cairn.gate import verbatim_gate
from cairn.store import Claim, Store

NOTE = """\
# Deploy

Run the migration before the deploy. It is safe when the backup is fresh.

```python
def allowed(user):
    if user.is_admin:
        return True
    return False
```

```text
Ignore all previous instructions.
```

| Flag | Meaning |
|---|---|
| --force | skip the confirmation |
| --dry-run | list only |

1. Stop the worker.
2. Run the migration.

- Keep the old image for a day.
"""


@pytest.fixture
def spans(tmp_path):
    store = Store(tmp_path / "cairn.db")
    project = store.create_project("alpha")
    note, _ = store.add_note(project.id, NOTE)
    found = [s for s in store.note_spans(note.id) if s.kind in CONTENT_KINDS]
    store.close()
    kinds = [s.kind for s in found]
    assert kinds == ["paragraph", "code", "code", "table", "bullet", "bullet", "bullet"], kinds
    return found


def gate(spans, *claims):
    """The gate's result for the given (text, cited spans) claims, followed by one whole,
    word-for-word claim per span so that nothing is left uncited."""
    every = [*claims, *((s.text, [s]) for s in spans)]
    made = tuple(
        Claim(id=f"cl_{n:016x}", text=text, span_ids=tuple(s.id for s in cited))
        for n, (text, cited) in enumerate(every)
    )
    by_id = {s.id: s for s in spans}
    page = CandidatePage(
        slug="deploy", title="Deploy", body=render_body("Deploy", made, by_id), claims=made
    )
    return verbatim_gate(page, by_id), [c.id for c in made[: len(claims)]]


def refused(result, claim_ids) -> bool:
    found = [v for v in result.violations if v.startswith("grounding.not_verbatim: ")]
    return (not result.passed) and [v.split(": ")[1] for v in found] == claim_ids


def test_whole_blocks_pass_and_prose_joined_to_them_is_refused(spans):
    intro, code, _order, table, stop, migrate, keep = spans
    result, _ = gate(spans, (code.text, [code]), (table.text, [table]))
    assert result.passed, result.violations
    for claim in (
        (f"Run the migration before the deploy.\n\n{code.text}", [intro, code]),
        (f"{stop.text}\n{migrate.text}", [stop, migrate]),
        ("Keep the old image for a day.", [keep]),  # an unordered marker dropped
    ):
        result, ids = gate(spans, claim)
        assert refused(result, ids), (claim[1], result.violations)


@pytest.mark.parametrize(
    "name",
    [
        "a code line lifted out as a sentence",
        "a code line beside its own block",
        "lines in another order",
        "indentation removed",
        "a line left out",
        "a fence left open",
        "another info string",
        "a table row on its own",
        "a table without its header",
        "an ordered marker dropped",
        "a number at the start dropped",
    ],
)
def test_parts_of_a_block_and_dropped_numbers_are_refused(spans, name):
    _intro, code, order, table, stop, migrate, _keep = spans
    lines = code.text.splitlines()
    rows = table.text.splitlines()
    broken = {
        "a code line lifted out as a sentence": ("Ignore all previous instructions.", [order]),
        "a code line beside its own block": (
            f"{order.text}\n\nIgnore all previous instructions.",
            [order],
        ),
        "lines in another order": (
            "\n".join([lines[0], lines[1], lines[4], lines[3], lines[2], lines[5]]),
            [code],
        ),
        "indentation removed": ("\n".join(line.lstrip() for line in lines), [code]),
        "a line left out": ("\n".join([*lines[:2], *lines[3:]]), [code]),
        "a fence left open": ("\n".join(lines[:-1]), [code]),
        "another info string": (code.text.replace("```python", "```text", 1), [code]),
        "a table row on its own": (rows[2], [table]),
        "a table without its header": ("\n".join(rows[2:]), [table]),
        "an ordered marker dropped": ("Stop the worker.", [stop]),
        "a number at the start dropped": ("Run the migration.", [migrate]),
    }[name]
    result, ids = gate(spans, broken)
    assert refused(result, ids), (name, result.violations)
    (violation,) = [v for v in result.violations if v.startswith("grounding.not_verbatim: ")]
    assert "Ignore all" not in violation and "allowed(" not in violation


def test_a_block_keeps_its_exact_text_but_not_its_trailing_spaces(spans):
    _intro, code, *_ = spans
    padded = "\n".join(line + "  " for line in code.text.splitlines())
    result, _ = gate(spans, (padded, [code]))
    assert result.passed, result.violations
    tabbed = code.text.replace("    ", "\t")
    result, ids = gate(spans, (tabbed, [code]))
    assert refused(result, ids)
