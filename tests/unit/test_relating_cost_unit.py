"""What one read of a page's related pages costs, counted in statements, rows and SQLite's
steps (never timed), for pages of 10, 200 and 3,000 terms, as cairn.store.relating says it.

A read (Store.related_pages) runs, T being the terms looked up (at most relating.TERMS_MAX):
- 2 statements for the page and the index's table (the caller's; a narrowed view resolves
  its reach with statements of its own before them);
- 2 for the page's claims and their passages;
- ceil(T / AT_ONCE) to list the claims of the project that hold each term's words, the
  page's own among them, relations.CLAIMS_COMMON + 1 rows a term at most;
- 3 for the candidate claims (their rows, the rows of their passages, the passages), none
  when no claim of another page was listed;
- 1 for the title, CLAIMS_COMMON + 1 rows at most;
- 2 for what is given (the claims, the pages' heads) and 2 for the notes of the passages
  those claims cite and who wrote them (the caller's), none when nothing is related.
So 13 statements for a page of 10 terms, and 16 for one of 200 and for one of 3,000: past
TERMS_MAX terms the count is the same, whatever the page holds. One more, the 17th at most,
asks the index where a claim that mentions the page names its title, when such a claim is
given cut (the last test). One statement more, once
(SETTINGS): the first time a connection uses a workspace's index FTS5 loads its settings, as
tests/unit/test_search_cost_unit.py says.
The rows, C being the page's claims: 2 + 2C + the claims listed (each term's own claim among
them) + 3 for each candidate claim read + what is given.
The counts are those the store's connection reports to its trace callback and hands to its
row factory. What they cannot see is counted in SQLite's steps: the same read costs the
same steps when the project holds two thousand claims more, on other pages, that hold none
of the page's words; and beside 1,000 and 8,000 claims of another project that hold every
one of them, wherever those were saved. A page of the project costs a few steps in each
statement that lists claims by their words (PAGE_STEPS), whatever it holds.
"""

import contextlib
import re
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest
from test_search_steps_unit import PERIOD, merge

from cairn import relations
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.relations import RELATED_MAX, SHARES, WHY_MAX
from cairn.store import Claim, Store, relating

QUIET = {"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE"}
SHARED = 3  # terms of the page that one other page states too


def settings_loaded(statements: list[str]) -> int:
    """How many of `statements` are FTS5 loading an index's settings (SETTINGS)."""
    return sum(1 for s in statements if s.startswith("SELECT k, v FROM 'main'.'claim_search_"))


@contextlib.contextmanager
def watched(store: Store):
    """The statements the store runs (FTS5's own, which SQLite reports as comments, apart), the
    rows it is handed and the longest text in them, the claims FTS5 reads by row, and
    SQLite's steps."""
    seen = SimpleNamespace(statements=[], rows=0, claims=0, steps=0, longest=0)
    before = store._state.observers.rows

    def traced(statement: str) -> None:
        text = statement.lstrip()
        if text.startswith("--"):
            seen.claims += "_content'" in text
        elif text and text.split()[0].upper() not in QUIET:
            seen.statements.append(text)

    def rows(cursor, row):
        seen.rows += 1
        texts = [len(value) for value in row if isinstance(value, str)]
        seen.longest = max(seen.longest, *texts, 0)  # the longest text a statement hands back
        return row if before is None else before(cursor, row)

    def stepped() -> int:
        seen.steps += PERIOD
        return 0

    store._observe(trace=traced)
    store._observe(rows=rows)
    store._observe(progress=(stepped, PERIOD))
    try:
        yield seen
    finally:
        store._observe(trace=None)
        store._observe(rows=before)
        store._observe(progress=None)


def term(n: int) -> str:
    return f"`t{n:05d}.cfg`"


def project_of(folder, terms: int, bulk: int = 0) -> tuple[Store, str]:
    """A project whose page "asked" holds `terms` terms, ten to a claim; a page "share" that
    states the first SHARED of them in one claim; and `bulk` claims on other pages that hold
    none of their words."""
    store = Store(folder / "cairn.db")
    project = store.create_project("ops")
    memory = Memory(store, lint_gate)
    lines = [
        "- Step: " + " ".join(term(n) for n in range(first, min(terms, first + 10)))
        for first in range(0, terms, 10)
    ]
    memory.note("ops", "\n".join(lines) + "\n", title="Asked", topic="Asked")
    shared = " ".join(term(n) for n in range(SHARED))
    memory.note("ops", f"Brook sets {shared} first.\n", title="Share", topic="Share")
    for first in range(0, bulk, 500):
        claims = [
            Claim(id=f"cl_{n}", text=f"Quill keeps ledger {n} for Ada.", span_ids=())
            for n in range(first, min(bulk, first + 500))
        ]
        store.save_page(project.id, f"bulk-{first}", f"Ledger {first}", "", claims)
    merge(store)
    return store, project.id


def counted(store: Store, project_id: str):
    with watched(store) as seen:
        found = store.related_pages(project_id, "asked", RELATED_MAX)
    return found, seen


def test_the_stated_bounds():
    assert (relating.TERMS_MAX, relating.CLAIMS_MAX, relating.BYTES_MAX) == (256, 512, 1_000_000)
    assert (relating.AT_ONCE, relations.CLAIMS_COMMON, relations.COMMON) == (64, 64, 5)
    assert not hasattr(relating, "HERE_MAX") and not hasattr(relating, "LISTED_MAX")


@pytest.mark.parametrize(
    ("terms", "statements", "rows"),
    [(10, 13, 27), (200, 16, 255), (3_000, 16, 871)],
    ids=["10 terms", "200 terms", "3,000 terms"],
)
def test_a_read_costs_these_statements_and_rows(tmp_path, terms, statements, rows):
    store, project_id = project_of(tmp_path, terms)
    try:
        found, seen = counted(store, project_id)
    finally:
        store.close()
    looked = min(terms, relating.TERMS_MAX)
    claims = -(-terms // 10)
    assert found.left == relating.Left(
        terms_looked_up=looked,
        terms_not_looked_up=terms - looked,
        claims_read=1,
        claims_not_read=0,
        pages_left_out=0,
    )
    ((page),) = found.pages
    assert (page.slug, [r.kind for r in page.why], page.more) == ("share", [SHARES] * WHY_MAX, 0)
    listing = -(-looked // relating.AT_ONCE)
    assert statements == 2 + 2 + listing + 3 + 1 + 2 + 2
    assert len(seen.statements) - settings_loaded(seen.statements) == statements
    # the page and the index, the page's claims and passages, each term's own claim and
    # the SHARED claims of the other page listed, the one candidate three times, the two
    # claims given with one head and two passages, and the two notes
    assert rows == 2 + 2 * claims + looked + SHARED + 3 + (2 + 1 + 2) + 2
    assert seen.rows == rows
    # FTS5 read by row: the page's claims, the candidate, and the two given, each once or twice
    assert claims + 1 + 2 <= seen.claims <= 2 * (claims + 1 + 2)


def test_past_the_bound_on_terms_a_page_costs_what_one_at_the_bound_does(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    at, at_id = project_of(tmp_path / "a", 2_560)
    past, past_id = project_of(tmp_path / "b", 3_000)
    try:
        (found_at, seen_at), (found_past, seen_past) = counted(at, at_id), counted(past, past_id)
    finally:
        at.close()
        past.close()
    assert found_at.left.terms_looked_up == found_past.left.terms_looked_up == relating.TERMS_MAX
    not_looked_up = (found_at.left.terms_not_looked_up, found_past.left.terms_not_looked_up)
    assert not_looked_up == (2_304, 2_744)
    assert len(seen_past.statements) == len(seen_at.statements)
    # 44 claims more on the page, each read once with its passage: nothing else grows
    assert seen_past.rows - seen_at.rows == 2 * 44


def test_claims_that_hold_none_of_the_pages_words_cost_nothing(tmp_path):
    """Two thousand claims more in the project, on other pages: the same statements, the same
    rows, the same claims read, and the same steps, give or take the index's pages."""
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small, small_id = project_of(tmp_path / "small", 200)
    large, large_id = project_of(tmp_path / "large", 200, bulk=2_000)
    try:
        (found, seen), (found_large, seen_large) = (
            counted(small, small_id),
            counted(large, large_id),
        )
    finally:
        small.close()
        large.close()
    assert found_large.left == found.left and len(found_large.pages) == len(found.pages) == 1
    assert len(seen_large.statements) == len(seen.statements)
    assert (seen_large.rows, seen_large.claims) == (seen.rows, seen.claims)
    assert seen_large.steps <= 1.1 * seen.steps + 10 * PERIOD, (seen.steps, seen_large.steps)


# Claims of other projects of the workspace that hold the page's words (RT4): the work of a
# read is the page's and its own project's. Four projects, each a page "asked" of 64 terms and
# a page "share" that states SHARED of them, placed around the claims of a fifth project that
# hold every one of those words and the title: its claims saved after a project's pages
# ("after"), before them ("before"), between them ("between"), or between them with "asked"
# saved again last ("again"). The fifth project always holds one claim with none of the
# words, so that where it stands is the same whatever it holds.
LAYOUTS = ("after", "before", "between", "again")
FOREIGN = (0, 1_000, 8_000)
LISTED = 64  # the terms of each page "asked": one statement lists them
PAGE_STEPS = 150  # SQLite's steps a page of the project costs a statement that lists claims


def around(folder, foreign: int) -> tuple[Store, dict[str, str]]:
    """The workspace above with `foreign` claims of the fifth project that hold the words, and
    each layout's project id. Saved in this order: after's pages; between's "asked"; again's
    two pages; the fifth project's claims; before's pages; between's "share"; again's "asked"
    a second time."""
    store = Store(folder / "cairn.db")
    memory = Memory(store, lint_gate)
    projects = {layout: store.create_project(layout).id for layout in LAYOUTS}
    other = store.create_project("elsewhere")
    lines = [
        "- Step: " + " ".join(term(n) for n in range(first, first + 8))
        for first in range(0, LISTED, 8)
    ]

    def asked(layout: str, again: bool = False) -> None:
        text = "\n".join(lines) + ("\n- Again: the same steps.\n" if again else "\n")
        memory.note(layout, text, title="Asked", topic="Asked")

    def share(layout: str) -> None:
        shared = " ".join(term(n) for n in range(SHARED))
        memory.note(layout, f"Brook sets {shared} first.\n", title="Share", topic="Share")

    asked("after")
    share("after")
    asked("between")
    asked("again")
    share("again")
    held = " ".join(term(n).strip("`") for n in range(LISTED)) + " Asked"
    claims = [Claim(id="cl_none", text="Quill keeps the ledger.", span_ids=())]
    claims += [Claim(id=f"cl_{n}", text=f"{held} {n}", span_ids=()) for n in range(foreign)]
    for first in range(0, len(claims), 500):
        store.save_page(
            other.id, f"bulk-{first}", f"Ledger {first}", "", claims[first : first + 500]
        )
    asked("before")
    share("before")
    share("between")
    asked("again", again=True)
    merge(store)
    return store, projects


def shown(found: relating.Related) -> list:
    """An answer without its ids, which each store makes its own."""
    return [(p.slug, [(r.kind, r.term, r.pages) for r in p.why], p.more) for p in found.pages]


@pytest.fixture(scope="module")
def beside(tmp_path_factory):
    """For each count of FOREIGN: each layout's answer and what its read cost."""
    costs: dict[int, dict[str, tuple]] = {}
    for foreign in FOREIGN:
        store, projects = around(tmp_path_factory.mktemp(f"foreign-{foreign}"), foreign)
        try:
            costs[foreign] = {layout: counted(store, id_) for layout, id_ in projects.items()}
        finally:
            store.close()
    return costs


@pytest.mark.parametrize("layout", LAYOUTS)
def test_claims_of_another_project_that_hold_the_pages_words_cost_no_step(beside, layout):
    """1,000 and 8,000 claims of another project that hold every word of the page and its
    title, wherever they were saved: the same answer, statements, rows, claims read by row
    and steps as with none, give or take the index's pages."""
    found, alone = beside[0][layout]
    assert [page.slug for page in found.pages] == ["share"] and found.left.complete
    for foreign in FOREIGN[1:]:
        found_beside, seen = beside[foreign][layout]
        assert (shown(found_beside), found_beside.left) == (shown(found), found.left), foreign
        assert len(seen.statements) == len(alone.statements), foreign
        assert (seen.rows, seen.claims) == (alone.rows, alone.claims), foreign
        assert seen.steps <= 1.1 * alone.steps + 10 * PERIOD, (foreign, alone.steps, seen.steps)


def test_each_page_of_the_project_costs_a_few_seeks_whatever_it_holds(tmp_path):
    """What the project's pages cost a read: each is found as a range of rows, a few seeks, in
    each statement that lists claims by their words (four for 200 terms, and the title's),
    whatever it holds; its claims that hold none of the words cost nothing."""
    costs = {}
    for pages, claims in ((0, 0), (40, 1), (40, 50)):
        folder = tmp_path / f"{pages}-{claims}"
        folder.mkdir()
        store, project_id = project_of(folder, 200)
        try:
            for k in range(pages):
                more = [
                    Claim(id=f"cl_{k}_{n}", text=f"Quill keeps ledger {n} for Ada.", span_ids=())
                    for n in range(claims)
                ]
                store.save_page(project_id, f"bulk-{k}", f"Ledger {k}", "", more)
            merge(store)
            costs[pages, claims] = counted(store, project_id)[1]
        finally:
            store.close()
    alone, few, many = costs[0, 0], costs[40, 1], costs[40, 50]
    assert len(few.statements) == len(many.statements) == len(alone.statements)
    assert (few.rows, few.claims) == (many.rows, many.claims) == (alone.rows, alone.claims)
    assert many.steps <= 1.1 * few.steps + 10 * PERIOD, (few.steps, many.steps)
    listing = [s for s in alone.statements if " MATCH " in s and "BETWEEN" in s]
    assert len(listing) == 4 + 1
    assert few.steps - alone.steps <= 40 * len(listing) * PAGE_STEPS, (alone.steps, few.steps)


def test_the_index_is_asked_for_a_terms_claims_within_the_projects_rows(tmp_path):
    """The plan of the statements that list claims by their words (the terms', the title's):
    the account's index is searched by the match and by a range of rows, one range of the
    project's at a time (cairn.store.relating), never walked from its first match on."""
    store, projects = around(tmp_path, 0)
    try:
        _, seen = counted(store, projects["between"])
        listing = [s for s in seen.statements if " MATCH " in s and "BETWEEN" in s]
        plans = [
            [step[-1] for step in store._con.execute("EXPLAIN QUERY PLAN " + statement)]
            for statement in listing
        ]
    finally:
        store.close()
    assert len(listing) == 2  # the 64 terms, and the title
    for plan in plans:
        searched = [step for step in plan if "VIRTUAL TABLE INDEX" in step]
        # FTS5 says what it was given: M a match, > and < the least and the greatest row
        assert searched and all(re.search(r"INDEX \d+:M\d*><", step) for step in searched), plan


def test_the_bounds_met_are_counted_and_the_read_stays_inside_them(tmp_path, monkeypatch):
    """A term more claims hold than make it common costs the rows listed and no claim; past the
    claims read in all, a term's claims are not read, and are counted."""
    monkeypatch.setattr(relations, "CLAIMS_COMMON", 8)
    monkeypatch.setattr(relating, "CLAIMS_MAX", 10)
    store = Store(tmp_path / "cairn.db")
    try:
        project = store.create_project("ops")
        memory = Memory(store, lint_gate)

        def file(title: str, text: str) -> None:
            memory.note("ops", text, title=title, topic=title)

        def lines(count: int, term: str, mark: str) -> str:
            return "\n".join(f"- {mark} {n} names {term}." for n in range(count)) + "\n"

        file("Asked", "Use `a.cfg`, `b.cfg`, `d.cfg` and `e.cfg`.\n")
        for n in range(3):  # a.cfg: one claim on each of three pages
            file(f"First {n}", f"Page {n} sets `a.cfg`.\n")
        file("Second", lines(12, "`b.cfg`", "Line"))  # b.cfg: thirteen claims hold it, with ours
        file("Third 0", lines(4, "`d.cfg`", "Row"))  # d.cfg: eight claims with ours, on two pages
        file("Third 1", lines(3, "`d.cfg`", "Mark"))
        file("Fourth 0", lines(4, "`e.cfg`", "Step"))  # e.cfg: the same, read after d.cfg
        file("Fourth 1", lines(3, "`e.cfg`", "Turn"))
        with watched(store) as seen:
            found = store.related_pages(project.id, "asked", RELATED_MAX)
    finally:
        store.close()
    # a.cfg's 3 claims and d.cfg's 7 are read; e.cfg's 7 would pass 10, so none of them is
    assert found.left == relating.Left(
        terms_looked_up=4,
        terms_not_looked_up=0,
        claims_read=10,
        claims_not_read=7,
        pages_left_out=0,
    )
    assert not found.left.complete
    related = {page.slug: [(r.term, r.pages) for r in page.why] for page in found.pages}
    assert related == {
        "first-0": [("a.cfg", 4)],
        "first-1": [("a.cfg", 4)],
        "first-2": [("a.cfg", 4)],
        "third-0": [("d.cfg", 3)],
        "third-1": [("d.cfg", 3)],
    }
    listed = 4 + 9 + 8 + 8  # ours and theirs; b.cfg's stop one past what makes it common
    given = (1 + 5) + 5 + 6  # the claims, the pages' heads, the passages the claims cite
    assert seen.rows == 2 + 2 * 1 + listed + 3 * 10 + given + 6


# What is read of a long claim: bytes, by the handles the read opens


class Handle:
    """A handle on a stored value, counting the bytes read through it."""

    def __init__(self, blob) -> None:
        self.blob, self.size, self.read_bytes = blob, len(blob), 0

    def __enter__(self):
        return self

    def __exit__(self, *raised) -> None:
        self.blob.close()

    def __len__(self) -> int:
        return self.size

    def read(self, length: int = -1) -> bytes:
        data = self.blob.read(length)
        self.read_bytes += len(data)
        return data


class Handles:
    """The connection of one read, keeping each handle it opens on a stored value."""

    def __init__(self, con) -> None:
        self.con, self.opened = con, []

    def execute(self, *arguments):
        return self.con.execute(*arguments)

    def blobopen(self, table: str, column: str, row: int, **named):
        handle = Handle(self.con.blobopen(table, column, row, **named))
        self.opened.append((table, column, handle))
        return handle


def test_a_claim_of_any_length_is_read_to_the_bound_and_a_passage_is_measured_unread(
    tmp_path, monkeypatch
):
    """A claim of 60,000 characters on each side of a reason: of each claim's text the read
    takes the bytes that TEXT_MAX + 1 characters can fill and no more, through a handle;
    no statement of this read names a claim's text (the one that does, for a mention, is
    the next test's); and a passage is measured by a handle that reads nothing of it (its
    text is then read by the statement that reads the passages)."""
    store = Store(tmp_path / "cairn.db")
    project = store.create_project("ops")
    memory = Memory(store, lint_gate)
    filler = "Brook keeps the ledger and Ada reads it at dawn; " * 1_200
    assert len(filler) > 58_000
    memory.note("ops", filler + "Ada pins `quill.lock`.\n", title="Asked", topic="Asked")
    memory.note("ops", filler + "Quill reads `quill.lock`.\n", title="Other", topic="Other")
    reads = []
    whole = relating.related

    def with_handles(con, *arguments):
        reads.append(Handles(con))
        return whole(reads[-1], *arguments)

    monkeypatch.setattr(relating, "related", with_handles)
    with watched(store) as seen:
        found = store.related_pages(project.id, "asked", RELATED_MAX)
    (reason,) = found.pages[0].why
    assert reason.here.cut and reason.there.cut and len(reason.there.text) == relations.TEXT_MAX
    (read,) = reads
    claims = [handle for table, column, handle in read.opened if table.endswith("_content")]
    passages = [handle for table, column, handle in read.opened if table == "spans"]
    assert {column for table, column, _ in read.opened} == {"c5", "text"}
    assert len(claims) == 2 and min(handle.size for handle in claims) > 58_000
    bound = 4 * (relations.TEXT_MAX + 1)
    assert [handle.read_bytes for handle in claims] == [bound, bound]
    assert len(passages) == 1 and passages[0].size > 58_000 and passages[0].read_bytes == 0
    (index,) = {table.removesuffix("_content") for table, _, _ in read.opened} - {"spans"}
    ours = [s for s in seen.statements if index in s]
    assert ours and not [s for s in ours if ".text" in s]  # under the index's name or another
    store.close()


@pytest.mark.parametrize(
    "named",
    ["the Quill ledger.", "the Quill" + " \u2014" * 900 + " ledger."],
    ids=["side by side", "1,800 characters apart"],
)
def test_a_cut_mention_costs_one_statement_more_and_hands_back_a_part_of_the_claim(tmp_path, named):
    """A page mentioned by a claim of 60,000 characters, which names it at its end. Beside a
    claim that mentions it in a sentence, the read runs one statement more: the one that
    asks the index where it matched the title. The index reads the long claim whole itself,
    once, to mark the match; no statement hands back more of it than the line needs, 3 *
    LINE_MAX + 1 characters, however far apart the title's words stand; and the claim's text
    is read by a handle to the bound, as any claim's."""
    counts = {}
    for size, filler in (("short", ""), ("long", "Brook keeps the ledger at dawn; " * 1_900)):
        store = Store(tmp_path / f"{size}.db")
        project = store.create_project("ops")
        memory = Memory(store, lint_gate)
        memory.note("ops", "Ada balances it at dusk.\n", title="Quill ledger", topic="Quill ledger")
        memory.note("ops", filler + "Then she opens " + named + "\n", title="Other", topic="Other")
        merge(store)
        with watched(store) as seen:
            found = store.related_pages(project.id, "quill-ledger", RELATED_MAX)
        (reason,) = found.pages[0].why
        assert reason.kind == "mentioned_by" and reason.there.cut == (
            size == "long" or len(named) > 500
        )
        counts[size] = (len(seen.statements) - settings_loaded(seen.statements), seen.longest)
        if size == "long":
            assert len(reason.there.line) == relations.LINE_MAX and reason.there.line_cut
            assert reason.there.line.startswith("Quill") or reason.there.line.endswith(named)
            asked = [s for s in seen.statements if "highlight(" in s]
            assert len(asked) == 1 and seen.longest <= 3 * relations.LINE_MAX + 1
            # the index marks the claim once a row: the SELECT that asks it is run apart
            # from the one that reads the marks twice, not folded into it
            with closing(sqlite3.connect(tmp_path / "long.db")) as con:
                plan = [step[-1] for step in con.execute("EXPLAIN QUERY PLAN " + asked[0])]
            assert [step for step in plan if step.startswith(("CO-ROUTINE", "MATERIALIZE"))]
        store.close()
    if len(named) < 500:
        # the caller's 2; the page's claims and passages, 2; no term to list; the title, 1;
        # what is given, 2; the notes of the passages given and who wrote them, 2
        assert counts["short"][0] == 9 and counts["long"][0] == 10
    else:  # both claims are cut: both ask the index
        assert counts["short"][0] == counts["long"][0] == 10
