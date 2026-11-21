"""RT1, one step past the ninth round's one rule for removing notes: what it did not reach.

A page of another project that cites a note is written as a database an earlier build holds
it (tests/earlier_build.py). The oracle for "the text is gone" is a scan of every table of
the database. Each case is marked as failing on purpose, strictly, so the file passes as
committed. Made-up notes."""

from contextlib import closing

import pytest
from test_rt1_forget_workspace_held import filed, passage, tables_holding

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import CitesForgotten, Claim, Store
from earlier_build import earlier_page

GONE = "Quill sealed the kqzvtxb crate."


@pytest.mark.xfail(
    strict=True,
    reason="a save drafted before a forget brings the note back once its project is deleted",
)
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
        assert tables_holding(store, "kqzvtxb") == []
