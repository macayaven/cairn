"""Q2: which words of a query count (ranking.split), the facts of what a search left out
(cairn.store.models.Searched) and how the doors word them (cairn.memory.searched), from
those facts alone, so REST, MCP and the web page cannot disagree."""

import pytest

from cairn.memory import NOTHING_LEFT_OUT, searched
from cairn.store import ranking
from cairn.store.models import Searched


def facts(**given) -> Searched:
    base = dict(
        words_searched=2,
        words_left_out=(),
        words_left_out_count=0,
        claims_matched=3,
        claims_ranked=3,
        ranked_by="all",
        projects_searched=2,
    )
    return Searched(**{**base, **given})


# Which words count


def test_the_first_32_words_count_and_the_rest_are_left_out_in_order():
    query = " ".join(f"w{n}" for n in range(40))
    counted, left_out = ranking.split(query)
    assert counted == [f"w{n}" for n in range(32)]
    assert left_out == [f"w{n}" for n in range(32, 40)]
    assert ranking.words(query) == counted


def test_a_word_given_twice_counts_twice_and_takes_two_of_the_32():
    counted, left_out = ranking.split("quill " * 33)
    assert counted == ["quill"] * 32 and left_out == ["quill"]


def test_words_stop_before_the_one_whose_parts_would_pass_64():
    words = [f"a{n}_b{n}_c{n}" for n in range(30)]  # three parts each: 21 make 63
    counted, left_out = ranking.split(" ".join(words))
    assert counted == words[:21] and left_out == words[21:]
    # A part already counted costs nothing: the same three parts again still fit.
    counted, _ = ranking.split(" ".join([*words[:21], words[0], "a0_b1"]))
    assert counted == [*words[:21], words[0], "a0_b1"]


def test_a_word_of_more_than_64_parts_leaves_it_and_every_word_after_it_out():
    long_word = "_".join(f"p{n}" for n in range(65))
    assert ranking.split(f"quill {long_word} relay") == (["quill"], [long_word, "relay"])
    assert ranking.split("_ __ quill") == (["_", "__", "quill"], [])  # "_" holds no part


# The facts


def test_complete_means_every_word_searched_and_every_match_ranked():
    assert facts().complete
    assert not facts(words_left_out=("x",), words_left_out_count=1).complete
    assert not facts(claims_matched=None, claims_ranked=1000, ranked_by="newest").complete
    assert not facts(claims_matched=1001, claims_ranked=1000, ranked_by="newest").complete


# The wording


def test_a_search_that_left_nothing_out_says_so():
    assert searched(facts()) == {
        "complete": True,
        "words_searched": 2,
        "words_left_out": [],
        "words_left_out_count": 0,
        "claims_matched": 3,
        "claims_ranked": 3,
        "said": NOTHING_LEFT_OUT,
    }
    assert NOTHING_LEFT_OUT == (
        "Every word of the query was searched, and every matching claim was ranked."
    )


def test_a_word_after_the_first_32_is_named():
    said = searched(facts(words_searched=32, words_left_out=("quasar",), words_left_out_count=1))
    assert said["words_left_out"] == ["quasar"] and said["complete"] is False
    assert said["said"] == (
        "A search takes the first 32 words of a query; 1 more was left out: 'quasar'."
        " Shorten the query to the words that matter most."
    )


def test_words_left_out_are_shown_five_at_most_each_cut_at_64_characters():
    long = tuple(f"w{n}" + "x" * 100 for n in range(5))
    said = searched(facts(words_searched=32, words_left_out=long, words_left_out_count=40))
    assert said["words_left_out"] == [word[:64] + "..." for word in long]
    assert said["words_left_out_count"] == 40
    assert "and 35 more." in said["said"] and len(said["said"]) < 1_000
    assert said["said"].count("...") == 5 and "x" * 65 not in said["said"]


def test_words_cut_by_the_parts_bound_say_why():
    said = searched(facts(words_searched=1, words_left_out=("a_b", "c"), words_left_out_count=2))
    assert said["said"] == (
        "A search takes the words of a query until they hold 64 parts between underscores;"
        " the first 1 was searched and 2 were left out: 'a_b' and 'c'."
        " Shorten the query to the words that matter most."
    )


def test_a_one_word_search_over_the_cap_says_the_newest_were_ranked():
    said = searched(facts(words_searched=1, claims_matched=1001, claims_ranked=1000,
                          ranked_by="newest"))  # fmt: skip
    assert said["said"] == (
        "1,001 claims match, so only the newest 1,000 were ranked."
        " Search one project so that fewer claims match, or add a rarer word to rank the claims"
        " that hold it first."
    )
    unknown = searched(facts(claims_matched=None, claims_ranked=1000, ranked_by="newest"))
    assert unknown["said"].startswith("More than 1,000 claims match, so only the newest 1,000")
    assert unknown["claims_matched"] is None


def test_a_search_of_several_words_over_the_cap_says_the_rarest_were_ranked():
    said = searched(facts(claims_matched=None, claims_ranked=1000, ranked_by="likeliest"))
    assert said["said"] == (
        "More than 1,000 claims match, so only the 1,000 holding the query's rarest words"
        " were ranked, the newest first among equals."
        " Search one project so that fewer claims match, or add a rarer word to rank the claims"
        " that hold it first."
    )


@pytest.mark.parametrize("by", ["newest", "likeliest"])
def test_a_search_of_one_project_says_only_to_add_a_rarer_word(by):
    one = searched(
        facts(claims_matched=None, claims_ranked=1000, ranked_by=by, projects_searched=1)
    )
    assert one["said"].endswith(". Add a rarer word to rank the claims that hold it first.")
    assert "one project" not in one["said"]
    several = searched(facts(claims_matched=None, claims_ranked=1000, ranked_by=by))
    assert several["said"].endswith(
        ". Search one project so that fewer claims match, or add a rarer word to rank the claims"
        " that hold it first."
    )
    # The count of projects decides the sentence; it is not a field of the answer.
    assert "projects_searched" not in one and set(one) == set(several)


def test_both_bounds_at_once_say_both():
    words = {"words_searched": 32, "words_left_out": ("x",), "words_left_out_count": 1}
    claims = {"claims_matched": None, "claims_ranked": 1000, "ranked_by": "likeliest"}
    said = searched(facts(**words, **claims))
    assert said["said"].startswith("A search takes the first 32 words")
    assert "More than 1,000 claims match" in said["said"]


@pytest.mark.parametrize(
    "fact",
    [
        facts(),
        facts(words_searched=32, words_left_out=("x",), words_left_out_count=1),
        facts(claims_matched=1001, claims_ranked=1000, ranked_by="newest"),
        facts(claims_matched=None, claims_ranked=1000, ranked_by="likeliest"),
    ],
)
def test_the_web_pages_count_line_keeps_its_own_word(fact):
    """search.html says "N claims matched" only below the most it shows; no sentence of this
    field uses that word, so the page's own test of it holds."""
    assert "matched" not in searched(fact)["said"]


# R6: a count that stopped at its bound


def test_a_count_that_stopped_says_more_than_and_carries_the_fact():
    stopped = facts(
        words_searched=1,
        claims_matched=8000,
        claims_matched_exact=False,
        claims_ranked=8000,
        ranked_by="newest",
    )
    said = searched(stopped)
    assert said["said"] == (
        "More than 8,000 claims match, so only the newest 8,000 were ranked."
        " Search one project so that fewer claims match, or add a rarer word to rank the claims"
        " that hold it first."
    )
    assert (said["claims_matched"], said["claims_matched_exact"]) == (8000, False)
    assert list(said)[4:7] == ["claims_matched", "claims_matched_exact", "claims_ranked"]
    assert "matched" not in said["said"]


def test_an_exact_count_answers_as_before_with_no_new_field():
    assert facts().claims_matched_exact
    assert "claims_matched_exact" not in searched(facts())
    exact = searched(facts(claims_matched=1001, claims_ranked=1000, ranked_by="newest"))
    assert "claims_matched_exact" not in exact
    assert exact["said"].startswith("1,001 claims match, so only the newest 1,000")


def test_underscores_alone_report_zero_searched_words_without_changing_split():
    assert ranking.split("_ __ quill") == (["_", "__", "quill"], [])
    assert ranking.found(["___"], []).searched.words_searched == 0
    assert ranking.found(["_", "__"], []).searched.words_searched == 0
    assert ranking.found(["_", "quill"], []).searched.words_searched == 2
