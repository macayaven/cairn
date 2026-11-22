"""The remedy the run guide gives for an export an import refuses because a page in it cites a
note of another project (a database an earlier build wrote; ninth round, item 3): forgetting
the note it cites, or deleting that note's project, rewrites the page, and the next export
imports whole. The refusal itself: test_rt1_forget_workspace_found.py. Made-up notes."""

import io
from contextlib import closing

import pytest

from cairn.store import Store
from support.store.rt1_forget_workspace_found import exported, legacy


@pytest.mark.parametrize("remedy", ["forget the note", "delete its project"])
def test_after_the_remedy_the_next_export_imports(remedy):
    with closing(Store(":memory:")) as store:
        desk, east, note = legacy(store)
        if remedy == "forget the note":
            store.forget_note(note.id)
        else:
            assert store.delete_project(desk.id)
        archive, text = exported(store)
        assert "saffron" not in text
        page = store.get_page(east.id, "minutes")
    with closing(Store(":memory:")) as fresh:
        summary = fresh.import_export(io.BytesIO(archive))
        projects = {p.name: p for p in fresh.list_projects()}
        kept = {"desk", "east"} if remedy == "forget the note" else {"east"}
        assert (set(projects), summary.notes) == (kept, 1)  # east's own note
        brought = fresh.get_page(projects["east"].id, "minutes")
        assert [c.text for c in brought.claims] == [c.text for c in page.claims]
        assert fresh.search("saffron") == []
