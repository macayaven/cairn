"""Deleting a project takes its notes' passages out of every page of the workspace that
cites them, whatever its project, as a forget of each note would, in the same transaction
(ninth round, item 1). A page of another project can cite them only in a database an
earlier build wrote (tests/earlier_build.py). The deleted project's audit event counts
those pages (`elsewhere`) and the log names none of them. Made-up notes."""

import logging

from test_forget_other_project_unit import SAID, borrowed, citing, filed, passage
from test_rt1_forget_workspace_found import tables_holding

from cairn.store import Store


def topic_keys(store: Store, project_id: str, slug: str) -> int:
    with store._read() as con:
        (count,) = con.execute(
            "SELECT COUNT(*) FROM page_keys WHERE project_id = ? AND slug = ?", (project_id, slug)
        ).fetchone()
    return count


def test_a_page_of_a_third_project_keeps_its_own_notes_claims_and_loses_the_rest(tmp_path, caplog):
    store = Store(tmp_path / "cairn.db")
    try:
        desk, east, west = (store.create_project(n) for n in ("desk", "east", "west"))
        gone = passage(store, filed(store, desk.id, f"# Dispatch\n\n{SAID}\n"))
        far = passage(store, filed(store, east.id, "# Far\n\nWren keeps the far log.\n"))
        own = passage(store, filed(store, west.id, "# Minutes\n\nThe ledger opens at noon.\n"))
        east_before = store.get_page(east.id, "far")
        borrowed(  # its own note's claim, the deleted project's, one that folds both, and
            store,  # one of a third project, which an import would refuse
            west.id,
            "minutes",
            citing(1, own.text, own),
            citing(2, SAID, gone),
            citing(3, own.text, own, gone),
            citing(4, far.text, far),
        )
        with caplog.at_level(logging.INFO, logger="cairn.store"):
            assert store.delete_project(desk.id)
        page = store.get_page(west.id, "minutes")
        assert [(c.text, c.span_ids) for c in page.claims] == [
            (own.text, (own.id,)),
            (own.text, (own.id,)),
        ]
        assert store.page_versions(west.id, "minutes") == [page.version]
        assert store.get_page(east.id, "far") == east_before  # cites nothing of desk
        assert store.search("saffron") == [] and tables_holding(store, "saffron") == []
        (event,) = [e for e in store.list_audit() if e.action == "project.deleted"]
        assert (event.detail["pages"], event.detail["elsewhere"]) == (1, 1)
        said = [r.getMessage() for r in caplog.records if r.name == "cairn.store"]
        assert said == [  # a count, never the page or its project
            "delete: 1 claim(s) that an import would refuse left a page of another project"
        ]
    finally:
        store.close()


def test_a_page_that_cites_only_the_deleted_projects_notes_goes_with_its_rows_and_key(tmp_path):
    store = Store(tmp_path / "cairn.db")
    try:
        desk, east = store.create_project("desk"), store.create_project("east")
        gone = passage(store, filed(store, desk.id, f"# Dispatch\n\n{SAID}\n"))
        borrowed(store, east.id, "embargo", citing(1, SAID, gone))
        assert topic_keys(store, east.id, "embargo") == 1
        assert store.delete_project(desk.id)
        assert store.get_page(east.id, "embargo") is None
        assert store.page_versions(east.id, "embargo") == []
        assert topic_keys(store, east.id, "embargo") == 0
        assert store.search("saffron") == [] and tables_holding(store, "saffron") == []
        (event,) = [e for e in store.list_audit() if e.action == "project.deleted"]
        assert event.detail["elsewhere"] == 1
        assert not [e for e in store.list_audit() if e.detail.get("page") == "embargo"]
    finally:
        store.close()


def test_a_page_that_cited_them_in_an_earlier_version_only_keeps_one_new_version(tmp_path):
    store = Store(tmp_path / "cairn.db")
    try:
        desk, east = store.create_project("desk"), store.create_project("east")
        gone = passage(store, filed(store, desk.id, f"# Dispatch\n\n{SAID}\n"))
        own = passage(store, filed(store, east.id, "# Minutes\n\nThe ledger opens at noon.\n"))
        borrowed(store, east.id, "minutes", citing(1, own.text, own), citing(2, SAID, gone))
        latest = borrowed(store, east.id, "minutes", citing(1, own.text, own))
        assert store.delete_project(desk.id)
        page = store.get_page(east.id, "minutes")
        assert page.claims == latest.claims and page.version == latest.version + 1
        assert store.page_versions(east.id, "minutes") == [page.version]
        assert tables_holding(store, "saffron") == []
    finally:
        store.close()


def test_deleting_a_project_of_5000_passages_that_nothing_elsewhere_cites_searches_once(
    tmp_path,
):
    """One search over the workspace's page versions for all of the project's passages,
    never one a note or a passage; it reads the page versions once, and the passages once."""
    store = Store(tmp_path / "cairn.db")
    try:
        desk, east = store.create_project("desk"), store.create_project("east")
        text = "".join(f"Line {n} of the ledger stands.\n\n" for n in range(1000))
        for n in range(5):
            _, created = store.add_note(desk.id, f"# Part {n}\n\n{text}")
            assert created
        filed(store, east.id, "# Minutes\n\nThe ledger opens at noon.\n")
        with store._read() as con:
            (passages,) = con.execute(
                "SELECT COUNT(*) FROM spans JOIN notes ON notes.id = spans.note_id"
                " WHERE notes.project_id = ?",
                (desk.id,),
            ).fetchone()
        assert passages >= 5000
        statements: list[str] = []
        store._observe(trace=statements.append)
        try:
            assert store.delete_project(desk.id)
        finally:
            store._observe(trace=None)
        searches = [s for s in statements if "json_each(p.claims)" in s]
        assert len(searches) == 1, searches
        (event,) = [e for e in store.list_audit() if e.action == "project.deleted"]
        assert "elsewhere" not in event.detail
    finally:
        store.close()
