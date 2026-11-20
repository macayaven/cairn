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
TERMS_MAX terms the count is the same, whatever the page holds. One statement more, once
(SETTINGS): the first time a connection uses a workspace's index FTS5 loads its settings, as
tests/unit/test_search_cost_unit.py says.
The rows, C being the page's claims: 2 + 2C + the claims listed (each term's own claim among
them) + 3 for each candidate claim read + what is given.
The counts are those the store's connection reports to its trace callback and hands to its
row factory. What they cannot see is counted in SQLite's steps: the same read costs the
same steps when the project holds two thousand claims more, on other pages, that hold none
of the page's words.
"""

import contextlib
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
    rows it is handed, the claims FTS5 reads by row, and SQLite's steps."""
    seen = SimpleNamespace(statements=[], rows=0, claims=0, steps=0)
    before = store._state.observers.rows

    def traced(statement: str) -> None:
        text = statement.lstrip()
        if text.startswith("--"):
            seen.claims += "_content'" in text
        elif text and text.split()[0].upper() not in QUIET:
            seen.statements.append(text)

    def rows(cursor, row):
        seen.rows += 1
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
