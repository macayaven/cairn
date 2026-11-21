"""A delete that changes or removes a page of another project leaves that project a row of
`forgotten` that is no digest of any text (erasure.PASSAGES_REMOVED, the empty string): it
records that passages a page of the project cited went with another project, so a save
drafted before the delete is CitesForgotten, as after a forget (ninth round, the stale save
after a delete). It goes with its project, is never exported, and refuses no note. Made-up
notes."""

import io
from contextlib import closing

import pytest
from test_forget_other_project_unit import SAID, borrowed, citing, filed, passage
from test_forget_unit import Interleaved
from test_rt1_forget_workspace_found import exported

from cairn.compiler import compile_note
from cairn.store import CitesForgotten, Store, erasure

MINUTES = "# Minutes\n\nThe ledger opens at noon.\n"


def markers(store: Store) -> list[str]:
    """The projects holding the row, by name."""
    with store._read() as con:
        return [
            name
            for (name,) in con.execute(
                "SELECT projects.name FROM forgotten JOIN projects ON projects.id = project_id"
                " WHERE text_sha256 = ? ORDER BY projects.name",
                (erasure.PASSAGES_REMOVED,),
            )
        ]


def borrowing(store: Store):
    """`east`'s page `minutes`, as an earlier build saved it, citing `desk`'s note alone."""
    desk, east = store.create_project("desk"), store.create_project("east")
    note = filed(store, desk.id, f"# Dispatch\n\n{SAID}\n")
    read = borrowed(store, east.id, "minutes", citing(1, SAID, passage(store, note)))
    return desk, east, read


def test_a_save_read_before_a_delete_never_brings_the_deleted_text_back():
    with closing(Store(":memory:")) as store:
        desk, east, read = borrowing(store)
        assert store.delete_project(desk.id) and store.get_page(east.id, "minutes") is None
        filed(store, east.id, MINUTES)  # made again, at the version the writer read
        again = store.get_page(east.id, "minutes")
        assert again.version == read.version == 1
        with pytest.raises(CitesForgotten):
            store.save_page(
                east.id, "minutes", read.title, read.body, read.claims, expected_version=1
            )
        assert store.get_page(east.id, "minutes") == again
        assert store.search("saffron") == [] and markers(store) == ["east"]


def test_a_compile_that_read_the_page_before_the_delete_builds_on_the_page_as_it_stands():
    with closing(Store(":memory:")) as store:
        desk, east, _ = borrowing(store)
        note, _ = store.add_note(east.id, "# Minutes\n\nWren rings the bell at two.\n")

        def meanwhile():
            store.delete_project(desk.id)
            filed(store, east.id, MINUTES)

        result = compile_note(Interleaved(store, meanwhile), note)  # read version 1
        page = store.get_page(east.id, "minutes")
        assert (result.status, page.version) == ("saved", 2)
        assert "bell at two" in page.body and "noon" in page.body and SAID not in page.body
        assert store.search("saffron") == []


def test_a_delete_that_changed_no_page_of_another_project_leaves_no_row():
    with closing(Store(":memory:")) as store:
        desk, east = store.create_project("desk"), store.create_project("east")
        filed(store, desk.id, f"# Dispatch\n\n{SAID}\n")
        filed(store, east.id, MINUTES)
        assert store.delete_project(desk.id)
        assert markers(store) == []


def test_a_second_delete_that_changes_the_same_project_writes_no_second_row():
    with closing(Store(":memory:")) as store:
        desk, east, _ = borrowing(store)
        west = store.create_project("west")
        other = passage(store, filed(store, west.id, "# Far\n\nWren keeps the far log.\n"))
        borrowed(store, east.id, "far", citing(2, other.text, other))
        assert store.delete_project(desk.id) and store.delete_project(west.id)
        assert markers(store) == ["east"]
        with store._read() as con:
            (rows,) = con.execute(
                "SELECT COUNT(*) FROM forgotten WHERE project_id = ?", (east.id,)
            ).fetchone()
        assert rows == 1


def test_the_row_goes_with_its_project_and_refuses_no_note():
    with closing(Store(":memory:")) as store:
        desk, east, _ = borrowing(store)
        assert store.delete_project(desk.id) and markers(store) == ["east"]
        _, created = store.add_note(east.id, f"# Dispatch\n\n{SAID}\n")  # any text, even this
        assert created
        assert store.delete_project(east.id) and markers(store) == []


def test_an_export_holds_nothing_of_the_row_and_imports():
    with closing(Store(":memory:")) as store:
        desk, east, _ = borrowing(store)
        filed(store, east.id, MINUTES)
        assert store.delete_project(desk.id) and markers(store) == ["east"]
        archive, text = exported(store)
    assert "forgotten" not in text and "saffron" not in text
    with closing(Store(":memory:")) as fresh:
        summary = fresh.import_export(io.BytesIO(archive))
        assert (summary.projects, summary.notes) == (1, 1)
        assert markers(fresh) == []


def test_a_forget_that_changes_only_its_own_projects_pages_writes_no_row():
    with closing(Store(":memory:")) as store:
        desk = store.create_project("desk")
        filed(store, desk.id, MINUTES)
        store.forget_note(filed(store, desk.id, f"# Minutes\n\n{SAID}\n").id)
        assert markers(store) == []


def test_a_forget_that_changes_pages_of_two_other_projects_writes_one_row_in_each():
    with closing(Store(":memory:")) as store:
        desk, east, west = (store.create_project(n) for n in ("desk", "east", "west"))
        first, second = (
            passage(store, filed(store, desk.id, f"# {title}\n\n{text}\n"))
            for title, text in (("Dispatch", SAID), ("Ledger", "Wren keeps the far log."))
        )
        filed(store, west.id, MINUTES)
        for project, slug in ((east, "minutes"), (west, "minutes")):
            borrowed(
                store, project.id, slug, citing(1, SAID, first), citing(2, second.text, second)
            )
        store.forget_note(first.source_id)
        assert markers(store) == ["east", "west"]
        store.forget_note(second.source_id)  # a later forget: no second row
        assert markers(store) == ["east", "west"]
        with store._read() as con:
            (rows,) = con.execute(
                "SELECT COUNT(*) FROM forgotten WHERE text_sha256 = ?", (erasure.PASSAGES_REMOVED,)
            ).fetchone()
        assert rows == 2
