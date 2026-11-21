"""An old fold on a page at the claim limit (third report, "Historical archives and page
growth"; CORE6A, decision 6).

A page holds at most PAGE_CLAIMS_MAX (2,000) claims. A fold made under the rule before CORE5
is one claim citing two passages that today are two texts, and rebuilding it
(compiler.rebuilt_folds) makes two claims of it. With 1,998 other claims the rebuilt page
holds 2,000 and a tidy saves it. With 1,999 it would hold 2,001: the limit is not raised and
nothing is lost. The tidy is refused, "rejected" with the store's own reason for a full page
(PageTooLong's words: the page and the bound, never a claim), and the page keeps the version
it had, as after any refused tidy. An arrangement answers "large" for a page of more claims
than it sends a model (MAX_CLAIMS, 300); one allowed that many hands the old fold to the tidy
and ends as the tidy does, without asking its model. The versions are built at the store's
level, one note of 2,000 passages, so the test costs two saves. Made-up notes."""

import pytest

from cairn.arrange import arrange_page, tidy_page
from cairn.compiler import render_body
from cairn.llm import FakeModel
from cairn.spans import claim_id
from cairn.store import PAGE_CLAIMS_MAX, Claim, Store

OLD = ("Larch  grows.", "Larch grows.")  # one text before CORE5, two today


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


def old_fold_beside(store: Store, others: int):
    """A page whose first claim folds OLD's two passages, beside `others` claims of their
    own passages: (project, the version saved, the passages, its claims)."""
    project = store.create_project("alpha")
    texts = [*OLD, *(f"Twig {n}." for n in range(others))]
    note, _ = store.add_note(project.id, "\n\n".join(texts), title="Ledger")
    passages = store.note_spans(note.id)
    assert [span.text for span in passages] == texts
    first, second, *rest = passages
    fold = Claim(id=claim_id(first.id), text=first.text, span_ids=(first.id, second.id))
    claims = [fold, *(Claim(id=claim_id(s.id), text=s.text, span_ids=(s.id,)) for s in rest)]
    body = render_body("Ledger", claims, {s.id: s for s in passages})
    return project, store.save_page(project.id, "ledger", "Ledger", body, claims), passages, claims


def test_with_1998_other_claims_a_tidy_rebuilds_the_fold_to_the_limit(store):
    project, before, passages, claims = old_fold_beside(store, PAGE_CLAIMS_MAX - 2)
    done = tidy_page(store, project.id, "ledger")
    assert (done.status, done.version) == ("arranged", before.version + 1)
    page = store.get_page(project.id, "ledger")
    assert len(page.claims) == PAGE_CLAIMS_MAX == len({claim.id for claim in page.claims})
    assert [(c.text, c.span_ids) for c in page.claims] == [(s.text, (s.id,)) for s in passages]
    assert page.claims[0].id == claims[0].id and page.claims[2:] == tuple(claims[1:])
    assert store.get_page(project.id, "ledger", before.version) == before


def test_with_1999_other_claims_the_tidy_is_refused_and_the_version_stays(store):
    project, before, _, _ = old_fold_beside(store, PAGE_CLAIMS_MAX - 1)
    assert len(before.claims) == PAGE_CLAIMS_MAX  # full, and the rebuilt page one more
    done = tidy_page(store, project.id, "ledger")
    assert (done.status, done.version, done.previous) == (
        "rejected",
        before.version,
        before.version,
    )
    assert done.problem == "page 'ledger' would hold 2,001 claims: a page holds at most 2,000"
    assert store.get_page(project.id, "ledger") == before
    assert store.page_versions(project.id, "ledger") == [before.version]

    model = FakeModel([])  # no model is asked, by either bound
    assert arrange_page(store, project.id, "ledger", model).status == "large"
    again = arrange_page(store, project.id, "ledger", model, max_claims=PAGE_CLAIMS_MAX)
    assert (again.status, again.problem) == (done.status, done.problem) and model.requests == []
    assert store.get_page(project.id, "ledger") == before
