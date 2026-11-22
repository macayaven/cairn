"""Shared test helpers extracted from test_earlier_page_writers_unit."""

import pytest

from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.store import Claim, Store
from earlier_build import earlier_page

BASE = "https://cairn.example.test"

SLUG = "drift-sync"

SAID = "Drift mirrors two buckets."

FIRST = (
    f"# Drift sync\n\n{SAID}\n\n## Flags\n\n"
    "- `--dry-run` lists the changes first.\n- `--delete` removes extra files.\n"
)

SECOND = (
    "# Drift sync\n\nDrift keeps a log.\n\n## Flags\n\n"
    "- `--retries 3` helps on flaky links.\n\n## Limits\n\n- At most 500 files go in one batch.\n"
)


def filed(view: Store, project_id: str, text: str):
    note, created = view.add_note(project_id, text)
    assert created and compile_note(view, note, gate=lint_gate).status == "saved"
    return note


def passages(view: Store, note):
    return [span for span in view.note_spans(note.id) if span.kind == "paragraph"]


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def legacy(store: Store, source: str = "project", own=lambda claims: claims):
    """`east`'s page, as an earlier build saved it: its own claims (in the order `own` gives)
    and, first, one that cites a note of `desk`, of its workspace (`source` "project") or of
    another ("workspace"), in the words of east's own first claim. (east, the cited passage.)"""
    east = store.create_project("east")
    there = store if source == "project" else store.for_account(store.create_account("Far").id)
    desk = there.create_project("desk")
    cited, _ = passages(
        there, filed(there, desk.id, f"# Dispatch\n\n{SAID}\n\nThe crate ships at dawn.\n")
    )
    for text in (FIRST, SECOND):
        filed(store, east.id, text)
    page = store.get_page(east.id, SLUG)
    borrowed = Claim(id="cl_" + "9" * 16, text=SAID, span_ids=(cited.id,))
    earlier_page(store, east.id, SLUG, page.title, page.body, [borrowed, *own(page.claims)])
    return east, cited
