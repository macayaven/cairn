"""Q2: under the bounds a search returns exactly what it returned before Q2.

tests/unit/reference/ranking_before_q2.py holds src/cairn/store/ranking.py byte for byte as it
was at commit b6b0eaa (under a three-line header). Each case runs the same search with the
reference and with the store, on the same connection, the same reach and the same projects
searched, and asserts that the hits are equal, field by field and in order, scores to the last
bit. Objects of the two copies are the same class (both import cairn.store.models), so they
are compared as dumps all the same. When a case differs, the reference is right by definition.

Under the bounds: every search whose words all count (32 at most, holding 64 parts at most)
and whose matches are all ranked (1,000 at most); for those the store must also say that
nothing was left out. Over the cap, two more promises: a query of one word ranks exactly what
the reference ranks with the same cap (recency stays its rule), and every scored claim has the
score the reference gives it with no cap at all. There the store's cap is the same for every
query (SCORED_MAX and the work SCORED_WORK both set to it).

Q3: a search over more than 1,000 matches that its own bound (ranking.scored_max: what the
work allows for its parts) lets it score in full returns what the reference returns with
its cap lifted (the test sets the reference's SCORED_MAX, never the file), says nothing was
left out, and is counted; some of them take the likeliest path, several words whose matches,
counted once a word, pass the bound while the claims they match do not.

Inputs: seeded corpora of four projects, the queries of test_search_unit, and seeded queries
of 1 to 32 words drawn from a vocabulary of repeats, case and accent variants, words joined
by "_" and words of "_" alone (the same every run); each over every project, over a view
narrowed to some, and over one project searched by a view of every project.
"""

import importlib.util
import random
import sys
from pathlib import Path

import pytest
from test_search_unit import QUERIES, VOCABULARY, random_corpus

from cairn.store import Claim, Store, ranking
from cairn.store.base import _has_table

REFERENCE = Path(__file__).resolve().parent / "reference" / "ranking_before_q2.py"


def _load():
    spec = importlib.util.spec_from_file_location("q2_reference_ranking", REFERENCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


old = _load()
COUNT = {"searches": 0, "hits": 0, "capped": 0, "over 1,000": 0, "likeliest": 0, "differences": 0}
EXTRA = ["_", "__", "a_", "_b", "quill_relay", "relay_quill_relay", "zzz", "Café", "QUEUE"]


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


def reference(view, query: str, project_ids, limit: int):
    """What the store searched before Q2: Store.search's body as it was, with the reference."""
    query_words = old.words(query)
    if not query_words or limit < 1 or (project_ids is not None and not project_ids):
        return []
    with view._read() as con:
        if not _has_table(con, view._index):
            return []
        reach = view._reach(con)
        if reach is None:
            reach = frozenset(
                project_id
                for (project_id,) in con.execute(
                    "SELECT id FROM projects WHERE account_id = ?", (view.account_id,)
                )
            )
        return old.search(con, view.account_id, view._index, reach, query_words, project_ids, limit)


def queries(rng: random.Random) -> list[str]:
    words = VOCABULARY + EXTRA
    drawn = [
        rng.choice((" ", ", ", " - ")).join(rng.choice(words) for _ in range(rng.randint(1, 32)))
        for _ in range(12)
    ]
    return [*QUERIES, *drawn]


def same(view, query: str, project_ids=None, limit: int = 10_000) -> None:
    found = view.find(query, project_ids, limit)
    expected = reference(view, query, project_ids, limit)
    COUNT["searches"] += 1
    COUNT["hits"] += len(expected)
    mine = [hit.model_dump() for hit in found.hits]
    theirs = [hit.model_dump() for hit in expected]
    if mine != theirs:
        COUNT["differences"] += 1
    assert mine == theirs, (query, project_ids)
    assert found.searched.complete, (query, found.searched)


@pytest.mark.parametrize("seed", range(16))
def test_under_the_bounds_every_search_returns_what_it_returned_before(store, seed):
    rng = random.Random(seed)
    ids = random_corpus(store, rng)
    chosen = [ids[name] for name in rng.sample(sorted(ids), rng.randint(1, 3))]
    narrowed = store.narrow(projects=chosen)
    one = ids[rng.choice(sorted(ids))]  # by name: ids are random
    for query in queries(rng):
        limit = rng.choice((1, 3, 10, 10_000))
        same(store, query, limit=limit)
        same(narrowed, query, limit=limit)
        same(store, query, [one], limit)
        same(narrowed, query, chosen[:1], limit)


@pytest.mark.parametrize("seed", range(8))
def test_over_the_cap_one_word_ranks_the_newest_and_every_score_is_uncapped(
    store, seed, monkeypatch
):
    rng = random.Random(seed)
    ids = random_corpus(store, rng)
    cap = rng.choice((1, 2, 3, 5))
    for query in queries(rng):
        uncapped = {hit.claim_id: hit.score for hit in reference(store, query, None, 10_000)}
        monkeypatch.setattr(ranking, "SCORED_MAX", cap)
        monkeypatch.setattr(ranking, "SCORED_WORK", cap)  # every query scores `cap` (Q3)
        monkeypatch.setattr(old, "SCORED_MAX", cap)
        found = store.find(query, None, 10_000)
        newest = reference(store, query, None, 10_000)
        monkeypatch.undo()
        COUNT["searches"] += 1
        COUNT["hits"] += len(found.hits)
        assert all(hit.score == uncapped[hit.claim_id] for hit in found.hits), query
        if len(uncapped) > cap:
            COUNT["capped"] += 1
            assert not found.searched.complete and found.searched.claims_ranked == cap
        if len(set(ranking.words(query))) == 1:  # one word: the reference's own rule
            assert [h.model_dump() for h in found.hits] == [h.model_dump() for h in newest]
    assert ids


BIG = ["quill", "relay", "lumen", "queue", "retry_count", "retry", "count", "Café", "cafe", "the"]


def big_corpus(store, rng: random.Random) -> dict[str, str]:
    """Three projects of 1,300, 500 and 60 claims of 4 to 14 words from BIG: a word of BIG is
    held by about 1,100 claims of the three together, two words by more than 1,000 of "api"."""
    ids = {name: store.create_project(name).id for name in ("api", "web", "ops")}
    for name, count in (("api", 1_300), ("web", 500), ("ops", 60)):
        for first in range(0, count, 700):
            texts = [
                rng.choice((" ", ", ", "_")).join(
                    rng.choice(BIG) for _ in range(rng.randint(4, 14))
                )
                for _ in range(min(700, count - first))
            ]
            page(store, ids[name], f"{name}-{first}", *texts)
    return ids


def page(view, project_id: str, slug: str, *texts: str) -> None:
    claims = [Claim(id=f"cl_{slug}_{n}", text=t, span_ids=()) for n, t in enumerate(texts)]
    view.save_page(project_id, slug, slug.title(), "", claims)


def matched(view, word: str, seen: dict) -> int:
    """How many claims of the view's reach hold one word (its document frequency): a search
    of it alone ranks them all here. `seen` keeps what was counted."""
    if (id(view), word) not in seen:
        found = view.find(word, None, 1)
        assert found.searched.complete
        seen[id(view), word] = found.searched.claims_matched
    return seen[id(view), word]


@pytest.mark.parametrize("seed", range(2))
def test_over_1000_matches_every_match_is_scored_as_the_reference_with_no_cap_scores_it(
    store, seed, monkeypatch
):
    """Q3: a search whose matches pass 1,000 and fit its own bound (ranking.scored_max)
    scores them all, and its hits are those the reference gives with its cap lifted: by the
    newest path, and by the likeliest (several words whose matches, counted once a word,
    pass the bound while the claims they match do not)."""
    rng = random.Random(seed)
    ids = big_corpus(store, rng)
    monkeypatch.setattr(old, "SCORED_MAX", 10**9)  # the reference's cap lifted, in this test
    narrowed = store.narrow(projects=[ids["api"]])
    # Queries of one to four parts, whose bounds (8,000 to 2,000) pass the 1,860 claims.
    queries = [*BIG, "quill relay lumen", "quill relay lumen queue", "retry_count quill relay"]
    while len(queries) < len(BIG) + 6:
        query = " ".join(rng.sample(BIG, rng.randint(2, 4)))
        if len({p for w in ranking.words(query) for p in w.split("_") if p}) <= 4:
            queries.append(query)
    over, likeliest, seen = 0, 0, {}
    for query in queries:
        counted = ranking.words(query)
        parts = {part for word in counted for part in word.split("_") if part}
        for view, project_ids in ((store, None), (narrowed, None), (store, [ids["api"]])):
            found = view.find(query, project_ids, 10_000)
            expected = reference(view, query, project_ids, 10_000)
            COUNT["searches"] += 1
            COUNT["hits"] += len(expected)
            mine = [hit.model_dump() for hit in found.hits]
            theirs = [hit.model_dump() for hit in expected]
            if mine != theirs:
                COUNT["differences"] += 1
            assert mine == theirs, (query, project_ids)
            assert found.searched.complete, (query, found.searched)
            assert found.searched.claims_ranked == len(expected)
            if len(expected) > ranking.SCORED_MAX:
                over += 1
                df = sum(matched(view, word, seen) for word in dict.fromkeys(counted))
                likeliest += len(set(counted)) > 1 and df > ranking.scored_max(len(parts))
    COUNT["over 1,000"] += over
    COUNT["likeliest"] += likeliest
    assert over and likeliest


def test_the_count_of_cases_compared():
    """Runs last in this module: how much the tests above compared, for the report."""
    print(
        f"\nQ2, Q3 differential: {COUNT['searches']} searches, {COUNT['hits']} hits compared,"
        f" {COUNT['capped']} over the cap, {COUNT['over 1,000']} over 1,000 matches scored in"
        f" full ({COUNT['likeliest']} by the likeliest path), {COUNT['differences']} differences"
    )
    assert COUNT["searches"] > 1_000 and COUNT["differences"] == 0
