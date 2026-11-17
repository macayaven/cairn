"""Unit tests for the two pages that ask before they remove, and the composer refusing a
forgotten note (H5b): what tests/acceptance/test_h5b_forget.py does not pin."""

import logging

import pytest
from starlette.testclient import TestClient

from cairn.compiler import compile_note
from cairn.server import create_app
from cairn.store import Store

BASE = "https://cairn.example.test"
ISSUER = "https://id.acme.example/pool-1"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
WEEKLY = "# Weekly\n\n- Brook owns the rollback.\n"
HIRING = "# Hiring\n\nDee starts in March.\n"


@pytest.fixture
def world(tmp_path):
    store = Store(tmp_path / "cairn.db")
    signed = store.sign_in(
        "sso", "sub-ada", "ada@acme.example", "Ada", issuer=ISSUER, join="default"
    )
    view = store.for_account(signed.account_id, signed.user.id)
    project = view.create_project("team")
    notes = []
    for text in (WEEKLY, WEEKLY.replace("rollback", "release"), HIRING):
        note, _ = view.add_note(project.id, text, source_ref="mail:1")
        assert compile_note(view, note).status == "saved"
        notes.append(note)
    headers = {"cookie": f"cairn_session={signed.token}", **SAME_ORIGIN}
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        yield store, view, client, headers, notes
    store.close()


def test_the_forget_page_says_what_changes_what_goes_and_what_it_means(world):
    _, _, client, headers, (weekly, _, hiring) = world
    shown = client.get(f"/b/team/notes/{weekly.id}/forget", headers=headers).text
    assert "Pages that change" in shown and 'href="/b/team/p/weekly"' in shown
    assert "Pages that go" not in shown
    for said in ("Every earlier version", "cannot be undone", "will not be stored in this project"):
        assert said in shown, said
    assert "mail:1" in shown and "<time datetime=" in shown
    gone = client.get(f"/b/team/notes/{hiring.id}/forget", headers=headers).text
    assert "Pages that go" in gone and 'href="/b/team/p/hiring"' in gone


def test_the_delete_page_counts_what_goes_and_asks_again_after_another_name(world):
    store, _, client, headers, _ = world
    shown = client.get("/b/team/delete", headers=headers).text
    assert "3 notes" in shown and "2 pages" in shown and "cannot be undone" in shown
    wrong = client.post("/b/team/delete", data={"confirm": "Team"}, headers=headers)
    assert wrong.status_code == 400 and 'role="alert"' in wrong.text
    assert 'name="confirm"' in wrong.text
    assert store.for_account(store.account_id).get_project("team") is not None


def test_the_composer_says_a_note_was_forgotten_and_keeps_what_was_typed(world):
    _, view, client, headers, (weekly, _, _) = world
    view.forget_note(weekly.id)
    r = client.post("/b/team/notes", data={"text": WEEKLY}, headers=headers)
    assert r.status_code == 409 and "forgotten" in r.text and "Brook owns the rollback" in r.text


def test_the_rest_calls_are_logged_under_their_own_actions(world, caplog):
    _, view, client, _, (weekly, _, _) = world
    key = {"x-api-key": view.create_key("script")}
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        assert (
            client.delete(f"/api/projects/team/notes/{weekly.id}", headers=key).status_code == 200
        )
        assert client.delete("/api/projects/team?confirm=team", headers=key).status_code == 200
    actions = [(r.action, r.project, r.status) for r in caplog.records if hasattr(r, "action")]
    assert actions == [("forget", "team", 200), ("delete_project", "team", 200)]
    assert weekly.id not in caplog.text  # ids of what went are in the audit log, not here


def test_a_post_from_another_site_removes_nothing(world):
    _, view, client, headers, (weekly, _, _) = world
    cross = {**headers, "sec-fetch-site": "cross-site"}
    forget = client.post(f"/b/team/notes/{weekly.id}/forget", headers=cross)
    delete = client.post("/b/team/delete", data={"confirm": "team"}, headers=cross)
    assert forget.status_code == delete.status_code == 403
    assert view.get_note(weekly.id) is not None and view.get_project("team") is not None
    assert "note.forgotten" not in [e.action for e in view.list_audit()]
