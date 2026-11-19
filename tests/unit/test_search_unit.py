"""Unit tests for search: BM25 over the projects a view reaches, matched in the account's own
FTS5 index, with the statistics save_page keeps. The reference in these tests is FTS5's own
bm25() on an index that holds exactly the reachable claims."""

import random
import re
import sqlite3
from pathlib import Path

import pytest

from cairn.store import Claim, Store, ranking, reach
from cairn.store.base import StoreBase
from cairn.store.schema import _CLAIM_INDEX

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    s = Store(db)
    yield s
    s.close()


def page(view, project_id: str, slug: str, *texts: str, title: str | None = None):
    claims = [Claim(id=f"cl_{slug}_{n}", text=t, span_ids=()) for n, t in enumerate(texts)]
    return view.save_page(project_id, slug, title or slug.title(), "", claims)


def reference(db: Path, account_id: str, project_ids, query: str) -> list[tuple[str, float]]:
    """(claim id, -bm25) in FTS5's own order, on an index holding exactly the claims of
    `project_ids`, inserted in the account index's order (so ties break the same way)."""
    with sqlite3.connect(db) as con:
        rows = con.execute(
            "SELECT project_id, slug, claim_id, span_ids, title, text"
            f" FROM claim_search_{account_id} ORDER BY rowid"
        ).fetchall()
    ref = sqlite3.connect(":memory:")
    try:
        ref.execute(_CLAIM_INDEX.format(index="ref"))
        ref.executemany(
            "INSERT INTO ref (project_id, slug, claim_id, span_ids, title, text)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [row for row in rows if row[0] in set(project_ids)],
        )
        words = ranking.words(query)
        return ref.execute(
            "SELECT claim_id, -bm25(ref) FROM ref WHERE ref MATCH ? ORDER BY bm25(ref), rowid",
            (" OR ".join(f'"{w}"' for w in words),),
        ).fetchall()
    finally:
        ref.close()


def cap(monkeypatch, scored: int) -> None:
    """Every query scores `scored` claims (Q3): the fewest any query scores, and an amount of
    work that allows no more (ranking.scored_max)."""
    monkeypatch.setattr(ranking, "SCORED_MAX", scored)
    monkeypatch.setattr(ranking, "SCORED_WORK", scored)


def ranked(view, query: str, project_ids=None, limit: int = 10_000) -> list[tuple[str, float]]:
    return [(hit.claim_id, hit.score) for hit in view.search(query, project_ids, limit)]


def assert_same_ranking(mine, theirs):
    """The same claims in the same order, and FTS5's scores up to the last bits: SQLite's C
    may fuse a multiply and an add (an FMA) where Python rounds twice."""
    assert [claim for claim, _ in mine] == [claim for claim, _ in theirs]
    for (_, score), (_, expected) in zip(mine, theirs, strict=True):
        assert score == pytest.approx(expected, rel=1e-12, abs=1e-18)


# (a) BM25 over the reach equals FTS5's bm25 on an index of exactly the reach

VOCABULARY = [
    "quill",
    "Quill",
    "QUILL",
    "naïve",
    "naive",
    "café",
    "cafe",
    "lumen",
    "queue",
    "relay",
    "retry_count",
    "retry",
    "count",
    "the",
    "a",
    "b",
    "ß",
    "zed",
    "ünïcode",
    "Ünïcode",
    "x9",
    "42",
    "the",
    "the",
]


def random_corpus(store, rng: random.Random) -> dict[str, str]:
    ids = {name: store.create_project(name).id for name in ("api", "web", "journal", "ops")}
    for name, project_id in ids.items():
        for p in range(rng.randint(0, 4)):
            texts = []
            for _ in range(rng.randint(1, 5)):
                n = rng.choice((1, 3, 8, 20, 150))  # 150: a docsize past one varint byte
                words = [rng.choice(VOCABULARY) for _ in range(n)]
                if rng.random() < 0.4:
                    words += ["the"] * rng.randint(1, 4)  # df > N/2: the IDF floor
                texts.append(rng.choice((" ", "_", ", ", "-")).join(words))
            title = " ".join(rng.choice(VOCABULARY) for _ in range(rng.randint(0, 3)))
            page(store, project_id, f"{name}-{p}", *texts, title=title or "Untitled")
        if rng.random() < 0.3 and store.list_pages(project_id):  # a page saved again
            page(store, project_id, f"{name}-0", "quill relay quill", title="Quill title only")
    return ids


QUERIES = (
    "quill",
    "Quill quill",  # the same word twice: two phrases, as FTS5 counts them
    "naïve café",
    "retry_count",  # one word, a two-token phrase
    "retry_count count_retry the",
    "the",  # in most claims: the IDF floor
    "title only",  # title-only hits
    "ß x9 42 zed",
    "a_a b_b a",  # self-overlapping phrases
)


@pytest.mark.parametrize("seed", range(12))
def test_bm25_over_the_reach_ranks_as_fts5_on_an_index_of_exactly_the_reach(store, db, seed):
    rng = random.Random(seed)
    ids = random_corpus(store, rng)
    chosen = rng.sample(sorted(ids.values()), rng.randint(1, 3))
    view = store.narrow(projects=chosen)
    for query in QUERIES:
        assert_same_ranking(ranked(view, query), reference(db, store.account_id, chosen, query))
        everything = ids.values()
        assert_same_ranking(
            ranked(store, query), reference(db, store.account_id, everything, query)
        )


def test_overlapping_phrase_instances_count_one_each(store, db):
    project = store.create_project("api").id
    page(store, project, "x", "a a a b", "a b a b a", "b a_a a", "a a", "a")
    for query in ("a_a", "a_b_a", "b_a"):
        assert_same_ranking(ranked(store, query), reference(db, store.account_id, [project], query))


# (b) nothing out of reach moves a limited view's hits or scores


def test_a_limited_views_hits_and_scores_ignore_every_project_out_of_reach(store, db):
    api, web = store.create_project("api").id, store.create_project("web").id
    page(store, api, "retries", "Quill retries three times.", "The queue holds jobs.")
    page(store, web, "checkout", "Quill renders the checkout.")
    view = store.narrow(projects=(api, web))
    before = view.search("quill queue the", limit=50)
    assert before

    outside = [store.create_project(f"other{n}").id for n in range(3)]
    for n, project in enumerate(outside):
        page(store, project, "notes", *(["Quill quill queue the."] * (n + 1)))
    assert view.search("quill queue the", limit=50) == before  # to the last digit
    page(store, outside[0], "notes", "Nothing about it.")  # replaced
    page(store, outside[1], "more", "queue " * 200)
    assert view.search("quill queue the", limit=50) == before
    other = store.for_account(store.create_account("Other").id)
    page(other, other.create_project("x").id, "x", "quill " * 50)
    assert view.search("quill queue the", limit=50) == before


# (c) the whole workspace ranks as before, on the fixture notes


def test_the_whole_workspace_ranks_the_fixture_notes_as_fts5(store, db):
    projects = []
    for fixture in sorted(FIXTURES.glob("*.md")):
        project = store.create_project(fixture.stem.replace("_", "-")).id
        projects.append(project)
        lines = [line.strip("#-* ") for line in fixture.read_text().splitlines()]
        page(store, project, "notes", *[line for line in lines if line])
    for query in ("the", "decision runbook", "incident timeline owner", "setup install", "x"):
        mine = ranked(store, query)
        assert_same_ranking(mine, reference(db, store.account_id, projects, query))
        assert ranked(store.narrow(), query) == mine  # a view of every project: the same path


# (d) a search costs what its reach holds, not what the workspace holds


def test_a_search_over_two_projects_reads_no_claim_of_the_other_two_hundred(store):
    reached = [store.create_project(f"in{n}").id for n in range(2)]
    for project in reached:
        page(store, project, "p", "quill relay", "quill", "nothing here")
    for n in range(200):
        page(store, store.create_project(f"out{n}").id, "p", "quill relay", "quill quill")
    view = store.narrow(projects=reached)
    read = []  # FTS5 reads a claim's row with one statement per row, or scans a range of them

    def traced(statement: str) -> None:
        if "_content'" in statement:
            read.append(statement)

    store._observe(trace=traced)
    try:
        hits = view.search("quill relay")
    finally:
        store._observe(trace=None)
    assert len(hits) == 4
    assert [s for s in read if not s.endswith("WHERE T.'rowid'=?")] == []  # no scan
    matched_in_reach, words = 4, 2  # each matched claim: once to match, once per word scored
    assert 0 < len(read) <= matched_in_reach * (1 + words)


def test_a_query_without_words_or_projects_never_touches_the_database(store, monkeypatch):
    project = store.create_project("api").id
    page(store, project, "p", "quill")
    entered = []
    monkeypatch.setattr(StoreBase, "_read", lambda self: entered.append(1))
    assert store.search("?! ... --") == []
    assert store.search("quill", project_ids=[]) == []
    assert store.search("quill", limit=0) == []
    assert entered == []


def test_only_the_newest_matches_are_scored_and_their_scores_do_not_change(store, monkeypatch):
    project = store.create_project("api").id
    for n in range(6):
        page(store, project, f"p{n}", f"quill note {n}")
    everything = {hit.claim_id: hit.score for hit in store.search("quill", limit=50)}
    cap(monkeypatch, 3)
    capped = store.search("quill", limit=50)
    # the three newest; their scores tie, so they come in index order
    assert [hit.claim_id for hit in capped] == ["cl_p3_0", "cl_p4_0", "cl_p5_0"]
    assert all(hit.score == everything[hit.claim_id] for hit in capped)  # N and df uncapped


def test_only_the_first_words_of_a_long_query_count(store):
    project = store.create_project("api").id
    page(store, project, "p", "first", "last")
    filler = " ".join(f"w{n}" for n in range(ranking.WORDS_MAX - 1))
    assert [hit.text for hit in store.search(f"first {filler} last")] == ["first"]


def test_save_page_keeps_each_projects_claim_and_token_counts(store, db):
    api, web = store.create_project("api").id, store.create_project("web").id

    def counts():
        with sqlite3.connect(db) as con:
            return dict(
                ((project, (claims, tokens)) for project, claims, tokens in con.execute(
                    "SELECT project_id, claims, tokens FROM search_projects"
                ))
            )  # fmt: skip

    page(store, api, "a", "one two three", "four", title="T")  # title counts in each claim
    assert counts() == {api: (2, 3 + 1 + 1 + 1)}
    page(store, web, "w", "x " * 200)
    page(store, api, "a", "one", title="Two words")  # replaced: the old rows leave
    assert counts() == {api: (1, 1 + 2), web: (1, 200 + 1)}
    page(store, api, "a")  # a page of no claims
    assert counts()[api] == (0, 0)


def test_deleting_an_account_deletes_its_search_statistics(store, db):
    other = store.for_account(store.create_account("Other").id)
    page(other, other.create_project("x").id, "x", "quill")
    page(store, store.create_project("api").id, "p", "quill")
    assert store.delete_account(other.account_id)
    with sqlite3.connect(db) as con:
        assert {a for (a,) in con.execute("SELECT account_id FROM search_claims")} == {
            store.account_id
        }
        assert {a for (a,) in con.execute("SELECT account_id FROM search_projects")} == {
            store.account_id
        }


def test_search_reads_its_reach_in_one_section_of_the_store(store):
    assert "search" in reach.PROJECT_DATA and "search" not in vars(reach.ReachMethods)


def test_no_search_ranks_with_bm25_in_sqlite():
    import inspect

    import cairn.store.memory
    import cairn.store.ranking

    for module in (cairn.store.memory, cairn.store.ranking, reach):
        assert not re.search(r"bm25\(\w|bm25\(\{", inspect.getsource(module))


# (e) over the cap, the claims scored are those whose words of the query weigh most (Q2)


@pytest.fixture
def weighed(store):
    """100 claims: Y holds "rare" (1 claim), X holds "midone" and "midtwo" (5 claims each),
    every claim holds "common". X and Y are the oldest, so recency would score neither."""
    project = store.create_project("api").id
    texts = ["rare common", "midone midtwo common"]
    texts += [f"midone common {n}" for n in range(4)] + [f"midtwo common {n}" for n in range(4)]
    texts += [f"common filler {n}" for n in range(90)]
    page(store, project, "p", *texts)
    return {
        "Y": "cl_p_0",
        "X": "cl_p_1",
        "midone": ["cl_p_1", *(f"cl_p_{n}" for n in range(2, 6))],
        "midtwo": ["cl_p_1", *(f"cl_p_{n}" for n in range(6, 10))],
    }


def scored(store, query: str) -> set[str]:
    return {hit.claim_id for hit in store.find(query, limit=50).hits}


def test_two_mid_rare_words_outweigh_the_rarest_alone(store, weighed, monkeypatch):
    # IDF over 100 claims: rare (df 1) 4.19; midone, midtwo (df 5) 2.85 each; common the floor
    cap(monkeypatch, 1)
    assert scored(store, "rare midone midtwo common") == {weighed["X"]}
    cap(monkeypatch, 2)
    found = store.find("rare midone midtwo common", limit=50)
    assert {hit.claim_id for hit in found.hits} == {weighed["X"], weighed["Y"]}
    assert (found.searched.claims_ranked, found.searched.ranked_by) == (2, "likeliest")


def test_among_equal_weights_the_newest_are_scored(store, weighed, monkeypatch):
    cap(monkeypatch, 2)
    assert scored(store, "midone common") == set(weighed["midone"][-2:])


def test_a_word_given_twice_weighs_twice(store, weighed, monkeypatch):
    cap(monkeypatch, 1)
    assert scored(store, "rare midtwo") == {weighed["Y"]}  # 4.19 against 2.85
    assert scored(store, "rare midtwo midtwo") == {weighed["midtwo"][-1]}  # 5.70: the newest


# (f) a word's count stops at its bound (R6)


def count_bound(monkeypatch, counted: int) -> None:
    monkeypatch.setattr(ranking, "COUNTED_MAX", counted)


def test_the_count_bound_is_the_most_claims_any_search_scores():
    assert ranking.COUNTED_MAX == ranking.SCORED_WORK == ranking.scored_max(1) == 8_000


def test_a_count_stops_one_match_past_its_bound_and_says_it_is_not_exact(store, monkeypatch):
    project = store.create_project("api").id
    page(store, project, "p", *(f"quill note {n}" for n in range(10)))
    cap(monkeypatch, 4)
    count_bound(monkeypatch, 6)
    facts = store.find("quill", limit=50).searched
    assert (facts.claims_matched, facts.claims_matched_exact) == (6, False)  # more than 6
    assert (facts.claims_ranked, facts.ranked_by) == (4, "newest")
    count_bound(monkeypatch, 10)  # a count at its bound is exact
    facts = store.find("quill", limit=50).searched
    assert (facts.claims_matched, facts.claims_matched_exact) == (10, True)
    count_bound(monkeypatch, 2)  # never below what the search scores: the path stays its own
    facts = store.find("quill", limit=50).searched
    assert (facts.claims_matched, facts.claims_matched_exact, facts.claims_ranked) == (4, False, 4)


def test_a_word_counted_to_its_bound_weighs_as_one_held_by_one_claim_more(store, monkeypatch):
    """lamp is held by 10 claims, kiln by 7, vane by 6, each claim two words long. With the
    bound at 6, lamp's count stops at 7: it weighs as kiln does, and never more than a word
    counted exactly (vane). The claims' other numbers are the same, so the scores say it."""
    project = store.create_project("api").id
    texts = [f"lamp f{n}" for n in range(10)] + [f"kiln g{n}" for n in range(7)]
    texts += [f"vane h{n}" for n in range(6)] + [f"moss k{n}" for n in range(7)]
    page(store, project, "p", *texts)

    def score(word: str) -> float:
        return store.find(word, limit=1).hits[0].score

    exact = {word: score(word) for word in ("lamp", "kiln", "vane")}
    assert exact["lamp"] < exact["kiln"] < exact["vane"]  # held by more, weighs less
    cap(monkeypatch, 4)  # a count never stops below what the search scores
    count_bound(monkeypatch, 6)
    assert score("lamp") == exact["kiln"] and score("lamp") > exact["lamp"]
    assert score("kiln") == exact["kiln"] < score("vane") == exact["vane"]


def test_the_likeliest_weigh_each_word_over_the_matches_its_count_read(store, monkeypatch):
    """The oldest claim holds both words; each word is held by more claims than its count
    reads. Each word's matches are walked newest first and stop where its count did, so the
    oldest claim is not weighed: the claims scored are among the newest of each word."""
    project = store.create_project("api").id
    texts = [
        "comet nebula",
        *(f"comet c{n}" for n in range(6)),
        *(f"nebula d{n}" for n in range(6)),
    ]
    page(store, project, "p", *texts)
    assert store.find("comet nebula", limit=1).hits[0].claim_id == "cl_p_0"  # all scored
    cap(monkeypatch, 2)
    count_bound(monkeypatch, 3)
    found = store.find("comet nebula", limit=50)
    assert (found.searched.ranked_by, found.searched.claims_ranked) == ("likeliest", 2)
    newest = {f"cl_p_{n}" for n in (*range(3, 7), *range(9, 13))}  # 4 of each word
    assert {hit.claim_id for hit in found.hits} <= newest  # the oldest is not among them
