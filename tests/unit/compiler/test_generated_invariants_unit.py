"""Two invariants over notes generated from a small grammar (second report, core4 item 3).

(a) Every page the compiler, a tidy or the arranger (a fake model) saves passes every gate,
its body is what render_body writes of its claims, no code fence in the parsed body runs
across two claims or a heading the body writes, and filing the same notes again changes
nothing, claim ids and the order of citations included. (b) Every such page exports and
imports, and the imported page is equal, claim ids and citation order included. Every claim is
the text of each passage it cites under the one rule (CORE5: only a CR before a line feed,
spaces and tabs at a line's end and blank lines around the text are ignored), checked by a
statement of that rule written here, not by the gate's function.

The same notes are also compiled by two composers of this test, standing in for what a model
may answer through LLMComposer (which only the evaluation script uses): one folds every
passage of one text into one claim (finding 5a), one reuses a passage, as a second claim with
the same text or with a space before it (the cross-reader's item 6). What either saves holds
both invariants; a draft the gate refuses saves nothing.

The grammar: repeated paragraphs, one a prefix of another and one joined from the first lines
of two earlier ones, quotes and lazy lines after quotes and lists, lists and nested lists with
varying indentation, fences with every closer shape, inline code with odd white space, links
with backticks in their destinations and titles, escaped backticks, setext headings, link
definitions, private-use and non-character code points, headings to depth eight, titles with
line breaks, no-break and other Unicode spaces. Seeded, so every run builds the same notes.
Made-up notes."""

import hashlib
import io
import json
import random

import pytest

from cairn.arrange import arrange_page, tidy_page
from cairn.compiler import (
    Draft,
    DraftStats,
    claim_id,
    compile_note,
)
from cairn.gate import lint_gate
from cairn.llm import FakeModel
from cairn.memory import Memory, RequestError
from cairn.store import Claim, Store
from support.compiler.generated_invariants import faults, note, reversed_order, title
from support.fuzz import fuzz_budget

pytestmark = pytest.mark.fuzz

SEED, CASES = 20261120, fuzz_budget(60, 300)
# The old probe character, other private-use and non-character code points.


def cases(seed: int, count: int):
    rng = random.Random(seed)
    for n in range(count):
        yield f"p{n:03d}", title(rng), [note(rng) for _ in range(rng.randint(1, 3))]


def snapshot(store: Store, project_id: str) -> dict:
    pages = {}
    for slug in sorted(p.slug for p in store.list_pages(project_id)):
        page = store.get_page(project_id, slug)
        claims = [(c.id, c.text, c.span_ids) for c in page.claims]
        pages[slug] = (page.title, page.body, claims, page.version)
    return pages


def folding(title: str, spans) -> Draft:
    """A composer as a model may answer: one claim for each text, citing every passage that
    says it, under whatever heading (the fold of finding 5a)."""
    by_text: dict[str, list] = {}
    for span in spans:
        by_text.setdefault(span.text, []).append(span)
    claims = tuple(
        Claim(id=claim_id(same[0].id), text=text, span_ids=tuple(s.id for s in same))
        for text, same in by_text.items()
    )
    return Draft(claims=claims, stats=DraftStats(composer="folding"))


def reusing(title: str, spans) -> Draft:
    """A composer as a model may answer, with LLMComposer's ids (a hash of text and citations,
    so a claim the same as an earlier one is dropped): each passage as a claim, and each text
    that several passages say once more, as a claim that cites them all (a reuse the gate
    and the import take). A note with no such text reuses its first passage instead, with a
    space before it (` X.` beside `X.`) or after it (`X. `, the whole passage, but past what
    the import takes of one version): the gate refuses the draft, and nothing is saved."""
    claims = {}

    def add(text: str, cited: tuple[str, ...]) -> None:
        made = "cl_" + hashlib.sha256(json.dumps([text, cited]).encode()).hexdigest()[:16]
        claims.setdefault(made, Claim(id=made, text=text, span_ids=cited))

    by_text: dict[str, list[str]] = {}
    for span in spans:
        add(span.text, (span.id,))
        by_text.setdefault(span.text, []).append(span.id)
    repeated = [(text, ids) for text, ids in by_text.items() if len(ids) > 1]
    for text, ids in repeated:
        add(text, tuple(ids))
    if not repeated:
        first = spans[0]
        add(f" {first.text}" if len(spans) % 2 else f"{first.text} ", (first.id,))
    return Draft(claims=tuple(claims.values()), stats=DraftStats(composer="reusing"))


def build(store: Store) -> dict[str, int]:
    """File every case's notes, check, tidy, check, arrange, check, file again; then compile
    the same notes in a project of their own with a composer that folds, and check. The
    counts of what happened, and an AssertionError naming the case at the first fault."""
    memory = Memory(store, lint_gate)
    counts = {"cases": 0, "notes": 0, "refused": 0, "pages": 0, "tidied": 0, "arranged": 0}
    counts |= {"composed": 0, "folds saved": 0, "arranger refused a fold": 0}
    counts |= {"reuses saved": 0, "reuses refused": 0}
    for name, page_title, notes in cases(SEED, CASES):
        project = store.create_project(name)
        counts["cases"] += 1
        filed = []
        for text in notes:
            counts["notes"] += 1
            try:
                answer = memory.note(name, text, title=page_title, topic=page_title)
                filed.append(text)
            except RequestError:
                counts["refused"] += 1
                continue
            # an extractive note is never refused by the gate (finding 1 was one)
            assert answer["status"] in ("saved", "unchanged"), (name, answer["violations"])
        assert faults(store, project.id) == [], (name, "compiled")
        for slug in [p.slug for p in store.list_pages(project.id)]:
            counts["pages"] += 1
            tidied = tidy_page(store, project.id, slug)
            assert tidied.status in ("arranged", "unchanged"), (name, tidied.problem)
            counts["tidied"] += tidied.status == "arranged"
            assert faults(store, project.id) == [], (name, "tidied")
            page = store.get_page(project.id, slug)
            reply = reversed_order(page)
            folds = json.loads(reply.text)["same"]
            arranged = arrange_page(store, project.id, slug, FakeModel([reply, reply]))
            if arranged.status in ("rejected", "invalid"):  # only a fold it must not make
                assert folds, (name, arranged.problem)
                counts["arranger refused a fold"] += 1
            else:
                assert arranged.status in ("arranged", "unchanged"), (name, arranged.problem)
            counts["arranged"] += arranged.status == "arranged"
            assert faults(store, project.id) == [], (name, "arranged")
        before = snapshot(store, project.id)
        for text in filed:
            memory.note(name, text, title=page_title, topic=page_title)
        assert snapshot(store, project.id) == before, (name, "filed again")
        composed = store.create_project(f"{name}-composed")
        for text in filed:
            note, _ = store.add_note(composed.id, text, title=page_title, topic=page_title)
            counts["composed"] += compile_note(store, note, compose=folding).status == "saved"
        assert faults(store, composed.id) == [], (name, "composed")
        counts["folds saved"] += sum(
            len(c.span_ids) > 1 for p in store.list_pages(composed.id) for c in p.claims
        )
        reused = store.create_project(f"{name}-reused")
        for text in filed:
            note, _ = store.add_note(reused.id, text, title=page_title, topic=page_title)
            result = compile_note(store, note, compose=reusing)
            assert result.status in ("saved", "unchanged", "rejected"), name
            counts["reuses saved"] += result.status == "saved"
            if result.status == "rejected":  # only a space around a reused passage
                codes = {v.split(":")[0] for v in result.gate.violations}
                assert codes <= {"grounding.not_verbatim", "grounding.passage_repeated"}, (
                    name,
                    codes,
                )
                counts["reuses refused"] += 1
        assert faults(store, reused.id) == [], (name, "reused")
    return counts


@pytest.fixture(scope="module")
def built_world(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("invariants") / "cairn.db")
    try:
        counts = build(store)
        yield store, counts
    finally:
        store.close()


def test_every_saved_page_passes_every_gate_and_filing_again_changes_nothing(built_world):
    _, counts = built_world
    assert counts["cases"] == CASES and counts["pages"] > CASES // 2, counts
    assert counts["tidied"] and counts["arranged"], counts  # both paths saved pages
    assert counts["composed"] and counts["folds saved"], counts
    assert counts["reuses saved"] and counts["reuses refused"], counts


def test_every_saved_page_exports_and_imports_equal(built_world, tmp_path):
    store, _ = built_world
    archive = io.BytesIO()
    store.export(archive)
    target = Store(tmp_path / "imported.db")
    try:
        target.import_export(io.BytesIO(archive.getvalue()))
        names = [
            n
            for name, _, _ in cases(SEED, CASES)
            for n in (name, f"{name}-composed", f"{name}-reused")
        ]
        for name in names:
            source, imported = store.get_project(name), target.get_project(name)
            mine = {s: p[:3] for s, p in snapshot(store, source.id).items()}
            theirs = {s: p[:3] for s, p in snapshot(target, imported.id).items()}
            assert theirs == mine, name
    finally:
        target.close()
