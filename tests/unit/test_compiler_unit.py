"""Unit tests for the baseline compiler: the inner TDD loop under the W1 acceptance tests."""

import threading

import pytest

from cairn.compiler import (
    CONTENT_KINDS,
    CandidatePage,
    Draft,
    DraftStats,
    GateResult,
    candidate_page,
    claim_id,
    compile_note,
    extractive,
    provenance_gate,
    render_body,
    slugify,
)
from cairn.spans import Span
from cairn.store import Claim, Page, Store


def span(span_id: str, path: tuple[str, ...], text: str, kind: str = "paragraph") -> Span:
    return Span(
        id=span_id,
        source_id="cap_000000000000",
        kind=kind,
        heading_path=path,
        start_line=1,
        end_line=1,
        text=text,
    )


def claim(s: Span) -> Claim:
    return Claim(id=claim_id(s.id), text=s.text, span_ids=(s.id,))


def page_of(claims: list[Claim], body: str | None = None) -> CandidatePage:
    body = render_body("T", claims, {}) if body is None else body
    return CandidatePage(slug="t", title="T", body=body, claims=tuple(claims))


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("", "untitled"),
        ("ﬁle ﬂow", "file-flow"),  # NFKD splits ligatures into ASCII letters
        ("a" * 64, "a" * 64),  # exactly at the limit: kept whole
        ("a" * 100, "a" * 64),  # no "-" to cut at: hard cut
        ("a" * 64 + "-b", "a" * 64),  # a "-" at index 64 is a boundary
        ("a" * 65 + "-b", "a" * 64),  # the only "-" is past the limit: hard cut
        ("ab-" + "c" * 100, "ab"),  # the last boundary at or before 64 wins, however early
    ],
)
def test_slugify_edges(text, slug):
    assert slugify(text) == slug


def test_claim_id_reuses_the_span_ids_hex_part():
    assert claim_id("sp_0123456789abcdef") == "cl_0123456789abcdef"


def test_body_of_a_page_without_claims_is_its_title():
    assert render_body("T", [], {}) == "# T\n"


def test_body_adds_a_heading_where_the_heading_path_changes():
    spans = [
        span("sp_1", ("T",), "intro"),
        span("sp_2", ("T", "A"), "a one"),
        span("sp_3", ("T", "A"), "a two"),
        span("sp_4", ("T", "B"), "b one"),
    ]
    body = render_body("T", [claim(s) for s in spans], {s.id: s for s in spans})
    assert body == "# T\n\nintro\n\n## A\n\na one\n\na two\n\n## B\n\nb one\n"


def test_body_heads_the_first_claim_with_its_whole_path_less_a_leading_title():
    s = span("sp_1", ("Doc", "Setup"), "x")
    assert render_body("Other", [claim(s)], {s.id: s}) == "# Other\n\n## Doc\n\n### Setup\n\nx\n"
    assert render_body("Doc", [claim(s)], {s.id: s}) == "# Doc\n\n## Setup\n\nx\n"
    t = span("sp_2", ("Setup",), "y")
    assert render_body("Setup", [claim(t)], {t.id: t}) == "# Setup\n\ny\n"


def test_body_tolerates_claims_without_known_spans_or_headings():
    top = span("sp_top", (), "top")
    claims = [
        Claim(id="cl_a", text="unknown", span_ids=("sp_missing",)),
        Claim(id="cl_b", text="uncited", span_ids=()),
        claim(top),
    ]
    assert render_body("T", claims, {top.id: top}) == "# T\n\nunknown\n\nuncited\n\ntop\n"


def test_gate_passes_a_grounded_page_and_counts_distinct_cited_spans():
    one, two = span("sp_1", ("T",), "one"), span("sp_2", ("T",), "two")
    claims = [claim(one), Claim(id="cl_both", text="two", span_ids=(one.id, two.id))]
    result = provenance_gate(page_of(claims), {s.id: s for s in (one, two)})
    assert (result.passed, result.violations) == (True, [])
    assert result.metrics == {"claims": 2, "spans_cited": 2}


def test_gate_names_each_violation_by_code_and_claim():
    known = span("sp_1", ("T",), "known")
    claims = [
        Claim(id="cl_none", text="known", span_ids=()),
        Claim(id="cl_unknown", text="known", span_ids=(known.id, "sp_missing")),
        Claim(id="cl_absent", text="not on the page", span_ids=(known.id,)),
    ]
    result = provenance_gate(page_of(claims, body="# T\n\nknown\n"), {known.id: known})
    assert not result.passed
    assert [v.split(": ")[:2] for v in result.violations] == [
        ["provenance.missing_passages", "cl_none"],
        ["provenance.unknown_passage", "cl_unknown"],
        ["provenance.claim_not_in_body", "cl_absent"],
        ["provenance.unclaimed_line", "-"],  # the body is not what render_body writes of them
    ]
    assert "sp_missing" in result.violations[1]
    assert result.metrics == {"claims": 3, "spans_cited": 2}


def test_gate_finds_the_claim_in_the_body_as_it_is_written():
    """CORE5: the claim's text, as it is; its words with white space collapsed are not it."""
    wrapped = span("sp_1", ("T",), "a\n  b")
    spans = {wrapped.id: wrapped}
    written = provenance_gate(page_of([claim(wrapped)], body="# T\n\na\n  b\n"), spans)
    collapsed = provenance_gate(page_of([claim(wrapped)], body="# T\n\na b\n"), spans)
    assert not any("claim_not_in_body" in v for v in written.violations)
    assert any("claim_not_in_body" in v for v in collapsed.violations)
    # Not the body render_body writes, so the reverse check refuses it too.
    assert not collapsed.passed


def test_candidate_for_a_new_page_cites_each_new_span_once():
    spans = [span("sp_1", ("T",), "one"), span("sp_2", ("T", "A"), "two")]
    page = candidate_page("t", "T", None, spans)
    assert (page.slug, page.title) == ("t", "T")
    assert page.claims == tuple(claim(s) for s in spans)
    assert page.body == "# T\n\none\n\n## A\n\ntwo\n"


def test_candidate_update_keeps_existing_claims_first_with_their_headings():
    old, new = span("sp_old", ("T", "A"), "old"), span("sp_new", ("T", "B"), "new")
    existing = Page(
        project_id="br_000000000000",
        slug="t",
        title="T",
        body="# T\n\n## A\n\nold\n",
        version=1,
        claims=(claim(old),),
        updated_at="2026-01-01T00:00:00.000000+00:00",
    )
    page = candidate_page("t", "T", existing, [new], existing_spans={old.id: old})
    assert page.claims == (claim(old), claim(new))
    assert page.body == "# T\n\n## A\n\nold\n\n## B\n\nnew\n"


# compile_note

GUIDE = "# Guide\n\nIntro line.\n\n## Install\n\n- step one\n- step two\n\n## Run\n\nRun it.\n"
GUIDE_UPDATE = "# Guide\n\n## Run\n\nRun it twice.\n"


def passing_gate(page: CandidatePage, spans) -> GateResult:
    return GateResult(passed=True)


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def project(store):
    return store.create_project("alpha")


def compiled(store, project, text, **kwargs):
    note, _ = store.add_note(project.id, text)
    return note, compile_note(store, note, **kwargs)


@pytest.mark.parametrize("text", ["# Only a heading\n", "---\ntitle: Front matter only\n---\n", ""])
def test_a_note_without_content_spans_saves_nothing(store, project, text):
    _, result = compiled(store, project, text)
    assert (result.status, result.version, result.gate) == ("unchanged", None, None)
    assert store.list_pages(project.id) == []


def test_compiling_nothing_new_is_unchanged_at_the_current_version(store, project):
    note, _ = compiled(store, project, GUIDE)
    again = compile_note(store, note)
    assert (again.status, again.version, again.gate) == ("unchanged", 1, None)


def test_a_rejected_update_leaves_the_page_at_its_version(store, project):
    compiled(store, project, GUIDE)
    reject = lambda page, spans: GateResult(passed=False, violations=["test.reject"])  # noqa: E731
    _, result = compiled(store, project, GUIDE_UPDATE, gate=reject)
    assert (result.status, result.version) == ("rejected", 1)
    assert store.page_versions(project.id, "guide") == [1]


def test_an_update_keeps_the_headings_of_existing_claims(store, project):
    compiled(store, project, GUIDE)
    seen = []

    def gate(page, spans):
        seen.append(spans)
        return provenance_gate(page, spans)

    _, result = compiled(store, project, GUIDE_UPDATE, gate=gate)
    v1, v2 = store.get_page(project.id, "guide", version=1), store.get_page(project.id, "guide")
    assert result.version == 2
    assert v2.body == v1.body + "\nRun it twice.\n"
    assert "## Install" in v2.body and "## Run" in v2.body
    cited = [i for c in v2.claims for i in c.span_ids]
    assert list(seen[0]) == cited  # the gate sees every cited span, old and new
    assert v2.body == render_body(v2.title, v2.claims, store.get_spans(cited))


def test_spans_the_page_already_covers_are_not_added_again(store, project):
    note, _ = store.add_note(project.id, "# Notes\n\nalpha\n\nbeta\n\ngamma\n")
    a, b, c = [s for s in store.note_spans(note.id) if s.kind in CONTENT_KINDS]
    covered = (
        Claim(id="cl_custom", text=a.text, span_ids=(a.id,)),  # cites a under another claim id
        Claim(id=claim_id(b.id), text=b.text, span_ids=("sp_elsewhere",)),  # b's claim id
    )
    store.save_page(project.id, "notes", "Notes", "# Notes\n\nalpha\n\nbeta\n", covered)
    result = compile_note(store, note, gate=passing_gate)
    assert result.status == "saved"
    assert store.get_page(project.id, "notes").claims == (*covered, claim(c))


def test_a_page_citing_an_unknown_span_is_rejected_not_raised(store, project):
    stale = Claim(id="cl_stale", text="stale", span_ids=("sp_0000000000000000",))
    store.save_page(project.id, "guide", "Guide", "# Guide\n\nstale\n", (stale,))
    _, result = compiled(store, project, GUIDE)
    assert (result.status, result.version) == ("rejected", 1)
    assert result.gate.violations[0].startswith("provenance.unknown_passage: cl_stale: ")


def test_concurrent_compiles_of_one_page_keep_both_notes(store, project):
    """Two notes on one topic compiled at once: the later save builds on the earlier one,
    never on the same stale version (which would drop the other note's claims)."""
    first, _ = store.add_note(project.id, "# Topic\n\nfirst fact\n")
    second, _ = store.add_note(project.id, "# Topic\n\nsecond fact\n")
    release, second_done = threading.Event(), threading.Event()

    def held_gate(page: CandidatePage, spans) -> GateResult:
        release.wait(5)
        return GateResult(passed=True)

    def compile_second():
        compile_note(store, second)
        second_done.set()

    threads = [
        threading.Thread(target=compile_note, args=(store, first, held_gate)),
        threading.Thread(target=compile_second),
    ]
    threads[0].start()
    threads[1].start()
    second_done.wait(0.3)  # unserialised, the second compile saves here from a stale read
    release.set()
    for t in threads:
        t.join(5)
    texts = sorted(c.text for c in store.get_page(project.id, "topic").claims)
    assert texts == ["first fact", "second fact"]


# Optimistic concurrency: a compile saves on the version it read


def counted(calls: list, before=None):
    """An extractive composer that records each call and runs `before` during the first."""

    def compose(title, spans):
        calls.append([s.id for s in spans])
        if before is not None and len(calls) == 1:
            before()
        return extractive(title, spans)

    return compose


def test_a_page_that_moved_is_read_again_and_the_same_draft_is_reused(tmp_path):
    here, there = Store(tmp_path / "c.db"), Store(tmp_path / "c.db")
    try:
        project = here.create_project("alpha")
        first, _ = here.add_note(project.id, "# Topic\n\nfirst fact\n")
        second, _ = there.add_note(project.id, "# Topic\n\nsecond fact\n")
        calls: list = []
        compose = counted(calls, lambda: compile_note(there, second))
        result = compile_note(here, first, passing_gate, compose=compose)
        assert (result.status, result.version) == ("saved", 2)
        assert len(calls) == 1  # the spans to draft were the same, so no second draft
        texts = [c.text for c in here.get_page(project.id, "topic").claims]
        assert texts == ["second fact", "first fact"]
    finally:
        here.close()
        there.close()


def test_a_page_that_moved_under_different_spans_is_composed_again(store, project):
    note, _ = store.add_note(project.id, "# Topic\n\none\n\ntwo\n")
    one, two = [s for s in store.note_spans(note.id) if s.kind in CONTENT_KINDS]

    def another_writer():
        store.save_page(project.id, "topic", "Topic", "# Topic\n\none\n", (claim(one),))

    calls: list = []
    result = compile_note(store, note, passing_gate, compose=counted(calls, another_writer))
    assert calls == [[one.id, two.id], [two.id]]
    assert (result.status, result.version) == ("saved", 2)
    assert [c.text for c in store.get_page(project.id, "topic").claims] == ["one", "two"]


def test_redrafting_adds_up_what_every_draft_cost(store, project):
    note, _ = store.add_note(project.id, "# Topic\n\none\n\ntwo\n")
    one, _ = [s for s in store.note_spans(note.id) if s.kind in CONTENT_KINDS]
    calls: list = []

    def compose(title, spans):
        calls.append(spans)
        if len(calls) == 1:
            store.save_page(project.id, "topic", "Topic", "# Topic\n\none\n", (claim(one),))
        stats = DraftStats(composer="m", attempts=2, prompt_tokens=10, latency_s=1.0)
        return Draft(claims=extractive(title, spans).claims, stats=stats)

    result = compile_note(store, note, passing_gate, compose=compose)
    assert result.status == "saved"
    assert result.draft == DraftStats(composer="m", attempts=4, prompt_tokens=20, latency_s=2.0)


def test_a_page_that_moved_to_cover_everything_is_unchanged(store, project):
    note, _ = store.add_note(project.id, "# Topic\n\none\n")
    calls: list = []
    compose = counted(calls, lambda: compile_note(store, note))
    result = compile_note(store, note, passing_gate, compose=compose)
    assert (result.status, result.version) == ("unchanged", 1)
    assert store.page_versions(project.id, "topic") == [1]


def test_a_page_that_keeps_moving_is_rejected_after_three_tries(store, project):
    note, _ = store.add_note(project.id, "# Topic\n\nmine\n")
    others = iter(range(10))

    def gate(page, spans):  # another writer saves while each candidate is being judged
        n = next(others)
        store.save_page(project.id, "topic", "Topic", f"# Topic\n\nother {n}\n", ())
        return GateResult(passed=True)

    result = compile_note(store, note, gate)
    assert result.status == "rejected"
    assert result.gate.violations == [
        "compile.stale_page: -: the page changed while it was compiled, 3 times in a row"
    ]
    assert result.version == 3 and result.candidate is not None
    assert store.page_versions(project.id, "topic") == [1, 2, 3]
    assert all(c.text != "mine" for c in store.get_page(project.id, "topic").claims)


def test_a_slow_draft_never_blocks_a_compile_of_another_page(store, project):
    slow, _ = store.add_note(project.id, "# Slow\n\nslow fact\n")
    fast, _ = store.add_note(project.id, "# Fast\n\nfast fact\n")
    drafting, release = threading.Event(), threading.Event()

    def held(title, spans):
        drafting.set()
        release.wait(60)  # longer than the join below, so the slow draft is still held there
        return extractive(title, spans)

    worker = threading.Thread(target=compile_note, args=(store, slow), kwargs={"compose": held})
    worker.start()
    try:
        assert drafting.wait(5)
        done = []
        other = threading.Thread(target=lambda: done.append(compile_note(store, fast).status))
        other.start()
        other.join(30)  # a hang guard: a process-wide lock holds it until the release
        assert done == ["saved"]  # while the slow draft is still waiting
    finally:
        release.set()
        worker.join(5)
    assert store.get_page(project.id, "slow").version == 1


def test_the_compiler_holds_no_process_wide_lock():
    import cairn.compiler

    assert not hasattr(cairn.compiler, "_COMPILE_LOCK")
