"""Sentences of the guides about how notes are filed by topic (N1: tests/acceptance/
test_n1_note_filing.py), each pinned here so the guides cannot go false unnoticed. Each test
finds its sentences in the guide, as a person reads it (guide_source: the product's name
filled in), then asks cairn: the topic rule and the pairs the sources guide names, a note
filed once and how one is moved, what the run guide says of a waiting note and of the
`waiting` command, and what it says an upgrade keeps. Everything runs over a real store in a
temporary file, with made-up notes; a fake source kind stands in for a real one."""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from typing import ClassVar

import httpx
import pytest
import uvicorn
from guide_source import guide_source
from starlette.testclient import TestClient
from test_store_unit import v23_database

from cairn import server
from cairn.compiler import provenance_gate
from cairn.memory import FILED_ELSEWHERE, WAITING, WAITS, FiledOnce, Memory, NoteForgotten
from cairn.server import create_app
from cairn.sources import KINDS, NoteDraft, Pulled, SourceKind, run_source
from cairn.store import Store
from cairn.topics import topic_key, topic_tag
from cairn.web import cli, filing

Y2099 = datetime(2099, 3, 2, 9, 0, tzinfo=UTC)
BASE = "http://127.0.0.1:8765"


def flat(text: str) -> str:
    return " ".join(text.split())


def says(guide: str, *sentences: str) -> None:
    """Each sentence is in the guide, as a person reads it."""
    text = flat(guide_source(guide))
    for sentence in sentences:
        assert flat(sentence) in text, sentence


@pytest.fixture
def db(tmp_path):
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db, clock=lambda: Y2099)
    yield s
    s.close()


@pytest.fixture
def memory(store):
    store.create_project("ops")
    return Memory(store, provenance_gate)


def page_of(memory: Memory, n: int, *, title: str | None = None, topic: str | None = None):
    """The page a new note filed with `title` or `topic` lands on."""
    text = f"- Point {n} of the plan.\n"
    return memory.note("ops", text, title=title, topic=topic)["page"]


# sources.md: when two notes are the same topic


ONE = [
    ("Retry policy", "retry-policy", "RETRY_POLICY", "Retry policy!"),
    ("Café menu", "Cafe menu"),
]
OTHER = [
    ("C++", "C#", "C"),
    ("Release 2.10", "Release 2-10"),
    ("e-mail", "email"),
]


def test_the_topic_rule_and_its_pairs_are_what_the_store_files_by(memory):
    """sources.md: "A note's topic is its `topic`, else its title. Two topics are one when
    they differ only in letter case, in accents on Latin or Greek letters, in how their words
    are separated (spaces, hyphens, underscores and slashes alike) or in sentence
    punctuation at the edges of words: ..." """
    says(
        "sources",
        "A note's topic is its `topic`, else its title.",
        "Two topics are one when they differ only in letter case, in accents on Latin or Greek"
        " letters, in how their words are separated (spaces, hyphens, underscores and slashes"
        " alike) or in sentence punctuation at the edges of words:",
        '"Retry policy", "retry-policy", "RETRY_POLICY" and "Retry policy!" build one page,'
        ' and so do "Café menu" and "Cafe menu".',
        'Anything else makes another topic: "C++", "C#" and "C" are three, "Release 2.10" and'
        ' "Release 2-10" are two, "e-mail" and "email" are two, and two titles that differ'
        " only in their last word are two, however long they are.",
    )
    n = 0
    for group in ONE:
        pages = set()
        for place, name in enumerate(group):  # by title, then by topic: either is the topic
            n += 1
            pages.add(page_of(memory, n, **{"title" if place % 2 else "topic": name}))
        assert len(pages) == 1, group
    for group in OTHER:
        pages = set()
        for name in group:
            n += 1
            pages.add(page_of(memory, n, topic=name))
        assert len(pages) == len(group), group
    long = "Quarterly plan for the northern and the southern regions of the river delta " * 2
    assert page_of(memory, 90, title=long + "final") != page_of(memory, 91, title=long + "draft")


def test_a_page_keeps_its_address_and_another_topic_gets_it_with_a_tag(memory, store):
    """sources.md: "A page keeps the address its first note gave it. A note of another topic
    whose title would make the same address gets a page of its own, at that address with a
    short tag added.\""""
    says(
        "sources",
        "A page keeps the address its first note gave it. A note of another topic whose title"
        " would make the same address gets a page of its own, at that address with a short tag"
        " added.",
    )
    assert page_of(memory, 1, topic="C++") == "c"
    assert page_of(memory, 2, topic="C#") == f"c-{topic_tag(topic_key('C#'))}"
    assert page_of(memory, 3, topic="Retry policy") == "retry-policy"
    assert page_of(memory, 4, topic="RETRY POLICY!") == "retry-policy"  # its address stays
    assert store.get_page(store.get_project("ops").id, "retry-policy").title == "RETRY POLICY!"


# sources.md: a note is filed once, and how to move one


class Feed:
    answers: ClassVar[list[Pulled]] = []

    def __init__(self, name: str) -> None:
        self.name = name

    async def pull(self, cursor):
        return Feed.answers.pop(0)


@pytest.fixture
def feed(monkeypatch, store):
    def _open(name, config, **options):
        return Feed(name)

    monkeypatch.setitem(KINDS, "feed", SourceKind(check=dict, open=_open, via="feed"))
    Feed.answers = []
    store.add_source("ops-feed", "feed", {"projects": ["ops"]})
    return store


def test_a_note_is_filed_once_and_is_moved_reworded_or_to_another_project(memory, feed):
    """sources.md: "**A note is filed once.** The same text with the same `source_ref` is one
    note, filed on the page of the topic it was first sent with. Sent again with another
    topic, it is refused and nothing is stored: the answer names the page the note is on
    when that page holds it, and otherwise says that the note waits for its page or is on
    none, and a source's run skips it with the reason `filed under another topic`. To move a
    note,
    forget it on the page that cites it, then send it with its new topic, reworded or to
    another project: a text forgotten in a project is not stored there again.\""""
    says(
        "sources",
        "**A note is filed once.** The same text with the same `source_ref` is one note, filed"
        " on the page of the topic it was first sent with. Sent again with another topic, it"
        " is refused and nothing is stored: the answer names the page the note is on when that"
        " page holds it, and otherwise says that the note waits for its page or is on none, and"
        " a source's run skips it with the reason `filed under another topic`.",
        "To move a note, forget it on the page that cites it, then send it with its new"
        " topic, reworded or to another project: a text forgotten in a project is not stored"
        " there again.",
    )
    store = feed
    ops = store.get_project("ops")
    text = "- Quill retries 3 times before failing.\n"
    first = memory.note("ops", text, topic="Retry policy", source_ref="mail:t-1")
    held = store.list_notes(ops.id)
    with pytest.raises(FiledOnce) as refused:
        memory.note("ops", text, topic="Backoff", source_ref="mail:t-1")
    assert f"'{first['page']}'" in str(refused.value) and store.list_notes(ops.id) == held
    waits = "- Lumen keeps the relay.\n"
    store.add_note(ops.id, waits, topic="Relay", source_ref="mail:t-2")  # stored, not filed
    with pytest.raises(FiledOnce) as refused:
        memory.note("ops", waits, topic="Backoff", source_ref="mail:t-2")
    assert refused.value.page is None and WAITS in str(refused.value)
    held = store.list_notes(ops.id)
    draft = NoteDraft("ops", "Backoff", text, source_ref="mail:t-1", label="mail")
    Feed.answers = [Pulled(drafts=[draft], cursor="c1")]
    report = asyncio.run(run_source(store, "ops-feed", http=httpx.AsyncClient(), env={}))
    assert FILED_ELSEWHERE == "filed under another topic"
    assert report.reasons == {FILED_ELSEWHERE: 1} and store.list_notes(ops.id) == held

    store.forget_note(first["note_id"])  # forgotten on the page that cites it
    with pytest.raises(NoteForgotten):
        memory.note("ops", text, topic="Backoff", source_ref="mail:t-1")
    reworded = memory.note("ops", "- Quill tries 3 times, then fails.\n", topic="Backoff")
    assert reworded["page"] == "backoff"
    store.create_project("archive")
    elsewhere = memory.note("archive", text, topic="Backoff", source_ref="mail:t-1")
    assert elsewhere["status"] == "saved" and elsewhere["page"] == "backoff"


# run.md: notes waiting for their page


def test_a_waiting_note_is_kept_cited_by_no_page_and_not_found(memory, store):
    """run.md: "A note is stored first, then filed on its page. Until its outcome is recorded
    it is waiting: it is kept, but no page cites it yet and a search does not find it.\""""
    says(
        "run",
        "A note is stored first, then filed on its page. Until its outcome is recorded it is"
        " waiting: it is kept, but no page cites it yet and a search does not find it.",
    )
    ops = store.get_project("ops")
    note, _ = store.add_note(ops.id, "- Zyqvorth keeps the relay.\n", topic="Relay")
    assert [n.note_id for n in store.waiting_notes()] == [note.id]
    assert store.list_pages(ops.id) == [] and store.search("Zyqvorth") == []
    assert memory.file_waiting(note.id) == "saved"
    assert store.waiting_notes() == [] and store.search("Zyqvorth")


def test_a_note_that_keeps_losing_its_page_is_answered_202_waiting(store, monkeypatch):
    """run.md: "... and so does a note whose page changed under it more often than a note is
    tried at once; the `note` call then answers `202` with the status `waiting` (an agent's
    tool gets the same as a result, not an error), and the note should not be sent again.\""""
    says(
        "run",
        "A server stopped between the two leaves a note waiting, and so does a note whose page"
        " changed under it more often than a note is tried at once; the `note` call then"
        " answers `202` with the status `waiting` (an agent's tool gets the same as a result,"
        " not an error), and the note should not be sent again.",
    )
    store.create_project("ops")
    key = store.create_key("t")
    original = Store.save_page

    def moved(s, project_id, slug, *args, **kwargs):  # the page is saved again first, always
        latest = s.get_page(project_id, slug)
        if latest is not None:
            original(s, project_id, slug, latest.title, latest.body, list(latest.claims))
        return original(s, project_id, slug, *args, **kwargs)

    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        h = {"x-api-key": key}
        client.post("/api/projects/ops/notes", headers=h, json={"text": "- A.\n", "topic": "Relay"})
        monkeypatch.setattr(Store, "save_page", moved)
        r = client.post(
            "/api/projects/ops/notes", headers=h, json={"text": "- B.\n", "topic": "Relay"}
        )
        assert r.status_code == 202 and r.json()["status"] == WAITING
        assert "not send it again" in r.json()["message"]
        call = {"name": "note", "arguments": {"project": "ops", "text": "- C.\n", "topic": "Relay"}}
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": call}
        headers = {**h, "accept": "application/json, text/event-stream"}
        result = client.post("/mcp", headers=headers, json=body).json()["result"]
        assert result["isError"] is False
        assert json.loads(result["content"][0]["text"])["status"] == WAITING


def test_serve_files_the_waiting_notes_by_itself_every_three_minutes(db, store, monkeypatch):
    """run.md: "`serve` files the waiting notes by itself: once it answers requests, then
    every three minutes, a few of each workspace's at a time, each under its workspace's
    plan. A note that keeps failing is tried five times at most and then left waiting; the
    log names it by its id, never by its text.\""""
    says(
        "run",
        "`serve` files the waiting notes by itself: once it answers requests, then every three"
        " minutes, a few of each workspace's at a time, each under its workspace's plan.",
        "A note that keeps failing is tried five times at most and then left waiting; the log"
        " names it by its id, never by its text.",
    )
    assert filing.FILE_EVERY == 3 * 60 and filing.TRIES_MAX == 5 and 1 < filing.PER_TURN <= 5
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    assert cli.main(["serve", "--db", str(db)]) == 0
    assert apps[0].state.filer.every == filing.FILE_EVERY
    ops = store.create_project("ops")
    store.add_note(ops.id, "- Quill keeps the relay.\n", topic="Relay")
    filer = create_app(store, public_url=BASE, file_every=3600).state.filer

    async def first_wait(seconds):  # the loop ends where it would wait for its next look
        raise asyncio.CancelledError

    monkeypatch.setattr(filing.asyncio, "sleep", first_wait)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(filer.loop())  # the start's pass
    assert store.waiting_notes() == [] and store.get_page(ops.id, "relay") is not None


def test_the_waiting_command_lists_notes_without_their_text(db, capsys):
    """run.md: "Each line names a note's id, its workspace, its project, when it was stored and
    how many tries it had. `--workspace NAME` lists one workspace's notes, and `--json`
    prints them as JSON. It never shows a note's text or title. "No note is waiting for its
    page." means there are none.\""""
    says(
        "run",
        "docker exec cairn python -m cairn.server waiting --db /data/cairn.db",
        "Each line names a note's id, its workspace, its project, when it was stored and how many"
        " tries it had. `--workspace NAME` lists one workspace's notes, and `--json` prints"
        " them as JSON. It never shows a note's text or title. \"No note is waiting for its"
        ' page." means there are none.',
    )
    Store(db).close()
    assert server.main(["waiting", "--db", str(db)]) == 0
    assert capsys.readouterr().out.strip() == "No note is waiting for its page."
    store = Store(db, clock=lambda: Y2099)
    ops = store.create_project("ops")
    one, _ = store.add_note(ops.id, "- Zyqvorth keeps it.\n", title="Secret plans")
    store.tried_waiting(one.id)
    other = store.for_account(store.create_account("Dee").id)
    two, _ = other.add_note(other.create_project("notes").id, "- Quill.\n", topic="Relay")
    store.close()
    assert server.main(["waiting", "--db", str(db)]) == 0
    first, second = capsys.readouterr().out.splitlines()
    assert first.startswith(one.id) and "'ops'" in first and "2099-03-02" in first
    assert "tries 1" in first and second.startswith(two.id) and "'Dee'" in second
    assert not any(word in first + second for word in ("Zyqvorth", "Secret", "Quill", "Relay"))
    assert server.main(["waiting", "--db", str(db), "--workspace", "Dee", "--json"]) == 0
    assert [n["note_id"] for n in json.loads(capsys.readouterr().out)] == [two.id]


# run.md: an upgrade keeps the address of every page that remains


def test_an_upgrade_keeps_every_pages_address_and_a_merged_page_stays_merged(db):
    """run.md: "An upgrade keeps the address of every page that remains, so links to it and
    to the claims that remain on it keep working. Since pages are filed by topic, a page that
    held notes of two topics before the upgrade keeps them both and takes the topic of its
    latest title: later notes of that topic join it, and later notes of the other topic build
    a page of their own.\""""
    says(
        "run",
        "An upgrade keeps the address of every page that remains, so links to it and to the"
        " claims that remain on it keep working. Since pages are filed by topic, a page that"
        " held notes of two topics before the upgrade keeps them both and takes the topic of its"
        " latest title: later notes of that topic join it, and later notes of the other topic"
        " build a page of their own.",
    )
    v23_database(db)  # page `c` holds a C++ note and a C# note; its latest title is "C#"
    store = Store(db, clock=lambda: Y2099)  # the upgrade
    try:
        con = sqlite3.connect(db)
        try:
            (account,) = con.execute(
                "SELECT account_id FROM projects WHERE id = 'br_aaaaaaaaaaaa'"
            ).fetchone()
        finally:
            con.close()
        view = store.for_account(account)
        project = view.get_project("br_aaaaaaaaaaaa")
        before = {page.slug: page.claims for page in view.list_pages(project.id)}
        assert set(before) == {"c", "relay-notes"}
        memory = Memory(view, provenance_gate)
        assert memory.note(project.name, "- C# keeps records.\n", topic="C#")["page"] == "c"
        new = memory.note(project.name, "- C++ keeps concepts.\n", topic="C++")["page"]
        assert new == f"c-{topic_tag(topic_key('C++'))}"
        after = view.get_page(project.id, "c")
        assert set(before["c"]) <= set(after.claims)  # the claims cited from it, kept
        assert view.get_page(project.id, "relay-notes").claims == before["relay-notes"]
    finally:
        store.close()


def test_an_upgrade_makes_the_notes_no_page_cites_wait_and_serve_files_them(
    tmp_path, monkeypatch, capsys
):
    """run.md: "A note that no page cites when you upgrade, because it was stored and never
    filed or because the gate rejected it, waits for its page, and `serve` files it: it gets
    its page, or its outcome is recorded again and it waits no more.\""""
    from test_filing_upgrade_unit import LEFT_NOTES, LEFT_PAGES, files_what_was_left, history

    says(
        "run",
        "A note that no page cites when you upgrade, because it was stored and never filed or"
        " because the gate rejected it, waits for its page, and `serve` files it: it gets its"
        " page, or its outcome is recorded again and it waits no more.",
    )
    db = tmp_path / "old.db"
    history(db, LEFT_NOTES, LEFT_PAGES)
    store = Store(db, clock=lambda: Y2099)  # the upgrade
    try:
        files_what_was_left(store, db, "br_0000000000b1", monkeypatch, capsys)
    finally:
        store.close()


# privacy.md: what is kept of a note's topic, and of a waiting note


def test_the_topic_kept_is_the_compared_form_and_a_waiting_note_keeps_its_tries(db, store):
    """privacy.md: "Each note and each page also keeps the topic it is filed by, written as
    cairn compares topics: its topic, else its title, in lower case, without accents on
    Latin or Greek letters and without punctuation at the edges of words. Until a note is
    on its page, cairn also keeps that it is waiting and how many times filing it was
    tried.\""""
    name = guide_source("privacy").split("Each note and each page", 1)[1].split(" compares", 1)
    product = name[0].rsplit(" ", 1)[1]
    says(
        "privacy",
        f"Each note and each page also keeps the topic it is filed by, written as {product}"
        " compares topics: its topic, else its title, in lower case, without accents on Latin"
        " or Greek letters and without punctuation at the edges of words. Until a note is on"
        f" its page, {product} also keeps that it is waiting and how many times filing it was"
        " tried.",
    )
    ops = store.create_project("ops")
    titled, _ = store.add_note(ops.id, "- One.\n", title="Café, Plan!")
    topical, _ = store.add_note(ops.id, "- Two.\n", title="Ignored", topic="(Ωμέγα) Notes")
    assert (titled.topic_key, topical.topic_key) == ("cafe plan", "ωμεγα notes")
    store.tried_waiting(titled.id)
    assert [(n.note_id, n.tries) for n in store.waiting_notes()] == [
        (titled.id, 1),
        (topical.id, 0),
    ]
    Memory(store, provenance_gate).file_waiting(titled.id)
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT topic_key FROM page_keys").fetchall() == [("cafe plan",)]
        assert con.execute("SELECT note_id FROM waiting").fetchall() == [(topical.id,)]
    finally:
        con.close()


# sources.md: running it twice is safe, for a note stored before N1 and for one stored since


def test_running_it_twice_is_safe_for_old_and_new_notes(tmp_path):
    """sources.md: "**Running it twice is safe.** The same text with the same `source_ref` is
    stored once. A second run that sends it with the same topic answers `unchanged` ... A
    note stored before cairn filed notes by topic stays the same note whatever topic or
    title it is sent with: it is not stored again, and once it is on its page it answers
    `unchanged`. A note stored since, sent with another topic, is refused as above.\""""
    from test_filing_upgrade_unit import OLD_NOTES, OLD_PAGES, PROJECT, history

    product = guide_source("sources").split("A note stored before ", 1)[1].split(" ", 1)[0]
    says(
        "sources",
        "**Running it twice is safe.** The same text with the same `source_ref` is stored once."
        " A second run that sends it with the same topic answers `unchanged`: no note is stored"
        " again and no page version is written, and the outcome is recorded in the workspace's"
        " audit log.",
        f"A note stored before {product} filed notes by topic stays the same note whatever"
        " topic or title it is sent with: it is not stored again, and once it is on its page it"
        " answers `unchanged`. A note stored since, sent with another topic, is refused as"
        " above.",
    )
    db = tmp_path / "old.db"
    history(db, OLD_NOTES, OLD_PAGES)
    store = Store(db, clock=lambda: Y2099)  # the upgrade
    try:
        memory = Memory(store, provenance_gate)
        for note, (title, _, text, ref) in OLD_NOTES.items():
            for given in ({"title": f"{title} (again)"}, {"topic": "Anything else"}):
                answer = memory.note("ops", text, source_ref=ref, **given)
                assert (answer["note_id"], answer["status"]) == (note, "unchanged")
        new = memory.note("ops", "- A note of today.\n", topic="Today", source_ref="doc:t")
        again = memory.note("ops", "- A note of today.\n", topic="Today", source_ref="doc:t")
        assert (again["note_id"], again["status"]) == (new["note_id"], "unchanged")
        with pytest.raises(FiledOnce):
            memory.note("ops", "- A note of today.\n", topic="Tomorrow", source_ref="doc:t")
        assert len(store.list_notes(PROJECT)) == len(OLD_NOTES) + 1
    finally:
        store.close()
