"""Unit tests for a removed source's events in the audit log. remove_source refiles every
event of the source, source.removed included, under audit.REMOVED_SOURCE (a value no source
name can hold), so a later source of the same name starts with none of them. One who does
not own the workspace never reads them, whatever they reach now (the source's config named
a project they could not reach, and that config is gone); an owner, and a view acting for no
person, still do."""

import re
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.server import SESSION_COOKIE, create_app
from cairn.store import Store
from cairn.store.memory import PROJECT_NAME

BASE = "https://cairn.example.test"
ISSUER = "https://id.acme.example/pool-1"
SOURCE = {
    "base_url": "https://langfuse.example.test",
    "public_key_env": "LF_PUBLIC",
    "secret_key_env": "LF_SECRET",
}
OLD = ["source.removed", "source.synced", "source.added"]


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
            token=signed.token,
            account_id=signed.account_id,
            view=self.store.for_account(signed.account_id, signed.user.id),
        )


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path)
    yield made
    made.store.close()


def removed_with_its_project(world) -> None:
    """Ada makes the private project "hiring" and the source "recruiting" that writes into it,
    which runs once; then she removes the source and deletes the project. Brook and Cy, who
    could not reach "hiring", now reach every project there is."""
    hiring = world.ada.view.create_project("hiring", everyone="none")
    world.ada.view.create_project("ops")
    world.ada.view.add_source("recruiting", "langfuse", {**SOURCE, "projects": ["hiring"]})
    claim = world.ada.view.claim_source("recruiting")
    assert world.ada.view.finish_source_run(claim, "c1", {"saved": 7, "rejected": 2})
    assert world.store.remove_source("recruiting") is True  # the command line's
    assert world.ada.view.delete_project(hiring.id) is True
    every = sorted(b.name for b in world.ada.view.list_projects())
    for person in (world.brook, world.cy):
        assert sorted(b.name for b in person.view.list_projects()) == every


def named_again(world) -> None:
    """Then Ada adds a source of the same name over the open project "ops"."""
    removed_with_its_project(world)
    world.ada.view.add_source("recruiting", "langfuse", {**SOURCE, "projects": ["ops"]})


def of_the_source(events, name: str = "recruiting") -> list[str]:
    return [e.action for e in events if e.detail.get("source") == name]


def refiled(events) -> dict[str, list[str]]:
    """The source events filed under a value other than a live source's name, by value."""
    out: dict[str, list[str]] = {}
    for e in events:
        if e.action.startswith("source.") and e.detail["source"] != "recruiting":
            out.setdefault(e.detail["source"], []).append(e.action)
    return out


def test_a_person_who_does_not_own_the_workspace_never_reads_the_removed_sources_events(world):
    removed_with_its_project(world)
    assert of_the_source(world.brook.view.list_audit()) == []
    assert of_the_source(world.cy.view.list_audit()) == []
    assert refiled(world.brook.view.list_audit()) == {}
    # Other events stay: the open project made alongside it, for one.
    assert "ops" in {e.project for e in world.brook.view.list_audit()}


def test_a_removed_sources_events_are_refiled_under_a_value_no_source_can_be_named(world):
    removed_with_its_project(world)
    events = world.ada.view.list_audit()
    assert of_the_source(events) == []
    [(gone, actions)] = refiled(events).items()
    assert actions == OLD
    assert not PROJECT_NAME.fullmatch(gone)  # a source's name follows the project-name rules
    removed = next(e for e in events if e.action == "source.removed")
    assert removed.detail == {"source": gone, "name": "recruiting"}  # the command line
    synced = next(e for e in events if e.action == "source.synced")
    assert synced.detail["saved"] == 7  # the event stays, with its counts


def test_a_later_source_of_the_same_name_starts_with_none_of_the_old_ones_events(world):
    named_again(world)
    for person in (world.brook, world.cy):
        seen = person.view.list_audit()
        # The admin reads the new source's events; a member reads no source's (finding 16).
        assert of_the_source(seen) == (["source.added"] if person is world.brook else [])
        assert refiled(seen) == {}
        assert not [e for e in seen if e.action == "source.synced"]
    for view in (world.ada.view, world.store.for_account(world.ada.account_id)):
        seen = view.list_audit()
        assert of_the_source(seen) == ["source.added"]
        assert list(refiled(seen).values()) == [OLD]


def test_each_removal_of_a_name_is_filed_apart(world):
    named_again(world)
    assert world.store.remove_source("RECRUITING") is True  # names ignore case
    filed = refiled(world.ada.view.list_audit())
    assert sorted(filed.values()) == sorted([OLD, ["source.removed", "source.added"]])
    assert all(re.match(r"recruiting \(", gone) for gone in filed)


def test_the_audit_page_shows_them_to_the_owner_and_not_to_the_admin(world):
    named_again(world)
    [gone] = refiled(world.ada.view.list_audit())
    with TestClient(create_app(world.store, public_url=BASE), base_url=BASE) as client:
        client.cookies.set(SESSION_COOKIE, world.brook.token)
        seen = client.get("/audit")
        assert seen.status_code == 200
        assert "recruiting" in seen.text  # the new source's source.added
        assert gone not in seen.text and "source.synced" not in seen.text
        assert "ops" in seen.text
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE, world.ada.token)
        seen = client.get("/audit")
        assert seen.status_code == 200
        assert gone in seen.text and "source.synced" in seen.text


# Finding 16 and rule (b): every source event for every manager, project names per viewer


def moved_out_of_private(world):
    """Ada sends "recruiting" into her private project "hiring" too, then moves it to the open
    project "ops" alone. Brook (admin) never reached "hiring"."""
    world.ada.view.create_project("hiring", everyone="none")
    world.ada.view.create_project("ops")
    world.ada.view.add_source("recruiting", "langfuse", {**SOURCE, "projects": ["ops"]})
    world.ada.view.enable_source("recruiting", ["ops", "hiring"])
    world.ada.view.enable_source("recruiting", ["ops"])


def enabled_projects(events) -> list[list[str]]:
    return [e.detail["projects"] for e in events if e.action == "source.enabled"]


def test_an_admin_never_reads_a_project_name_out_of_reach_in_an_old_source_event(world):
    moved_out_of_private(world)
    seen = world.brook.view.list_audit()
    assert enabled_projects(seen) == [["ops"], ["ops"]]
    assert "hiring" not in str([e.detail for e in seen if e.action.startswith("source.")])
    assert enabled_projects(world.ada.view.list_audit()) == [["ops"], ["ops", "hiring"]]


def test_every_manager_reads_every_source_event_and_a_member_none(world):
    world.ada.view.create_project("hiring", everyone="none")
    world.ada.view.add_source("recruiting", "langfuse", {**SOURCE, "projects": ["hiring"]})
    world.ada.view.disable_source("recruiting")
    world.ada.view.enable_source("recruiting")
    actions = ["source.enabled", "source.disabled", "source.added"]
    assert of_the_source(world.brook.view.list_audit()) == actions
    assert enabled_projects(world.brook.view.list_audit()) == [[]]  # "hiring" left out
    assert of_the_source(world.ada.view.list_audit()) == actions
    assert of_the_source(world.cy.view.list_audit()) == []  # a member: nothing of sources
    narrowed = world.store.for_account(world.ada.account_id).narrow(None, "read")
    assert narrowed.list_audit() == []


def test_a_deleted_project_is_named_in_source_events_to_the_operator_only(world):
    hiring = world.ada.view.create_project("hiring")
    world.ada.view.create_project("ops")
    world.ada.view.add_source("recruiting", "langfuse", {**SOURCE, "projects": ["ops"]})
    world.ada.view.enable_source("recruiting", ["ops", "hiring"])
    world.ada.view.delete_project(hiring.id)
    for view in (world.ada.view, world.brook.view):
        assert enabled_projects(view.list_audit()) == [["ops"]], view
    operator = world.store.for_account(world.ada.account_id)
    assert enabled_projects(operator.list_audit()) == [["ops", f"{hiring.id} (deleted)"]]
