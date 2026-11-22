"""An import takes a claim only when it is the whole of every passage it cites, by the verbatim
gate's own comparison (review blocker 2). A fold's passages share one heading path, and no two
claims of one version share an id. Every archive is built in the test, from made-up notes."""

import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from cairn.compiler import compile_note, render_body
from cairn.store import Claim, Store

APPROVED = "The Quill release is Approved.\n"
REJECTED = "The Quill release is Rejected.\n"
CODE = "```python\nif ready:\n    ship()\n```\n"


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


@pytest.fixture
def source(tmp_path):
    view = Store(tmp_path / "source.db")
    yield view
    view.close()


def filed(view: Store, *texts: str, topic: str = "Quill") -> tuple[str, str]:
    project = view.get_project("ops") or view.create_project("ops")
    slug = ""
    for text in texts:
        note, _ = view.add_note(project.id, text, title=topic, topic=topic)
        result = compile_note(view, note)
        assert result.status == "saved", result.gate
        slug = result.slug
    return project.id, slug


def rewritten(view: Store, project_id: str, slug: str, change) -> bytes:
    """The export of `view` with `change(claims)` applied to the page's latest version (a list
    of Claim, returned changed), and its body written again as cairn renders it, so only the
    claims are in question."""
    page = view.get_page(project_id, slug)
    spans = {s.id: s for note in view.list_notes(project_id) for s in view.note_spans(note.id)}
    claims = change(list(page.claims))
    body = render_body(page.title, claims, spans)
    out = io.BytesIO()
    view.export(out)
    source, built = zipfile.ZipFile(io.BytesIO(out.getvalue())), io.BytesIO()
    with zipfile.ZipFile(built, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "projects/ops/pages.jsonl":
                lines = [json.loads(row) for row in data.splitlines()]
                lines[-1].update(claims=[claim.shown() for claim in claims], body=body)
                data = b"".join(json.dumps(line).encode() + b"\n" for line in lines)
            elif name == f"projects/ops/pages/{slug}.md":
                data = body.encode()
            target.writestr(name, data)
    return built.getvalue()


def refused(tmp_path, archive: bytes, match: str) -> str:
    db = tmp_path / "refused.db"
    target = Store(db)
    try:
        before = dump(db)
        with pytest.raises(ValueError, match=match) as error:
            target.import_export(io.BytesIO(archive))
        assert dump(db) == before
        return str(error.value)
    finally:
        target.close()


def imports(tmp_path, archive: bytes) -> Store:
    target = Store(tmp_path / "target.db")
    target.import_export(io.BytesIO(archive))
    return target


# The reviewer's archives


def test_a_claim_with_a_false_first_citation_is_refused(source, tmp_path):
    """ "Approved", citing first the passage that says "Rejected", then the one it copies."""
    project_id, slug = filed(source, APPROVED, REJECTED)

    def false_first(claims):
        approved, rejected = claims
        cites = (*rejected.span_ids, *approved.span_ids)
        return [approved.model_copy(update={"span_ids": cites}), rejected]

    said = refused(tmp_path, rewritten(source, project_id, slug, false_first), "does not say")
    assert "Approved" not in said and "Rejected" not in said and "sp_" not in said


def test_a_claim_that_changes_the_indentation_of_code_is_refused(source, tmp_path):
    project_id, slug = filed(source, CODE)

    def flattened(claims):
        (claim,) = claims
        return [claim.model_copy(update={"text": claim.text.replace("    ship()", "ship()")})]

    refused(tmp_path, rewritten(source, project_id, slug, flattened), "does not say")


def test_two_claims_with_one_id_are_refused(source, tmp_path):
    project_id, slug = filed(source, APPROVED, REJECTED)

    def one_id(claims):
        first, second = claims
        return [first, second.model_copy(update={"id": first.id})]

    said = refused(tmp_path, rewritten(source, project_id, slug, one_id), "one id")
    assert slug in said and "cl_" not in said


def test_a_fold_of_passages_under_two_headings_is_refused(source, tmp_path):
    """The same words under two headings are two claims: the first citation's heading would
    show the second passage under a heading it is not under."""
    project_id, slug = filed(source, f"## Staging\n\n{APPROVED}", f"## Production\n\n{APPROVED}")

    def folded(claims):
        first, second = claims
        return [first.model_copy(update={"span_ids": (*first.span_ids, *second.span_ids)})]

    said = refused(tmp_path, rewritten(source, project_id, slug, folded), "heading")
    assert "Staging" not in said and "Production" not in said


def test_a_claim_that_cites_a_heading_is_refused(source, tmp_path):
    """ "# Approved" said what the heading passage says, white space aside, and was taken."""
    project_id, slug = filed(source, f"# Approved\n\n{REJECTED}", topic="Release")
    heading = next(
        span
        for span in source.note_spans(source.list_notes(project_id)[0].id)
        if span.kind == "heading"
    )

    def cites_heading(claims):
        (claim,) = claims
        made = Claim(id="cl_" + "a" * 16, text=heading.text, span_ids=(heading.id,))
        return [made, claim]

    refused(tmp_path, rewritten(source, project_id, slug, cites_heading), "does not say")


# What cairn writes still imports


def test_a_fold_of_the_same_words_under_one_heading_imports(source, tmp_path):
    project_id, slug = filed(source, APPROVED, "The Quill release is\nApproved.\n")

    def folded(claims):
        first, second = claims
        return [first.model_copy(update={"span_ids": (*first.span_ids, *second.span_ids)})]

    target = imports(tmp_path, rewritten(source, project_id, slug, folded))
    try:
        (claim,) = target.get_page(target.get_project("ops").id, slug).claims
        assert len(claim.span_ids) == 2
    finally:
        target.close()


def test_a_prose_claim_rewrapped_imports(source, tmp_path):
    project_id, slug = filed(source, "Quill ships on Fridays after the review.\n")

    def rewrapped(claims):
        (claim,) = claims
        return [claim.model_copy(update={"text": claim.text.replace(" after ", "\nafter ")})]

    imports(tmp_path, rewritten(source, project_id, slug, rewrapped)).close()
