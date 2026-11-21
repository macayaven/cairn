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
TERMS_MAX terms the count is the same, whatever the page holds. A claim of another page that
holds the title's words is read as a candidate is, and one statement more asks the index
where it matched the title: 17 at the most, as before, since no statement asks for the
line of a claim given cut any more (the last tests). One statement more, once
(SETTINGS): the first time a connection uses a workspace's index FTS5 loads its settings, as
tests/unit/test_search_cost_unit.py says.
The rows, C being the page's claims: 2 + 1 (the project's span) + 2C + the claims listed (each
term's own claim among them) + 3 for each candidate claim read + what is given.
The counts are those the store's connection reports to its trace callback and hands to its
row factory. What they cannot see is counted in SQLite's steps: the same read costs the
same steps when the project holds two thousand claims more, on other pages, that hold none
of the page's words, and beside claims of another project that hold every one of them and
the title saved outside the project's span, before its pages or after them; claims saved
inside it cost ROW_STEPS steps at most for each row of the index handed over, ROWS_MAX rows
in all (`visited`), a claim of the project that names the title given cut among what is
read. A page of the project costs a few steps once a read, to find the span (PAGE_STEPS),
whatever it holds.
"""

import contextlib
import re
import sqlite3
import threading
from contextlib import closing
from types import SimpleNamespace

import pytest
from test_search_steps_unit import PERIOD, merge

from cairn import relations, server, spans
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.relations import RELATED_MAX, SHARES, WHY_MAX
from cairn.store import Claim, Store, relating

QUIET = {"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE"}
ROWS_MAX = 150_000  # the rows of the index a read visits in all (relating.ROWS_MAX)
SHARED = 3  # terms of the page that one other page states too


def settings_loaded(statements: list[str]) -> int:
    """How many of `statements` are FTS5 loading an index's settings (SETTINGS)."""
    return sum(1 for s in statements if s.startswith("SELECT k, v FROM 'main'.'claim_search_"))


@contextlib.contextmanager
def watched(store: Store):
    """The statements the store runs (FTS5's own, which SQLite reports as comments, apart), the
    rows it is handed and the longest text in them, the claims FTS5 reads by row, SQLite's
    steps, and the rows of the index the read was handed for its terms and title (`visited`,
    relating.ROWS_MAX at most)."""
    seen = SimpleNamespace(statements=[], rows=0, claims=0, steps=0, longest=0, counts=[])
    before = store._state.observers.rows
    counting = relating._Visits

    class Counted(counting):
        def __init__(self) -> None:
            super().__init__()
            seen.counts.append(self)

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
    relating._Visits = Counted
    try:
        yield seen
    finally:
        relating._Visits = counting
        store._observe(trace=None)
        store._observe(rows=before)
        store._observe(progress=None)
        seen.visited = sum(count.total for count in seen.counts)


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


def run(seen) -> int:
    """The statements of a read, FTS5's loading of an index's settings (SETTINGS) aside, which
    a connection does again after a write to the index."""
    return len(seen.statements) - settings_loaded(seen.statements)


def test_the_stated_bounds():
    assert (relating.TERMS_MAX, relating.CLAIMS_MAX, relating.BYTES_MAX) == (256, 512, 750_000)
    assert relating.ROWS_MAX == ROWS_MAX
    assert (relating.AT_ONCE, relations.CLAIMS_COMMON, relations.COMMON) == (64, 64, 5)
    assert not hasattr(relating, "HERE_MAX") and not hasattr(relating, "LISTED_MAX")


def test_the_documents_say_what_bounds_a_reads_rows_and_what_is_not_counted():
    contract = " ".join(server.__doc__.split())
    assert (
        "and those not looked up once the read has been handed 150,000 rows of the index for"
        " the terms and the title (the title then counting as one)" in contract
    )
    assert (
        "The rows of the index a read is handed are bounded whatever the workspace holds:"
        " where other projects hold a page's terms in very many claims, the answer can be"
        " incomplete, and says so; each reason it gives is true and counts the pages it would"
        " count with nothing left out, though an incomplete answer can hold other true reasons"
        " than a complete one would show first." in contract
    )
    module = " ".join(relating.__doc__.split())
    assert "whatever other projects hold" not in module
    assert "The rows of the index a read is handed are bounded, whatever the workspace" in module
    assert "so ROWS_MAX does not bound that work (measured beside it)" in module


def test_the_fact_reader_is_handed_the_pages_passages_and_the_bound_in_bytes_of_others(
    tmp_path, monkeypatch
):
    """Where a read's time goes: reading the terms of passages (cairn.facts.extract_facts on
    each passage whole). The page's own first passages are read, and the candidates' to
    BYTES_MAX bytes and no further. Pages of 40 passages of about 3,000 bytes, each stating
    one of the page's 256 terms, more of them than BYTES_MAX holds and fewer claims than
    CLAIMS_MAX: a read at the largest limit hands the reader the page's passages and
    BYTES_MAX bytes at most of the others', within 17 statements, and counts the claims it
    did not read."""
    store = Store(tmp_path / "cairn.db")
    project = store.create_project("ops")
    memory = Memory(store, lint_gate)
    filler = "Brook keeps the ledger and Ada reads it at dawn; " * 60
    asked = "\n\n".join(" ".join(term(n) for n in range(f, f + 8)) for f in range(0, 256, 8))
    memory.note("ops", asked + "\n", title="Asked", topic="Asked")
    pages = relating.BYTES_MAX // (40 * 3_000) + 2
    assert 40 * pages <= relating.CLAIMS_MAX  # the bytes bind, not the count of claims
    for k in range(pages):
        lines = [f"Page {k} sets {term((32 * k + i) % 256)} here. {filler}" for i in range(40)]
        memory.note("ops", "\n\n".join(lines) + "\n", title=f"Other {k}", topic=f"Other {k}")
    own = {claim.span_ids[0] for claim in store.get_page(project.id, "asked").claims}
    handed: dict[str, int] = {}
    reader = relations.extract_facts

    def counted_reader(span):
        handed[span.id] = len(span.text.encode())
        return reader(span)

    monkeypatch.setattr(relations, "extract_facts", counted_reader)
    try:
        with watched(store) as seen:
            found = store.related_pages(project.id, "asked", RELATED_MAX)
    finally:
        store.close()
    assert set(handed) >= own
    others = sum(size for span_id, size in handed.items() if span_id not in own)
    assert relating.BYTES_MAX - 3_100 < others <= relating.BYTES_MAX, others
    assert found.left.claims_read == len(handed) - len(own) <= relating.CLAIMS_MAX
    assert found.left.claims_not_read == pages * 40 - found.left.claims_read > 0
    assert len(seen.statements) - settings_loaded(seen.statements) <= 17


def test_a_passage_listed_for_a_term_and_for_the_title_is_read_once_and_charged_once(
    tmp_path, monkeypatch
):
    """Four pages each state a term of the page and name its title in prose, in one claim of
    one passage: the claim is listed for the term and for the title. Within the read each
    passage's facts are extracted once, kept for that read alone, and its bytes are charged
    to BYTES_MAX once: with the bound set to the four passages' bytes, all are read."""
    store = Store(tmp_path / "cairn.db")
    project = store.create_project("ops")
    memory = Memory(store, lint_gate)
    memory.note("ops", f"Ada sets {term(1)} and {term(2)}.\n", title="Asked", topic="Asked")
    for k in range(4):
        text = f"Brook {k} reads Asked and sets {term(1)} here.\n"
        memory.note("ops", text, title=f"Other {k}", topic=f"Other {k}")
    others = [
        claim.span_ids[0]
        for k in range(4)
        for claim in store.get_page(project.id, f"other-{k}").claims
    ]
    sizes = {span.id: len(span.text.encode()) for span in store.get_spans(others).values()}
    assert len(sizes) == 4
    monkeypatch.setattr(relating, "BYTES_MAX", sum(sizes.values()))
    extracted: dict[str, int] = {}
    reader = relations.extract_facts

    def counted_reader(span):
        extracted[span.id] = extracted.get(span.id, 0) + 1
        return reader(span)

    monkeypatch.setattr(relations, "extract_facts", counted_reader)
    try:
        found = store.related_pages(project.id, "asked", RELATED_MAX)
        again = store.related_pages(project.id, "asked", RELATED_MAX)
    finally:
        store.close()
    assert found is not None and again is not None
    assert found.left.complete and found.left.claims_read == 4 == len(found.pages)
    for page in found.pages:
        assert sorted(reason.kind for reason in page.why) == [relations.MENTIONED_BY, SHARES]
    # each passage once a read: the page's own and the four others', in each of the two reads
    assert set(extracted) >= set(sizes) and set(extracted.values()) == {2}, extracted


def test_a_passage_the_page_and_another_page_both_cite_is_read_once_and_not_charged(
    tmp_path, monkeypatch
):
    """A page saved by hand whose claim cites the page's own passage: the read holds that
    passage whole already, as the page's, so it is not read again, its facts are extracted
    once, and it is not charged to BYTES_MAX, which counts other pages' passages: with a
    bound it does not fit in, the claim is read and the pages share both terms."""
    store = Store(tmp_path / "cairn.db")
    project = store.create_project("ops")
    memory = Memory(store, lint_gate)
    memory.note("ops", f"Ada sets {term(1)} and {term(2)}.\n", title="Asked", topic="Asked")
    (claim,) = store.get_page(project.id, "asked").claims
    store.save_page(
        project.id,
        "copy",
        "Copy",
        "",
        [Claim(id="cl_copy", text=claim.text, span_ids=claim.span_ids)],
    )
    (span,) = store.get_spans(claim.span_ids[:1]).values()
    monkeypatch.setattr(relating, "BYTES_MAX", len(span.text.encode()) - 1)
    extracted: dict[str, int] = {}
    reader = relations.extract_facts

    def counted_reader(passage):
        extracted[passage.id] = extracted.get(passage.id, 0) + 1
        return reader(passage)

    monkeypatch.setattr(relations, "extract_facts", counted_reader)
    try:
        found = store.related_pages(project.id, "asked", RELATED_MAX)
    finally:
        store.close()
    assert found is not None and found.left.complete and found.left.claims_read == 1
    assert [page.slug for page in found.pages] == ["copy"]
    assert [reason.kind for reason in found.pages[0].why] == [SHARES, SHARES]
    assert extracted == {span.id: 1}


@pytest.mark.parametrize(
    ("terms", "statements", "rows"),
    [(10, 13, 28), (200, 16, 256), (3_000, 16, 872)],
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
    # the page and the index, the project's span, the page's claims and passages, each
    # term's own claim and the SHARED claims of the other page listed, the one candidate
    # three times, the two claims given with one head and two passages, and the two notes
    assert rows == 2 + 1 + 2 * claims + looked + SHARED + 3 + (2 + 1 + 2) + 2
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


# Claims of other projects of the workspace that hold the page's words (RT4). The index is
# asked for a term over the project's span, its first row to its last: a claim of another
# project outside it is never walked, and one inside it is handed over, passed over by its
# project and counted, ROWS_MAX rows in all. Four projects, each a page "asked" of 64 terms
# and a page "share" that states SHARED of them, placed around the claims of a fifth project
# that hold every one of those words and the title: its claims saved after a project's pages
# ("after"), before them ("before"), between them ("between"), or between them with "asked"
# saved again last ("again"). The fifth project always holds one claim with none of the
# words, so that where it stands is the same whatever it holds.
LAYOUTS = ("after", "before", "between", "again")
OUTSIDE = ("after", "before")  # the fifth project's claims fall outside the project's span
FOREIGN = (0, 1_000, 8_000)
LISTED = 64  # the terms of each page "asked": one statement lists them
PAGE_STEPS = 60  # SQLite's steps a page of the project costs a read: its span, two seeks
ROW_STEPS = 25  # SQLite's steps a row of the index the read is handed costs it, at most


MENTION = "Brook reads Asked before each run, and keeps the ledger by it. " * 12  # one claim


def around(folder, foreign: int) -> tuple[Store, dict[str, str]]:
    """The workspace above with `foreign` claims of the fifth project that hold the words, and
    each layout's project id. Each project holds a page "mention" too, one long claim that
    names the title in prose, given cut with the line that names it. Saved in this order:
    after's pages; between's "asked"; again's three pages; the fifth project's claims;
    before's pages; between's "share" and "mention"; again's "asked" a second time."""
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
        memory.note(layout, MENTION + "\n", title="Mention", topic="Mention")

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


def reasons_of(found: relating.Related) -> set:
    """Each reason given, as an answer says it: its page, kind, term and count of pages, and
    the text and line of each claim it rests on."""

    def said(claim: relating.Stated | None) -> tuple | None:
        return None if claim is None else (claim.text, claim.cut, claim.line)

    return {
        (page.slug, reason.kind, reason.term, reason.pages, said(reason.here), said(reason.there))
        for page in found.pages
        for reason in page.why
    }


def part_of(found: relating.Related, whole: relating.Related) -> None:
    """`found` against `whole`, a complete answer of the same page, where nothing but ROWS_MAX
    leaves anything out of `found` (the fixtures here, within CLAIMS_MAX and BYTES_MAX): past
    ROWS_MAX it gives a part of whole's reasons, each with the same term, count of pages and
    claims, and says it is not complete by counting terms not looked up; within it, the same
    answer. (Its counts of claims read and of reasons past WHY_MAX or `limit` may be fewer.
    Where CLAIMS_MAX or BYTES_MAX bind too, a term not looked up leaves room for another
    term's claims, and an incomplete answer can hold true reasons a complete one would not
    show first.)"""
    assert whole.left.complete
    if found.left.terms_not_looked_up == whole.left.terms_not_looked_up:
        assert (reasons_of(found), found.left) == (reasons_of(whole), whole.left)
    else:
        assert found.left.terms_not_looked_up > whole.left.terms_not_looked_up
        assert not found.left.complete
        assert reasons_of(found) <= reasons_of(whole)


@pytest.mark.parametrize("layout", LAYOUTS)
def test_claims_of_another_project_that_hold_the_pages_words(beside, layout):
    """1,000 and 8,000 claims of another project that hold every word of the page and its
    title. Saved outside the project's span, they cost no step: the same answer, statements,
    rows, claims read by row and steps as with none, give or take the index's pages. Saved
    inside it, the index hands each to the read for each term, which passes it over by its
    project: ROW_STEPS steps a row at most, ROWS_MAX rows in all; within that, the same
    answer, and past it, with these claims, a part of that answer, which says it is not
    complete."""
    found, alone = beside[0][layout]
    assert sorted(page.slug for page in found.pages) == ["mention", "share"]
    assert found.left.complete
    (mention,) = [page for page in found.pages if page.slug == "mention"]
    (named,) = mention.why  # the mention's path: listed, read, marked by the index, its line
    assert named.kind == relations.MENTIONED_BY and named.there.cut
    assert named.there.line is not None and "Asked" in named.there.line
    for foreign in FOREIGN[1:]:
        found_beside, seen = beside[foreign][layout]
        assert seen.visited <= relating.ROWS_MAX
        if layout in OUTSIDE:
            assert (shown(found_beside), found_beside.left) == (shown(found), found.left)
            assert len(seen.statements) == len(alone.statements), foreign
            assert (seen.rows, seen.claims, seen.visited) == (
                alone.rows,
                alone.claims,
                alone.visited,
            )
            limit = 1.1 * alone.steps + 10 * PERIOD
        else:
            part_of(found_beside, found)
            assert len(seen.statements) <= len(alone.statements), foreign
            limit = alone.steps + (seen.visited - alone.visited) * ROW_STEPS + 10 * PERIOD
        assert seen.steps <= limit, (foreign, alone.steps, seen.steps, seen.visited)
    # within ROWS_MAX at 1,000 (65 rows each: 64 terms and the title), past it at 8,000
    if layout not in OUTSIDE:
        assert beside[1_000][layout][0].left.complete
        assert not beside[8_000][layout][0].left.complete


def test_each_page_of_the_project_costs_a_few_seeks_whatever_it_holds(tmp_path):
    """What the project's pages cost a read: each is a range of rows, whose first and last
    are two seeks, once a read, to find the project's span; whatever it holds, and its claims
    that hold none of the words cost nothing. The statements that list claims by their
    words (four for 200 terms, and the title's) ask the index over that span."""
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
    assert few.steps - alone.steps <= 40 * PAGE_STEPS, (alone.steps, few.steps)


def test_the_index_is_asked_for_a_terms_claims_within_the_projects_rows(tmp_path):
    """The plan of the statements that list claims by their words (the terms', the title's):
    the account's index is searched by the match and by a range of rows, the project's span
    (cairn.store.relating), never walked from its first match on."""
    store, projects = around(tmp_path, 0)
    try:
        _, seen = counted(store, projects["between"])
        listing = [s for s in seen.statements if " MATCH " in s and "BETWEEN" in s]
        relating._counting(store._con)  # the functions the statements count rows by
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
    # Which claims listed for the title are their passage's text: each by its own row (=),
    # the project's rows alone, and no match asked.
    (judged,) = [s for s in seen.statements if "= spans.text" in s]
    with closing(Store(tmp_path / "cairn.db")) as again:
        plan = [step[-1] for step in again._con.execute("EXPLAIN QUERY PLAN " + judged)]
    (searched,) = [step for step in plan if step.startswith("SCAN claim_search_")]
    assert re.search(r"VIRTUAL TABLE INDEX \d+:=$", searched), plan


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
    assert seen.rows == 2 + 1 + 2 * 1 + listed + 3 * 10 + given + 6


def test_past_the_rows_a_read_visits_the_term_being_asked_and_those_after_it_are_not_looked_up(
    tmp_path, monkeypatch
):
    """The rows of the index a read is handed, ROWS_MAX in all, the project's and another
    project's alike, are counted as they are handed over: the terms in the order they are
    looked up, the title last. The term being asked when the count reaches ROWS_MAX, and
    every one after it, are not asked or not taken: counted in terms_not_looked_up, the title
    as one more, they give no reason, and the answer is not complete. What it gives here is a
    part of the whole answer, each reason with the same count of pages and the same claims."""
    store = Store(tmp_path / "cairn.db")
    project = store.create_project("ops")
    other = store.create_project("elsewhere")
    memory = Memory(store, lint_gate)

    def file(title: str, text: str) -> None:
        memory.note("ops", text, title=title, topic=title)

    file("Asked", "Use `a.cfg`, `bb.cfg` and `ccc.cfg`.\n")  # looked up in that order
    # ten claims of another project that hold bb.cfg's words, among the project's rows
    claims = [Claim(id=f"cl_bb{n}", text=f"Ada keeps bb.cfg {n}.", span_ids=()) for n in range(10)]
    store.save_page(other.id, "theirs", "Theirs", "", claims)
    for n, said in enumerate(("`a.cfg`", "`bb.cfg`", "`ccc.cfg`")):
        file(f"First {n}", f"Page {n} sets {said}.\n")
    file("Mention", "Brook reads Asked daily.\n")
    answers = {}
    try:
        for rows in (100, 18, 17, 16, 10):
            monkeypatch.setattr(relating, "ROWS_MAX", rows)
            with watched(store) as seen:
                answers[rows] = store.related_pages(project.id, "asked", RELATED_MAX), seen
    finally:
        store.close()
    whole, seen = answers[100]
    # a.cfg: 2 rows (ours, First 0's); bb.cfg: 12 (ours, First 1's, the ten); ccc.cfg: 2; the
    # title: 1 (Mention's)
    assert seen.visited == 17
    assert whole.left == relating.Left(3, 0, 4, 0, 0)
    assert {page.slug for page in whole.pages} == {"first-0", "first-1", "first-2", "mention"}
    # (terms looked up, terms not looked up, the pages given): 18 rows are enough; at 17 the
    # title is being asked when the count reaches it; at 16, ccc.cfg; at 10, bb.cfg
    expected = {
        18: (3, 0, {"first-0", "first-1", "first-2", "mention"}),
        17: (3, 1, {"first-0", "first-1", "first-2"}),
        16: (2, 2, {"first-0", "first-1"}),
        10: (1, 3, {"first-0"}),
    }
    for rows, (looked, not_looked, pages) in expected.items():
        found, seen = answers[rows]
        assert seen.visited <= rows
        assert (found.left.terms_looked_up, found.left.terms_not_looked_up) == (looked, not_looked)
        assert {page.slug for page in found.pages} == pages, rows
        part_of(found, whole)


def test_a_read_that_fails_while_it_lists_claims_leaves_the_next_read_whole(tmp_path):
    """A read whose statement that lists claims fails (here SQLite interrupts it once it has
    begun) raises that error; the next read on the same store counts the rows of the index
    it is handed from none, and answers as it did before."""
    store, project_id = project_of(tmp_path, 200)
    try:
        found, seen = counted(store, project_id)
        listing = threading.Event()

        def traced(statement: str) -> None:
            if " MATCH " in statement and "BETWEEN" in statement:
                listing.set()

        store._observe(trace=traced)
        store._observe(progress=(lambda: int(listing.is_set()), PERIOD))
        try:
            with pytest.raises(sqlite3.OperationalError, match="interrupted"):
                store.related_pages(project_id, "asked", RELATED_MAX)
        finally:
            store._observe(trace=None)
            store._observe(progress=None)
        again, seen_again = counted(store, project_id)
    finally:
        store.close()
    assert listing.is_set()
    assert (shown(again), again.left) == (shown(found), found.left)
    assert seen_again.visited == seen.visited > 0


def test_a_read_that_fails_while_a_statement_is_half_read_leaves_the_next_read_whole(
    tmp_path, monkeypatch
):
    """A read that fails in Python while a statement of its connection is read in part (here
    the second passage it reads cannot be made), the error kept: the statement is left under
    way, and the next read on the same store, on the same connection, answers as before."""
    store, project_id = project_of(tmp_path, 200)
    made = relating.Span
    try:
        found, seen = counted(store, project_id)
        calls = []

        def failing(**fields):
            calls.append(fields["id"])
            if len(calls) == 2:
                raise ValueError("a passage that cannot be made")
            return made(**fields)

        monkeypatch.setattr(relating, "Span", failing)
        with pytest.raises(ValueError) as kept:
            store.related_pages(project_id, "asked", RELATED_MAX)
        monkeypatch.setattr(relating, "Span", made)
        again, seen_again = counted(store, project_id)
    finally:
        store.close()
    assert kept.value is not None and len(calls) == 2
    assert (shown(again), again.left) == (shown(found), found.left)
    assert seen_again.visited == seen.visited > 0


def test_a_read_counts_its_own_rows_on_a_connection_with_a_statement_left_under_way():
    """The two functions the statements count rows by are given to the connection at each
    read. Where a statement of it is left under way (by anything that read on it before),
    SQLite refuses to give them again, and the two it holds already count for the read under
    way on that thread."""
    con = sqlite3.connect(":memory:")
    try:
        relating._counting(con)
        left = con.execute("SELECT value FROM json_each('[1, 2, 3]')")
        assert left.fetchone() == (1,)  # under way: two rows not read
        visits = relating._counting(con)
        counted = con.execute("SELECT cairn_visited(7), cairn_rows_left()").fetchone()
        assert counted == (1, relating.ROWS_MAX - 1)
        assert (visits.total, visits.by) == (1, {7: 1})
    finally:
        con.close()


# The layouts of the second attack (RT4): a project's pages saved together, or each in turn
# with a page of another project (claims that hold none of the words), at 120, 1,000 and
# 4,000 pages; and beside them 0, 8,000 and 64,000 claims of the other project that hold the
# words of the page asked, each the words of 8 of its 256 terms and its title. Those are
# saved after both projects' pages, and then the alternating project's page "asked" is saved
# again, so they fall inside its span and outside the other's.
SIZES = (120, 1_000, 4_000)
HOLDING = (0, 8_000, 64_000)


def grid_of(folder, pages: int) -> dict[int, dict[str, tuple]]:
    """For each count of HOLDING: each layout's answer and what its read cost, in one store."""
    store = Store(folder / "cairn.db")
    memory = Memory(store, lint_gate)
    layouts = ("together", "alternating")
    projects = {layout: store.create_project(layout).id for layout in layouts}
    other = store.create_project("elsewhere")
    lines = [
        "- Step: " + " ".join(term(n) for n in range(first, first + 8))
        for first in range(0, relating.TERMS_MAX, 8)
    ]

    def asked(layout: str, again: int = 0) -> None:
        text = "\n".join(lines) + "".join(f"\n- Saved again, time {k}." for k in range(again))
        memory.note(layout, text + "\n", title="Asked", topic="Asked")

    def others(layout: str) -> None:
        shared = " ".join(term(n) for n in range(SHARED))
        memory.note(layout, f"Brook sets {shared} first.\n", title="Share", topic="Share")
        text = "Brook reads Asked before each run.\n"
        memory.note(layout, text, title="Mention", topic="Mention")

    def page(layout: str, n: int) -> None:
        claim = Claim(
            id=f"cl_{layout}_{n}", text=f"Brook writes line {n} of the book.", span_ids=()
        )
        store.save_page(projects[layout], f"page-{n}", f"Page {n}", "", [claim])

    def theirs(n: int) -> None:
        claim = Claim(id=f"cl_q{n}", text=f"Ada keeps {n}.", span_ids=())
        store.save_page(other, f"their-{n}", f"Theirs {n}", "", [claim])

    def holding(first: int, last: int) -> None:
        for start in range(first, last, 2_000):
            claims = [
                Claim(
                    id=f"cl_h{n}",
                    text=" ".join(term(8 * (n % 32) + i).strip("`") for i in range(8))
                    + f" Asked {n}",
                    span_ids=(),
                )
                for n in range(start, min(last, start + 2_000))
            ]
            store.save_page(other, f"holding-{start}", f"Holding {start}", "", claims)

    other = other.id
    try:
        asked("together")
        others("together")
        for n in range(pages):
            page("together", n)
        asked("alternating")
        for n in range(pages):
            page("alternating", n)
            theirs(n)
        others("alternating")
        costs = {}
        done = 0
        for again, count in enumerate(HOLDING):
            holding(done, count)
            done = count
            if again:
                asked("alternating", again)
            merge(store)
            costs[count] = {layout: counted(store, projects[layout]) for layout in layouts}
        return costs
    finally:
        store.close()


@pytest.fixture(scope="module")
def grid(tmp_path_factory):
    return {pages: grid_of(tmp_path_factory.mktemp(f"pages-{pages}"), pages) for pages in SIZES}


@pytest.mark.parametrize("pages", SIZES)
def test_another_projects_pages_between_a_projects_add_no_work(grid, pages):
    """The same pages saved together or each in turn with another project's: the same answer,
    statements and rows, and the same steps, give or take the index's pages."""
    (found, alone), (found_in_turn, in_turn) = (grid[pages][0][k] for k in grid[pages][0])
    assert sorted(page.slug for page in found.pages) == ["mention", "share"]
    assert found.left.complete
    assert (shown(found_in_turn), found_in_turn.left) == (shown(found), found.left)
    assert run(in_turn) == run(alone)
    assert (in_turn.rows, in_turn.visited) == (alone.rows, alone.visited)
    assert in_turn.steps <= 1.1 * alone.steps + 10 * PERIOD, (alone.steps, in_turn.steps)


@pytest.mark.parametrize("pages", SIZES)
def test_claims_of_another_project_outside_a_projects_span_cost_nothing(grid, pages):
    found, alone = grid[pages][0]["together"]
    for count in HOLDING[1:]:
        found_beside, seen = grid[pages][count]["together"]
        assert (shown(found_beside), found_beside.left) == (shown(found), found.left)
        assert run(seen) == run(alone)
        assert (seen.rows, seen.claims, seen.visited) == (alone.rows, alone.claims, alone.visited)
        assert seen.steps <= 1.1 * alone.steps + 10 * PERIOD, (count, alone.steps, seen.steps)


@pytest.mark.parametrize("pages", SIZES)
def test_claims_of_another_project_inside_a_projects_span_cost_the_rows_handed_over(grid, pages):
    """Within ROWS_MAX (8,000 claims: 64,000 rows for the terms and 8,000 for the title), the
    same answer; past it (64,000 claims), here a part of it that says it is not complete. Each row
    handed over costs ROW_STEPS steps at most, whatever the project's size."""
    found, alone = grid[pages][0]["alternating"]
    for count in HOLDING[1:]:
        found_beside, seen = grid[pages][count]["alternating"]
        assert alone.visited < seen.visited <= relating.ROWS_MAX
        part_of(found_beside, found)
        limit = alone.steps + (seen.visited - alone.visited) * ROW_STEPS + 10 * PERIOD
        assert seen.steps <= limit, (count, alone.steps, seen.steps, seen.visited)
    assert grid[pages][8_000]["alternating"][0].left.complete
    assert not grid[pages][64_000]["alternating"][0].left.complete


def test_a_projects_pages_cost_a_read_a_few_seeks_each(grid):
    """From 120 pages to 4,000: two seeks a page more, once a read, to find the span."""
    small, large = grid[SIZES[0]][0]["together"][1], grid[SIZES[-1]][0]["together"][1]
    more = SIZES[-1] - SIZES[0]
    assert large.steps - small.steps <= more * PAGE_STEPS, (small.steps, large.steps)


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

    def create_function(self, *arguments):
        return self.con.create_function(*arguments)

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


def mentioned(folder, said: str, by_hand: bool = False) -> tuple[Store, str]:
    """A page "Quill ledger" and a page "Other" whose one claim is `said`: the text of its
    passage, as a note is compiled; or, `by_hand`, a claim saved with a short passage of
    other words."""
    store = Store(folder / "cairn.db")
    project = store.create_project("ops")
    memory = Memory(store, lint_gate)
    memory.note("ops", "Ada balances it at dusk.\n", title="Quill ledger", topic="Quill ledger")
    if by_hand:
        note, _ = store.add_note(project.id, "# Other\n\nShe opens the Quill ledger.\n")
        (span,) = [s.id for s in store.note_spans(note.id) if s.kind == "paragraph"]
        store.save_page(
            project.id, "other", "Other", "", [Claim(id="cl_h", text=said, span_ids=[span])]
        )
    else:
        memory.note("ops", said + "\n", title="Other", topic="Other")
    merge(store)
    return store, project.id


LONG = "Brook keeps the ledger at dawn; " * 1_900  # 60,800 characters


@contextlib.contextmanager
def apart(monkeypatch):
    """What a read does beside the store's statements to judge each place a title stands
    (relating._in_prose): the statements it runs in its own database in memory, FTS5's own
    apart, and the length of each text the Markdown parser reads for it (spans.code_read)."""
    seen = SimpleNamespace(statements=[], parsed=[])
    made, parse = relating._scratch, spans._CODE_READER.parse

    def scratch() -> sqlite3.Connection:
        con = made()
        con.set_trace_callback(
            lambda statement: (
                None if statement.lstrip().startswith("--") else seen.statements.append(statement)
            )
        )
        return con

    def parsed(text: str, env=None):
        seen.parsed.append(len(text))
        return parse(text, env)

    monkeypatch.setattr(relating, "_scratch", scratch)
    monkeypatch.setattr(spans._CODE_READER, "parse", parsed)
    yield seen


@pytest.mark.parametrize(
    "named",
    ["the Quill ledger.", "the Quill" + " \u2014" * 900 + " ledger."],
    ids=["side by side", "1,800 characters apart"],
)
def test_what_it_costs_to_know_that_a_claim_names_the_title(tmp_path, monkeypatch, named):
    """A page mentioned by one claim, short or of 60,000 characters, which names it at its
    end. The claim is read as a term's candidate is, and the statement that says it is its
    passage's text hands back no text: 13 statements, however long the claim. Its passage
    is handed back once, and BYTES_MAX counts it once, its facts being read once. The
    index's own tokenizer is then given the passage with what is not prose blanked out, in
    a database of the read's own in memory: 3 statements there. With less room than the
    passage the claim is not read, nothing of it is handed back, nothing is asked in memory,
    the page is not said to be mentioned, and the claim is counted."""
    for size, said in (
        ("short", "Then she opens " + named),
        ("long", LONG + "Then she opens " + named),
    ):
        (tmp_path / size).mkdir()
        store, project_id = mentioned(tmp_path / size, said)
        with watched(store) as seen, apart(monkeypatch) as judged:
            found = store.related_pages(project_id, "quill-ledger", RELATED_MAX)
        (reason,) = found.pages[0].why
        assert reason.kind == "mentioned_by" and found.left.claims_read == 1
        # the caller's 2; the page's claims and passages, 2; no term to list; the title, 1; its
        # claim, the size of the claim's passage and the passage, 3; whether the claim is its
        # passage's text, 1; what is given, 2; the notes of the passages given and who wrote
        # them, 2
        assert len(seen.statements) - settings_loaded(seen.statements) == 13
        assert not [s for s in seen.statements if "highlight(" in s]
        # made, given the text, asked where the title stands in it
        assert [s.split()[0] for s in judged.statements] == ["CREATE", "INSERT", "SELECT"]
        assert judged.parsed == []  # no backtick, tilde, tab or four spaces: nothing to parse
        stored = len(said.encode())
        assert seen.longest == len(said)  # the passage, once
        if size == "long":
            assert len(reason.there.line) == relations.LINE_MAX and reason.there.line_cut
            assert reason.there.line.startswith("Quill") or reason.there.line.endswith(named)
            monkeypatch.setattr(relating, "BYTES_MAX", stored - 1)
            with watched(store) as seen, apart(monkeypatch) as judged:
                found = store.related_pages(project_id, "quill-ledger", RELATED_MAX)
            assert found.pages == () and found.left.claims_not_read == 1
            assert seen.longest < 100 and judged.statements == judged.parsed == []
            monkeypatch.setattr(relating, "BYTES_MAX", stored)
            assert len(store.related_pages(project_id, "quill-ledger", RELATED_MAX).pages) == 1
            monkeypatch.undo()
        store.close()


def test_every_claim_that_holds_the_title_is_judged_in_the_same_three_statements(
    tmp_path, monkeypatch
):
    """CLAIMS_COMMON claims of other pages hold the title, each with code in backticks (so
    each is read by the Markdown parser, once) and some naming it in prose: the store's
    statements are those of one such claim, and the database in memory is made, given every
    text and asked, once. One claim more makes the title common: nothing is judged."""
    store = Store(tmp_path / "cairn.db")
    memory = Memory(store, lint_gate)
    project = store.create_project("ops")
    memory.note("ops", "Ada balances it at dusk.\n", title="Quill ledger", topic="Quill ledger")
    texts = [
        f"Group {n} opens `quill` ledger {n}" + (", then the Quill ledger." if n % 2 else ".")
        for n in range(relations.CLAIMS_COMMON)
    ]
    for n, text in enumerate(texts):
        memory.note("ops", text + "\n", title=f"Group {n}", topic=f"Group {n}")
    merge(store)
    try:
        with watched(store) as seen, apart(monkeypatch) as judged:
            found = store.related_pages(project.id, "quill-ledger", RELATED_MAX)
        # 32 pages name it: more than COMMON, so the title is common and relates none
        assert found.pages == () and found.left.complete
        # the caller's 2; the page's 2; the title's 1; its claims 3; which are their passage's
        # text, 1; nothing given
        assert run(seen) == 9
        assert [s.split()[0] for s in judged.statements] == ["CREATE", "INSERT", "SELECT"]
        assert sorted(judged.parsed) == sorted(len(text) for text in texts)
        memory.note("ops", "Brook opens `quill` ledger 99.\n", title="One more", topic="One more")
        merge(store)
        with watched(store) as seen, apart(monkeypatch) as judged:
            found = store.related_pages(project.id, "quill-ledger", RELATED_MAX)
        assert found.pages == () and judged.statements == judged.parsed == []
    finally:
        store.close()


def test_a_passage_that_claims_of_several_pages_are_is_parsed_and_given_in_memory_once(
    tmp_path, monkeypatch
):
    """COMMON pages saved by hand, each with one claim that is the text of the same passage,
    with code in backticks and the title in prose. BYTES_MAX counts the passage once, and so
    is the cost of judging it: the Markdown parser reads it once, and the database in memory
    is given it once. Each page is named, at the same place."""
    store = Store(tmp_path / "cairn.db")
    memory = Memory(store, lint_gate)
    project = store.create_project("ops")
    memory.note("ops", "Ada balances it at dusk.\n", title="Quill ledger", topic="Quill ledger")
    said = "Brook runs `ledger --check` " * 70 + "and then opens the Quill ledger."
    note, _ = store.add_note(project.id, f"# Shelf\n\n{said}\n")
    (span,) = [s for s in store.note_spans(note.id) if s.kind == "paragraph"]
    assert span.text == said
    for n in range(relations.COMMON):
        claims = [Claim(id=f"cl_{n}", text=said, span_ids=[span.id])]
        store.save_page(project.id, f"shelf-{n}", f"Shelf {n}", "", claims)
    merge(store)
    try:
        with apart(monkeypatch) as judged:
            found = store.related_pages(project.id, "quill-ledger", RELATED_MAX)
        assert sorted(page.slug for page in found.pages) == [
            f"shelf-{n}" for n in range(relations.COMMON)
        ]
        assert judged.parsed == [len(said)]
        given = [s for s in judged.statements if s.startswith("INSERT")]
        assert len(given) == 1 and given[0].count("opens the Quill ledger") == 1
    finally:
        store.close()


def test_a_reworded_claim_that_mentions_costs_no_statement_more_and_nothing_of_its_text(
    tmp_path, monkeypatch
):
    """A claim of 60,000 characters saved by hand, whose text is not its passage's: the
    mention stands as the index has it. Nothing of the claim is handed back to know that
    (the statement that says whether it is its passage's text hands back no text), nothing
    is asked in memory, and no line is asked for: the claim is given by its head, read by a
    handle to the bound. The same 13 statements, and the longest text handed back is the
    short passage."""
    store, project_id = mentioned(tmp_path, LONG + "Then she opens the Quill ledger.", by_hand=True)
    with watched(store) as seen, apart(monkeypatch) as judged:
        found = store.related_pages(project_id, "quill-ledger", RELATED_MAX)
    (reason,) = found.pages[0].why
    assert reason.kind == "mentioned_by" and reason.there.cut and reason.there.line is None
    assert len(seen.statements) - settings_loaded(seen.statements) == 13
    assert seen.longest < 100 and judged.statements == judged.parsed == []
    store.close()
