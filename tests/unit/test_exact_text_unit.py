"""Two passages are the same only when their text is the same (CORE5, decision of the
maintainer): what is ignored is a carriage return at a line's end, spaces and tabs at a
line's end, and blank lines before the first and after the last line of text. Indentation,
inner runs of white space, tabs, no-break and every other space are text.

The cross-reader's inputs, each one a pair of notes that the collapsing reader folded into
one claim citing both, or a note it refused. Each pair stays two claims, each with its own
citation, through a tidy, an export and an import. Made-up notes."""

import io
import itertools

import pytest

from cairn.arrange import repeat_groups, tidy_page
from cairn.compiler import (
    CandidatePage,
    Draft,
    DraftStats,
    compile_note,
    provenance_gate,
    render_body,
)
from cairn.gate import lint_gate, verbatim_gate, whole
from cairn.memory import Memory
from cairn.spans import chunk
from cairn.store import Claim, Store

PAIRS = {
    "links with backticks in their destinations": (
        "[x](a`) `a  b.txt` [y](b`)",
        "[x](a`) `a b.txt` [y](b`)",
    ),
    "a lazy line after a quote": ("> Stray `\nx  y` z` q``w``", "> Stray `\nx y` z` q``w``"),
    "nested lists indented otherwise": ("- A\n  - B\n    - C", "- A\n  - B\n  - C"),
    "prose wrapped otherwise": ("Ada keeps the log\nnightly.", "Ada keeps the log nightly."),
    "a no-break space": ("Ada keeps the\u00a0log.", "Ada keeps the log."),
    "indentation": ("  Ada keeps the log.", "Ada keeps the log."),
}


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


def filed(store: Store, first: str, second: str):
    project = store.create_project("alpha")
    memory = Memory(store, lint_gate)
    for text in (first, second):
        answer = memory.note("alpha", text, title="Files", topic="Files")
        assert answer["status"] == "saved", answer["violations"]
    return project, store.get_page(project.id, "files")


def two_claims(store: Store, project_id: str, first: str, second: str) -> None:
    """Each claim is the text of every passage it cites, and the passage that tells the two
    notes apart (the last of each) is cited by a claim of its own."""
    page = store.get_page(project_id, "files")
    spans = store.get_spans([i for claim in page.claims for i in claim.span_ids])
    for claim in page.claims:
        assert {spans[i].text for i in claim.span_ids} == {claim.text}
    last = [chunk("n", text)[-1].text for text in (first, second)]
    assert last[0] != last[1]
    citing = [[c for c in page.claims if c.text == text] for text in last]
    assert [len(found) for found in citing] == [1, 1]
    assert [len(found[0].span_ids) for found in citing] == [1, 1]


@pytest.mark.parametrize("name", list(PAIRS))
def test_a_pair_that_differs_by_white_space_stays_two_claims_through_tidy_and_import(
    store, tmp_path, name
):
    first, second = PAIRS[name]
    project, _ = filed(store, first, second)
    assert tidy_page(store, project.id, "files").status in ("arranged", "unchanged")
    two_claims(store, project.id, first, second)
    archive = io.BytesIO()
    store.export(archive)
    target = Store(tmp_path / "imported.db")
    try:
        target.import_export(io.BytesIO(archive.getvalue()))
        two_claims(target, target.get_project("alpha").id, first, second)
    finally:
        target.close()


@pytest.mark.parametrize("name", list(PAIRS))
def test_a_fold_of_such_a_pair_is_refused_by_the_gate(name):
    first, second = PAIRS[name]
    one, two = chunk("n1", first)[0], chunk("n2", second)[0]
    spans = {one.id: one, two.id: two}
    fold = Claim(id="cl_0000000000000001", text=first, span_ids=(one.id, two.id))
    assert not whole(fold, spans)
    page = CandidatePage(
        slug="files", title="Files", body=render_body("Files", [fold], spans), claims=(fold,)
    )
    assert not verbatim_gate(page, spans).passed


def test_an_escaped_backtick_does_not_get_a_note_refused(store):
    """The cross-reader's finding 4: a literal backtick before code-like text, under a topic."""
    store.create_project("alpha")
    text = "Keep \\`a  b.txt` here."
    answer = Memory(store, lint_gate).note("alpha", text, title="Ops", topic="Ops")
    assert (answer["status"], answer["violations"]) == ("saved", [])
    page = store.get_page(store.get_project("alpha").id, answer["page"])
    assert [c.text for c in page.claims] == [text]


# The rule, stated directly

SAME = {
    "a CR before each line feed": ("Ada.\r\nBrook.", "Ada.\nBrook."),
    "spaces and tabs at a line's end": ("Ada. \t\nBrook.  ", "Ada.\nBrook."),
    "blank lines around the text": ("\n  \nAda.\nBrook.\n\t\n", "Ada.\nBrook."),
    "code with spaces at a line's end": ("```\nrun()   \n```", "```\nrun()\n```"),
}
OTHER = {
    "a line break for a space": ("Ada keeps\nthe log.", "Ada keeps the log."),
    "two spaces for one": ("Ada keeps  the log.", "Ada keeps the log."),
    "a tab for a space": ("Ada keeps\tthe log.", "Ada keeps the log."),
    "a no-break space for a space": ("Ada keeps\u00a0the log.", "Ada keeps the log."),
    "an em space for a space": ("Ada keeps\u2003the log.", "Ada keeps the log."),
    "indentation added": ("  Ada keeps the log.", "Ada keeps the log."),
    "a space before the text": (" X.", "X."),
    "a list item rewrapped": ("- Install Docker\n  and uv", "- Install Docker and uv"),
    "a list nested otherwise": ("- A\n  - B\n    - C", "- A\n  - B\n  - C"),
    "a blank line inside": ("Ada.\n\nBrook.", "Ada.\nBrook."),
    "a CR inside a line": ("Ada.\rBrook.", "Ada. Brook."),
    "code indented otherwise": ("```\n  run()\n```", "```\nrun()\n```"),
    "inline code spaced otherwise": ("Keep `a  b.txt` here.", "Keep `a b.txt` here."),
}


def same(first: str, second: str) -> tuple[bool, bool]:
    """(whether a claim of the first text is the whole of a passage of the second, whether the
    repeat key folds the two): both must answer the rule."""
    span = chunk("n2", second)[0].model_copy(update={"text": second})
    claim = Claim(id="cl_0000000000000001", text=first, span_ids=(span.id,))
    claims = [claim, Claim(id="cl_0000000000000002", text=second, span_ids=(span.id,))]
    return whole(claim, {span.id: span}), repeat_groups(claims, {}) == [[1, 2]]


@pytest.mark.parametrize("name", list(SAME))
def test_what_the_rule_ignores_is_the_same(name):
    assert same(*SAME[name]) == (True, True)


@pytest.mark.parametrize("name", list(OTHER))
def test_every_other_white_space_is_text(name):
    first, second = OTHER[name]
    assert same(first, second) == (False, False)
    assert same(second, first) == (False, False)


EDITS = [" ", "  ", "\t", "\n", "\u00a0", "\u2003", "\u3000", "\u200b", "\r"]
TEXTS = [
    "Ada keeps the log.",
    "- Ada keeps\n  - the log",
    "> Ada keeps\n> the `log  file`.",
    "Keep `a b.txt` [x](a`) here.",
    "```\nrun()\n```",
    "| a | b |\n|---|---|",
]


def one_edit(text: str):
    """Every text that differs from `text` by one white-space edit inside its text: a white
    space character inserted at any place between two of its characters, removed, or
    replaced by another. The ends are left out: what the rule ignores lives there."""
    for at, edit in itertools.product(range(1, len(text)), EDITS):
        yield text[:at] + edit + text[at:]
    for at, char in enumerate(text):
        if char in " \t\n\u00a0" and 0 < at < len(text) - 1:
            yield text[:at] + text[at + 1 :]
            for edit in EDITS:
                if edit != char:
                    yield text[:at] + edit + text[at + 1 :]


def ignored(first: str, second: str) -> bool:
    """The rule, written out here on its own: the same rows once each loses a CR and the
    spaces and tabs at its end, with the blank rows at the two ends dropped."""

    def rows(text: str) -> list[str]:
        out = [row.rstrip(" \t\r") for row in text.split("\n")]
        while out and not out[0]:
            out.pop(0)
        while out and not out[-1]:
            out.pop()
        return out

    return rows(first) == rows(second)


@pytest.mark.parametrize("text", TEXTS)
def test_one_white_space_edit_in_any_place_never_folds(text):
    asked = 0
    for edited in one_edit(text):
        if ignored(edited, text):
            continue  # a space at a line's end: what the rule ignores
        asked += 1
        assert same(edited, text) == (False, False), repr(edited)
    assert asked > 4 * len(text)


# What a gate accepts, the import accepts (the cross-reader's item 6)


def reused(title: str, spans) -> Draft:
    """A composer, as LLMComposer (the evaluation script's alone) may answer: `X.` and ` X.`,
    both citing the one passage."""
    (span,) = spans
    claims = (
        Claim(id="cl_00000000000000a1", text=span.text, span_ids=(span.id,)),
        Claim(id="cl_00000000000000a2", text=f" {span.text}", span_ids=(span.id,)),
    )
    return Draft(claims=claims, stats=DraftStats(composer="reused"))


def test_a_draft_that_copies_one_passage_twice_with_a_space_is_refused(store):
    project = store.create_project("alpha")
    note, _ = store.add_note(project.id, "X.", title="Files")
    result = compile_note(store, note, compose=reused)
    assert result.status == "rejected"
    assert any(
        v.startswith("grounding.not_verbatim: cl_00000000000000a2") for v in result.gate.violations
    )
    assert store.get_page(project.id, "files") is None


def again_with(suffix: str, copies: int = 1):
    """A composer, as LLMComposer may answer (its ids are a hash of text and citations, so
    claims that differ only by spaces at a line's end are kept): the passage, then `copies`
    more claims of it, each with `suffix` added once more."""

    def compose(title: str, spans) -> Draft:
        (span,) = spans
        claims = tuple(
            Claim(id=f"cl_00000000000000b{n}", text=span.text + suffix * n, span_ids=(span.id,))
            for n in range(copies + 1)
        )
        return Draft(claims=claims, stats=DraftStats(composer="again"))

    return compose


@pytest.mark.parametrize(("suffix", "copies"), [(" ", 1), ("\t", 1), (" ", 2), ("", 2)], ids=repr)
def test_a_draft_the_import_would_refuse_is_refused_by_the_gate(store, suffix, copies):
    """Each claim is the whole passage, under the rule; together they hold more than twice
    its text, the import's bound (store.portable.CLAIM_TEXT_FACTOR): the gate refuses the
    draft, so no export holds a version its own import refuses."""
    project = store.create_project("alpha")
    note, _ = store.add_note(project.id, "X.", title="Files")
    result = compile_note(store, note, compose=again_with(suffix, copies))
    assert result.status == "rejected"
    assert [v.split(":")[0] for v in result.gate.violations] == ["grounding.passage_repeated"]
    assert "X." not in result.gate.violations[0]
    assert store.get_page(project.id, "files") is None


def test_a_passage_copied_twice_exactly_is_what_the_import_takes_and_the_gate_takes(
    store, tmp_path
):
    project = store.create_project("alpha")
    note, _ = store.add_note(project.id, "X.", title="Files")
    assert compile_note(store, note, compose=again_with("", 1)).status == "saved"
    archive = io.BytesIO()
    store.export(archive)
    target = Store(tmp_path / "imported.db")
    try:
        target.import_export(io.BytesIO(archive.getvalue()))
        page = target.get_page(target.get_project("alpha").id, "files")
        assert [c.text for c in page.claims] == ["X.", "X."]
    finally:
        target.close()


def test_the_default_gate_finds_a_claim_in_the_body_as_it_is_written():
    """provenance_gate, serve's gate for its extractive composer: a claim is in the body when
    its text is, not its words with their white space collapsed."""
    (span,) = chunk("n1", "Ada keeps  the log.")
    claim = Claim(id="cl_0000000000000001", text=span.text, span_ids=(span.id,))
    body = render_body("Files", [claim], {span.id: span}).replace("keeps  the", "keeps the")
    page = CandidatePage(slug="files", title="Files", body=body, claims=(claim,))
    codes = [v.split(":")[0] for v in provenance_gate(page, {span.id: span}).violations]
    assert "provenance.claim_not_in_body" in codes
