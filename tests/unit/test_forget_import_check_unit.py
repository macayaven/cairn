"""What a forget writes, an import takes: one check, the import's own, and a forget is never
refused (third report, must-fix 1; CORE6A, decisions 2 and 3).

forget_note writes a page's next version itself, past every gate. It runs the import's check
of a version (store.portable.version_faults, the function an import raises from) on the
claims it is about to write, and the claims that fail it leave the version, as the forgotten
note's own claims do: the forget completes, the notes of those claims stay stored, and one
line on the store's log counts the claims by page and version, never by text.

Every version here is built at the store's level (Store.save_page gates nothing), since no
compile, tidy or arrangement of cairn's saves one: a fold across two headings, a claim in
other words than its passage, an id held twice, a passage of another project, and three
copies of one passage (a composer can save those, beside a longer note). Made-up notes."""

import io
import logging

import pytest

from cairn.compiler import render_body
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Claim, Store
from cairn.store.portable import Refusal, version_faults

SAME = "Ada keeps port 80 open."


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


class Built:
    """A project `alpha` with one page, `net`: each of `texts` a note of it, and `claims`
    (made by `shape` from the notes' own claims) saved over them at the store's level."""

    def __init__(self, store: Store, texts, shape) -> None:
        self.store, self.memory = store, Memory(store, lint_gate)
        self.project = store.create_project("alpha")
        self.notes = [
            self.memory.note("alpha", text, title="Net", topic="Net")["note_id"] for text in texts
        ]
        page = self.page()
        claims = shape(list(page.claims))
        spans = store.get_spans([i for claim in claims for i in claim.span_ids])
        body = render_body("Net", claims, spans)
        store.save_page(self.project.id, "net", "Net", body, claims, expected_version=page.version)
        self.version = page.version + 1

    def page(self):
        return self.store.get_page(self.project.id, "net")

    def imported(self, tmp_path) -> list[tuple[int, list[tuple[str, tuple[str, ...]]]]]:
        """Each version of the page, (number, its claims' texts and citations), as a store
        holds it that the export was imported into."""
        archive = io.BytesIO()
        self.store.export(archive)
        tmp_path.mkdir(exist_ok=True)
        target = Store(tmp_path / "imported.db")
        try:
            target.import_export(io.BytesIO(archive.getvalue()))
            project_id = target.get_project("alpha").id
            pages = [
                target.get_page(project_id, "net", version)
                for version in target.page_versions(project_id, "net")
            ]
            return [(p.version, [(c.text, c.span_ids) for c in p.claims]) for p in pages]
        finally:
            target.close()


def lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "cairn.store"]


def test_a_fold_left_under_two_headings_leaves_the_version_and_the_forget_completes(
    store, tmp_path, caplog
):
    """The version that cannot pass: one claim citing the same words in three notes, two of
    them under different headings. Without the first note the claim is still the whole of
    both passages left, and an import refuses a fold across headings."""

    def fold(claims):
        cited = tuple(i for claim in claims[:3] for i in claim.span_ids)
        return [claims[0].model_copy(update={"span_ids": cited}), claims[3]]

    texts = [SAME, f"## Ports\n\n{SAME}", f"## Firewall\n\n{SAME}", "Quill keeps the log."]
    built = Built(store, texts, fold)
    said = store.forget_preview(built.notes[0])
    with caplog.at_level(logging.INFO, logger="cairn.store"):
        done = store.forget_note(built.notes[0])
    assert done == said and done.rebuilt == ("net",)
    page = built.page()
    assert [claim.text for claim in page.claims] == ["Quill keeps the log."]
    assert page.metrics == {"claims": 1, "spans_cited": 1}
    assert built.imported(tmp_path) == [
        (built.version + 1, [("Quill keeps the log.", page.claims[0].span_ids)])
    ]
    (line,) = lines(caplog)
    assert "1 claim" in line and "'net'" in line and f"version {built.version + 1}" in line
    assert "Ada" not in line and "cl_" not in line and "sp_" not in line
    # the notes of the claim that left stay, and sending one again files it
    again = built.memory.note("alpha", texts[1], title="Net", topic="Net")
    assert (again["note_id"], again["created"], again["status"]) == (built.notes[1], False, "saved")
    assert [claim.text for claim in built.page().claims] == ["Quill keeps the log.", SAME]


def test_a_claim_in_other_words_leaves_with_a_forget_that_never_cited_it(store, tmp_path, caplog):
    def reworded(claims):
        return [claims[0].model_copy(update={"text": "Ada opens every port."}), *claims[1:]]

    built = Built(store, [SAME, "Brook owns the rollback.", "Quill keeps the log."], reworded)
    with caplog.at_level(logging.INFO, logger="cairn.store"):
        store.forget_note(built.notes[2])
    assert [claim.text for claim in built.page().claims] == ["Brook owns the rollback."]
    assert [claims for _, claims in built.imported(tmp_path)] == [
        [("Brook owns the rollback.", built.page().claims[0].span_ids)]
    ]
    assert len(lines(caplog)) == 1 and "1 claim" in lines(caplog)[0]


def test_the_later_of_two_claims_with_one_id_leaves(store, tmp_path, caplog):
    def doubled(claims):
        return [claims[0], claims[1].model_copy(update={"id": claims[0].id}), claims[2]]

    built = Built(store, [SAME, "Brook owns the rollback.", "Quill keeps the log."], doubled)
    waits = built.memory.note("alpha", "Wren reads the log.", title="Net", topic="Net")
    assert waits["status"] == "rejected"  # the page takes no note while it holds the id twice
    assert waits["violations"][0].startswith("import.id_twice:")
    with caplog.at_level(logging.INFO, logger="cairn.store"):
        store.forget_note(built.notes[2])
    assert [claim.text for claim in built.page().claims] == [SAME]
    assert len(built.imported(tmp_path)) == 1
    assert len(lines(caplog)) == 1 and "1 claim" in lines(caplog)[0]
    # and takes one again once the forget dropped the later claim
    again = built.memory.note("alpha", "Wren reads the log.", title="Net", topic="Net")
    assert (again["note_id"], again["status"]) == (waits["note_id"], "saved")
    assert [claim.text for claim in built.page().claims] == [SAME, "Wren reads the log."]


def test_a_claim_citing_a_passage_of_another_project_leaves(store, tmp_path):
    other = store.create_project("beta")
    elsewhere, _ = store.add_note(other.id, SAME, title="Net")
    foreign = store.note_spans(elsewhere.id)[0]

    def citing_beta(claims):
        return [claims[0].model_copy(update={"span_ids": (foreign.id,)}), *claims[1:]]

    built = Built(store, [SAME, "Brook owns the rollback.", "Quill keeps the log."], citing_beta)
    store.forget_note(built.notes[2])
    assert [claim.text for claim in built.page().claims] == ["Brook owns the rollback."]
    assert len(built.imported(tmp_path)) == 1


def test_of_a_passage_copied_too_often_the_first_copies_stay(store, tmp_path, caplog):
    """Three copies of one passage, within twice the page's passages only while a longer
    note is on it. Forgetting that note leaves three times the text of the one passage left:
    no claim fails alone, the version does. From the first claim on, a claim leaves when it
    takes those before it past the bound: the third copy, and nothing else."""

    def copies(claims):
        again = [
            Claim(id=f"cl_{n:016x}", text=claims[0].text, span_ids=claims[0].span_ids)
            for n in (1, 2)
        ]
        return [claims[0], again[0], claims[2], again[1], claims[1]]

    longer = "Brook owns the rollback and the nightly log of Fable Works."
    built = Built(store, [SAME, longer, "Fin."], copies)
    before = built.page().claims
    assert built.imported(tmp_path)[-1][0] == built.version  # an import takes it as it is
    with caplog.at_level(logging.INFO, logger="cairn.store"):
        store.forget_note(built.notes[1])
    assert built.page().claims == before[:3]  # two copies and the claim of the other note
    (line,) = lines(caplog)
    assert "1 claim" in line and f"'net' at its version {built.version + 1}" in line
    assert [number for number, _ in built.imported(tmp_path / "after")] == [built.version + 1]


def test_a_page_whose_every_claim_leaves_is_removed_and_the_log_names_no_slug(
    store, tmp_path, caplog
):
    """A removed page's slug is made from a note's title, which the audit log takes out of
    its events too: the line counts the claims and names nothing."""

    def reworded(claims):
        return [claims[0].model_copy(update={"text": "Ada opens every port."}), claims[1]]

    built = Built(store, [SAME, "Quill keeps the log."], reworded)
    said = store.forget_preview(built.notes[1])
    with caplog.at_level(logging.INFO, logger="cairn.store"):
        done = store.forget_note(built.notes[1])
    assert done == said and (done.rebuilt, done.removed) == ((), ("net",))
    assert built.page() is None and store.page_versions(built.project.id, "net") == []
    (line,) = lines(caplog)
    assert "1 claim" in line and "net" not in line and "Ada" not in line
    archive = io.BytesIO()
    store.export(archive)
    target = Store(tmp_path / "imported.db")
    try:
        target.import_export(io.BytesIO(archive.getvalue()))  # nothing of it is refused
    finally:
        target.close()
    # the note stays, and sending it again files it
    again = built.memory.note("alpha", SAME, title="Net", topic="Net")
    assert (again["note_id"], again["created"], again["status"]) == (built.notes[0], False, "saved")
    assert [claim.text for claim in built.page().claims] == [SAME]


def test_a_forget_that_leaves_every_claim_an_import_takes_logs_nothing(store, caplog):
    built = Built(store, [SAME, "Quill keeps the log."], lambda claims: claims[::-1])
    with caplog.at_level(logging.INFO, logger="cairn.store"):
        store.forget_note(built.notes[1])
    assert [claim.text for claim in built.page().claims] == [SAME]
    assert lines(caplog) == []


def test_the_claims_no_fault_names_pass_together(store):
    """The check's own promise, which a forget relies on to run it once: of a version with a
    claim in other words, an id held twice and a fold across headings, the claims it does
    not name are a version it finds no fault in."""

    def mixed(claims):
        cited = tuple(i for claim in claims[1:3] for i in claim.span_ids)
        return [
            claims[0].model_copy(update={"text": "Ada opens every port."}),
            claims[1].model_copy(update={"span_ids": cited}),
            claims[3],
            claims[4].model_copy(update={"id": claims[3].id}),
            claims[4],
        ]

    texts = [
        "Ada closes port 22.",
        f"## Ports\n\n{SAME}",
        f"## Firewall\n\n{SAME}",
        "Quill keeps the log.",
        "Brook owns the rollback.",
    ]
    built = Built(store, texts, mixed)
    claims = built.page().claims
    spans = store.get_spans([i for claim in claims for i in claim.span_ids])
    faults = list(version_faults(claims, spans, legacy=True))
    assert faults == [(1, Refusal.NOT_WHOLE), (2, Refusal.HEADINGS), (4, Refusal.ID_TWICE)]
    rest = [claim for place, claim in enumerate(claims, 1) if place not in {1, 2, 4}]
    assert [claim.text for claim in rest] == ["Quill keeps the log.", "Brook owns the rollback."]
    assert list(version_faults(rest, spans, legacy=True)) == []
