"""Related pages: the rules that need no store.

A relation between two pages of one project is worked out when it is asked for, by rule, from
what the pages say, and every relation carries the evidence it rests on. No model is asked
and nothing of a relation is stored: cairn.store.relating reads the pages as they are, in one
read, so forgetting a note changes the relations in the write that changes the pages.

Two kinds of relation:
- SHARES: a claim of each page states the same term. A term is a fact as
  cairn.facts.extract_facts reads it from the passage a claim cites, of one of TERM_KINDS,
  and two facts are one term when their kind is the same and their text as read in the
  passage is the same, capitals included (`terms_of`): `-L` and `-l` are two flags, and a
  path, code or an address that differs by a capital is another term, the host of an
  address like the rest. The text is facts.cased of the fact: as facts.compared reads it,
  but for its case; so a number's thousands separators and white space at the fact's end
  are left out of the comparison, and nothing else is. A term is given as it is read. A
  fact is never read a second time, alone, and never read from a claim's text cut from its
  passage.
- MENTIONED_BY: a claim of the other page names this page's title in its prose. Each place
  the claim's text holds the title's words side by side is judged by itself, whatever
  other place overlaps it: it names the title when it lies whole outside every stretch of
  the passage the claim cites that is a url, a path, a flag, a version or inline code, or
  a fenced code block (`covered`: the facts of cairn.facts.extract_facts on the passage,
  each by its own start and end, and a fenced block from its opening line, the word after
  its marks included; nothing is read a second time alone), and outside what the Markdown
  parser reads as code in the passage read alone (cairn.spans.code_read: code spans,
  fenced blocks with their opening line, indented blocks; text indented under a list item
  is the item's prose). The search index's own tokenizer says where the places are, on
  the text with those stretches blanked out where they stand (`blanked`;
  cairn.store.relating), and nothing here reads the words. So a page titled Deploy is not
  named by a claim whose only "deploy" stands in `deploy/compose.yaml`, or after the marks
  that open a fence, nor one titled Audit by `--audit-keep`; what the facts and the parser
  do not read as one of those is prose, so a bare `deploy/notes.md` or `ada@deploy.dev`
  names Deploy. The places of a claim's text are
  places of its passage because the claim is the passage's text; where it is not (a page
  saved by hand; a claim that cites no passage of the project), the mention stands as the
  index has it. A claim whose text holds a mark of the index or a null character names no
  page: where the index matched in it cannot be checked (cairn.store.relating.MARKS).

Which terms relate pages (`specific`): every url, path, flag and version; an inline code
only when it holds a character that is not a letter (a digit, a space, punctuation:
`pyproject.toml`, `os_login_2fa`, `compact --if-due`) or is at least CODE_MIN characters
long, so a plain word in backticks (`name`, `true`) relates nothing; and no term without a
letter or a digit, of which the index holds no word. Numbers, dates and code blocks never
relate pages. Nor does a term longer than TERM_MAX characters as it is written in its
passage (`placed`): an answer gives the term and a line that holds it, and both are
bounded. The longest term that relates two of this repository's own documents is
111 characters.

Which terms are common, and relate none: a term more than COMMON pages of the project hold;
and a term whose words more than CLAIMS_COMMON claims of the project hold, the asking
page's own among them. The second is the index's count of the claims whose text holds the
term's words, whatever their passages hold: 64 claims that say "dry run" make `--dry-run`
common. Both are known whole from what is read (cairn.store.relating reads one claim more
than CLAIMS_COMMON, and then every claim that counts), so each is the same whichever page
asks. A title is common the same way: one that more than COMMON other pages name by the
rule above, or whose words more than CLAIMS_COMMON claims of other pages hold, in prose or
not, relates none by mention (a page's own claims do not mention it, and do not count).

Which pages are the best (`best`): a shared term weighs 1 / (the pages that hold it - 1), so a
term only the two pages hold weighs most, and a mention weighs as one term held by two
pages. A page's weight is the sum over its reasons, an exact fraction; pages are given
heaviest first, equal weights by slug, and each with its WHY_MAX weightiest reasons (equal
weights: a mention, then the terms by kind and text) and how many more there are. No answer
shows a weight: the order is the answer, and each reason says how many pages hold its term.

What a reason gives of a claim: its text whole up to TEXT_MAX characters. A longer claim
is marked cut, and what its reader must see is the term stated, not the claim's first
words: so a claim cut that states a shared term is given by the line of its passage that
holds the term (`line_of`), in place of its text: the line the term's fact stands on
(`placed`: the fact's own start and end, never a second search of the text), whole up to
LINE_MAX characters, else LINE_MAX characters of it around the term. A claim cut that
mentions the page is given the same way by the line of its own text that names the title,
where the index matched it (cairn.store.relating). Only where the index cannot say which
line that is does a claim cut give its first TEXT_MAX characters instead; an answer never
holds a head beside a line (cairn.memory words it). And the passages it cites: the first
PASSAGES_MAX, and a count of the others. A claim cites one passage as it is compiled; a
tidy folds claims of the same text into one that cites every passage of theirs, without a
bound, and the claim is read by its first.

When a page holds more terms than are looked up (cairn.store.relating.TERMS_MAX), the
shortest are looked up first (`looked_up_before`): the terms a few pages share are short (a
flag, a path, a name), and the long ones (a command line with its arguments, a sentence in
code) are mostly one page's alone, which relate nothing. Measured on this repository's own
documents, each filed as a page (32 pages; 15 hold more than 64 terms, 5 more than 256, the
most 692), against a read of every term: of the 212 places in the pages' best 8, looking up
64 terms the shortest first keeps 195 and the longest first 147; looking up 256, as shipped,
the shortest first keeps 211 and the longest first 207.
"""

from collections.abc import Iterable, Mapping, Sequence
from fractions import Fraction
from typing import Any, NamedTuple

from cairn.facts import Fact, cased, extract_facts
from cairn.spans import Span

TERM_KINDS = ("url", "path", "flag", "version", "inline_code")  # the facts that can be terms
CODE_MIN = 12  # an inline code of letters only relates pages from this many characters
TERM_MAX = 200  # a term longer than this as written relates none
COMMON = 5  # a term more pages of the project hold than this relates none
CLAIMS_COMMON = 64  # nor does one whose words more claims of the project hold than this
WHY_MAX = 3  # the reasons an answer gives for one related page; it counts the rest
RELATED_MAX = 20  # the most related pages one answer gives
RELATED_DEFAULT = 5  # the related pages an answer gives when no number is asked for
TEXT_MAX = 600  # the characters of a claim's text an answer gives; a longer one is cut
LINE_MAX = 400  # the characters of the line that holds a term, given beside a claim cut
PASSAGES_MAX = 8  # the passages a claim given names, the first it cites; it counts the rest
SHARES, MENTIONED_BY = "shares", "mentioned_by"  # the kinds of relation
TITLE = "title"  # the kind of what a mention holds: the page's title


class Term(NamedTuple):
    """What two claims share: a fact's kind, and its text as facts.cased reads it: as it is
    written in the passage, capitals included."""

    kind: str
    text: str


def specific(kind: str, text: str) -> bool:
    """Whether a term of this kind and text relates pages (the module's rule)."""
    if kind not in TERM_KINDS or not any(char.isalnum() for char in text):
        return False
    if len(text) > TERM_MAX:
        return False
    if kind != "inline_code":
        return True
    return len(text) >= CODE_MIN or not text.isalpha()


def placed(span: Span, facts: Sequence[Fact] | None = None) -> dict[Term, tuple[int, int]]:
    """The terms of a passage that relate pages, each once, in the order they are read: each
    with where its first fact stands in the passage's text, the fact's own start and end. A
    fact written in more than TERM_MAX characters is no term. `facts`: the passage's, when
    the caller has read them already (extract_facts of the passage whole)."""
    found: dict[Term, tuple[int, int]] = {}
    for fact in extract_facts(span) if facts is None else facts:
        if fact.kind in TERM_KINDS and fact.end - fact.start <= TERM_MAX:
            term = Term(fact.kind, cased(fact.as_read))
            if specific(*term):
                found.setdefault(term, (fact.start, fact.end))
    return found


def terms_of(span: Span) -> list[Term]:
    """The terms of a passage that relate pages, each once, in the order they are read."""
    return list(placed(span))


def line_of(text: str, start: int, end: int) -> tuple[str, bool]:
    """The line of `text` that holds text[start:end], and whether it is cut: a term where
    `placed` found it in its passage (a fact is read within one line), or a page's title
    where the index matched it in a claim's text (the lines from the one it starts on to
    the one it ends on, when its words stand on two). At most LINE_MAX characters whole.
    Of more, LINE_MAX characters that hold the match, as near their middle as the line's
    ends let it be; and of a match itself longer than that, its first LINE_MAX. So what is
    given is LINE_MAX characters at most, whatever is asked. Nothing is searched for: the
    place is the caller's."""
    first = text.rfind("\n", 0, start) + 1
    last = text.find("\n", end)
    last = len(text) if last < 0 else last
    if last - first <= LINE_MAX:
        return text[first:last], False
    room = max(0, LINE_MAX - (end - start))
    left = min(max(first, start - room // 2), last - LINE_MAX)
    return text[left : left + LINE_MAX], True


def covered(span: Span, facts: Sequence[Fact] | None = None) -> list[tuple[int, int]]:
    """The stretches of a passage's text that are no prose, in the order they stand: every
    fact of TERM_KINDS, a term that relates pages or not (a plain word in backticks covers
    too), by the fact's own start and end; and every fenced code block from the start of its
    opening line, the word after the fence's marks included, to the end of its body (a
    code block's fact is its body, which starts on the line after the opening one). What
    the facts do not own is prose to them: a path written with no root
    (`deploy/compose.yaml` outside backticks is no fact), an email address, a host with no
    scheme, a Windows path; whether a block indented is code, the Markdown parser says
    (cairn.spans.code_read). `facts`: as `placed`."""
    text = span.text
    return [
        (opening(text, fact.start) if fact.kind == "code_block" else fact.start, fact.end)
        for fact in (extract_facts(span) if facts is None else facts)
        if fact.kind in TERM_KINDS or fact.kind == "code_block"
    ]


def opening(text: str, body: int) -> int:
    """Where the line before the one at `body` starts: a fenced block's opening line, its
    body starting on the next. A body at the text's start has no line before it."""
    return text.rfind("\n", 0, body - 1) + 1 if body > 0 else 0


def blanked(text: str, stretches: Iterable[tuple[int, int]], asked: str) -> str:
    """`text` with every character of `stretches` (`covered`, and what the Markdown parser
    reads as code: cairn.spans.code_read) made one character that `asked`, the phrase a
    title is asked as, does not hold: one of private use, which the index's tokenizer reads
    as part of a word, as it does a letter. Each character stays where it stood, so a place
    in the one text is a place in the other; and each stretch is one word or more that is
    no word of the title, so the words on its two sides are not side by side, and a word
    that stands in it only in part is another word. Where the index then finds the title,
    each of its words stands whole in prose."""
    fill = next(chr(code) for code in range(0xE000, 0xF900) if chr(code) not in asked)
    chars = list(text)
    for start, end in stretches:
        chars[start:end] = fill * (end - start)
    return "".join(chars)


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
