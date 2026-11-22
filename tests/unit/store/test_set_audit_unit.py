"""Unit tests for the audit events of project sets: creating, changing and deleting a set is
recorded in the same write, under whoever did it, with the set's name and its projects' ids;
a reader is shown only the ids of the projects they reach."""

import pytest
from starlette.testclient import TestClient

from cairn.server import SESSION_COOKIE, create_app
from cairn.store import Store

BASE = "http://127.0.0.1:8765"
ISSUER = "https://id.example.test"


@pytest.fixture
def world(tmp_path):
    """Ada owns the default workspace; Brook is an admin who reaches `ops` but not Ada's
    private `hiring`."""
    store = Store(tmp_path / "cairn.db")
    signed = {
        name: store.sign_in(
            "sso", f"s-{name}", f"{name}@example.test", name.title(), issuer=ISSUER,
            join="default",
        )
        for name in ("ada", "brook")
    }  # fmt: skip
    ada = store.for_account(signed["ada"].account_id, signed["ada"].user.id)
    ada.set_role(signed["brook"].user.id, "admin")
    brook = store.for_account(signed["brook"].account_id, signed["brook"].user.id)
    ops, hiring = ada.create_project("ops"), ada.create_project("hiring", everyone="none")
    yield store, signed, ada, brook, ops, hiring
    store.close()


def set_events(view: Store) -> list:
    return [e for e in reversed(view.list_audit()) if e.action.startswith("set.")]


def test_creating_changing_and_deleting_a_set_is_recorded_under_whoever_did_it(world):
    _, signed, ada, _, ops, hiring = world
    made = ada.create_set("work", (ops.id, hiring.id))
    ada.update_set(made.id, (ops.id,))
    assert ada.delete_set(made.id) is True
    events = set_events(ada)
    assert [(e.action, e.actor_kind, e.actor_id) for e in events] == [
        ("set.created", "system", None),
        ("set.changed", "system", None),
        ("set.deleted", "system", None),
    ]
    assert [(e.detail["set"], e.detail["projects"]) for e in events] == [
        ("work", [ops.id, hiring.id]),
        ("work", [ops.id]),
        ("work", [ops.id]),
    ]
    assert all(e.detail["user"] == signed["ada"].user.id for e in events)


def test_a_set_change_that_fails_records_nothing(world):
    _, _, ada, brook, ops, _ = world
    made = ada.create_set("work", (ops.id,))
    with pytest.raises(ValueError):
        ada.update_set(made.id, ("br_000000000000",))
    with pytest.raises(PermissionError):
        brook.narrow(access="read").delete_set(made.id)
    assert [e.action for e in set_events(ada)] == ["set.created"]


def test_a_reader_sees_only_the_projects_of_a_set_they_reach(world):
    store, signed, ada, brook, ops, hiring = world
    ada.create_set("work", (ops.id, hiring.id))
    (seen,) = set_events(brook)
    assert seen.detail["projects"] == [ops.id]
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        client.cookies.set(SESSION_COOKIE, signed["brook"].token)
        page = client.get("/audit")
    assert page.status_code == 200 and "set.created" in page.text
    assert ops.id in page.text and hiring.id not in page.text
    assert "keys, sets of projects, connected agents" in page.text  # it says sets are recorded
