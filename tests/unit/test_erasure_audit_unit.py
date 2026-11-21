"""Unit tests for what the audit log keeps of a deleted project and a forgotten note (H5b
follow-up): a deleted project's events never attach to a later project of the same name, and
no page slug of what was erased (a slug comes from a note's title) stays in the log."""

import json
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.compiler import compile_note
from cairn.server import create_app
from cairn.store import Store
from cookie_names import session_cookie

BASE = "https://cairn.example.test"
ISSUER = "https://id.acme.example/pool-1"
OFFER = "# Offer Saltmarsh\n\n- The offer to Saltmarsh closes on Friday.\n"
WEEKLY = "# Weekly\n\n- Brook owns the rollback.\n"
ECHO = "# Weekly\n\n- Quill ships on Thursday.\n"


class World:
    """The default workspace: Ada owns it, Brook is an admin, Cy a member."""

    def __init__(self, tmp_path) -> None:
        self.store = Store(tmp_path / "cairn.db")
        self.ada, self.brook, self.cy = (self.person(n) for n in ("Ada", "Brook", "Cy"))
        self.ada.view.set_role(self.brook.id, "admin")

    def person(self, name: str) -> SimpleNamespace:
        signed = self.store.sign_in(
            "sso", f"sub-{name}", f"{name.lower()}@acme.example", name, issuer=ISSUER,
            join="default",
        )  # fmt: skip
        return SimpleNamespace(
            id=signed.user.id,
            cookie={"cookie": f"{session_cookie('https')}={signed.token}"},
            view=self.store.for_account(signed.account_id, signed.user.id),
        )


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path)
    yield made
    made.store.close()


def add(view: Store, project_id: str, text: str):
    note, _ = view.add_note(project_id, text)
    assert compile_note(view, note).status == "saved"
    return note


def said(events) -> str:
    return json.dumps([[e.action, e.project, e.detail] for e in events]).lower()


def deleted_then_named_again(world):
    """Cy makes the private project "hiring", writes in it and deletes it; later Ada makes a
    project "hiring" open to everyone."""
    old = world.cy.view.create_project("hiring", everyone="none")
    add(world.cy.view, old.id, OFFER)
    assert world.cy.view.delete_project(old.id) is True
    new = world.ada.view.create_project("hiring")
    return old, new


def test_an_admin_never_reads_a_deleted_private_projects_events_under_its_name_again(world):
    old, _ = deleted_then_named_again(world)
    seen = world.brook.view.list_audit()
    assert [e.action for e in seen if e.project == "hiring"] == ["project.created"]
    assert not [e for e in seen if e.action in ("project.deleted", "note.saved")]
    assert "saltmarsh" not in said(seen) and old.id not in said(seen)


def test_an_owner_reads_them_under_a_value_no_project_can_be_named(world):
    old, _ = deleted_then_named_again(world)
    events = world.ada.view.list_audit()
    theirs = [e for e in events if e.project != "hiring" and e.project is not None]
    assert {e.action for e in theirs} == {"project.created", "note.saved", "project.deleted"}
    assert {e.project for e in theirs} == {f"{old.id} (deleted)"}
    deleted = next(e for e in theirs if e.action == "project.deleted")
    assert deleted.detail["name"] == "hiring" and deleted.detail["id"] == old.id
    # The events stay (who, what, when); the slug made from the note's title goes.
    saved = next(e for e in theirs if e.action == "note.saved")
    assert saved.detail.get("user") == world.cy.id and "page" not in saved.detail
    assert "saltmarsh" not in said(events)


def test_a_new_project_of_the_same_name_starts_with_no_events_of_the_old_one(world):
    _, new = deleted_then_named_again(world)
    for view in (world.ada.view, world.brook.view, world.cy.view):
        assert [e.action for e in view.list_audit() if e.project == "hiring"] == ["project.created"]
    assert world.store.for_account(world.store.account_id).get_project("hiring").id == new.id


def test_the_audit_page_shows_an_admin_nothing_of_the_old_project(world):
    old, _ = deleted_then_named_again(world)
    with TestClient(create_app(world.store, public_url=BASE), base_url=BASE) as client:
        admin = client.get("/audit", headers=world.brook.cookie)
        owner = client.get("/audit", headers=world.ada.cookie)
    assert admin.status_code == owner.status_code == 200
    assert old.id not in admin.text and "project.deleted" not in admin.text
    assert f"{old.id} (deleted)" in owner.text and "saltmarsh" not in owner.text.lower()


def test_forgetting_takes_the_slug_of_a_page_it_removes_out_of_the_log(world):
    ada = world.ada.view
    project = ada.create_project("team")
    offer, weekly = add(ada, project.id, OFFER), add(ada, project.id, WEEKLY)
    add(ada, project.id, ECHO)
    before = len(ada.list_audit(1000))

    assert ada.forget_note(offer.id).removed == ("offer-saltmarsh",)
    assert ada.forget_note(weekly.id).rebuilt == ("weekly",)

    events = ada.list_audit(1000)
    assert len(events) == before + 2  # each event stays; only the slug went
    pages = [e.detail.get("page") for e in events if e.action == "note.saved"]
    assert pages == ["weekly", "weekly", None]  # newest first; the page that stays keeps it
    assert "saltmarsh" not in said(events)
