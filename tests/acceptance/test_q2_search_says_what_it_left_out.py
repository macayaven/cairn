"""Q2 acceptance tests: a search says what it left out, and ranks the likeliest claims when it
cannot rank them all.

Written before implementation. Implementers must not edit this file once it is frozen.

Search keeps two bounds, so that one search costs bounded work whoever asks (decision 38): it
uses the first 32 words of a query, and it scores only as many of the claims that match as
a fixed amount of work allows for the parts of those words (Q3: 8,000 for one part, 4,000
for two, 1,000 from eight on, never fewer than 1,000). A recall tool that answers "nothing"
or "these five" while a better claim exists makes an agent act on a wrong belief, so neither
bound may cut silently (decision 112: a request never loses a field silently).

The cases about a search over the bound run under a small budget (the fixture
`small_budget`), under which a query of one part and one of two both score 1,000, as before
Q3: the real bound is larger than the corpora these cases build. One case pins the real
constants and the arithmetic, and the reproductions also run under the real budget, where
they rank every match.

1. Every answer of a search says what it left out, in one field, `searched`, always present,
   so that nobody infers completeness from an absent key:
   - `complete`: true when every word of the query was searched and every matching claim
     was ranked; false otherwise;
   - `words_searched`: how many of the query's words were searched (a word given twice
     counts twice, as it does in a score);
   - `words_left_out`: the words that were not, in the query's order, the first 5 of them,
     each cut to its first 64 characters followed by "..." when longer; and
     `words_left_out_count`, how many in all;
   - `claims_ranked`: how many matching claims were ranked; `claims_matched`: how many
     claims match in the projects searched, when that is known without another statement,
     else null, which means more than `claims_ranked`;
   - `said`: one or two plain sentences, from those facts, that say what was left out and
     what to do about it (search one project, add a rarer word, shorten the query), or,
     when nothing was, exactly "Every word of the query was searched, and every matching
     claim was ranked." The advice follows the search: a search of more than one project
     says to search one project so that fewer claims match, or add a rarer word
     (EVERY_PROJECT); a search of one project, whether the caller named it or reaches no
     other, says only to add a rarer word (ONE_PROJECT).
   The field is the same at the three doors: in the JSON of GET /api/search, in what the MCP
   tool `recall` returns (whose description says what it means), and on the search page,
   where `said` is a line above the hits.
2. When more claims match than a search ranks, the claims ranked are the likeliest to score
   highest, by a rule that depends only on the projects the caller reaches: a claim holding
   a rare word of the query is ranked before one holding only its common words. A query of
   one word has no such order (every match holds it): the newest claims are ranked, as
   before, and the answer says the ranking was partial.
3. The words that count are the query's first 32, as before, and fewer when their parts
   (FTS5 splits a word at "_") would pass 64: the words left out are named either way.
4. Under the bounds nothing changes: for a search that uses all its words and ranks all its
   matches, the hits, their order and their scores are what FTS5's bm25() gives on an index
   holding exactly the projects the caller reaches, and `searched` says nothing was left out.
5. A caller who reaches one project learns nothing about another from the counts: what a
   search answers it is the same whatever the projects out of its reach hold, and a
   one-word count over every reached project is never given for a search of one project.

Every name here is made up.
"""

import html
import json
import re
import sqlite3
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store
from cookie_names import session_cookie

BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
KEYS = (
    "complete",
    "words_searched",
    "words_left_out",
    "words_left_out_count",
    "claims_matched",
    "claims_ranked",
    "said",
)
NOTHING_LEFT_OUT = "Every word of the query was searched, and every matching claim was ranked."
NO_WORDS = (
    "Nothing was searched: the query holds no words, only spaces or punctuation. Search with"
    " words of letters or digits."
)
EVERY_PROJECT = (
    "Search one project so that fewer claims match, or add a rarer word to rank the claims that"
    " hold it first."
)
ONE_PROJECT = "Add a rarer word to rank the claims that hold it first."
CAP, WORDS = 1_000, 32
FILLER = " ".join(f"zq{n:02d}" for n in range(WORDS))  # 32 words that match nothing


def visible(page: str) -> str:
    """A web page's text as a person reads it: no tags, entities read, blanks folded."""
    text = re.sub(r"(?s)<(script|style)\b.*?</\1>", " ", page)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())


class World:
    """A store, an app over it, an API key that reaches every project, and a web session
    opened with that key."""

    def __init__(self, folder: Path) -> None:
        self.db = folder / "cairn.db"
        self.store = Store(self.db)
        self.key = self.store.create_key("desk")
        self.client = TestClient(create_app(self.store, public_url=BASE), base_url=BASE)
        self.client.__enter__()
        opened = self.client.post("/login", data={"key": self.key}, follow_redirects=False)
        assert opened.status_code == 303
        self.cookie = {"cookie": f"{session_cookie()}={opened.cookies[session_cookie()]}"}
        self.client.cookies.clear()

    def close(self) -> None:
        self.client.__exit__(None, None, None)
        self.store.close()

    def project(self, name: str) -> str:
        r = self.client.post("/api/projects", json={"name": name}, headers={"x-api-key": self.key})
        assert r.status_code in (200, 201), r.text
        return self.store.get_project(name).id

    def note(self, project: str, text: str, topic: str) -> None:
        r = self.client.post(
            f"/api/projects/{project}/notes",
            json={"text": text, "topic": topic},
            headers={"x-api-key": self.key},
        )
        assert r.status_code in (200, 201), r.text
        assert r.json()["status"] == "saved", r.json()

    def rest(self, q: str, key: str | None = None, **params) -> dict:
        r = self.client.get(
            "/api/search", params={"q": q, **params}, headers={"x-api-key": key or self.key}
        )
        assert r.status_code == 200, r.text
        return r.json()

    def mcp(self, query: str, key: str | None = None, **arguments) -> dict:
        params = {"name": "recall", "arguments": {"query": query, **arguments}}
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}
        r = self.client.post(
            "/mcp", headers={**MCP_HEADERS, "x-api-key": key or self.key}, json=body
        )
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        assert result["isError"] is False, result
        return json.loads(result["content"][0]["text"])

    def web(self, q: str, **params) -> str:
        r = self.client.get("/search", params={"q": q, **params}, headers=self.cookie)
        assert r.status_code == 200, r.text
        return r.text

    def everywhere(self, q: str, **params) -> dict:
        """The same search at the three doors: the `searched` of REST and MCP are equal, and
        the web page shows its `said` above its first hit. Returns the REST answer."""
        rest = self.rest(q, **params)
        mcp = self.mcp(q, **params)
        assert list(rest["searched"]) == list(KEYS), rest["searched"]
        assert mcp["searched"] == rest["searched"]
        page = self.web(q, **params)
        shown = visible(page)
        said = rest["searched"]["said"]
        assert said in shown, (said, shown[:2000])
        first = shown.find("See where this comes from")
        assert first == -1 or shown.index(said) < first  # a line above the hits
        return rest


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """deep: one note of 1,001 passages that all hold "quasar", the oldest twenty times.
    sky: three passages that hold "pulsar" and "comet", then 1,200 newer that hold "comet"
    alone. shelf: a few claims, under every bound."""
    w = World(tmp_path_factory.mktemp("q2"))
    w.project("deep")
    strong = "Quasar " * 20 + "light is the oldest record here."
    weak = "".join(f"Quasar entry {n} sits in row {n} of the deep survey.\n\n" for n in range(1000))
    w.note("deep", f"# Survey\n\n{strong}\n\n{weak}", "survey")
    w.project("sky")
    w.note("sky", "".join(f"Pulsar {n} crossed the comet trail.\n\n" for n in range(3)), "pulsars")
    w.note("sky", "".join(f"Comet {n} passed the east ridge.\n\n" for n in range(1200)), "comets")
    w.project("shelf")
    w.note(
        "shelf",
        "Quill retries the relay three times.\n\nThe relay waits a minute.\n\n"
        "Quill keeps spare nibs in the drawer.\n",
        "quills",
    )
    yield w
    w.close()


@pytest.fixture
def small_budget(monkeypatch):
    """An amount of work under which a query of one part and one of two both score CAP claims,
    the fewest any query scores: the corpora of the cases over the bound are built for that."""
    from cairn.store import ranking

    monkeypatch.setattr(ranking, "SCORED_WORK", CAP)
    assert ranking.scored_max(1) == ranking.scored_max(2) == CAP


def test_the_bounds_are_the_ones_this_file_states():
    from cairn.store import ranking

    assert (ranking.SCORED_MAX, ranking.WORDS_MAX, ranking.PARTS_MAX) == (CAP, WORDS, 64)
    # Q3: the work a search may spend, in claims scored times parts: what 8 parts cost at CAP
    assert ranking.SCORED_WORK == 8 * CAP == 8_000
    bounds = [ranking.scored_max(parts) for parts in (1, 2, 3, 4, 8, 64)]
    assert bounds == [8_000, 4_000, 2_666, 2_000, CAP, CAP]
    assert min(ranking.scored_max(parts) for parts in range(65)) == CAP


# 1. The reproductions


def test_a_word_held_by_1001_claims_ranks_them_all_and_the_strongest_comes_first(world):
    """Q3: the reproduction under the real budget. A query of one part scores up to 8,000
    claims, so the oldest passage, which holds the word twenty times, is ranked and first."""
    deep = world.store.get_project("deep").id
    claims = world.store.get_page(deep, "survey").claims
    found = world.everywhere("quasar", limit=5)
    assert found["hits"][0]["claim_id"] == claims[0].id
    assert found["searched"] == {
        "complete": True,
        "words_searched": 1,
        "words_left_out": [],
        "words_left_out_count": 0,
        "claims_matched": CAP + 1,
        "claims_ranked": CAP + 1,
        "said": NOTHING_LEFT_OUT,
    }


def test_two_words_over_1203_claims_rank_them_all_with_the_rare_words_claims_first(world):
    sky = world.store.get_project("sky").id
    pulsars = {claim.id for claim in world.store.get_page(sky, "pulsars").claims}
    for project in ({}, {"project": "sky"}):
        found = world.everywhere("comet pulsar", limit=5, **project)
        assert {hit["claim_id"] for hit in found["hits"][:3]} == pulsars, project
        searched = found["searched"]
        assert searched["complete"] is True and searched["said"] == NOTHING_LEFT_OUT
        assert (searched["claims_matched"], searched["claims_ranked"]) == (1_203, 1_203)


def test_a_word_held_by_more_claims_than_are_ranked_says_only_the_newest_were_ranked(
    world, small_budget
):
    deep = world.store.get_project("deep").id
    claims = world.store.get_page(deep, "survey").claims
    assert len(claims) == CAP + 1 and claims[0].text.count("Quasar") == 20
    found = world.everywhere("quasar", limit=5)
    # One word: recency stays the rule, so the oldest and strongest claim is not ranked...
    assert len(found["hits"]) == 5
    assert claims[0].id not in {hit["claim_id"] for hit in found["hits"]}
    # ...and the answer says so.
    searched = found["searched"]
    assert searched["complete"] is False
    assert (searched["words_searched"], searched["words_left_out"]) == (1, [])
    assert searched["words_left_out_count"] == 0
    assert (searched["claims_matched"], searched["claims_ranked"]) == (CAP + 1, CAP)
    said = searched["said"]
    assert "1,001" in said and "1,000" in said and "newest" in said
    assert said == ("1,001 claims match, so only the newest 1,000 were ranked. " + EVERY_PROJECT)


def test_a_search_of_one_project_does_not_say_to_search_one_project(world, small_budget):
    """The caller already searched one project: the advice is the rarer word alone."""
    newest = world.everywhere("quasar", project="deep", limit=5)["searched"]
    assert newest["said"] == (
        "More than 1,000 claims match, so only the newest 1,000 were ranked. " + ONE_PROJECT
    )
    likeliest = world.everywhere("comet pulsar", project="sky", limit=5)["searched"]
    assert likeliest["said"] == (
        "More than 1,000 claims match, so only the 1,000 holding the query's rarest words"
        " were ranked, the newest first among equals. " + ONE_PROJECT
    )
    assert "one project" not in newest["said"] + likeliest["said"]


def test_a_word_after_the_first_32_is_named_as_left_out(world):
    found = world.everywhere(f"{FILLER} quasar")
    assert found["hits"] == []  # the 33rd word is not searched, as before
    searched = found["searched"]
    assert searched["complete"] is False
    assert searched["words_searched"] == WORDS
    assert (searched["words_left_out"], searched["words_left_out_count"]) == (["quasar"], 1)
    assert (searched["claims_matched"], searched["claims_ranked"]) == (0, 0)
    assert "'quasar'" in searched["said"] and "32" in searched["said"]
    assert "shorten" in searched["said"].lower()


# 2. The likeliest are ranked


def test_a_rare_words_claims_older_than_a_thousand_common_ones_are_ranked_and_returned(
    world, small_budget
):
    sky = world.store.get_project("sky").id
    pulsars = {claim.id for claim in world.store.get_page(sky, "pulsars").claims}
    assert len(pulsars) == 3
    found = world.everywhere("comet pulsar", limit=5)
    assert {hit["claim_id"] for hit in found["hits"][:3]} == pulsars
    searched = found["searched"]
    assert searched["complete"] is False
    assert (searched["claims_matched"], searched["claims_ranked"]) == (None, CAP)
    assert searched["said"] == (
        "More than 1,000 claims match, so only the 1,000 holding the query's rarest words"
        " were ranked, the newest first among equals. " + EVERY_PROJECT
    )
    # The same words in either order, or one given twice, still rank the rare ones first.
    for query in ("pulsar comet", "comet comet pulsar"):
        hits = world.rest(query, limit=5)["hits"]
        assert {hit["claim_id"] for hit in hits[:3]} == pulsars, query


def test_a_scored_claims_score_does_not_depend_on_whether_the_cap_applied(world, small_budget):
    """The scores of the ranked claims are BM25 over the reach, cap or no cap: here the pulsar
    claims' scores are what FTS5's bm25() gives them on the whole index."""
    sky = world.store.get_project("sky").id
    pulsars = {claim.id for claim in world.store.get_page(sky, "pulsars").claims}
    found = {hit["claim_id"]: hit["score"] for hit in world.rest("comet pulsar", limit=3)["hits"]}
    assert set(found) == pulsars
    expected = dict(reference(world, "comet pulsar"))
    for claim, score in found.items():
        assert score == pytest.approx(expected[claim], rel=1e-12, abs=1e-18)


# 3. Which words count


def test_words_whose_parts_pass_the_bound_are_named_as_left_out(world):
    long_word = "_".join(f"p{n}" for n in range(70))  # one word of 70 parts
    found = world.everywhere(f"quill {long_word} relay")
    searched = found["searched"]
    assert searched["complete"] is False
    assert searched["words_searched"] == 1
    assert searched["words_left_out_count"] == 2
    assert searched["words_left_out"] == [long_word[:64] + "...", "relay"]
    assert found["hits"] and all("quill" in hit["text"].lower() for hit in found["hits"])


def test_a_long_tail_of_words_is_named_within_a_bound(world):
    tail = [f"w{n:03d}" + "x" * 100 for n in range(40)]
    found = world.everywhere(" ".join([FILLER, *tail]))
    searched = found["searched"]
    assert searched["words_left_out_count"] == 40
    assert searched["words_left_out"] == [word[:64] + "..." for word in tail[:5]]
    assert "35 more" in searched["said"]
    assert len(searched["said"]) < 1_000


# 4. Under the bounds, nothing changes


def reference(world: World, query: str) -> list[tuple[str, float]]:
    """(claim id, score) as FTS5's own bm25() ranks the account's index, which holds exactly
    the projects a key of every project reaches."""
    words = re.findall(r"\w+", query)
    index = f"claim_search_{world.store.account_id}"
    con = sqlite3.connect(world.db)
    try:
        return con.execute(
            f"SELECT claim_id, -bm25({index}) FROM {index} WHERE {index} MATCH ?"
            f" ORDER BY bm25({index}), rowid",
            (" OR ".join(f'"{word}"' for word in words),),
        ).fetchall()
    finally:
        con.close()


@pytest.mark.parametrize("query", ["quill relay", "relay", "quill quill nibs", "drawer"])
def test_a_search_under_both_bounds_says_nothing_was_left_out_and_ranks_as_before(world, query):
    found = world.everywhere(query, limit=50)
    expected = reference(world, query)
    assert [hit["claim_id"] for hit in found["hits"]] == [claim for claim, _ in expected]
    for hit, (_, score) in zip(found["hits"], expected, strict=True):
        assert hit["score"] == pytest.approx(score, rel=1e-12, abs=1e-18)
    words = len(re.findall(r"\w+", query))
    assert found["searched"] == {
        "complete": True,
        "words_searched": words,
        "words_left_out": [],
        "words_left_out_count": 0,
        "claims_matched": len(expected),
        "claims_ranked": len(expected),
        "said": NOTHING_LEFT_OUT,
    }


@pytest.mark.parametrize(
    ("query", "params"),
    [("nothing-here-at-all", {}), ("?! ... --", {}), ("quill", {"project": "shelf"})],
)
def test_the_field_is_always_there(world, query, params):
    searched = world.everywhere(query, **params)["searched"]
    # 8.4: a query of no words says that nothing was searched, never "every word"
    said = NO_WORDS if query == "?! ... --" else NOTHING_LEFT_OUT
    assert searched["complete"] is True and searched["said"] == said


def test_the_recall_tools_description_says_what_the_field_means(world):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    r = world.client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": world.key}, json=body)
    tools = {tool["name"]: tool["description"] for tool in r.json()["result"]["tools"]}
    assert "`searched`" in tools["recall"] or "searched" in tools["recall"].split()


# 5. Nothing about another project


def test_a_caller_of_one_project_learns_nothing_from_the_counts_of_another(tmp_path, small_budget):
    w = World(tmp_path)
    try:
        w.project("near")
        w.note("near", "".join(f"Quasar {n} is near.\n\n" for n in range(3)), "near")
        nebulae = "".join(f"Nebula {n} drifts near.\n\n" for n in range(CAP + 1))
        w.note("near", nebulae, "nebulae")  # several words over the cap inside the one project
        w.project("far")
        near = w.store.get_project("near").id
        narrow = w.store.create_key("near-only", projects=[near])
        before = {
            "key of one project": w.rest("quasar", key=narrow),
            "over MCP": w.mcp("quasar", key=narrow),
            "one project searched": w.rest("quasar", project="near")["searched"],
            "several words": w.rest("nebula quasar", key=narrow, limit=5),
        }
        assert before["key of one project"]["searched"]["claims_matched"] == 3
        several = before["several words"]
        assert (several["searched"]["claims_matched"], several["searched"]["claims_ranked"]) == (
            None,
            CAP,
        )
        assert {hit["text"] for hit in several["hits"][:3]} == {
            f"Quasar {n} is near." for n in range(3)
        }
        assert several["searched"]["said"].endswith(ONE_PROJECT)  # it reaches no other
        assert before["one project searched"]["claims_matched"] == 3
        far = "".join(f"Quasar {n} and a nebula are far away.\n\n" for n in range(CAP + 5))
        w.note("far", far, "far")
        after = {
            "key of one project": w.rest("quasar", key=narrow),
            "over MCP": w.mcp("quasar", key=narrow),
            "one project searched": w.rest("quasar", project="near")["searched"],
            "several words": w.rest("nebula quasar", key=narrow, limit=5),
        }
        assert after == before
        # One word over a capped project of a caller who reaches both: the word's count over
        # every reached project is not the count of the project searched, so none is given.
        capped = w.rest("quasar", project="far")["searched"]
        assert (capped["claims_matched"], capped["claims_ranked"]) == (None, CAP)
        assert "1,008" not in capped["said"] and "1,005" not in capped["said"]
        everything = w.rest("quasar")["searched"]
        assert (everything["claims_matched"], everything["claims_ranked"]) == (CAP + 8, CAP)
    finally:
        w.close()
