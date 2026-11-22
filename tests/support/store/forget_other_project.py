"""Shared test helpers extracted from test_forget_other_project_unit."""

from cairn.compiler import compile_note
from cairn.store import Claim, Store
from earlier_build import earlier_page

SAID = "Quill sealed the saffron crate."


def filed(view: Store, project_id: str, text: str):
    note, created = view.add_note(project_id, text)
    assert created and compile_note(view, note).status == "saved"
    return note


def passage(view: Store, note):
    return next(span for span in view.note_spans(note.id) if span.kind == "paragraph")


def citing(n: int, text: str, *spans) -> Claim:
    return Claim(id=f"cl_{n:016x}", text=text, span_ids=tuple(span.id for span in spans))


def borrowed(store: Store, project_id: str, slug: str, *claims: Claim):
    body = "".join(f"\n{claim.text}\n" for claim in claims)
    return earlier_page(store, project_id, slug, slug.title(), f"# {slug.title()}\n{body}", claims)
