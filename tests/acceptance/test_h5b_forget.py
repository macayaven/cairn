"""H5b acceptance tests (frozen): one project can be deleted, and one note forgotten.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/store/__init__.py and src/cairn/server.py.

Why: H5 lets an owner take everything a workspace holds, or erase all of it. A memory that
holds meetings and mail needs something smaller as well: this project, this note.

The store
- `view.delete_project(project_id) -> bool` removes the project: its notes, their spans and
  facts, its pages with every version, its part of the search index and its access rule.
  It leaves every project set that named it, and a key or agent limited to some projects
  loses that one (one limited to it alone then reaches nothing, never everything). The
  name is free again at once. False for a project that is unknown or out of reach.
- `view.forget_note(note_id) -> Forgotten` removes one note with its spans and
  facts. `Forgotten` has `note_id`, `rebuilt` and `removed` (page slugs). A page that
  cited the note loses every earlier version and continues as one new version, numbered
  one past its latest: the claims that cite other notes, in their order, with the same ids
  and text, its body `render_body` of them. A page left with no claim is removed. The
  search index follows. KeyError for a note that is unknown or out of reach.
- A forgotten note stays forgotten: writing the same text to that project again raises
  `cairn.store.ForgottenNote` (a ValueError), whatever its source_ref, until the project is
  deleted. The store keeps a digest for that, never the text.
- Who may do either: an owner of the workspace, the person who made the project, or a
  credential of the workspace that acts for no person. Anyone else who reaches the project
  gets PermissionError, and so does any connection limited to some projects or to reading,
  whoever approved it. Out of reach is checked first, and is exactly unknown.
- What is removed is zeroed in the database's files at once, as when a workspace is
  deleted: the note's text, the page bodies and the index entries. Short index keys (a
  slug, a name) can remain until `compact()`, as H5 says.
- `stored_bytes()` goes down by what was removed. The month's count of notes written does
  not: they were written.
- The audit log gets `project.deleted` (the project's id and name, how many notes and pages)
  and `note.forgotten` (the note's id, its project, how many pages changed) under whoever
  did it, and nothing of the text, the title or the source_ref.
- An export made afterwards holds no trace of what was removed.

The service
- GET /b/<project>/delete: a form that asks for the project's name; POST with `confirm` equal
  to the name deletes it and redirects to /. Another `confirm` is 400.
- GET /b/<project>/notes/<note id>/forget: a page that names the note and the pages it
  built; POST forgets it and redirects to /b/<project>.
- Both answer 403 to someone who reaches the project and may not, and 404 when the project or
  the note is out of reach. A project's page and a page's sources link to them only for
  people they will open for.
- REST: DELETE /api/projects/<project>?confirm=<name> answers 200 `{"deleted": true}` (400
  without the name); DELETE /api/projects/<project>/notes/<note id> answers 200 with
  `rebuilt` and `removed`. 403 and 404 as above. Writing a forgotten note again is 409 over
  REST and a tool error over MCP, each saying it was forgotten.
- MCP keeps its seven tools: forgetting is a person's act, or a key's over REST.
"""

import io
import json
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.compiler import compile_note, render_body
from cairn.server import create_app
from cairn.store import Store

BASE = "https://cairn.example.test"
ISSUER = "https://id.acme.example/pool-1"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
TOOLS = ["projects", "new_project", "note", "recall", "page", "evidence", "overview"]

# Three notes on one topic and one on another. Each body has a word no other text has.
FIRST = (
    "# Payments weekly\n\n- Quill ships the cobaltfinch fix on Thursday.\n"
    "- Brook owns the rollback.\n"
)
SECOND = "# Payments weekly\n\n## Decided\n\n- Lumen keeps its own saffronwren rule.\n"
THIRD = "# Payments weekly\n\nThe fix moved to Friday, said the umberlark memo.\n"
ALONE = "# Hiring\n\nDee starts on the first of March, per the slatepipit letter.\n"
WORDS = {
    "FIRST": "cobaltfinch",
    "SECOND": "saffronwren",
    "THIRD": "umberlark",
    "ALONE": "slatepipit",
}


def add(view: Store, project_id: str, text: str, **fields):
    note, created = view.add_note(project_id, text, **fields)
    assert created and compile_note(view, note).status == "saved"
    return note


def files(db: Path) -> list[Path]:
    return [p for p in db.parent.iterdir() if p.name.startswith(db.name)]


def in_files(db: Path, word: str) -> bool:
    return any(word.encode() in path.read_bytes().lower() for path in files(db))


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def kind_of(action, *arguments) -> str:
    try:
        action(*arguments)
    except Exception as error:
        return type(error).__name__
    return "ok"


class World:
    """The default workspace. Ada signs in first and owns it; Brook is an admin; Cy and Dee
    are members. `meetings` is Cy's project, open to every member; `ledger` is Ada's."""

    def __init__(self, tmp_path) -> None:
        self.db = tmp_path / "cairn.db"
        self.store = Store(self.db)
        self.app = create_app(self.store, public_url=BASE)
        self.account = self.store.account_id
        self.ada, self.brook, self.cy, self.dee = (
            self.person(name) for name in ("Ada", "Brook", "Cy", "Dee")
        )
        self.ada.view.set_role(self.brook.id, "admin")
        self.meetings = self.cy.view.create_project("meetings", kind="meetings")
        self.ledger = self.ada.view.create_project("ledger")
        self.first = add(self.cy.view, self.meetings.id, FIRST, source_ref="meeting:m-41")
        self.second = add(self.dee.view, self.meetings.id, SECOND, source_ref="meeting:m-42")
        self.third = add(self.cy.view, self.meetings.id, THIRD, source_ref="meeting:m-41")
        self.alone = add(self.cy.view, self.meetings.id, ALONE)
        add(self.ada.view, self.ledger.id, "# Ledger\n\nThe ledger closes on the last Friday.\n")

    def person(self, name: str) -> SimpleNamespace:
        email = f"{name.lower()}@acme.example"
        signed = self.store.sign_in(
            "sso", f"sub-{name}", email, name, issuer=ISSUER, join="default"
        )
        return SimpleNamespace(
            id=signed.user.id,
            name=name,
            cookie={"cookie": f"cairn_session={signed.token}"},
            view=self.store.for_account(signed.account_id, signed.user.id),
        )


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path)
    yield made
    made.store.close()


@pytest.fixture
def client(world):
    with TestClient(world.app, base_url=BASE) as opened:
        yield opened


def texts(view: Store, project_id: str, slug: str) -> list[str]:
    return [claim.text for claim in view.get_page(project_id, slug).claims]


# Forgetting one note


def test_a_forgotten_note_leaves_its_pages_and_the_rest_stays(world):
    cy, project = world.cy.view, world.meetings.id
    before = cy.get_page(project, "payments-weekly")
    assert before.version == 3 and cy.page_versions(project, "payments-weekly") == [1, 2, 3]
    kept = [c for c in before.claims if "cobaltfinch" not in c.text and "rollback" not in c.text]
    bytes_before, notes_before = cy.stored_bytes(), cy.usage().notes

    forgotten = cy.forget_note(world.first.id)
    assert (forgotten.note_id, forgotten.rebuilt, forgotten.removed) == (
        world.first.id,
        ("payments-weekly",),
        (),
    )
    after = cy.get_page(project, "payments-weekly")
    assert after.version == 4 and cy.page_versions(project, "payments-weekly") == [4]
    assert cy.get_page(project, "payments-weekly", 2) is None  # earlier versions held its text
    assert [(c.id, c.text, c.span_ids) for c in after.claims] == [
        (c.id, c.text, c.span_ids) for c in kept
    ]
    spans = {s.id: s for c in (world.second, world.third) for s in cy.note_spans(c.id)}
    assert after.body == render_body(after.title, after.claims, spans)
    assert "cobaltfinch" not in after.body and "saffronwren" in after.body

    assert cy.get_note(world.first.id) is None
    assert [c.id for c in cy.list_notes(project)] == [
        world.second.id,
        world.third.id,
        world.alone.id,
    ]
    assert cy.search("cobaltfinch") == [] and cy.search("rollback") == []
    assert [h.slug for h in cy.search("saffronwren")] == ["payments-weekly"]
    assert cy.stored_bytes() == bytes_before - len(FIRST.encode())
    assert cy.usage().notes == notes_before  # it was written this month all the same


def test_a_page_left_with_no_claim_is_removed(world):
    cy, project = world.cy.view, world.meetings.id
    forgotten = cy.forget_note(world.alone.id)
    assert (forgotten.rebuilt, forgotten.removed) == ((), ("hiring",))
    assert cy.get_page(project, "hiring") is None and cy.page_versions(project, "hiring") == []
    assert [p.slug for p in cy.list_pages(project)] == ["payments-weekly"]
    assert cy.search("slatepipit") == []
    assert kind_of(cy.forget_note, world.alone.id) == "KeyError"  # once


def test_a_forgotten_note_is_not_stored_again(world):
    from cairn.store import ForgottenNote

    cy, project = world.cy.view, world.meetings.id
    cy.forget_note(world.first.id)
    for ref in ("meeting:m-41", "meeting:other", None):
        with pytest.raises(ForgottenNote) as refused:
            cy.add_note(project, FIRST, source_ref=ref)
        assert isinstance(refused.value, ValueError) and "cobaltfinch" not in str(refused.value)
    assert "cobaltfinch" not in dump(world.db)  # a digest is kept, never the text
    # Other words are another note, and another project is another place.
    changed, created = cy.add_note(project, FIRST.replace("Thursday", "Monday"))
    assert created and changed.id != world.first.id
    _, elsewhere = world.ada.view.add_note(world.ledger.id, FIRST)
    assert elsewhere


def test_the_text_leaves_the_files_at_once(world):
    world.cy.view.forget_note(world.first.id)
    assert not in_files(world.db, "cobaltfinch")
    assert in_files(world.db, "saffronwren") and in_files(world.db, "umberlark")
    con = sqlite3.connect(world.db)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


# Deleting one project


def test_a_deleted_project_takes_everything_it_held(world):
    ada, cy = world.ada.view, world.cy.view
    team = ada.create_set("team", (world.meetings.id, world.ledger.id))
    only = ada.create_key("meetings-agent", projects=(world.meetings.id,))
    both = ada.create_key("report", projects=(world.meetings.id, world.ledger.id), access="read")
    bytes_before = ada.stored_bytes()
    held = sum(len(text.encode()) for text in (FIRST, SECOND, THIRD, ALONE))

    assert cy.delete_project(world.meetings.id) is True
    assert cy.delete_project(world.meetings.id) is False
    assert [b.name for b in ada.list_projects()] == ["ledger"]
    assert ada.get_note(world.first.id) is None
    for word in WORDS.values():
        assert ada.search(word) == [] and not in_files(world.db, word), word
    assert [h.slug for h in ada.search("ledger closes")] == ["ledger"]
    assert ada.stored_bytes() == bytes_before - held
    assert [s.project_ids for s in ada.list_sets() if s.id == team.id] == [(world.ledger.id,)]

    with TestClient(world.app, base_url=BASE) as http:
        seen = http.get("/api/projects", headers={"x-api-key": only}).json()["projects"]
        assert seen == []  # limited to a project that is gone: nothing, not everything
        seen = http.get("/api/projects", headers={"x-api-key": both}).json()["projects"]
        assert [b["name"] for b in seen] == ["ledger"]

    again = cy.create_project("meetings")  # the name is free, and nothing comes back with it
    assert again.id != world.meetings.id and cy.list_notes(again.id) == []
    _, created = cy.add_note(again.id, FIRST)  # what the old project forgot is not this one's
    assert created
    assert world.meetings.id not in outside_the_audit_log(world.db)


def outside_the_audit_log(db: Path) -> str:
    """The database's rows without the audit log's own, which may name a deleted project."""
    rows = dump(db).splitlines()
    return "\n".join(row for row in rows if not row.startswith('INSERT INTO "audit_events"'))


# Who may


def test_only_an_owner_the_projects_maker_or_the_workspaces_own_key_may(world):
    project = world.meetings.id
    for who in (world.brook, world.dee):  # an admin is a member here; Dee wrote a note there
        assert who.view.get_project(project) is not None
        assert kind_of(who.view.delete_project, project) == "PermissionError"
        assert kind_of(who.view.forget_note, world.second.id) == "PermissionError"
    for limited in (
        world.ada.view.narrow([project]),  # an owner's connection limited to some projects
        world.cy.view.narrow(None, "read"),  # the maker's, limited to reading
    ):
        assert kind_of(limited.delete_project, project) == "PermissionError"
        assert kind_of(limited.forget_note, world.second.id) == "PermissionError"
    assert world.cy.view.get_page(project, "payments-weekly").version == 3  # nothing changed

    # Out of reach is exactly unknown, checked before anything else.
    hidden = world.cy.view.create_project("drafts", everyone="none")
    note = add(world.cy.view, hidden.id, "# Draft\n\nA plan nobody has seen.\n")
    assert world.dee.view.delete_project(hidden.id) is False
    assert world.dee.view.delete_project("brn_000000000000") is False
    assert kind_of(world.dee.view.forget_note, note.id) == "KeyError"
    assert kind_of(world.dee.view.forget_note, "cap_000000000000") == "KeyError"

    # The maker, an owner, and the workspace's own credential may.
    assert world.cy.view.forget_note(world.second.id).rebuilt == ("payments-weekly",)
    assert world.ada.view.forget_note(world.third.id).rebuilt == ("payments-weekly",)
    operator = world.store.for_account(world.account)
    assert operator.forget_note(world.first.id).removed == ("payments-weekly",)
    assert operator.delete_project(project) is True


def test_the_audit_log_says_who_and_holds_none_of_it(world, client):
    post = {**world.cy.cookie, **SAME_ORIGIN}
    r = client.post(
        f"/b/meetings/notes/{world.first.id}/forget", headers=post, follow_redirects=False
    )
    assert (r.status_code, r.headers["location"]) == (303, "/b/meetings")
    r = client.post(
        "/b/meetings/delete", data={"confirm": "meetings"}, headers=post, follow_redirects=False
    )
    assert (r.status_code, r.headers["location"]) == (303, "/")
    events = {e.action: e for e in world.ada.view.list_audit()}
    forgotten, deleted = events["note.forgotten"], events["project.deleted"]
    assert (forgotten.actor_kind, forgotten.actor) == ("session", "Cy")
    assert (deleted.actor_kind, deleted.actor) == ("session", "Cy")
    assert world.first.id in json.dumps(forgotten.detail)
    assert "meetings" in json.dumps(deleted.detail)
    said = json.dumps([forgotten.detail, deleted.detail]).lower()
    for unsaid in (*WORDS.values(), "payments weekly", "meeting:m-41", "rollback"):
        assert unsaid not in said, unsaid


def test_an_export_afterwards_holds_no_trace(world):
    world.cy.view.forget_note(world.first.id)
    world.ada.view.delete_project(world.ledger.id)
    out = io.BytesIO()
    world.ada.view.export(out)
    with zipfile.ZipFile(out) as archive:
        everything = "\n".join(
            name + "\n" + archive.read(name).decode("utf-8", "replace")
            for name in archive.namelist()
        )
    assert "cobaltfinch" not in everything and world.first.id not in everything
    assert "ledger" not in everything.lower()
    assert "saffronwren" in everything and "umberlark" in everything


# The service


def test_the_web_view_asks_before_it_removes(world, client):
    def get(path, who):
        return client.get(path, headers=who.cookie, follow_redirects=False)

    def post(path, who, **data):
        headers = {**who.cookie, **SAME_ORIGIN}
        return client.post(path, data=data, headers=headers, follow_redirects=False)

    forget = f"/b/meetings/notes/{world.second.id}/forget"
    # Links show only for people the pages open for.
    page = get("/b/meetings/p/payments-weekly", world.cy).text
    assert f'href="{forget}"' in page
    assert 'href="/b/meetings/delete"' in get("/b/meetings", world.cy).text
    for who in (world.dee, world.brook):
        assert "/forget" not in get("/b/meetings/p/payments-weekly", who).text
        assert "/b/meetings/delete" not in get("/b/meetings", who).text
        assert get(forget, who).status_code == 403
        assert get("/b/meetings/delete", who).status_code == 403
        assert post(forget, who).status_code == 403
        assert post("/b/meetings/delete", who, confirm="meetings").status_code == 403

    asked = get(forget, world.ada)  # an owner
    assert asked.status_code == 200 and "Payments weekly" in asked.text
    assert "payments-weekly" in asked.text and "<script" not in asked.text.lower()
    assert get(f"/b/meetings/notes/{'cap_000000000000'}/forget", world.ada).status_code == 404
    assert get(f"/b/ledger/notes/{world.second.id}/forget", world.ada).status_code == 404

    confirm = get("/b/meetings/delete", world.cy)
    assert confirm.status_code == 200 and 'name="confirm"' in confirm.text
    assert post("/b/meetings/delete", world.cy, confirm="ledger").status_code == 400
    assert post("/b/meetings/delete", world.cy).status_code == 400
    assert world.cy.view.get_project("meetings") is not None

    done = post(forget, world.ada)
    assert (done.status_code, done.headers["location"]) == (303, "/b/meetings")
    assert "saffronwren" not in get("/b/meetings/p/payments-weekly", world.cy).text
    gone = post("/b/meetings/delete", world.cy, confirm="meetings")
    assert (gone.status_code, gone.headers["location"]) == (303, "/")
    assert get("/b/meetings", world.cy).status_code == 404
    assert get("/b/meetings/delete", world.cy).status_code == 404


def test_rest_deletes_and_mcp_keeps_its_seven_tools(world, client):
    whole = {"x-api-key": world.ada.view.create_key("owner-script")}
    narrow = {"x-api-key": world.ada.view.create_key("one-project", projects=(world.meetings.id,))}
    note = f"/api/projects/meetings/notes/{world.first.id}"

    assert client.delete(note, headers=narrow).status_code == 403
    assert (
        client.delete("/api/projects/meetings?confirm=meetings", headers=narrow).status_code == 403
    )
    assert (
        client.delete("/api/projects/meetings/notes/cap_000000000000", headers=whole).status_code
        == 404
    )
    assert (
        client.delete(f"/api/projects/ledger/notes/{world.first.id}", headers=whole).status_code
        == 404
    )

    done = client.delete(note, headers=whole)
    assert done.status_code == 200
    assert (done.json()["rebuilt"], done.json()["removed"]) == (["payments-weekly"], [])

    again = client.post("/api/projects/meetings/notes", headers=whole, json={"text": FIRST})
    assert again.status_code == 409 and "forgotten" in again.json()["error"].lower()
    assert "cobaltfinch" not in again.text

    def mcp(method: str, params: dict) -> dict:
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        r = client.post("/mcp", headers={**MCP_HEADERS, **whole}, json=body)
        assert r.status_code == 200, r.text
        return r.json()["result"]

    assert [tool["name"] for tool in mcp("tools/list", {})["tools"]] == TOOLS
    refused = mcp(
        "tools/call", {"name": "note", "arguments": {"project": "meetings", "text": FIRST}}
    )
    assert refused["isError"] is True and "forgotten" in refused["content"][0]["text"].lower()
    shown = mcp(
        "tools/call",
        {"name": "page", "arguments": {"project": "meetings", "slug": "payments-weekly"}},
    )
    assert "cobaltfinch" not in shown["content"][0]["text"]

    assert client.delete("/api/projects/meetings", headers=whole).status_code == 400
    assert client.delete("/api/projects/meetings?confirm=ledger", headers=whole).status_code == 400
    removed = client.delete("/api/projects/meetings?confirm=meetings", headers=whole)
    assert removed.status_code == 200 and removed.json() == {"deleted": True}
    assert client.get("/api/projects/meetings/pages", headers=whole).status_code == 404
    assert (
        client.delete("/api/projects/meetings?confirm=meetings", headers=whole).status_code == 404
    )
