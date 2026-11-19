"""The page of a project's notes (review: UI and UX, note 8): /b/<project>/notes lists them 50
at a time, newest first, each with the page it is filed under or that it waits for one, and
for whoever may forget notes the link to the page that forgets it. Fixtures are synthetic."""

import logging
import re
from html import unescape

import pytest
from starlette.testclient import TestClient

from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.memory import READ_EVENTS
from cairn.server import create_app
from cairn.store import Store

BASE = "https://cairn.example.test"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
ISSUER = "https://id.example.test/pool-notes"
SECRET_LINE = "Zebrafinch rotates the relay at dawn."


def visible(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split()).replace(" .", ".")


def main_of(html: str) -> str:
    return html.split('<main id="main">', 1)[1].split("</main>", 1)[0]


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


@pytest.fixture
def client(store):
    key = store.create_key("laptop")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as c:
        c.post("/login", data={"key": key}, headers=SAME_ORIGIN)
        yield c


def add(store, project, n: int, text: str | None = None):
    made, _ = store.add_note(project.id, text or f"# Topic {n}\n\nQuill line {n}.\n")
    return made


def test_the_page_lists_the_notes_newest_first_without_their_text(store, client):
    project = store.create_project("ops")
    waiting = add(store, project, 1, f"# Relay\n\n{SECRET_LINE}\n")
    filed = add(store, project, 2)
    assert compile_note(store, filed, gate=lint_gate).status == "saved"
    shown = client.get("/b/ops/notes")
    assert shown.status_code == 200
    main = main_of(shown.text)
    assert main.count("<h1>") == 1 and "<h1>ops notes</h1>" in main
    crumbs = '<nav class="crumbs" aria-label="Breadcrumb"><a href="/">Projects</a> /'
    assert f'{crumbs} <a href="/b/ops">ops</a> /</nav>' in main
    assert "<script" not in shown.text and "style=" not in shown.text
    assert SECRET_LINE not in shown.text
    text = visible(main)
    assert text.index("Topic 2") < text.index("Relay")  # newest first
    assert '<a href="/b/ops/p/topic-2">topic-2</a>' in main
    assert "Waiting for its page" in text
    # The workspace's key may forget notes: each row links the page that forgets it.
    for note in (waiting, filed):
        link = f'href="/b/ops/notes/{note.id}/forget" aria-label="Forget {note.title}"'
        assert link in main
    assert "added on the server's command line" in text  # who wrote it: the store's own


def test_fifty_at_a_time_with_a_link_to_older_notes(store, client):
    project = store.create_project("ops")
    notes = [add(store, project, n) for n in range(51)]
    first = main_of(client.get("/b/ops/notes").text)
    assert first.count("<tr>") == 1 + 50  # the head and 50 rows
    older = f'href="/b/ops/notes?before={notes[1].id}"'
    assert older in first and ">Older notes</a>" in first
    second = main_of(client.get(f"/b/ops/notes?before={notes[1].id}").text)
    assert second.count("<tr>") == 1 + 1 and "Topic 0" in visible(second)
    assert "Older notes" not in second and 'href="/b/ops/notes">Newest notes</a>' in second


def test_a_project_without_notes_says_so(store, client):
    store.create_project("ops")
    shown = client.get("/b/ops/notes")
    assert shown.status_code == 200 and "No notes yet" in visible(shown.text)


def test_a_project_out_of_reach_is_not_found(store, client):
    assert client.get("/b/nowhere/notes").status_code == 404


def test_the_project_page_links_the_notes_beside_its_other_links(store, client):
    project = store.create_project("ops")
    add(store, project, 1)
    main = main_of(client.get("/b/ops").text)
    meta = main.split('href="/b/ops/graph">Graph</a>', 1)[1].split("</p>", 1)[0]
    assert '<a href="/b/ops/notes">Notes</a>' in meta


def test_a_member_who_may_not_forget_sees_no_forget_link(store):
    for name in ("Ada", "Cy"):  # Ada owns the workspace; Cy is a member
        signed = store.sign_in(
            "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER,
            join="default",
        )  # fmt: skip
    project = store.create_project("ops")  # the workspace's: Cy reaches it and did not make it
    add(store, project, 1)
    cookie = {"cookie": f"__Host-cairn_session={signed.token}"}
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as c:
        shown = c.get("/b/ops/notes", headers=cookie)
    assert shown.status_code == 200 and "Topic 1" in visible(shown.text)
    assert "/forget" not in shown.text


def test_the_read_is_on_the_access_log_and_with_audit_reads_an_event(store, client, caplog):
    project = store.create_project("ops")
    add(store, project, 1, f"# Relay\n\n{SECRET_LINE}\n")
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        assert client.get("/b/ops/notes").status_code == 200
    (record,) = [r for r in caplog.records if r.name == "cairn.access"]
    assert (record.action, record.project, record.status) == ("notes", "ops", 200)
    assert READ_EVENTS["notes"] == "read.notes"
    assert not [e for e in store.list_audit() if e.action.startswith("read.")]
    store.reads_audited = True
    assert client.get("/b/ops/notes").status_code == 200
    (event,) = [e for e in store.list_audit() if e.action.startswith("read.")]
    assert (event.action, event.project, event.detail) == ("read.notes", "ops", {})
    assert "Relay" not in str(event.model_dump()) and SECRET_LINE not in str(event.model_dump())
