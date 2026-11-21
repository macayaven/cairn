"""A page as a database an earlier build wrote can hold it: claims that cite passages of
another project. Store.save_page refuses such a page now, and an upgrade does not rewrite
one (a forget finds it when the note it cites is forgotten: cairn.store.erasure), so the
tests that show how cairn reads, relates, traces, draws or forgets one write it here,
straight into the tables, as that build's save wrote it: the page's next version with its
search rows (memory._write_page_version), its topic key when no other page of the project
holds it, and its note.saved event."""

import json
from collections.abc import Sequence

from cairn.store import Claim, Store
from cairn.store.memory import _write_page_version
from cairn.topics import topic_key


def earlier_page(
    store: Store, project_id: str, slug: str, title: str, body: str, claims: Sequence[Claim]
):
    """The page's next version, written as an earlier build's save_page wrote it, past the
    refusal of another project's passages; the page as get_page reads it back."""
    claims = tuple(claims)
    with store._write() as con:
        (latest,) = con.execute(
            "SELECT COALESCE(MAX(version), 0) FROM page_versions WHERE project_id = ? AND slug = ?",
            (project_id, slug),
        ).fetchone()
        version, at = latest + 1, store._stamp()
        stored = json.dumps([claim.model_dump(mode="json") for claim in claims])
        row = (project_id, slug, version, title, body, stored, "{}", at)
        _write_page_version(con, store._account_id, store._index, row, claims)
        con.execute(
            "INSERT OR IGNORE INTO page_keys (project_id, slug, topic_key) VALUES (?, ?, ?)",
            (project_id, slug, topic_key(title)),
        )
        (name,) = con.execute("SELECT name FROM projects WHERE id = ?", (project_id,)).fetchone()
        detail = {"page": slug, "version": version}
        store._audit(con, "note.saved", project=name, detail=detail, at=at)
    return store.get_page(project_id, slug)
