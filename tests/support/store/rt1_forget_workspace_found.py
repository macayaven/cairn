"""Shared test helpers extracted from test_rt1_forget_workspace_found."""

import io
from zipfile import ZipFile

from cairn.compiler import compile_note
from cairn.store import Claim, Store
from earlier_build import earlier_page

SAID = "Quill sealed the saffron crate."


def filed(view: Store, project_id: str, text: str):
    note, created = view.add_note(project_id, text)
    assert created and compile_note(view, note).status == "saved"
    return note


def passage(view: Store, note):
    return next(span for span in view.note_spans(note.id) if span.kind == "paragraph")


def tables_holding(store: Store, needle: str) -> list[str]:
    """Every table with a text value that holds `needle`: the database as a copy of it reads."""
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


def exported(store: Store) -> tuple[bytes, str]:
    out = io.BytesIO()
    store.export(out)
    with ZipFile(io.BytesIO(out.getvalue())) as archive:
        text = "".join(archive.read(n).decode(errors="replace") for n in archive.namelist())
    return out.getvalue(), text


def legacy(store: Store):
    """Project `desk` with the note, and `east` whose page `minutes` cites it beside its own
    note, as an earlier build saved it."""
    desk, east = store.create_project("desk"), store.create_project("east")
    note = filed(store, desk.id, f"# Dispatch\n\n{SAID}\n")
    filed(store, east.id, "# Minutes\n\nThe ledger opens at noon.\n")
    own = store.get_page(east.id, "minutes").claims[0]
    borrowed = Claim(id="cl_" + "9" * 16, text=SAID, span_ids=(passage(store, note).id,))
    body = f"# Minutes\n\n{own.text}\n\n{SAID}\n"
    earlier_page(store, east.id, "minutes", "Minutes", body, [own, borrowed])
    return desk, east, note
