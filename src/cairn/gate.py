"""The lint gate: T3's guards between the compiler and the store.

Contract (frozen by tests/acceptance/test_w3_gate.py):
- `lint_gate(page, spans)` lints the candidate as one cairn-format page (claims as given,
  body = page.body) against exactly the spans its claims cite, via `lint.lint_pages` with no
  entities and no unassigned spans, as a rendered page: its facts are read from its claims,
  each alone, as extracted (CORE5; the body is its claims, by the reverse check below). It
  passes when the report has no violations.
- violations: one string per lint violation, "<code>: <claim_id or ->: <message>", and
  provenance.unclaimed_line when the body is not what compiler.render_body writes of the
  page's claims and the passages given (`unclaimed_line`, review blocker 1 and the second
  report's finding 1: the reverse of the claim-in-body check, by equality with a re-render;
  no statement, as the gate reads nothing from the store); and compile.fence_left_open for
  each claim that leaves a code fence open that no line the body could write after it
  closes, alone or where the body puts it after the claims before it (`fence_left_open`;
  none is known), naming the claim, never its text. Every gate asks both
  (`unclaimed_violations`), and the import refuses a claim that alone is such a claim.
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
- Every passage is compared the same way (CORE5): a claim is the whole of a passage when
  their texts are the same, character for character, but for what cairn.spans.compared_text
  leaves out: the spaces, tabs and carriage returns at the end of a line, and the blank lines
  before the first and after the last line. Indentation, runs of white space inside a line,
  tabs, line breaks, no-break, zero-width and every other space are text, so `a  b.txt` and
  `a b.txt` are two texts, a list nested otherwise is another list, and prose wrapped
  otherwise is another passage. Nothing is read as Markdown: no inline code, link, list or
  quote. No case folding, no Unicode normalisation; a homoglyph or an override never matches.
  Code, tables and prose (paragraph, bullet, quote) are copied; a passage of any other kind
  is cited by no claim.
- A claim that breaks the rule is one violation, "grounding.not_verbatim: <claim id>: ...". It
  says in plain words that a claim must be the whole of each passage it cites, names the first
  passage the claim is not, never text, and says what to do. A cited id the gate was not
  given is passed over here (the strict gate reports it as provenance.unknown_passage) and
  never repeated; a claim left with no passage
  is refused. A fold of passages under different headings is refused too, as an import
  refuses it (the second report's finding 5a); the message names no heading. A claim that
  is its passages whole and leaves a code fence open (it copies a passage of a note that
  never closes one) passes, as lint_gate passes it when the note is saved: the body closes
  the fence where the claim ends (compiler.render_body), so a page that holds one is tidied
  and arranged again (review 7.2). When every claim is whole, claims that together copy their
  passages more than the import takes of one version (store.portable: CLAIM_TEXT_FACTOR,
  CITATION_FACTOR; a passage copied again with a space at a line's end, say) are one
  violation, "grounding.passage_repeated: -: ...", counts only. Violations follow
  strict_lint_gate's; the metrics are strict_lint_gate's. The extractive draft passes by
  construction, and for it the result equals strict_lint_gate's.
- Cost: a passage is read once per gate call however many claims cite it, and a claim once
  however many passages it cites, so the work is linear in their text.
- What it cannot do: it keeps a passage whole; it does not keep two passages together. A
  condition the author put in the next passage is a claim of its own: the strict gate's
  coverage rule puts it on the page, and the arrangement decides where. Spaces at a line's
  end are not compared, so a claim may add or drop the two that make a Markdown hard line
  break.
"""

from collections.abc import Iterable, Mapping

from cairn.candidate import CandidatePage, GateResult
from cairn.lint import ClaimInput, PageInput, lint_pages
from cairn.spans import CLOSERS_MAX, FenceLeftOpen, Span, compared_text, open_fence_closer
from cairn.store import Claim

PROSE_KINDS = frozenset({"paragraph", "bullet", "quote"})
LINE_KINDS = frozenset({"code", "table"})
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
        rendered=True,  # unclaimed_violations checks the body is its claims rendered
    )
    plain, cairn = report.metrics["plain"], report.metrics["cairn"]
    unclaimed = unclaimed_violations(page, spans)
    return GateResult(
        passed=not report.violations and not unclaimed,
        violations=[
            *(f"{v.code}: {v.claim_id or '-'}: {v.message}" for v in report.violations),
            *unclaimed,
        ],
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
        [PageInput(slug=page.slug, text=page.body, claims=claims)],
        list(spans.values()),
        rendered=True,  # unclaimed_violations checks the body is its claims rendered
    )
    plain, cairn = report.metrics["plain"], report.metrics["cairn"]
    unclaimed = unclaimed_violations(page, spans)
    return GateResult(
        passed=not report.violations and not unclaimed,
        violations=[
            *(f"{v.code}: {v.claim_id or '-'}: {v.message}" for v in report.violations),
            *unclaimed,
        ],
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
    whole: one violation per claim, naming a passage id or a line number, never text; when
    every claim is whole, grounding.passage_repeated once if together they copy their
    passages more than the import takes of one version (counts only). The metrics are
    strict_lint_gate's. The default gate for every composer but the extractive
    one.

    The rule: a claim is one passage, whole. Its text is the text of every passage it cites,
    the same character for character but for the white space at the end of a line and the
    blank lines around it (cairn.spans.compared_text). Nothing is left out, nothing is added,
    and no two passages are joined; a claim that cites several passages is a fold of passages
    whose texts are the same, under one heading path, as the import asks.

    What it guarantees, and what it does not: it keeps a passage whole; it does not keep two
    passages together. "The operation is safe." cannot be cut from "The operation is safe.
    Only if approved." But when the author put "Only if approved." in a passage of its own,
    that is a claim of its own: the strict gate's coverage rule puts it on the page, and the
    arrangement decides where."""
    strict = strict_lint_gate(page, spans)
    refused = _refusals(page.claims, spans) or _past_the_import(page.claims, spans)
    return GateResult(
        passed=strict.passed and not refused,
        violations=[*strict.violations, *refused],
        metrics=strict.metrics,
    )


def whole(claim: Claim, spans: Mapping[str, Span]) -> bool:
    """Whether the claim is the whole of every passage it cites that `spans` holds, by
    verbatim_gate's own rule (and cites at least one): what a claim must still be to survive
    a forgotten note (store.erasure.kept_claims, review 7.5)."""
    return _not_verbatim(claim, spans) is None


def _refusals(claims: Iterable[Claim], spans: Mapping[str, Span]) -> list[str]:
    """The grounding.not_verbatim violations of `claims`, in order, reading each passage once:
    a claim that is not every passage it cites, whole, or a fold of passages under different
    headings (the import's rule, store.portable._page_version: the body shows a claim under
    one section, so what is saved can always be exported and imported; second report,
    finding 5a). Never the headings: the note's text."""
    readings: dict[str, str | None] = {}
    return [
        v
        for claim in claims
        if (v := _not_verbatim(claim, spans, readings) or _folds_sections(claim, spans))
    ]


def _past_the_import(claims: Iterable[Claim], spans: Mapping[str, Span]) -> list[str]:
    """grounding.passage_repeated when the claims, each a passage whole, hold more than the
    import takes of one version (store.portable: CLAIM_TEXT_FACTOR times the text of the
    passages they cite, CITATION_FACTOR times the claims and passages in citations), as a
    composer that copies a passage more than once, with or without spaces at a line's end,
    may answer. What the gate saves, an export carries and its import takes. Counts only."""
    from cairn.store.portable import CITATION_FACTOR, CLAIM_TEXT_FACTOR

    claims = list(claims)
    cited = {span_id for claim in claims for span_id in claim.span_ids if span_id in spans}
    held = sum(len(claim.text.encode()) for claim in claims)
    citations = sum(len(claim.span_ids) for claim in claims)
    if held <= CLAIM_TEXT_FACTOR * sum(len(spans[i].text.encode()) for i in cited) and (
        citations <= CITATION_FACTOR * (len(claims) + len(cited))
    ):
        return []
    return [
        "grounding.passage_repeated: -: the claims copy their passages more often than a page"
        " holds them: give each passage one claim"
    ]


def _folds_sections(claim: Claim, spans: Mapping[str, Span]) -> str | None:
    if len({spans[i].heading_path for i in claim.span_ids if i in spans}) < 2:
        return None
    return _refusal(
        claim, "it folds passages under different headings: cite the passages of one heading"
    )


def _not_verbatim(
    claim: Claim, spans: Mapping[str, Span], readings: dict[str, str | None] | None = None
) -> str | None:
    """The claim's grounding.not_verbatim violation, or None when it is every passage it cites,
    whole. `readings` keeps each passage's compared text between calls of one gate."""
    readings = {} if readings is None else readings
    # An id the gate was not given is the strict gate's provenance.unknown_passage; here it is
    # passed over, and never repeated, since a model wrote it.
    known = [span_id for span_id in dict.fromkeys(claim.span_ids) if span_id in spans]
    if not known:
        return _refusal(
            claim, f"{_WHOLE}; it cites no passage that was given: cite the id of the one it copies"
        )
    text = compared_text(claim.text)
    for span_id in known:
        span = spans[span_id]
        if span_id not in readings:
            copied = span.kind in PROSE_KINDS or span.kind in LINE_KINDS
            readings[span_id] = compared_text(span.text) if copied else None
        if (reading := readings[span_id]) is None:
            return _refusal(
                claim,
                f"{_WHOLE}; {span_id} is a {span.kind}, which no claim copies: cite only "
                "the passage it copies",
            )
        if text != reading:
            return _refusal(
                claim,
                f"{_WHOLE}; it is not the whole of {span_id}: copy all of {span_id}, leave "
                "nothing out, add nothing, and join no other passage to it",
            )
    return None


def _refusal(claim: Claim, message: str) -> str:
    return f"grounding.not_verbatim: {claim.id}: {message}"


# The reverse of provenance.claim_not_in_body (review blocker 1; second report, finding 1):
# every body a gate sees is written by compiler.render_body from the page's claims, so the
# check is equality with a re-render, never a reading of the body's lines.

BREADCRUMB = " > "  # between the headings past the sixth level, in its one heading


FENCE_LEFT_OPEN = "compile.fence_left_open"  # a claim leaves a fence no closer closes


def unclaimed_violations(page: CandidatePage, spans: Mapping[str, Span]) -> list[str]:
    """provenance.unclaimed_line, once, for the first line of the body that is not what
    render_body writes there (`unclaimed_line`), naming its number, never its text; then
    FENCE_LEFT_OPEN for each claim the body cannot close (`fence_left_open`), alone or
    where the body puts it. Every gate asks this, so no compile, tidy or arrangement saves
    such a page. One rendering when the body is as written; one more, to find the line,
    when it is not."""
    from cairn.compiler import assembled  # the compiler builds on the gate

    written, in_body = assembled(page.title, page.claims, spans)
    at = None if page.body == written else unclaimed_line(page, spans)
    unclaimed = [
        f"provenance.unclaimed_line: -: line {at} of the body is not in a claim, nor a heading"
        " or fence the body writes"
    ]
    return [*(unclaimed if at is not None else []), *fence_left_open(page.claims, in_body)]


def fence_left_open(claims: Iterable[Claim], in_body: str | None = None) -> list[str]:
    """FENCE_LEFT_OPEN, once for each claim whose text leaves a code fence open that no line
    render_body could write after it closes (cairn.spans.FenceLeftOpen), and once for the
    claim `in_body` names: the one whose fence, where the body puts it after the claims
    before it, runs into the next claim and the body does not close it (compiler.assembled:
    no line closes it, or the body already closes CLOSERS_MAX such fences). Naming the
    claim, never its text. No text or page that no line closes has been found; the check
    stands so that one is refused, never saved."""
    left = []
    for claim in claims:
        try:
            open_fence_closer(claim.text)
        except FenceLeftOpen:
            left.append(claim.id)
    out = [
        f"{FENCE_LEFT_OPEN}: {claim_id}: the passage leaves a code fence open that no"
        " closing line after it closes, so the claims after it would read as code"
        for claim_id in left
    ]
    if in_body is not None and in_body not in left:
        out.append(
            f"{FENCE_LEFT_OPEN}: {in_body}: where the page puts the passage, it opens a code"
            f" fence the body does not close (a body closes at most {CLOSERS_MAX} such"
            " fences), so the claims after it would read as code"
        )
    return out


def unclaimed_line(page: CandidatePage, spans: Mapping[str, Span]) -> int | None:
    """The number of the first line of `page.body` that differs from the body
    compiler.render_body writes of the page's title, its claims in their order and the
    passages in `spans` (the line past the shorter one's end when one body holds the other);
    None when the two are equal, byte for byte.

    Every body a gate is given is written so: an extractive compile, a model's compile (the
    model drafts claims; compile_note renders the body), a tidy and the arranger
    (cairn.arrange) all call render_body with the spans they gate with. A body equal to its
    re-render holds each claim once, whole and in order, under the headings of its path at
    their depth, a closer only where Markdown leaves a claim's fence open, and nothing
    else. Pure, no statement; one rendering and one comparison."""
    from cairn.compiler import render_body  # the compiler builds on the gate

    body = page.body
    expected = render_body(page.title, page.claims, spans)
    if body == expected:
        return None
    given, written = body.split("\n"), expected.split("\n")
    return next(
        (n for n, (a, b) in enumerate(zip(given, written, strict=False), 1) if a != b),
        min(len(given), len(written)) + 1,
    )
