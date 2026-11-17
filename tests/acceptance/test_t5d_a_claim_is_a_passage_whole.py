"""T5d acceptance tests: a claim is a passage, whole.

Written by the implementer before the change, for the maintainer to review and freeze when it
is merged. The contract goes in the docstrings of src/cairn/gate.py and src/cairn/llm_compiler.py.

Why: cairn promises that a claim on a page says what the passage it cites says. The verbatim
gate let a model select whole sentences of a passage, so it passed this draft:

    passage:  "The synthetic release is safe. Only after a reviewer approves it."
    claim:    "The synthetic release is safe."     (citing that passage)

That is a claim with no condition and a valid citation, cut from the condition that makes it
true. No rule about sentences can know which neighbour a sentence needs. The rule that can be
checked is the one the rest of cairn keeps already: the extractive composer writes one claim
per passage, the passage's text; the arranger and tidy_page only reorder claims and fold claims
that have the same words; an import refuses a claim that is not the text of a passage it cites.
This file makes the gate for a model's drafts keep it too.

`cairn.gate.verbatim_gate`
- It is strict_lint_gate plus one rule: a claim is one passage, whole. Its text is the text of
  every passage it cites: nothing of a passage left out, nothing added, and two passages never
  joined into one claim. A claim that cites several passages is a fold: each of them says the
  same, in the same words.
- How text is compared, nowhere looser than before:
  - A code or table passage, and a prose passage that holds structure (a hard line break, a
    rule, a line indented four columns, a table row, a code fence, HTML, a link or footnote
    definition, a list marker alone on its line), line for line, exactly. Only white space at
    the end of a line may differ, and only for code and tables; blank lines before and after
    the claim's text are not read.
  - Any other prose passage (a paragraph, a bullet, a quote): the same characters, with a run
    of white space (space, tab, CR, LF and nothing else) read as one space and white space at
    the two ends dropped, so a hard-wrapped passage may be wrapped elsewhere. There is no case
    folding and no Unicode normalisation: a no-break space, a zero-width character or a
    bidirectional control is text. A list or quote marker ("- ", "* ", "+ ", ">") is part of
    the passage's text: it is neither dropped nor added, and a line of the claim starts with
    one where a line of the passage does and nowhere else; a line that is markers alone (a
    quote's blank line) stays a line of its own. A list number ("1. ") is text like any other
    word. The claim adds no structure: no hard line break, rule or indented code.
- A refusal is one violation per claim, "grounding.not_verbatim: <claim id>: ...". It says in
  plain words that a claim must be the whole of each passage it cites, and names the passage
  it is not (or a line number), never a claim's or a passage's text. The violations follow
  strict_lint_gate's, and the metrics are strict_lint_gate's. A cited id the gate was not given
  is the strict gate's to report (provenance.unknown_passage); a claim that cites no passage
  it was given is refused, without repeating the id.
- What it still cannot do: it keeps a passage whole; it does not keep two passages together.
  A condition the author put in the next passage is a claim of its own: the strict gate's
  coverage rule puts it on the page, and the arrangement decides where.

The compiler, and what served pages need
- `compile_note` with a model's composer and no gate named judges the draft with
  verbatim_gate: a draft that cuts or joins passages is "rejected" and saves nothing; a draft
  that copies each passage whole, or folds passages that say the same, is saved.
- `LLMComposer` drops only the blank lines before and after a claim's text, which the gate
  does not read, so a passage that begins with indented code comes back from a model whole.
- What served pages are made of still passes the gate: the extractive draft of every fixture,
  and `arrange_page` and `tidy_page` folding repeats of every kind of passage.
"""

import json
from pathlib import Path

import pytest

from cairn.arrange import arrange_page, tidy_page
from cairn.compiler import CONTENT_KINDS, CandidatePage, compile_note, extractive, render_body
from cairn.gate import lint_gate, strict_lint_gate, verbatim_gate
from cairn.llm import FakeModel
from cairn.llm_compiler import LLMComposer
from cairn.spans import chunk
from cairn.store import Claim, Store

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
NOT_VERBATIM = "grounding.not_verbatim: "
NOTE = """\
# Release

The synthetic release is safe. Only after a reviewer approves it.

The mirror is copied every night. Deleting the mirror is safe when
the dry run is clean. Ask the owner first.

- Deleting the mirror is safe.
  - Only after a clean dry run.
  - Never on a Friday.

- Keep the old image for a day.

1. Stop the worker.

> Keep the mirror off.
> Turn it on after a clean dry run.
> Ask the owner first.

```bash
drift sync --src ./a --dst ./b --dry-run
drift sync --src ./a --dst ./b
```

| Flag | Meaning |
|---|---|
| --dry-run | list only |

Alpha holds.  \nBeta follows.

Before the rule.
***
After the rule.

    dangerous()
    safe().

> | A | B |
> |---|---|
> | x | y |
"""
KINDS = {
    "the reproduction": "paragraph",
    "a hard-wrapped paragraph": "paragraph",
    "a bullet with nested items": "bullet",
    "a bullet": "bullet",
    "a numbered item": "bullet",
    "a quote": "quote",
    "a code block": "code",
    "a table": "table",
    "a hard line break": "paragraph",
    "a rule": "paragraph",
    "indented code": "paragraph",
    "a quoted table": "quote",
}


def passages(text: str = NOTE, source: str = "cap_t5d_one") -> list:
    return [s for s in chunk(source, text) if s.kind in CONTENT_KINDS]


@pytest.fixture
def every():
    found = passages()
    assert [s.kind for s in found] == list(KINDS.values())
    return dict(zip(KINDS, found, strict=True))


def judged(*claims):
    """verbatim_gate's and strict_lint_gate's results for (text, cited passages) claims on one
    page, judged against the passages they cite, and the claims as made."""
    spans = {s.id: s for _, cited in claims for s in cited}
    made = tuple(
        Claim(id=f"cl_{n:016x}", text=text, span_ids=tuple(s.id for s in cited))
        for n, (text, cited) in enumerate(claims)
    )
    page = CandidatePage(
        slug="release", title="Release", body=render_body("Release", made, spans), claims=made
    )
    return verbatim_gate(page, spans), strict_lint_gate(page, spans), made


def assert_passes(*claims):
    result, strict, _ = judged(*claims)
    assert result.passed, result.violations
    assert result == strict


def assert_refused(text: str, cited: list, named, *, alone: bool = True) -> None:
    """The claim is refused in one grounding.not_verbatim violation that names `named`, the
    passage it is not the whole of, and repeats none of its text or the passages'. With
    `alone`, the strict gate passes it, so the refusal is this rule's and nothing else's."""
    result, strict, (claim,) = judged((text, cited))
    assert not result.passed
    assert result.metrics == strict.metrics
    assert result.violations[: len(strict.violations)] == strict.violations
    if alone:
        assert strict.passed, strict.violations
    (violation,) = [v for v in result.violations if v.startswith(NOT_VERBATIM)]
    assert violation.startswith(f"{NOT_VERBATIM}{claim.id}: "), violation
    said = violation.removeprefix(f"{NOT_VERBATIM}{claim.id}: ")
    assert named.id in said and "whole" in said, said
    for line in (*text.splitlines(), *(s.text for s in cited)):
        for part in line.split(". "):
            if len(part.strip()) > 12:
                assert part.strip() not in said, said


# The reproduction


def test_the_reproduction_is_refused_and_the_whole_passage_passes():
    source = "The synthetic release is safe. Only after a reviewer approves it."
    (span,) = [s for s in chunk("cap_synthetic", source) if s.kind == "paragraph"]
    assert_refused("The synthetic release is safe.", [span], span)
    assert_passes((source, [span]))


# A claim that is its passage, whole


@pytest.mark.parametrize("name", list(KINDS))
def test_a_claim_that_is_its_passage_whole_passes(every, name):
    assert_passes((every[name].text, [every[name]]))


def test_the_extractive_draft_of_the_note_passes_and_equals_the_strict_gate():
    spans = passages()
    claims = extractive("Release", spans).claims
    by_id = {s.id: s for s in spans}
    page = CandidatePage(
        slug="release", title="Release", body=render_body("Release", claims, by_id), claims=claims
    )
    result = verbatim_gate(page, by_id)
    assert result.passed, result.violations
    assert result == strict_lint_gate(page, by_id)


def test_a_fold_of_two_notes_that_say_the_same_passes():
    (one,) = passages("# Release\n\nDeleting the mirror is safe when\nthe dry run is clean.\n")
    (other,) = passages(
        "# Release\n\nDeleting the mirror is safe when the dry run is clean.\n", "cap_t5d_two"
    )
    assert_passes((one.text, [one, other]))
    assert_passes((other.text, [one, other]))
    code = "```bash\ndrift sync --src ./a --dst ./b\n```"
    (a,) = passages(f"# Release\n\n{code}\n")
    (b,) = passages(f"# Release\n\n{code}\n", "cap_t5d_two")
    assert_passes((code, [a, b]))


# Each way of cutting a passage


CUTS = [
    "a first sentence",
    "a last sentence",
    "a middle sentence",
    "two sentences of three",
    "one list item of a passage that holds several",
    "a list item without its nested items",
    "a quote without its last line",
]


@pytest.mark.parametrize("name", CUTS)
def test_each_way_of_cutting_a_passage_is_refused(every, name):
    wrapped = every["a hard-wrapped paragraph"]
    nested = every["a bullet with nested items"]
    quote = every["a quote"]
    text, span = {
        "a first sentence": ("The mirror is copied every night.", wrapped),
        "a last sentence": ("Ask the owner first.", wrapped),
        "a middle sentence": ("Deleting the mirror is safe when\nthe dry run is clean.", wrapped),
        "two sentences of three": (
            "The mirror is copied every night. Deleting the mirror is safe when\n"
            "the dry run is clean.",
            wrapped,
        ),
        "one list item of a passage that holds several": (
            "- Deleting the mirror is safe.",
            nested,
        ),
        "a list item without its nested items": ("Deleting the mirror is safe.", nested),
        "a quote without its last line": (
            "> Keep the mirror off.\n> Turn it on after a clean dry run.",
            quote,
        ),
    }[name]
    assert_refused(text, [span], span)


def test_a_fold_where_one_passage_says_more_is_refused():
    """A claim that cites a passage for one sentence of it: the second note's passage is the
    claim and more, so citing it would say that it says only this."""
    (short,) = passages("# Release\n\nDeleting the mirror is safe.\n")
    (longer,) = passages(
        "# Release\n\nDeleting the mirror is safe. Only after a clean dry run.\n", "cap_t5d_two"
    )
    assert_refused("Deleting the mirror is safe.", [short, longer], longer)


# Each way of joining passages


JOINS = [
    "two whole passages",
    "two whole passages on one line",
    "a passage and a sentence of another",
    "a sentence of each of two passages",
    "a bullet and a paragraph",
    "prose and a code block",
]


@pytest.mark.parametrize("name", JOINS)
def test_each_way_of_joining_passages_is_refused(every, name):
    reproduction = every["the reproduction"]
    wrapped = every["a hard-wrapped paragraph"]
    bullet = every["a bullet"]
    code = every["a code block"]
    text, cited = {
        "two whole passages": (f"{reproduction.text}\n\n{wrapped.text}", [reproduction, wrapped]),
        "two whole passages on one line": (
            f"{reproduction.text} {wrapped.text}",
            [reproduction, wrapped],
        ),
        "a passage and a sentence of another": (
            f"{reproduction.text}\nAsk the owner first.",
            [reproduction, wrapped],
        ),
        "a sentence of each of two passages": (
            "The synthetic release is safe. Ask the owner first.",
            [reproduction, wrapped],
        ),
        "a bullet and a paragraph": (
            f"{bullet.text}\n\n{reproduction.text}",
            [bullet, reproduction],
        ),
        "prose and a code block": (f"{reproduction.text}\n\n{code.text}", [reproduction, code]),
    }[name]
    assert_refused(text, cited, cited[0])


# Markers


MARKERS = [
    "a list marker dropped",
    "a list marker added",
    "a list marker changed",
    "a nested item's markers dropped",
    "a nested item joined to its parent's line",
    "a list number dropped",
    "a list number changed",
    "a quote's markers dropped",
    "a quote rewrapped, its markers moved",
    "a quote's lines joined, its markers inside the line",
    "a quote marker added",
    "a quote marker moved to a line of its own",
    "a quote's blank line dropped",
    "a line broken before a dash, which then reads as a marker",
]


@pytest.mark.parametrize("name", MARKERS)
def test_a_marker_dropped_added_moved_or_changed_is_refused(every, name):
    bullet = every["a bullet"]
    nested = every["a bullet with nested items"]
    numbered = every["a numbered item"]
    quote = every["a quote"]
    reproduction = every["the reproduction"]
    (dash,) = passages("# Release\n\nThe mirror is safe - after a clean dry run.\n")
    (one_line,) = passages("# Release\n\n> Keep the mirror off until the dry run is clean.\n")
    (blank,) = passages("# Release\n\n> Keep the mirror off.\n>\n> Ask the owner first.\n")
    text, span = {
        "a list marker dropped": ("Keep the old image for a day.", bullet),
        "a list marker added": (f"- {reproduction.text}", reproduction),
        "a list marker changed": ("* Keep the old image for a day.", bullet),
        "a nested item's markers dropped": (
            "- Deleting the mirror is safe.\n  Only after a clean dry run.\n  Never on a Friday.",
            nested,
        ),
        "a nested item joined to its parent's line": (
            "- Deleting the mirror is safe. - Only after a clean dry run.\n  - Never on a Friday.",
            nested,
        ),
        "a list number dropped": ("Stop the worker.", numbered),
        "a list number changed": ("2. Stop the worker.", numbered),
        "a quote's markers dropped": (
            "Keep the mirror off.\nTurn it on after a clean dry run.\nAsk the owner first.",
            quote,
        ),
        "a quote rewrapped, its markers moved": (
            "> Keep the mirror off. Turn it on\n> after a clean dry run. Ask the owner first.",
            quote,
        ),
        "a quote's lines joined, its markers inside the line": (
            "> Keep the mirror off. > Turn it on after a clean dry run. > Ask the owner first.",
            quote,
        ),
        "a quote marker added": (f"> {reproduction.text}", reproduction),
        # Alone on its line, ">" is a quote's blank line, and the next line is not quoted.
        "a quote marker moved to a line of its own": (
            ">\nKeep the mirror off until the dry run is clean.",
            one_line,
        ),
        "a quote's blank line dropped": ("> Keep the mirror off.\n> Ask the owner first.", blank),
        "a line broken before a dash, which then reads as a marker": (
            "The mirror is safe\n- after a clean dry run.",
            dash,
        ),
    }[name]
    # A list number is a fact the strict gate reads, so dropping or changing one fails it too.
    assert_refused(text, [span], span, alone=span is not numbered)


# White space


def test_rewrapping_a_prose_passage_passes(every):
    reproduction = every["the reproduction"]
    wrapped = every["a hard-wrapped paragraph"]
    nested = every["a bullet with nested items"]
    quote = every["a quote"]
    assert_passes(
        ("The synthetic release is safe.\nOnly after a reviewer approves it.", [reproduction]),
        (
            "The mirror is copied every night.\tDeleting the mirror\r\nis safe when the  dry run"
            " is clean.\nAsk the owner first.",
            [wrapped],
        ),
        (
            "- Deleting the mirror\n  is safe.\n  - Only after a clean\n    dry run.\n  - Never on"
            " a Friday.",
            [nested],
        ),
        (f"\n\n  {quote.text}\n", [quote]),
    )


SPACES = {
    "no-break space": chr(0x00A0),
    "narrow no-break space": chr(0x202F),
    "zero-width space": chr(0x200B),
    "zero-width joiner": chr(0x200D),
    "line separator": chr(0x2028),
    "ideographic space": chr(0x3000),
    "vertical tab": chr(0x000B),
    "form feed": chr(0x000C),
}
RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)


@pytest.mark.parametrize("character", SPACES.values(), ids=SPACES)
def test_a_character_in_place_of_a_space_is_refused(every, character):
    reproduction = every["the reproduction"]
    text = reproduction.text.replace("release is", f"release{character}is")
    assert_refused(text, [reproduction], reproduction)


def test_a_bidirectional_control_is_text(every):
    reproduction = every["the reproduction"]
    assert_refused(RIGHT_TO_LEFT_OVERRIDE + reproduction.text, [reproduction], reproduction)


# Code, tables and structured prose, as before


def test_code_tables_and_structured_prose_whole_pass_with_end_of_line_space_aside(every):
    code = every["a code block"]
    table = every["a table"]
    padded = "\n".join(f"{line} \t" for line in code.text.split("\n"))
    assert_passes((padded, [code]), (f"\n{table.text}\n\n", [table]))
    for name in ("a hard line break", "a rule", "indented code", "a quoted table"):
        assert_passes((f"\n{every[name].text}\n", [every[name]]))


LINE_FOR_LINE = [
    "a code line left out",
    "code lines in another order",
    "code indented",
    "code indented with a tab",
    "a table row alone",
    "a table without its header",
    "a hard line break removed",
    "a hard line break's spaces changed",
    "a rule flattened",
    "a sentence beside a rule",
    "indented code read out",
    "a quoted table without its markers",
    "a hard line break added",
    "a rule added",
]


@pytest.mark.parametrize("name", LINE_FOR_LINE)
def test_code_tables_and_structured_prose_are_taken_line_for_line(every, name):
    code = every["a code block"]
    table = every["a table"]
    lines = code.text.split("\n")
    rows = table.text.split("\n")
    text, span = {
        "a code line left out": ("\n".join([*lines[:1], *lines[2:]]), code),
        "code lines in another order": ("\n".join([lines[0], lines[2], lines[1], lines[3]]), code),
        "code indented": ("\n".join(f"  {line}" for line in lines), code),
        "code indented with a tab": ("\n".join(f"\t{line}" for line in lines), code),
        "a table row alone": (rows[2], table),
        "a table without its header": ("\n".join(rows[1:]), table),
        "a hard line break removed": ("Alpha holds. Beta follows.", every["a hard line break"]),
        "a hard line break's spaces changed": (
            "Alpha holds.   \nBeta follows.",
            every["a hard line break"],
        ),
        "a rule flattened": ("Before the rule. *** After the rule.", every["a rule"]),
        "a sentence beside a rule": ("After the rule.", every["a rule"]),
        "indented code read out": ("dangerous() safe().", every["indented code"]),
        "a quoted table without its markers": (
            "| A | B |\n|---|---|\n| x | y |",
            every["a quoted table"],
        ),
        "a hard line break added": (
            "The synthetic release is safe.  \nOnly after a reviewer approves it.",
            every["the reproduction"],
        ),
        "a rule added": (
            "The synthetic release is safe.\n***\nOnly after a reviewer approves it.",
            every["the reproduction"],
        ),
    }[name]
    result, strict, (claim,) = judged((text, [span]))
    assert not result.passed
    assert result.violations[: len(strict.violations)] == strict.violations
    (violation,) = [v for v in result.violations if v.startswith(NOT_VERBATIM)]
    assert violation.startswith(f"{NOT_VERBATIM}{claim.id}: ")
    assert "dangerous" not in violation and "Alpha" not in violation and "drift" not in violation


def test_one_violation_per_claim_in_the_order_of_the_claims(every):
    reproduction = every["the reproduction"]
    bullet = every["a bullet"]
    result, strict, made = judged(
        ("The synthetic release is safe.", [reproduction]),
        (bullet.text, [bullet]),
        ("Keep the old image for a day.", [bullet]),
    )
    assert strict.passed and not result.passed
    assert [v.split(": ")[1] for v in result.violations] == [made[0].id, made[2].id]


def test_an_id_the_gate_was_not_given_is_the_strict_gates_to_report(every):
    reproduction = every["the reproduction"]
    made_up = "sp_made_up_by_a_model"
    made = (
        Claim(id=f"cl_{0:016x}", text=reproduction.text, span_ids=(reproduction.id, made_up)),
        Claim(id=f"cl_{1:016x}", text=reproduction.text, span_ids=(made_up,)),
    )
    page = CandidatePage(
        slug="release", title="Release", body=f"# Release\n\n{reproduction.text}\n", claims=made
    )
    spans = {reproduction.id: reproduction}
    result, strict = verbatim_gate(page, spans), strict_lint_gate(page, spans)
    assert any(v.startswith("provenance.unknown_passage: ") for v in strict.violations)
    (refusal,) = [v for v in result.violations if v.startswith(NOT_VERBATIM)]
    assert refusal.startswith(f"{NOT_VERBATIM}{made[1].id}: ") and made_up not in refusal


# Through compile_note, with a stand-in model


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def note_of(store, text, project="alpha"):
    found = store.get_project(project) or store.create_project(project)
    note, _ = store.add_note(found.id, text)
    return found, note, [s for s in store.note_spans(note.id) if s.kind in CONTENT_KINDS]


def reply(*claims) -> str:
    return json.dumps(
        {"claims": [{"text": text, "span_ids": [s.id for s in cited]} for text, cited in claims]}
    )


def compiled(store, note, *claims):
    return compile_note(store, note, compose=LLMComposer(FakeModel([reply(*claims)])))


def test_a_model_draft_that_cuts_or_joins_is_rejected_and_saves_nothing(store):
    project, note, spans = note_of(store, NOTE)
    reproduction, wrapped, *_ = spans
    whole = [(s.text, [s]) for s in spans]

    cut = compiled(store, note, ("The synthetic release is safe.", [reproduction]), *whole[1:])
    assert cut.status == "rejected"
    (violation,) = [v for v in cut.gate.violations if v.startswith(NOT_VERBATIM)]
    assert reproduction.id in violation and "synthetic" not in violation

    joined = compiled(
        store, note, (f"{reproduction.text}\n\n{wrapped.text}", [reproduction, wrapped]), *whole[2:]
    )
    assert joined.status == "rejected"
    assert [v for v in joined.gate.violations if v.startswith(NOT_VERBATIM)]
    assert store.get_page(project.id, "release") is None


def test_a_model_draft_that_copies_each_passage_whole_is_saved(store):
    # Among NOTE's passages is one that begins with indented code: the composer keeps the
    # white space a passage starts with, and drops only blank lines around a claim's text.
    project, note, spans = note_of(store, NOTE)
    assert any(s.text.startswith("    ") for s in spans)
    reproduction, *rest = spans
    rewrapped = reproduction.text.replace("safe. ", "safe.\n")
    result = compiled(
        store, note, (rewrapped, [reproduction]), *((f"\n{s.text}\n\n", [s]) for s in rest)
    )
    assert result.status == "saved", result.gate.violations
    page = store.get_page(project.id, "release")
    assert [c.span_ids for c in page.claims] == [(s.id,) for s in spans]
    assert [c.text for c in page.claims[1:]] == [s.text for s in rest]


def test_a_model_that_copies_every_passage_of_every_fixture_whole_is_saved(store):
    for path in sorted(FIXTURES.glob("*_synthetic.md")):
        _, note, spans = note_of(store, path.read_text(encoding="utf-8"), project=path.stem)
        result = compiled(store, note, *((s.text, [s]) for s in spans))
        assert result.status == "saved", (path.name, result.gate.violations)


def test_a_note_that_says_one_thing_twice_may_fold_it_into_one_claim(store):
    text = (
        "# Release\n\nKeep the old image for a day.\n\n## Again\n\nKeep the old image for a day.\n"
    )
    project, note, (first, second) = note_of(store, text)
    result = compiled(store, note, (first.text, [first, second]))
    assert result.status == "saved", result.gate.violations
    (claim,) = store.get_page(project.id, "release").claims
    assert claim.span_ids == (first.id, second.id)


# What served pages need


def test_every_fixture_compiles_through_the_gate_with_the_extractive_composer(store):
    for path in sorted(FIXTURES.glob("*_synthetic.md")):
        _, note, _ = note_of(store, path.read_text(encoding="utf-8"), project=path.stem)
        result = compile_note(store, note, gate=verbatim_gate)
        assert result.status == "saved", (path.name, result.gate.violations)
        cited = store.get_spans([i for c in result.candidate.claims for i in c.span_ids])
        assert result.gate == strict_lint_gate(result.candidate, cited), path.name


# NOTE again, as a second note might say it: one paragraph wrapped elsewhere, white space at
# the end of a code line and of a table row.
REWRAPPED = (
    NOTE.replace("safe when\nthe dry run", "safe\nwhen the dry run")
    .replace("--dry-run\n", "--dry-run  \n")
    .replace("| --dry-run | list only |", "| --dry-run | list only |\t")
)


def page_with_repeats(store, project):
    """A page of NOTE's claims followed by REWRAPPED's: each of its claims says the same as one
    of NOTE's, in the same words."""
    found = store.create_project(project)
    for text in (NOTE, REWRAPPED):
        note, _ = store.add_note(found.id, text)
        assert compile_note(store, note, gate=lint_gate).status == "saved"
    page = store.get_page(found.id, "release")
    assert len(page.claims) == 2 * len(KINDS)
    return found, page


def test_tidy_page_folds_repeats_of_every_kind_of_passage(store):
    project, page = page_with_repeats(store, "tidied")
    result = tidy_page(store, project.id, "release")
    assert (result.status, result.merged) == ("arranged", len(KINDS)), result.problem
    after = store.get_page(project.id, "release")
    assert [c.text for c in after.claims] == [c.text for c in page.claims[: len(KINDS)]]
    assert all(len(c.span_ids) == 2 for c in after.claims)


def test_arrange_page_folds_repeats_of_every_kind_of_passage(store):
    project, page = page_with_repeats(store, "arranged")
    n = len(KINDS)
    answer = {"order": list(range(1, n + 1)), "same": [[k, k + n] for k in range(1, n + 1)]}
    result = arrange_page(store, project.id, "release", FakeModel([json.dumps(answer)]))
    assert (result.status, result.merged) == ("arranged", n), result.problem
    after = store.get_page(project.id, "release")
    assert [c.text for c in after.claims] == [c.text for c in page.claims[:n]]
    assert all(len(c.span_ids) == 2 for c in after.claims)
