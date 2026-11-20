"""A version whose fold was made under an old inline-code rule is rebuilt from its passages
when it is next touched (second report, finding 5b).

Before decision 189 a tidy read white space inside inline code as prose, and at fe47cfd6 a
backtick left as text in a quote could still pair across the quote's lines; either way two
passages that differ only inside code were folded into one claim, which today's gate does
not call the whole of both. Such a version is built here at the store's level, as that tidy
saved it. What happens to it, found first: a tidy and an arrangement answered "unchanged",
sending either note again answered "unchanged", a new note to its page was refused
(retention.fact_dropped), and an import refused the archive. Now a tidy, an arrangement and
a new note rebuild the fold (one claim per passage, its text the passage's) and an import
rebuilds each version that holds one; sending a note again still changes nothing. Made-up
notes; every model is a FakeModel."""

import asyncio
import io
import json

import pytest

import cairn.compiler
from cairn.arrange import Arranger, arrange_page, tidy_page
from cairn.compiler import CandidatePage, render_body
from cairn.gate import lint_gate, verbatim_gate, whole
from cairn.llm import FakeModel, Reply
from cairn.memory import Memory
from cairn.store import Claim, Store

OLD = {
    "before decision 189": ("Keep `a  b.txt` here.", "Keep `a b.txt` here."),
    "a quote at fe47cfd6": (
        "> Stray `\n>\n> Keep `a  b.txt` here.",
        "> Stray `\n>\n> Keep `a b.txt` here.",
    ),
}


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


def old_fold(store: Store, first: str, second: str):
    """A page whose latest version is one claim, the first note's text, citing both notes'
    passages: what the tidy saved under the old rule. Earlier versions hold the two apart."""
    project = store.create_project("alpha")
    memory = Memory(store, lint_gate)
    memory.note("alpha", first, title="Files")
    memory.note("alpha", second, title="Files", topic="Files")
    page = store.get_page(project.id, "files")
    spans = store.get_spans([i for claim in page.claims for i in claim.span_ids])
    cited = tuple(i for claim in page.claims for i in claim.span_ids)
    fold = Claim(id=page.claims[0].id, text=first, span_ids=cited)
    assert not whole(fold, spans)  # today's rule: not the whole of the second passage
    body = render_body("Files", [fold], spans)
    store.save_page(project.id, "files", "Files", body, [fold], expected_version=page.version)
    return project, memory, cited


def rebuilt(store: Store, project_id: str, first: str, second: str, cited) -> None:
    page = store.get_page(project_id, "files")
    assert [(c.text, c.span_ids) for c in page.claims] == [
        (first, (cited[0],)),
        (second, (cited[1],)),
    ]
    spans = store.get_spans(cited)
    candidate = CandidatePage(slug="files", title=page.title, body=page.body, claims=page.claims)
    assert page.body == render_body(page.title, page.claims, spans)
    assert verbatim_gate(candidate, spans).passed


@pytest.fixture(params=list(OLD), ids=list(OLD))
def old(request, store):
    first, second = OLD[request.param]
    project, memory, cited = old_fold(store, first, second)
    return project, memory, cited, first, second


def test_the_worker_without_a_model_rebuilds_the_fold(store, old):
    """`cairn arrange --tidy` and serve's tidy run this worker."""
    project, _, cited, first, second = old
    before = store.get_page(project.id, "files").version
    arranger = Arranger(None, price=None, view_for=store.for_account)
    arranger.queue(store.account_id, project.id, "files")
    (done,) = asyncio.run(arranger.run_once(limit=1))
    assert (done.status, done.previous, done.version) == ("arranged", before, before + 1)
    rebuilt(store, project.id, first, second, cited)
    assert tidy_page(store, project.id, "files").status == "unchanged"  # and stays so


def test_an_arrangement_rebuilds_the_fold_before_any_model_is_asked(store, old):
    project, _, cited, first, second = old
    model = FakeModel([Reply(text=json.dumps({"order": [1], "same": []}), prompt_tokens=1)])
    assert arrange_page(store, project.id, "files", model).status == "arranged"
    assert model.requests == []
    rebuilt(store, project.id, first, second, cited)


def test_a_new_note_to_the_page_saves_and_rebuilds_the_fold(store, old):
    project, memory, cited, first, second = old
    answer = memory.note("alpha", "Brook keeps the log.", title="Files", topic="Files")
    assert answer["status"] == "saved", answer["violations"]
    page = store.get_page(project.id, "files")
    assert [c.text for c in page.claims] == [first, second, "Brook keeps the log."]
    assert [c.span_ids for c in page.claims][:2] == [(cited[0],), (cited[1],)]
    assert all(len(c.span_ids) == 1 for c in page.claims)


@pytest.mark.parametrize("which", [0, 1], ids=["first", "second"])
def test_sending_either_note_again_changes_nothing(store, old, which):
    project, memory, *_ = old
    before = store.get_page(project.id, "files")
    answer = memory.note("alpha", old[3 + which], title="Files", topic="Files")
    assert (answer["created"], answer["status"]) == (False, "unchanged")
    assert store.get_page(project.id, "files") == before


def test_an_archive_that_holds_the_fold_imports_with_every_version_rebuilt(store, old, tmp_path):
    _, _, cited, first, second = old
    archive = io.BytesIO()
    store.export(archive)  # the latest version is the fold
    target = Store(tmp_path / "imported.db")
    try:
        target.import_export(io.BytesIO(archive.getvalue()))
        rebuilt(target, target.get_project("alpha").id, first, second, cited)
    finally:
        target.close()


def test_an_archive_with_the_fold_in_its_history_imports(store, old, tmp_path):
    project, _, cited, first, second = old
    assert tidy_page(store, project.id, "files").status == "arranged"  # latest rebuilt
    archive = io.BytesIO()
    store.export(archive)  # an older version, retained, is the fold
    target = Store(tmp_path / "imported.db")
    try:
        target.import_export(io.BytesIO(archive.getvalue()))
        alpha = target.get_project("alpha").id
        rebuilt(target, alpha, first, second, cited)
        spans = target.get_spans(cited)
        versions = target.page_versions(alpha, "files")
        assert len(versions) == 4
        for version in versions:
            claims = target.get_page(alpha, "files", version).claims
            assert all(whole(claim, spans) for claim in claims), version
    finally:
        target.close()


def test_a_false_citation_beside_an_old_fold_is_still_refused(store, tmp_path):
    """Only a fold of passages that say the same but for white space is rebuilt; a claim
    citing a passage that says something else is refused, as before (the first report's B2)."""
    first, second = OLD["before decision 189"]
    project, memory, cited = old_fold(store, first, second)
    memory.note("alpha", "Quill runs `make  all` nightly.", title="Other")
    other = store.get_page(project.id, "other")
    spans = store.get_spans([*cited, *other.claims[0].span_ids])
    false = Claim(id=other.claims[0].id, text=first, span_ids=(cited[0], *other.claims[0].span_ids))
    body = render_body("Other", [false], spans)
    store.save_page(project.id, "other", "Other", body, [false], expected_version=other.version)
    archive = io.BytesIO()
    store.export(archive)
    target = Store(tmp_path / "imported.db")
    try:
        with pytest.raises(ValueError, match="does not say what a passage it cites says"):
            target.import_export(io.BytesIO(archive.getvalue()))
    finally:
        target.close()


def test_a_page_with_no_fold_is_never_read_for_one(store, monkeypatch):
    """No parse unless a claim cites more than one passage: a tidy and a note of a page whose
    claims each cite one passage never ask whether a fold is whole."""
    memory = Memory(store, lint_gate)
    store.create_project("alpha")
    memory.note("alpha", "Keep `a  b.txt` here.\n\nBrook too.", title="Files")

    def never(*_):
        raise AssertionError("a page with no fold was read for one")

    monkeypatch.setattr(cairn.compiler, "whole", never)
    memory.note("alpha", "Quill too.", title="Files", topic="Files")
    project = store.get_project("alpha")
    assert tidy_page(store, project.id, "files").status == "unchanged"
