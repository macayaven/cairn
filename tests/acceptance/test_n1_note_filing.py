"""N1 acceptance tests (frozen): a note is filed where it belongs, once, and is never left behind.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/compiler.py (where a note's page is chosen),
src/cairn/memory.py, src/cairn/store/__init__.py and src/cairn/server.py.

Why: a note compiles into the page of its topic (its `topic`, else its title), and that page
was found by a slug alone, which throws away most of what tells topics apart: "C++" and "C#"
were one page, every title in a script other than Latin was the page `untitled`, a note sent
again with a corrected topic was answered "unchanged" and stayed where it was, and a note
stored and not yet compiled when the server stopped was never compiled. One question sits
under all of it, when two titles are the same topic, and this file answers it once.

One rule for "the same topic"
- The topic key. Two titles are the same topic when they read the same ignoring letter case,
  an accent on a Latin or a Greek letter, the difference between spaces, hyphens, underscores
  and slashes (and how many of them), and sentence punctuation at the edges of words (. , ; :
  ! ? " ' ( ) [ ] { }, the ellipsis, the en and the em dash). Every letter and digit of every
  script counts, and so does a symbol written into a word (C++, C#, R&D, 50%), and so does
  punctuation inside a word ("2.10", "retry.policy"). A combining mark on a letter of any
  other script is part of the letter and counts, written composed or decomposed: a Cyrillic
  short i is not i, a Japanese voiced kana is not the unvoiced one, a Devanagari or Thai vowel
  sign or tone mark is part of the word. A title with no letter, digit or symbol left ("!!!")
  has the empty key; such titles are one topic. Pinned pairs are in SAME and DIFFERENT below;
  what the rule leaves open (".NET" and "NET", "e-mail" and "email", full- and half-width
  forms, Arabic with and without vowel marks, Cyrillic yo and ie, "ß" and "ss", a symbol
  standing alone between spaces, CJK punctuation at a word's edge) is not pinned here.
- A page is one topic, for good. A page's key is fixed when the page is made, from the
  title of the note that made it. Later notes with that key go to that page whatever they
  call it (and may change its displayed title, as before); none changes its key. A note
  whose key no page of the project has makes a new page.
- The page's address. A new page's slug is `cairn.compiler.slugify(title)` (unchanged, see
  test_w1_store.py) when no page of the project has that slug and slugify kept something of
  the title. Otherwise (the slug is another topic's, or slugify kept nothing of a title
  that has a key, so "untitled") it is that slug, then "-", then a short tag of lower-case
  hex made from the key alone (the same key gives the same tag on every install), cut so
  the whole stays within the 64 characters a slug has. When another topic holds that
  slug too, the note's topic still gets a page of its own (its address is not pinned
  here). A slug never changes. The empty key's page is plain "untitled" when that slug is
  free. A page made before this change keeps its slug and takes the key of its latest
  title at the upgrade.
- Two writers. Two notes of two topics that would take one free slug at the same moment end
  as two pages, each with its own claims; neither merges into the other on a retry. Two
  notes of one topic end on one page with both.
- Everything that names a note's page agrees: the `page` that `note` answers over MCP and
  REST, the composer's redirect, the room-on-page check made before a note is stored, a
  source's run, `recall`, `page` and `evidence`.

One note, one page
- A note sent again (same project, text and source_ref) with the same topic key is the same
  note: not stored twice, answered as before (`created` false). A different label or
  via, or another title while its topic stays, is the same note and changes nothing.
- Sent again with another topic key (another topic, or no topic and another title), it is a
  conflict and nothing is written: REST 409 with {"error": sentence}, MCP a tool error with
  the sentence, the composer its form again (a 4xx) with it. The sentence names the page the
  note is on (by its slug), says that a note is filed once and how to move it (forget it
  there and send it again), and holds nothing of the note's text. A source's run counts it
  as skipped under a reason of its own (one no other skip uses) and goes on: the other
  drafts are written and the cursor moves.

Never left behind
- From the moment a note is stored until its outcome is recorded (saved, unchanged,
  rejected, or refused for a full page), the database says it is waiting.
  `python -m cairn.server waiting --db PATH` lists every waiting note of every workspace,
  one per line or object, each naming the note's id (exit 0); a note that waits no more
  is not listed. Waiting meters nothing: the month's count of notes counts it once, when it
  was stored, and the workspace's usage (GET /api/usage) has the fields it had.
- `serve` files the waiting notes of every workspace by itself, in the background of the
  app it hands to uvicorn: never before uvicorn runs that app and never in the way of its
  answers (/healthz and an authenticated read answer while a note is being filed), each
  under its workspace's own plan (a plan's versions_per_page holds) and serve's gate, each
  exactly once on its page however often the server stops in between. Once `serve`
  returns, the work it had in hand is finished or left waiting. A note that cannot be
  compiled (its gate rejects it: recorded as note.rejected; its page is full) waits no more.
  One whose compile fails for a reason nobody foresaw stays waiting, is logged at WARNING
  or above with nothing of its text (no message, no traceback), and is tried again at the
  next start, at most a stated number of times (here: it stops within 20 starts).
- A note whose page moves under it more often than the compiler retries is compiled once
  more, whole, before `note` answers; if it still loses, the answer is not one of success
  (REST: not 201; MCP: an error, or a status other than saved and unchanged), and the note
  waits for `serve`.
- Forgetting a waiting note, deleting its project or its workspace leaves no waiting record.
  An export carries each page's key and an import restores it, so an imported workspace
  files notes as the exported one did (also for a page whose title no longer reads as its
  key).
- With nothing waiting and no collision, a note's answer over REST and MCP is byte for byte
  what it was.

The tests reach `serve` as the frozen files do: `uvicorn.run` is replaced by a runner that
enters the app's lifespan with Starlette's TestClient and sends it requests. They hold a
compile by wrapping `Store.save_page` (the one call that writes a page), and the two-writer
tests run two real processes held at one point by a barrier.
"""

import asyncio
import contextlib
import io
import json
import logging
import multiprocessing
import os
import re
import signal
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from starlette.testclient import TestClient

import cairn
from cairn import server
from cairn.compiler import SAVE_TRIES, claim_id, compile_note, render_body, slugify
from cairn.gate import lint_gate
from cairn.memory import FORGOTTEN, PAGE_FULL, EmptyText, LongHeadings, Malformed, PastBound
from cairn.server import create_app
from cairn.sources import HELD_CREDENTIALS, KINDS, NoteDraft, Pulled, SourceKind, run_source
from cairn.spans import chunk
from cairn.store import DEFAULT_ACCOUNT_ID, PAGE_CLAIMS_MAX, Claim, Store

BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
WAIT = 30.0  # seconds: the most a thread, a process or the server is waited for
QUIET = 3.0  # seconds a start is watched for a try that must not come
Y2099 = datetime(2099, 3, 2, 9, 0, tzinfo=UTC)
SLUG_MAX = 64
ADDRESS = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
TAGGED = re.compile(r"(?P<base>[a-z0-9]+(?:-[a-z0-9]+)*)-(?P<tag>[0-9a-f]+)")
LONG = "Incident review for the payments ledger reconciliation service in region"
# GET /api/usage as it is before this change: its fields, and the limits it reports.
USAGE_FIELDS = ["label", "limits", "passages", "period", "plan", "resets_at"]
LIMIT_FIELDS = ["model_budget_micros", "notes_per_month", "projects", "storage_bytes"]
# Every reason a source's run skips a draft under before this change.
KNOWN_SKIPS = {
    FORGOTTEN,
    PAGE_FULL,
    HELD_CREDENTIALS,
    "project not allowed",
    "no such project",
    *(kind.reason for kind in (Malformed, EmptyText, PastBound, LongHeadings)),
}

# (a title, another title of the same topic, a title of another topic)
SAME = [
    ("Retry policy", "retry-policy.", "Retry policies"),
    ("Retry policy", "RETRY  POLICY", "Retry: policy (v2)"),
    ("Café menu", "Cafe menu", "Café menus"),
    ("Cafe\u0301 menu", "CAFÉ MENU", "Cafe venue"),  # a decomposed é
    ("retry_policy", "retry/policy", "retry.policy"),
    ('"Retry" (policy)!', "retry policy", "Retry policy v2"),
    ("[Retry] {policy}\u2026", "\u2013 Retry policy \u2014", "Retry policy, two"),
    ("Résumé; draft", "resume draft", "résumé drafts"),
    ("Настройка сервера", "НАСТРОЙКА-СЕРВЕРА", "Настройка клиента"),  # noqa: RUF001
    ("Настройка", "Настрои\u0306ка", "Настроика"),  # noqa: RUF001  # a decomposed й
    ("ガイド", "カ\u3099イド", "カイド"),  # a decomposed ガ
    ("がっこう", "か\u3099っこう", "かっこう"),  # a decomposed が
    ("\u095bरा", "ज\u093cरा", "जरा"),  # za composed, then with its nukta apart
    ("Πολιτική επανάληψης", "ΠΟΛΙΤΙΚΗ ΕΠΑΝΑΛΗΨΗΣ", "Πολιτική ασφαλείας"),
    ("מדיניות ניסיון", "מדיניות-ניסיון", "מדיניות אבטחה"),
    ("سياسة الإعادة", "سياسة_الإعادة", "سياسة الأمان"),
    ("재시도 정책", "재시도-정책", "보안 정책"),
    ("日本語", "日本語?", "中文"),
    ("Release 2.10", "release 2.10.", "Release 2-10"),
    ("R&D budget", "r&d BUDGET", "R D budget"),
    ("50% rollout", "50%  Rollout", "50 rollout"),
    ("C++", "c++", "C#"),
    (f"{LONG} east", f"{LONG.upper()} EAST", f"{LONG} west"),
    ("!!!", "???", "日本語"),
]
# Titles that are each a topic of their own.
DIFFERENT = [
    ("C++", "C#", "C"),
    ("日本語", "中文"),
    (f"{LONG} east", f"{LONG} west"),  # alike in their first 64 characters
    ("Настройка сервера", "Настройка клиента"),
    ("Release 2.10", "Release 2-10", "Release 210"),
    ("R&D", "R D", "RD"),
    ("50% rollout", "50 rollout"),
    ("retry.policy", "retry policy"),
    ("Cafe", "\u0421afe"),  # the second begins with a Cyrillic letter
    ("Alpha", "\u0391lpha"),  # the second begins with a Greek letter
    # Only a mark tells these apart, outside Latin and Greek: the mark counts.
    ("Настройка", "Настроика"),
    ("Настрои\u0306ка", "Настроика"),  # noqa: RUF001  # a decomposed й
    ("ガイド", "カイド"),
    ("カ\u3099イド", "カイド"),  # a decomposed ガ
    ("がっこう", "かっこう"),
    ("か\u3099っこう", "かっこう"),  # a decomposed が
    ("की", "कि", "क"),  # Devanagari vowel signs
    ("\u095bरा", "जरा"),  # a Devanagari nukta
    ("ज\u093cरा", "जरा"),  # the nukta apart
    ("ข่าว", "ข้าว", "ขาว"),  # Thai tone marks
    ("น้ำ", "นำ"),  # a Thai tone mark over sara am, which NFKD splits
    ("!!!", "日本語", "中文", "Настройка"),
]


class Clock:
    def __init__(self, now: datetime = Y2099) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def fact(n: int | str) -> str:
    """A note of one claim; mark(n) is in that claim's text and in no other note's."""
    return f"- Quill keeps fact {n} about the relay.\n"


def mark(n: int | str) -> str:
    return f"fact {n} about"


def dump(db: Path) -> list[str]:
    con = sqlite3.connect(db)
    try:
        return list(con.iterdump())
    finally:
        con.close()


def unaudited(lines: list[str]) -> list[str]:
    return [line for line in lines if not line.startswith('INSERT INTO "audit_events"')]


def within(check: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    pause = threading.Event()
    while not check():
        if time.monotonic() >= deadline:
            return False
        pause.wait(0.02)
    return True


def eventually(check: Callable[[], bool], what: str) -> None:
    assert within(check, WAIT), f"not within {WAIT:g} seconds: {what}"


@contextlib.contextmanager
def app_on(store: Store, db: Path, **options) -> Iterator[SimpleNamespace]:
    key = store.create_key("n1")
    with TestClient(create_app(store, public_url=BASE, **options), base_url=BASE) as client:
        yield SimpleNamespace(store=store, db=db, key=key, client=client, h={"x-api-key": key})


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "cairn.db"
    store = Store(db, clock=Clock())
    with app_on(store, db) as made:
        yield made
    store.close()


def new_project(env, name: str) -> None:
    r = env.client.post("/api/projects", headers=env.h, json={"name": name})
    assert r.status_code == 201, r.text


def send(env, project: str, text: str, **fields) -> httpx.Response:
    return env.client.post(
        f"/api/projects/{project}/notes", headers=env.h, json={"text": text, **fields}
    )


def noted(env, project: str, text: str, **fields) -> dict:
    r = send(env, project, text, **fields)
    assert r.status_code == 201, r.text
    return r.json()


def page(env, project: str, slug: str, version: int | None = None) -> dict:
    params = {} if version is None else {"version": version}
    r = env.client.get(f"/api/projects/{project}/pages/{slug}", headers=env.h, params=params)
    assert r.status_code == 200, (slug, r.text)
    return r.json()


def slugs(env, project: str) -> set[str]:
    r = env.client.get(f"/api/projects/{project}/pages", headers=env.h)
    assert r.status_code == 200, r.text
    return {summary["slug"] for summary in r.json()["pages"]}


def texts(env, project: str, slug: str) -> list[str]:
    return [claim["text"] for claim in page(env, project, slug)["claims"]]


def held(env, project: str) -> dict[str, list[str]]:
    """Every page of the project and the texts of its claims."""
    return {slug: texts(env, project, slug) for slug in slugs(env, project)}


def times_held(env, project: str, marker: str) -> int:
    return sum(text.count(marker) for claims in held(env, project).values() for text in claims)


def rpc(env, method: str, params: dict | None = None) -> httpx.Response:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return env.client.post("/mcp", headers={**MCP_HEADERS, **env.h}, json=body)


def tool(env, name: str, /, **arguments) -> dict:
    r = rpc(env, "tools/call", {"name": name, "arguments": arguments})
    assert r.status_code == 200, r.text
    return r.json()["result"]


def call(env, name: str, /, **arguments) -> dict:
    result = tool(env, name, **arguments)
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def login(env) -> None:
    r = env.client.post("/login", data={"key": env.key}, follow_redirects=False)
    assert r.status_code == 303, r.text


def compose(env, project: str, text: str, **fields) -> httpx.Response:
    data = {"text": text, **fields}
    return env.client.post(
        f"/b/{project}/notes", data=data, headers=SAME_ORIGIN, follow_redirects=False
    )


def filed(store: Store, project_id: str, text: str, topic: str) -> str:
    """A note stored and compiled through the store's own calls: the slug of its page."""
    note, _ = store.add_note(project_id, text, topic=topic)
    result = compile_note(store, note)
    assert result.status == "saved", result
    return result.slug


def listed(db: Path, capsys, heard: list[str] | None = None) -> str:
    """What `waiting` prints for the database (it exits 0). `heard` keeps everything
    printed, the server's own output included."""
    before = capsys.readouterr()
    assert server.main(["waiting", "--db", str(db)]) == 0
    after = capsys.readouterr()
    if heard is not None:
        heard.extend([before.out, before.err, after.out, after.err])
    return after.out


def serve(
    monkeypatch,
    db: Path,
    during: Callable[[TestClient], object],
    *options: str,
    started: threading.Event | None = None,
    stopped: threading.Event | None = None,
):
    """`serve` on the database, with uvicorn.run replaced by a runner that enters the app's
    lifespan with TestClient and hands the client to `during`; what `during` returns.
    `started` is set as uvicorn would start running the app, `stopped` once its lifespan
    has ended."""
    ran = []

    def run(app, **_):
        if started is not None:
            started.set()
        with TestClient(app, base_url=BASE) as client:
            ran.append(during(client))
        if stopped is not None:
            stopped.set()

    monkeypatch.setattr(uvicorn, "run", run)
    assert server.main(["serve", "--db", str(db), "--no-tidy", *options]) == 0
    (result,) = ran
    return result


def answered(client: TestClient, path: str, headers: dict | None = None) -> int | None:
    """The status a request gets, from another thread; None when it gets none in time."""
    got: dict[str, int] = {}

    def ask() -> None:
        got["status"] = client.get(path, headers=headers).status_code

    asker = threading.Thread(target=ask, daemon=True)
    asker.start()
    asker.join(WAIT / 3)
    return got.get("status")


def waiting_note(db: Path, project: str, text: str, topic: str, **options) -> str:
    """A note stored and not compiled, as when the server stops between the two: its id."""
    store = Store(db, clock=Clock())
    try:
        found = next((b for b in store.list_projects() if b.name == project), None)
        target = found or store.create_project(project)
        note, created = store.add_note(target.id, text, topic=topic, **options)
        assert created
        return note.id
    finally:
        store.close()


def says_filed_once(sentence: str, slug: str) -> None:
    assert slug in sentence, sentence
    assert "filed once" in sentence.casefold() and "forget" in sentence.casefold(), sentence
    for word in ("Quill", "retries", "failing"):  # the note's text
        assert word not in sentence, sentence


# 1. The topic key


def test_titles_that_read_the_same_share_a_page_and_another_topic_does_not(env):
    wrong = []
    for n, titles in enumerate(SAME):
        project = f"same-{n}"
        new_project(env, project)
        pages = [
            noted(env, project, fact(i), topic=title)["page"] for i, title in enumerate(titles)
        ]
        if not (pages[0] == pages[1] != pages[2] and slugs(env, project) == set(pages)):
            wrong.append((titles, pages))
    assert wrong == []


def test_titles_that_are_topics_of_their_own_each_get_a_page_of_their_own(env):
    wrong = []
    for n, titles in enumerate(DIFFERENT):
        project = f"apart-{n}"
        new_project(env, project)
        pages = [
            noted(env, project, fact(i), topic=title)["page"] for i, title in enumerate(titles)
        ]
        claims = held(env, project)
        alone = all(
            [text for text in claims[slug] if mark(i) in text] == claims[slug]
            and len(claims[slug]) == 1
            for i, slug in enumerate(pages)
        )
        if len(set(pages)) != len(titles) or set(claims) != set(pages) or not alone:
            wrong.append((titles, pages))
    assert wrong == []


def test_the_title_counts_as_the_topic_when_a_note_has_none(env):
    new_project(env, "titled")
    by_title = noted(env, "titled", f"# C++\n\n{fact(1)}")["page"]
    assert noted(env, "titled", fact(2), title="c++")["page"] == by_title
    assert noted(env, "titled", fact(3), topic="C++ ")["page"] == by_title
    other = noted(env, "titled", f"# C#\n\n{fact(4)}")["page"]
    assert other != by_title
    assert noted(env, "titled", fact(5), title="Generics", topic="c#")["page"] == other


# 2. The address


def test_a_new_page_takes_its_slug_and_a_colliding_topic_takes_it_with_a_tag(env):
    new_project(env, "addr")
    made = {}
    for n, title in enumerate(["Retry policy", "Café menu", "C++", "Release 2.10", "!!!"]):
        made[title] = noted(env, "addr", fact(n), topic=title)["page"]
        assert made[title] == slugify(title), title
    made["east"] = noted(env, "addr", fact("east"), topic=f"{LONG} east")["page"]
    assert made["east"] == slugify(f"{LONG} east")
    assert noted(env, "addr", fact("again"), topic="???")["page"] == "untitled"

    colliding = ["C#", "C", "c+", f"{LONG} west", "日本語", "中文", "Настройка"]
    for n, title in enumerate(colliding):
        slug = noted(env, "addr", fact(f"c{n}"), topic=title)["page"]
        tagged = TAGGED.fullmatch(slug)
        assert tagged and len(slug) <= SLUG_MAX, (title, slug)
        assert slugify(title).startswith(tagged["base"]), (title, slug)
        assert len(tagged["tag"]) <= 16, (title, slug)
        if slugify(title) != "untitled":
            assert slug != slugify(title), (title, slug)
        made[title] = slug
    for title in ("日本語", "中文", "Настройка"):
        assert TAGGED.fullmatch(made[title])["base"] == "untitled", made[title]
    assert len(set(made.values())) == len(made)  # every topic its own page

    login(env)
    for slug in made.values():
        assert ADDRESS.fullmatch(slug) and len(slug) <= SLUG_MAX, slug
        assert page(env, "addr", slug)["slug"] == slug
        r = env.client.get(f"/b/addr/p/{slug}")
        assert r.status_code == 200, (slug, r.status_code)


def test_a_topic_whose_tagged_slug_another_topic_holds_still_gets_a_page_of_its_own(env):
    new_project(env, "probe")
    noted(env, "probe", fact(1), topic="C++")
    tagged = noted(env, "probe", fact(2), topic="C#")["page"]
    new_project(env, "taken")
    assert noted(env, "taken", fact(3), topic=tagged)["page"] == tagged  # another topic
    assert noted(env, "taken", fact(4), topic="C++")["page"] == "c"
    answer = noted(env, "taken", fact(5), topic="C#")
    slug = answer["page"]
    assert answer["status"] == "saved" and slug not in ("c", tagged), slug
    assert ADDRESS.fullmatch(slug) and len(slug) <= SLUG_MAX, slug
    assert len(texts(env, "taken", slug)) == 1 and mark(5) in texts(env, "taken", slug)[0]
    assert noted(env, "taken", fact(6), topic="c#")["page"] == slug


def test_the_same_key_gives_the_same_tag_on_every_install(tmp_path):
    def two_pages(path: Path, occupant: str, colliding: str) -> str:
        store = Store(path, clock=Clock())
        try:
            project = store.create_project("tags")
            first = filed(store, project.id, fact(1), occupant)
            second = filed(store, project.id, fact(2), colliding)
            assert first != second
            return TAGGED.fullmatch(second)["tag"]
        finally:
            store.close()

    assert two_pages(tmp_path / "a.db", "C++", "C#") == two_pages(tmp_path / "b.db", "C", "c#")
    assert two_pages(tmp_path / "c.db", "!!!", "日本語") == two_pages(
        tmp_path / "d.db", "-", "日本語"
    )
    assert two_pages(tmp_path / "e.db", "C++", "C#") != two_pages(tmp_path / "f.db", "C++", "C")


# 3. A page made before the change


V2_TABLES = """
CREATE TABLE accounts (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (account_id, name)
);
CREATE TABLE api_keys (
    seq INTEGER PRIMARY KEY,
    digest TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX api_keys_by_label ON api_keys (account_id, label);
CREATE TABLE sessions (
    digest TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE notes (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    text TEXT NOT NULL,
    text_sha256 TEXT NOT NULL,
    title TEXT NOT NULL,
    topic TEXT,
    label TEXT,
    source_ref TEXT,
    via TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX notes_by_text ON notes (project_id, text_sha256);
CREATE TABLE spans (
    id TEXT PRIMARY KEY,
    note_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    kind TEXT NOT NULL,
    heading_path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    text TEXT NOT NULL,
    UNIQUE (note_id, ordinal)
);
CREATE TABLE page_versions (
    project_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    claims TEXT NOT NULL,
    metrics TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, slug, version)
);
"""
OLD_VERSION = 22  # the schema before this change
LEGACY = "br_00000000a001"
LEGACY_NOTES = {  # id: (title, text)
    "cap_00000000a001": ("C++", "- C++ templates are expanded when the code is compiled.\n"),
    "cap_00000000a002": ("C#", "- C# generics are reified at run time.\n"),
    "cap_00000000a003": ("日本語", "- The 日本語 handbook is kept in the archive.\n"),
    "cap_00000000a004": ("中文", "- The 中文 handbook is kept in the ledger.\n"),
    "cap_00000000a005": ("Retry policy", "- Retries back off twice as long each time.\n"),
}
LEGACY_PAGES = [  # slug, version, title, the notes its claims come from: today's merges
    ("c", 1, "C++", ["cap_00000000a001"]),
    ("c", 2, "C#", ["cap_00000000a001", "cap_00000000a002"]),
    ("untitled", 1, "日本語", ["cap_00000000a003"]),
    ("untitled", 2, "中文", ["cap_00000000a003", "cap_00000000a004"]),
    ("retry-policy", 1, "Retry policy", ["cap_00000000a005"]),
]


def stamp(n: int) -> str:
    return f"2026-10-01T08:{n:02d}:00.000000+00:00"


def legacy_database(db: Path) -> None:
    """A database as cairn wrote it before this change: a project whose pages were found by
    slug alone, so "C++" and "C#" share the page `c`, and "日本語" and "中文" `untitled`."""
    from cairn.store.schema import _MIGRATIONS  # history: the steps from version 2 on

    spans = {note: chunk(note, text) for note, (_, text) in LEGACY_NOTES.items()}
    con = sqlite3.connect(db, isolation_level=None)
    try:
        con.executescript(V2_TABLES)
        con.execute("BEGIN")
        con.execute(
            "INSERT INTO accounts (id, name, created_at) VALUES (?, 'default', ?)",
            (DEFAULT_ACCOUNT_ID, stamp(0)),
        )
        for version in range(3, OLD_VERSION + 1):
            _MIGRATIONS[version](con)
        con.execute(
            "INSERT INTO projects (id, account_id, name, description, created_at, kind, maker_id,"
            " everyone) VALUES (?, ?, 'legacy', '', ?, 'general', NULL, 'write')",
            (LEGACY, DEFAULT_ACCOUNT_ID, stamp(0)),
        )
        for n, (note, (title, text)) in enumerate(LEGACY_NOTES.items(), start=1):
            con.execute(
                "INSERT INTO notes (id, project_id, text, text_sha256, title, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (note, LEGACY, text, __import__("hashlib").sha256(text.encode()).hexdigest(),
                 title, stamp(n)),
            )  # fmt: skip
            con.executemany(
                "INSERT INTO spans (id, note_id, ordinal, kind, heading_path, start_line,"
                " end_line, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (s.id, note, i, s.kind, json.dumps(list(s.heading_path)), s.start_line,
                     s.end_line, s.text)
                    for i, s in enumerate(spans[note])
                ],
            )  # fmt: skip
        for n, (slug, version, title, notes) in enumerate(LEGACY_PAGES, start=10):
            cited = [s for note in notes for s in spans[note]]
            claims = [Claim(id=claim_id(s.id), text=s.text, span_ids=(s.id,)) for s in cited]
            body = render_body(title, claims, {s.id: s for s in cited})
            con.execute(
                "INSERT INTO page_versions (project_id, slug, version, title, body, claims,"
                " metrics, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (LEGACY, slug, version, title, body,
                 json.dumps([c.model_dump(mode="json") for c in claims]),
                 json.dumps({"claims": len(claims), "spans_cited": len(claims)}), stamp(n)),
            )  # fmt: skip
        con.execute(f"PRAGMA user_version = {OLD_VERSION}")
        con.execute("COMMIT")
    finally:
        con.close()


def test_a_page_made_before_the_change_keeps_its_slug_and_files_by_its_latest_title(tmp_path):
    db = tmp_path / "cairn.db"
    legacy_database(db)
    store = Store(db, clock=Clock())
    try:
        con = sqlite3.connect(db)
        try:
            assert con.execute("PRAGMA user_version").fetchone()[0] > OLD_VERSION
        finally:
            con.close()
        assert [c.id for c in store.list_notes(LEGACY)] == list(LEGACY_NOTES)
        with app_on(store, db) as env:
            assert slugs(env, "legacy") == {"c", "untitled", "retry-policy"}
            for slug, version, title, notes in LEGACY_PAGES:  # every version as it was
                old = page(env, "legacy", slug, version)
                assert old["title"] == title and len(old["claims"]) == len(notes)
            assert page(env, "legacy", "c")["versions"] == [1, 2]

            # each page takes the key of its latest title: C#, 中文, Retry policy
            assert noted(env, "legacy", fact(1), topic="c#")["page"] == "c"
            assert noted(env, "legacy", fact(2), topic="中文")["page"] == "untitled"
            assert noted(env, "legacy", fact(3), topic="retry-policy.")["page"] == "retry-policy"
            cpp = noted(env, "legacy", fact(4), topic="C++")["page"]
            japanese = noted(env, "legacy", fact(5), topic="日本語")["page"]
            assert TAGGED.fullmatch(cpp)["base"] == "c"
            assert TAGGED.fullmatch(japanese)["base"] == "untitled"
            assert page(env, "legacy", "c")["versions"] == [1, 2, 3]

            # Forgetting the C# note retitles `c` "C++" (the oldest note left): it stays C#'s.
            r = env.client.delete("/api/projects/legacy/notes/cap_00000000a002", headers=env.h)
            assert r.status_code == 200, r.text
            assert page(env, "legacy", "c")["title"] == "C++"
            assert noted(env, "legacy", fact(6), topic="c++")["page"] == cpp
            assert slugs(env, "legacy") == {"c", "untitled", "retry-policy", cpp, japanese}
            archive = io.BytesIO()
            store.export(archive)  # `c` is titled "C++" and is C#'s
            assert noted(env, "legacy", fact(7), topic="C#")["page"] == "c"

        moved = tmp_path / "moved.db"
        target = Store(moved, clock=Clock())
        try:
            target.import_export(io.BytesIO(archive.getvalue()))
            with app_on(target, moved) as there:
                before = slugs(there, "legacy")
                assert page(there, "legacy", "c")["title"] == "C++"
                for n, (topic, slug) in enumerate(
                    [("C#", "c"), ("C++", cpp), ("中文", "untitled"), ("日本語", japanese)]
                ):
                    assert noted(there, "legacy", fact(f"m{n}"), topic=topic)["page"] == slug
                assert slugs(there, "legacy") == before
        finally:
            target.close()
    finally:
        store.close()


# 4. Two writers


_BARRIER = None


def _hold_at(barrier) -> None:
    global _BARRIER
    _BARRIER = barrier


def _compile_held(args: tuple[str, str]) -> tuple[str, str, str]:
    """In a child process: compile one stored note, its first gate call held at the barrier
    until every child has read the page and drafted; (cairn's path, status, slug)."""
    db, note_id = args
    first = [True]

    def gate(candidate, spans):
        if first[0]:
            first[0] = False
            _BARRIER.wait(WAIT)
        return lint_gate(candidate, spans)

    store = Store(db, clock=Clock())
    try:
        result = compile_note(store, store.get_note(note_id), gate)
        return cairn.__file__, result.status, result.slug
    finally:
        store.close()


def race(db: Path, topics: list[str]) -> tuple[str, list[tuple[str, str, str]]]:
    store = Store(db, clock=Clock())
    try:
        project = store.create_project("race")
        ids = [store.add_note(project.id, fact(n), topic=t)[0].id for n, t in enumerate(topics)]
    finally:
        store.close()
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(len(ids))
    with context.Pool(len(ids), initializer=_hold_at, initargs=(barrier,)) as pool:
        results = pool.map(_compile_held, [(str(db), note_id) for note_id in ids])
    assert {path for path, _, _ in results} == {cairn.__file__}  # the code under test
    return project.id, results


def test_two_topics_that_would_take_one_free_slug_at_once_end_as_two_pages(tmp_path):
    db = tmp_path / "cairn.db"
    project_id, results = race(db, ["C++", "C#"])
    assert [status for _, status, _ in results] == ["saved", "saved"]
    store = Store(db, clock=Clock())
    try:
        pages = {slug: store.get_page(project_id, slug) for slug in store_slugs(store, project_id)}
        assert len(pages) == 2 and "c" in pages
        by_title = {p.title: [c.text for c in p.claims] for p in pages.values()}
        assert set(by_title) == {"C++", "C#"}
        assert [mark(0) in text for text in by_title["C++"]] == [True]
        assert [mark(1) in text for text in by_title["C#"]] == [True]
        assert {slug for _, _, slug in results} == set(pages)
    finally:
        store.close()


def test_two_notes_of_one_topic_at_once_end_on_one_page_with_both(tmp_path):
    db = tmp_path / "cairn.db"
    project_id, results = race(db, ["Retry policy", "retry-policy."])
    assert [status for _, status, _ in results] == ["saved", "saved"]
    store = Store(db, clock=Clock())
    try:
        assert store_slugs(store, project_id) == {"retry-policy"}
        claims = [c.text for c in store.get_page(project_id, "retry-policy").claims]
        assert sorted(mark(n) in text for n in (0, 1) for text in claims).count(True) == 2
        assert len(claims) == 2
    finally:
        store.close()


def store_slugs(store: Store, project_id: str) -> set[str]:
    return {p.slug for p in store.list_pages(project_id)}


# 5. Everything that names a note's page agrees


def test_what_note_answers_is_the_page_recall_page_and_trace_find(env):
    new_project(env, "agree")
    assert noted(env, "agree", fact("cpp"), topic="C++")["page"] == "c"
    answered_pages: dict[str, set[str]] = {"c#": set(), "日本語": set()}
    for over, topic, words, line in [
        ("mcp", "C#", "reified generics", "- C# keeps reified generics at run time.\n"),
        ("rest", "c#", "boxed structs", "- C# boxes value structs on the heap.\n"),
        ("rest", "日本語", "archive handbook", "- The handbook is kept in the archive.\n"),
        ("mcp", "日本語?", "vault binder", "- The binder sits in the vault.\n"),
    ]:
        if over == "mcp":
            answer = call(env, "note", project="agree", text=line, topic=topic)
            hits = call(env, "recall", query=words, project="agree")["hits"]
        else:
            answer = noted(env, "agree", line, topic=topic)
            r = env.client.get(
                "/api/search", headers=env.h, params={"q": words, "project": "agree"}
            )
            hits = r.json()["hits"]
        slug = answer["page"]
        answered_pages[topic.lower().rstrip("?")].add(slug)
        assert slug not in ("c", "untitled"), (topic, slug)
        (hit,) = [h for h in hits if line.strip("- \n") == h["text"].strip("- \n")]
        assert hit["page"] == slug
        if over == "mcp":
            shown = call(env, "page", project="agree", slug=slug)
            traced = call(env, "evidence", project="agree", slug=slug, claim_id=hit["claim_id"])
        else:
            shown = page(env, "agree", slug)
            path = f"/api/projects/agree/pages/{slug}/claims/{hit['claim_id']}/evidence"
            traced = env.client.get(path, headers=env.h).json()
        assert hit["claim_id"] in [claim["id"] for claim in shown["claims"]]
        assert [span["note_id"] for span in traced["passages"]] == [answer["note_id"]]
        assert mark("cpp") not in " ".join(texts(env, "agree", slug))
    assert [len(found) for found in answered_pages.values()] == [1, 1]  # one page per topic

    login(env)
    r = compose(env, "agree", "- The 中文 handbook is kept in the ledger.\n", topic="中文")
    assert r.status_code == 303, r.text
    landed = r.headers["location"].rsplit("/p/", 1)[1]
    assert noted(env, "agree", fact("zh"), topic="中文")["page"] == landed
    assert len(texts(env, "agree", landed)) == 2


def test_the_room_on_a_page_is_counted_on_the_page_the_note_goes_to(env):
    new_project(env, "room")
    full = "".join(f"- Quill keeps item {n} of the list.\n" for n in range(PAGE_CLAIMS_MAX))
    assert noted(env, "room", full, topic="C++")["page"] == "c"
    other = noted(env, "room", fact(1), topic="C#")  # `c` held its slug, not its topic
    assert other["status"] == "saved" and other["page"] != "c"
    before = dump(env.db)
    r = send(env, "room", fact(2), topic="c++")
    assert r.status_code == 409, r.text
    assert dump(env.db) == before  # refused before it was stored


# 6, 7. One note, one page


TEXT = "Quill retries 3 times before failing.\n"
REF = "session:n1-retry"


def test_a_note_sent_again_is_the_same_note_and_another_topic_is_a_conflict_over_rest(env):
    new_project(env, "ops")
    first = noted(env, "ops", TEXT, topic="Retry policy", source_ref=REF, label="ops")
    assert first["created"] is True and first["page"] == "retry-policy"
    before = dump(env.db)
    for fields in [
        {"topic": "Retry policy"},
        {"topic": "retry-policy."},  # the same key
        {"topic": "RETRY  POLICY", "label": "another"},
        {"title": "Retry policy"},  # no topic: the title is the topic
        {"title": "Backoff rules", "topic": "Retry policy"},  # another title, the same topic
    ]:
        again = noted(env, "ops", TEXT, source_ref=REF, **fields)
        assert again["created"] is False, fields
        assert (again["note_id"], again["page"]) == (first["note_id"], "retry-policy")
    over_mcp = call(env, "note", project="ops", text=TEXT, topic="retry policy", source_ref=REF)
    assert over_mcp["created"] is False and over_mcp["note_id"] == first["note_id"]
    assert unaudited(dump(env.db)) == unaudited(before)  # changes nothing

    for fields in [
        {"topic": "Backoff rules"},
        {"title": "Backoff rules"},  # no topic: another title is another topic
        {},  # no topic, no title: the text's first line is the title
        {"topic": "Backoff rules", "label": "ops"},
    ]:
        before = dump(env.db)
        r = send(env, "ops", TEXT, source_ref=REF, **fields)
        assert r.status_code == 409, (fields, r.status_code, r.text)
        assert set(r.json()) == {"error"}
        says_filed_once(r.json()["error"], "retry-policy")
        assert dump(env.db) == before, fields
    assert len(texts(env, "ops", "retry-policy")) == 1
    assert slugs(env, "ops") == {"retry-policy"}


def test_another_topic_is_a_tool_error_over_mcp_and_the_form_again_in_the_composer(env):
    new_project(env, "ops")
    first = call(env, "note", project="ops", text=TEXT, topic="Retry policy", source_ref=REF)
    assert first["page"] == "retry-policy"
    before = dump(env.db)
    refused = tool(env, "note", project="ops", text=TEXT, topic="Backoff rules", source_ref=REF)
    assert refused["isError"] is True
    says_filed_once(refused["content"][0]["text"], "retry-policy")
    assert dump(env.db) == before

    login(env)
    r = compose(env, "ops", TEXT, topic="retry policy", source_ref=REF)
    assert r.status_code == 303 and r.headers["location"] == "/b/ops/p/retry-policy"
    before = dump(env.db)
    r = compose(env, "ops", TEXT, topic="Backoff rules", source_ref=REF)
    assert 400 <= r.status_code < 500, r.status_code
    shown = r.text.replace("&#x27;", "'").replace("&#39;", "'").replace("&quot;", '"')
    assert "retry-policy" in shown and "filed once" in shown.casefold()
    assert "forget" in shown.casefold()
    assert dump(env.db) == before
    assert slugs(env, "ops") == {"retry-policy"}


class Feed:
    """A stand-in kind of source: each pull hands back the next batch the test queued."""

    def __init__(self) -> None:
        self.batches: list[list[dict]] = []

    def check(self, config: object) -> dict:
        assert isinstance(config, dict)
        return {"projects": list(config.get("projects", []))}

    def open(self, name: str, config: dict, **options) -> "FeedSource":
        return FeedSource(config["projects"][0], self)

    def kind(self) -> SourceKind:
        return SourceKind(check=self.check, open=self.open, via="minutes")


class FeedSource:
    def __init__(self, project: str, feed: Feed) -> None:
        self.project, self.feed = project, feed

    async def pull(self, cursor: str | None) -> Pulled:
        batch = self.feed.batches.pop(0)
        drafts = [NoteDraft(project=self.project, **fields) for fields in batch]
        return Pulled(drafts=drafts, cursor=f"c{int((cursor or 'c0')[1:]) + 1}")


def never_called(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"the stand-in source reads nothing: {request.url}")


def test_a_source_skips_a_note_sent_again_for_another_topic_under_a_reason_of_its_own(
    tmp_path, monkeypatch
):
    feed = Feed()
    monkeypatch.setitem(KINDS, "minutes", feed.kind())
    db = tmp_path / "cairn.db"
    store = Store(db, clock=Clock())
    http = httpx.AsyncClient(transport=httpx.MockTransport(never_called))

    def run():
        return asyncio.run(run_source(store, "standup", http=http, env={}, clock=Clock()))

    try:
        project = store.create_project("meetings")
        store.add_source("standup", "minutes", {"projects": ["meetings"]})
        first = {"title": "Payments weekly", "text": TEXT, "source_ref": REF, "label": "meeting"}
        later = {**first, "text": fact(2), "source_ref": "meeting:m-2"}
        cyrillic = {**first, "title": "Настройка", "text": fact(5), "source_ref": "meeting:m-5"}
        feed.batches = [
            [first, cyrillic],
            [{**first, "label": "minutes"}, {**first, "title": "Payments retro"}, later],
            [{**first, "topic": "Payments retro"}],
        ]
        assert run().saved == 2
        two = run()
        assert (two.saved, two.unchanged, two.skipped) == (1, 1, 1), two
        (reason,) = two.reasons
        assert reason not in KNOWN_SKIPS
        assert store.get_source("standup").cursor == "c2"
        three = run()
        assert (three.saved, three.skipped, three.reasons) == (0, 1, {reason: 1}), three
        assert store.get_source("standup").cursor == "c3"
        assert len(store.list_notes(project.id)) == 3
        assert len(store.get_page(project.id, "payments-weekly").claims) == 2
        # the source's note is on the page any other note of its topic goes to
        (sourced,) = store_slugs(store, project.id) - {"payments-weekly"}
        assert sourced != "untitled" and filed(store, project.id, fact(6), "настройка") == sourced
        assert any(mark(5) in c.text for c in store.get_page(project.id, sourced).claims)
    finally:
        asyncio.run(http.aclose())
        store.close()


# 8, 9. Never left behind


class Hold:
    """Store.save_page wrapped: the first call waits until `release` is set. `calls` says, for
    each call, whether the runner standing in for uvicorn had started by then."""

    def __init__(self, monkeypatch) -> None:
        self.started, self.entered, self.release = (threading.Event() for _ in range(3))
        self.calls: list[bool] = []
        original = Store.save_page

        def save_page(store, *args, **kwargs):
            self.calls.append(self.started.is_set())
            if not self.entered.is_set():
                self.entered.set()
                assert self.release.wait(WAIT), "the held compile was never released"
            return original(store, *args, **kwargs)

        monkeypatch.setattr(Store, "save_page", save_page)


def test_a_stored_note_waits_and_serve_files_it_once_after_it_answers(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "cairn.db"
    store = Store(db, clock=Clock())
    key = store.create_key("ops")
    project = store.create_project("ops")
    note, _ = store.add_note(project.id, fact(1), topic="Relay keys", source_ref=REF)
    assert store.search("relay") == []
    assert store.usage().notes == 1
    store.close()
    assert note.id in listed(db, capsys)
    hold = Hold(monkeypatch)
    headers = {"x-api-key": key}

    def during(client):
        try:
            assert hold.entered.wait(WAIT), "serve filed nothing"
            assert answered(client, "/healthz") == 200
            assert answered(client, "/api/projects", headers) == 200
            assert note.id in listed(db, capsys)  # waiting, until its outcome is recorded
            usage = client.get("/api/usage", headers=headers).json()
        finally:
            hold.release.set()
        eventually(lambda: note.id not in listed(db, capsys), "the note filed")
        hits = client.get("/api/search", headers=headers, params={"q": "relay"}).json()["hits"]
        return usage, hits, client.get("/api/usage", headers=headers).json()

    usage, hits, after = serve(monkeypatch, db, during, started=hold.started)
    assert all(hold.calls) and hold.calls  # never before uvicorn ran the app
    assert [hit["page"] for hit in hits] == ["relay-keys"]
    for shown in (usage, after):  # the fields it had; the note is 2099's, not this month's
        assert sorted(shown) == USAGE_FIELDS and sorted(shown["limits"]) == LIMIT_FIELDS
        assert shown["limits"]["notes_per_month"]["used"] == 0
    store = Store(db, clock=Clock())
    try:
        assert store.usage().notes == 1  # counted once, when it was stored
        assert store.page_versions(project.id, "relay-keys") == [1]
        claims = store.get_page(project.id, "relay-keys").claims
        assert [mark(1) in claim.text for claim in claims] == [True]
    finally:
        store.close()
    assert note.id not in listed(db, capsys)


def test_once_serve_returns_the_note_it_had_in_hand_is_filed_or_left_waiting(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "cairn.db"
    note = waiting_note(db, "ops", fact(1), "Relay keys")
    hold, stopped, ended = Hold(monkeypatch), threading.Event(), []

    def release() -> None:  # once the app has stopped, or after QUIET seconds if it waits
        ended.append(stopped.wait(QUIET))
        hold.release.set()

    releaser = threading.Thread(target=release, daemon=True)

    def during(client):
        assert hold.entered.wait(WAIT), "serve filed nothing"
        releaser.start()  # the app stops with the note's page write in hand

    serve(monkeypatch, db, during, started=hold.started, stopped=stopped)
    releaser.join(WAIT)
    assert ended == [False], "the app stopped while a note's page was being written"
    before = dump(db)
    store = Store(db, clock=Clock())
    try:
        (project,) = store.list_projects()
        on_page = store.get_page(project.id, "relay-keys")
        claims = [mark(1) in c.text for c in on_page.claims] if on_page else []
    finally:
        store.close()
    if note in listed(db, capsys):  # left waiting, and nothing of it written
        assert claims == []
    else:  # finished
        assert claims == [True]
    assert dump(db) == before  # nothing writes after serve returned


PLANS_FILE = (
    "default: roomy\n"
    "plans:\n"
    "  roomy:\n    label: Roomy\n"
    "  tight:\n    label: Tight\n    versions_per_page: 1\n"
)


def test_serve_files_each_workspace_s_waiting_notes_under_its_own_plan(
    tmp_path, monkeypatch, capsys
):
    db, plans = tmp_path / "cairn.db", tmp_path / "plans.yaml"
    plans.write_text(PLANS_FILE)
    store = Store(db, clock=Clock())
    workspaces = {}
    for name, plan in (("Ada", "tight"), ("Brook", None)):
        account = store.create_account(name)
        store.set_plan(account.id, plan)
        view = store.for_account(account.id)
        project = view.create_project("ops")
        assert filed(view, project.id, fact(1), "Relay keys") == "relay-keys"
        waiting, _ = view.add_note(project.id, fact(2), topic="relay keys")
        workspaces[name] = (account.id, project.id, waiting.id)
    store.close()

    def during(client):
        for _, _, note in workspaces.values():
            eventually(lambda note=note: note not in listed(db, capsys), "the note filed")

    serve(monkeypatch, db, during, "--plans", str(plans))
    store = Store(db, clock=Clock())
    try:
        for name, kept in (("Ada", [2]), ("Brook", [1, 2])):
            account_id, project_id, _ = workspaces[name]
            view = store.for_account(account_id)
            assert view.page_versions(project_id, "relay-keys") == kept, name
            assert len(view.get_page(project_id, "relay-keys").claims) == 2, name
    finally:
        store.close()


def _serve_until_killed(db: str, when: str) -> None:
    """In a child process: `serve` on the database, killed outright at its second page write,
    `before` it or `after` it."""
    original = Store.save_page
    calls = [0]

    def save_page(store, *args, **kwargs):
        calls[0] += 1
        if calls[0] == 2 and when == "before":
            os.kill(os.getpid(), signal.SIGKILL)
        saved = original(store, *args, **kwargs)
        if calls[0] == 2 and when == "after":
            os.kill(os.getpid(), signal.SIGKILL)
        return saved

    def run(app, **_):
        with TestClient(app, base_url=BASE):
            threading.Event().wait(WAIT)

    Store.save_page = save_page
    uvicorn.run = run
    server.main(["serve", "--db", db, "--no-tidy"])


@pytest.mark.parametrize("when", ["before", "after"])
def test_a_server_killed_in_the_backlog_finishes_it_at_the_next_start_with_no_claim_twice(
    tmp_path, monkeypatch, capsys, when
):
    db = tmp_path / "cairn.db"
    notes = [waiting_note(db, "ops", fact(n), f"Topic {n}") for n in range(3)]
    child = multiprocessing.get_context("spawn").Process(
        target=_serve_until_killed, args=(str(db), when)
    )
    child.start()
    child.join(WAIT * 2)
    assert child.exitcode == -signal.SIGKILL, child.exitcode
    left = [note for note in notes if note in listed(db, capsys)]
    assert left  # at least the note being written at the kill was not filed

    def during(client):
        eventually(lambda: not any(n in listed(db, capsys) for n in notes), "the backlog")

    serve(monkeypatch, db, during)
    store = Store(db, clock=Clock())
    try:
        (project,) = store.list_projects()
        for n in range(3):
            slug = f"topic-{n}"
            assert store.page_versions(project.id, slug) == [1], slug
            assert [mark(n) in c.text for c in store.get_page(project.id, slug).claims] == [True]
        assert store_slugs(store, project.id) == {f"topic-{n}" for n in range(3)}
    finally:
        store.close()


def test_a_note_its_gate_rejects_or_its_full_page_refuses_waits_no_more(
    tmp_path, monkeypatch, capsys
):
    db = tmp_path / "cairn.db"
    store = Store(db, clock=Clock())
    project = store.create_project("ops")
    bogus = Claim(id="cl_00000000000a", text="A claim.", span_ids=("sp_00000000000a",))
    store.save_page(project.id, "bogus", "Bogus", "# Bogus\n\n- A claim.\n", [bogus])
    rejected, _ = store.add_note(project.id, fact(1), topic="Bogus")
    full = "".join(f"- Quill keeps item {n} of the list.\n" for n in range(PAGE_CLAIMS_MAX))
    assert filed(store, project.id, full, "Full list") == "full-list"
    refused, _ = store.add_note(project.id, fact(2), topic="full list")
    store.close()
    assert all(n in listed(db, capsys) for n in (rejected.id, refused.id))

    def during(client):
        eventually(
            lambda: not {rejected.id, refused.id} & set(re.findall(r"cap_\w+", listed(db, capsys))),
            "both outcomes recorded",
        )

    serve(monkeypatch, db, during)
    store = Store(db, clock=Clock())
    try:
        assert "note.rejected" in [event.action for event in store.list_audit(50)]
        assert store.page_versions(project.id, "bogus") == [1]
        assert store.page_versions(project.id, "full-list") == [1]
        assert len(store.get_page(project.id, "full-list").claims) == PAGE_CLAIMS_MAX
    finally:
        store.close()


LOCKED = "- Brook's locker code is 4471, and the spare is under the blue mat.\n"
SECRET = ("4471", "locker", "blue mat")
FLAKY = "- Cy turns the relay keys every Friday at noon.\n"


def test_a_compile_that_fails_unforeseen_is_logged_without_its_text_and_tried_a_bounded_number(
    tmp_path, monkeypatch, capsys, caplog
):
    caplog.set_level(logging.DEBUG)
    db = tmp_path / "cairn.db"
    heard: list[str] = []

    def one_start(n: int) -> int:
        """A start of `serve` with one more ordinary note waiting, watched until that note is
        filed and, for up to QUIET seconds, for a try of the locked note: the records at
        WARNING or above it logged."""
        before, records = tries["locked"], len(caplog.records)
        sentinel = waiting_note(db, "ops", fact(f"s{n}"), f"Sentinel {n}")

        def during(client):
            eventually(lambda: sentinel not in listed(db, capsys, heard), "the start's other note")
            within(lambda: tries["locked"] > before, QUIET)

        serve(monkeypatch, db, during)
        heard.extend(capsys.readouterr())
        return sum(r.levelno >= logging.WARNING for r in caplog.records[records:])

    tries = {"locked": 0, "flaky": 0}
    control = one_start(0)  # nothing fails yet: what a start logs anyway
    locked = waiting_note(db, "ops", LOCKED, "Lockers")
    flaky = waiting_note(db, "ops", FLAKY, "Relay")
    original = Store.save_page

    def save_page(store, *args, **kwargs):
        seen = repr((args, kwargs))
        if "4471" in seen:
            tries["locked"] += 1
            raise RuntimeError(f"the disk refused {LOCKED!r}")
        if "relay keys" in seen:
            tries["flaky"] += 1
            if tries["flaky"] == 1:
                raise RuntimeError(f"the disk refused {FLAKY!r}")
        return original(store, *args, **kwargs)

    monkeypatch.setattr(Store, "save_page", save_page)
    per_start: list[int] = []
    warned: list[int] = []
    for start in range(1, 23):
        before = tries["locked"]
        warned.append(one_start(start))
        per_start.append(tries["locked"] - before)
        if per_start[-2:] == [0, 0]:
            break
    assert per_start[0] >= 1 and per_start[1] >= 1, per_start  # tried again at the next start
    stopped = per_start.index(0)
    assert stopped <= 20 and not any(per_start[stopped:]), per_start
    assert warned[0] > control and warned[1] > control, (control, warned)  # logged each time
    assert locked in listed(db, capsys)  # still waiting
    assert flaky not in listed(db, capsys) and tries["flaky"] == 2

    for record in caplog.records:
        said = record.getMessage() + str(record.args)
        assert not any(word in said for word in SECRET), said
        if record.levelno >= logging.WARNING:  # no traceback, which names what failed
            assert record.exc_info is None and record.exc_text is None, said
    everything = caplog.text + "".join(heard)
    assert not any(word in everything for word in SECRET)
    store = Store(db, clock=Clock())
    try:
        (project,) = store.list_projects()
        assert [("relay keys" in c.text) for c in store.get_page(project.id, "relay").claims] == [
            True
        ]
    finally:
        store.close()


# 10. A note that loses the race for its page


class Interloper:
    """Store.save_page wrapped: before each of the first `times` writes of a page for the note
    holding `marker`, a second Store on the same file saves that page again, so the write
    finds the page moved."""

    def __init__(self, monkeypatch, db: Path, marker: str, times: int) -> None:
        self.other = Store(db, clock=Clock())
        self.left, self.moved = times, 0
        original = Store.save_page

        def save_page(store, *args, **kwargs):
            if self.left and marker in repr((args, kwargs)):
                self.left -= 1
                named = {**dict(zip(("project_id", "slug"), args, strict=False)), **kwargs}
                latest = self.other.get_page(named["project_id"], named["slug"])
                if latest is not None:
                    self.moved += 1
                    original(
                        self.other, latest.project_id, latest.slug, latest.title, latest.body,
                        list(latest.claims), metrics=latest.metrics,
                    )  # fmt: skip
            return original(store, *args, **kwargs)

        monkeypatch.setattr(Store, "save_page", save_page)


def test_a_note_whose_page_moves_more_often_than_the_compiler_retries_is_compiled_once_more(
    env, monkeypatch
):
    new_project(env, "busy")
    noted(env, "busy", fact(0), topic="Relay keys")
    interloper = Interloper(monkeypatch, env.db, mark(1), SAVE_TRIES)
    try:
        answer = noted(env, "busy", fact(1), topic="Relay keys")
    finally:
        interloper.other.close()
    assert interloper.moved == SAVE_TRIES
    assert answer["status"] == "saved" and answer["page"] == "relay-keys"
    assert times_held(env, "busy", mark(1)) == 1


def test_a_note_that_keeps_losing_is_not_answered_as_done_and_waits_for_serve(
    env, tmp_path, monkeypatch, capsys
):
    new_project(env, "busy")
    noted(env, "busy", fact(0), topic="Relay keys")
    interloper = Interloper(monkeypatch, env.db, "keeps losing", 10**6)
    try:
        r = send(env, "busy", "- Quill keeps losing over REST.\n", topic="Relay keys")
        assert r.status_code != 201, r.text
        assert interloper.moved >= 2 * SAVE_TRIES  # the whole compile, once more
        result = tool(
            env, "note", project="busy", text="- It keeps losing over MCP.\n", topic="relay keys"
        )
        if not result["isError"]:
            assert json.loads(result["content"][0]["text"])["status"] not in ("saved", "unchanged")
    finally:
        interloper.left = 0
        interloper.other.close()
    assert times_held(env, "busy", "keeps losing") == 0
    out = listed(env.db, capsys)
    (project,) = env.store.list_projects()
    lost = [c.id for c in env.store.list_notes(project.id) if "keeps losing" in c.text]
    assert len(lost) == 2 and all(note in out for note in lost)

    def during(client):
        eventually(lambda: not any(n in listed(env.db, capsys) for n in lost), "both filed")

    serve(monkeypatch, env.db, during)
    assert times_held(env, "busy", "keeps losing") == 2


def test_five_notes_racing_for_one_page_all_end_on_it_and_none_is_answered_201_without_it(
    tmp_path, monkeypatch, capsys
):
    barrier = threading.Barrier(5)
    local = threading.local()

    def gate(candidate, spans):
        if not getattr(local, "seen", False):
            local.seen = True
            with contextlib.suppress(threading.BrokenBarrierError):
                barrier.wait(WAIT / 3)  # a writer that waits its turn breaks it, and goes on
        return lint_gate(candidate, spans)

    db = tmp_path / "cairn.db"
    store = Store(db, clock=Clock())
    answers: dict[int, httpx.Response] = {}
    with app_on(store, db, gate=gate) as env:
        new_project(env, "race")

        def post(n: int) -> None:
            answers[n] = send(env, "race", fact(n), topic="Shared")

        threads = [threading.Thread(target=post, args=(n,)) for n in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(WAIT * 2)
        assert sorted(answers) == list(range(5))
        for n, r in answers.items():
            if r.status_code == 201:
                assert r.json()["status"] in ("saved", "unchanged"), r.text
                version = r.json()["version"]
                shown = texts_at(env, "race", "shared", version)
                assert any(mark(n) in text for text in shown), (n, r.text)
    store.close()

    def during(client):
        eventually(lambda: "cap_" not in listed(db, capsys), "nothing waiting")

    serve(monkeypatch, db, during)
    store = Store(db, clock=Clock())
    try:
        (project,) = store.list_projects()
        claims = [c.text for c in store.get_page(project.id, "shared").claims]
        assert sorted(sum(mark(n) in t for t in claims) for n in range(5)) == [1] * 5
    finally:
        store.close()


def texts_at(env, project: str, slug: str, version: int) -> list[str]:
    return [claim["text"] for claim in page(env, project, slug, version)["claims"]]


# 11. Erasure, export and import


def test_forgetting_a_waiting_note_or_deleting_its_project_or_workspace_leaves_no_record(
    tmp_path, capsys
):
    db = tmp_path / "cairn.db"
    store = Store(db, clock=Clock())
    try:
        kept = store.create_project("kept")
        gone = store.create_project("gone")
        forgotten, _ = store.add_note(kept.id, fact(1), topic="Relay keys")
        in_project, _ = store.add_note(gone.id, fact(2), topic="Relay keys")
        dee = store.create_account("Dee")
        dee_view = store.for_account(dee.id)
        in_workspace, _ = dee_view.add_note(
            dee_view.create_project("notes").id, fact(3), topic="Relay keys"
        )
        out = listed(db, capsys)
        assert all(n.id in out for n in (forgotten, in_project, in_workspace))

        store.forget_note(forgotten.id)
        assert store.delete_project(gone.id)
        assert store.delete_account(dee.id)
        out = listed(db, capsys)
        for note in (forgotten, in_project, in_workspace):
            assert note.id not in out
            rows = [line for line in unaudited(dump(db)) if note.id in line]
            assert rows == [], rows
    finally:
        store.close()


def test_an_imported_workspace_files_notes_on_the_pages_the_exported_one_did(env, tmp_path):
    new_project(env, "keys")
    topics = ["C++", "C#", "C", "日本語", "中文", "!!!", f"{LONG} east", f"{LONG} west"]
    pages = {
        topic: noted(env, "keys", fact(n), topic=topic)["page"] for n, topic in enumerate(topics)
    }
    assert len(set(pages.values())) == len(topics)
    archive = io.BytesIO()
    env.store.export(archive)

    moved = tmp_path / "moved.db"
    target = Store(moved, clock=Clock())
    try:
        target.import_export(io.BytesIO(archive.getvalue()))
        with app_on(target, moved) as there:
            for n, topic in enumerate(topics):
                again = noted(there, "keys", fact(f"m{n}"), topic=topic.lower())
                assert again["page"] == pages[topic], topic
            assert slugs(there, "keys") == set(pages.values())
    finally:
        target.close()


# With nothing waiting and no collision


def test_a_note_s_answer_is_byte_for_byte_what_it_was(env):
    new_project(env, "alpha")
    r = send(env, "alpha", TEXT, topic="Retry policy", source_ref=REF)
    answer = {
        "note_id": r.json()["note_id"],
        "created": True,
        "page": "retry-policy",
        "status": "saved",
        "version": 1,
        "violations": [],
    }
    compact = json.dumps(answer, separators=(",", ":"), ensure_ascii=False).encode()
    assert (r.status_code, r.headers["content-type"], r.content) == (
        201,
        "application/json",
        compact,
    )
    r = send(env, "alpha", TEXT, topic="Retry policy", source_ref=REF)
    again = {**answer, "created": False, "status": "unchanged"}
    assert r.content == json.dumps(again, separators=(",", ":"), ensure_ascii=False).encode()

    result = tool(
        env, "note", project="alpha", text="- Lumen never retries.\n", topic="Retry policy"
    )
    note = json.loads(result["content"][0]["text"])["note_id"]
    over_mcp = {**answer, "note_id": note, "version": 2}
    assert result == {
        "content": [{"type": "text", "text": json.dumps(over_mcp, indent=2)}],
        "isError": False,
    }
