"""Shared test helpers extracted from test_rt1_forget_workspace_held."""

from cairn.compiler import compile_note
from cairn.store import Claim, Store
from earlier_build import earlier_page

ISSUER = "https://id.acme.example/pool-1"

GONE = "Quill sealed the kqzvtxb crate."


def filed(view: Store, project_id: str, text: str):
    note, created = view.add_note(project_id, text)
    assert created and compile_note(view, note).status == "saved"
    return note


def passage(view: Store, note):
    return next(span for span in view.note_spans(note.id) if span.kind == "paragraph")


def citing(n: int, text: str, *passages) -> Claim:
    return Claim(id=f"cl_{n:016x}", text=text, span_ids=tuple(p.id for p in passages))


def borrowed(store: Store, project_id: str, slug: str, *claims: Claim):
    body = "".join(f"\n{claim.text}\n" for claim in claims)
    return earlier_page(store, project_id, slug, slug.title(), f"# {slug.title()}\n{body}", claims)


def tables_holding(store: Store, needle: str) -> list[str]:
    found = set()
    with store._read() as con:
        names = [n for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        for name in names:
            try:
                rows = con.execute(f'SELECT * FROM "{name}"').fetchall()
            except Exception:  # a virtual table's shadow that cannot be read alone
                continue
            for row in rows:
                for value in row:
                    text = value.decode(errors="replace") if isinstance(value, bytes) else value
                    if isinstance(text, str) and needle in text:
                        found.add(name)
    return sorted(found)


def forgotten_rows(store: Store) -> list[tuple[str, str]]:
    with store._read() as con:
        return con.execute(
            "SELECT projects.name, forgotten.text_sha256 FROM forgotten"
            " JOIN projects ON projects.id = forgotten.project_id ORDER BY 1, 2"
        ).fetchall()
