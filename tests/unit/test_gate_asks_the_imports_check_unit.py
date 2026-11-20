"""What a gate accepts, an import accepts, because every gate asks the import's own check of
a version (third report, must-fix 1, and the readers after it; CORE6A).

A composer handed to `compile_note(..., compose=...)` could return a whole-passage claim
with an id cairn does not make ("claim-1", "", capitals), one claim twice, a passage cited
twice or three copies of one passage: each was saved, and the export then failed its own
import. The gates no longer learn the import's rules one by one. store.portable.version_faults
is the one check of a version's claims: an import raises from it, a forget drops claims by
it, and every gate refuses a candidate it names a claim of (cairn.gate.importable), with the
import's reason in a violation "import.<rule>".

Here, for every reason that check can give (store.portable.Refusal), the smallest draft a
composer can return that meets it: the compile is "rejected" and saves no version, every
gate refuses the candidate, and an import refuses the same claims in the same words. The
table must hold every Refusal, and the check's source is walked so that it can give no reason
that is not one: a rule added to the import fails this file until it has a row.

Two sides of two parameters. `legacy`: a fold whole only by the comparison before CORE5
passes for a version that exists (an import, a forget) and never for one a gate judges.
`reworded`: lint_gate, strict_lint_gate and provenance_gate take a claim in other words than
its passage (T4, T5d: frozen), so they ask every rule but that one, and an import refuses
what they save for it, as store.portable says; verbatim_gate, which every composer meets
unless a caller names another, asks them all.

The composer interface exists for the evaluation script alone; `serve` compiles with the
extractive composer. Made-up notes."""

import ast
import inspect
import textwrap

import pytest

import cairn.gate
from cairn.candidate import GateResult
from cairn.compiler import (
    CandidatePage,
    Draft,
    DraftStats,
    compile_note,
    extractive,
    provenance_gate,
    render_body,
)
from cairn.gate import importable, lint_gate, strict_lint_gate, verbatim_gate
from cairn.spans import FenceLeftOpen, Span
from cairn.store import Store, portable
from cairn.store.portable import Refusal, _claim_text, _page_version, version_faults

GATES = (provenance_gate, lint_gate, strict_lint_gate, verbatim_gate)
REWORDED = (provenance_gate, lint_gate, strict_lint_gate)  # they take a claim in other words
SAME = "Ada keeps port 80 open."
FIVE = "\n\n".join([SAME] * 5)
UNDER_TWO = f"## Ports\n\n{SAME}\n\n## Firewall\n\n{SAME}"
OPEN = "```\nNever closed"


def numbered(claims, start=1):
    return tuple(c.model_copy(update={"id": f"cl_{n:016x}"}) for n, c in enumerate(claims, start))


# Each reason: (the note's text, what a composer makes of the extractive claims of its
# passages; `foreign` is a passage of another project that says the same).
CASES = {
    Refusal.ID_SHAPE: [
        (SAME, lambda c, foreign, bad=bad: (c[0].model_copy(update={"id": bad}),))
        for bad in ("claim-1", "", "cl_ABCDEF0123456789")
    ],
    Refusal.CITED_TWICE: [
        (SAME, lambda c, foreign: (c[0].model_copy(update={"span_ids": c[0].span_ids * 2}),))
    ],
    Refusal.NOT_OF_PROJECT: [
        (SAME, lambda c, foreign: (c[0].model_copy(update={"span_ids": (foreign.id,)}),))
    ],
    Refusal.NOT_WHOLE: [
        (SAME, lambda c, foreign: (c[0].model_copy(update={"text": "Ada keeps port 80."}),))
    ],
    Refusal.FENCE: [(OPEN, lambda c, foreign: c)],  # no such text is known: stood in for below
    Refusal.HEADINGS: [
        (
            UNDER_TWO,
            lambda c, foreign: (
                c[0].model_copy(update={"span_ids": (*c[0].span_ids, *c[1].span_ids)}),
            ),
        )
    ],
    Refusal.ID_TWICE: [(SAME, lambda c, foreign: (c[0], c[0]))],
    Refusal.CITATIONS: [
        (
            FIVE,
            lambda c, foreign: numbered(
                c[0].model_copy(update={"span_ids": tuple(i for x in c for i in x.span_ids)})
                for _ in c
            ),
        )
    ],
    Refusal.TEXT: [(SAME, lambda c, foreign: numbered([c[0]] * 3))],
}
ROWS = [(refusal, n) for refusal, cases in CASES.items() for n in range(len(cases))]


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


@pytest.fixture(autouse=True)
def a_fence_no_closer_closes(monkeypatch):
    """The parser's answer for OPEN, stood in for (as test_fence_question_unit does): no
    text has been found that leaves a fence no closer closes."""

    def unclosable(text: str) -> str | None:
        if "Never closed" in text:
            raise FenceLeftOpen
        return None

    monkeypatch.setattr(cairn.gate, "open_fence_closer", unclosable)
    monkeypatch.setattr(cairn.compiler, "open_fence_closer", unclosable)


def drafted(store: Store, refusal: Refusal, n: int):
    """The case's note in project `alpha`, and the composer that returns its draft."""
    text, shape = CASES[refusal][n]
    project = store.create_project("alpha")
    other, _ = store.add_note(store.create_project("beta").id, SAME, title="Net")
    foreign = store.note_spans(other.id)[0]
    note, _ = store.add_note(project.id, text, title="Net")

    def compose(title, spans) -> Draft:
        claims = tuple(shape(extractive(title, spans).claims, foreign))
        return Draft(claims=claims, stats=DraftStats(composer="made up"))

    return project, note, compose


def candidate(store: Store, refusal: Refusal, n: int) -> tuple[CandidatePage, dict[str, Span]]:
    """The page compile_note would gate for the case, and the passages it gates with."""
    _, note, compose = drafted(store, refusal, n)
    spans = {s.id: s for s in store.note_spans(note.id) if s.kind != "heading"}
    claims = compose("Net", list(spans.values())).claims
    body = render_body("Net", claims, spans)
    return CandidatePage(slug="net", title="Net", body=body, claims=claims), spans


def test_the_table_has_a_row_for_every_reason_the_import_can_give():
    assert set(CASES) == set(Refusal)


def test_the_check_can_give_no_reason_that_is_not_a_refusal():
    """The source of the check, walked: every `yield` gives a Refusal, named there or held in
    `fault`, which is only ever assigned one; and together they name every Refusal, so a
    reason written into the check has a member, and a member has a row above."""
    named = set()
    for function in (version_faults, portable._past_bounds):
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        given = [node.value.elts[1] for node in ast.walk(tree) if isinstance(node, ast.Yield)]
        given += [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "fault"
        ]
        assert given, function.__name__
        for value in given:
            if isinstance(value, ast.Name):
                assert value.id == "fault", ast.unparse(value)
                continue
            assert isinstance(value, ast.Attribute), ast.unparse(value)
            assert ast.unparse(value.value) == "Refusal", ast.unparse(value)
            named.add(value.attr)
    assert named == {refusal.name for refusal in Refusal}


@pytest.mark.parametrize(("refusal", "n"), ROWS, ids=lambda value: getattr(value, "name", value))
def test_a_composers_draft_an_import_would_refuse_saves_no_version(store, refusal, n):
    project, note, compose = drafted(store, refusal, n)
    result = compile_note(store, note, compose=compose)
    assert result.status == "rejected"
    assert result.gate is not None and not result.gate.passed and result.gate.violations
    assert store.list_pages(project.id) == []
    assert all("port 80" not in violation for violation in result.gate.violations)


@pytest.mark.parametrize(("refusal", "n"), ROWS, ids=lambda value: getattr(value, "name", value))
def test_the_case_meets_that_reason_and_an_import_says_it(store, refusal, n):
    """The row is a row of its reason: the check names it first, for a version to be made
    and for one that exists, and an import of the same claims is refused in its words."""
    page, spans = candidate(store, refusal, n)
    for legacy in (False, True):
        faults = list(version_faults(page.claims, spans, legacy=legacy))
        assert faults and faults[0][1] is refusal, faults
    line = {
        "slug": "net",
        "version": 1,
        "title": "Net",
        "body": page.body,
        "claims": [
            {"id": c.id, "text": c.text, "passage_ids": list(c.span_ids)} for c in page.claims
        ],
        "metrics": {},
        "created_at": "2026-11-20T00:00:00+00:00",
    }
    with pytest.raises(ValueError) as refused:
        _claim_text(_page_version("projects/alpha/", line, {}, spans), spans, "projects/alpha/")
    place = faults[0][0]
    assert str(refused.value) == refusal.said("net", 1, "projects/alpha/", place, page.claims)
    assert "port 80" not in str(refused.value)


@pytest.mark.parametrize("gate", GATES, ids=lambda gate: gate.__name__)
@pytest.mark.parametrize(("refusal", "n"), ROWS, ids=lambda value: getattr(value, "name", value))
def test_every_gate_refuses_what_the_import_refuses(store, refusal, n, gate):
    page, spans = candidate(store, refusal, n)
    verdict = gate(page, spans)
    if refusal is Refusal.NOT_WHOLE and gate in REWORDED:
        # the one rule these three do not ask: their own rules judge a claim in other words
        assert not any(v.startswith("import.") for v in verdict.violations)
        return
    assert not verdict.passed and verdict.violations
    assert all("port 80" not in violation for violation in verdict.violations)
    codes = {violation.split(":")[0] for violation in verdict.violations}
    if any(code.startswith("import.") for code in codes):  # no rule of the gate's own met it
        assert codes == {f"import.{refusal.name.lower()}"}
        assert all(
            ": -: an import would refuse the page 'net' for claim " in v for v in verdict.violations
        )


@pytest.mark.parametrize(
    "refusal", [Refusal.ID_SHAPE, Refusal.CITED_TWICE, Refusal.ID_TWICE, Refusal.TEXT]
)
def test_the_readers_cases_are_refused_by_the_imports_reason_alone(store, refusal):
    """The four the readers found saved: no rule of verbatim_gate's own meets an id not of
    cairn's shape, an id held twice or a passage cited twice, so the violation is the
    import's; three copies of a passage its own count meets first."""
    page, spans = candidate(store, refusal, 0)
    codes = [violation.split(":")[0] for violation in verbatim_gate(page, spans).violations]
    own = {Refusal.TEXT: "grounding.passage_repeated"}
    assert set(codes) == {own.get(refusal, f"import.{refusal.name.lower()}")}
    assert set(v.split(":")[0] for v in provenance_gate(page, spans).violations) == {
        f"import.{refusal.name.lower()}"
    }


def test_a_page_the_gate_fails_keeps_the_gates_own_violations_alone(store):
    """As compiler._offered_only: T4's frozen tests pin a rejection's exact violations."""
    page, spans = candidate(store, Refusal.NOT_WHOLE, 0)
    bad = page.model_copy(update={"claims": (page.claims[0].model_copy(update={"id": "x"}),)})
    codes = [violation.split(":")[0] for violation in verbatim_gate(bad, spans).violations]
    assert codes == ["grounding.not_verbatim"]  # and nothing of the id cairn does not make


# The legacy comparison: for a version that exists, never for one a gate judges

OLD = ("Larch  grows.", "Larch grows.")  # one text before CORE5, two today


def old_fold(store: Store) -> tuple[CandidatePage, dict[str, Span]]:
    project = store.create_project("alpha")
    note, _ = store.add_note(project.id, "\n\n".join(OLD), title="Net")
    first, second = store.note_spans(note.id)
    (claim,) = extractive("Net", [first]).claims
    fold = claim.model_copy(update={"span_ids": (first.id, second.id)})
    spans = {first.id: first, second.id: second}
    body = render_body("Net", [fold], spans)
    return CandidatePage(slug="net", title="Net", body=body, claims=(fold,)), spans


def test_an_old_fold_passes_for_a_version_that_exists_and_never_for_one_to_be_made(store):
    page, spans = old_fold(store)
    assert list(version_faults(page.claims, spans, legacy=True)) == []  # an import, a forget
    assert list(version_faults(page.claims, spans, legacy=False)) == [(1, Refusal.NOT_WHOLE)]
    line = {
        "slug": "net",
        "version": 1,
        "title": "Net",
        "body": page.body,
        "claims": [
            {"id": c.id, "text": c.text, "passage_ids": list(c.span_ids)} for c in page.claims
        ],
        "metrics": {},
        "created_at": "2026-11-20T00:00:00+00:00",
    }
    assert _page_version("projects/alpha/", line, {}, spans).claims == page.claims  # imported
    passed = GateResult(passed=True, metrics={})
    asked = importable(passed, page, spans)  # what a gate asks of a version to be made
    assert [violation.split(":")[0] for violation in asked.violations] == ["import.not_whole"]
    assert not asked.passed and not verbatim_gate(page, spans).passed
    assert importable(passed, page, spans, reworded=True) is passed  # the three that take words
