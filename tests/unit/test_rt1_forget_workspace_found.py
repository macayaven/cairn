"""RT1, one step past the forget across projects: what the eighth round's fix did not reach.

A page of another project that cites a note can exist only in a database an earlier build
wrote (tests/earlier_build.py writes one; save_page refuses a new such citation). The forget
found such a page wherever it was; these were its neighbours. The first was found failing
and is fixed since the ninth round (a delete changes such pages as a forget does: erasure's
one rule). The second is a stated limit (src/cairn/guides/run.md), kept as a passing test
of what the import says. What the ninth round left: test_rt1_forget_workspace_found2.py.
Made-up notes."""

import io
from contextlib import closing
from zipfile import ZipFile

import pytest

from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.memory import Memory
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


def test_deleting_a_project_takes_its_notes_text_out_of_pages_of_other_projects():
    """The privacy guide: a project's notes go at once. Found on the eighth round's fix:
    deleting the note's whole project left east's page with the sentence in its body, its
    claims, its search rows and a fresh export. Fixed: the delete rewrites the page as a
    forget does, and no table holds the text."""
    with closing(Store(":memory:")) as store:
        desk, east, _ = legacy(store)
        assert Memory(store, lint_gate).delete_project("desk", "desk") == {"deleted": True}
        assert store.get_project("desk") is None and store.list_notes(desk.id) == []
        _, archive = exported(store)
        page = store.get_page(east.id, "minutes")
        assert (SAID in page.body, store.search("saffron") != [], SAID in archive) == (
            False,
            False,
            False,
        )
        assert tables_holding(store, "saffron") == []


def test_limit_an_export_of_a_database_an_earlier_build_wrote_is_refused_naming_the_page():
    """Restated as a passing test of a stated limit (src/cairn/guides/run.md, beside the
    import): the import's check stays, so an empty store refuses the whole export of such a
    database, with the line that names the page. Forgetting the note it cites, or deleting
    that note's project, rewrites the page, and the next export imports
    (test_earlier_page_export_unit.py)."""
    with closing(Store(":memory:")) as store:
        legacy(store)
        archive, _ = exported(store)
    with closing(Store(":memory:")) as fresh:
        refused = r"^a claim of 'minutes' in projects/east/ cites a passage that is not of its"
        with pytest.raises(ValueError, match=refused):
            fresh.import_export(io.BytesIO(archive))
        assert fresh.list_projects() == []
