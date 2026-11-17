"""Sentences of the guides about Q2 (tests/acceptance/test_q2_search_says_what_it_left_out.py),
pinned here so the guides cannot go false unnoticed: what the connect guide says a search
leaves out and how its answer says so. Each test finds its sentences in the guide as a person
reads it (guide_source), then asks cairn over REST and over MCP, on a real store in a
temporary file. Every name is made up."""

import json

import pytest
from guide_source import guide_source
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Claim, Store, ranking

BASE = "http://127.0.0.1:8765"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


def flat(text: str) -> str:
    return " ".join(text.split())


def says(guide: str, *sentences: str) -> None:
    """Each sentence is in the guide, as a person reads it."""
    text = flat(guide_source(guide))
    for sentence in sentences:
        assert flat(sentence) in text, sentence


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "cairn.db")
    key = store.create_key("desk")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:

        def rest(q: str, **params) -> dict:
            r = client.get("/api/search", params={"q": q, **params}, headers={"x-api-key": key})
            assert r.status_code == 200, r.text
            return r.json()

        def mcp(query: str, **arguments) -> dict:
            params = {"name": "recall", "arguments": {"query": query, **arguments}}
            body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}
            r = client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": key}, json=body)
            return json.loads(r.json()["result"]["content"][0]["text"])

        def both(q: str, **params) -> dict:
            found = rest(q, **params)
            assert mcp(q, **params) == found
            return found["searched"]

        yield store, both
    store.close()


def save(store: Store, project: str, slug: str, *texts: str) -> list[str]:
    claims = [Claim(id=f"cl_{slug}_{n}", text=t, span_ids=()) for n, t in enumerate(texts)]
    store.save_page(project, slug, slug.title(), "", claims)
    return [claim.id for claim in claims]


def test_a_search_says_what_it_left_out(env, monkeypatch):
    says(
        "connect",
        "A search says what it left out. `recall` and `GET /api/search` search the first 32"
        " words of a query, fewer when those words hold more than 64 parts between underscores"
        " (as in `retry_count`), and rank as many of the claims that match as a fixed amount"
        " of work allows for the parts of its words: 8,000 for one part, 4,000 for two, 2,000"
        " for four, and 1,000 from eight parts on, as before.",
        "So a short query ranks more of its matches with the work a query of eight parts"
        " already took: a short query does more work than it did, and none does more than a"
        " query of eight parts did before.",
        "Every answer has `searched`: `complete` is true when every word was searched and every"
        " matching claim was ranked.",
        "Otherwise `words_left_out` names the first 5 words left out, each cut to 64 characters,"
        " and `words_left_out_count` counts them all; when more claims match than the search"
        " ranks, `claims_ranked` says how many were ranked and `claims_matched` how many"
        " match, or is null when that was not counted.",
        "A word's count stops at 8,000 matches, so that no search grows with the claims it"
        " reaches.",
        "When a search of one word over every project it reaches stopped there,"
        " `claims_matched` is 8,000, `claims_matched_exact` is false, and `said` says that more"
        " than 8,000 claims match; otherwise the field is not there.",
        "The claims ranked then are those that hold the query's rarest words, the newest first"
        " among equals; for a query of one word, the newest.",
        "`said` says it in a sentence, with what to do: search one project (when the search"
        " covered more than one), add a rarer word or shorten the query.",
    )
    assert (ranking.WORDS_MAX, ranking.PARTS_MAX, ranking.SCORED_MAX) == (32, 64, 1000)
    scored = [ranking.scored_max(parts) for parts in (1, 2, 3, 4, 8, 64)]
    assert scored == [8_000, 4_000, 2_666, 2_000, 1_000, 1_000]
    assert min(ranking.scored_max(parts) for parts in range(ranking.PARTS_MAX + 1)) == 1_000
    store, both = env
    ops = store.create_project("ops").id
    rare = save(store, ops, "rare", *(f"Kestrel {n} and the lamp." for n in range(2)))
    common = save(store, ops, "common", *(f"Lamp {n} is lit." for n in range(1001)))

    # Every word searched, every match ranked.
    complete = both("kestrel")
    assert complete["complete"] is True
    assert (complete["claims_matched"], complete["claims_ranked"]) == (2, 2)

    # The first 32 words; the first 5 left out named, each cut to 64 characters.
    filler = " ".join(f"zq{n}" for n in range(32))
    tail = [f"w{n}" + "x" * 80 for n in range(7)]
    cut = both(" ".join([filler, *tail]))
    assert cut["complete"] is False and cut["words_searched"] == 32
    assert cut["words_left_out"] == [word[:64] + "..." for word in tail[:5]]
    assert cut["words_left_out_count"] == 7

    # Fewer when the words hold more than 64 parts between underscores.
    parts = [f"a{n}_b{n}_c{n}" for n in range(30)]
    assert both(" ".join(parts))["words_searched"] == 21  # 63 parts; a 22nd word makes 66

    # 1,003 match "lamp", and "lamp kestrel": under the real budget every match is ranked.
    assert both("lamp") == {**complete, "claims_matched": 1003, "claims_ranked": 1003}
    assert both("lamp kestrel")["claims_ranked"] == 1003
    assert both("lamp kestrel")["complete"] is True

    # More match than the search ranks (here an amount of work under which one part and two
    # rank 1,000, the corpus being built for that): one word ranks the newest, counted when
    # known.
    monkeypatch.setattr(ranking, "SCORED_WORK", 1_000)
    one = both("lamp")
    assert (one["claims_matched"], one["claims_ranked"]) == (1003, 1000)
    assert "newest" in one["said"]
    hits = both("lamp", limit=50)
    assert hits["claims_ranked"] == 1000
    assert "claims_matched_exact" not in one  # under the count's bound

    # A word's count stops at its bound (8,000; here 1,000, the corpus being built for that).
    assert ranking.COUNTED_MAX == 8_000
    monkeypatch.setattr(ranking, "COUNTED_MAX", 1_000)
    stopped = both("lamp")
    assert (stopped["claims_matched"], stopped["claims_matched_exact"]) == (1000, False)
    assert stopped["said"].startswith("More than 1,000 claims match, so only the newest 1,000")
    monkeypatch.setattr(ranking, "COUNTED_MAX", 8_000)

    # Several words rank the claims that hold the rarest first; the count is null.
    several = both("lamp kestrel")
    assert (several["claims_matched"], several["claims_ranked"]) == (None, 1000)
    store_hits = store.find("lamp kestrel", None, 2).hits
    assert {hit.claim_id for hit in store_hits} == set(rare)
    assert common  # the 1,001 newer claims hold only "lamp"

    # What to do: shorten the query; and, over the cap, add a rarer word, and search one
    # project only when the search covered more than one.
    assert "shorten the query" in cut["said"].lower()
    for answer in (one, several):  # "ops" is the only project: one project searched
        assert "add a rarer word" in answer["said"].lower()
        assert "search one project" not in answer["said"]
    store.create_project("spare")
    for answer in (both("lamp"), both("lamp kestrel")):  # two projects searched
        assert (
            "Search one project so that fewer claims match, or add a rarer word" in answer["said"]
        )
    assert "search one project" not in both("lamp", project="ops")["said"]
