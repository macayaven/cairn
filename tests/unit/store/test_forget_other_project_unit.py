"""Forgetting a note takes its passages out of every page of the workspace that cites them,
whatever project the page is in (eighth round, finding 1). A page of another project can
cite them only in a database an earlier build wrote (tests/earlier_build.py): save_page
refuses that now. What the forgetter is told counts those pages and names none of them or
their projects. Made-up notes; the reviewer's own cases are in
test_reviewer8_forget_other_project_unit.py."""

import json
import logging

import pytest
from starlette.testclient import TestClient

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.server import create_app
from cairn.store import CitesForgotten, Claim, Store
from support.cookie_names import session_cookie
from support.store.forget_other_project import SAID, borrowed, citing, filed, passage

BASE = "https://cairn.example.test"
ISSUER = "https://id.acme.example/pool-1"


def test_the_forgetter_is_told_how_many_pages_of_other_projects_changed_and_never_which(
    tmp_path, caplog
):
    """Brook reaches his own project only; Ada's `vault` is closed to every member. Its
    pages that cite Brook's note change or go by the same rule as his own, and the preview,
    the answer, the log and the audit event count them and name nothing of them."""
    store = Store(tmp_path / "cairn.db")
    try:
        ada, brook = (
            store.sign_in("sso", f"sub-{n}", f"{n}@acme.example", n, issuer=ISSUER, join="default")
            for n in ("ada", "brook")
        )
        as_ada = store.for_account(ada.account_id, ada.user.id)
        as_brook = store.for_account(brook.account_id, brook.user.id)
        desk = as_brook.create_project("desk")
        note = filed(as_brook, desk.id, f"# Dispatch\n\n{SAID}\n")
        vault = as_ada.create_project("vault", everyone="none")
        west = as_ada.create_project("west")
        theirs = passage(store, filed(as_ada, vault.id, f"# Kept\n\n{SAID}\n"))
        far = passage(store, filed(as_ada, west.id, "# Far\n\nWren keeps the far log.\n"))
        ours = passage(store, note)
        borrowed(store, vault.id, "embargo", citing(1, SAID, ours))
        borrowed(  # a fold with the vault's own note, and a passage of a third project
            store, vault.id, "annex", citing(2, SAID, ours, theirs), citing(3, far.text, far)
        )
        hidden = (vault.name, vault.id, "embargo", "annex", "Embargo", "Annex", west.id)

        preview = as_brook.forget_preview(note.id)
        assert (preview.rebuilt, preview.removed, preview.elsewhere) == ((), ("dispatch",), 2)
        cookie = {"cookie": f"{session_cookie('https')}={brook.token}"}
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            shown = client.get(f"/b/desk/notes/{note.id}/forget", headers=cookie).text
        assert "2 pages of other projects" in shown and "Pages that go" in shown
        assert not [word for word in hidden if word in shown]

        with caplog.at_level(logging.INFO, logger="cairn.store"):
            answer = Memory(as_brook, lint_gate).forget("desk", note.id)
        assert answer == {
            "note_id": note.id,
            "rebuilt": [],
            "removed": ["dispatch"],
            "elsewhere": 2,
        }
        said = [r.getMessage() for r in caplog.records if r.name == "cairn.store"]
        assert said == [
            "forget: 1 claim(s) that an import would refuse left a page of another project"
        ]
        (event,) = [e for e in store.list_audit() if e.action == "note.forgotten"]
        assert event.project == "desk" and event.detail == {
            "note": note.id,
            "project": desk.id,
            "pages": 3,
            "elsewhere": 2,
            "claims_left": 1,
            "user": brook.user.id,
        }
        assert not [word for word in hidden if word in json.dumps(event.detail)]

        # and the closed project's pages changed: one went, one keeps its own note's claim
        assert as_ada.get_page(vault.id, "embargo") is None
        annex = as_ada.get_page(vault.id, "annex")
        assert [(c.text, c.span_ids) for c in annex.claims] == [(SAID, (theirs.id,))]
        assert as_ada.page_versions(vault.id, "annex") == [annex.version]
        assert (
            not [  # the slug of the page that went leaves the vault's events
                e for e in store.list_audit() if e.detail.get("page") == "embargo"
            ]
        )
        assert [e.project for e in store.list_audit() if e.detail.get("page") == "annex"]
    finally:
        store.close()


def test_a_page_of_the_same_slug_in_another_project_is_its_own(tmp_path):
    store = Store(tmp_path / "cairn.db")
    try:
        desk, east = store.create_project("desk"), store.create_project("east")
        note = filed(store, desk.id, f"# Minutes\n\n{SAID}\n")
        kept = filed(store, east.id, "# Minutes\n\nThe ledger opens at noon.\n")
        page = store.get_page(east.id, "minutes")
        stays = page.claims[0]
        borrowed(store, east.id, "minutes", stays, citing(9, SAID, passage(store, note)))
        assert store.forget_preview(note.id).model_dump() == {
            "note_id": note.id,
            "rebuilt": (),
            "removed": ("minutes",),
            "elsewhere": 1,
        }
        store.forget_note(note.id)
        assert store.get_page(desk.id, "minutes") is None
        east_page = store.get_page(east.id, "minutes")
        assert [c.span_ids for c in east_page.claims] == [(passage(store, kept).id,)]
        assert store.page_versions(east.id, "minutes") == [east_page.version]
        assert store.search("saffron") == []
    finally:
        store.close()


def test_a_forget_searches_the_workspaces_page_versions_once_whatever_its_projects(tmp_path):
    """One search over the workspace's page versions, never one a project: a forget makes
    as many statements in a workspace of 2 projects as of 20, each with a page (cairn's own:
    those FTS5 makes inside the index, traced with a leading "--", follow its size)."""
    made = {}
    for projects in (2, 20):
        store = Store(tmp_path / f"{projects}.db")
        try:
            ids = [store.create_project(f"p{n}").id for n in range(projects)]
            for n, project_id in enumerate(ids):
                filed(store, project_id, f"# Topic\n\nFact {n} holds.\n")
            note = filed(store, ids[0], f"# Dispatch\n\n{SAID}\n")
            statements: list[str] = []
            store._observe(trace=statements.append)
            try:
                store.forget_note(note.id)
            finally:
                store._observe(trace=None)
            made[projects] = [s for s in statements if not s.startswith("--")]
        finally:
            store.close()
    for statements in made.values():
        assert len([s for s in statements if "json_each(p.claims)" in s]) == 1
    assert len(made[2]) == len(made[20]), {n: len(s) for n, s in made.items()}


def test_a_save_refuses_a_held_passage_of_another_project_and_never_one_it_does_not_hold(
    tmp_path,
):
    store = Store(tmp_path / "cairn.db")
    try:
        desk, east = store.create_project("desk"), store.create_project("east")
        note = filed(store, desk.id, f"# Dispatch\n\n{SAID}\n")
        foreign = citing(1, SAID, passage(store, note))
        with pytest.raises(ValueError, match=r"^A page can cite only passages from its own"):
            store.save_page(east.id, "borrowed", "Borrowed", "", [foreign])
        assert store.get_page(east.id, "borrowed") is None
        made_up = Claim(id="cl_" + "0" * 16, text=SAID, span_ids=("sp_" + "0" * 16,))
        assert store.save_page(east.id, "made-up", "Made up", "", [made_up]).version == 1
    finally:
        store.close()


def test_a_save_read_before_the_forget_never_brings_the_note_back_into_another_project(
    tmp_path,
):
    """A writer that read a page of another project before the forget, and saves once the
    page was removed and made again at the version it read, cites a passage the store no
    longer holds: CitesForgotten, as in the note's own project, and nothing is written."""
    store = Store(tmp_path / "cairn.db")
    try:
        desk, east = store.create_project("desk"), store.create_project("east")
        note = filed(store, desk.id, f"# Dispatch\n\n{SAID}\n")
        read = borrowed(store, east.id, "minutes", citing(1, SAID, passage(store, note)))
        store.forget_note(note.id)
        assert store.get_page(east.id, "minutes") is None
        filed(store, east.id, "# Minutes\n\nThe ledger opens at noon.\n")  # made again
        again = store.get_page(east.id, "minutes")
        assert again.version == read.version == 1
        with pytest.raises(CitesForgotten):
            store.save_page(
                east.id, "minutes", read.title, read.body, read.claims, expected_version=1
            )
        assert store.get_page(east.id, "minutes") == again
        assert store.search("saffron") == []
    finally:
        store.close()


def test_a_forget_in_one_workspace_never_refuses_a_save_in_another(tmp_path):
    """The workspace that forgot is asked, never the store: a save of another workspace that
    cites a span of its own making still passes."""
    store = Store(tmp_path / "cairn.db")
    try:
        desk = store.create_project("desk")
        store.forget_note(filed(store, desk.id, f"# Dispatch\n\n{SAID}\n").id)
        other = store.for_account(store.create_account("Fieldwork").id)
        west = other.create_project("west")
        made_up = Claim(id="cl_" + "0" * 16, text=SAID, span_ids=("sp_" + "0" * 16,))
        assert other.save_page(west.id, "made-up", "Made up", "", [made_up]).version == 1
    finally:
        store.close()
