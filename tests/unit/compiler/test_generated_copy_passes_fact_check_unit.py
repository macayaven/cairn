"""A page that copies its passages word for word passes the fact check (third report, must-fix 3).

The property: a note filed through `Memory.note` with `lint_gate`, the gate `serve` builds, is
saved, and its page holds each content passage of the note as a claim, word for word, that
cites it. Grounding and retention can only refuse such a page by reading a fact one way in its
passage and another way where they compare it. The report's case: a URL the extractor ended
before `>`, whose final underscore was then read with nothing after it.

Five families of made-up notes, each case filed in a project of its own:
- `ends`, the whole product, nothing drawn: a URL that ends in a number, a version or a
  date, in angle brackets, in a link, in parentheses, in quotes, bare and before a tag,
  ending in or followed by nothing, each of `_ * ~ ` ) ] > . , ; : ! ?`, each pair of them
  and each run of three and four. The report's note is one of its cases.
- `starts`, the whole product: the other edge of the same fact, a URL whose scheme holds a
  version or an underscore, after each character that may stand before it.
- `kinds`, the whole product: a number, a version, a date, a flag, a path and inline code,
  each standing where the URL's number stood: after a character that may stand before it,
  before a run of underscores and a `>` or a `<`.
- `mixed`, seeded: URLs, versions, flags, paths, numbers, dates and inline code with emphasis
  around them, across one of their edges, or a backslash at an edge; in a paragraph, a list
  item, a quote, a table cell, a nested item, and as lines of a fenced block.
- `pairs`, seeded: two passages, a fact at the end of the first and an emphasis character at
  the start of the second, as one note of two paragraphs, as two notes on one topic, and as
  the two lines of one passage; and the first passage twice in one note.

A failure names case ids and violation codes, never a note's text: `cases()` builds the same
notes on every run. The report's note is also filed by name, alone and in the places a
passage can stand, and so is the simplest note of each other shape the families found.

The last tests are the sweep of the other places that read a piece of a passage: what a fact
keeps of its passage, a code block's lines, a claim read as a paragraph whatever its passage
was, the `lint` command's reading of a body, and the one thing the place of a fact still
changes, white space that ends inline code."""

import random
from collections import Counter

import pytest

from cairn.compiler import CONTENT_KINDS, CandidatePage, extractive, render_body
from cairn.facts import as_extracted, compared, extract_facts
from cairn.gate import lint_gate, strict_lint_gate, verbatim_gate
from cairn.lint import ClaimInput, PageInput, lint_pages
from cairn.memory import Memory
from cairn.spans import chunk
from cairn.store import Claim
from support.compiler.generated_copy_passes_fact_check import (
    BEFORE,
    CONTEXTS,
    EDGE,
    LITERAL,
    MIXED,
    NAMED,
    PAIRS,
    REVIEWED,
    ROWS,
    SCHEMES,
    SEED,
    TITLE,
    URLS,
    cases,
    content,
    facts_of_every_case,
    file,
    mixed,
    reads_differently_alone,
    refused,
)
from support.compiler.generated_copy_passes_fact_check import store as store

# A fact at the URL's end, after a parenthesis: a number, a version, a date.


def test_the_report_note_and_the_reviewer_s_six_are_cases_of_the_whole_product():
    made = cases()
    assert [LITERAL] in made["ends"].values()
    for text in REVIEWED:
        assert [text] in made["ends"].values(), REVIEWED.index(text)
    assert len(made["ends"]) == len(URLS) * len(ROWS) * (1 + 3 * len(EDGE) + len(EDGE) ** 2)
    assert len(made["starts"]) == len(SCHEMES) * len(BEFORE)


def test_every_context_holds_facts_and_most_pairs_are_two_passages():
    rng = random.Random(SEED)
    with_a_fact = Counter(
        context
        for context, text in (mixed(rng) for _ in range(MIXED))
        if any(extract_facts(span) for span in content(text))
    )
    assert all(with_a_fact[context] >= 20 for context in CONTEXTS), with_a_fact
    passages = Counter(
        (name[:-4], len(content(notes[0]))) for name, notes in cases()["pairs"].items()
    )
    assert passages["two-paragraphs", 2] > PAIRS * 0.9, passages
    assert passages["two-lines", 1] == PAIRS, passages


def test_each_family_holds_facts_that_read_differently_alone():
    """So the property is not kept by cases in which nothing could go wrong. These are the
    cases a reader of one value, out of its passage, gets wrong: at 23c24229 each of them was
    a note the gate refused, and no other case was."""
    alone = {
        family: sum(reads_differently_alone(notes) for notes in made.values())
        for family, made in cases().items()
    }
    assert alone["ends"] == 42 and alone["starts"] == 33, alone  # whole products
    assert alone["kinds"] == 0, alone  # a fact of another kind read the same alone
    assert alone["mixed"] >= 3 and alone["pairs"] >= 20, alone  # drawn


@pytest.mark.parametrize("family", ["ends", "starts", "kinds", "mixed", "pairs"])
def test_a_page_that_copies_its_passages_passes_the_fact_check(family):
    found = refused(cases()[family])
    assert found == {}, (len(found), found)


def test_the_report_note_is_saved_and_its_page_cites_it(store):
    project = store.create_project("report")
    answer = Memory(store, lint_gate).note("report", LITERAL, title=TITLE, topic=TITLE)
    assert [v.split(":")[0] for v in answer["violations"]] == []
    assert answer["status"] == "saved"
    (passage,) = store.note_spans(answer["note_id"])
    page = store.get_page(project.id, answer["page"])
    assert [(c.text, list(c.span_ids)) for c in page.claims] == [(LITERAL, [passage.id])]
    assert store.note_page(project.id, answer["note_id"]) == (False, page.slug)


@pytest.mark.parametrize("name", sorted(NAMED))
def test_the_report_note_elsewhere_and_the_other_shapes_are_saved_and_cited(store, name):
    assert file(Memory(store, lint_gate), store, "named", [NAMED[name]]) == []


@pytest.mark.parametrize("gate", [lint_gate, strict_lint_gate, verbatim_gate])
@pytest.mark.parametrize("text", [LITERAL, NAMED["a scheme"]])
def test_every_gate_that_checks_facts_passes_the_copy(gate, text):
    spans = {span.id: span for span in chunk("note", text)}
    claims = extractive(TITLE, list(spans.values())).claims
    body = render_body(TITLE, claims, spans)
    page = CandidatePage(slug="quill-notes", title=TITLE, body=body, claims=claims)
    assert [v.split(":")[0] for v in gate(page, spans).violations] == []


def test_a_claim_that_writes_those_underscores_as_asterisks_is_still_refused():
    """The other direction, for each case of the two products where the URL read differently
    alone: there the underscore is no emphasis, so a claim with an asterisk in its place
    changes the fact, and grounding or retention refuses it, as before the fix."""
    made = cases()
    notes = [
        n[0] for f in ("ends", "starts") for n in made[f].values() if reads_differently_alone(n)
    ]
    refusals = Counter()
    for text in notes:
        spans = {span.id: span for span in chunk("note", text)}
        claims = (Claim(id="cl_a", text=text.replace("_", "*"), span_ids=tuple(spans)),)
        body = render_body(TITLE, claims, spans)
        page = CandidatePage(slug="quill-notes", title=TITLE, body=body, claims=claims)
        codes = {v.split(":")[0] for v in lint_gate(page, spans).violations}
        refusals[
            bool(codes & {"grounding.fact_not_in_cited_passages", "retention.fact_dropped"})
        ] += 1
    assert refusals == Counter({True: 42 + 33})


def test_a_fact_keeps_its_own_characters_of_the_passage_as_read():
    for span, f in facts_of_every_case():
        assert (f.value, len(f.as_read)) == (span.text[f.start : f.end], len(f.value))
        changed = {(was, now) for was, now in zip(f.value, f.as_read, strict=True) if was != now}
        assert changed <= {("_", "*")}, (f.kind, changed)


def test_only_a_url_reads_differently_alone_and_no_line_of_a_code_block_does():
    """Reading a fact's text alone, as the gate did, against the same text read in its passage:
    of the kinds only a URL ever differs, so nothing else was refused or let through by it;
    and a code block's lines, which were read alone too, each read the same either way."""
    differ, lines = Counter(), Counter()
    for _, f in facts_of_every_case():
        if f.kind != "code_block":
            differ[f.kind] += as_extracted(f.value) != compared(f.as_read)
            continue
        pairs = zip(f.value.split("\n"), f.as_read.split("\n"), strict=True)
        for alone, read in pairs:
            lines["with an underscore"] += "_" in alone
            lines["that differ"] += as_extracted(alone) != compared(read)
    assert {kind for kind, n in differ.items() if n} == {"url"}, differ
    assert set(differ) == {"url", "number", "version", "date", "flag", "path", "inline_code"}
    assert lines["with an underscore"] > 100 and lines["that differ"] == 0, lines


@pytest.mark.parametrize("kind", sorted(CONTENT_KINDS))
def test_a_text_reads_the_same_whatever_kind_of_passage_it_is(kind):
    """lint reads a claim as a paragraph, whatever passage it copies."""
    for span, _ in facts_of_every_case()[::7]:
        other = span.model_copy(update={"kind": kind})
        assert extract_facts(other) == extract_facts(span)


def test_the_lint_command_finds_every_fact_of_passages_copied_into_a_body():
    """The `lint` command reads a page by its body, whole (not by its claims): each passage
    on lines of its own between blank lines reads there as it does alone."""
    found = Counter()
    for family, made in cases().items():
        for notes in made.values():
            spans = [span for n, text in enumerate(notes) for span in chunk(f"note-{n}", text)]
            copied = [span for span in spans if span.kind in CONTENT_KINDS]
            claims = [
                ClaimInput(id=f"c{n}", text=span.text, span_ids=[span.id])
                for n, span in enumerate(copied)
            ]
            body = "# Quill notes\n\n" + "\n\n".join(span.text for span in copied) + "\n"
            report = lint_pages([PageInput(slug="quill", text=body, claims=claims)], spans)
            found.update((family, v.code) for v in report.violations)
    assert found == Counter()


@pytest.mark.parametrize(
    ("passage", "claim"),
    [("Keep `a ` here.", "Keep `a` here."), ("Keep `a` here.", "Keep `a ` here.")],
)
def test_white_space_that_ends_inline_code_is_not_compared_by_the_fact_check(passage, claim):
    """A limit, as before this fix: the white-space rule (compared_text) is applied to the
    fact's own text, whose end it takes for a line's end. The fact check passes the changed
    claim; the verbatim gate, which compares whole texts, refuses it."""
    spans = {span.id: span for span in chunk("note", passage)}
    claims = (Claim(id="cl_000000000000000a", text=claim, span_ids=tuple(spans)),)
    page = CandidatePage(
        slug="q", title=TITLE, body=render_body(TITLE, claims, spans), claims=claims
    )
    assert lint_gate(page, spans).violations == []
    assert strict_lint_gate(page, spans).violations == []
    assert [v.split(":")[0] for v in verbatim_gate(page, spans).violations] == [
        "grounding.not_verbatim"
    ]
