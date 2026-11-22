"""RT1 against the rule that a page is held to its own project's notes (schema step 32, the
writers' leave-out, save_page's refusal): what held.

The builder (`at_31`): this build's store writes the workspaces, projects and notes through
its own paths, and the pages an earlier build's save wrote go straight into the tables as that
save wrote them (`written`: the next version with its search rows, statistics and topic key);
the file is then stamped at schema 31 with its floor at 31, which is honest because step 32
adds and changes no table. The oracle for "the text is gone" is a scan of every row of every
table (`holding`), by the row's project or note, and a fresh export. Made-up notes."""

import io
import json
import logging
import sqlite3
import tracemalloc
from contextlib import closing
from pathlib import Path
from zipfile import ZipFile

import pytest
from starlette.testclient import TestClient

import cairn.store.schema as schema
from cairn.server import create_app
from cairn.store import SchemaTooNew, Store, erasure
from support.store.rt1_own_project_held import SRC, cite, clean_and_hit, filed, para, written

OUT = "Brook moved the zmvqpr barge."  # a note of another workspace, cited by yard
BASE = "http://127.0.0.1:8765"
ISSUER = "https://id.acme.example/pool-1"


def at_31(db: Path) -> None:
    con = sqlite3.connect(db)
    try:
        con.execute("PRAGMA user_version = 31")
        con.execute("UPDATE oldest_build SET version = 31")
        con.commit()
    finally:
        con.close()


def holding(db: Path, needle: str) -> set[tuple[str, str]]:
    """(table, the row's project id, note id or id) of every row with a text holding `needle`;
    the search index's own shadow rows by their table alone."""
    found = set()
    con = sqlite3.connect(db)
    try:
        names = [n for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        for name in names:
            try:
                cursor = con.execute(f'SELECT * FROM "{name}"')
            except sqlite3.Error:
                continue
            columns = [d[0] for d in cursor.description]
            for row in cursor:
                for value in row:
                    text = value.decode(errors="replace") if isinstance(value, bytes) else value
                    if isinstance(text, str) and needle in text:
                        by = dict(zip(columns, row, strict=True))
                        found.add((name, str(by.get("project_id") or by.get("note_id") or "")))
    finally:
        con.close()
    return found


def earlier(db: Path) -> dict:
    """The file an earlier build left: desk's note cited by pages of east (beside its own
    claims; alone), west (an older version only; three versions), yard (a claim folded over
    its own passage and desk's), vault (closed to members) and another workspace's `out`;
    yard also cites out's note; east cites a passage no longer held; east and west each cite
    the other's note (two pages, each of the other's project)."""
    with closing(Store(db)) as store:
        desk, east, west, vault, yard = (
            store.create_project(n, **({"everyone": "none"} if n == "vault" else {}))
            for n in ("desk", "east", "west", "vault", "yard")
        )
        src = para(store, filed(store, desk.id, f"# Dispatch\n\n{SRC}\n"))
        places = {
            (east, "east"),
            (east, "dead"),
            (east, "swap"),
            (west, "swap"),
            (west, "west"),
            (west, "many"),
            (yard, "yard"),
            (yard, "barge"),
            (vault, "vault"),
        }
        own = {  # one own note a page, each on its own page first: a passage on one page only
            (p.name, slug): para(
                store, filed(store, p.id, f"# {slug.title()}\n\nThe {p.name} {slug} log.\n")
            )
            for p, slug in places
        }
        other = store.for_account(store.create_account("Fieldwork").id)
        out = other.create_project("out")
        far = para(other, filed(other, out.id, f"# Barge\n\n{OUT}\n"))
        o = own
        e, d = o["east", "east"], o["east", "dead"]
        written(store, east.id, "east", "East", [cite(1, e.text, e), cite(2, SRC, src)])
        written(store, east.id, "alone", "Alone", [cite(3, SRC, src)])
        written(
            store, east.id, "dead", "Dead", [cite(4, d.text, d), cite(5, "Lost.", "sp_" + "d" * 16)]
        )
        written(
            store, east.id, "swap", "Swap", [cite(6, o["west", "swap"].text, o["west", "swap"])]
        )
        written(
            store, west.id, "swap", "Swap", [cite(7, o["east", "swap"].text, o["east", "swap"])]
        )
        w, m = o["west", "west"], o["west", "many"]
        written(store, west.id, "west", "West", [cite(8, SRC, src), cite(9, w.text, w)])
        written(store, west.id, "west", "West", [cite(9, w.text, w)])
        for k in range(3):
            written(store, west.id, "many", "Many", [cite(10, m.text, m), cite(11 + k, SRC, src)])
        y, b = o["yard", "yard"], o["yard", "barge"]
        written(store, yard.id, "yard", "Yard", [cite(20, y.text, y, src)])
        written(store, yard.id, "barge", "Barge", [cite(21, b.text, b), cite(22, OUT, far)])
        written(store, vault.id, "vault", "Vault", [cite(23, SRC, src)])
        written(other, out.id, "borrow", "Borrow", [cite(24, SRC, src)])
        with store._read() as con:
            counted = erasure.count_pages_citing_other_projects(con)
        ids = {p.name: p.id for p in (desk, east, west, vault, yard)}
        assert counted == {store._account_id: 10, other._account_id: 1}
        return {"ids": ids | {"out": out.id}, "own": own, "two": other._account_id}


def pages(store: Store) -> dict:
    return {
        (p.name, page.slug): (
            [c.span_ids for c in store.get_page(p.id, page.slug).claims],
            len(store.page_versions(p.id, page.slug)),
        )
        for p in store.list_projects()
        for page in store.list_pages(p.id)
    }


def test_a_file_at_31_opens_held_to_its_own_projects_notes_once(tmp_path, caplog):
    db = tmp_path / "cairn.db"
    made = earlier(db)
    at_31(db)
    ids, own = made["ids"], made["own"]
    with caplog.at_level(logging.WARNING, logger="cairn.store"), closing(Store(db)) as store:
        with store._read() as con:
            assert erasure.count_pages_citing_other_projects(con) == {}
            assert con.execute("SELECT version FROM oldest_build").fetchall() == [(32,)]
            assert con.execute("PRAGMA user_version").fetchone() == (32,)
        kept = ("east", "dead"), ("east", "east"), ("west", "many"), ("west", "west")
        kept += ("yard", "barge"), ("yard", "yard")
        assert {k: v for k, v in pages(store).items() if k[0] != "desk"} == {
            place: ([(own[place].id,)], 1) for place in kept
        }  # alone, both swaps and vault (whose latest version held nothing of its own) went
        assert [s.project_id for s in store.search("kqzvtxb")] == [ids["desk"]]
        assert store.search("zmvqpr") == []
        archives = []
        for view in (store, store.for_account(made["two"])):
            out = io.BytesIO()
            view.export(out)
            archives.append(out.getvalue())
        with store._read() as con:
            disk = dict(con.execute("SELECT account_id, history_bytes FROM account_disk"))
            counted = dict(
                con.execute(
                    "SELECT projects.account_id, SUM(LENGTH(CAST(body AS BLOB))"
                    " + LENGTH(CAST(claims AS BLOB))) FROM page_versions"
                    " JOIN projects ON projects.id = page_versions.project_id GROUP BY 1"
                )
            )
            stale_heads = con.execute(
                "SELECT COUNT(*) FROM page_heads AS h WHERE h.version IS NOT"
                " (SELECT MAX(version) FROM page_versions AS p"
                "  WHERE p.project_id = h.project_id AND p.slug = h.slug)"
            ).fetchone()
        assert {a: disk.get(a) for a in counted} == counted and stale_heads == (0,)
    repairs = [r.args[1:] for r in caplog.records if r.getMessage().startswith("repair:")]
    assert sorted(repairs) == [(1, 0, 1), (10, 6, 4)]
    assert {table for table, at in holding(db, "kqzvtxb") if at and at != ids["desk"]} <= {"spans"}
    assert {table for table, at in holding(db, "zmvqpr") if at and at != ids["out"]} <= {"spans"}
    assert holding(db, "Lost.") == set()
    for archive in archives:  # each imports, and its pages export again as they were
        with closing(Store(":memory:")) as fresh:
            fresh.import_export(io.BytesIO(archive))
            again = io.BytesIO()
            fresh.export(again)
            assert exported_pages(again.getvalue()) == exported_pages(archive)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="cairn.store"), closing(Store(db)):
        assert not [r for r in caplog.records if r.getMessage().startswith("repair:")]


def exported_pages(archive: bytes) -> list:
    """Each page version an archive holds: project, slug, version, title, body and claims."""
    with ZipFile(io.BytesIO(archive)) as zipped:
        found = []
        for name in sorted(n for n in zipped.namelist() if n.endswith("pages.jsonl")):
            for line in zipped.read(name).decode().splitlines():
                row = json.loads(line)
                found.append((name, *(row.get(k) for k in ("slug", "version", "title", "body"))))
                found.append(json.dumps(row.get("claims"), sort_keys=True))
        return found


def test_an_older_build_refuses_the_file_once_it_is_at_32(tmp_path, monkeypatch):
    db = tmp_path / "cairn.db"
    earlier(db)
    at_31(db)
    Store(db).close()
    monkeypatch.setattr(schema, "SCHEMA_VERSION", 31)
    with pytest.raises(SchemaTooNew):
        Store(db)


def test_a_repair_that_fails_on_its_second_page_leaves_the_file_as_it_was(tmp_path, monkeypatch):
    db = tmp_path / "cairn.db"
    earlier(db)
    at_31(db)

    def dump() -> dict:
        con = sqlite3.connect(db)
        try:
            names = con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            return {
                n: con.execute(f'SELECT * FROM "{n}" ORDER BY 1').fetchall()
                for (n,) in names.fetchall()
                if "search" not in n
            } | {"version": con.execute("PRAGMA user_version").fetchone()}
        finally:
            con.close()

    before, real, calls = dump(), erasure._plan_page, []

    def halfway(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("halfway")
        return real(*args, **kwargs)

    monkeypatch.setattr(erasure, "_plan_page", halfway)
    with pytest.raises(RuntimeError, match="halfway"):
        Store(db)
    assert dump() == before
    monkeypatch.setattr(erasure, "_plan_page", real)
    with closing(Store(db)) as store, store._read() as con:
        assert erasure.count_pages_citing_other_projects(con) == {}


def test_on_clean_pages_the_repair_makes_one_search_and_writes_nothing():
    with closing(clean_and_hit(300, 0)) as store:
        said: list[str] = []
        store._observe(trace=said.append)
        try:
            with store._write() as con:
                done = erasure.hold_to_own_projects(con, None, store._stamp())
        finally:
            store._observe(trace=None)
        mine = [s for s in said if not s.startswith("--")]
        assert done == {"workspaces": 0, "rewritten": 0, "removed": 0}
        assert len([s for s in mine if "json_each(p.claims)" in s]) == 1
        assert not [s for s in mine if s.split()[0].upper() in ("INSERT", "UPDATE", "DELETE")]


def test_the_repair_holds_one_page_at_a_time_whatever_it_repairs():
    """Python's peak while it repairs 40 pages and 160 pages: the same, give or take."""
    peaks = []
    for hit in (40, 160):
        with closing(clean_and_hit(0, hit)) as store:
            tracemalloc.start()
            try:
                with store._write() as con:
                    assert (
                        erasure.hold_to_own_projects(con, None, store._stamp())["rewritten"] == hit
                    )
                peaks.append(tracemalloc.get_traced_memory()[1])
            finally:
                tracemalloc.stop()
    assert peaks[1] < 2 * peaks[0], peaks


@pytest.mark.parametrize("state", ["same workspace", "another workspace", "not held", "folded"])
def test_a_key_limited_to_the_receiving_project_files_onto_a_page_written_behind_the_store(state):
    """A row written straight into the tables at schema 32, and a note sent over REST with a
    key that reaches east alone. Held anywhere: the claim leaves the latest version (its
    earlier versions keep it: a write is not an erasure), the note is saved, nothing waits.
    Not held: the note is refused with ids alone (the stated limit). Folded over east's own
    passage: the claim leaves whole, and east's own passage with it (the stated limit). No
    answer, log line or event names desk, out or their text."""
    with closing(Store(":memory:")) as store:
        desk, east = store.create_project("desk"), store.create_project("east")
        other = store.for_account(store.create_account("Fieldwork").id)
        out = other.create_project("out")
        where = (other, out) if state == "another workspace" else (store, desk)
        src = para(where[0], filed(where[0], where[1].id, f"# Dispatch\n\n{SRC}\n"))
        own = para(store, filed(store, east.id, "# Minutes\n\nThe ledger opens at noon.\n"))
        cited = "sp_" + "d" * 16 if state == "not held" else src.id
        claims = (
            [cite(1, own.text, own, cited)]
            if state == "folded"
            else [cite(1, own.text, own), cite(2, SRC, cited)]
        )
        version = written(store, east.id, "minutes", "Minutes", claims)
        key = store.create_key("east only", projects=[east.id])
        last = max(e.n for e in store.list_audit(1))
        records: list[str] = []
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record.getMessage())
        logging.getLogger().addHandler(handler)
        try:
            with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
                said = client.post(
                    "/api/projects/east/notes",
                    headers={"x-api-key": key},
                    json={"text": "# Minutes\n\nWren rings the bell at two.\n"},
                )
        finally:
            logging.getLogger().removeHandler(handler)
        answer = said.json()
        page = store.get_page(east.id, "minutes")
        latest = [c.span_ids for c in page.claims]
        events = json.dumps(  # the events the note's write made
            [e.model_dump(mode="json") for e in store.list_audit(20) if e.n > last]
        )
        for text in (said.text, events, *records):
            assert not [w for w in ("desk", "out", "Fieldwork", "kqzvtxb", "Quill") if w in text]
        assert said.status_code == 201 and store.waiting_notes() == []
        if state == "not held":
            assert answer["status"] == "rejected" and page.version == version
            assert answer["violations"][0].startswith("provenance.unknown_passage")
        else:
            assert answer["status"] == "saved" and page.version == version + 1
            assert not [ids for ids in latest if src.id in ids]
            assert len(store.page_versions(east.id, "minutes")) == version + 1
            assert ((own.id,) in latest) is (state != "folded")


def test_the_preview_counts_other_projects_only_for_one_who_may_forget():
    with closing(Store(":memory:")) as store:
        ada, brook, cara = (
            store.sign_in("sso", f"sub-{n}", f"{n}@acme.example", n, issuer=ISSUER, join="default")
            for n in ("ada", "brook", "cara")
        )
        as_ada, as_brook, as_cara = (
            store.for_account(s.account_id, s.user.id) for s in (ada, brook, cara)
        )
        desk = as_brook.create_project("desk")
        note = filed(as_brook, desk.id, f"# Dispatch\n\n{SRC}\n")
        vault = as_ada.create_project("vault", everyone="none")
        written(store, vault.id, "embargo", "Embargo", [cite(1, SRC, para(store, note))])
        principal = store.resolve_key(store.create_key("desk only", projects=[desk.id]))
        narrowed = store.for_account(principal.account_id).narrow(
            principal.projects, principal.access
        )
        seen = [
            view.forget_preview(note.id).elsewhere
            for view in (as_cara, narrowed, as_brook, as_ada, store)
        ]
        assert seen == [0, 0, 1, 1, 1]
