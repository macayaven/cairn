"""RT1, one step past the forget across projects: what the eighth round's fix did not reach.

A page of another project that cites a note can exist only in a database an earlier build
wrote (tests/earlier_build.py writes one; save_page refuses a new such citation). The forget
found such a page wherever it was; these were its neighbours. The first was found failing
and is fixed since the ninth round (a delete changes such pages as a forget does: erasure's
one rule). The second is a stated limit (src/cairn/guides/run.md), kept as a passing test
of what the import says. What the ninth round left: test_rt1_forget_workspace_found2.py.
Made-up notes."""

import io
from contextlib import closing

import pytest

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import CitesForgotten, Claim, Store
from earlier_build import earlier_page
from support.store.rt1_forget_workspace_found import SAID, exported, legacy, tables_holding
from support.store.rt1_forget_workspace_held import (
    filed,
    passage,
)
from support.store.rt1_forget_workspace_held import (
    tables_holding as held_tables_holding,
)


def test_deleting_a_project_takes_its_notes_text_out_of_pages_of_other_projects():
    """The privacy guide: a project's notes go at once. Found on the eighth round's fix:
    deleting the note's whole project left east's page with the sentence in its body, its
    claims, its search rows and a fresh export. Fixed: the delete rewrites the page as a
    forget does, and no table holds the text."""
    with closing(Store(":memory:")) as store:
        desk, east, _ = legacy(store)
        assert Memory(store, lint_gate).delete_project("desk", "desk") == {"deleted": True}
        assert store.get_project("desk") is None and store.list_notes(desk.id) == []
        _, archive = exported(store)
        page = store.get_page(east.id, "minutes")
        assert (SAID in page.body, store.search("saffron") != [], SAID in archive) == (
            False,
            False,
            False,
        )
        assert tables_holding(store, "saffron") == []


def test_limit_an_export_of_a_database_an_earlier_build_wrote_is_refused_naming_the_page():
    """Restated as a passing test of a stated limit (src/cairn/guides/run.md, beside the
    import): the import's check stays, so an empty store refuses the whole export of such a
    database, with the line that names the page. Forgetting the note it cites, or deleting
    that note's project, rewrites the page, and the next export imports
    (test_earlier_page_export_unit.py)."""
    with closing(Store(":memory:")) as store:
        legacy(store)
        archive, _ = exported(store)
    with closing(Store(":memory:")) as fresh:
        refused = r"^a claim of 'minutes' in projects/east/ cites a passage that is not of its"
        with pytest.raises(ValueError, match=refused):
            fresh.import_export(io.BytesIO(archive))
        assert fresh.list_projects() == []


# --- test_rt1_forget_workspace_found2.py ---

GONE = "Quill sealed the kqzvtxb crate."


def test_a_save_drafted_before_a_forget_stays_stale_after_the_notes_project_is_deleted():
    """The store's contract: a save drafted before a note was forgotten never brings it back
    (CitesForgotten), and since the ninth round the same after a delete. Both hold alone
    (test_forget_other_project_unit, test_delete_project_marker_unit). In sequence they do
    not: the forget of A's note removes B's page, so the delete of A changes no page of B and
    leaves B no row of `forgotten`; A's digest goes with A, and the workspace has none left.
    B's page made again at version 1, the draft saved at version 1 is accepted (seen: version
    2, the sentence in page_versions and the search index's tables). Reached through
    Store.save_page; through Memory only by a compile racing the forget, the delete and the
    note that makes the page again (not driven here)."""
    with closing(Store(":memory:")) as store:
        a, b = store.create_project("a"), store.create_project("b")
        note = filed(store, a.id, f"# Dispatch\n\n{GONE}\n")
        claim = Claim(id="cl_" + "1" * 16, text=GONE, span_ids=(passage(store, note).id,))
        read = earlier_page(store, b.id, "minutes", "Minutes", f"# Minutes\n\n{GONE}\n", [claim])
        assert read.version == 1
        store.forget_note(note.id)
        assert Memory(store, lint_gate).delete_project("a", "a") == {"deleted": True}
        filed(store, b.id, "# Minutes\n\nThe ledger opens at noon.\n")
        assert store.get_page(b.id, "minutes").version == 1
        with pytest.raises(CitesForgotten):
            store.save_page(b.id, "minutes", read.title, read.body, read.claims, expected_version=1)
        assert held_tables_holding(store, "kqzvtxb") == []
