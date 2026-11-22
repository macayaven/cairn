"""A version that holds a fold made under an older rule (CORE5 item 6).

Before CORE5 a tidy, the arranger and a model's draft could fold passages that differ only
by white space: inside inline code before decision 189, across a quote's lines at fe47cfd6,
and in any prose wrapped otherwise until CORE5. Today two passages are the same only when
their text is, so such a fold is not the whole of every passage it cites. It is built here
at the store's level, as that tidy saved it, and every way to touch it is run:

- a tidy, an arrangement and the operator's command save the latest version again, rebuilt
  from its passages; earlier versions stay as they were and their evidence answers as it did;
- a new note to the page saves, on the rebuilt claims;
- sending either note again changes nothing (nothing new to file, so nothing is compiled);
- forgetting the note whose words the fold holds takes the claim, and the page with it,
  until the other note is sent again; forgetting the other note leaves the fold whole;
- an export holds every version as it is, and an import accepts each version unchanged when
  each claim is whole by today's rule or by the old gate's own (store.portable,
  cairn.gate.whole_before_core5); nothing it brings is rewritten.

Ids: a rebuilt claim of a fold's first passage keeps the fold's id; each other one takes
claim_id of its passage, or, when a claim of the version holds that id, an id made from its
place and passage; no id of the version is lost or held twice (the cross-reader's `Third.`
case). Made-up notes; every model is a FakeModel."""

import asyncio
import io
import json

import pytest

from cairn.arrange import Arranger, arrange_page, tidy_page
from cairn.compiler import CandidatePage, render_body
from cairn.gate import lint_gate, verbatim_gate, whole
from cairn.llm import FakeModel, Reply
from cairn.memory import Memory
from cairn.store import Claim, Store
from cairn.store.before_core5 import whole_before_core5

OLD = {
    "before decision 189": ("Keep `a  b.txt` here.", "Keep `a b.txt` here."),
    "a quote at fe47cfd6": (
        "> Stray `\n>\n> Keep `a  b.txt` here.",
        "> Stray `\n>\n> Keep `a b.txt` here.",
    ),
    "prose wrapped otherwise": ("Ada keeps the log\nnightly.", "Ada keeps the log nightly."),
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


def test_a_tidy_saves_a_new_version_and_the_fold_answers_as_it_did(store, old):
    project, memory, cited, first, second = old
    fold = store.get_page(project.id, "files")
    asked = memory.evidence("alpha", "files", fold.claims[0].id, version=fold.version)
    assert tidy_page(store, project.id, "files").status == "arranged"
    rebuilt(store, project.id, first, second, cited)
    assert store.get_page(project.id, "files", fold.version) == fold
    again = memory.evidence("alpha", "files", fold.claims[0].id, version=fold.version)
    assert again == asked
    assert [p["text"] for p in again["passages"]] == [first, second]


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


def versions_of(store: Store, project_id: str) -> list:
    return [
        store.get_page(project_id, "files", v) for v in store.page_versions(project_id, "files")
    ]


def same_versions(source: Store, target: Store) -> None:
    """Every version of the page imported as it was exported: claims, ids, citations, body."""
    mine = versions_of(source, source.get_project("alpha").id)
    theirs = versions_of(target, target.get_project("alpha").id)
    assert [(p.version, p.title, p.body, p.claims) for p in theirs] == [
        (p.version, p.title, p.body, p.claims) for p in mine
    ]


def test_an_archive_that_holds_the_fold_imports_unchanged(store, old, tmp_path):
    _, _, cited, first, second = old
    archive = io.BytesIO()
    store.export(archive)  # the latest version is the fold
    target = Store(tmp_path / "imported.db")
    try:
        target.import_export(io.BytesIO(archive.getvalue()))
        same_versions(store, target)
        alpha = target.get_project("alpha").id
        assert [(c.text, c.span_ids) for c in target.get_page(alpha, "files").claims] == [
            (first, cited)
        ]
        assert tidy_page(target, alpha, "files").status == "arranged"  # rebuilt where it is
        rebuilt(target, alpha, first, second, cited)
    finally:
        target.close()


def test_an_archive_with_the_fold_in_its_history_imports_unchanged(store, old, tmp_path):
    project, _, cited, first, second = old
    assert tidy_page(store, project.id, "files").status == "arranged"  # latest rebuilt
    archive = io.BytesIO()
    store.export(archive)  # an older version, retained, is the fold
    target = Store(tmp_path / "imported.db")
    try:
        target.import_export(io.BytesIO(archive.getvalue()))
        same_versions(store, target)
        assert len(versions_of(target, target.get_project("alpha").id)) == 4
        rebuilt(target, target.get_project("alpha").id, first, second, cited)
    finally:
        target.close()


def test_a_fold_no_rule_ever_accepted_is_refused_naming_page_and_version(store, tmp_path):
    """A no-break space was text for the old gate too (the reference is its code, not an
    approximation): such a fold was never saved by cairn, and an archive that holds one is
    refused, naming the page and the version, never the text."""
    first, second = "Ada keeps the\u00a0log.", "Ada keeps the log."
    project, _, cited = old_fold(store, first, second)
    spans = store.get_spans(cited)
    fold = Claim(id="cl_0000000000000001", text=first, span_ids=cited)
    assert not whole(fold, spans) and not whole_before_core5(fold, spans)
    version = store.get_page(project.id, "files").version
    archive = io.BytesIO()
    store.export(archive)
    target = Store(tmp_path / "imported.db")
    try:
        with pytest.raises(ValueError) as refused:
            target.import_export(io.BytesIO(archive.getvalue()))
        assert f"'files' version {version}" in str(refused.value)
        assert "Ada" not in str(refused.value)
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


def test_the_operator_command_rebuilds_every_such_page(tmp_path, capsys):
    """`cairn arrange --db ... --account ... --tidy`, the step an operator runs for every page
    of a workspace at once, rather than wait for the next note or tidy."""
    from cairn.web import cli

    first, second = OLD["before decision 189"]
    db = tmp_path / "old.db"
    store = Store(db)
    try:
        project, _, cited = old_fold(store, first, second)
        before, account = store.get_page(project.id, "files").version, store.account_id
    finally:
        store.close()
    argv = ["arrange", "--db", str(db), "--account", account, "--tidy"]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [f"alpha/files: arranged, version {before} -> {before + 1}"]
    store = Store(db)
    try:
        rebuilt(store, project.id, first, second, cited)
    finally:
        store.close()


def note_of(store: Store, project_id: str, span_id: str) -> str:
    return next(
        note.id
        for note in store.list_notes(project_id)
        if span_id in {span.id for span in store.note_spans(note.id)}
    )


def test_forgetting_the_note_whose_words_the_fold_holds_leaves_the_claim_as_the_other_notes(
    store,
):
    """Forgetting is a touch that does not rebuild (store.erasure.kept_claims): forgetting the
    second note leaves the fold citing the first, whole. Forgetting the first, whose words the
    fold holds, left a claim in a forgotten note's words that was not the whole of the
    passage left, so it went and the page with it until the second note was sent again; the
    last outside reading showed that on a database the public build made. The claim is the
    whole of that passage by the rule its fold was made under, so it stays, as the second
    note's own text."""
    first, second = OLD["before decision 189"]
    project, memory, cited = old_fold(store, first, second)
    store.forget_note(note_of(store, project.id, cited[0]))
    page = store.get_page(project.id, "files")
    assert [(c.text, c.span_ids) for c in page.claims] == [(second, (cited[1],))]
    assert first not in page.body
    answer = memory.note("alpha", second, title="Files", topic="Files")
    assert (answer["created"], answer["page"]) == (False, "files")


def test_forgetting_the_other_note_leaves_the_fold_whole(store):
    first, second = OLD["before decision 189"]
    project, _, cited = old_fold(store, first, second)
    store.forget_note(note_of(store, project.id, cited[1]))
    page = store.get_page(project.id, "files")
    assert [(c.text, c.span_ids) for c in page.claims] == [(first, (cited[0],))]


CROSS = "## Ports\n\nAda keeps port 80 open.\n\n## Firewall\n\nAda keeps port 80 open."


def cross_heading_fold(store: Store):
    """A fold a model composer could save before finding 5a: one text under two headings."""
    project = store.create_project("alpha")
    Memory(store, lint_gate).note("alpha", CROSS, title="Net")
    page = store.get_page(project.id, "net")
    cited = tuple(i for claim in page.claims for i in claim.span_ids)
    spans = store.get_spans(cited)
    fold = Claim(id=page.claims[0].id, text=page.claims[0].text, span_ids=cited)
    assert whole(fold, spans) and len({spans[i].heading_path for i in cited}) == 2
    body = render_body("Net", [fold], spans)
    store.save_page(project.id, "net", "Net", body, [fold], expected_version=page.version)
    return project, cited


def test_a_tidy_rebuilds_a_fold_under_two_headings(store):
    project, cited = cross_heading_fold(store)
    assert tidy_page(store, project.id, "net").status == "arranged"
    page = store.get_page(project.id, "net")
    assert [c.span_ids for c in page.claims] == [(cited[0],), (cited[1],)]


def test_an_archive_that_holds_a_fold_under_two_headings_is_still_refused(store, tmp_path):
    """The import's heading rule stays (the first report's B2): only a fold that differs by
    white space is rebuilt on import. Such a fold came only from a model composer (the
    evaluations) before finding 5a. A tidy rebuilds the latest version (above), but the
    version that holds the fold is kept in the page's history, so the archive is refused
    still: a known limit."""
    project, _ = cross_heading_fold(store)
    assert tidy_page(store, project.id, "net").status == "arranged"
    archive = io.BytesIO()
    store.export(archive)
    target = Store(tmp_path / "imported.db")
    try:
        with pytest.raises(ValueError, match="folds passages under different headings"):
            target.import_export(io.BytesIO(archive.getvalue()))
    finally:
        target.close()


def test_a_rebuilt_claim_never_takes_an_id_another_claim_holds(store, tmp_path):
    """The cross-reader's `Third.` case: a claim of the version already holds the id the
    second passage would give its claim (claim_id of that passage). The rebuilt claim of the
    fold's first passage keeps the fold's id, `Third.` keeps its own, and the other rebuilt
    claim takes an id no claim of the version holds. The archive of that version imports
    unchanged, ids and all."""
    from cairn.spans import claim_id

    first, second = OLD["prose wrapped otherwise"]
    project, memory, cited = old_fold(store, first, second)
    (fold,) = store.get_page(project.id, "files").claims
    memory.note("alpha", "Third.", title="Files", topic="Files")  # rebuilds the fold, too
    page = store.get_page(project.id, "files")
    third = next(c for c in page.claims if c.text == "Third.")
    spans = store.get_spans([*cited, *third.span_ids])
    taken = Claim(id=claim_id(cited[1]), text="Third.", span_ids=third.span_ids)
    claims = [fold, taken]
    body = render_body("Files", claims, spans)
    store.save_page(project.id, "files", "Files", body, claims, expected_version=page.version)
    held = {fold.id, taken.id}

    archive = io.BytesIO()
    store.export(archive)
    target = Store(tmp_path / "imported.db")
    try:
        target.import_export(io.BytesIO(archive.getvalue()))
        same_versions(store, target)
    finally:
        target.close()

    assert tidy_page(store, project.id, "files").status == "arranged"
    after = store.get_page(project.id, "files").claims
    ids = [c.id for c in after]
    assert len(ids) == len(set(ids)) == 3
    assert held <= set(ids)
    assert [(c.text, c.span_ids) for c in after] == [
        (first, (cited[0],)),
        (second, (cited[1],)),
        ("Third.", third.span_ids),
    ]
    assert after[0].id == fold.id and after[2].id == taken.id
    candidate = CandidatePage(
        slug="files", title="Files", body=store.get_page(project.id, "files").body, claims=after
    )
    assert verbatim_gate(candidate, spans).passed
