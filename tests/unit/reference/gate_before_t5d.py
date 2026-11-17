# Reference copy for the T5d differential test (tests/unit/test_verbatim_differential_unit.py).
# Below these three lines: src/cairn/gate.py byte for byte as of commit d22f741, before T5d
# made a claim one passage, whole. Nothing imports it but that test. Do not edit or fix it.
"""The lint gate: T3's guards between the compiler and the store.

Contract (frozen by tests/acceptance/test_w3_gate.py):
- `lint_gate(page, spans)` lints the candidate as one cairn-format page (claims as given,
  body = page.body) against exactly the spans its claims cite, via `lint.lint_pages` with no
  entities and no unassigned spans. It passes when the report has no violations.
- violations: one string per lint violation, "<code>: <claim_id or ->: <message>".
- metrics: {"claims": claim count, "spans_cited": distinct cited span count,
  "retention": plain retention overall, "code_line_retention": plain code-line retention,
  "span_coverage": cairn span coverage, "omission_rate": cairn omission rate}.

The strict gate (frozen by tests/acceptance/test_t4_coverage_guard.py):
- `strict_lint_gate(page, spans)` is lint_gate judged against every span in `spans`, cited or
  not. The compiler passes the spans the page already cites plus the note's new content
  spans, so a draft that leaves a new content span uncited fails coverage.passage_unaccounted,
  one violation per span. Heading spans need no claim. Violations and metrics have
  lint_gate's form, and for a page that cites every span they equal lint_gate's.
- `serve` gates the extractive draft with lint_gate. That draft cites every new span by
  construction, so the two gates agree on it.

The verbatim gate, for drafts a model makes (frozen by tests/acceptance/test_t5_grounding.py,
tests/acceptance/test_t5b_blocks.py and tests/acceptance/test_t5c_structure.py):
- `verbatim_gate(page, spans)` is strict_lint_gate plus one rule: a model may select and
  arrange, never write. compile_note uses it when no gate is named and the composer is not
  the extractive one; lint_gate and strict_lint_gate stay available to a caller who names
  them, and nothing in `serve` does so for a model.
- A code or table span is taken whole or not at all: a claim that cites one holds its text
  exactly, every line in order with its indentation, opening fence, info string and closing
  fence, as consecutive lines. Only whitespace at the end of a line may differ. No other line
  of the claim may equal, whitespace aside, a line of a block it cites, and the claim's code
  fences balance, so nothing of a code sample becomes page text and no fence is left open.
- A prose span (paragraph, bullet, quote) that holds structure is taken whole the same way,
  but exactly: every line as it is, trailing spaces included, with a blank line or the
  claim's edge on each side so it joins no other line. Structure is what `_structure`
  finds, once quote markers are set aside: a line indented four columns or more past the
  text of its list item (or past the margin), a code fence, a table row, a rule or heading
  underline, a hard line break (two spaces or a backslash at a line's end), a line that opens
  an HTML tag, a link-reference or footnote definition, or a list marker or number alone on
  its line. A claim line outside such a block may hold none of these, so a claim never adds
  a line break or a rule.
- Every other non-blank line of a claim is part of a run of consecutive prose lines that is
  made only of whole sentences of the prose spans (paragraph, bullet, quote) it cites. A run
  is read like a prose span: line breaks as spaces, so a hard-wrapped sentence may be wrapped
  anywhere, and blank lines inside it are ignored.
- Sentences come from `sentences(text)`, one function for spans and claims: a sentence ends
  at ".", "!" or "?" followed by a space or the end of the text; a span with no terminator is
  one sentence, nested items included. In doubt it does not end, so a unit is never cut
  short: not before a lowercase letter, nor after an ellipsis (".."), single-letter initials
  ("J.", "e.g.", "U.S."), an ABBREVIATIONS entry ("Dr.", "etc.") or a bare number ("1.",
  "1.1.", "2020."), so "Install Docker." is never a unit of "Setup: 1. Install Docker.".
  Nor inside inline code or a link's text or address, so backticks and brackets stay paired.
  The work is linear in the text, however many sentences repeat.
- A claim sentence that is not one whole unit may still be several whole units laid side by
  side, cut only where the claim kept a list marker or a list number at a line's start
  ("- Install Docker" on one line, "- Install uv" on the next; the number stays). An item
  is never cut from the child that completes it: "- Deleting is safe" alone is refused when
  the span goes on "  - only after a clean dry run".
- Whitespace is space, tab, CR and LF only, collapsed to one space. Nothing else is
  normalised: no case folding, no Unicode normalisation, and zero-width, no-break and
  bidirectional characters are text, so a homoglyph or an override never matches.
- Markers: one list marker ("- ", "* ", "+ ") or quote marker (">") at the start of a line
  may be kept or dropped, never added, moved or changed: a kept marker must stand where the
  span has the same one. Paragraph spans have no markers. An ordered-list number ("1. ",
  "2020. ") is text, never dropped. The cost of reading in doubt: "It shipped in 2020. Then
  it grew." is one unit, so "Then it grew." alone is refused.
- Every span a claim cites must give it at least one whole sentence or block, so a span
  cannot be cited only to look covered, and two notes that say the same sentence can share
  one claim that cites both. A quote marker alone on a line (a quote's blank line) gives
  nothing: a claim may keep one where a cited span has one, but a claim made of it is
  refused. A list marker or number alone on a line is structure, as above.
- A claim that breaks a rule is one violation, "grounding.not_verbatim: <claim id>: ...",
  naming the first failing line's number (or the span id cited for nothing), never text.
  Violations follow strict_lint_gate's; the metrics are strict_lint_gate's. The extractive
  draft passes by construction, and for it the result equals strict_lint_gate's, except
  where a span itself leaves a code fence open (a note that never closes one): that claim
  is refused.
- What it cannot do: it checks sentences, not meaning. A claim may leave out a neighbouring
  sentence of the span it cites, so a reader who needs the context opens the source. It
  reads lines and does not render the page, so it cannot see all a Markdown renderer would
  (verbatim_gate's docstring names what still passes).
"""

import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping

from cairn.compiler import CandidatePage, GateResult
from cairn.lint import ClaimInput, PageInput, lint_pages
from cairn.spans import Span
from cairn.store import Claim

PROSE_KINDS = frozenset({"paragraph", "bullet", "quote"})
LINE_KINDS = frozenset({"code", "table"})
# Whitespace is these four characters and nothing else: a no-break space, a zero-width
# character or a bidirectional control is text, compared like any other character.
_WHITESPACE = re.compile(r"[ \t\r\n]+")
_TOKEN = re.compile(r"[^ ]+")
# A list marker ("- ", "* ", "+ ", "1. ", "1) ") or a quote marker (">", one space optional)
# at the start of a line whose whitespace is already collapsed.
_MARKER = re.compile(r"(?:([-*+])(?: |$)|(>) ?)")
_ORDINAL = re.compile(r"\d+(?:\.\d+)*\.")  # "1.", "1.1.", "2020.": no sentence ends after one
_QUOTED = re.compile(r"^[ \t]*(?:> ?)+")  # a line's quote markers, set aside to find code
_NUMBERED = re.compile(r"\d{1,9}[.)] ")  # a line that starts with a list number: text, a cut
_FENCE = re.compile(r"[ \t]*(`{3,}|~{3,})(.*)")  # a code fence and its info string
_INITIALS = re.compile(r"(?:[^\W\d_]\.)+")  # "J.", "e.g.", "U.S."
_ITEM = re.compile(r"(?:[-*+]|\d{1,9}[.)])(?: |$)")  # a list item's marker, at its text's start
# A line's text (indentation and quote markers aside) that Markdown reads as structure.
_STRUCTURE = re.compile(
    r"`{3,}|~{3,}"  # a code fence
    r"|\||:?-+:?[ \t]*\|"  # a table row or delimiter row
    r"|(?:=+|-+|(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})[ \t]*$"  # a rule or underline
    r"|</?[A-Za-z]|<!|<\?"  # an HTML tag, comment or declaration
    r"|\[[^\]]+\]:"  # a link-reference or footnote definition
)
_OPENERS = "([{\"'*_`"
# Compared lowercased: a sentence never ends after one of these.
ABBREVIATIONS = frozenset(
    {
        "al.",
        "approx.",
        "ca.",
        "cf.",
        "dept.",
        "dr.",
        "eq.",
        "etc.",
        "fig.",
        "inc.",
        "jr.",
        "ltd.",
        "mr.",
        "mrs.",
        "ms.",
        "no.",
        "nos.",
        "prof.",
        "resp.",
        "sr.",
        "st.",
        "viz.",
        "vs.",
    }
)


def lint_gate(page: CandidatePage, spans: Mapping[str, Span]) -> GateResult:
    """Lint the candidate against the spans in `spans` that its claims cite; spans it does not
    cite are ignored, and a cited id missing from `spans` is provenance.unknown_passage.
    `spans_cited` counts every distinct cited id, as compiler.provenance_gate does."""
    cited = list(dict.fromkeys(span_id for claim in page.claims for span_id in claim.span_ids))
    claims = [ClaimInput(id=c.id, text=c.text, span_ids=list(c.span_ids)) for c in page.claims]
    report = lint_pages(
        [PageInput(slug=page.slug, text=page.body, claims=claims)],
        [spans[span_id] for span_id in cited if span_id in spans],
    )
    plain, cairn = report.metrics["plain"], report.metrics["cairn"]
    return GateResult(
        passed=not report.violations,
        violations=[f"{v.code}: {v.claim_id or '-'}: {v.message}" for v in report.violations],
        metrics={
            "claims": len(page.claims),
            "spans_cited": len(cited),
            "retention": plain["retention"]["overall"],
            "code_line_retention": plain["code_line_retention"],
            "span_coverage": cairn["passage_coverage"],
            "omission_rate": cairn["omission_rate"],
        },
    )


def strict_lint_gate(page: CandidatePage, spans: Mapping[str, Span]) -> GateResult:
    """lint_gate against every span in `spans`, cited or not, so an uncited content span
    fails coverage.passage_unaccounted. Gate a model's draft with this."""
    cited = list(dict.fromkeys(span_id for claim in page.claims for span_id in claim.span_ids))
    claims = [ClaimInput(id=c.id, text=c.text, span_ids=list(c.span_ids)) for c in page.claims]
    report = lint_pages(
        [PageInput(slug=page.slug, text=page.body, claims=claims)], list(spans.values())
    )
    plain, cairn = report.metrics["plain"], report.metrics["cairn"]
    return GateResult(
        passed=not report.violations,
        violations=[f"{v.code}: {v.claim_id or '-'}: {v.message}" for v in report.violations],
        metrics={
            "claims": len(page.claims),
            "spans_cited": len(cited),
            "retention": plain["retention"]["overall"],
            "code_line_retention": plain["code_line_retention"],
            "span_coverage": cairn["passage_coverage"],
            "omission_rate": cairn["omission_rate"],
        },
    )


def verbatim_gate(page: CandidatePage, spans: Mapping[str, Span]) -> GateResult:
    """strict_lint_gate, plus grounding.not_verbatim for each claim that writes rather than
    selects: one violation per claim, naming a line number or a span id, never text. The
    metrics are strict_lint_gate's. The default gate for every composer but the extractive
    one.

    What it guarantees, and what it does not: every claim is whole sentences of the prose
    spans it cites, or whole blocks of them taken exactly, in an order of its choosing; a span
    that holds structure (code, a table, a rule, a hard line break, indentation, HTML, a link
    definition) is taken whole; no sentence is cut inside inline code or a link; and every
    cited span gives the claim something. It checks sentences, not meaning. A claim may leave
    out a neighbouring sentence of the span it cites ("The operation is safe." from "The
    operation is safe. Only if approved."), so a reader who needs the context opens the
    source. And it reads lines; it does not render the page, so it cannot see everything a
    Markdown renderer would: a list item re-nested by indentation under four columns, or a
    sentence cut from emphasis that spans two ("**Bold." from "**Bold. Still bold.**"),
    still passes."""
    strict = strict_lint_gate(page, spans)
    refused = [v for claim in page.claims if (v := _not_verbatim(claim, spans))]
    return GateResult(
        passed=strict.passed and not refused,
        violations=[*strict.violations, *refused],
        metrics=strict.metrics,
    )


def sentences(text: str) -> list[tuple[int, int]]:
    """(start, end) of each sentence of `text`, whose words are separated by single spaces.

    A sentence ends at a word ending in ".", "!" or "?" that is followed by a space, or at the
    end of the text. In doubt, a sentence goes on, so a unit is never cut short: no end before
    a word that starts with a lowercase letter, nor after a word ending in ".." (an ellipsis),
    a run of single-letter initials ("J.", "e.g.", "U.S.") or a common abbreviation ("Dr.",
    "etc."), nor after a bare number ("1.", "1.1.", "2020."), so a list item's number stays
    with its item wherever it stands. Nor does a sentence end inside inline code or inside a
    link's text or address (`_paired`), so backticks and brackets stay paired. Linear in the
    text's length."""
    found: list[tuple[int, int]] = []
    start = end = -1
    previous = ""
    inside = _paired(text)
    for word in _TOKEN.finditer(text):
        if start < 0:
            start = word.start()
        elif (
            not inside[end]
            and not _ORDINAL.fullmatch(previous)
            and _ends_sentence(previous, word.group())
        ):
            found.append((start, end))
            start = word.start()
        end, previous = word.end(), word.group()
    if start >= 0:
        found.append((start, end))
    return found


def _paired(text: str) -> list[bool]:
    """For each offset of `text` (and its end), whether it lies inside inline code, inside a
    link's text ("[...]", whether or not a link follows) or inside the address or label that
    follows one ("(...)", "[...]"). Inline code opens at a run of backticks and closes at the
    next run of the same length; brackets and parentheses pair by nesting, outside code. An
    opener that never closes is text. A backslash escapes a mark outside code but not inside
    it, so the text is read twice, once with escapes and once without, and what either
    reading pairs is inside. In doubt more is inside, which only joins sentences."""
    depth = [0] * (len(text) + 2)  # +1 where a paired range opens, -1 just after it closes

    def mark(first: int, last: int) -> None:  # offsets first..last-1 are inside
        if first < last:
            depth[first] += 1
            depth[last] -= 1

    def escaped(at: int) -> bool:
        return at > 0 and text[at - 1] == "\\"

    for escapes in (False, True):
        runs = []
        for m in re.finditer(r"`+", text):
            start = m.start() + (1 if escapes and escaped(m.start()) else 0)
            if start < m.end():
                runs.append((start, m.end()))
        after: list[int | None] = [None] * len(runs)  # the next run of the same length
        nearest: dict[int, int] = {}
        for i in range(len(runs) - 1, -1, -1):
            length = runs[i][1] - runs[i][0]
            after[i] = nearest.get(length)
            nearest[length] = i
        code = [False] * (len(text) + 1)
        i = 0
        while i < len(runs):
            if (j := after[i]) is None:
                i += 1
                continue
            mark(runs[i][1], runs[j][0])
            for k in range(runs[i][0], runs[j][1]):
                code[k] = True
            i = j + 1
        # Open brackets and parentheses, each with whether its contents are kept together:
        # always for "[", and for "(" only right after a "]", where it holds a link's address.
        opened: dict[str, list[tuple[int, bool]]] = {"[": [], "(": []}
        closes = {"]": "[", ")": "("}
        closed_bracket = -2  # where the last "[...]" closed
        for at, char in enumerate(text):
            if code[at] or (escapes and escaped(at)):
                continue
            if char in opened:
                opened[char].append((at, char == "[" or at == closed_bracket + 1))
            elif char in closes and (stack := opened[closes[char]]):
                first, kept = stack.pop()
                if kept:
                    mark(first + 1, at)
                if char == "]":
                    closed_bracket = at
    inside, level = [], 0
    for at in range(len(text) + 1):
        level += depth[at]
        inside.append(level > 0)
    return inside


def _ends_sentence(word: str, following: str) -> bool:
    if word[-1] not in ".!?" or following[0].islower():
        return False
    if word[-1] != ".":
        return True
    core = word.lstrip(_OPENERS)
    return not (word.endswith("..") or _INITIALS.fullmatch(core) or core.lower() in ABBREVIATIONS)


class _Prose:
    """Lines read as one text: whitespace collapsed, line breaks as spaces, and (where markers
    are read) one list or quote marker taken off the start of each line and kept aside."""

    def __init__(self, lines: Iterable[tuple[int, str]], markers: bool) -> None:
        parts: list[str] = []
        self.bare_markers: list[tuple[int, str]] = []  # (line number, marker) of empty lines
        self._offsets: list[int] = []  # text offset of each line marker, in order
        self._keys: list[str] = []  # the marker at each of those offsets
        self._numbered: list[int] = []  # text offset of each line that starts with a number
        self._starts: list[int] = []  # text offset of each line's start
        self._numbers: list[int] = []  # and that line's number
        offset = 0
        for number, raw in lines:
            line = _WHITESPACE.sub(" ", raw).strip(" ")
            if not line:
                continue
            marker = _MARKER.match(line) if markers else None
            key = (marker[1] or marker[2]) if marker else None
            content = line[marker.end() :] if marker else line
            if not content:
                if key:
                    self.bare_markers.append((number, key))
                continue
            offset += 1 if parts else 0  # the space that joins it to the line before
            self._starts.append(offset)
            self._numbers.append(number)
            if key:
                self._offsets.append(offset)
                self._keys.append(key)
            if _NUMBERED.match(content):
                self._numbered.append(offset)
            parts.append(content)
            offset += len(content)
        self.text = " ".join(parts)

    def marks(self, start: int, end: int) -> dict[int, str]:
        """The markers of the lines that start within [start, end), by offset from start."""
        first, last = bisect_left(self._offsets, start), bisect_left(self._offsets, end)
        return {self._offsets[i] - start: self._keys[i] for i in range(first, last)}

    def item_starts(self, start: int, end: int) -> list[int]:
        """Offsets in (start, end) where a line starts with a list marker or a list number."""
        first, last = bisect_right(self._offsets, start), bisect_left(self._offsets, end)
        marked = [self._offsets[i] for i in range(first, last) if self._keys[i] != ">"]
        first, last = bisect_right(self._numbered, start), bisect_left(self._numbered, end)
        return sorted({*marked, *self._numbered[first:last]})

    def line_of(self, offset: int) -> int:
        return self._numbers[bisect_right(self._starts, offset) - 1]


def _not_verbatim(claim: Claim, spans: Mapping[str, Span]) -> str | None:
    """The claim's grounding.not_verbatim violation, or None when it only selects."""
    cited = [spans[i] for i in dict.fromkeys(claim.span_ids) if i in spans]
    # A block's lines, and whether they are compared exactly (prose) or with whitespace at
    # the end of a line aside (code, tables) -> the spans that are it.
    blocks: dict[tuple[bool, tuple[str, ...]], set[str]] = {}
    block_lines: set[str] = set()  # every line of a cited block, whitespace collapsed
    units: dict[str, list[tuple[str, dict[int, str]]]] = {}  # sentence -> (span, its markers)
    bare: set[str] = set()  # the markers that stand alone on a line of a cited span
    seen: set[tuple[str, str, tuple[tuple[int, str], ...]]] = set()  # units already recorded
    for span in cited:
        lines = span.text.split("\n")
        exact = span.kind in PROSE_KINDS and _structure(lines) is not None
        if span.kind in LINE_KINDS or exact:
            rows = tuple(line.rstrip("\r" if exact else " \t\r") for line in lines)
            blocks.setdefault((exact, rows), set()).add(span.id)
            block_lines.update(c for row in rows if (c := _WHITESPACE.sub(" ", row).strip(" ")))
        elif span.kind in PROSE_KINDS:
            prose = _Prose(enumerate(lines, 1), markers=span.kind != "paragraph")
            for start, end in sentences(prose.text):
                marks = prose.marks(start, end)
                text = prose.text[start:end]
                if (key := (text, span.id, tuple(sorted(marks.items())))) not in seen:
                    seen.add(key)  # one unit for a sentence however often a span repeats it
                    units.setdefault(text, []).append((span.id, marks))
            for _, key in prose.bare_markers:
                bare.add(key)
    lengths = {len(text) for text in units}
    longest = max(lengths, default=0)

    given: set[str] = set()  # spans that gave the claim a sentence or a line
    run: list[tuple[int, str]] = []  # consecutive prose lines of the claim

    def suppliers(prose: _Prose, start: int, end: int) -> list[str]:
        """The cited spans that have prose.text[start:end] as a whole sentence, with every
        marker the claim kept there standing where the span has the same one."""
        if end - start not in lengths:
            return []
        marks = prose.marks(start, end)
        return [
            span_id
            for span_id, found in units.get(prose.text[start:end], ())
            if all(found.get(o) == k for o, k in marks.items())
        ]

    def matched(prose: _Prose, start: int, end: int) -> bool:
        """Whether the claim's sentence [start, end) is one whole sentence of a cited span or,
        where it kept list markers, a sequence of them that each start at such a marker."""
        if whole := suppliers(prose, start, end):
            given.update(whole)
            return True
        cuts = [start, *prose.item_starts(start, end)]
        ends = [cut - 1 for cut in cuts[1:]] + [end]  # each piece stops before the space
        # back[j]: how the first j pieces were matched, as (where the last unit began, its
        # suppliers). A unit is never longer than the longest one, which bounds the look back.
        back: list[tuple[int, list[str]] | None] = [(0, [])] + [None] * len(cuts)
        for j in range(1, len(cuts) + 1):
            for i in range(j - 1, -1, -1):
                if ends[j - 1] - cuts[i] > longest:
                    break
                if back[i] and (found := suppliers(prose, cuts[i], ends[j - 1])):
                    back[j] = (i, found)
                    break
        j = len(cuts)
        if back[j] is None:
            return False
        while j:
            j, found = back[j]
            given.update(found)
        return True

    def failed_line() -> int | None:
        if (at := _structure(raw for _, raw in run)) is not None:
            return run[at - 1][0]  # structure outside a block the claim takes whole
        prose = _Prose(run, markers=True)
        run.clear()
        for number, key in prose.bare_markers:
            if key not in bare:  # kept where a span has one, but it gives the claim nothing
                return number
        for start, end in sentences(prose.text):
            if not matched(prose, start, end):
                return prose.line_of(start)
        return None

    claim_lines = claim.text.split("\n")
    if bad := _open_fence(claim_lines):
        return _refusal(claim, f"line {bad} opens a code fence the claim never closes")
    as_is = {
        True: [line.rstrip("\r") for line in claim_lines],
        False: [line.rstrip(" \t\r") for line in claim_lines],
    }
    shapes = sorted(blocks, key=lambda b: len(b[1]), reverse=True)  # the longest block first
    number = 0
    while number < len(claim_lines):
        at = number
        block = next(
            (
                (exact, rows)
                for exact, rows in shapes
                if as_is[exact][at] == rows[0] and tuple(as_is[exact][at : at + len(rows)]) == rows
            ),
            None,
        )
        if block:
            if run and (bad := failed_line()):
                return _refusal(claim, f"line {bad} is not whole sentences of a passage it cites")
            exact, rows = block
            close = (at - 1, at + len(rows))  # the lines just before and after it
            if exact and any(
                0 <= n < len(claim_lines) and claim_lines[n].strip(" \t\r") for n in close
            ):
                # Prose that holds structure joins a line it touches: indented code becomes
                # running text, a hard break or an underline takes the line beside it.
                return _refusal(claim, f"line {at + 1} takes a block with no blank line around it")
            given.update(blocks[block])
            number += len(rows)
            continue
        number += 1
        raw = claim_lines[at]
        line = _WHITESPACE.sub(" ", raw).strip(" ")
        if not line:
            if run:
                run.append((number, raw))  # a blank line: the next may start a list
            continue
        if line in block_lines:
            return _refusal(claim, f"line {number} is part of a block it does not take whole")
        run.append((number, raw))
    if run and (bad := failed_line()):
        return _refusal(claim, f"line {bad} is not whole sentences of a passage it cites")
    for span in cited:
        if span.id not in given:
            return _refusal(claim, f"it cites {span.id} but takes no whole sentence or block of it")
    return None


def _structure(lines: Iterable[str]) -> int | None:
    """The number of the first line that Markdown reads as structure rather than as running
    text, or None. One test for spans and claims: a span with such a line is taken whole, and
    a claim line with one must be part of a block it takes whole.

    Structure is, once a line's quote markers are set aside: a line indented four columns or
    more past the text of the list item it sits in (or past the margin), a code fence, a table
    row or delimiter row, a rule or heading underline, a hard line break (two spaces or a
    backslash at the end), a line that opens an HTML tag, a link-reference or footnote
    definition, or a list marker or number with nothing after it on its line. In doubt a line
    is structure. A quote marker alone is a quote's blank line, not structure."""
    items: list[int] = []  # the text column of each list item the current line may sit in
    for number, raw in enumerate(lines, 1):
        line = raw.rstrip("\r")
        if line.endswith(("  ", "\\")):
            return number
        expanded = line.expandtabs(4)
        outer = len(expanded) - len(expanded.lstrip(" "))  # before any quote marker
        quoted = _QUOTED.match(expanded)
        margin = quoted.end() if quoted else 0  # where a quote's text may start
        text = expanded[margin:].lstrip(" ")
        if not text:
            continue
        if outer - (items[-1] if items else 0) >= 4:
            return number
        column = len(expanded) - len(text)
        while items and column < items[-1]:
            items.pop()
        if column - max(items[-1] if items else 0, margin) >= 4 or _STRUCTURE.match(text):
            return number
        if item := _ITEM.match(text):
            rest = text[item.end() :]
            if not rest.strip(" ") or rest.startswith("    ") or _STRUCTURE.match(rest):
                return number  # an item with no text on its line, or with code or a rule
            items.append(column + item.end())
    return None


def _open_fence(lines: Iterable[str]) -> int | None:
    """The number of the line whose code fence the text leaves open, or None. A fence is three
    or more backticks or tildes at a line's start, and only a run of the same character at
    least as long, with nothing after it, closes it (CommonMark)."""
    opened: tuple[int, str] | None = None  # (line number, fence)
    for number, line in enumerate(lines, 1):
        fence = _FENCE.fullmatch(line.rstrip(" \t\r"))
        if not fence:
            continue
        run, rest = fence[1], fence[2]
        if opened is None:
            if not (run[0] == "`" and "`" in rest):  # "```a```" is inline code, not a fence
                opened = (number, run)
        elif run[0] == opened[1][0] and len(run) >= len(opened[1]) and not rest.strip(" \t"):
            opened = None
    return opened[0] if opened else None


def _refusal(claim: Claim, message: str) -> str:
    return f"grounding.not_verbatim: {claim.id}: {message}"
