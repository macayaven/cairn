"""A claim that opens a code fence it never closes never swallows what follows (review 7.2).

The body closes such a claim's fence where the claim ends, so the next claim is never read as
code, and the gate a page is tidied or arranged with accepts what the gate a note is saved
with accepted: a page that holds such a claim is tidied and arranged again. Made-up notes."""

import html
import io
import json
import re

import pytest

from cairn.arrange import arrange_page, tidy_page
from cairn.compiler import CandidatePage, compile_note, render_body
from cairn.gate import lint_gate, verbatim_gate
from cairn.llm import FakeModel, Reply
from cairn.memory import Memory
from cairn.spans import chunk
from cairn.store import Claim, Store
from cairn.web.markdown import render

OPEN = "```python\nx = 1"
CRITICAL = "CRITICAL: Always run migrations with --dry-run first."


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "cairn.db")
    yield store
    store.close()


@pytest.fixture
def memory(store):
    store.create_project("alpha")
    return Memory(store, lint_gate)


def after_the_claims(body: str) -> list[tuple[str, str]]:
    """The body's blocks as cairn reads a note: (kind, text) of each span after the title."""
    return [(span.kind, span.text) for span in chunk("body", body)[1:]]


def outside_code(body: str, text: str) -> bool:
    """Whether CommonMark (the web view's renderer) shows `text` as a paragraph of its own,
    not inside a code block."""
    shown = str(render(body))
    return f"<p>{text}</p>" in shown and not re.search(rf"<code[^>]*>[^<]*{re.escape(text)}", shown)


# The reviewer's probes, on the repository's fixtures


def test_a_note_after_an_open_fence_is_not_code_in_the_body(memory):
    first = memory.note("alpha", OPEN, title="Runbook")
    second = memory.note("alpha", CRITICAL, title="Runbook", topic="Runbook")
    assert first["status"] == second["status"] == "saved"
    body = memory.page("alpha", first["page"])["body"]
    assert ("paragraph", CRITICAL) in after_the_claims(body)
    assert outside_code(body, CRITICAL)


def test_a_page_with_an_open_fence_and_a_repeat_is_tidied(memory, store):
    first = memory.note("alpha", OPEN, title="Runbook")
    memory.note("alpha", CRITICAL, title="Runbook", topic="Runbook")
    memory.note("alpha", CRITICAL, title="Runbook", topic="Runbook", source_ref="s-repeat")
    project = store.get_project("alpha")
    tidied = tidy_page(store, project.id, first["page"])
    assert (tidied.status, tidied.merged, tidied.problem) == ("arranged", 1, None)
    page = store.get_page(project.id, first["page"])
    assert [claim.text for claim in page.claims] == [OPEN, CRITICAL]


# The closer: the opener's own run, where the opener stands


@pytest.mark.parametrize(
    "opened",
    [
        "```python\nx = 1",
        "~~~\nx = 1",
        "`````sh\nrun ```\nstill code",  # a shorter run inside does not close it
        "  ```\nindented by two",
        "````md\n```\ninner\n```",  # an inner fence of fewer backticks closes nothing
    ],
)
def test_the_body_closes_a_claims_open_fence_where_the_claim_ends(opened):
    spans = chunk("n1", opened) + chunk("n2", CRITICAL)
    claims = [Claim(id=f"cl_{n}", text=s.text, span_ids=(s.id,)) for n, s in enumerate(spans)]
    body = render_body("Runbook", claims, {s.id: s for s in spans})
    assert after_the_claims(body)[-1] == ("paragraph", CRITICAL)
    assert outside_code(body, CRITICAL)
    assert body.startswith(f"# Runbook\n\n{opened}\n")  # the claim's own text, untouched


@pytest.mark.parametrize(
    "item",
    [
        "- Steps:\n\n  ```sh\n  make deploy",
        "- ```sh\n  make deploy",  # opened on the item's marker line
        "12. ~~~\n    make deploy",
    ],
)
def test_a_fence_left_open_in_a_list_item_is_closed_inside_the_item(item):
    spans = chunk("n1", item) + chunk("n2", CRITICAL)
    claims = [Claim(id=f"cl_{n}", text=s.text, span_ids=(s.id,)) for n, s in enumerate(spans)]
    body = render_body("Runbook", claims, {s.id: s for s in spans})
    assert after_the_claims(body)[-1] == ("paragraph", CRITICAL)
    assert outside_code(body, CRITICAL)


def test_a_closed_fence_and_inline_code_get_no_closer():
    for text in ("```\nclosed\n```", "```inline``` code", "No fence at all."):
        spans = chunk("n1", text)
        claims = [Claim(id="cl_1", text=spans[0].text, span_ids=(spans[0].id,))]
        assert render_body("T", claims, {spans[0].id: spans[0]}) == f"# T\n\n{text}\n"


# The gates agree


def test_the_verbatim_gate_accepts_what_the_lint_gate_saved(memory, store):
    first = memory.note("alpha", OPEN, title="Runbook")
    memory.note("alpha", CRITICAL, title="Runbook", topic="Runbook")
    project = store.get_project("alpha")
    page = store.get_page(project.id, first["page"])
    spans = store.get_spans([i for claim in page.claims for i in claim.span_ids])
    candidate = CandidatePage(
        slug=page.slug,
        title=page.title,
        body=render_body(page.title, page.claims, spans),
        claims=page.claims,
    )
    assert lint_gate(candidate, spans).passed
    assert verbatim_gate(candidate, spans).violations == []


# A page saved before this change, its body stored with the fence open


def page_saved_before(store):
    project = store.create_project("alpha")
    spans = chunk("x", OPEN) + chunk("y", CRITICAL)
    first, _ = store.add_note(project.id, OPEN, title="Runbook")
    second, _ = store.add_note(project.id, CRITICAL, title="Runbook", topic="Runbook")
    second_again, _ = store.add_note(
        project.id, CRITICAL, title="Runbook", topic="Runbook", source_ref="s-repeat"
    )
    assert spans  # the notes' own spans are the store's
    claims = []
    for note in (first, second, second_again):
        (span,) = store.note_spans(note.id)
        claims.append(Claim(id="cl_" + span.id[3:], text=span.text, span_ids=(span.id,)))
    old_body = "# Runbook\n\n" + "\n\n".join(claim.text for claim in claims) + "\n"
    store.save_page(project.id, "runbook", "Runbook", old_body, claims, expected_version=0)
    return project


def test_a_page_saved_with_its_fence_open_is_tidied_again(store):
    project = page_saved_before(store)
    tidied = tidy_page(store, project.id, "runbook")
    assert (tidied.status, tidied.merged) == ("arranged", 1)
    body = store.get_page(project.id, "runbook").body
    assert outside_code(body, CRITICAL)


def test_a_page_saved_with_its_fence_open_is_arranged_again(store):
    project = page_saved_before(store)
    ids = [claim.id for claim in store.get_page(project.id, "runbook").claims]
    answer = Reply(text=json.dumps({"order": [ids[1], ids[0]], "same": [[ids[1], ids[2]]]}))
    arranged = arrange_page(store, project.id, "runbook", FakeModel([answer]))
    assert (arranged.status, arranged.merged, arranged.problem) == ("arranged", 1, None)


def test_a_note_compiled_by_the_default_gate_after_an_open_fence_is_saved(store):
    project = store.create_project("alpha")
    first, _ = store.add_note(project.id, OPEN, title="Runbook")
    second, _ = store.add_note(project.id, CRITICAL, title="Runbook", topic="Runbook")
    assert compile_note(store, first).status == "saved"
    result = compile_note(store, second)
    assert result.status == "saved"
    assert outside_code(result.candidate.body, CRITICAL)


# An archive written before this change holds the body with the fence open


def archive_of(tmp_path, text: str, headings: str) -> io.BytesIO:
    source = Store(tmp_path / "before.db")
    try:
        project = source.create_project("alpha")
        note, _ = source.add_note(project.id, text, title="Net")
        (span,) = [s for s in source.note_spans(note.id) if s.kind == "code"]
        claims = [Claim(id="cl_" + span.id[3:], text=span.text, span_ids=(span.id,))]
        body = f"# Net\n\n{headings}{span.text}\n"  # as render_body wrote it before 7.2
        source.save_page(project.id, "net", "Net", body, claims, expected_version=0)
        archive = io.BytesIO()
        source.export(archive)
        archive.seek(0)
        return archive
    finally:
        source.close()


def test_an_archive_of_a_page_with_an_open_fence_and_no_heading_imports(tmp_path, store):
    store.import_export(archive_of(tmp_path, "```sh\nopen 80", ""))
    body = store.get_page(store.get_project("alpha").id, "net").body
    assert body == "# Net\n\n```sh\nopen 80\n```\n"


@pytest.mark.xfail(
    strict=True,
    reason="store.portable._as_rendered (not this change's file) must also accept the body"
    " render_body wrote before review 7.2, its fences left open",
)
def test_an_archive_of_a_sectioned_page_with_an_open_fence_imports(tmp_path, store):
    text = "## Ports\n\n### TCP\n\n```sh\nopen 80"
    store.import_export(archive_of(tmp_path, text, "## Ports\n\n### TCP\n\n"))
    assert store.get_page(store.get_project("alpha").id, "net").body.endswith("```\n")


# On the web page


def test_the_web_page_shows_the_next_claim_as_text_and_the_body_closed(tmp_path):
    from starlette.testclient import TestClient

    from cairn.server import create_app

    base, same_origin = "http://127.0.0.1:8765", {"sec-fetch-site": "same-origin"}
    store = Store(tmp_path / "web.db")
    key = store.create_key("laptop")
    memory = Memory(store, lint_gate)
    store.create_project("alpha")
    slug = memory.note("alpha", OPEN, title="Runbook")["page"]
    memory.note("alpha", CRITICAL, title="Runbook", topic="Runbook")
    try:
        with TestClient(create_app(store, public_url=base), base_url=base) as client:
            assert client.post("/login", data={"key": key}, headers=same_origin).status_code < 400
            shown = client.get(f"/b/alpha/p/{slug}").text
    finally:
        store.close()
    claims = re.findall(r'<div class="claim-text md">(.*?)</div>', shown, re.S)
    assert claims[-1].strip() == f"<p>{CRITICAL}</p>"
    (body,) = re.findall(r"<pre>([^<]*)</pre>", shown)
    assert html.unescape(body) == f"# Runbook\n\n{OPEN}\n```\n\n{CRITICAL}\n"
