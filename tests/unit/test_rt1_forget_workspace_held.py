"""RT1, one step past the forget across projects: what held.

Pages of other projects that cite a note are written as a database an earlier build holds
them (tests/earlier_build.py). The oracle for what a forget keeps is the plain loop of
test_rt1_harness (drop the claim the render names, render, ask again), and a claim's power
to open a fence is read leniently by that harness's `opens_a_fence_line`, never by
cairn.store.erasure. Made-up notes; bounded to seconds."""

import io
import json
import sys
from contextlib import closing
from zipfile import ZipFile

import pytest
from test_forget_render_cap_unit import CAP, EXAMPLE, HARD, cascade, sectioned
from test_forget_siblings_unit import narrow, page, under_steps
from test_rt1_harness import PROJECT, Run, Watch, expected, opens_a_fence_line

from cairn import compiler, spans
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.memory import Forbidden, Memory
from cairn.store import CitesForgotten, Claim, Store, erasure
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


def test_a_forget_leaves_no_passage_of_the_note_in_any_table_of_three_other_projects():
    """east: a claim citing the note and a note of west (it leaves), and a fold with east's
    own note that says the same (it keeps east's passage); west: only an older version
    cites it; north: three versions do. After the forget no table holds a passage id of
    the note, its id stays in the audit log alone, each changed page keeps one version, the
    stored bytes fall by the note's size, and the export restores into an empty store with
    no passage id of it either."""
    with closing(Store(":memory:")) as store:
        desk, east, west, north = (
            store.create_project(n) for n in ("desk", "east", "west", "north")
        )
        note = filed(store, desk.id, f"# Dispatch\n\n{GONE}\n")
        gone = passage(store, note)
        own = passage(store, filed(store, east.id, "# Minutes\n\nThe ledger opens at noon.\n"))
        same = passage(store, filed(store, east.id, f"# Copy\n\n{GONE}\n"))
        far = passage(store, filed(store, west.id, "# Far\n\nWren keeps the far log.\n"))
        gate = passage(store, filed(store, north.id, "# North\n\nThe north gate shuts.\n"))
        borrowed(store, east.id, "minutes", citing(1, own.text, own), citing(3, GONE, gone, far))
        borrowed(store, east.id, "copy", citing(2, GONE, gone, same))
        borrowed(store, west.id, "far", citing(4, GONE, gone), citing(5, far.text, far))
        borrowed(store, west.id, "far", citing(5, far.text, far))
        for k in range(3):
            borrowed(
                store, north.id, "north", citing(6, gate.text, gate), citing(7 + k, GONE, gone)
            )
        ids = [span.id for span in store.note_spans(note.id)]
        before = store.stored_bytes()

        answer = Memory(store, lint_gate).forget("desk", note.id)

        assert answer == {
            "note_id": note.id,
            "rebuilt": [],
            "removed": ["dispatch"],
            "elsewhere": 4,
        }
        assert before - store.stored_bytes() == len(f"# Dispatch\n\n{GONE}\n".encode())
        assert [tables_holding(store, i) for i in ids] == [[] for _ in ids]
        assert tables_holding(store, note.id) == ["audit_events"]
        changed = ((east, "minutes"), (east, "copy"), (west, "far"), (north, "north"))
        shown = {
            (p.name, slug): [(c.text, c.span_ids) for c in store.get_page(p.id, slug).claims]
            for p, slug in changed
        }
        assert shown == {
            ("east", "minutes"): [(own.text, (own.id,))],
            ("east", "copy"): [(same.text, (same.id,))],
            ("west", "far"): [(far.text, (far.id,))],
            ("north", "north"): [(gate.text, (gate.id,))],
        }
        assert [len(store.page_versions(p.id, slug)) for p, slug in changed] == [1] * 4
        out = io.BytesIO()
        store.export(out)
        with ZipFile(io.BytesIO(out.getvalue())) as archive:
            text = "".join(archive.read(n).decode(errors="replace") for n in archive.namelist())
        assert not [i for i in [*ids, note.id] if i in text]
    with closing(Store(":memory:")) as fresh:
        assert fresh.import_export(io.BytesIO(out.getvalue())).pages == 4
        assert [tables_holding(fresh, i) for i in ids] == [[] for _ in ids]


def attempt(view: Store, project_id: str, slug: str, claims) -> str:
    try:
        return f"v{view.save_page(project_id, slug, slug.title(), '', claims).version}"
    except CitesForgotten:
        return "CitesForgotten"
    except ValueError as refused:
        return str(refused)


def test_the_two_refusals_of_save_page_by_what_a_claim_cites():
    """Another project's passage, alone, beside the page's own or beside a third project's,
    and a passage of another workspace: refused in one sentence. A span the store never
    held, or no span: saved, in a workspace that never forgot. Once a note of any project of
    the workspace is forgotten, a span it does not hold is CitesForgotten; another workspace
    is never asked, and its page may cite the forgotten span's id, which it does not hold."""
    refused = "A page can cite only passages from its own project."
    with closing(Store(":memory:")) as store:
        desk, east, west = (store.create_project(n) for n in ("desk", "east", "west"))
        d = passage(store, filed(store, desk.id, f"# Dispatch\n\n{GONE}\n"))
        e = passage(store, filed(store, east.id, "# Minutes\n\nThe ledger opens at noon.\n"))
        w = passage(store, filed(store, west.id, "# Far\n\nWren keeps the far log.\n"))
        other = store.for_account(store.create_account("Fieldwork").id)
        out = other.create_project("out")
        x = passage(other, filed(other, out.id, "# Out\n\nThe outer gate shuts.\n"))
        made = Claim(id="cl_" + "1" * 16, text=GONE, span_ids=("sp_" + "0" * 16,))
        bare = Claim(id="cl_" + "2" * 16, text=GONE, span_ids=())
        assert [
            attempt(store, east.id, "a", [citing(1, GONE, d)]),
            attempt(store, east.id, "b", [citing(1, GONE, e, d)]),
            attempt(store, east.id, "c", [citing(1, GONE, d, w)]),
            attempt(store, east.id, "d", [citing(1, GONE, x)]),
            attempt(other, out.id, "e", [citing(1, GONE, d)]),
            attempt(store, east.id, "f", [made]),
            attempt(store, east.id, "g", [bare]),
        ] == [refused] * 5 + ["v1", "v1"]
        assert [store.get_page(east.id, s) for s in "abcd"] == [None] * 4
        store.forget_note(w.source_id)
        assert attempt(store, east.id, "h", [made]) == "CitesForgotten"
        assert attempt(other, out.id, "i", [made]) == "v1"
        assert attempt(other, out.id, "j", [citing(1, w.text, w)]) == "v1"


def test_limit_a_a_note_compiled_into_an_old_cross_project_page_is_stored_and_waits():
    """The implementer's limit (a), through Memory.note: the writer gets ValueError (over
    REST a 500), the note is stored and waits for its page, the page is as it was."""
    with closing(Store(":memory:")) as store:
        desk, east = store.create_project("desk"), store.create_project("east")
        note = filed(store, desk.id, f"# Dispatch\n\n{GONE}\n")
        filed(store, east.id, "# Minutes\n\nThe ledger opens at noon.\n")
        own = store.get_page(east.id, "minutes").claims[0]
        borrowed(store, east.id, "minutes", own, citing(9, GONE, passage(store, note)))
        before = store.get_page(east.id, "minutes")
        with pytest.raises(ValueError, match=r"^A page can cite only passages from its own"):
            Memory(store, lint_gate).note("east", "# Minutes\n\nWren rings the bell at two.\n")
        assert len(store.list_notes(east.id)) == 2
        assert store.get_page(east.id, "minutes") == before
        assert [w.project_id for w in store.waiting_notes()] == [east.id]


def test_a_member_who_may_not_forget_gets_no_preview_and_reads_only_counts_after():
    """Cara reaches Brook's `desk` but did not make it: the preview is Forbidden to her.
    After Brook forgets, desk's audit log, which she reads, carries how many pages of other
    projects changed (here one, of Ada's closed `vault`) and nothing that names them."""
    with closing(Store(":memory:")) as store:
        ada, brook, cara = (
            store.sign_in("sso", f"sub-{n}", f"{n}@acme.example", n, issuer=ISSUER, join="default")
            for n in ("ada", "brook", "cara")
        )
        as_ada, as_brook, as_cara = (
            store.for_account(s.account_id, s.user.id) for s in (ada, brook, cara)
        )
        desk = as_brook.create_project("desk")
        note = filed(as_brook, desk.id, f"# Dispatch\n\n{GONE}\n")
        vault = as_ada.create_project("vault", everyone="none")
        borrowed(store, vault.id, "embargo", citing(1, GONE, passage(store, note)))
        with pytest.raises(Forbidden):
            Memory(as_cara, lint_gate).forget_preview("desk", note.id)
        Memory(as_brook, lint_gate).forget("desk", note.id)
        (event,) = [e for e in as_cara.list_audit() if e.action == "note.forgotten"]
        assert (event.detail["pages"], event.detail["elsewhere"]) == (2, 1)
        seen = json.dumps([e.model_dump(mode="json") for e in as_cara.list_audit()])
        assert not [w for w in ("vault", vault.id, "embargo", "Embargo") if w in seen]


TAILS = {
    "letters": lambda width: " " * 8 + "x" * (width - 8),
    "blanks": lambda width: " " * (width - 8) + "x" * 8,
    "tabs": lambda width: "\t" * ((width - 8) // 4) + "x" * 8,
}


@pytest.mark.parametrize("shape", sorted(TAILS))
def test_the_following_line_is_read_once_a_render_at_112_siblings(monkeypatch, shape):
    """Twice the reviewer's largest size, with a following line of letters, of blanks or of
    tabs: the line is worked out once a render and every question is built from that."""
    with closing(Store(":memory:")) as store:
        store.create_project("alpha")
        tail = TAILS[shape](3200)
        separator = under_steps(store, narrow(112), [tail])
        read, whole, renders = [], [], []
        work, ask, render = spans.following, spans.asked_line, compiler.left_to_run

        def following(line):
            read.append(len(line))
            return work(line)

        def asked_line(line, block):
            whole.append(not isinstance(line, spans.Following))
            return ask(line, block)

        def left_to_run(*args, **kwargs):
            renders.append(1)
            return render(*args, **kwargs)

        for module in (spans, erasure):
            monkeypatch.setattr(module, "following", following)
            monkeypatch.setattr(module, "asked_line", asked_line)
        monkeypatch.setattr(compiler, "left_to_run", left_to_run)
        Memory(store, lint_gate).forget("alpha", separator)
        assert whole and not any(whole)
        assert read == [len(tail)] * len(read) and 0 < len(read) <= len(renders) == 2
        assert tail in [claim.text for claim in page(store).claims]


def bounded(plan, forget: str):
    """(the plain loop's claims, the forget's claims, its renders) forgetting `forget`."""
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20_000))
    run = Run(plan, check_restore=False)
    try:
        assert set(run.status.values()) == {"saved"}, run.status
        slug = run.store.list_pages(run.pid)[0].slug
        loop = expected(run.store, run.pid, run.ids[forget])[slug]
        watch = Watch()
        with watch.on():
            Memory(run.store, lint_gate).forget(PROJECT, run.ids[forget])
        kept = [claim.text for claim in run.store.get_page(run.pid, slug).claims]
        return [c.text for c in loop.kept], kept, len(watch.renders)
    finally:
        run.close()


@pytest.mark.parametrize(
    ("plan", "forget", "renders"),
    [
        (lambda: cascade(CAP + 1, wide=True, example=EXAMPLE), "sep", CAP + 2),
        (lambda: sectioned(HARD + 2), "intro", HARD + 2),
    ],
    ids=["level 25", "level 33"],
)
def test_past_each_cap_only_claims_that_can_open_a_fence_leave_beyond_the_plain_loop(
    plan, forget, renders
):
    """Decision 204 at its second and third levels, reached by notes alone: every claim the
    forget takes that the plain loop keeps begins with indentation and has a line that opens
    a fence (read leniently), it takes nothing the plain loop drops back, and it renders
    under the ceiling of RENDERS_HARD + 3 (seen: 26 and 34 renders, 0.2 s and 1.3 s)."""
    plain, kept, made = bounded(plan(), forget)
    beyond = [text for text in plain if text not in kept]
    assert beyond and all(t[:1] in (" ", "\t") and opens_a_fence_line(t) for t in beyond)
    assert set(kept) <= set(plain) and made == renders <= erasure.RENDERS_HARD + 3
