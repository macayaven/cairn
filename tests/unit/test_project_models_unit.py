"""Unit tests for a project kept from every model (M4): the switch in the store, who changes it,
where it is kept and what happens to it. The inner TDD loop under
tests/acceptance/test_m4_a_project_no_model_reads.py. Fixtures are synthetic."""

import io
import sqlite3
from pathlib import Path

import pytest

from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.store import MODELS, Store
from cairn.store.access import MODELS_PREFIX

ISSUER = "https://id.example.test/pool-m4"


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


def person(store, name):
    signed = store.sign_in(
        "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER, join="default"
    )
    return store.for_account(signed.account_id, signed.user.id)


@pytest.fixture
def team(store):
    """Ada owns the workspace, Brook administers it, Cy and Dee are members. Cy made "journal",
    private, with Dee at "manage"; Ada made "handbook", open to everyone."""
    ada, brook, cy, dee = (person(store, name) for name in ("Ada", "Brook", "Cy", "Dee"))
    ada.set_role(brook.user_id, "admin")
    handbook = ada.create_project("handbook")
    journal = cy.create_project("journal", everyone="none")
    cy.set_project_access(journal.id, people={dee.user_id: "manage"})
    return ada, brook, cy, dee, handbook, journal


def rows(db: Path) -> list[tuple]:
    """The settings rows of the switch, every workspace's."""
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT account_id, name, value FROM settings WHERE name LIKE 'project.models:%'"
            " ORDER BY name"
        ).fetchall()
    finally:
        con.close()


def test_a_project_is_allowed_until_someone_switches_it(store, team, db):
    _, _, cy, _, handbook, journal = team
    assert MODELS == ("allowed", "never")
    assert store.kept_from_models([handbook.id, journal.id]) == frozenset()
    assert cy.set_project_models(journal.id, "never") == "never"
    assert store.kept_from_models([handbook.id, journal.id]) == {journal.id}
    assert rows(db) == [(store.account_id, MODELS_PREFIX + journal.id, "never")]
    assert cy.set_project_models(journal.id, "allowed") == "allowed"
    assert store.kept_from_models([journal.id]) == frozenset()
    assert rows(db) == [(store.account_id, MODELS_PREFIX + journal.id, "allowed")]  # Privacy 4


def test_setting_the_state_it_has_writes_nothing(store, team, db):
    _, _, cy, _, _, journal = team
    cy.set_project_models(journal.id, "never")
    events = store.list_audit(limit=500)
    cy.set_project_models(journal.id, "never")
    store.set_project_models(journal.id, "never")
    assert store.list_audit(limit=500) == events
    cy.set_project_models(journal.id, "allowed")
    events = store.list_audit(limit=500)
    cy.set_project_models(journal.id, "allowed")
    assert store.list_audit(limit=500) == events


def test_a_change_is_one_event_with_the_project_its_id_and_the_new_state(store, team):
    _, _, _, dee, _, journal = team
    dee.set_project_models(journal.id, "never")
    (event,) = [e for e in store.list_audit(limit=500) if e.action == "project.models_changed"]
    assert event.project == "journal"
    assert event.detail == {"project_id": journal.id, "models": "never", "user": dee.user_id}
    store.set_project_models(journal.id, "allowed")  # no person: none named
    latest = store.list_audit(limit=1)[0]
    assert (latest.action, latest.detail) == (
        "project.models_changed",
        {"project_id": journal.id, "models": "allowed"},
    )


def test_who_may_change_it_is_who_may_change_who_reaches_it(store, team, db):
    ada, brook, cy, dee, handbook, journal = team
    for view in (ada, cy, dee, store):  # the owner, the maker, "manage", no person
        view.set_project_models(journal.id, "never")
        view.set_project_models(journal.id, "allowed")
    with pytest.raises(PermissionError):
        brook.set_project_models(handbook.id, "never")  # an admin who reaches it
    with pytest.raises(PermissionError):
        cy.set_project_models(handbook.id, "never")  # a member who writes there
    with pytest.raises(KeyError):
        brook.set_project_models(journal.id, "never")  # out of his reach
    with pytest.raises(KeyError):
        ada.set_project_models("br_000000000000", "never")
    with pytest.raises(PermissionError):
        ada.narrow([journal.id]).set_project_models(journal.id, "never")
    with pytest.raises(PermissionError):
        ada.narrow(None, "read").set_project_models(journal.id, "never")
    assert rows(db) == [(store.account_id, MODELS_PREFIX + journal.id, "allowed")]


@pytest.mark.parametrize("value", ["", "Never", "send", "withhold", None, True])
def test_another_value_is_refused_before_anything_is_read(store, team, db, value):
    _, brook, _, _, _, journal = team
    with pytest.raises(ValueError):
        brook.set_project_models(journal.id, value)  # out of reach, but the value comes first
    with pytest.raises(ValueError):
        store.set_project_models("br_000000000000", value)
    assert rows(db) == []


def test_a_person_learns_only_of_the_projects_they_reach(store, team):
    _, brook, cy, _, handbook, journal = team
    cy.set_project_models(journal.id, "never")
    store.set_project_models(handbook.id, "never")
    assert brook.kept_from_models([handbook.id, journal.id]) == {handbook.id}
    assert store.narrow([handbook.id]).kept_from_models([journal.id]) == frozenset()
    assert cy.kept_from_models([journal.id, "br_000000000000"]) == {journal.id}
    assert cy.kept_from_models([]) == frozenset()


def test_the_setting_calls_do_not_read_or_write_it(store, team):
    _, _, cy, _, _, journal = team
    cy.set_project_models(journal.id, "never")
    with pytest.raises(ValueError):
        store.get_setting(MODELS_PREFIX + journal.id)
    with pytest.raises(ValueError):
        store.set_setting(MODELS_PREFIX + journal.id, "never")


def test_a_page_is_read_with_whether_its_project_is_kept(store, team):
    _, _, cy, _, handbook, journal = team
    note, _ = cy.add_note(journal.id, "# Diary\n\nThe crate went north.\n")
    compile_note(cy, note, gate=lint_gate)
    page = store.get_page(journal.id, "diary")
    assert store.page_for_model(journal.id, "diary") == (page, False, False)
    cy.set_project_models(journal.id, "never")
    assert store.page_for_model(journal.id, "diary") == (page, True, False)
    assert store.page_for_model(journal.id, "nothing") is None
    assert store.page_for_model(handbook.id, "diary") is None
    other = store.for_account(store.create_account("Other").id)
    assert other.page_for_model(journal.id, "diary") is None  # another workspace's


def test_a_deleted_project_leaves_nothing_of_it(store, team, db):
    _, _, cy, _, handbook, journal = team
    cy.set_project_models(journal.id, "never")
    store.set_project_models(handbook.id, "never")
    assert cy.delete_project(journal.id) is True
    assert rows(db) == [(store.account_id, MODELS_PREFIX + handbook.id, "never")]


def test_a_deleted_workspace_leaves_nothing_of_it(store, db):
    other = store.for_account(store.create_account("Other").id)
    project = other.create_project("diary", kind="personal")
    assert other.kept_from_models([project.id]) == {project.id}
    assert store.delete_account(other.account_id) is True
    assert rows(db) == []


def test_a_project_private_by_its_kind_starts_at_never(store, team):
    ada, _, cy, _, _, _ = team
    made = [
        cy.create_project("diary", kind="personal"),
        cy.create_project("open-diary", kind="personal", everyone="write"),
        *ada.create_projects([("notebook", "personal"), ("ledger", "general")]),
        cy.create_project("plans"),
        cy.create_project("hidden", everyone="none"),  # private by choice, not by its kind
    ]
    kept = store.kept_from_models([project.id for project in made])
    assert {p.name for p in made if p.id in kept} == {"diary", "open-diary", "notebook"}


def test_an_import_brings_the_switch_and_starts_a_private_kind_at_never(store, team, tmp_path):
    ada, _, cy, _, _, journal = team
    cy.set_project_models(journal.id, "never")
    ada.create_project("diary", kind="personal")
    out = io.BytesIO()
    ada.export(out)
    elsewhere = Store(tmp_path / "elsewhere.db")
    try:
        elsewhere.import_export(io.BytesIO(out.getvalue()))
        projects = {p.name: p for p in elsewhere.list_projects()}
        assert set(projects) == {"handbook", "journal", "diary"}
        kept = elsewhere.kept_from_models([p.id for p in projects.values()])
        # journal was at never, and the archive says so; diary is of a private kind
        assert kept == {projects["journal"].id, projects["diary"].id}
        assert sorted(rows(tmp_path / "elsewhere.db")) == sorted(
            (elsewhere.account_id, MODELS_PREFIX + projects[name].id, "never")
            for name in ("journal", "diary")
        )
    finally:
        elsewhere.close()


def test_a_refused_import_leaves_no_switch(store, team, tmp_path, monkeypatch):
    """The row is written in the import's own transaction: an import that fails after its
    projects are written leaves neither them nor a row."""
    from cairn.store import portable

    ada = team[0]
    diary = ada.create_project("diary", kind="personal")
    ada.add_note(diary.id, "# Reading list\n\n- Finish the atlas.\n", via="mcp")
    out = io.BytesIO()
    ada.export(out)
    elsewhere = Store(tmp_path / "elsewhere.db")

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(portable, "_write_note", broken)
    try:
        with pytest.raises(sqlite3.OperationalError):
            elsewhere.import_export(io.BytesIO(out.getvalue()))
        assert elsewhere.list_projects() == []
        assert rows(tmp_path / "elsewhere.db") == []
    finally:
        elsewhere.close()
