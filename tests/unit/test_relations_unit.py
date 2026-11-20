"""The rules of related pages that need no store (cairn.relations): which facts of a passage
are terms that relate pages, the order a page's terms are looked up in, what a reason weighs,
and which related pages are the best. Made-up names; nothing is stored or searched here."""

from fractions import Fraction

import pytest

from cairn import relations
from cairn.relations import MENTIONED_BY, SHARES, TITLE, Reason, Term, best, specific, terms_of
from cairn.spans import Span


def passage(text: str) -> Span:
    return Span(
        id="sp_unit",
        source_id="unit",
        kind="paragraph",
        heading_path=(),
        start_line=1,
        end_line=text.count("\n") + 1,
        text=text,
    )


def test_the_stated_constants():
    assert relations.TERM_KINDS == ("url", "path", "flag", "version", "inline_code")
    assert (relations.COMMON, relations.WHY_MAX, relations.CODE_MIN) == (5, 3, 12)
    assert relations.CLAIMS_COMMON == 64


def test_a_reason_says_how_many_pages_hold_its_term_and_nothing_of_how_far_it_was_read():
    """Every count given is exact: a term whose pages are not all known gives no reason."""
    assert Reason._fields == ("kind", "term", "term_kind", "pages", "here", "there")


# Which terms relate pages: one function, one table.

SPECIFIC = [
    # every url, path, flag and version
    ("url", "https://example.invalid/a", True),
    ("path", "/var/log/a_b", True),
    ("path", "~/notes", True),
    ("flag", "--audit-keep", True),
    ("flag", "-x", True),
    ("version", "0.1.0", True),
    ("version", "v2", True),
    # inline code that holds a character that is not a letter
    ("inline_code", "pyproject.toml", True),
    ("inline_code", "--audit-keep", True),
    ("inline_code", "os_login_2fa", True),
    ("inline_code", "compact --if-due", True),
    ("inline_code", "a1", True),
    ("inline_code", " a", True),  # a space is not a letter: the rule's letter
    # inline code of letters only: a plain word in backticks, until it is CODE_MIN long
    ("inline_code", "name", False),
    ("inline_code", "serve", False),
    ("inline_code", "false", False),
    ("inline_code", "true", False),
    ("inline_code", "id", False),
    ("inline_code", "naïve", False),
    ("inline_code", "abcdefghijk", False),  # 11 letters
    ("inline_code", "abcdefghijkl", True),  # 12
    ("inline_code", "authorization", True),
    # nothing the index has a word of: no letter and no digit
    ("inline_code", "->", False),
    ("inline_code", "___", False),
    ("inline_code", "", False),
    ("path", "~/~", False),
    # none longer than TERM_MAX characters
    ("url", "https://fable.example/" + "a" * 178, True),  # 200
    ("url", "https://fable.example/" + "a" * 179, False),  # 201
    ("inline_code", "quill run " + "--json " * 27 + "x", True),  # 200
    ("inline_code", "quill run " + "--json " * 27 + "xy", False),
    ("path", "/" + "a" * 200, False),
    # numbers, dates and code blocks never relate pages
    ("number", "5 min", False),
    ("number", "2,700", False),
    ("date", "2026-03-14", False),
    ("code_block", "quill run --json", False),
]


@pytest.mark.parametrize(("kind", "text", "relates"), SPECIFIC)
def test_a_term_relates_pages_only_when_it_is_specific(kind, text, relates):
    assert specific(kind, text) is relates


def test_a_passages_terms_are_its_specific_facts_as_compared_each_once_in_the_order_read():
    span = passage(
        "On 2026-03-14 run `compact --if-due` with --audit-keep 7 on v1.2.3 for 5 min; see"
        " https://example.invalid/A and ~/Notes, then `serve`, `README.md` and --audit-keep."
    )
    assert terms_of(span) == [
        Term("inline_code", "compact --if-due"),
        Term("flag", "--audit-keep"),
        Term("version", "v1.2.3"),
        Term("url", "https://example.invalid/a"),  # as facts.compared reads it: case folded
        Term("path", "~/notes"),
        Term("inline_code", "readme.md"),
    ]


def test_a_term_is_the_fact_as_read_in_its_passage_never_a_piece_read_again():
    """The third report's note: the URL's last underscore is no emphasis in its passage. Read
    alone it would be, and the term would be another."""
    (term,) = terms_of(passage("See <https://example.invalid/(2_> today."))
    assert term == Term("url", "https://example.invalid/(2_")
    emphasised = terms_of(passage("Now _v1.2.3_ and `a_b`."))
    assert emphasised == [Term("version", "v1.2.3"), Term("inline_code", "a_b")]


def test_code_blocks_hold_no_term():
    span = passage("```\nquill run --json https://example.invalid/a\n```")
    assert terms_of(span.model_copy(update={"kind": "code"})) == []


# The order a page's terms are looked up in, when it holds more than are looked up.


def test_the_terms_of_a_page_are_looked_up_shortest_first():
    terms = [
        Term("inline_code", "sudo journalctl -u quill"),
        Term("url", "https://example.invalid/a"),
        Term("flag", "--json"),
        Term("inline_code", "--json"),
        Term("inline_code", "a.b"),
        Term("path", "/mcp"),
    ]
    assert sorted(terms, key=relations.looked_up_before) == [
        Term("inline_code", "a.b"),
        Term("path", "/mcp"),
        Term("flag", "--json"),  # as long as the next: by kind, then by text
        Term("inline_code", "--json"),
        Term("inline_code", "sudo journalctl -u quill"),
        Term("url", "https://example.invalid/a"),
    ]


# What a reason weighs, and which pages are the best.


def shares(term: str, pages: int, kind: str = "flag") -> Reason:
    return Reason(SHARES, term, kind, pages, here="here", there="there")


def mention(title: str = "Quill") -> Reason:
    return Reason(MENTIONED_BY, title, TITLE, 2, here=None, there="there")


def test_a_term_weighs_one_over_the_other_pages_that_hold_it():
    assert [shares("--a", n).weight for n in (2, 3, 5)] == [1, Fraction(1, 2), Fraction(1, 4)]
    assert mention().weight == 1  # as one term held by two pages


def test_pages_are_ranked_by_weight_then_slug_and_only_the_best_are_given():
    reasons = {
        "brook": [shares("--a", 3), shares("--b", 3)],  # 1/2 + 1/2
        "ada": [shares("--c", 2)],  # 1
        "quill": [shares("--a", 3)],  # 1/2
        "fable": [mention(), shares("--d", 5)],  # 1 + 1/4
    }
    found, left_out = best(reasons, limit=3)
    assert [slug for slug, _, _ in found] == ["fable", "ada", "brook"]  # 5/4, then 1 and 1 by slug
    assert left_out == 1
    everything, none_left = best(reasons, limit=20)
    assert [slug for slug, _, _ in everything] == ["fable", "ada", "brook", "quill"]
    assert none_left == 0
    assert best({}, limit=8) == ([], 0)


def test_a_page_gives_its_weightiest_reasons_and_counts_the_rest():
    reasons = {
        "ada": [
            shares("--e", 5),
            shares("--b", 2),
            shares("--d", 3),
            mention(),
            shares("--a", 2),
            shares("--c", 3),
        ]
    }
    ((slug, why, more),) = best(reasons, limit=8)[0]
    # weight 1 three times (the mention first, then the terms by kind and text), then 1/2
    assert [(r.kind, r.term) for r in why] == [
        (MENTIONED_BY, "Quill"),
        (SHARES, "--a"),
        (SHARES, "--b"),
    ]
    assert (slug, more) == ("ada", 3)
    assert len(why) == relations.WHY_MAX


def test_sums_of_weights_are_exact():
    """Thirds and halves add up without rounding: 1/2 + 1/3 + 1/6 is 1, and ties are ties."""
    reasons = {
        "ada": [shares("--a", 3), shares("--b", 4), shares("--c", 7)],
        "brook": [shares("--d", 2)],
    }
    assert [slug for slug, _, _ in best(reasons, limit=8)[0]] == ["ada", "brook"]


def test_a_term_longer_than_the_bound_as_written_or_as_compared_is_no_term():
    """TERM_MAX characters: of the term as it is compared, which an answer gives, and of the
    fact as it is written in its passage, which a line given must hold."""
    assert relations.TERM_MAX == 200 and 2 * relations.TERM_MAX <= relations.LINE_MAX
    at_bound = "https://fable.example/" + "a" * 178
    assert terms_of(passage(f"See {at_bound} first.")) == [Term("url", at_bound)]
    assert terms_of(passage(f"See {at_bound}a first.")) == []
    # written longer than it is compared: spaces at the end of a code's line are not compared
    padded = "`a1" + " " * 198 + "`"
    assert len(padded) == 202 and terms_of(passage(f"Run {padded} now.")) == [
        Term("inline_code", "a1")
    ]
    assert terms_of(passage("Run `a1" + " " * 199 + "` now.")) == []
    # compared longer than it is written: one letter that folds to three
    folded = "`a1" + "\u0390" * 67 + "`"
    assert terms_of(passage(f"Run {folded} now.")) == []
    assert len(terms_of(passage("Run `a1" + "\u0390" * 66 + "` now."))[0].text) == 200


# A long claim: where its term stands in the passage, and the line that holds it


def test_the_bounds_of_a_claim_given_are_stated():
    assert (relations.TEXT_MAX, relations.LINE_MAX) == (2_000, 400)


def test_a_term_is_placed_where_its_first_fact_stands_in_the_passage():
    span = passage(
        "Brook pins `quill.lock` at dawn.\n"
        "Ada reads https://fable.example/Runbook, then `quill.lock` and `Quill.Lock` again."
    )
    placed = relations.placed(span)
    assert list(placed) == terms_of(span)
    assert {term: span.text[slice(*at)] for term, at in placed.items()} == {
        Term("inline_code", "quill.lock"): "quill.lock",  # the first of its three, as written
        Term("url", "https://fable.example/runbook"): "https://fable.example/Runbook",
    }
    assert placed[Term("inline_code", "quill.lock")] == (12, 22)


def line(text: str, written: str) -> tuple[str, bool]:
    start = text.index(written)
    return relations.line_of(text, start, start + len(written))


def test_the_line_that_holds_a_term_is_given_whole_when_it_is_short():
    text = "Brook pins the file.\nAda reads `quill.lock` daily.\nQuill files the report."
    assert line(text, "quill.lock") == ("Ada reads `quill.lock` daily.", False)
    assert line(text, "Brook") == ("Brook pins the file.", False)  # the first line
    assert line(text, "report") == ("Quill files the report.", False)  # the last, no line end
    assert line("`quill.lock`", "quill.lock") == ("`quill.lock`", False)
    exactly = "a" * (relations.LINE_MAX - 4) + " --x"
    assert line("one\n" + exactly + "\ntwo", "--x") == (exactly, False)


@pytest.mark.parametrize("before", [0, 1, 150, 199, 200, 201, 800, 1_588, 1_589, 1_590])
def test_a_longer_line_is_given_as_a_window_of_it_that_holds_the_term(before):
    """LINE_MAX characters of the line, the term in their middle where the line lets it be;
    never a character of another line."""
    written = "`quill.lock`"
    long = "x" * before + written + "y" * (1_590 - before)
    assert len(long) == 1_602
    window, cut = line("first line\n" + long + "\nlast line", "quill.lock")
    assert cut is True and len(window) == relations.LINE_MAX
    assert window in long and "quill.lock" in window
    lead = window.index("quill.lock")
    room = relations.LINE_MAX - len("quill.lock")
    assert lead == min(max(room // 2, room - (1_590 - before) - 1), before + 1)


def test_a_term_as_long_as_the_window_or_longer_is_its_own_line():
    """No window is ever less than the term. No term is that long: relations.TERM_MAX keeps
    every term, as written, to half a window, so every line given is LINE_MAX at most."""
    written = "https://fable.example/" + "a" * relations.LINE_MAX
    text = "see " * 200 + written + " and more" * 100
    assert line(text, written) == (written, True)
    assert line("x" + "a" * relations.LINE_MAX, "a" * relations.LINE_MAX) == (
        "a" * relations.LINE_MAX,
        True,
    )
