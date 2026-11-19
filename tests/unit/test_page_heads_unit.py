"""Schema step 26 (Q1): page_heads, one row per page with its latest version's number, title,
claim count and time, kept by triggers on page_versions, so listing a project's pages reads no
version's body. A database built from history: test_writers_schema_unit's version 24, then
step 25, as cairn wrote them. Every clock is in 2099."""

import io
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from test_store_unit import connect, rows, statements, user_version
from test_writers_schema_unit import v24_database

import cairn.store.schema
from cairn.compiler import compile_note, provenance_gate
from cairn.store import SCHEMA_VERSION, Claim, Store

START = datetime(2099, 11, 1, 9, 0, tzinfo=UTC)
HEADS = "SELECT project_id, slug, version, title, claims, updated_at FROM page_heads ORDER BY 1, 2"
# What page_heads must hold: computed from every version, the slow way.
FROM_VERSIONS = (
    "SELECT project_id, slug, version, title, json_array_length(claims), created_at"
    " FROM page_versions AS v WHERE version = (SELECT MAX(version) FROM page_versions AS p"
    " WHERE p.project_id = v.project_id AND p.slug = v.slug) ORDER BY 1, 2"
)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


def v25_database(db) -> dict:
    v24_database(db)
    stamped = cairn.store.schema._STAMP.set(lambda: "2099-10-31T09:00:00.000000+00:00")
    try:
        with connect(db) as con:
            con.execute("BEGIN")
            cairn.store.schema._MIGRATIONS[cairn.store.schema._WRITERS](con)
            con.execute(f"PRAGMA user_version = {cairn.store.schema._WRITERS}")
            con.commit()
    finally:
        cairn.store.schema._STAMP.reset(stamped)
    assert user_version(db) == 25
    return {"statements": statements(db)}


def heads_hold_the_latest(db) -> list:
    held = rows(db, HEADS)
    assert held == rows(db, FROM_VERSIONS)
    return held


def test_step_26_adds_page_heads_filled_from_every_pages_latest_version(tmp_path):
    db = tmp_path / "cairn.db"
    v25 = v25_database(db)
    assert cairn.store.schema._PAGE_HEADS == 26 and SCHEMA_VERSION >= 26
    Store(db, clock=Clock()).close()
    assert user_version(db) == SCHEMA_VERSION
    held = heads_hold_the_latest(db)
    assert ("br_aaaaaaaaaaaa", "c", 2, "C#") in [row[:4] for row in held]
    before = set(v25["statements"])
    added = [line for line in statements(db) if line not in before]
    later = ("checkouts", "credential_tokens", "google_revocations", "slack_")  # 27-29: empty
    later += ("sessions",)  # 30: sessions rebuilt with their last use
    assert added and all(
        "page_heads" in line or any(name in line for name in later) for line in added
    ), added


def test_a_database_migrated_from_version_25_has_the_fresh_schema(tmp_path):
    fresh, migrated = tmp_path / "fresh.db", tmp_path / "migrated.db"
    v25_database(migrated)
    Store(fresh).close()
    Store(migrated).close()
    schema = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'claim#_search%' ESCAPE '#' ORDER BY name"
    )
    assert rows(migrated, schema) == rows(fresh, schema)


def test_a_failed_step_26_leaves_the_version_25_database_as_it_was(tmp_path, monkeypatch):
    db = tmp_path / "cairn.db"
    v25 = v25_database(db)
    version = cairn.store.schema._PAGE_HEADS
    step = cairn.store.schema._MIGRATIONS[version]

    def fail_after_the_real_work(con):
        step(con)
        raise RuntimeError("boom")

    monkeypatch.setitem(cairn.store.schema._MIGRATIONS, version, fail_after_the_real_work)
    with pytest.raises(RuntimeError, match="boom"):
        Store(db)
    assert statements(db) == v25["statements"] and user_version(db) == 25


@pytest.fixture
def db(tmp_path):
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db, clock=Clock())
    yield s
    s.close()


def claims(n: int) -> list[Claim]:
    return [Claim(id=f"cl_{i}", text=f"Claim {i}.", span_ids=()) for i in range(n)]


def test_saves_prunes_and_removals_keep_each_head_on_the_latest_version(store, db):
    project = store.create_project("north")
    for i in range(4):
        store.save_page(project.id, "a", f"A{i}", "Body.", claims(i + 1))
    store.save_page(project.id, "b", "B", "Body.", claims(2))
    assert heads_hold_the_latest(db)[0][2:5] == (4, "A3", 4)
    store.bound_history(2)
    store.save_page(project.id, "a", "A4", "Body.", claims(1))  # older versions removed
    assert heads_hold_the_latest(db)[0][2:5] == (5, "A4", 1)
    with sqlite3.connect(db) as con:  # the latest removed while older remain: the next
        con.execute("DELETE FROM page_versions WHERE slug = 'a' AND version = 5")
    assert heads_hold_the_latest(db)[0][2:5] == (4, "A3", 4)
    with sqlite3.connect(db) as con:  # every version: no head
        con.execute("DELETE FROM page_versions WHERE slug = 'a'")
    assert [row[1] for row in heads_hold_the_latest(db)] == ["b"]


def test_deleting_a_project_or_a_workspace_takes_its_heads(store, db):
    north, south = store.create_project("north"), store.create_project("south")
    for project in (north, south):
        store.save_page(project.id, "a", "A", "Body.", claims(1))
    store.delete_project(north.id)
    assert [row[0] for row in heads_hold_the_latest(db)] == [south.id]
    other = store.create_account("Other")
    theirs = store.for_account(other.id)
    far = theirs.create_project("far")
    theirs.save_page(far.id, "x", "X", "Body.", claims(1))
    store.delete_account(other.id)
    assert [row[0] for row in heads_hold_the_latest(db)] == [south.id]


def test_forgetting_a_note_and_an_import_keep_the_heads(store, db):
    project = store.create_project("north")
    note, _ = store.add_note(project.id, "- North keeps the dock.\n", topic="dock")
    assert compile_note(store, note, provenance_gate).status == "saved"
    archive = io.BytesIO()
    store.export(archive)
    store.forget_note(note.id)
    heads_hold_the_latest(db)
    other = store.create_account("Other")
    archive.seek(0)
    store.for_account(other.id).import_export(archive)
    held = heads_hold_the_latest(db)
    assert len(held) >= 1


def test_listing_pages_reads_the_heads_in_two_statements_without_bodies(store, db):
    project = store.create_project("north")
    for slug in ("c", "a", "b"):
        for _ in range(2):
            store.save_page(project.id, slug, slug.upper(), "y" * 200_000, claims(3))
    seen: list[str] = []
    store._observe(trace=seen.append)
    try:
        heads, total = store.page_heads(project.id, 2)
        counts = store.page_counts()
    finally:
        store._observe(trace=None)
    assert [(h.slug, h.version, h.claims) for h in heads] == [("a", 2, 3), ("b", 2, 3)]
    assert total == 3 and counts == {project.id: 3}
    read = [s for s in seen if s.lstrip().upper().startswith("SELECT")]
    assert len(read) == 3 and all("page_heads" in s and "page_versions" not in s for s in read)
