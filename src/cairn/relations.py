"""Related pages: the rules that need no store.

A relation between two pages of one project is worked out when it is asked for, by rule, from
what the pages say, and every relation carries the evidence it rests on. No model is asked
and nothing of a relation is stored: cairn.store.relating reads the pages as they are, in one
read, so forgetting a note changes the relations in the write that changes the pages.

Two kinds of relation:
- SHARES: a claim of each page states the same term. A term is a fact as
  cairn.facts.extract_facts reads it from the passage a claim cites, of one of TERM_KINDS,
  and two facts are one term when their kind is the same and facts.compared of each, as it
  was read in its passage, is the same text (`terms_of`). A fact is never read a second
  time, alone, and never read from a claim's text cut from its passage.
- MENTIONED_BY: a claim of the other page holds this page's title as a phrase (the search
  index decides: cairn.store.relating).

Which terms relate pages (`specific`): every url, path, flag and version; an inline code
only when it holds a character that is not a letter (a digit, a space, punctuation:
`pyproject.toml`, `os_login_2fa`, `compact --if-due`) or is at least CODE_MIN characters
long, so a plain word in backticks (`name`, `true`) relates nothing; and no term without a
letter or a digit, of which the index holds no word. Numbers, dates and code blocks never
relate pages.

Which terms are common, and relate none: a term more than COMMON pages of the project hold;
and a term whose words more than CLAIMS_COMMON claims of the project hold, the asking
page's own among them. The second is the index's count of the claims whose text holds the
term's words, whatever their passages hold: 64 claims that say "dry run" make `--dry-run`
common. Both are known whole from what is read (cairn.store.relating reads one claim more
than CLAIMS_COMMON, and then every claim that counts), so each is the same whichever page
asks. A title is common the same way: one that more than COMMON other pages name, or that
more than CLAIMS_COMMON claims of other pages hold, relates none by mention (a page's own
claims do not mention it, and do not count).

Which pages are the best (`best`): a shared term weighs 1 / (the pages that hold it - 1), so a
term only the two pages hold weighs most, and a mention weighs as one term held by two
pages. A page's weight is the sum over its reasons, an exact fraction; pages are given
heaviest first, equal weights by slug, and each with its WHY_MAX weightiest reasons (equal
weights: a mention, then the terms by kind and text) and how many more there are. No answer
shows a weight: the order is the answer, and each reason says how many pages hold its term.

When a page holds more terms than are looked up (cairn.store.relating.TERMS_MAX), the
shortest are looked up first (`looked_up_before`): the terms a few pages share are short (a
flag, a path, a name), and the long ones (a command line with its arguments, a sentence in
code) are mostly one page's alone, which relate nothing.
"""

from collections.abc import Mapping, Sequence
from fractions import Fraction
from typing import Any, NamedTuple

from cairn.facts import compared, extract_facts
from cairn.spans import Span

TERM_KINDS = ("url", "path", "flag", "version", "inline_code")  # the facts that can be terms
CODE_MIN = 12  # an inline code of letters only relates pages from this many characters
COMMON = 5  # a term more pages of the project hold than this relates none
CLAIMS_COMMON = 64  # nor does one whose words more claims of the project hold than this
WHY_MAX = 3  # the reasons an answer gives for one related page; it counts the rest
RELATED_MAX = 20  # the most related pages one answer gives
SHARES, MENTIONED_BY = "shares", "mentioned_by"  # the kinds of relation
TITLE = "title"  # the kind of what a mention holds: the page's title


class Term(NamedTuple):
    """What two claims share: a fact's kind, and its text as facts.compared reads it."""

    kind: str
    text: str


def specific(kind: str, text: str) -> bool:
    """Whether a term of this kind and text relates pages (the module's rule)."""
    if kind not in TERM_KINDS or not any(char.isalnum() for char in text):
        return False
    if kind != "inline_code":
        return True
    return len(text) >= CODE_MIN or not text.isalpha()


def terms_of(span: Span) -> list[Term]:
    """The terms of a passage that relate pages, each once, in the order they are read."""
    found = (
        Term(fact.kind, compared(fact.as_read))
        for fact in extract_facts(span)
        if fact.kind in TERM_KINDS
    )
    return list(dict.fromkeys(term for term in found if specific(*term)))


def looked_up_before(term: Term) -> tuple[int, str, str]:
    """The order a page's terms are looked up in: the shortest first, then by kind and text."""
    return len(term.text), term.kind, term.text


class Reason(NamedTuple):
    """Why two pages are related: the kind of relation, the term both state (or this page's
    title), its kind, how many pages of the project hold it (this page among them; 2 for a
    mention), and the claim on each side (none here for a mention). The count is exact: a
    term whose pages are not all known gives no reason (cairn.store.relating)."""

    kind: str
    term: str
    term_kind: str
    pages: int
    here: Any
    there: Any

    @property
    def weight(self) -> Fraction:
        return Fraction(1, self.pages - 1)


def best(
    reasons: Mapping[str, Sequence[Reason]], limit: int
) -> tuple[list[tuple[str, list[Reason], int]], int]:
    """The `limit` pages most related, heaviest first and equal weights by slug: each as
    (its slug, its WHY_MAX weightiest reasons, how many more it has); and how many related
    pages are left out."""
    weighed = sorted(
        (
            (-sum(reason.weight for reason in found), slug)
            for slug, found in reasons.items()
            if found
        )
    )
    given = []
    for _, slug in weighed[:limit]:
        why = sorted(reasons[slug], key=lambda r: (-r.weight, r.kind, r.term_kind, r.term))
        given.append((slug, why[:WHY_MAX], max(0, len(why) - WHY_MAX)))
    return given, max(0, len(weighed) - limit)
