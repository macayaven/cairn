"""Q1 acceptance tests (frozen): bounded reads, a bounded history, numbers for the operator.

Written by the orchestrator before implementation. Implementers must not edit this file.
The contract goes in the docstrings of src/cairn/server.py (A and the answers' new fields),
src/cairn/web/cli.py (serve's options, `stats`, `compact`) and src/cairn/web/app.py
(create_app's new arguments and the stats line).

A. A read costs what it returns
1. A read's statements are what the store's connection runs for one request, as its trace
   callback reports them, leaving out transaction control (BEGIN, COMMIT, END, ROLLBACK,
   SAVEPOINT, RELEASE) and what SQLite runs inside a statement (reported with a leading
   "--"). Each of these runs the same number of statements whatever the number of projects,
   pages, notes, versions or cited notes (two sizes, a factor of ten apart), and at most 12,
   for an API key and for a web session opened with one: the home page; `projects` (MCP) and
   GET /api/projects; `overview` (MCP) and GET /api/projects/<project>/pages; the project's page
   GET /b/<project>; a page whose claim cites many notes, GET /b/<project>/p/<slug>, `page`
   (MCP) and GET /api/projects/<project>/pages/<slug>; `recall` (MCP) and GET /api/search for a
   hit that cites several notes; `evidence` (MCP) and GET .../claims/<id>/evidence for that
   claim. A person's web session runs the same number at both sizes on the home page, the
   project's page and the page.
2. A count does not read what it counts: with every note's text and every page's body
   300,000 characters larger, the bytes the rows of those statements hold (every value, as it comes
   back from the connection) do not grow for the home page, `projects`, GET /api/projects,
   `overview`, GET /api/projects/<project>/pages and GET /b/<project>.
3. Under the bounds below every answer is today's: every field today's answers have, with
   today's value, among themselves in today's order, and each list in today's order (other
   changes may add fields beside them). No field of this contract appears there.
4. Lists have a bound and say when there is more. `overview` (MCP) and GET
   /api/projects/<project>/pages give a project's pages at most 100 at a time, by slug as today;
   they take `limit` (how many, at most 100) and `after` (MCP: arguments; REST: the query).
   An answer that leaves pages out has `pages_total` (how many there are in all) and, while
   more follow, `pages_next`: what to pass as `after` for the next part, passed back as
   given. Pages added between two askings neither make the next part repeat a page nor skip
   one: the next part holds every page after the last one given. `notes` still counts
   every note. The web page of a project shows 200 at a time, says how many there are in all,
   and links to the next part with an address of the same page and a query. A page's
   versions (`page` over MCP, GET /api/projects/<project>/pages/<slug>) are the one list read
   from its end: the part given first is the latest 50, in today's order (oldest first
   within the part); `limit` (at most 50) takes fewer of the latest, and `before` (a
   version's number) gives the versions numbered below it, the latest of those first in
   the same way. A part that leaves versions out has `versions_total`, and, while earlier
   versions remain, `versions_next`: what to pass as `before`, passed back as given. The
   web page view shows the latest 50 version numbers, says how many there are in all, and
   links to the earlier ones. An answer that holds everything has none of these fields.

B. A page's history has a bound without plans
5. `serve` keeps the last 50 versions of each page when it has no plans; `--keep-versions N`
   (a whole number, 1 or more) keeps N, `--keep-versions all` every version; any other
   value exits 2. With `--plans` a plan's own versions_per_page rules as today, and
   `--keep-versions`, whatever its value, is refused: exit 2, one line on stderr, nothing
   served. What `serve` builds without plans is `create_app(store, keep_versions=N)`;
   `keep_versions=None`, create_app's default, keeps every version.
6. A save that takes a page past the bound removes its oldest versions in its own
   transaction: a save that fails removes nothing. Reading never removes a version. The
   page's latest version, its address and every citation (claims cite notes, not versions)
   are untouched.
7. At start, when pages hold more versions than the bound, `serve` says so on one line of
   stderr before it listens: how many pages, the bound, and that `--keep-versions all`
   keeps them; it removes nothing then, and a page's older versions go when it is next
   saved. `serve` records in the database the bound it runs with (a number, every version,
   or its plans' rules), and `compact` removes by the last such record and by nothing else,
   at once: what `serve` was told to keep, a compaction keeps; with plans, each workspace's
   plan rules as for a save. Its output says how many versions it removed. On a database
   no `serve` of this version has run on it removes no version and says why, naming
   `serve`. How the record is kept is the implementer's: these tests read it only through
   `serve` then `compact`.
8. The review's case: thirteen large notes on one page keep 13 versions under the default
   (24 KB each here: a page holds at most 400,000 characters of claims, so the review's own
   72 KB notes no longer all fit one page); 60 small saves of one page leave the newest 50;
   from then on each further small save grows the compacted database by far less than one
   copy of the page (each version holds the whole page, so a growing page still grows the
   file a little).
9. The page's history on the web says, when older versions were removed, that it shows the
   last N; `page`'s `versions` over MCP and REST lists the versions that exist.

C. Numbers an operator can read
10. `python -m cairn.server stats --db PATH [--json]` opens the database read-only: the
    file's bytes and modification time are unchanged, and it runs while `serve` runs. It
    refuses as the other commands that open an existing database refuse (exit 1, one line
    on stderr, nothing on stdout, no traceback, nothing written, made or removed, no mode
    changed), with the line `key-list` prints for the same path: a missing database, a
    file that is not a database, one it may not read, one in a folder it may not write, a
    folder, and one of a newer schema. One of an older schema is refused the same way, on
    purpose, with a sentence that says to start the server once (it holds "start" and
    "serve"): the other commands upgrade an older database, and a command that only reads
    must not. `--json` prints one object:
    - for the install: `schema` (the schema's number), `db_bytes` and `wal_bytes` (the sizes
      of the database file and of its "-wal" file, 0 when there is none), `free_bytes`
      (free space on its disk: `shutil.disk_usage(...).free` for the database's folder or
      file, looked up when the numbers are gathered) and `low_disk` (free_bytes under twice
      db_bytes, what a compaction needs);
    - for the install, and per workspace under `workspaces` (an object keyed by the
      workspace's id): `projects`, `notes`, `pages`, `page_versions`, `waiting` (notes stored
      and not yet filed on a page), `keys`, `agents` (connected agents), `newest_note` (the
      newest note's time as stored; null with none) and `sources`: an object keyed by kind,
      each `{"on": how many are on, "failed": how many's last run failed}`.
    Counts, sizes and times only: no note's text, no title, no name of a project, a person, a
    workspace, a source or a connected agent, no address, no key's label, no reason a
    source failed, in either output.
11. `create_app(store, stats_every=MINUTES, stats_wait=W)` logs the install's numbers as one
    line on a logger of its own, not "cairn.access" (its name is the implementer's; the
    tests find the line by its shape on whatever logger it comes): a fixed first word, a
    space, then one JSON object holding the install's numbers of `stats --json` (the
    totals: no `workspaces`, no workspace's id) and `requests`: {"2xx", "3xx", "4xx", "5xx",
    "429"}, the responses the app sent since the last line by class, the 429s counted in
    "4xx" too. The first line comes at start, then the loop awaits `W(seconds)`
    (asyncio.sleep when not given) and logs again, without end. The numbers are gathered on
    a worker thread, never on the event loop. A pass that fails logs its error's class,
    never its message, on the same logger, and the next pass runs. `stats_every` None or 0:
    no line and no loop (create_app's default). `serve --stats-every MINUTES` (15; 0: off;
    anything else not a number of 0 or more exits 2) passes it, and `app.state.stats_every`
    holds the minutes, None when off. On by default: a plain `serve` writes the line at
    start to its stderr, where its other lines go.
12. When free space is under twice the database's size, the line is logged at WARNING (at
    INFO otherwise) with a sentence that says writes fail and compaction and backup cannot
    run; nothing else changes, and /healthz answers as before.
13. No address serves these numbers: no route's path names them, and no answer of the app
    holds their names.

Every name here is made up; every clock is far ahead of the real date.
"""

import asyncio
import contextlib
import html
import json
import logging
import os
import re
import sqlite3
import stat
import threading
from collections import namedtuple
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from starlette.routing import Route
from starlette.testclient import TestClient

from cairn.compiler import claim_id, compile_note, provenance_gate, render_body
from cairn.kinds import KINDS as PROJECT_KINDS
from cairn.kinds import kind_of
from cairn.plans import Plan, Plans
from cairn.server import create_app, main
from cairn.sources import KINDS as SOURCE_KINDS
from cairn.store import SCHEMA_VERSION, Claim, Store

BASE = "http://127.0.0.1:8765"
ISSUER = "https://id.example.test/pool-q1"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
START = datetime(2099, 3, 1, 9, 0, tzinfo=UTC)
CEILING = 12
PAD = 300_000  # characters: a large paragraph, inside the 400,000 a page's claims may hold
QUIET = {"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE"}
STATS_NAMES = ("free_bytes", "wal_bytes", "low_disk")  # names no answer of the app holds
Usage = namedtuple("Usage", "total used free")


class Clock:
    """A store's clock, far ahead of the real date: each reading a second after the last, so
    each note has its own time."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


def dump(db: Path) -> str:
    con = sqlite3.connect(db)
    try:
        return "\n".join(con.iterdump())
    finally:
        con.close()


def visible(page: str) -> str:
    """A web page's text as a person reads it: no tags, entities read, blanks folded."""
    text = re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", page)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def hrefs(page: str) -> list[str]:
    return [html.unescape(link) for link in re.findall(r'href="([^"]*)"', page)]


def tool(w, name: str, /, **arguments):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}  # fmt: skip
    return w.client.post("/mcp", headers={**MCP_HEADERS, **w.h}, json=body)


def answer(response) -> dict:
    """What a REST route answered, or what an MCP tool returned; never an error."""
    assert response.status_code == 200, response.text
    data = response.json()
    if "jsonrpc" in data:
        result = data["result"]
        assert result["isError"] is False, result
        return json.loads(result["content"][0]["text"])
    return data


def holds(actual, expected, where: str = "answer") -> None:
    """Every field `expected` has is in `actual` with the same value, and in the same order
    among themselves; lists match item by item. Fields beside them are allowed."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{where}: {actual!r}"
        missing = [name for name in expected if name not in actual]
        assert not missing, f"{where}: {missing} missing"
        assert [name for name in actual if name in expected] == list(expected), where
        for name, value in expected.items():
            holds(actual[name], value, f"{where}.{name}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{where}: {actual!r}"
        assert len(actual) == len(expected), f"{where}: {len(actual)} items, not {len(expected)}"
        for n, (got, wanted) in enumerate(zip(actual, expected, strict=True)):
            holds(got, wanted, f"{where}[{n}]")
    else:
        assert actual == expected, f"{where}: {actual!r} != {expected!r}"


class Watch:
    """What the store's connection runs and hands back during a call: its statements (the
    trace callback) and the bytes of every value of every row (the row factory)."""

    def __init__(self, store: Store) -> None:
        self.store = store

    @contextlib.contextmanager
    def __call__(self):
        seen = SimpleNamespace(statements=[], bytes=0)
        before = self.store._state.observers.rows

        def traced(statement: str) -> None:
            text = statement.lstrip()
            if text.startswith("--") or not text:
                return
            if text.split(None, 1)[0].upper().rstrip(";") in QUIET:
                return
            seen.statements.append(text)

        def rows(cursor, row):
            for value in row:
                if isinstance(value, str):
                    seen.bytes += len(value.encode())
                elif isinstance(value, bytes):
                    seen.bytes += len(value)
                else:
                    seen.bytes += 8
            return row if before is None else before(cursor, row)

        self.store._observe(trace=traced)
        self.store._observe(rows=rows)
        try:
            yield seen
        finally:
            self.store._observe(trace=None)
            self.store._observe(rows=before)


# A: what a read costs


def grow(store: Store, n: int, pad: str = "") -> Claim:
    """`n` projects of one page each; the project "atlas": `n` pages of one note each and the page
    "ledger", saved `n` times, whose one claim cites a passage of each of `n` more notes.
    `pad`: one more paragraph in every note (and so on every page)."""
    tail = f"\n\n{pad}" if pad else ""
    for i in range(n):
        shelf = store.create_project(f"shelf-{i:03d}")
        note, _ = store.add_note(shelf.id, f"# Shelf {i}\n\nShelf {i} holds quill spares.{tail}")
        compile_note(store, note, provenance_gate)
    atlas = store.create_project("atlas", "Maps of the north wing")
    for i in range(n):
        text = f"Leaf {i} faces the north wall.{tail}"
        note, _ = store.add_note(atlas.id, text, topic=f"leaf-{i:03d}")
        compile_note(store, note, provenance_gate)
    cited = []
    for i in range(n):
        text = f"# Entry {i}\n\nLedger entry {i} names the quorum.{tail}"
        note, _ = store.add_note(atlas.id, text, source_ref=f"desk-{i}")
        cited.append(next(s for s in store.note_spans(note.id) if s.kind == "paragraph"))
    claim = Claim(
        id=claim_id(cited[0].id),
        text="The quorum ledger counts every entry.",
        span_ids=tuple(span.id for span in cited),
    )
    body = render_body("Ledger", [claim], {span.id: span for span in cited})
    for _ in range(n):
        store.save_page(atlas.id, "ledger", "Ledger", body, [claim])
    return claim


def world_of(folder: Path, n: int, pad: str = "") -> SimpleNamespace:
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "cairn.db"
    store = Store(db, clock=Clock())
    key = store.create_key("ops-desk")
    claim = grow(store, n, pad)
    ada = store.sign_in(
        "sso", "sub-ada", "ada@example.test", "Ada", issuer=ISSUER, join="default",
        ttl_seconds=90 * 86_400,
    )  # fmt: skip
    client = TestClient(create_app(store, public_url=BASE), base_url=BASE)
    client.__enter__()
    opened = client.post("/login", data={"key": key}, follow_redirects=False)
    assert opened.status_code == 303
    keyed = {"cookie": f"cairn_session={opened.cookies['cairn_session']}"}
    client.cookies.clear()
    return SimpleNamespace(
        n=n, db=db, store=store, client=client, h={"x-api-key": key}, keyed=keyed,
        person={"cookie": f"cairn_session={ada.token}"}, claim=claim, watch=Watch(store),
    )  # fmt: skip


def close(w: SimpleNamespace) -> None:
    w.client.__exit__(None, None, None)
    w.store.close()


@pytest.fixture(scope="module")
def worlds(tmp_path_factory):
    folder = tmp_path_factory.mktemp("q1-reads")
    made = SimpleNamespace(
        small=world_of(folder / "small", 3),
        large=world_of(folder / "large", 30),
        padded=world_of(folder / "padded", 3, "x" * PAD),
    )
    yield made
    for w in vars(made).values():
        close(w)


def trace_path(w) -> str:
    return f"/api/projects/atlas/pages/ledger/claims/{w.claim.id}/evidence"


READS = {
    "home": lambda w: w.client.get("/", headers=w.keyed),
    "projects over MCP": lambda w: tool(w, "projects"),
    "projects over REST": lambda w: w.client.get("/api/projects", headers=w.h),
    "overview over MCP": lambda w: tool(w, "overview", project="atlas"),
    "overview over REST": lambda w: w.client.get("/api/projects/atlas/pages", headers=w.h),
    "project page": lambda w: w.client.get("/b/atlas", headers=w.keyed),
    "page on the web": lambda w: w.client.get("/b/atlas/p/ledger", headers=w.keyed),
    "page over MCP": lambda w: tool(w, "page", project="atlas", slug="ledger"),
    "page over REST": lambda w: w.client.get("/api/projects/atlas/pages/ledger", headers=w.h),
    "recall over MCP": lambda w: tool(w, "recall", query="quorum ledger"),
    "recall over REST": lambda w: w.client.get("/api/search?q=quorum+ledger", headers=w.h),
    "trace over MCP": lambda w: tool(
        w, "evidence", project="atlas", slug="ledger", claim_id=w.claim.id
    ),
    "trace over REST": lambda w: w.client.get(trace_path(w), headers=w.h),
}
COUNTED = (  # rule 2: the reads that count
    "home", "projects over MCP", "projects over REST", "overview over MCP", "overview over REST",
    "project page",
)  # fmt: skip


def read(w, name: str):
    response = READS[name](w)
    assert response.status_code == 200, (name, response.text[:300])
    if response.headers["content-type"].startswith("application/json"):
        found = answer(response)
        if name.startswith("recall"):  # the hit cites a passage of each of the n notes
            assert len(found["hits"][0]["passage_ids"]) == w.n
        if name.startswith("trace"):
            assert len(found["passages"]) == w.n
    return response


@pytest.mark.parametrize("name", list(READS))
def test_a_read_runs_the_same_statements_at_two_sizes_and_twelve_at_most(worlds, name):
    counts = []
    for w in (worlds.small, worlds.large):
        read(w, name)  # once before: nothing a first call does once is counted
        with w.watch() as seen:
            read(w, name)
        counts.append(len(seen.statements))
    assert counts[0] == counts[1], (
        f"{name}: {counts[0]} statements with {worlds.small.n} of each, {counts[1]} with"
        f" {worlds.large.n}"
    )
    assert counts[1] <= CEILING, f"{name}: {counts[1]} statements"


@pytest.mark.parametrize("path", ["/", "/b/atlas", "/b/atlas/p/ledger"])
def test_a_persons_web_session_runs_the_same_statements_at_two_sizes(worlds, path):
    counts = []
    for w in (worlds.small, worlds.large):
        assert w.client.get(path, headers=w.person).status_code == 200
        with w.watch() as seen:
            assert w.client.get(path, headers=w.person).status_code == 200
        counts.append(len(seen.statements))
    assert counts[0] == counts[1], f"{path}: {counts}"


@pytest.mark.parametrize("name", COUNTED)
def test_a_count_reads_no_page_body_and_no_note_text(worlds, name):
    measured = []
    for w in (worlds.small, worlds.padded):
        read(w, name)
        with w.watch() as seen:
            read(w, name)
        measured.append(seen.bytes)
    plain, padded = measured
    # Every note and page of the padded world is 300,000 characters larger; what is read is not.
    assert padded - plain < 16_384, f"{name}: {plain:,} bytes read, {padded:,} when padded"


def test_answers_under_the_bounds_are_todays(worlds):
    w = worlds.small
    store = w.store
    atlas = store.get_project("atlas")

    def kind_fields(project) -> dict:
        kind = kind_of(project.kind)
        return {"kind": kind.id, "guidance": kind.guidance}

    projects = {
        "projects": [
            {"id": b.id, "name": b.name, "description": b.description,
             "pages": len(store.list_pages(b.id)), **kind_fields(b)}
            for b in store.list_projects()
        ],
        "kinds": [
            {"id": k.id, "title": k.title, "summary": k.summary} for k in PROJECT_KINDS.values()
        ],
    }  # fmt: skip
    holds(answer(tool(w, "projects")), projects, "projects")
    holds(answer(w.client.get("/api/projects", headers=w.h)), projects, "GET /api/projects")

    summaries = [
        {"slug": p.slug, "title": p.title, "version": p.version, "claims": len(p.claims),
         "updated_at": p.updated_at}
        for p in store.list_pages(atlas.id)
    ]  # fmt: skip
    overview = answer(tool(w, "overview", project="atlas"))
    holds(overview, {
        "project": "atlas", **kind_fields(atlas), "notes": len(store.list_notes(atlas.id)),
        "pages": summaries,
    }, "overview")  # fmt: skip
    listed = answer(w.client.get("/api/projects/atlas/pages", headers=w.h))
    holds(listed, {"project": "atlas", "pages": summaries}, "GET pages")
    for found in (overview, listed):
        assert not [name for name in found if name.startswith("pages_")]

    latest = store.get_page(atlas.id, "ledger")
    page = {
        "project": "atlas", "slug": "ledger", "title": latest.title, "version": latest.version,
        "versions": store.page_versions(atlas.id, "ledger"), "body": latest.body,
        "claims": [
            {"id": c.id, "text": c.text, "passage_ids": list(c.span_ids)} for c in latest.claims
        ],
        "metrics": latest.metrics,
    }  # fmt: skip
    for found in (
        answer(tool(w, "page", project="atlas", slug="ledger")),
        answer(w.client.get("/api/projects/atlas/pages/ledger", headers=w.h)),
    ):
        holds(found, page, "page")
        assert not [name for name in found if name.startswith("versions_")]

    hits = {
        "hits": [
            {"project": hit.project, "page": hit.slug, "title": hit.title, "claim_id": hit.claim_id,
             "text": hit.text, "passage_ids": list(hit.span_ids), "score": hit.score}
            for hit in store.search("quorum ledger", None, 8)
        ]
    }  # fmt: skip
    holds(answer(tool(w, "recall", query="quorum ledger")), hits, "recall")
    holds(answer(w.client.get("/api/search?q=quorum+ledger", headers=w.h)), hits, "search")

    spans = store.get_spans(w.claim.span_ids)
    traced = {
        "claim": {"id": w.claim.id, "text": w.claim.text, "passage_ids": list(w.claim.span_ids)},
        "passages": [
            {"id": s.id, "note_id": s.source_id,
             "note_title": store.get_note(s.source_id).title,
             "source_ref": store.get_note(s.source_id).source_ref,
             "heading_path": list(s.heading_path), "start_line": s.start_line,
             "end_line": s.end_line, "text": s.text}
            for s in (spans[span_id] for span_id in w.claim.span_ids)
        ],
    }  # fmt: skip
    holds(answer(tool(w, "evidence", project="atlas", slug="ledger", claim_id=w.claim.id)), traced)
    holds(answer(w.client.get(trace_path(w), headers=w.h)), traced, "GET trace")

    # The web pages show what they show today.
    home = w.client.get("/", headers=w.keyed).text
    for b in store.list_projects():
        count = len(store.list_pages(b.id))
        assert f'href="/b/{b.name}"' in home
        assert f"{count} {'page' if count == 1 else 'pages'}." in visible(home)
    shown = w.client.get("/b/atlas", headers=w.keyed).text
    assert [link for link in hrefs(shown) if link.startswith("/b/atlas/p/")] == [
        f"/b/atlas/p/{p['slug']}" for p in summaries
    ]
    notes = len(store.list_notes(atlas.id))
    assert f"{len(summaries)} pages compiled from {notes} notes" in visible(shown)
    viewed = visible(w.client.get("/b/atlas/p/ledger", headers=w.keyed).text)
    assert w.claim.text in viewed
    for number in store.page_versions(atlas.id, "ledger"):
        assert f"v{number}" in viewed.split()


@pytest.fixture
def many(tmp_path):
    """A project of 250 pages, one note each and 7 more on the first, served to a key and a
    web session: 257 notes."""
    db = tmp_path / "cairn.db"
    store = Store(db, clock=Clock())
    key = store.create_key("ops-desk")
    atlas = store.create_project("atlas")
    for i in range(250):
        note, _ = store.add_note(atlas.id, f"Leaf {i} faces the north wall.", topic=f"leaf-{i:03d}")
        compile_note(store, note, provenance_gate)
    for i in range(7):
        note, _ = store.add_note(atlas.id, f"Leaf 0 turns {i} times.", topic="leaf-000")
        compile_note(store, note, provenance_gate)
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        opened = client.post("/login", data={"key": key}, follow_redirects=False)
        keyed = {"cookie": f"cairn_session={opened.cookies['cairn_session']}"}
        client.cookies.clear()
        yield SimpleNamespace(
            store=store, client=client, h={"x-api-key": key}, keyed=keyed, atlas=atlas,
            slugs=[f"leaf-{i:03d}" for i in range(250)],
        )  # fmt: skip
    store.close()


def walk(ask, listed: str = "pages") -> list[dict]:
    """Every part of a list: ask(None), then ask(<the last part's `<listed>_next`>) while there
    is one."""
    parts = [ask(None)]
    while parts[-1].get(f"{listed}_next") is not None:
        assert len(parts) < 40, "the parts never end"
        parts.append(ask(parts[-1][f"{listed}_next"]))
    return parts


def rest_pages(w, **query):
    def ask(after):
        params = {**query, **({} if after is None else {"after": after})}
        return answer(w.client.get("/api/projects/atlas/pages", headers=w.h, params=params))

    return ask


def mcp_overview(w, **arguments):
    def ask(after):
        given = {**arguments, **({} if after is None else {"after": after})}
        return answer(tool(w, "overview", project="atlas", **given))

    return ask


@pytest.mark.parametrize("way", ["REST", "MCP"])
def test_250_pages_come_100_at_a_time_in_slug_order(many, way):
    ask = rest_pages(many) if way == "REST" else mcp_overview(many)
    parts = walk(ask)
    assert [len(part["pages"]) for part in parts] == [100, 100, 50]
    assert [p["slug"] for part in parts for p in part["pages"]] == many.slugs  # in order, once
    assert parts[0]["pages_total"] == 250 and parts[1]["pages_total"] == 250
    if way == "MCP":
        assert all(part["notes"] == 257 for part in parts)  # every note, not the part's

    smaller = rest_pages(many, limit=30) if way == "REST" else mcp_overview(many, limit=30)
    parts = walk(smaller)
    assert [len(part["pages"]) for part in parts] == [30] * 8 + [10]
    assert [p["slug"] for part in parts for p in part["pages"]] == many.slugs

    larger = rest_pages(many, limit=500) if way == "REST" else mcp_overview(many, limit=500)
    assert len(larger(None)["pages"]) == 100  # at most 100, whatever is asked


def test_pages_added_between_two_askings_are_neither_repeated_nor_skipped(many):
    ask = rest_pages(many)
    first = ask(None)
    assert [p["slug"] for p in first["pages"]] == many.slugs[:100]
    for topic in ("aaa-first", "leaf-050a", "leaf-150a", "zzz-last"):  # before and after the cut
        r = many.client.post(
            "/api/projects/atlas/notes",
            headers=many.h,
            json={"text": f"Mark {topic}.", "topic": topic},
        )
        assert r.status_code == 201, r.text
    rest, after = [], first["pages_next"]
    while after is not None:
        part = ask(after)
        rest.append(part)
        after = part.get("pages_next")
        assert len(rest) < 10
    seen = [p["slug"] for p in first["pages"]] + [p["slug"] for part in rest for p in part["pages"]]
    assert len(seen) == len(set(seen)), "a page came twice"
    assert set(many.slugs) <= set(seen), "a page was skipped"
    assert {"leaf-150a", "zzz-last"} <= set(seen)  # after the cut: in the parts that follow


def test_250_pages_on_the_web_come_in_two_parts_linked(many):
    def page_links(page: str) -> list[str]:
        return [link for link in hrefs(page) if link.startswith("/b/atlas/p/")]

    def part_links(page: str) -> set[str]:
        return {link for link in hrefs(page) if link.startswith("/b/atlas?")}

    first = many.client.get("/b/atlas", headers=many.keyed)
    assert first.status_code == 200
    shown = page_links(first.text)
    assert len(shown) == 200 and len(set(shown)) == 200
    words = visible(first.text).split()
    assert "250" in words and "257" in words  # how many pages in all, and every note
    (onward,) = part_links(first.text)
    second = many.client.get(onward, headers=many.keyed)
    assert second.status_code == 200
    more = page_links(second.text)
    assert len(more) == 50
    assert sorted(shown + more) == [f"/b/atlas/p/{slug}" for slug in many.slugs]  # each once
    for link in part_links(second.text):  # a way back, if any, leads to what was shown
        assert set(page_links(many.client.get(link, headers=many.keyed).text)) <= set(shown)


@pytest.fixture
def sixty(tmp_path):
    """A page of one claim saved 60 times ("drift") and one of three notes saved 3 times
    ("calm"), every version kept."""
    db = tmp_path / "cairn.db"
    store = Store(db, clock=Clock())
    key = store.create_key("ops-desk")
    atlas = store.create_project("atlas")
    for i in range(3):
        note, _ = store.add_note(atlas.id, f"The calm gauge read {i} bar.", topic="calm")
        assert compile_note(store, note, provenance_gate).status == "saved"
    resave(store, atlas.id, "The drift gauge read 4 bar.", "drift", 60)
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        opened = client.post("/login", data={"key": key}, follow_redirects=False)
        keyed = {"cookie": f"cairn_session={opened.cookies['cairn_session']}"}
        client.cookies.clear()
        yield SimpleNamespace(store=store, client=client, h={"x-api-key": key}, keyed=keyed)
    store.close()


def test_60_versions_come_the_latest_50_first_then_back_in_time_over_rest_and_mcp(sixty):
    w = sixty

    def rest(before=None, **query):
        params = {**query, **({} if before is None else {"before": before})}
        return answer(w.client.get("/api/projects/atlas/pages/drift", headers=w.h, params=params))

    def mcp(before=None, **arguments):
        given = {**arguments, **({} if before is None else {"before": before})}
        return answer(tool(w, "page", project="atlas", slug="drift", **given))

    for ask in (rest, mcp):
        parts = walk(ask, "versions")
        assert [part["versions"] for part in parts] == [list(range(11, 61)), list(range(1, 11))]
        assert parts[0]["versions_total"] == 60
        assert all(part["version"] == 60 and len(part["claims"]) == 1 for part in parts)
        assert ask(before=31)["versions"] == list(range(1, 31))  # the ones numbered below
        assert ask(limit=20)["versions"] == list(range(41, 61))  # fewer of the latest
        latest = ask(limit=500)  # at most 50, whatever is asked
        assert latest["versions"] == list(range(11, 61)) and latest["versions_total"] == 60

    for calm in (
        answer(w.client.get("/api/projects/atlas/pages/calm", headers=w.h)),
        answer(tool(w, "page", project="atlas", slug="calm")),
    ):
        assert calm["versions"] == [1, 2, 3]
        assert not [name for name in calm if name.startswith("versions_")]


def test_60_versions_on_the_web_page_show_the_latest_50_and_a_link_to_the_earlier(sixty):
    w = sixty
    here = "/b/atlas/p/drift"

    def numbers(page: str) -> set[int]:
        return {int(n) for n in re.findall(r">\s*v(\d+)\s*<", page)}

    first = w.client.get(here, headers=w.keyed)
    assert first.status_code == 200
    shown = numbers(first.text)
    assert shown == set(range(11, 61))  # the latest 50, not version 1 to 50
    assert "60" in visible(first.text).split()  # how many in all
    one_version = re.compile(re.escape(here) + r"\?version=\d+")
    onward = {
        link
        for link in hrefs(first.text)
        if link.startswith(here + "?") and not one_version.fullmatch(link)
    }
    assert onward, "no link to the other versions"
    more = set()
    for link in onward:
        more |= numbers(w.client.get(link, headers=w.keyed).text)
    assert shown | more == set(range(1, 61))
    calm = w.client.get("/b/atlas/p/calm", headers=w.keyed).text
    assert numbers(calm) == {1, 2, 3}


# B: a page's history


def note_by(client, key: str, i: int, topic: str = "drift", size: int = 0):
    text = f"{topic.title()} check {i}: the gauge read {i} bar."
    if size:
        text += "\n\n" + " ".join(f"w{i}x{k:06d}" for k in range(size // 10))
    r = client.post(
        "/api/projects/atlas/notes", headers={"x-api-key": key}, json={"text": text, "topic": topic}
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "saved", r.json()
    return r.json()


def all_versions(client, key: str, slug: str = "drift") -> list[int]:
    """Every version of the page, by REST, part by part back in time, oldest first."""

    def ask(before):
        params = {} if before is None else {"before": before}
        r = client.get(
            f"/api/projects/atlas/pages/{slug}", headers={"x-api-key": key}, params=params
        )
        return answer(r)

    return [v for part in reversed(walk(ask, "versions")) for v in part["versions"]]


def prepared(db: Path) -> str:
    """A database with the project "atlas" and a key, made before `serve` opens it."""
    db.parent.mkdir(parents=True, exist_ok=True)
    store = Store(db, clock=Clock())
    try:
        store.create_project("atlas")
        return store.create_key("ops-desk")
    finally:
        store.close()


@pytest.fixture
def serve(monkeypatch, capsys):
    """`serve` with uvicorn replaced: `during(client, app)` runs inside the app's lifespan
    while serve holds its store and its lock. Returns the exit code, what serve said on
    stderr before it would listen, and the app (None when it was never run)."""

    def run_serve(db: Path, *argv: str, during=None) -> SimpleNamespace:
        seen = SimpleNamespace(code=None, said="", app=None)

        def run(app, **options):
            seen.app = app
            seen.said = capsys.readouterr().err
            if during is not None:
                with TestClient(app, base_url=BASE) as client:
                    during(client, app)

        monkeypatch.setattr(uvicorn, "run", run)
        capsys.readouterr()
        try:
            seen.code = main(["serve", "--db", str(db), "--public-url", BASE, "--no-tidy", *argv])
        except SystemExit as stopped:
            seen.code = stopped.code
        if seen.app is None:
            seen.said = capsys.readouterr().err
        return seen

    return run_serve


def test_serve_keeps_the_last_50_versions_of_each_page_by_default(tmp_path, serve):
    db = tmp_path / "cairn.db"
    key = prepared(db)
    found = {}

    def during(client, app):
        first = note_by(client, key, 0)
        for i in range(1, 60):
            note_by(client, key, i)
        found["versions"] = all_versions(client, key)
        page = answer(client.get("/api/projects/atlas/pages/drift", headers={"x-api-key": key}))
        found["latest"], found["claims"] = page["version"], page["claims"]
        claim = next(c for c in page["claims"] if "Drift check 0:" in c["text"])
        trace = client.get(
            f"/api/projects/atlas/pages/drift/claims/{claim['id']}/evidence",
            headers={"x-api-key": key},
        )
        found["cited"] = answer(trace)["passages"][0]["note_id"] == first["note_id"]
        h = {"x-api-key": key}
        found["gone"] = client.get("/api/projects/atlas/pages/drift?version=10", headers=h)
        found["kept"] = client.get("/api/projects/atlas/pages/drift?version=11", headers=h)

    ran = serve(db, during=during)
    assert ran.code == 0 and ran.app is not None
    assert found["versions"] == list(range(11, 61))  # the newest 50
    assert found["latest"] == 60 and len(found["claims"]) == 60  # the latest is whole
    assert found["cited"]  # a claim from the first note still cites it
    assert found["gone"].status_code == 404 and found["kept"].status_code == 200


@pytest.mark.parametrize(("option", "kept"), [("3", [3, 4, 5]), ("all", [1, 2, 3, 4, 5])])
def test_keep_versions_takes_a_number_or_all(tmp_path, serve, option, kept):
    db = tmp_path / "cairn.db"
    key = prepared(db)
    found = {}

    def during(client, app):
        for i in range(5):
            note_by(client, key, i)
        found["versions"] = all_versions(client, key)

    assert serve(db, "--keep-versions", option, during=during).code == 0
    assert found["versions"] == kept


@pytest.mark.parametrize("bad", ["0", "-2", "2.5", "some", ""])
def test_keep_versions_refuses_anything_else(tmp_path, serve, bad):
    db = tmp_path / "cairn.db"
    prepared(db)
    assert serve(db, "--keep-versions", "4").code == 0  # the option exists: a number runs
    ran = serve(db, "--keep-versions", bad)
    assert ran.code == 2 and ran.app is None


PLAN_FILE = """\
default: base
plans:
  base:
    label: Base
{extra}"""


@pytest.mark.parametrize(("extra", "saves", "kept"), [
    ("    versions_per_page: 2\n", 4, [3, 4]),
    ("", 52, list(range(1, 53))),  # past serve's own 50: a plan without a bound keeps all
])  # fmt: skip
def test_with_plans_the_plan_decides_how_many_versions_are_kept(
    tmp_path, serve, extra, saves, kept
):
    db = tmp_path / "cairn.db"
    key = prepared(db)
    plans = tmp_path / "plans.yaml"
    plans.write_text(PLAN_FILE.format(extra=extra), encoding="utf-8")
    found = {}

    def during(client, app):
        for i in range(saves):
            note_by(client, key, i)
        found["versions"] = all_versions(client, key)

    assert serve(db, "--plans", str(plans), during=during).code == 0
    assert found["versions"] == kept


@pytest.mark.parametrize("option", ["5", "50", "all"])
def test_with_plans_keep_versions_is_refused_in_one_line(tmp_path, serve, option):
    db = tmp_path / "cairn.db"
    prepared(db)
    plans = tmp_path / "plans.yaml"
    plans.write_text(PLAN_FILE.format(extra="    versions_per_page: 2\n"), encoding="utf-8")
    before = dump(db)
    ran = serve(db, "--plans", str(plans), "--keep-versions", option)
    assert ran.code == 2 and ran.app is None
    assert len([line for line in ran.said.splitlines() if line.strip()]) == 1, ran.said
    assert "Traceback" not in ran.said
    assert dump(db) == before


def fail_the_commit_after_a_prune(con: sqlite3.Connection) -> None:
    """From now on, the COMMIT of a transaction in which versions of a page were deleted is
    interrupted, so the transaction is rolled back. Other transactions go through."""
    state = {"pruned": False, "running": ""}

    def traced(statement: str) -> None:
        text = statement.lstrip()
        if text.startswith("--"):
            return
        state["running"] = text.split(None, 1)[0].upper() if text else ""
        if re.match(r'(?is)delete\s+from\s+"?page_versions\b', text):
            state["pruned"] = True
        elif state["running"] in ("BEGIN", "ROLLBACK"):
            state["pruned"] = False

    def progress() -> int:
        return 1 if state["pruned"] and state["running"] == "COMMIT" else 0

    con.set_trace_callback(traced)
    con.set_progress_handler(progress, 1)


def test_a_save_removes_old_versions_in_its_own_transaction(tmp_path):
    db = tmp_path / "cairn.db"
    store = Store(db, clock=Clock())
    key = store.create_key("ops-desk")
    atlas = store.create_project("atlas")
    app = create_app(store, public_url=BASE, keep_versions=3)
    with TestClient(app, base_url=BASE, raise_server_exceptions=False) as client:
        for i in range(3):
            note_by(client, key, i)
        assert store.page_versions(atlas.id, "drift") == [1, 2, 3]
        fail_the_commit_after_a_prune(store._con)
        try:
            r = client.post(
                "/api/projects/atlas/notes", headers={"x-api-key": key},
                json={"text": "Drift check 3: the gauge read 3 bar.", "topic": "drift"},
            )  # fmt: skip
        finally:
            store._con.set_progress_handler(None, 1)
            store._con.set_trace_callback(None)
        assert r.status_code >= 500
        assert store.page_versions(atlas.id, "drift") == [1, 2, 3]  # nothing removed
        assert store.get_page(atlas.id, "drift").version == 3
        note_by(client, key, 4)
        assert store.page_versions(atlas.id, "drift") == [2, 3, 4]
    store.close()


def resave(store: Store, project_id: str, text: str, slug: str, times: int) -> None:
    """One note compiled into page `slug` (its first version, which files the note), then the
    page saved again as it is until it has `times` versions."""
    note, _ = store.add_note(project_id, text, topic=slug)
    assert compile_note(store, note, provenance_gate).status == "saved"
    page = store.get_page(project_id, slug)
    for _ in range(times - 1):
        store.save_page(project_id, slug, page.title, page.body, page.claims, page.metrics)


def build_long_history(db: Path, saves: dict[str, int]) -> str:
    """Pages of the project "atlas" saved as often as `saves` says, every version kept; a key."""
    db.parent.mkdir(parents=True, exist_ok=True)
    store = Store(db, clock=Clock())
    try:
        key = store.create_key("ops-desk")
        atlas = store.create_project("atlas")
        for slug, times in saves.items():
            resave(store, atlas.id, f"The {slug} gate opens at dawn.", slug, times)
        return key
    finally:
        store.close()


def versions_in(db: Path, slugs) -> dict[str, list[int]]:
    store = Store(db, clock=Clock())
    try:
        atlas = store.get_project("atlas")
        return {slug: store.page_versions(atlas.id, slug) for slug in slugs}
    finally:
        store.close()


HISTORY = {"north": 69, "south": 68, "east": 67, "west": 10}


def test_serve_says_at_start_which_pages_hold_more_and_removes_nothing_then(tmp_path, serve):
    db = tmp_path / "cairn.db"
    key = build_long_history(db, HISTORY)
    before = versions_in(db, HISTORY)
    found = {}

    def during(client, app):
        found["at start"] = {slug: all_versions(client, key, slug) for slug in HISTORY}
        h = {"x-api-key": key}
        for slug in HISTORY:  # reading removes nothing
            assert client.get(f"/api/projects/atlas/pages/{slug}", headers=h).status_code == 200
            asked = tool(SimpleNamespace(client=client, h=h), "page", project="atlas", slug=slug)
            assert asked.status_code == 200
        found["after reading"] = {slug: all_versions(client, key, slug) for slug in HISTORY}
        note_by(client, key, 1, topic="north")  # saved: its older versions go now
        found["after a save"] = {slug: all_versions(client, key, slug) for slug in HISTORY}

    ran = serve(db, during=during)
    assert ran.code == 0
    (line,) = [line for line in ran.said.splitlines() if "--keep-versions all" in line]
    told = line.replace(str(db), "")
    assert re.search(r"\b3\b", told) and re.search(r"\b50\b", told), line
    assert found["at start"] == before and found["after reading"] == before
    assert found["after a save"]["north"] == list(range(21, 71))  # the newest 50
    for slug in ("south", "east", "west"):
        assert found["after a save"][slug] == before[slug]
    assert versions_in(db, HISTORY) == found["after a save"]

    quiet = serve(db, "--keep-versions", "all", during=lambda client, app: None)
    assert quiet.code == 0 and "keep-versions" not in quiet.said
    small = tmp_path / "small" / "cairn.db"
    build_long_history(small, {"west": 10})
    calm = serve(small, during=lambda client, app: None)
    assert calm.code == 0 and "keep-versions" not in calm.said


def kept_of(keep: int) -> dict[str, list[int]]:
    """The versions of HISTORY's pages left when each page keeps its newest `keep`."""
    return {slug: list(range(max(1, n - keep + 1), n + 1)) for slug, n in HISTORY.items()}


def compact(db: Path, capsys) -> tuple[int, str]:
    """`compact` run on the database: its exit code and all it said, without the paths."""
    capsys.readouterr()
    code = main(["compact", "--db", str(db)])
    said = capsys.readouterr()
    return code, (said.out + said.err).replace(str(db), "").replace(str(db.parent), "")


def served(serve, db: Path, *argv: str) -> None:
    """`serve` run to the end with its app started and stopped: whatever it records is
    recorded, before or after it listens."""
    ran = serve(db, *argv, during=lambda client, app: None)
    assert ran.code == 0 and ran.app is not None, ran.said


@pytest.mark.parametrize(("argv", "keep", "removed"), [
    ((), 50, 54),  # 19 + 18 + 17
    (("--keep-versions", "20"), 20, 144),  # 49 + 48 + 47
    (("--keep-versions", "all"), None, 0),
])  # fmt: skip
def test_compact_keeps_what_the_last_serve_was_told_to_keep(
    tmp_path, serve, capsys, argv, keep, removed
):
    db = tmp_path / "cairn.db"
    build_long_history(db, HISTORY)
    before = versions_in(db, HISTORY)
    served(serve, db, *argv)
    assert versions_in(db, HISTORY) == before  # serve removes nothing at start
    code, said = compact(db, capsys)
    assert code == 0, said
    assert versions_in(db, HISTORY) == (before if keep is None else kept_of(keep))
    assert re.search(rf"\b{removed}\b", said), said  # how many it removed


def test_compact_on_a_database_no_serve_recorded_removes_nothing_and_says_why(tmp_path, capsys):
    db = tmp_path / "cairn.db"
    build_long_history(db, HISTORY)
    before = versions_in(db, HISTORY)
    _, said = compact(db, capsys)  # its exit code is not the contract's
    assert "Traceback" not in said
    assert versions_in(db, HISTORY) == before  # no default of its own
    assert "serve" in said.casefold(), said  # why: no serve has recorded what to keep


def test_the_record_follows_the_last_serve(tmp_path, serve, capsys):
    db = tmp_path / "cairn.db"
    build_long_history(db, HISTORY)
    before = versions_in(db, HISTORY)
    served(serve, db, "--keep-versions", "20")
    served(serve, db, "--keep-versions", "all")
    code, said = compact(db, capsys)
    assert code == 0 and versions_in(db, HISTORY) == before, said  # all, as the last one
    assert re.search(r"\b0\b", said), said
    served(serve, db)  # then the default
    code, said = compact(db, capsys)
    assert code == 0 and versions_in(db, HISTORY) == kept_of(50), said


TWO_PLANS = """\
default: roomy
plans:
  roomy:
    label: Roomy
  tight:
    label: Tight
    versions_per_page: 3
"""


def test_with_plans_compact_keeps_what_each_workspaces_plan_keeps(tmp_path, serve, capsys):
    """The default workspace on a plan that keeps every version, Ada's on one that keeps 3."""
    db = tmp_path / "cairn.db"
    build_long_history(db, HISTORY)
    store = Store(db, clock=Clock())
    try:
        ada = store.sign_in(
            "sso", "sub-ada", "ada@example.test", "Ada Quill", issuer=ISSUER, join="personal",
            ttl_seconds=86_400,
        )  # fmt: skip
        theirs = store.for_account(ada.account_id)
        beacon = theirs.create_project("beacon")
        resave(theirs, beacon.id, "The beacon lamp turns at dusk.", "lamp", 12)
        store.set_plan(ada.account_id, "tight")
    finally:
        store.close()

    def lamp() -> list[int]:
        reader = Store(db, clock=Clock())
        try:
            return reader.for_account(ada.account_id).page_versions(beacon.id, "lamp")
        finally:
            reader.close()

    before = versions_in(db, HISTORY)
    assert lamp() == list(range(1, 13))
    plans = tmp_path / "plans.yaml"
    plans.write_text(TWO_PLANS, encoding="utf-8")
    served(serve, db, "--plans", str(plans))
    code, said = compact(db, capsys)
    assert code == 0, said
    assert versions_in(db, HISTORY) == before  # the roomy plan keeps every version
    assert lamp() == [10, 11, 12]  # the tight plan keeps 3
    assert re.search(r"\b9\b", said), said


def test_the_reviews_case(tmp_path, serve):
    """Thirteen large notes on one page, then small notes: the history stops growing by the
    page's size with each save once the bound is reached. The review's own notes were 72 KB
    each; since a page holds at most 400,000 characters of claims (cairn.store.PAGE_CHARS_MAX,
    another task's bound for the same review) the sixth of those is refused, so the notes
    here are 24 KB: thirteen of them fit, and the page is still large beside a small save."""
    db = tmp_path / "cairn.db"
    key = prepared(db)
    found = {}

    def dense(client, app):
        for i in range(13):
            note_by(client, key, i, size=24_000)
        found["dense"] = all_versions(client, key)
        for i in range(13, 60):
            note_by(client, key, i)
        found["bounded"] = all_versions(client, key)

    assert serve(db, during=dense).code == 0
    assert found["dense"] == list(range(1, 14))  # 13 versions: under the bound
    assert found["bounded"] == list(range(11, 61))  # 60 saves: the newest 50
    assert main(["compact", "--db", str(db)]) == 0
    compacted = db.stat().st_size

    def more(client, app):
        for i in range(60, 65):
            note_by(client, key, i)

    assert serve(db, during=more).code == 0
    assert main(["compact", "--db", str(db)]) == 0
    # Each version holds the whole page (13 x 24 KB and the small claims), so keeping every
    # version grows the database by about one page copy a save. Under the bound, a save adds
    # a version and removes one: what is left is the small claims' own growth in each kept
    # version and the search index's segments.
    page_copy = 13 * 24_000
    per_save = (db.stat().st_size - compacted) / 5
    assert per_save < page_copy / 2, f"a small save grew the database by {per_save:,.0f} bytes"


def test_the_history_on_the_web_says_it_shows_the_last_n(tmp_path):
    db = tmp_path / "cairn.db"
    store = Store(db, clock=Clock())
    key = store.create_key("ops-desk")
    store.create_project("atlas")
    app = create_app(store, public_url=BASE, keep_versions=3)
    with TestClient(app, base_url=BASE) as client:
        for i in range(5):
            note_by(client, key, i)
        for i in range(2):
            note_by(client, key, i, topic="calm")
        opened = client.post("/login", data={"key": key}, follow_redirects=False)
        keyed = {"cookie": f"cairn_session={opened.cookies['cairn_session']}"}
        client.cookies.clear()
        trimmed = visible(client.get("/b/atlas/p/drift", headers=keyed).text)
        whole = visible(client.get("/b/atlas/p/calm", headers=keyed).text)
        assert re.search(r"(?i)\blast 3\b", trimmed), trimmed
        assert not re.search(r"(?i)\blast \d+\b", whole), whole
        h = {"x-api-key": key}
        listed = answer(client.get("/api/projects/atlas/pages/drift", headers=h))
        called = answer(
            tool(SimpleNamespace(client=client, h=h), "page", project="atlas", slug="drift")
        )
        assert listed["versions"] == called["versions"] == [3, 4, 5]
    store.close()


# C: the numbers


FREE = 987_654_321_000


class Disk:
    """shutil.disk_usage, replaced: the free space it says (`free`, or what `free(db)` gives),
    where it was asked about, and whether it was asked on a thread that runs an event loop."""

    def __init__(self, free=FREE) -> None:
        self.free = free
        self.asked: list[Path] = []
        self.on_loop: list[bool] = []
        self.threads: list[int] = []
        self.fail: BaseException | None = None

    def __call__(self, path):
        self.asked.append(Path(path))
        self.threads.append(threading.get_ident())
        try:
            asyncio.get_running_loop()
            self.on_loop.append(True)
        except RuntimeError:
            self.on_loop.append(False)
        if self.fail is not None:
            failure, self.fail = self.fail, None
            raise failure
        free = self.free() if callable(self.free) else self.free
        return Usage(10**13, 10**12, free)


@pytest.fixture
def disk(monkeypatch):
    import shutil

    replaced = Disk()
    monkeypatch.setattr(shutil, "disk_usage", replaced)
    return replaced


def two_kinds() -> list[str]:
    """Two kinds of source the software ships, found through the store: the first two that a
    scratch store adds, off, with a key read from an environment variable."""
    probe, found = Store(":memory:", clock=Clock()), []
    try:
        for n, kind in enumerate(SOURCE_KINDS):
            try:
                added = probe.add_source(
                    f"probe-{n}", kind, {"key_env": "QZ_FEED_KEY", "projects": []}
                )
            except ValueError:
                continue
            if not added.enabled:
                found.append(kind)
    finally:
        probe.close()
    assert len(found) >= 2, found
    return found[:2]


SEEDED = (  # what the numbers must never show: each seeded somewhere below
    "zephyrqz", "quokkaqz", "tidetableqz", "orchardqz", "wombatqz", "numbatqz", "bilbyqz",
    "dingoqz", "emuqz", "pademelonqz", "wallabyqz", "Brook Quollqz", "brook.quollqz",
    "example.test", "reasonqz", "SeedSecretQz", "waitingqz",
)  # fmt: skip


def failed_run(view: Store, name: str, reason: str) -> None:
    claim = view.claim_source(name)
    assert claim is not None
    view.fail_source_run(claim, reason)


def counts_of(view: Store, waiting: int) -> dict:
    """A workspace's numbers, from its own views (a view acting for no person sees all), with
    the notes the test left waiting."""
    projects = view.list_projects()
    pages = [(b.id, p.slug) for b in projects for p in view.list_pages(b.id)]
    notes = [c for b in projects for c in view.list_notes(b.id)]
    sources: dict[str, dict[str, int]] = {}
    for source in view.list_sources():
        tally = sources.setdefault(source.kind, {"on": 0, "failed": 0})
        tally["on"] += int(source.enabled)
        tally["failed"] += int(source.last_error is not None)
    return {
        "projects": len(projects),
        "notes": len(notes),
        "pages": len(pages),
        "page_versions": sum(len(view.page_versions(b, slug)) for b, slug in pages),
        "waiting": waiting,
        "keys": len(view.list_keys()),
        "agents": len(view.list_grants()),
        "newest_note": max((c.created_at for c in notes), default=None),
        "sources": sources,
    }


def install_of(per: dict[str, dict]) -> dict:
    summed = ("projects", "notes", "pages", "page_versions", "waiting", "keys", "agents")
    total = {name: sum(c[name] for c in per.values()) for name in summed}
    total["newest_note"] = max(
        (c["newest_note"] for c in per.values() if c["newest_note"]), default=None
    )
    sources: dict[str, dict[str, int]] = {}
    for c in per.values():
        for kind, tally in c["sources"].items():
            mine = sources.setdefault(kind, {"on": 0, "failed": 0})
            mine["on"] += tally["on"]
            mine["failed"] += tally["failed"]
    total["sources"] = sources
    return total


def stocked(db: Path) -> SimpleNamespace:
    """Two workspaces with projects, notes, pages of several versions, notes waiting to be filed
    (2 and 1), keys, a connected agent and sources, one failed and one never run, with a
    distinctive word in every text, title,
    name, address, label and reason (SEEDED). Their numbers are read before the store
    closes."""
    db.parent.mkdir(parents=True, exist_ok=True)
    store = Store(db, clock=Clock())
    first, second = two_kinds()
    harbor = store.create_project("harbor-zephyrqz", "Plans for the zephyrqz dock")
    orchard = store.create_project("orchardqz")
    for text, title, topic, project in (
        ("# Tide quokkaqz\n\nThe quokkaqz tide turns at noon.", "Tidetableqz", "quokkaqz", harbor),
        ("The quokkaqz tide turns again at midnight.", None, "quokkaqz", harbor),
        ("Pear rows face the orchardqz wall.", None, None, orchard),
    ):
        note, _ = store.add_note(project.id, text, title=title, topic=topic)
        compile_note(store, note, provenance_gate)
    store.create_key("wombatqz-desk")
    store.create_key("wombatqz-runner")
    client = store.register_client("numbatqz helper", ["https://agent.example.test/back"])
    code = store.create_code(client.id, "https://agent.example.test/back", "c" * 43)
    assert store.redeem_code(code, client.id) is not None
    store.add_source("bilbyqz-feed", first, {"key_env": "QZ_FEED_KEY", "projects": []})
    store.enable_source("bilbyqz-feed", ["orchardqz"])
    failed_run(store, "bilbyqz-feed", "refused by reasonqz.example.test: SeedSecretQz")
    store.add_source("dingoqz-feed", first, {"key_env": "QZ_FEED_KEY", "projects": []})
    store.add_source("emuqz-feed", second, {"key_env": "QZ_FEED_KEY", "projects": []})
    store.enable_source("emuqz-feed", ["harbor-zephyrqz"])
    failed_run(store, "emuqz-feed", "reasonqz: gone")
    brook = store.sign_in(
        "sso", "sub-brook", "brook.quollqz@example.test", "Brook Quollqz", issuer=ISSUER,
        join="personal", ttl_seconds=90 * 86_400,
    )  # fmt: skip
    theirs = store.for_account(brook.account_id)
    garden = theirs.create_project("pademelonqz")
    for i in range(3):
        note, _ = theirs.add_note(garden.id, f"Bed {i} of the pademelonqz garden is dug.")
        compile_note(theirs, note, provenance_gate)
    theirs.create_key("wallabyqz-key")
    for text, view, project in (  # stored and not compiled, as when serve stops between the two
        ("The quokkaqz tide waits at dawn.", store, harbor),
        ("Pear rows wait by the orchardqz gate.", store, orchard),
        ("Bed 9 of the pademelonqz garden waits.", theirs, garden),
    ):
        view.add_note(project.id, text, topic="waitingqz")
    per = {store.account_id: counts_of(store, 2), brook.account_id: counts_of(theirs, 1)}
    store.close()
    return SimpleNamespace(per=per, install=install_of(per), accounts=list(per))


def stats(*argv: str, capsys) -> tuple[int, str, str]:
    capsys.readouterr()
    code = main(["stats", *argv])
    said = capsys.readouterr()
    return code, said.out, said.err


def on_disk(db: Path) -> tuple[bytes, int]:
    return db.read_bytes(), db.stat().st_mtime_ns


def test_stats_prints_every_number_for_the_install_and_each_workspace(tmp_path, disk, capsys):
    db = tmp_path / "data" / "cairn.db"
    world = stocked(db)
    before = on_disk(db)
    code, out, err = stats("--db", str(db), "--json", capsys=capsys)
    assert code == 0, err
    assert on_disk(db) == before  # read-only: not a byte, not the time
    numbers = json.loads(out)
    wal = db.with_name(db.name + "-wal")
    assert numbers["schema"] == SCHEMA_VERSION
    assert numbers["db_bytes"] == len(before[0])
    assert numbers["wal_bytes"] == (wal.stat().st_size if wal.exists() else 0)
    assert numbers["free_bytes"] == FREE and numbers["low_disk"] is False
    assert disk.asked and all(
        p.resolve() in (db.resolve(), db.parent.resolve()) for p in disk.asked
    )
    for name, value in world.install.items():
        if name == "sources":
            for kind, tally in value.items():
                holds(numbers["sources"][kind], tally, f"sources.{kind}")
        else:
            assert numbers[name] == value, name
    assert sum(t["failed"] for t in world.install["sources"].values()) == 2  # the seed's runs
    for account, expected in world.per.items():
        mine = numbers["workspaces"][account]
        for name, value in expected.items():
            if name == "sources":
                for kind, tally in value.items():
                    holds(mine["sources"][kind], tally, f"{account}.sources.{kind}")
            else:
                assert mine[name] == value, (account, name)
    assert world.per[world.accounts[1]]["notes"] == 4 and world.install["agents"] == 1
    assert [world.per[a]["waiting"] for a in world.accounts] == [2, 1]  # left waiting above
    assert numbers["waiting"] == 3


def test_stats_says_low_disk_under_twice_the_databases_size(tmp_path, disk, capsys):
    db = tmp_path / "cairn.db"
    stocked(db)
    size = db.stat().st_size
    for free, low in ((2 * size - 1, True), (2 * size, False), (0, True)):
        disk.free = free
        code, out, err = stats("--db", str(db), "--json", capsys=capsys)
        assert code == 0, err
        numbers = json.loads(out)
        assert (numbers["free_bytes"], numbers["low_disk"]) == (free, low)


def test_stats_shows_no_text_title_name_address_label_or_reason(tmp_path, disk, capsys):
    db = tmp_path / "cairn.db"
    world = stocked(db)
    for argv in ((), ("--json",)):
        code, out, err = stats("--db", str(db), *argv, capsys=capsys)
        assert code == 0 and out.strip(), err
        said = (out + err).casefold()
        for seeded in SEEDED:
            assert seeded.casefold() not in said, (argv, seeded)
        if not argv:  # the plain output shows the numbers too
            assert str(world.install["notes"]) in out


def test_stats_runs_beside_a_running_serve(tmp_path, serve, disk, capsys):
    db = tmp_path / "cairn.db"
    key = prepared(db)
    found = {}

    def during(client, app):
        note_by(client, key, 1)
        note_by(client, key, 2)
        capsys.readouterr()
        found["code"] = main(["stats", "--db", str(db), "--json"])
        found["out"] = capsys.readouterr().out

    assert serve(db, during=during).code == 0
    assert found["code"] == 0
    numbers = json.loads(found["out"])
    assert numbers["notes"] == 2 and numbers["pages"] == 1 and numbers["page_versions"] == 2


V0_SCHEMA = """
CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE api_keys (digest TEXT PRIMARY KEY, label TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX api_keys_by_label ON api_keys (label);
CREATE TABLE sessions (digest TEXT PRIMARY KEY, expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL);
CREATE TABLE notes (seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL, text TEXT NOT NULL, text_sha256 TEXT NOT NULL, title TEXT NOT NULL,
    topic TEXT, label TEXT, source_ref TEXT, via TEXT, created_at TEXT NOT NULL);
CREATE INDEX notes_by_text ON notes (project_id, text_sha256);
CREATE TABLE spans (id TEXT PRIMARY KEY, note_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
    kind TEXT NOT NULL, heading_path TEXT NOT NULL, start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL, text TEXT NOT NULL, UNIQUE (note_id, ordinal));
CREATE TABLE page_versions (project_id TEXT NOT NULL, slug TEXT NOT NULL,
    version INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, claims TEXT NOT NULL,
    metrics TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (project_id, slug, version));
CREATE VIRTUAL TABLE claim_search USING fts5 (
    project_id UNINDEXED, slug UNINDEXED, claim_id UNINDEXED, span_ids UNINDEXED, title, text);
INSERT INTO projects VALUES ('br_aaaaaaaaaaaa', 'harbor', '', '2099-01-01T00:00:00.000000+00:00');
"""  # the oldest schema the tests keep: cairn v0's, which shipped unstamped (version 1)


def test_stats_refuses_an_older_database_and_says_to_start_the_server_once(tmp_path, disk, capsys):
    older = tmp_path / "older.db"
    con = sqlite3.connect(older)
    con.executescript(V0_SCHEMA)
    con.close()
    before, files = on_disk(older), sorted(tmp_path.iterdir())
    for argv in ((), ("--json",)):
        code, out, err = stats("--db", str(older), *argv, capsys=capsys)
        assert code == 1 and out == "", (out, err)
        assert "Traceback" not in err
        (line,) = err.splitlines()  # exactly one line
        said = line.casefold()
        assert "start" in said and "serve" in said, line  # start the server once
    assert on_disk(older) == before, "an older database was written"
    assert sorted(tmp_path.iterdir()) == files, "a file was made or removed beside it"


AS_ROOT = os.geteuid() == 0
needs_modes = pytest.mark.skipif(
    AS_ROOT, reason="root reads and writes a file or folder whatever its mode says"
)
NOT_A_DATABASE = b"field notes, not a database: the dingoqz word stays here\n" * 64


def missing(data: Path) -> tuple[Path, list[Path]]:
    return data / "none" / "cairn.db", []


def not_a_database(data: Path) -> tuple[Path, list[Path]]:
    db = data / "cairn.db"
    db.write_bytes(NOT_A_DATABASE)
    return db, []


def unreadable(data: Path) -> tuple[Path, list[Path]]:
    db = data / "cairn.db"
    Store(db, clock=Clock()).close()
    os.chmod(db, 0)
    return db, [db]


def in_a_read_only_folder(data: Path) -> tuple[Path, list[Path]]:
    db = data / "cairn.db"
    Store(db, clock=Clock()).close()
    os.chmod(data, 0o555)
    return db, [data]


def a_folder(data: Path) -> tuple[Path, list[Path]]:
    db = data / "cairn.db"
    db.mkdir()
    return db, []


def newer(data: Path) -> tuple[Path, list[Path]]:
    db = data / "cairn.db"
    Store(db, clock=Clock()).close()
    con = sqlite3.connect(db)
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    # a step this cairn may not run on: the floor goes up with it
    con.execute("UPDATE oldest_build SET version = ?", (SCHEMA_VERSION + 1,))
    con.commit()
    con.close()
    return db, []


def tree(root: Path) -> dict[str, bytes | None]:
    """Every path under root, with a file's bytes (None for a folder)."""
    return {
        str(path.relative_to(root)): None if path.is_dir() else path.read_bytes()
        for path in sorted(root.rglob("*"))
    }


@pytest.mark.parametrize("case", [
    missing, not_a_database, pytest.param(unreadable, marks=needs_modes), a_folder, newer,
])  # fmt: skip
def test_stats_refuses_as_the_other_commands_do(tmp_path, disk, capsys, case):
    data = tmp_path / "data"
    data.mkdir()
    db, locked = case(data)
    modes = {path: stat.S_IMODE(path.lstat().st_mode) for path in locked}

    def unlock() -> None:
        for path in locked:
            os.chmod(path, 0o755 if path.is_dir() else 0o644)

    unlock()  # read the tree as it was, then lock it again
    before = tree(tmp_path)
    for path, mode in modes.items():
        os.chmod(path, mode)
    said = {}
    try:
        for command in ("stats", "key-list"):
            capsys.readouterr()
            code = main([command, "--db", str(db)])
            out, err = capsys.readouterr()
            said[command] = (code, out, err)
            assert {path: stat.S_IMODE(path.lstat().st_mode) for path in locked} == modes
    finally:
        unlock()
    code, out, err = said["stats"]
    assert code == 1 and out == "", (out, err)
    assert "Traceback" not in err
    (line,) = err.splitlines()  # exactly one line
    assert err == line + "\n"
    assert said["key-list"] == (1, "", err)  # the line the other commands print
    assert tree(tmp_path) == before, "a file was written, made or removed"
    assert "dingoqz" not in line and "field notes" not in line


@needs_modes
def test_stats_in_a_folder_it_may_not_write_reads_or_refuses_and_writes_nothing(
    tmp_path, disk, capsys
):
    """A command that only reads may read a database on a volume it cannot write (a mounted
    backup); whether it can is SQLite's to say, and the implementer's to try. Either answer
    is right here: the numbers, or the one line the other commands print. What is pinned is
    that nothing is written, made or removed, no mode changes, and no traceback."""
    data = tmp_path / "data"
    data.mkdir()
    db, locked = in_a_read_only_folder(data)
    modes = {path: stat.S_IMODE(path.lstat().st_mode) for path in locked}
    os.chmod(data, 0o755)
    before = tree(tmp_path)
    os.chmod(data, modes[data])
    try:
        capsys.readouterr()
        code = main(["stats", "--db", str(db)])
        out, err = capsys.readouterr()
        assert {path: stat.S_IMODE(path.lstat().st_mode) for path in locked} == modes
    finally:
        os.chmod(data, 0o755)
    assert "Traceback" not in out + err
    assert tree(tmp_path) == before, "a file was written, made or removed"
    if code == 0:
        assert out.strip() and err == "", (out, err)
    else:
        assert code == 1 and out == "", (out, err)
        (line,) = err.splitlines()  # exactly one line
        assert err == line + "\n"


# C: the line in the log


class Ticks:
    """The stats loop's wait, injected: each call records the seconds asked and the thread it
    runs on, says it has come (`reached`), and returns when the test moves on (`tick`)."""

    def __init__(self) -> None:
        self.asked: list[float] = []
        self.loop_threads: set[int] = set()
        self._came = threading.Semaphore(0)
        self._waits: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = []
        self._lock = threading.Lock()

    async def __call__(self, seconds: float) -> None:
        self.asked.append(seconds)
        self.loop_threads.add(threading.get_ident())
        event = asyncio.Event()
        with self._lock:
            self._waits.append((asyncio.get_running_loop(), event))
        self._came.release()
        await event.wait()

    def reached(self) -> None:
        assert self._came.acquire(timeout=60), "the stats loop never came to its wait"

    def tick(self) -> None:
        with self._lock:
            loop, event = self._waits.pop(0)
        loop.call_soon_threadsafe(event.set)


@pytest.fixture
def records(monkeypatch):
    """Every record at INFO and above that any logger hands to its handlers while the test
    runs, whatever the logger's name and wherever it propagates: the root logger at INFO,
    so a logger with no level of its own lets INFO through, and Logger.callHandlers
    watched."""
    kept: list[logging.LogRecord] = []
    original = logging.Logger.callHandlers

    def call_handlers(logger: logging.Logger, record: logging.LogRecord) -> None:
        if record.levelno >= logging.INFO:
            kept.append(record)
        original(logger, record)

    monkeypatch.setattr(logging.Logger, "callHandlers", call_handlers)
    root = logging.getLogger()
    level = root.level
    root.setLevel(logging.INFO)
    try:
        yield kept
    finally:
        root.setLevel(level)


def stats_lines(kept: list[logging.LogRecord]) -> list[SimpleNamespace]:
    """The records that are a stats line: a word, a space and a JSON object with `requests`."""
    found = []
    for record in kept:
        message = record.getMessage()
        word, _, rest = message.partition(" ")
        try:
            numbers = json.loads(rest)
        except ValueError:
            continue
        if isinstance(numbers, dict) and "requests" in numbers:
            found.append(SimpleNamespace(level=record.levelno, word=word, numbers=numbers,
                                         message=message, logger=record.name))  # fmt: skip
    return found


def on_a_logger_of_its_own(lines: list[SimpleNamespace]) -> str:
    """The one logger every stats line came on, which is not the access lines' logger."""
    (name,) = {line.logger for line in lines}
    assert name != "cairn.access", "the line is on the access logger"
    return name


def ticking(tmp_path, *, plans=None, every=15) -> SimpleNamespace:
    db = tmp_path / "cairn.db"
    store = Store(db, clock=Clock())
    key = store.create_key("ops-desk")
    atlas = store.create_project("atlas")
    for i in range(2):
        note, _ = store.add_note(atlas.id, f"Pump {i} runs at noon.", topic="pumps")
        compile_note(store, note, provenance_gate)
    store.add_note(atlas.id, "Pump 3 waits for its turn.", topic="pumps")  # left waiting
    ada = store.sign_in(
        "sso", "sub-ada", "ada@example.test", "Ada Quill", issuer=ISSUER, join="personal",
        ttl_seconds=86_400,
    )  # fmt: skip
    theirs = store.for_account(ada.account_id)
    note, _ = theirs.add_note(theirs.create_project("beacon").id, "The lamp turns at dusk.")
    compile_note(theirs, note, provenance_gate)
    ticks = Ticks()
    app = create_app(
        store, public_url=BASE, plans=plans, clock=lambda: START, stats_every=every,
        stats_wait=ticks,
    )  # fmt: skip
    return SimpleNamespace(
        db=db, store=store, key=key, ticks=ticks, app=app,
        accounts=[store.account_id, ada.account_id],
    )  # fmt: skip


def test_serve_logs_the_numbers_at_start_and_after_each_interval(tmp_path, disk, records):
    plans = Plans({"base": Plan("Base", requests_per_minute=1)}, default="base")
    w = ticking(tmp_path, plans=plans)
    with TestClient(w.app, base_url=BASE) as client:
        w.ticks.reached()  # the line at start is out
        lines = stats_lines(records)
        assert len(lines) == 1
        start = lines[0]
        assert start.level == logging.INFO and " " not in start.word and start.word
        assert start.numbers["requests"] == {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "429": 0}
        assert start.numbers["schema"] == SCHEMA_VERSION and start.numbers["notes"] == 4
        assert start.numbers["projects"] == 2 and start.numbers["page_versions"] == 3
        assert start.numbers["waiting"] == 1  # in total, the one left waiting
        assert "workspaces" not in start.numbers  # the install's totals only
        assert not [a for a in w.accounts if a in start.message], start.message
        assert start.numbers["free_bytes"] == FREE and start.numbers["low_disk"] is False
        assert start.numbers["db_bytes"] == w.db.stat().st_size

        h = {"x-api-key": w.key}
        assert client.get("/api/projects", headers=h).status_code == 200
        assert client.get("/api/projects", headers=h).status_code == 429  # the plan's rate
        assert client.get("/api/projects").status_code == 401
        assert client.get("/login").status_code == 200
        assert client.get("/", follow_redirects=False).status_code in (302, 303, 307)
        w.ticks.tick()
        w.ticks.reached()
        w.ticks.tick()
        w.ticks.reached()
    lines = stats_lines(records)
    assert len(lines) == 3 and {line.word for line in lines} == {start.word}
    on_a_logger_of_its_own(lines)
    assert lines[1].numbers["requests"] == {"2xx": 2, "3xx": 1, "4xx": 2, "5xx": 0, "429": 1}
    assert lines[2].numbers["requests"] == {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "429": 0}
    assert w.ticks.asked[:2] == [15 * 60, 15 * 60]  # every 15 minutes
    assert not any(disk.on_loop) and not set(disk.threads) & w.ticks.loop_threads
    w.store.close()


def test_the_numbers_are_gathered_off_the_event_loop(tmp_path, disk, records):
    w = ticking(tmp_path, every=5)
    with TestClient(w.app, base_url=BASE):
        w.ticks.reached()
        w.ticks.tick()
        w.ticks.reached()
    assert len(disk.on_loop) >= 2 and not any(disk.on_loop)
    assert not set(disk.threads) & w.ticks.loop_threads
    assert w.ticks.asked[:2] == [5 * 60, 5 * 60]
    w.store.close()


class VolumeGone(OSError):
    pass


def test_a_pass_that_fails_logs_its_error_class_and_the_next_one_runs(tmp_path, disk, records):
    w = ticking(tmp_path)
    with TestClient(w.app, base_url=BASE):
        w.ticks.reached()
        disk.fail = VolumeGone("the volume /mnt/reasonqz-volume went away")
        w.ticks.tick()
        w.ticks.reached()
        assert len(stats_lines(records)) == 1  # the pass failed: no line
        w.ticks.tick()
        w.ticks.reached()
    said = [record.getMessage() for record in records]
    assert any("VolumeGone" in message for message in said), said
    assert not any("reasonqz" in message for message in said)
    assert len(stats_lines(records)) == 2  # the next pass ran
    own = on_a_logger_of_its_own(stats_lines(records))
    failed = [r for r in records if "VolumeGone" in r.getMessage()]
    assert {r.name for r in failed} == {own}  # on the same logger
    w.store.close()


def test_low_disk_is_a_warning_and_healthz_stays_as_it_was(tmp_path, disk, records):
    w = ticking(tmp_path)
    disk.free = lambda: 2 * w.db.stat().st_size - 1
    with TestClient(w.app, base_url=BASE) as client:
        w.ticks.reached()
        healthz = client.get("/healthz")
        assert (healthz.status_code, healthz.content) == (200, b'{"status":"ok"}')
        disk.free = FREE
        w.ticks.tick()
        w.ticks.reached()
    low, fine = stats_lines(records)
    assert low.level == logging.WARNING and low.numbers["low_disk"] is True
    warned = " ".join(r.getMessage() for r in records if r.levelno == logging.WARNING).casefold()
    for word in ("write", "compact", "backup"):  # what stops working when the disk fills
        assert word in warned, warned
    assert fine.level == logging.INFO and fine.numbers["low_disk"] is False
    w.store.close()


@pytest.mark.parametrize("every", [None, 0])
def test_no_line_and_no_loop_when_off(tmp_path, disk, records, every):
    w = ticking(tmp_path, every=every)
    with TestClient(w.app, base_url=BASE) as client:
        assert client.get("/api/projects", headers={"x-api-key": w.key}).status_code == 200
    assert stats_lines(records) == [] and w.ticks.asked == [] and disk.asked == []
    w.store.close()


@pytest.mark.parametrize(("argv", "minutes"), [((), 15), (("--stats-every", "5"), 5),
                                               (("--stats-every", "0"), None)])  # fmt: skip
def test_serve_passes_stats_every(tmp_path, serve, disk, argv, minutes):
    db = tmp_path / "cairn.db"
    prepared(db)
    ran = serve(db, *argv)
    assert ran.code == 0 and ran.app.state.stats_every == minutes


def test_a_plain_serve_writes_the_line_at_start_to_its_stderr(tmp_path, monkeypatch, capfd, disk):
    """On by default where an operator looks: no option, no logging set up by the test."""
    db = tmp_path / "cairn.db"
    prepared(db)
    came = threading.Event()
    original = logging.Logger.callHandlers

    def call_handlers(logger: logging.Logger, record: logging.LogRecord) -> None:
        original(logger, record)
        if '"requests"' in record.getMessage():
            came.set()

    def run(app, **options):
        with TestClient(app, base_url=BASE):
            assert came.wait(60), "serve logged no line at start"

    monkeypatch.setattr(logging.Logger, "callHandlers", call_handlers)
    monkeypatch.setattr(uvicorn, "run", run)
    assert main(["serve", "--db", str(db), "--public-url", BASE, "--no-tidy"]) == 0
    err = capfd.readouterr().err
    (line,) = [line for line in err.splitlines() if '"requests"' in line]
    numbers = json.loads(line[line.index("{") :])
    assert numbers["projects"] == 1 and "workspaces" not in numbers
    assert "cairn.access" not in line


@pytest.mark.parametrize("bad", ["-1", "soon", "nan", ""])
def test_serve_refuses_a_stats_interval_that_is_not_one(tmp_path, serve, bad):
    db = tmp_path / "cairn.db"
    prepared(db)
    assert serve(db, "--stats-every", "30").code == 0  # the option exists: a number runs
    ran = serve(db, "--stats-every", bad)
    assert ran.code == 2 and ran.app is None


def test_no_address_serves_the_numbers(tmp_path, disk, records):
    w = ticking(tmp_path)
    with TestClient(w.app, base_url=BASE) as client:
        w.ticks.reached()
        assert stats_lines(records)  # the numbers exist, in the log
        opened = client.post("/login", data={"key": w.key}, follow_redirects=False)
        keyed = {"cookie": f"cairn_session={opened.cookies['cairn_session']}"}
        client.cookies.clear()
        paths = []
        for route in w.app.routes:
            path = getattr(route, "path", "")
            assert not re.search(r"(?i)stats|metric", path), path
            if isinstance(route, Route) and "GET" in (route.methods or ()) and "{" not in path:
                paths.append(path)
        assert "/" in paths and "/api/projects" in paths
        for path in paths:
            headers = {"x-api-key": w.key} if path.startswith("/api") else keyed
            body = client.get(path, headers=headers, follow_redirects=False).text
            for name in STATS_NAMES:
                assert name not in body, (path, name)
        listed = client.post(
            "/mcp", headers={**MCP_HEADERS, "x-api-key": w.key},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )  # fmt: skip
        assert listed.status_code == 200
        for name in STATS_NAMES:
            assert name not in listed.text
        tools = [t["name"] for t in listed.json()["result"]["tools"]]
        assert not [name for name in tools if re.search(r"(?i)stats|metric", name)], tools
        healthz = client.get("/healthz")
        assert healthz.content == b'{"status":"ok"}'
    w.store.close()
