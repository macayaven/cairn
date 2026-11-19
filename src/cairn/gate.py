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

The verbatim gate, for drafts a model makes (frozen by
tests/acceptance/test_t5d_a_claim_is_a_passage_whole.py, which narrows the rules of
test_t5_grounding.py, test_t5b_blocks.py and test_t5c_structure.py):
- `verbatim_gate(page, spans)` is strict_lint_gate plus one rule: a claim is one passage,
  whole. Its text is the text of every passage it cites: nothing of a passage left out,
  nothing added, and two passages never joined into one claim. A claim that cites several
  passages is a fold: each of them says the same, in the same words. compile_note uses it
  when no gate is named and the composer is not the extractive one; lint_gate and
  strict_lint_gate stay available to a caller who names them, and nothing in `serve` does so
  for a model.
- A code or table passage is compared line for line: every line in order, with its
  indentation and both fence lines. Only white space at the end of a line may differ.
- A prose passage (paragraph, bullet, quote) that holds structure is compared line for line
  too, but exactly, white space at the end of a line included. Structure is what `_structure`
  finds, once quote markers are set aside: a line indented four columns or more past the
  text of its list item (or past the margin), a code fence, a table row, a rule or heading
  underline, a hard line break (two spaces or a backslash at a line's end), a line that opens
  an HTML tag, a link-reference or footnote definition, or a list marker or number alone on
  its line. For both kinds of block, blank lines before and after the claim's text are not
  read.
- Any other prose passage is compared as text: the same characters, with each run of white
  space read as one space and the white space at the two ends dropped, so a hard-wrapped
  passage may be wrapped elsewhere. White space is space, tab, CR and LF only. Nothing else is
  normalised: no case folding, no Unicode normalisation, and zero-width, no-break and
  bidirectional characters are text, so a homoglyph or an override never matches. A list or
  quote marker ("- ", "* ", "+ ", ">") is text too, never dropped or added, and it keeps its
  place: a line of the claim starts with one where a line of the passage does and nowhere
  else, and a line that is markers alone (a quote's blank line) stays a line of its own. A
  list number ("1. ", "2020. ") is text like any other word. The claim holds no structure the
  passage does not: no hard line break, rule or indented code (`_structure`, read from the
  claim's first non-blank line).
- A claim that breaks the rule is one violation, "grounding.not_verbatim: <claim id>: ...". It
  says in plain words that a claim must be the whole of each passage it cites, names the first
  passage the claim is not (and the line, where the claim adds structure), never text, and
  says what to do. A cited id the gate was not given is passed over here (the strict gate
  reports it as provenance.unknown_passage) and never repeated; a claim left with no passage
  is refused. A claim that is its passages whole and leaves a code fence open (it copies
  a passage of a note that never closes one) passes, as lint_gate passes it when the note is
  saved: the body closes the fence where the claim ends (compiler.render_body), so a page
  that holds one is tidied and arranged again (review 7.2). Violations follow
  strict_lint_gate's; the metrics are strict_lint_gate's. The extractive draft passes by
  construction, and for it the result equals strict_lint_gate's.
- Cost: a passage is read once per gate call however many claims cite it, and a claim once
  however many passages it cites, so the work is linear in their text.
- What it cannot do: it keeps a passage whole; it does not keep two passages together. A
  condition the author put in the next passage is a claim of its own: the strict gate's
  coverage rule puts it on the page, and the arrangement decides where. It reads lines and
  does not render the page, so it cannot see all a Markdown renderer would: a list item
  re-nested by indentation under four columns still passes.
"""

import re
from collections.abc import Iterable, Mapping

from cairn.compiler import CandidatePage, GateResult
from cairn.lint import ClaimInput, PageInput, lint_pages
from cairn.spans import Span, chunk
from cairn.store import Claim

PROSE_KINDS = frozenset({"paragraph", "bullet", "quote"})
LINE_KINDS = frozenset({"code", "table"})
# Whitespace is these four characters and nothing else: a no-break space, a zero-width
# character or a bidirectional control is text, compared like any other character.
_WHITESPACE = re.compile(r"[ \t\r\n]+")
# On a line whose white space is collapsed: one that opens with a list marker ("- ", "* ",
# "+ ", or one alone) or a quote marker (">"), and one that is nothing but such markers.
_LEADS = re.compile(r"[-*+](?: |$)|>")
_MARKERS_ALONE = re.compile(r"(?:[-*+](?: |$)|> ?)+")
_QUOTED = re.compile(r"^[ \t]*(?:> ?)+")  # a line's quote markers, set aside to find code
_ITEM = re.compile(r"(?:[-*+]|\d{1,9}[.)])(?: |$)")  # a list item's marker, at its text's start
# A line's text (indentation and quote markers aside) that Markdown reads as structure.
_STRUCTURE = re.compile(
    r"`{3,}|~{3,}"  # a code fence
    r"|\||:?-+:?[ \t]*\|"  # a table row or delimiter row
    r"|(?:=+|-+|(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})[ \t]*$"  # a rule or underline
    r"|</?[A-Za-z]|<!|<\?"  # an HTML tag, comment or declaration
    r"|\[[^\]]+\]:"  # a link-reference or footnote definition
)
# How a passage is compared, and what it is under that comparison.
_LINES, _EXACT, _TEXT = "lines", "exact", "text"
_Reading = tuple[str, str | tuple[str, ...]] | None
_WHOLE = "a claim must be the whole of each passage it cites, copied exactly"


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
    """strict_lint_gate, plus grounding.not_verbatim for each claim that is not one passage,
    whole: one violation per claim, naming a passage id or a line number, never text. The
    metrics are strict_lint_gate's. The default gate for every composer but the extractive
    one.

    The rule: a claim is one passage, whole. Its text is the text of every passage it cites,
    compared line for line for code, tables and prose that holds structure, and otherwise as
    text whose runs of white space read as one space, with every list or quote marker kept at
    the start of its line. Nothing is left out, nothing is added, and no two passages are
    joined; a claim that cites several passages is a fold of passages that say the same.

    What it guarantees, and what it does not: it keeps a passage whole; it does not keep two
    passages together. "The operation is safe." cannot be cut from "The operation is safe.
    Only if approved." But when the author put "Only if approved." in a passage of its own,
    that is a claim of its own: the strict gate's coverage rule puts it on the page, and the
    arrangement decides where. It reads lines and does not render the page, so a list item
    re-nested by indentation under four columns still passes."""
    strict = strict_lint_gate(page, spans)
    refused = _refusals(page.claims, spans)
    return GateResult(
        passed=strict.passed and not refused,
        violations=[*strict.violations, *refused],
        metrics=strict.metrics,
    )


def _refusals(claims: Iterable[Claim], spans: Mapping[str, Span]) -> list[str]:
    """The grounding.not_verbatim violations of `claims`, in order, reading each passage once."""
    readings: dict[str, _Reading] = {}
    return [v for claim in claims if (v := _not_verbatim(claim, spans, readings))]


def _not_verbatim(
    claim: Claim, spans: Mapping[str, Span], readings: dict[str, _Reading] | None = None
) -> str | None:
    """The claim's grounding.not_verbatim violation, or None when it is every passage it cites,
    whole. `readings` keeps each passage's reading between calls of one gate."""
    readings = {} if readings is None else readings
    lines = claim.text.split("\n")
    read: dict[str, str | tuple[str, ...]] = {}  # the claim, read each way a passage asks

    def claim_as(how: str) -> str | tuple[str, ...]:
        if how not in read:
            read[how] = _text(lines) if how == _TEXT else _rows(lines, how == _EXACT)
        return read[how]

    # An id the gate was not given is the strict gate's provenance.unknown_passage; here it is
    # passed over, and never repeated, since a model wrote it.
    known = [span_id for span_id in dict.fromkeys(claim.span_ids) if span_id in spans]
    if not known:
        return _refusal(
            claim, f"{_WHOLE}; it cites no passage that was given: cite the id of the one it copies"
        )
    structure: list[int | None] = []  # the claim's first line of structure, once found
    for span_id in known:
        span = spans[span_id]
        if span_id not in readings:
            readings[span_id] = _reading(span)
        if (reading := readings[span_id]) is None:
            return _refusal(
                claim,
                f"{_WHOLE}; {span_id} is a {span.kind}, which no claim copies: cite only "
                "the passage it copies",
            )
        how, text = reading
        if claim_as(how) != text:
            return _refusal(
                claim,
                f"{_WHOLE}; it is not the whole of {span_id}: copy all of {span_id}, leave "
                "nothing out, add nothing, and join no other passage to it",
            )
        if how == _TEXT and not structure:
            structure.append(_added_structure(lines))
        if how == _TEXT and (at := structure[0]) is not None:
            return _refusal(
                claim,
                f"{_WHOLE}; line {at} adds a line break, a rule or indentation that "
                f"{span_id} does not have: keep the lines of {span_id} as they are",
            )
    return None


def text_reading(text: str) -> _Reading:
    """What verbatim_gate reads in a passage of this text, read as a note's text is: its kind
    is the kind of its first block (cairn.spans.chunk). Two claims that copy passages whole
    say the same, by the gate's own equality, exactly when their readings are equal
    (arrange.repeat_groups, review 7.3); None for text that holds no passage a claim copies."""
    blocks = chunk("", text)
    if not blocks:
        return None
    return _reading(blocks[0].model_copy(update={"text": text}))


def _reading(span: Span) -> _Reading:
    """How a passage is compared and what it is so read, or None for a kind no claim copies:
    code and tables line by line with white space at the end of each line aside, prose that
    holds structure line by line exactly, and other prose as text."""
    lines = span.text.split("\n")
    if span.kind in LINE_KINDS:
        return _LINES, _rows(lines, exact=False)
    if span.kind not in PROSE_KINDS:
        return None
    if _structure(lines) is not None:
        return _EXACT, _rows(lines, exact=True)
    return _TEXT, _text(lines)


def _rows(lines: list[str], exact: bool) -> tuple[str, ...]:
    """Lines compared one by one: CR (and, unless exact, white space) at each end of line
    aside, and the blank lines before the first and after the last line of text dropped."""
    rows = [line.rstrip("\r" if exact else " \t\r") for line in lines]
    first = next((n for n, row in enumerate(rows) if row.strip(" \t\r")), len(rows))
    last = next((n for n in range(len(rows), first, -1) if rows[n - 1].strip(" \t\r")), first)
    return tuple(rows[first:last])


def _text(lines: Iterable[str]) -> str:
    """Prose read as text: each run of white space one space and the ends dropped, except that
    a line break stays before a line that opens with a list or quote marker and after a line
    that is markers alone, so every marker stays at the start of its line."""
    out: list[str] = []
    alone = False
    for raw in lines:
        line = _WHITESPACE.sub(" ", raw).strip(" ")
        if not line:
            continue
        if out:
            out.append("\n" if alone or _LEADS.match(line) else " ")
        out.append(line)
        alone = _MARKERS_ALONE.fullmatch(line) is not None
    return "".join(out)


def _added_structure(lines: list[str]) -> int | None:
    """The number of the first line from the claim's first line of text on that Markdown reads
    as structure, or None: a claim read as text may hold none."""
    first = next((n for n, line in enumerate(lines) if line.strip(" \t\r")), len(lines))
    at = _structure(lines[first:])
    return None if at is None else first + at


def _structure(lines: Iterable[str]) -> int | None:
    """The number of the first line that Markdown reads as structure rather than as running
    text, or None. One test for passages and claims: a passage with such a line is compared
    line for line, and a claim compared as text may have none.

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


def _refusal(claim: Claim, message: str) -> str:
    return f"grounding.not_verbatim: {claim.id}: {message}"
