"""Unit tests for the access log of the web view: a page that shows memory leaves one record
on "cairn.access", as an MCP call or a REST request does (cairn.memory, Memory.access), and
never the query a person typed or a page's title."""

import logging

import pytest
from starlette.testclient import TestClient

from cairn.compiler import compile_note
from cairn.server import create_app
from cairn.store import Store

BASE = "https://cairn.example.test"
ISSUER = "https://id.acme.example/pool-1"
CANARY_TITLE = "Quillfeather rollback"
CANARY_QUERY = "zebracanary"
NOTE = f"# {CANARY_TITLE}\n\n- Brook owns the rollback.\n"


@pytest.fixture
def world(tmp_path):
    store = Store(tmp_path / "cairn.db")
    signed = store.sign_in(
        "sso", "sub-ada", "ada@acme.example", "Ada", issuer=ISSUER, join="default"
    )
    view = store.for_account(signed.account_id, signed.user.id)
    project = view.create_project("team")
    note, _ = view.add_note(project.id, NOTE)
    assert compile_note(view, note).status == "saved"
    (page,) = view.list_pages(project.id)
    headers = {"cookie": f"cairn_session={signed.token}"}
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        yield client, headers, signed.user.id, note, page
    store.close()


def records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "cairn.access"]


def test_each_web_read_of_memory_is_one_access_record(world, caplog):
    client, headers, user_id, note, page = world
    reads = [
        ("/", "projects", None),
        ("/b/team", "overview", "team"),
        (f"/b/team/p/{page.slug}", "page", "team"),
        (f"/b/team/p/{page.slug}?version=1", "page", "team"),
        (f"/search?q={CANARY_QUERY}", "recall", None),
        (f"/search?q={CANARY_QUERY}&project=team", "recall", "team"),
        ("/b/team/access", "project_access", "team"),
        ("/b/team/delete", "ask_delete_project", "team"),
        (f"/b/team/notes/{note.id}/forget", "ask_forget", "team"),
    ]
    for path, action, project in reads:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="cairn.access"):
            assert client.get(path, headers=headers).status_code == 200, path
        seen = [
            (r.actor_kind, r.actor, r.actor_id, r.action, r.project, r.status)
            for r in records(caplog)
        ]
        assert seen == [("session", "Ada", user_id, action, project, 200)], path
        assert CANARY_QUERY not in caplog.text and CANARY_TITLE not in caplog.text, path
        assert page.slug not in caplog.text and note.id not in caplog.text, path


def test_a_refused_web_read_is_recorded_with_its_status_and_not_what_was_asked(world, caplog):
    client, headers, _, _, _ = world
    refused = [
        ("/b/no-such-project", "overview", None, 404),
        ("/b/team/p/no-such-page", "page", "team", 404),
        ("/b/team/p/no-such-page?version=x", "page", "team", 400),
        ("/search?q=x&project=no-such-project", "recall", None, 404),
        ("/b/no-such-project/access", "project_access", None, 404),
    ]
    for path, action, project, status in refused:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="cairn.access"):
            assert client.get(path, headers=headers).status_code == status, path
        seen = [(r.action, r.project, r.status) for r in records(caplog)]
        assert seen == [(action, project, status)], path
        assert "no-such" not in caplog.text, path


@pytest.mark.parametrize(
    "path", ["/keys", "/sets", "/agents", "/members", "/workspace", "/setup", "/new", "/b/team/new"]
)
def test_pages_that_show_no_memory_leave_no_access_record(world, caplog, path):
    """The workspace's settings, people and credentials, and the composer's empty form: not
    a read of what the workspace holds (what they change is in the audit log)."""
    client, headers, _, _, _ = world
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        assert client.get(path, headers=headers).status_code == 200, path
    assert records(caplog) == []
