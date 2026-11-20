"""A code fence that opens only where the claims are put together is closed where its claim
ends (third report, must-fix 4).

A claim's blocks depend on what the body writes before it: after a list item, a passage that
alone is indented text may open a fence inside the item, and the claim after it reads as
code. The body is parsed whole, and a fence that runs from one claim into the next gets a
closer after the claim it starts in. Each body here is read by the Markdown parser, as the
package reads it and with raw HTML on. The bodies "as 23c24229 wrote them" were written by
that commit, through Memory.note, and copied here. Made-up notes."""

import io
import json
import zipfile

import pytest
from markdown_it import MarkdownIt

import cairn.compiler
import cairn.spans
from cairn.arrange import arrange_page, tidy_page
from cairn.compiler import (
    CandidatePage,
    Draft,
    DraftStats,
    assembled,
    compile_note,
    previous_render_body,
    provenance_gate,
    render_body,
    render_body_before_review_four,
    render_body_before_review_three,
    render_body_before_review_two,
    render_body_with_fences_open,
)
from cairn.gate import lint_gate, strict_lint_gate, unclaimed_violations, verbatim_gate
from cairn.llm import FakeModel, Reply
from cairn.memory import Memory
from cairn.spans import chunk, claim_id
from cairn.store import Claim, Store

READERS = {
    "raw HTML off, as the web view reads": MarkdownIt("commonmark", {"html": False}),
    "raw HTML on": MarkdownIt("commonmark", {"html": True}),
}
REVIEWER = ["- item", "    ~~~\n    sample", "  After."]  # the third report's three notes
AS_23C24229_WROTE_IT = "# Ops\n\n- item\n\n    ~~~\n    sample\n\n  After.\n"
CLOSED = "# Ops\n\n- item\n\n    ~~~\n    sample\n    ~~~\n\n  After.\n"
AT_THE_MARGIN = "# Ops\n\n- item\n\n    ~~~\n    sample\n~~~\n\n  After.\n"  # a closer that is none


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


@pytest.fixture
def memory(store):
    store.create_project("alpha")
    return Memory(store, lint_gate)


def prose(body: str, text: str) -> bool:
    """Whether every reader shows `text` as a paragraph's whole text, and none shows it in a
    fence or an indented code block."""
    for reader in READERS.values():
        tokens = reader.parse(body)
        paragraphs = [
            tokens[n + 1].content
            for n, token in enumerate(tokens)
            if token.type == "paragraph_open"
        ]
        code = [token.content for token in tokens if token.type in ("fence", "code_block")]
        if text not in paragraphs or any(text in block for block in code):
            return False
    return True


def headings(body: str) -> list[str]:
    tokens = READERS["raw HTML on"].parse(body)
    return [tokens[n + 1].content for n, t in enumerate(tokens) if t.type == "heading_open"]


def filed(memory: Memory, store: Store, texts: list[str]):
    answers = [memory.note("alpha", text, title="Ops", topic="Ops") for text in texts]
    assert [answer["status"] for answer in answers] == ["saved"] * len(texts)
    return store.get_page(store.get_project("alpha").id, answers[0]["page"])


def made(texts: list[str]) -> tuple[list[Claim], dict]:
    """The claims and passages of notes given one by one, as an extractive compile makes them."""
    spans = {s.id: s for n, text in enumerate(texts) for s in chunk(f"n{n}", text)}
    claims = [
        Claim(id=claim_id(i), text=s.text, span_ids=(i,))
        for i, s in spans.items()
        if s.kind != "heading"
    ]
    return claims, spans


def exported(store: Store) -> bytes:
    archive = io.BytesIO()
    store.export(archive)
    return archive.getvalue()


def imported(store: Store, tmp_path) -> Store:
    target = Store(tmp_path / "imported.db")
    target.import_export(io.BytesIO(exported(store)))
    return target


# The reviewer's three notes


def test_the_reviewers_third_note_is_a_paragraph_of_the_body_and_in_no_code(memory, store):
    assert not prose(AS_23C24229_WROTE_IT, "After.")  # the fault, as the parser reads it
    page = filed(memory, store, REVIEWER)
    assert [claim.text for claim in page.claims] == REVIEWER  # no claim's text is changed
    assert page.body == CLOSED
    assert prose(page.body, "After.")
    assert memory.page("alpha", page.slug)["body"] == CLOSED  # as the API gives it


def test_the_page_is_tidied_exported_and_imported_with_the_third_note_as_prose(
    memory, store, tmp_path
):
    page = filed(memory, store, REVIEWER)
    again = memory.note("alpha", "  After.", title="Ops", topic="Ops", source_ref="s-repeat")
    assert again["status"] == "saved"
    project = store.get_project("alpha")
    tidied = tidy_page(store, project.id, page.slug)  # the repeat is folded: a version is saved
    assert (tidied.status, tidied.merged, tidied.problem) == ("arranged", 1, None)
    page = store.get_page(project.id, page.slug)
    assert page.body == CLOSED
    target = imported(store, tmp_path)
    try:
        there = target.get_page(target.get_project("alpha").id, page.slug)
        assert (there.version, there.body) == (page.version, CLOSED)
        assert [claim.text for claim in there.claims] == REVIEWER
        assert prose(there.body, "After.")
    finally:
        target.close()


# The neighbours: (the notes, the body 23c24229 wrote, the body written now)

NEIGHBOURS = {
    "a quote before: the passage is indented text, as it was": (
        ["> text", "    ~~~\n    sample", "  After."],
        "# Ops\n\n> text\n\n    ~~~\n    sample\n\n  After.\n",
        "# Ops\n\n> text\n\n    ~~~\n    sample\n\n  After.\n",
    ),
    "a nested list": (
        ["- a\n  - b", "      ~~~\n      sample", "    After."],
        "# Ops\n\n- a\n  - b\n\n      ~~~\n      sample\n\n    After.\n",
        "# Ops\n\n- a\n  - b\n\n      ~~~\n      sample\n      ~~~\n\n    After.\n",
    ),
    "a backtick fence": (
        ["- item", "    ```\n    sample", "  After."],
        "# Ops\n\n- item\n\n    ```\n    sample\n\n  After.\n",
        "# Ops\n\n- item\n\n    ```\n    sample\n    ```\n\n  After.\n",
    ),
    "an ordered list": (
        ["1. item", "     ~~~\n     sample", "   After."],
        "# Ops\n\n1. item\n\n     ~~~\n     sample\n\n   After.\n",
        "# Ops\n\n1. item\n\n     ~~~\n     sample\n     ~~~\n\n   After.\n",
    ),
    "a tab before the run": (
        ["- item", "\t~~~\n\tsample", "  After."],
        "# Ops\n\n- item\n\n\t~~~\n\tsample\n\n  After.\n",
        "# Ops\n\n- item\n\n\t~~~\n\tsample\n\t~~~\n\n  After.\n",
    ),
    "the run on an indented item's marker line": (
        ["- item", "    - ~~~\n      sample", "      After."],
        "# Ops\n\n- item\n\n    - ~~~\n      sample\n\n      After.\n",
        "# Ops\n\n- item\n\n    - ~~~\n      sample\n      ~~~\n\n      After.\n",
    ),
    "the list above a heading the body writes: indented text, as it was": (
        ["- item", "## Ports\n\n    ~~~\n    sample", "## Ports\n\n  After."],
        "# Ops\n\n- item\n\n## Ports\n\n    ~~~\n    sample\n\n  After.\n",
        "# Ops\n\n- item\n\n## Ports\n\n    ~~~\n    sample\n\n  After.\n",
    ),
    "all three under a heading the body writes": (
        ["## Ports\n\n- item", "## Ports\n\n    ~~~\n    sample", "## Ports\n\n  After."],
        "# Ops\n\n## Ports\n\n- item\n\n    ~~~\n    sample\n\n  After.\n",
        "# Ops\n\n## Ports\n\n- item\n\n    ~~~\n    sample\n    ~~~\n\n  After.\n",
    ),
    "a claim's own closing line, read as an opener once its list has ended": (
        ["1. item", "   ~~~\n   x\n~~~", "After."],
        "# Ops\n\n1. item\n\n   ~~~\n   x\n~~~\n\nAfter.\n",
        "# Ops\n\n1. item\n\n   ~~~\n   x\n~~~\n~~~\n\nAfter.\n",
    ),
    "that claim last before a heading the body writes": (
        ["1. item", "   ~~~\n   x\n~~~", "## Ports\n\nAfter."],
        "# Ops\n\n1. item\n\n   ~~~\n   x\n~~~\n\n## Ports\n\nAfter.\n",
        "# Ops\n\n1. item\n\n   ~~~\n   x\n~~~\n~~~\n\n## Ports\n\nAfter.\n",
    ),
    "the closer the body writes after a claim alone, read as an opener": (
        ["- item", "  ~~~\n  x\n  \t~~~", "  After."],
        "# Ops\n\n- item\n\n  ~~~\n  x\n  \t~~~\n  ~~~\n\n  After.\n",
        "# Ops\n\n- item\n\n  ~~~\n  x\n  \t~~~\n  ~~~\n  ~~~\n\n  After.\n",
    ),
    "the next claim a fence whose first line closed it": (
        ["- item", "    ~~~\n    sample", "  ~~~\n  thistle\n  ~~~", "After."],
        "# Ops\n\n- item\n\n    ~~~\n    sample\n\n  ~~~\n  thistle\n  ~~~\n\nAfter.\n",
        "# Ops\n\n- item\n\n    ~~~\n    sample\n    ~~~\n\n  ~~~\n  thistle\n  ~~~\n\nAfter.\n",
    ),
    "the middle claim last on the page: nothing follows, as it was": (
        ["- item", "    ~~~\n    sample"],
        "# Ops\n\n- item\n\n    ~~~\n    sample\n",
        "# Ops\n\n- item\n\n    ~~~\n    sample\n",
    ),
}


@pytest.mark.parametrize("name", list(NEIGHBOURS))
def test_the_claim_after_is_prose_and_a_body_with_no_such_fence_is_as_it_was(memory, store, name):
    texts, before, now = NEIGHBOURS[name]
    page = filed(memory, store, texts)
    assert page.body == now
    assert (now == before) == ("as it was" in name)
    assert prose(now, "After.") or "After." not in now
    if "thistle" in now:  # a claim that is a fence reads as one, not as a paragraph in another's
        assert prose(before, "thistle") and not prose(now, "thistle")
    elif now != before:
        assert not prose(before, "After.")  # the fault was there
    if "## Ports" in now:
        assert headings(now) == ["Ops", "Ports"]


@pytest.mark.parametrize("name", list(NEIGHBOURS))
def test_the_renderer_kept_for_the_import_writes_what_23c24229_wrote(name):
    texts, before, _ = NEIGHBOURS[name]
    claims, spans = made(texts)
    assert render_body_before_review_four("Ops", claims, spans) == before


def typed_with_a_cr(title: str, spans) -> Draft:
    """A composer that ends each claim with a CR: the whole passage still, for the gate."""
    claims = tuple(Claim(id=claim_id(s.id), text=s.text + "\r", span_ids=(s.id,)) for s in spans)
    return Draft(claims=claims, stats=DraftStats(composer="typed"))


def test_a_claim_a_composer_ends_with_a_cr_does_not_leave_its_fence_open(store):
    """Found by the generated test: asked of such a claim alone, the parser's lines are
    counted one too many and its open fence gets no closer; the body's reading closes it."""
    project = store.create_project("alpha")
    for text in ("```\nopen", "After."):
        note, _ = store.add_note(project.id, text, title="Ops", topic="Ops")
        result = compile_note(store, note, compose=typed_with_a_cr)  # the verbatim gate
        assert result.status == "saved", result.gate.violations
    page = store.get_page(project.id, "ops")
    assert [claim.text for claim in page.claims] == ["```\nopen\r", "After.\r"]
    assert page.body == "# Ops\n\n```\nopen\r\n```\n\nAfter.\r\n"
    assert prose(page.body, "After.")
    before = render_body_before_review_four("Ops", page.claims, {})
    assert before == "# Ops\n\n```\nopen\r\n\nAfter.\r\n" and not prose(before, "After.")


def test_a_blank_line_a_cr_makes_is_counted_as_the_parser_counts_it():
    """A claim typed with CR CR LF holds a blank line for the parser, which "\\n" alone does
    not show: the claims after it are still found on their own lines. Here the fence ends
    with its list, before the next claim, so nothing is written; then it does not, and the
    closer comes after the claim it starts in."""
    spans = {s.id: s for n, t in enumerate(["x\ny", *REVIEWER]) for s in chunk(f"n{n}", t)}
    typed = dict(zip(spans, ["x\r\r\ny", "- item", "    ~~~\n    sample", "After."], strict=True))
    claims = [Claim(id=claim_id(i), text=text, span_ids=(i,)) for i, text in typed.items()]
    apart = "# Ops\n\nx\r\r\ny\n\n- item\n\n    ~~~\n    sample\n\nAfter.\n"
    assert render_body("Ops", claims, spans) == apart
    claims[-1] = claims[-1].model_copy(update={"text": "  After."})
    closed = apart.replace("sample\n\nAfter.", "sample\n    ~~~\n\n  After.")
    assert render_body("Ops", claims, spans) == closed and prose(closed, "After.")


# What must not change: a page 23c24229 saved, and an archive it exported


def filed_by_23c24229(memory: Memory, store: Store, monkeypatch, repeat: bool):
    """The page of the reviewer's notes as 23c24229 saved it, compiled and gated with its
    renderer; with `repeat`, the third note sent once more, so a tidy has a claim to fold."""
    with monkeypatch.context() as patch:
        patch.setattr(cairn.compiler, "render_body", render_body_before_review_four)
        page = filed(memory, store, REVIEWER)
        if repeat:
            again = memory.note("alpha", "  After.", title="Ops", topic="Ops", source_ref="s-2")
            assert again["status"] == "saved"
    page = store.get_page(page.project_id, page.slug)
    assert page.body == AS_23C24229_WROTE_IT + ("\n  After.\n" if repeat else "")
    return page


def test_a_page_23c24229_saved_with_the_fault_is_tidied(memory, store, monkeypatch):
    page = filed_by_23c24229(memory, store, monkeypatch, repeat=True)
    tidied = tidy_page(store, page.project_id, page.slug)
    assert (tidied.status, tidied.merged, tidied.problem) == ("arranged", 1, None)
    assert store.get_page(page.project_id, page.slug).body == CLOSED


def test_a_page_23c24229_saved_with_the_fault_is_arranged(memory, store, monkeypatch):
    page = filed_by_23c24229(memory, store, monkeypatch, repeat=True)
    answer = Reply(text=json.dumps({"order": [1, 2, 3], "same": [[3, 4]]}), prompt_tokens=9)
    arranged = arrange_page(store, page.project_id, page.slug, FakeModel([answer, answer]))
    assert (arranged.status, arranged.merged, arranged.problem) == ("arranged", 1, None)
    assert store.get_page(page.project_id, page.slug).body == CLOSED


def test_a_page_23c24229_saved_with_the_fault_takes_a_new_note(memory, store, monkeypatch):
    page = filed_by_23c24229(memory, store, monkeypatch, repeat=False)
    assert memory.page("alpha", page.slug)["body"] == CLOSED  # read: rendered, not as stored
    answer = memory.note("alpha", "More.", title="Ops", topic="Ops")
    assert (answer["status"], answer["page"]) == ("saved", page.slug)
    body = store.get_page(page.project_id, page.slug).body
    assert body == CLOSED + "\nMore.\n"
    assert prose(body, "After.") and prose(body, "More.")


def test_an_archive_23c24229_exported_with_the_fault_imports_and_is_stored_as_now(
    memory, store, monkeypatch, tmp_path
):
    page = filed_by_23c24229(memory, store, monkeypatch, repeat=False)
    assert not prose(page.body, "After.")
    with zipfile.ZipFile(io.BytesIO(exported(store))) as archive:
        (name,) = [n for n in archive.namelist() if n.endswith(f"pages/{page.slug}.md")]
        # a limit: until the page is saved again, the export holds its body as it is stored
        assert archive.read(name).decode() == AS_23C24229_WROTE_IT
    target = imported(store, tmp_path)
    try:
        project = target.get_project("alpha")
        there = target.get_page(project.id, page.slug)
        assert (there.version, there.body) == (page.version, CLOSED)
        answer = Memory(target, lint_gate).note("alpha", "More.", title="Ops", topic="Ops")
        assert answer["status"] == "saved"  # its next save
        assert target.get_page(project.id, page.slug).body == CLOSED + "\nMore.\n"
    finally:
        target.close()


# A page only 23c24229's renderer wrote so: the parser closes its first claim's fence (after a
# no-break space the line rules took the closer), and the body was not yet read whole.
ONLY_23C24229 = ["~~~\nsample\n\u00a0~~~", *REVIEWER]
ONLY_23C24229_WROTE = (
    "# Ops\n\n~~~\nsample\n\u00a0~~~\n~~~\n\n- item\n\n    ~~~\n    sample\n\n  After.\n"
)


def test_an_archive_of_a_body_only_23c24229_wrote_imports_and_is_stored_as_now(
    memory, store, monkeypatch, tmp_path
):
    """The reviewer's three notes alone are a body the renderer before the second report
    wrote too, so their archive would import without the new entry; this one would not."""
    with monkeypatch.context() as patch:
        patch.setattr(cairn.compiler, "render_body", render_body_before_review_four)
        page = filed(memory, store, ONLY_23C24229)
    assert page.body == ONLY_23C24229_WROTE and not prose(page.body, "After.")
    spans = store.get_spans([i for claim in page.claims for i in claim.span_ids])
    for earlier in (
        previous_render_body,
        render_body_with_fences_open,
        render_body_before_review_two,
        render_body_before_review_three,
        render_body,
    ):
        assert earlier("Ops", page.claims, spans) != page.body, earlier.__name__
    target = imported(store, tmp_path)
    try:
        there = target.get_page(target.get_project("alpha").id, page.slug)
        now = ONLY_23C24229_WROTE.replace("    sample\n", "    sample\n    ~~~\n")
        assert (there.version, there.body) == (page.version, now)
        assert prose(there.body, "After.")
    finally:
        target.close()


def test_an_archive_with_a_sentence_no_claim_holds_beside_the_fault_is_still_refused(
    memory, store, tmp_path
):
    page = filed(memory, store, REVIEWER)
    body = AS_23C24229_WROTE_IT + "\nNot a claim.\n"
    store.save_page(
        page.project_id, page.slug, "Ops", body, page.claims, expected_version=page.version
    )
    with pytest.raises(ValueError, match="not what cairn writes") as refused:
        imported(store, tmp_path).close()
    assert "Not a claim" not in str(refused.value)


# A forgotten note changes what stands before a claim: its page is written, never refused


def test_forgetting_the_note_between_closes_the_fence_it_brings_together(memory, store, tmp_path):
    texts = ["- item", "Between.", "    ~~~\n    sample", "  After."]
    answers = [memory.note("alpha", text, title="Ops", topic="Ops") for text in texts]
    project = store.get_project("alpha")
    slug = answers[0]["page"]
    # a paragraph ends the list, so the passage is indented text: the body 23c24229 wrote
    as_it_was = "# Ops\n\n- item\n\nBetween.\n\n    ~~~\n    sample\n\n  After.\n"
    assert store.get_page(project.id, slug).body == as_it_was
    store.forget_note(answers[1]["note_id"])
    page = store.get_page(project.id, slug)
    assert [claim.text for claim in page.claims] == REVIEWER
    assert page.body == CLOSED  # written by the erasure, which no gate reads
    target = imported(store, tmp_path)
    try:
        assert target.get_page(target.get_project("alpha").id, slug).body == CLOSED
    finally:
        target.close()


# A closer is proven by the parser, never assumed; a fence no closer holds in is refused


def no_closer(monkeypatch) -> None:
    """The parser's answer stood in for (no page is known whose fence the closer does not
    hold in): the closer is written at the margin, where it ends the list and opens a fence
    at the top, so the claim after it is code still."""
    assert not prose(AT_THE_MARGIN, "After.")
    monkeypatch.setattr(cairn.spans, "_closer", lambda opener, run: run)


def test_a_fence_no_closer_holds_in_is_written_as_it_is_and_every_gate_refuses_it(monkeypatch):
    no_closer(monkeypatch)
    claims, spans = made(REVIEWER)
    body = render_body("Ops", claims, spans)  # never raises: a reader is given the page
    assert body == AS_23C24229_WROTE_IT  # a closer the parser does not prove is not kept
    assert assembled("Ops", claims, spans) == (body, claims[1].id)
    page = CandidatePage(slug="ops", title="Ops", body=body, claims=claims)
    refusal = (
        f"compile.fence_left_open: {claims[1].id}: the passage leaves a code fence open that no"
        " closing line after it closes, so the claims after it would read as code"
    )
    assert unclaimed_violations(page, spans) == [refusal]
    for gate in (provenance_gate, lint_gate, strict_lint_gate, verbatim_gate):
        verdict = gate(page, spans)
        assert not verdict.passed and refusal in verdict.violations, gate.__name__
        assert not any("sample" in violation for violation in verdict.violations)


def test_a_fence_that_is_no_claims_is_refused_too_and_named_by_no_id(monkeypatch):
    """No line of a title or a heading opens a fence; were one to, the page is refused."""
    monkeypatch.setattr(cairn.compiler, "fences_kept_in", lambda blocks: (list(blocks), 0))
    claims, spans = made(["Ada keeps the log."])
    body, left = assembled("Ops", claims, spans)
    assert (body, left) == ("# Ops\n\nAda keeps the log.\n", "-")
    page = CandidatePage(slug="ops", title="Ops", body=body, claims=claims)
    (refusal,) = unclaimed_violations(page, spans)
    assert refusal.startswith("compile.fence_left_open: -: ")


def test_a_note_that_would_be_read_as_code_is_refused_and_nothing_is_saved(
    memory, store, monkeypatch
):
    page = filed(memory, store, REVIEWER[:2])
    no_closer(monkeypatch)
    answer = memory.note("alpha", "  After.", title="Ops", topic="Ops")
    assert answer["status"] == "rejected"
    assert answer["violations"] == [
        f"compile.fence_left_open: {page.claims[1].id}: the passage leaves a code fence open"
        " that no closing line after it closes, so the claims after it would read as code"
    ]
    assert store.get_page(page.project_id, page.slug) == page


def test_forgetting_a_note_is_not_refused_where_no_closer_holds_the_fence_in(
    memory, store, monkeypatch, tmp_path
):
    """The erasure writes the page no gate would save, and the workspace is still exported
    and restored: nothing that deletes is refused, and nothing it wrote is refused later."""
    texts = ["- item", "Between.", "    ~~~\n    sample", "  After."]
    answers = [memory.note("alpha", text, title="Ops", topic="Ops") for text in texts]
    no_closer(monkeypatch)
    store.forget_note(answers[1]["note_id"])
    page = store.get_page(store.get_project("alpha").id, answers[0]["page"])
    assert [claim.text for claim in page.claims] == REVIEWER
    assert page.body == AS_23C24229_WROTE_IT  # the note is gone; the page is as it can be
    target = imported(store, tmp_path)
    try:
        there = target.get_page(target.get_project("alpha").id, page.slug)
        assert (there.body, there.claims) == (page.body, page.claims)
    finally:
        target.close()


# Cost: how often the parser is asked


def parses(monkeypatch, run) -> int:
    """How many times `run` has the package's parser parse."""
    calls, parse = [], cairn.spans._PARSER.parse
    with monkeypatch.context() as patch:
        patch.setattr(
            cairn.spans._PARSER, "parse", lambda *a, **k: calls.append(1) or parse(*a, **k)
        )
        run()
    return len(calls)


PAGES_OF_200 = {
    # the notes: (parses by the renderer of 23c24229, parses now)
    "no backtick and no tilde": ([f"Ada keeps log {n}." for n in range(200)], 0, 0),
    "inline code in every claim": ([f"Ada keeps `log {n}`." for n in range(200)], 200, 201),
    "a closed fence in every claim": ([f"```\nlog {n}\n```" for n in range(200)], 200, 201),
    "66 times the reviewer's three notes": (
        [t for n in range(66) for t in ("- item", f"    ~~~\n    sample {n}", f"  After {n}.")],
        66,
        133,
    ),
    "one list item, then 199 indented fences, each closing or opening the next": (
        ["- item", *(f"    ~~~\n    sample {n}" for n in range(199))],
        199,
        398,
    ),
}


@pytest.mark.parametrize("name", list(PAGES_OF_200))
def test_the_parser_is_asked_once_of_the_body_and_once_more_for_each_closer(monkeypatch, name):
    texts, before, now = PAGES_OF_200[name]
    claims, spans = made(texts)
    assert len(claims) in (198, 200)
    assert parses(monkeypatch, lambda: render_body_before_review_four("Ops", claims, spans)) == (
        before
    )
    assert parses(monkeypatch, lambda: render_body("Ops", claims, spans)) == now


def test_a_gate_renders_once_when_the_body_is_as_written(monkeypatch):
    claims, spans = made([f"```\nlog {n}\n```" for n in range(200)])
    page = CandidatePage(
        slug="ops", title="Ops", body=render_body("Ops", claims, spans), claims=claims
    )
    # 200 claims alone and the body, for the rendering; 200 claims alone, for the gate's own ask
    assert parses(monkeypatch, lambda: unclaimed_violations(page, spans)) == 201 + 200
    other = page.model_copy(update={"body": page.body + "\nNot a claim.\n"})
    assert unclaimed_violations(other, spans)[0].startswith("provenance.unclaimed_line: -: line")
    # not as written: one more rendering, to find the line
    assert parses(monkeypatch, lambda: unclaimed_violations(other, spans)) == 2 * 201 + 200
    assert parses(monkeypatch, lambda: render_body("Ops", *made(REVIEWER))) == 3


# A stated limit, not this change's: a reader with raw HTML on


@pytest.mark.parametrize(
    "opener",
    ["<!-- kept", "<script>", "<pre>", "<?php", "<!DOCTYPE quill", "<![CDATA[ kept"],
    ids=["comment", "script", "pre", "processing", "declaration", "cdata"],
)
def test_an_html_block_a_note_leaves_open_holds_the_next_claim_for_a_reader_with_html_on(
    memory, store, opener
):
    """The package's parser has raw HTML off: for it the note is a paragraph and so is the
    claim after it, and no check of the body sees more. A reader that takes raw HTML reads
    every such block to the end of the body, the claims after it inside."""
    page = filed(memory, store, [opener, "After."])
    assert page.body == f"# Ops\n\n{opener}\n\nAfter.\n"
    off, on = (reader.parse(page.body) for reader in READERS.values())
    assert [t.content for t in off if t.type == "inline"] == ["Ops", opener, "After."]
    (held,) = [t.content for t in on if t.type == "html_block"]
    assert held == f"{opener}\n\nAfter.\n"
    assert [t.content for t in on if t.type == "inline"] == ["Ops"]
