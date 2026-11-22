"""Shared test helpers extracted from test_rt1_own_project_held."""

import json

from cairn.compiler import compile_note
from cairn.store import Claim, Store
from cairn.store.base import _index_name
from cairn.store.memory import _write_page_version
from cairn.topics import topic_key

SRC = "Quill sealed the kqzvtxb crate."  # desk's note, cited by pages of other projects


def filed(view: Store, project_id: str, text: str):
    note, created = view.add_note(project_id, text)
    assert created and compile_note(view, note).status == "saved", text
    return note


def para(view: Store, note):
    return next(span for span in view.note_spans(note.id) if span.kind == "paragraph")


def cite(n: int, text: str, *passages) -> Claim:
    ids = tuple(p if isinstance(p, str) else p.id for p in passages)
    return Claim(id=f"cl_{n:016x}", text=text, span_ids=ids)


def written(view: Store, project_id: str, slug: str, title: str, claims) -> int:
    """The page's next version as an earlier build's save wrote it, straight into the tables:
    the version, its search rows and statistics, its topic key when the page has none."""
    claims = tuple(claims)
    body = f"# {title}\n" + "".join(f"\n{claim.text}\n" for claim in claims)
    account = view._account_id
    with view._write() as con:
        (latest,) = con.execute(
            "SELECT COALESCE(MAX(version), 0) FROM page_versions WHERE project_id = ? AND slug = ?",
            (project_id, slug),
        ).fetchone()
        stored = json.dumps([claim.model_dump(mode="json") for claim in claims])
        row = (project_id, slug, latest + 1, title, body, stored, "{}", view._stamp())
        _write_page_version(con, account, _index_name(account), row, claims)
        con.execute(
            "INSERT OR IGNORE INTO page_keys (project_id, slug, topic_key) VALUES (?, ?, ?)",
            (project_id, slug, topic_key(title)),
        )
    return latest + 1


def clean_and_hit(clean: int, hit: int) -> Store:
    store = Store(":memory:")
    a, b = store.create_project("a"), store.create_project("b")
    src = para(store, filed(store, a.id, f"# Src\n\n{SRC}\n"))
    own = para(store, filed(store, b.id, "# Own\n\nThe ledger opens.\n"))
    for n in range(clean):
        written(store, b.id, f"clean-{n:05d}", f"Clean {n}", [cite(1, own.text, own)])
    for n in range(hit):
        written(
            store, b.id, f"hit-{n:05d}", f"Hit {n}", [cite(1, own.text, own), cite(2, SRC, src)]
        )
    return store
