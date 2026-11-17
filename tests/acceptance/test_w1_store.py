"""W1 acceptance tests (frozen): the SQLite store and the baseline compiler.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract is in the docstrings of src/cairn/store.py and src/cairn/compiler.py.
"""

import sqlite3
from pathlib import Path

import pytest

from cairn.compiler import (
    CandidatePage,
    GateResult,
    claim_id,
    compile_note,
    slugify,
)
from cairn.spans import chunk
from cairn.store import Claim, Store

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RUNBOOK = (FIXTURES / "runbook_synthetic.md").read_text(encoding="utf-8")
SLUG = "quill-and-lumen-runbook"
FOLLOWUP = "# Quill and Lumen runbook\n\nQuill retries 3 times before failing.\n"
CONTENT_KINDS = {"paragraph", "bullet", "code", "table", "quote"}


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


@pytest.fixture
def alpha(store):
    return store.create_project("alpha", "test project")


def test_projects_are_named_unique_and_found_by_id_or_name(store):
    a = store.create_project("agent-harness-path", "a repo project")
    b = store.create_project("Core")
    assert a.id.startswith("br_") and len(a.id) == 15
    assert [x.name for x in store.list_projects()] == ["agent-harness-path", "Core"]
    assert store.get_project(a.id) == a
    assert store.get_project("core") == b
    assert store.get_project("missing") is None
    with pytest.raises(ValueError):
        store.create_project("CORE")
    for bad in ("", "has space", "-leading", "x" * 65, "slash/name"):
        with pytest.raises(ValueError):
            store.create_project(bad)


def test_keys_are_returned_once_and_stored_hashed(store, db):
    key = store.create_key("laptop")
    assert len(key) >= 32
    assert store.check_key(key)
    assert not store.check_key(key + "x")
    assert not store.check_key("")
    assert key not in dump(db)
    assert store.revoke_key("laptop") is True
    assert not store.check_key(key)
    assert store.revoke_key("laptop") is False


def test_sessions_expire_and_end(store, db):
    token = store.create_session()
    assert store.check_session(token)
    assert token not in dump(db)
    store.end_session(token)
    assert not store.check_session(token)
    assert not store.check_session(store.create_session(ttl_seconds=0))


def test_note_stores_its_spans_and_derives_a_title(store, alpha):
    note, created = store.add_note(alpha.id, RUNBOOK, source_ref="test:runbook")
    assert created and note.id.startswith("cap_") and len(note.id) == 16
    assert note.title == "Quill and Lumen runbook"  # from front matter
    assert store.note_spans(note.id) == chunk(note.id, RUNBOOK)
    assert store.get_note(note.id) == note
    heading_only, _ = store.add_note(alpha.id, "intro line\n\n# Later heading\n")
    assert heading_only.title == "Later heading"
    plain, _ = store.add_note(alpha.id, "\n  " + "w" * 100 + "\nmore\n")
    assert plain.title == "w" * 80
    given, _ = store.add_note(alpha.id, "# Ignored\n", title="Given")
    assert given.title == "Given"


def test_duplicate_note_is_returned_not_stored(store, alpha, db):
    first, _ = store.add_note(alpha.id, RUNBOOK, source_ref="test:runbook")
    before = dump(db)
    again, created = store.add_note(alpha.id, RUNBOOK, source_ref="test:runbook")
    assert (again, created) == (first, False)
    assert dump(db) == before
    other_ref, created = store.add_note(alpha.id, RUNBOOK, source_ref="test:other")
    assert created and other_ref.id != first.id
    with pytest.raises(KeyError):
        store.add_note("br_000000000000", "text")


def test_get_spans_returns_only_known_ids(store, alpha):
    note, _ = store.add_note(alpha.id, RUNBOOK)
    spans = store.note_spans(note.id)
    got = store.get_spans([spans[1].id, "sp_0000000000000000"])
    assert got == {spans[1].id: spans[1]}


def test_compile_makes_one_verbatim_claim_per_content_span(store, alpha):
    note, _ = store.add_note(alpha.id, RUNBOOK)
    result = compile_note(store, note)
    assert (result.status, result.version, result.slug) == ("saved", 1, SLUG)
    assert result.gate is not None and result.gate.passed
    page = store.get_page(alpha.id, SLUG)
    content = [s for s in store.note_spans(note.id) if s.kind in CONTENT_KINDS]
    assert [(c.id, c.text, c.span_ids) for c in page.claims] == [
        (claim_id(s.id), s.text, (s.id,)) for s in content
    ]
    assert page.title == "Quill and Lumen runbook"
    assert page.body.startswith("# Quill and Lumen runbook\n\n")
    assert all(c.text in page.body for c in page.claims)
    assert "## Quill — `quill run`" in page.body
    assert page.metrics == result.gate.metrics
    assert page.metrics["claims"] == len(content)


def test_second_note_on_the_same_topic_adds_a_version(store, alpha):
    first, _ = store.add_note(alpha.id, RUNBOOK)
    compile_note(store, first)
    second, _ = store.add_note(alpha.id, FOLLOWUP)
    result = compile_note(store, second)
    assert (result.status, result.version) == ("saved", 2)
    v1, v2 = store.get_page(alpha.id, SLUG, version=1), store.get_page(alpha.id, SLUG)
    assert v2.version == 2 and store.page_versions(alpha.id, SLUG) == [1, 2]
    assert v2.claims[: len(v1.claims)] == v1.claims
    assert v2.claims[-1].text == "Quill retries 3 times before failing."
    assert "Quill retries" not in v1.body
    assert compile_note(store, second).status == "unchanged"


def test_topic_overrides_title(store, alpha):
    note, _ = store.add_note(alpha.id, FOLLOWUP, topic="Retry policy")
    assert compile_note(store, note).slug == "retry-policy"
    assert store.get_page(alpha.id, "retry-policy").title == "Retry policy"


def test_failing_gate_saves_nothing(store, alpha):
    note, _ = store.add_note(alpha.id, RUNBOOK)
    seen = []

    def reject(page: CandidatePage, spans) -> GateResult:
        seen.append((page, spans))
        return GateResult(passed=False, violations=["test.reject"])

    result = compile_note(store, note, gate=reject)
    assert (result.status, result.version) == ("rejected", None)
    assert result.gate.violations == ["test.reject"]
    assert store.get_page(alpha.id, SLUG) is None
    page, spans = seen[0]
    assert set(spans) == {i for c in page.claims for i in c.span_ids}


def test_search_is_scoped_by_project_and_accepts_any_text(store, alpha):
    beta = store.create_project("beta")
    note, _ = store.add_note(alpha.id, RUNBOOK)
    compile_note(store, note)
    other, _ = store.add_note(beta.id, "# Beta notes\n\nNothing about tools here.\n")
    compile_note(store, other)
    hits = store.search("quill run", project_ids=[alpha.id])
    assert hits and all(h.project_id == alpha.id and h.project == "alpha" for h in hits)
    assert hits[0].slug == SLUG and hits[0].span_ids
    page = store.get_page(alpha.id, SLUG)
    assert {h.claim_id for h in hits} <= {c.id for c in page.claims}
    assert store.search("quill run", project_ids=[beta.id]) == []
    assert {h.project for h in store.search("nothing tools")} == {"beta"}
    for odd in ("--set cache.enabled=false", '"unbalanced', "a AND (b OR", "*", "NEAR(x y)", ""):
        store.search(odd)  # never raises


def test_search_sees_only_the_latest_version(store, alpha):
    first, _ = store.add_note(alpha.id, "# Topic\n\nalpha one\n", topic="t")
    compile_note(store, first)
    store.save_page(
        alpha.id,
        "t",
        "t",
        "# t\n\nbeta two\n",
        [Claim(id="cl_x", text="beta two", span_ids=("sp_x",))],
    )
    assert store.search("one", project_ids=[alpha.id]) == []
    assert [h.claim_id for h in store.search("two", project_ids=[alpha.id])] == ["cl_x"]


def test_reads_never_write(store, alpha, db):
    note, _ = store.add_note(alpha.id, RUNBOOK)
    compile_note(store, note)
    key = store.create_key("k")
    token = store.create_session()
    before = dump(db)
    store.list_projects()
    store.get_project("alpha")
    store.get_note(note.id)
    store.list_notes(alpha.id)
    store.note_spans(note.id)
    store.get_spans([c.span_ids[0] for c in store.get_page(alpha.id, SLUG).claims])
    store.get_page(alpha.id, SLUG, version=1)
    store.list_pages(alpha.id)
    store.page_versions(alpha.id, SLUG)
    store.search("quill")
    store.check_key(key)
    store.check_session(token)
    assert dump(db) == before


def test_data_survives_reopening(db):
    s = Store(db)
    project = s.create_project("alpha")
    note, _ = s.add_note(project.id, RUNBOOK)
    compile_note(s, note)
    key = s.create_key("k")
    s.close()
    s = Store(db)
    try:
        assert s.get_project("alpha") == project
        assert s.get_page(project.id, SLUG).version == 1
        assert s.check_key(key)
        assert s.search("lumen", project_ids=[project.id])
    finally:
        s.close()


def test_store_works_in_memory():
    s = Store(":memory:")
    try:
        project = s.create_project("alpha")
        note, _ = s.add_note(project.id, FOLLOWUP)
        assert compile_note(s, note).status == "saved"
    finally:
        s.close()


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("Quill and Lumen runbook", "quill-and-lumen-runbook"),
        ("  Retry: policy (v2)!  ", "retry-policy-v2"),
        ("Ünïcödé — naïve", "unicode-naive"),
        ("!!!", "untitled"),
        ("word " * 30, ("word-" * 13)[:-1]),
    ],
)
def test_slugify(text, slug):
    assert slugify(text) == slug
