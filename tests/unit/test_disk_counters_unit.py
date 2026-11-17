"""Unit tests for the counters behind the disk bounds (H6c): each workspace's passages and
the bytes of its page history, kept by every write that adds or removes them, always equal
to a recomputation from the rows."""

import io
import json
import random
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from cairn.arrange import arrange_page
from cairn.compiler import CONTENT_KINDS
from cairn.gate import lint_gate
from cairn.llm import FakeModel, Reply
from cairn.memory import Memory, RequestError
from cairn.store import BYTES_PER_PASSAGE, PASSAGE_KINDS, Limits, Store


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


def recount(db: Path, account_id: str) -> tuple[int, int]:
    """(passages, history bytes) of a workspace, from its rows: one for each passage, and
    one for every full BYTES_PER_PASSAGE bytes of each span's heading path as stored (its
    JSON less the brackets, quotes and separators)."""
    con = sqlite3.connect(db)
    try:
        passages = 0
        for kind, path in con.execute(
            "SELECT spans.kind, spans.heading_path FROM spans"
            " JOIN notes ON notes.id = spans.note_id"
            " JOIN projects ON projects.id = notes.project_id WHERE projects.account_id = ?",
            (account_id,),
        ):
            headings = json.loads(path)
            stored = len(path.encode()) - (4 * len(headings) if headings else 2)
            passages += (kind in CONTENT_KINDS) + stored // BYTES_PER_PASSAGE
        (history,) = con.execute(
            "SELECT COALESCE(SUM(LENGTH(CAST(body AS BLOB)) + LENGTH(CAST(claims AS BLOB))), 0)"
            " FROM page_versions JOIN projects ON projects.id = page_versions.project_id"
            " WHERE projects.account_id = ?",
            (account_id,),
        ).fetchone()
    finally:
        con.close()
    return passages, history


def counted(view: Store) -> tuple[int, int]:
    return view.passages(), view.history_bytes()


def versions_of(view: Store) -> dict[tuple[str, str], list[int]]:
    return {
        (project.id, page.slug): view.page_versions(project.id, page.slug)
        for project in view.list_projects()
        for page in view.list_pages(project.id)
    }


def test_a_passage_is_what_the_compiler_makes_a_claim_of():
    assert PASSAGE_KINDS == CONTENT_KINDS


def shape(rng: random.Random, n: int) -> str:
    topic = rng.choice(["Relay", "Queue", "Ledger", f"Page {n % 7}"])
    kind = rng.choice(["prose", "bullets", "code", "quote", "table", "sections", "headings"])
    if kind == "prose":
        body = "".join(f"Line {n}-{i} holds a fact.\n\n" for i in range(rng.randint(1, 6)))
    elif kind == "bullets":
        body = "".join(f"- item {n}-{i}\n" for i in range(rng.randint(1, 12)))
    elif kind == "code":
        body = f"```\nrun --n {n}\n```\n"
    elif kind == "quote":
        body = f"> Said {n}, plainly.\n"
    elif kind == "table":
        body = f"| a | b |\n|---|---|\n| {n} | é{n} |\n"
    elif kind == "sections":
        body = f"## Part {n}\n\nOne {n}.\n\n### Deeper\n\n- two {n}\n"
    else:  # long, nested, accented or escaped headings: their paths are charged
        mark = rng.choice(["h", "é", '"', "\\"])
        long = mark * rng.randint(10, 120)
        body = f"## {long} {n}\n\nOne {n}.\n\n### {long}\n\n- two {n}\n- three {n}\n"
    return f"# {topic}\n\n{body}"


def test_the_counters_equal_the_rows_after_hundreds_of_random_writes(store, db, tmp_path):
    rng = random.Random(16)
    other = store.for_account(store.create_account("Brook").id)
    other.create_project("ops")
    Memory(other, lint_gate).note("ops", "# Brook\n\nTheirs alone.\n\n- one\n")
    theirs = recount(db, other.account_id)
    done: Counter[str] = Counter()
    imports = 0
    for name in ("ops", "notes", "team"):
        store.create_project(name)
    for n in range(300):
        roll = rng.random()
        projects = store.list_projects()
        if roll < 0.6 and projects:
            project = rng.choice(projects)
            view = store.limited(Limits(versions_per_page=2)) if rng.random() < 0.3 else store
            try:
                Memory(view, lint_gate).note(project.name, shape(rng, n))
                done["note"] += 1
            except RequestError:
                done["refused"] += 1  # a forgotten text again: nothing stored
        elif roll < 0.72 and projects:
            notes = [c for b in projects for c in store.list_notes(b.id)]
            if notes:
                store.forget_note(rng.choice(notes).id)
                done["forget"] += 1
        elif roll < 0.77 and len(projects) > 1:
            store.delete_project(rng.choice(projects).id)
            store.create_project(f"fresh-{n}")
            done["delete project"] += 1
        elif roll < 0.87 and projects:
            project = rng.choice(projects)
            pages = store.list_pages(project.id)
            if pages:
                page = rng.choice(pages)
                order = [claim.id for claim in reversed(page.claims)]
                reply = Reply(text=json.dumps({"order": order, "same": []}))
                model = FakeModel([reply], name="fake")
                capped = rng.random() < 0.5  # a history budget of 8 kB: older versions go
                view = store.limited(Limits(storage_bytes=2_000)) if capped else store
                versions = sum(len(v) for v in versions_of(store).values())
                status = arrange_page(view, project.id, page.slug, model).status
                done[status] += 1
                saved = status == "arranged"
                if (
                    saved
                    and capped
                    and sum(len(v) for v in versions_of(store).values()) <= versions
                ):
                    done["history dropped"] += 1
                    assert store.history_bytes() <= 8_000 or all(
                        len(v) == 1 for v in versions_of(store).values()
                    )
        elif roll < 0.9 and imports < 3:
            archive = io.BytesIO()
            store.export(archive)
            copied = tmp_path / f"copy-{n}.db"  # notes keep their ids: another file
            copy = Store(copied)
            try:
                target = copy.for_account(copy.create_account("Copy").id)
                target.import_export(io.BytesIO(archive.getvalue()))
                assert counted(target) == recount(copied, target.account_id) == counted(store)
                copy.delete_account(target.account_id)
                assert recount(copied, target.account_id) == (0, 0) == counted(target)
            finally:
                copy.close()
            imports += 1
            done["import"] += 1
        assert counted(store) == recount(db, store.account_id), n
    assert counted(store)[0] > 0 and counted(store)[1] > 0  # it held something at the end
    assert counted(other) == recount(db, other.account_id) == theirs != (0, 0)
    assert done["note"] > 100 and done["arranged"] > 5 and done["import"] == 3, done
    assert done["forget"] > 10 and done["delete project"] > 5, done
    assert done["history dropped"] > 2, done


def test_a_deleted_workspace_leaves_no_count_behind(store, db):
    brook = store.for_account(store.create_account("Brook").id)
    project = brook.create_project("ops")
    Memory(brook, lint_gate).note("ops", "# Ops\n\nOne.\n\n- two\n")
    assert counted(brook) == recount(db, brook.account_id) != (0, 0)
    store.delete_account(brook.account_id)
    con = sqlite3.connect(db)
    try:
        left = con.execute(
            "SELECT COUNT(*) FROM account_disk WHERE account_id = ?", (brook.account_id,)
        ).fetchone()
    finally:
        con.close()
    assert left == (0,) and project.id
