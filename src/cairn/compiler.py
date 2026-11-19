"""Baseline extractive compiler: notes become pages whose claims are verbatim spans.

Contract (frozen by tests/acceptance/test_w1_store.py):
- A note compiles into the page of its topic (N1, below), titled with its topic, else
  its title (`note.topic or note.title`). Each content span (paragraph, bullet,
  code, table, quote) becomes one claim: id = claim_id(span.id), text = span.text
  verbatim, span_ids = (span.id,). Headings and front matter are structure, not claims.
- A later note with the same topic updates the page: the new version keeps every existing
  claim in order and appends claims for spans it does not cite yet. Nothing new: "unchanged".
- The body is "# <title>" followed by the claim texts in claim order, separated by blank
  lines. The title is one line of heading text (heading_text: each line break and the blanks
  around it one space, review blocker 1). A claim is preceded by the headings of its span's
  path that differ from the previous claim's (section_headings; past level 6 the rest of the
  path is one breadcrumb heading, should-fix 9). A claim whose text leaves a code fence open
  as Markdown reads it (it copies a note that never closes one, or closes it deeper than
  Markdown's limit) is followed by a line that closes it, the opener's own indentation and
  run, so the claim after it is never read as code (review 7.2, should-fix 8); the claim's
  text is never changed. Every line of the body is a claim's, a heading or fence it wrote, or
  blank: each gate checks that (gate.unclaimed_line).
- Before saving, the candidate page and the spans its claims cite go through a gate. A
  failing gate saves nothing and returns status "rejected" with the gate's result. The gate's
  metrics are saved with the page version.
- This is the extractive baseline: it passes the provenance and grounding guards by
  construction. Synthesis quality is neither built nor measured here.

Composers (T4, frozen by tests/acceptance/test_t4_llm_compile.py):
- `compile_note(..., compose=extractive)`: `compose(title, new_spans)` drafts the claims for
  the new content spans and returns them with `DraftStats` (who drafted them, model calls,
  tokens, seconds). It is never called when nothing is new. The candidate is the existing
  claims in order, then the drafted ones; its body is rendered by `render_body`.
- The gate gets the spans the existing claims cite and the new spans, nothing else, so a
  drafted citation of any other span (even one in the store) is provenance.unknown_passage.
- A composer that cannot draft raises ComposeError: the compile is "rejected" with that one
  violation ("<code>: -: <message>"), its stats as `draft`, and no candidate.
- The result carries `draft` (None when nothing was composed) and `candidate`, the page that
  went through the gate, whether it was saved or rejected.

What a draft may say, cite and cost (T5, frozen by tests/acceptance/test_t5_grounding.py):
- With no gate named, the extractive composer is gated by provenance_gate, as before, and any
  other composer (a wrapped extractive one included) by cairn.gate.verbatim_gate.
  The existing claims go through the same gate as the new ones, so a page holding reworded
  claims saved under an explicitly named lint gate, or claims cut from or joining passages
  that the verbatim gate passed before T5d, fails the verbatim gate on its next model compile.
- A drafted claim may cite only spans its composer was offered. When the gate passed a draft
  that cites any other span, even one the page already cites, the compile is "rejected" with
  one "provenance.passage_not_offered: <claim id>: <span id> ..." violation per citation and the
  gate's metrics. When the gate already failed, its violations stand alone (T4's frozen tests
  pin a rejection's exact codes); such a draft is never saved either way.
- `DraftStats.usage_complete` is False when a model call reported no token counts or failed,
  so a partial total is never mistaken for the whole cost. It is True for the extractive
  draft, and a sum of several drafts is complete only when each was.

Concurrency (T5, frozen by tests/acceptance/test_t5_grounding.py):
- No lock is held across a composer's call. A compile saves with the version it read
  (`save_page(expected_version=...)`, checked in the store's write transaction). When another
  writer, in this process or another, saved first, it reads the page again, rebuilds and gates
  the candidate again and saves again, SAVE_TRIES (3) times in all. The draft is reused when the
  spans to draft are the same as before; otherwise the composer is called again, and `draft`
  adds up every call's attempts, tokens and seconds. A page that came to cover every span is
  "unchanged". A page that keeps moving is "rejected" with one
  "compile.stale_page: -: ..." violation (STALE_PAGE; `lost_race` says so) and the last
  candidate.

The page of a topic (N1, frozen by tests/acceptance/test_n1_note_filing.py). This module
chooses a note's page, and nothing else does: `page_for`, which asks the store.
- A note is filed under its topic key (cairn.topics.topic_key), made once when it was
  stored, from its topic, else its title as given (cairn.store.stored_key). Its page is
  the project's page of that key (Store.topic_page) whatever the note calls it; the note's
  `topic or title` may change the page's displayed title, never its key.
- A key no page has makes a new page. Its slug is `slugify(title)` (unchanged, pinned by
  test_w1_store.py::test_slugify) when no page of the project has that slug and slugify kept
  something of the title, or the key is empty ("!!!": plain "untitled" when free).
  Otherwise it is that slug, "-" and the key's tag (cairn.topics.topic_tag: lower-case hex
  of its SHA-256, the same on every install), 8 characters, else 12, else 16 when a
  shorter one's slug is another topic's, with the slug cut at a "-" so the whole keeps
  within SLUG_MAX; past those, the 16-character one and "-2", "-3", and so on. Which
  slugs are taken is one read (Store.taken_slugs) for all of them.
- The page is chosen again on every try, and saved for the key (`save_page(topic=key)`),
  which the store refuses (StalePage) when the slug is another topic's or the key has
  another page by then: two notes of two topics racing for one free slug end on two
  pages, two of one topic on one. A saved page ends the note's wait in the same write
  (`filed`); every other outcome is recorded by the caller (cairn.memory).
"""

import itertools
import re
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from cairn.candidate import CandidatePage, GateResult
from cairn.gate import BREADCRUMB, unclaimed_violations, verbatim_gate
from cairn.spans import Span, claim_id, fence_closes, fence_opener
from cairn.store import Claim, Note, Page, StalePage, Store, stored_key
from cairn.topics import topic_tag

CONTENT_KINDS = frozenset({"paragraph", "bullet", "code", "table", "quote"})
SLUG_MAX = 64
SAVE_TRIES = 3  # reads of a page that keeps moving before a compile gives up
STALE_PAGE = "compile.stale_page"  # the code of a compile that lost its page SAVE_TRIES times
TAG_SIZES = (8, 12, 16)  # hex characters of the tag a colliding topic's slug ends with
_ADDRESS_BATCH = 8  # slugs looked up in one read when choosing a new page's address
# A code fence, three or more backticks or tildes after any indentation and any list marker
# (a fence may open on an item's marker line), and its info string.
_FENCE = re.compile(r"([ \t]*)((?:[-*+]|\d{1,9}[.)])[ \t]+)?(`{3,}|~{3,})(.*)")
_ITEM_MARK = re.compile(r"(?:[-*+]|\d{1,9}[.)])(?:[ \t]|$)")  # a list item's marker


Gate = Callable[[CandidatePage, Mapping[str, Span]], GateResult]


class DraftStats(BaseModel):
    """How a draft was made. Tokens are None when nothing reported them."""

    model_config = ConfigDict(frozen=True)

    composer: str  # "extractive", or the model's name
    attempts: int = 0  # model calls made
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_s: float = 0.0  # wall-clock seconds spent drafting
    # False when a model call reported no prompt or no completion tokens, or failed: the
    # totals above are then only what was reported, not the whole cost.
    usage_complete: bool = True


class Draft(BaseModel):
    model_config = ConfigDict(frozen=True)

    claims: tuple[Claim, ...]
    stats: DraftStats


class ComposeError(Exception):
    """A composer could not draft claims; compile_note rejects with `violation`."""

    def __init__(self, code: str, message: str, stats: DraftStats) -> None:
        self.code, self.message, self.stats = code, message, stats
        super().__init__(self.violation)

    @property
    def violation(self) -> str:
        return f"{self.code}: -: {self.message}"


Composer = Callable[[str, Sequence[Span]], Draft]


class CompileResult(BaseModel):
    note_id: str
    slug: str
    status: Literal["saved", "unchanged", "rejected"]
    version: int | None  # the page's version after this call; None when no page exists
    gate: GateResult | None = None  # None when nothing was gated ("unchanged")
    draft: DraftStats | None = None  # None when nothing was composed ("unchanged")
    candidate: CandidatePage | None = None  # the gated page, saved or rejected


def slugify(text: str) -> str:
    """NFKD-decompose and drop non-ASCII (so "ü" becomes "u"), lowercase, turn each run of
    characters outside [a-z0-9] into "-", trim "-" from both ends, keep at most 64 characters
    (cut at a "-" boundary when possible), and return "untitled" when nothing is left."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    if len(slug) > SLUG_MAX:
        cut = slug.rfind("-", 0, SLUG_MAX + 1)  # the last "-" at or before index 64
        slug = slug[:cut] if cut > 0 else slug[:SLUG_MAX]
    return slug or "untitled"


def page_for(store: Store, project_id: str, key: str, title: str) -> str:
    """The slug of the page a note of topic key `key`, titled `title` (its `topic or title`),
    compiles into (N1, see the module): the project's page of that key, else the first of
    `_addresses` that no page of the project has and no topic of it holds. Reads only."""
    held = store.topic_page(project_id, key)
    if held is not None:
        return held
    candidates = _addresses(key, title)
    while True:  # ends: the candidates never run out, and a project holds finitely many slugs
        batch = list(itertools.islice(candidates, _ADDRESS_BATCH))
        taken = store.taken_slugs(project_id, batch)
        free = next((slug for slug in batch if slug not in taken), None)
        if free is not None:
            return free


def _addresses(key: str, title: str) -> Iterator[str]:
    """The slugs a new page of `key` may take, in the order they are tried: `slugify(title)`
    when slugify kept something of it or the key is empty, then that slug tagged with the
    key's 8, 12 and 16 hex characters, then the 16 and "-2", "-3", and so on."""
    base = slugify(title)
    if not key or _kept(title):
        yield base
    for size in TAG_SIZES:
        yield _tagged(base, topic_tag(key, size))
    longest = topic_tag(key, TAG_SIZES[-1])
    for n in itertools.count(2):
        yield _tagged(base, f"{longest}-{n}")


def _kept(title: str) -> bool:
    """Whether slugify keeps a letter or digit of `title` (its "untitled" is then a word)."""
    ascii_text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    return re.search(r"[a-z0-9]", ascii_text.lower()) is not None


def _tagged(base: str, tag: str) -> str:
    """`base`, cut at a "-" (else at the bound) so that "-" and `tag` fit within SLUG_MAX,
    then "-" and `tag`."""
    room = SLUG_MAX - len(tag) - 1
    if len(base) > room:
        cut = base.rfind("-", 0, room + 1)
        base = base[:cut] if cut > 0 else base[:room]
    return f"{base.strip('-')}-{tag}"


def lost_race(result: "CompileResult") -> bool:
    """Whether a compile ended because its page moved under it SAVE_TRIES times in a row."""
    gate = result.gate
    return (
        result.status == "rejected"
        and gate is not None
        and any(violation.startswith(STALE_PAGE) for violation in gate.violations)
    )


def section_headings(
    title: str, path: Sequence[str], previous: Sequence[str]
) -> tuple[tuple[str, ...], list[tuple[int, str]]]:
    """A claim's section and the headings to write before it, as (level, text), when the
    claim shown before it had the section `previous`. The section is `path` (its heading
    path) less a first heading that is the page's title; empty, the claim has no section.
    Each heading is written at its depth, the first at level 2; in a section deeper than
    levels 2 to 6 hold, its fifth heading and every one after it are one heading at level 6,
    joined by BREADCRUMB (should-fix 9), so its whole ancestry is shown and no heading of it
    reads as a sibling of its parent. They are written from the first that differs from
    `previous`'s, or, when the section is `previous` cut short, its last, so no claim reads
    as under a heading off its path."""
    section = tuple(path[1:] if path[:1] == (title,) else path)
    return section, _headings(_shown(section), _shown(tuple(previous)), lambda depth: depth + 2)


def _shown(section: tuple[str, ...]) -> tuple[str, ...]:
    """The headings a section is written as: its own while levels 2 to 6 hold them, else its
    first four and the rest as one breadcrumb."""
    deepest = 6 - 2  # the index of the heading written at level 6
    if len(section) <= deepest + 1:
        return section
    return (*section[:deepest], BREADCRUMB.join(section[deepest:]))


def _headings(
    shown: tuple[str, ...], before: tuple[str, ...], level: Callable[[int], int]
) -> list[tuple[int, str]]:
    """The headings of `shown` to write after a claim under `before`: from the first that
    differs, or, when `shown` is `before` cut short, its last (it is written again)."""
    same = 0
    while same < min(len(shown), len(before)) and shown[same] == before[same]:
        same += 1
    if shown and same == len(shown) < len(before):
        same -= 1  # back up to a parent: it is written again
    return [(level(depth), shown[depth]) for depth in range(same, len(shown))]


def _section_headings_before_review_two(
    title: str, path: Sequence[str], previous: Sequence[str]
) -> tuple[tuple[str, ...], list[tuple[int, str]]]:
    """section_headings as it was before should-fix 9: past level 6 every heading was written
    at level 6. For the import's check alone (render_body_before_review_two)."""
    section = tuple(path[1:] if path[:1] == (title,) else path)
    return section, _headings(section, tuple(previous), lambda depth: min(depth + 2, 6))


def heading_text(title: str) -> str:
    """A page's title as the body writes it: one line of heading text (review blocker 1). A
    title of one line is as it is; in any other, each line break (every one str.splitlines
    knows, as cairn.spans splits a note: CR, LF, the line and paragraph separators and the
    rest) with the blanks around it is one space, and blank lines are dropped. The one place
    the rule lives: a body is rendered when it is read, so a title taken in, stored or
    imported with a line break is written by it, never as given."""
    lines = title.splitlines()
    if lines == [title]:
        return title
    return " ".join(line.strip() for line in lines if line.strip())


def render_body(title: str, claims: Sequence[Claim], spans: Mapping[str, Span]) -> str:
    """Render the extractive body. A claim's section is the heading path of its first span
    found in `spans` (section_headings); a claim with no known span counts as having no
    heading path. The claims with no section come first, in their order; then the others in
    their order, each under its section's headings. The title is written as heading_text.
    One pass over `claims`."""
    return _rendered(title, claims, spans, _closed, section_headings, heading_text)


def render_body_with_fences_open(
    title: str, claims: Sequence[Claim], spans: Mapping[str, Span]
) -> str:
    """The body as render_body wrote it before review 7.2, a claim that leaves a code fence
    open left open. For the import's check alone (store.portable._as_rendered), so an
    archive written then still imports; never shown."""
    return _rendered(
        title, claims, spans, lambda text: text, _section_headings_before_review_two, str
    )


def render_body_before_review_two(
    title: str, claims: Sequence[Claim], spans: Mapping[str, Span]
) -> str:
    """The body as render_body wrote it before the second review: the title as given
    (blocker 1), every heading past level 6 at level 6 (should-fix 9), and a fence closed by
    any line of its run at any indentation (should-fix 8). For the import's check alone
    (store.portable._as_rendered), so an archive written then still imports; never shown."""
    return _rendered(
        title, claims, spans, _closed_before_review_two, _section_headings_before_review_two, str
    )


def _rendered(
    title: str,
    claims: Sequence[Claim],
    spans: Mapping[str, Span],
    closed: Callable[[str], str],
    headings_of: Callable[
        [str, Sequence[str], Sequence[str]], tuple[tuple[str, ...], list[tuple[int, str]]]
    ],
    written: Callable[[str], str],
) -> str:
    top, rest = [f"# {written(title)}"], []
    previous: tuple[str, ...] = ()
    for claim in claims:
        path = next((spans[i].heading_path for i in claim.span_ids if i in spans), ())
        section, headings = headings_of(title, path, previous)
        if not section:
            top.append(closed(claim.text))
            continue
        rest.extend(f"{'#' * level} {text}" for level, text in headings)
        rest.append(closed(claim.text))
        previous = section
    return "\n\n".join([*top, *rest]) + "\n"


def _closed(text: str) -> str:
    """`text`, and when it leaves a code fence open as Markdown reads it, a line that closes
    it: the opener's own indentation (past a list marker it opens after) and run, so it closes
    inside a list item too. Openers and closers are cairn.spans' rules (fence_opener,
    fence_closes: one rule for splitting and for the body, should-fix 8), held to Markdown's
    limit: a fence opens, and a closer closes it, indented at most 3 columns past the text of
    the list item it sits in, or past the margin. A closer indented deeper is the code's text
    ("    ~~~" under "~~~"), so the fence is still open and the body closes it; a line
    indented 4 past its item opens no fence (it is indented code), so nothing is added."""
    opened: tuple[str, str, int] | None = None  # (indentation to write, run, deepest closer)
    items: list[int] = []  # the text column of each list item a line may sit in
    for line in text.split("\n"):
        if opened is not None:
            if fence_closes(line, opened[1], opened[2]):
                opened = None
            continue
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        while items and indent < items[-1] and _ITEM_MARK.match(line.lstrip()):
            items.pop()  # a marker less indented starts an item of a list further out
        container = next((column for column in reversed(items) if column <= indent), 0)
        if indent - container > 3:
            continue  # indented code, or text that goes on: no marker, no fence
        found = fence_opener(line)
        if _ITEM_MARK.match(line.lstrip()):
            column = found[0] if found else _text_column(line)
            items.append(column)
            container = column
        if found:
            column, run = found
            written = "".join(c if c == "\t" else " " for c in line[:column])
            opened = (written, run, container + 3)
    return text if opened is None else f"{text}\n{opened[0]}{opened[1]}"


def _text_column(line: str) -> int:
    """Where a list item's text starts on its marker line: past its indentation, the marker
    and the blanks after it."""
    body = line.lstrip()
    marker = _ITEM_MARK.match(body)
    rest = body[marker.end() :] if marker else body
    return len(line) - len(rest.lstrip())


def _closed_before_review_two(text: str) -> str:
    """`_closed` as it was before should-fix 8: any line of the opener's run closed it, at any
    indentation, and a line of any indentation opened one. For the import's check alone
    (render_body_before_review_two)."""
    opened: tuple[str, str] | None = None  # (indentation, run)
    for line in text.split("\n"):
        fence = _FENCE.fullmatch(line.rstrip(" \t\r"))
        if not fence:
            continue
        indent, marker, run, rest = fence.groups()
        if opened is None:
            if not (run[0] == "`" and "`" in rest):
                opened = (indent + " " * len(marker or ""), run)
        elif (
            not marker
            and run[0] == opened[1][0]
            and len(run) >= len(opened[1])
            and not rest.strip(" \t")
        ):
            opened = None
    return text if opened is None else f"{text}\n{opened[0]}{opened[1]}"


def previous_render_body(title: str, claims: Sequence[Claim], spans: Mapping[str, Span]) -> str:
    """The body as cairn wrote it before fix H (one "##" of a path's last heading wherever
    the path changed, nothing for a claim with no section). For the import's check alone
    (store.portable._as_rendered), so an archive written then still imports; never shown."""
    blocks = [f"# {title}"]
    previous: tuple[str, ...] | None = None
    for claim in claims:
        path = next((spans[i].heading_path for i in claim.span_ids if i in spans), ())
        if path != previous and path and path[-1] != title:
            blocks.append(f"## {path[-1]}")
        blocks.append(claim.text)
        previous = path
    return "\n\n".join(blocks) + "\n"


def candidate_page(
    slug: str,
    title: str,
    existing: Page | None,
    new_spans: Sequence[Span],
    *,
    existing_spans: Mapping[str, Span] | None = None,
    claims: Sequence[Claim] | None = None,
) -> CandidatePage:
    """The existing claims in order, then `claims` (default: one verbatim claim per new span).

    `existing_spans` (the spans the existing claims cite) give those claims their heading
    paths in the body; without them, an updated body loses the existing claims' "##" lines.
    """
    new = extractive(title, new_spans).claims if claims is None else tuple(claims)
    every = (*(existing.claims if existing else ()), *new)
    spans = {**(existing_spans or {}), **{s.id: s for s in new_spans}}
    return CandidatePage(
        slug=slug, title=title, body=render_body(title, every, spans), claims=every
    )


def extractive(title: str, spans: Sequence[Span]) -> Draft:
    """The baseline composer: one verbatim claim per span, claim id claim_id(span.id)."""
    claims = tuple(Claim(id=claim_id(s.id), text=s.text, span_ids=(s.id,)) for s in spans)
    return Draft(claims=claims, stats=DraftStats(composer="extractive"))


def provenance_gate(page: CandidatePage, spans: Mapping[str, Span]) -> GateResult:
    """The default gate. Violations: a claim with no span ids, a span id missing from `spans`,
    a claim text (whitespace-collapsed) missing from the body, and the reverse: a line of the
    body that is no claim's, nor a heading or fence the body writes (gate.unclaimed_line,
    review blocker 1). Metrics: {"claims": n, "spans_cited": distinct cited span count}.

    Each violation reads "<code>: <claim id>: <message>", like cairn.gate.lint_gate's."""
    violations = []
    body = " ".join(page.body.split())
    for claim in page.claims:
        if not claim.span_ids:
            violations.append(
                f"provenance.missing_passages: {claim.id}: the claim cites no passage"
            )
        violations.extend(
            f"provenance.unknown_passage: {claim.id}: {span_id} is not a known passage"
            for span_id in claim.span_ids
            if span_id not in spans
        )
        if " ".join(claim.text.split()) not in body:
            violations.append(
                f"provenance.claim_not_in_body: {claim.id}: the claim text is not in the body"
            )
    violations.extend(unclaimed_violations(page, spans))  # the reverse (review blocker 1)
    cited = {span_id for claim in page.claims for span_id in claim.span_ids}
    return GateResult(
        passed=not violations,
        violations=violations,
        metrics={"claims": len(page.claims), "spans_cited": len(cited)},
    )


def compile_note(
    store: Store,
    note: Note,
    gate: Gate | None = None,
    *,
    compose: Composer = extractive,
) -> CompileResult:
    """Compile a stored note into its page.

    A content span is new unless the page already cites it or already holds its claim id.
    `compose` drafts the claims for the new spans. The gate gets the spans the existing claims
    cite and the new spans, as found in the store; the body is rendered from the same spans,
    so existing claims keep their headings.

    No lock is held: the page is saved with the version that was read
    (`save_page(expected_version=...)`). When another writer saved first, the page is read
    again and the candidate rebuilt and gated again, up to SAVE_TRIES times in all; the
    draft is reused when the spans to draft are the same, and composed again otherwise.

    With no gate named, the extractive composer is gated by provenance_gate and any other
    composer by cairn.gate.verbatim_gate: a model may copy whole passages and arrange them,
    never write.
    """
    if gate is None:
        gate = provenance_gate if compose is extractive else _verbatim_gate()
    title = note.topic or note.title
    key = stored_key(note)  # N1: the topic the note was stored under
    outcome: dict[str, Any] = {"note_id": note.id}
    draft: Draft | None = None
    drafted: list[str] = []  # the span ids `draft` was composed for
    stats: DraftStats | None = None  # every draft's cost, added up
    candidate: CandidatePage | None = None
    for _ in range(SAVE_TRIES):
        slug = outcome["slug"] = page_for(store, note.project_id, key, title)  # chosen anew
        existing = store.get_page(note.project_id, slug)
        version = existing.version if existing else None
        claims = existing.claims if existing else ()
        cited = [span_id for claim in claims for span_id in claim.span_ids]
        covered, on_page = set(cited), {claim.id for claim in claims}
        new_spans = [
            s
            for s in store.note_spans(note.id)
            if s.kind in CONTENT_KINDS and s.id not in covered and claim_id(s.id) not in on_page
        ]
        if not new_spans:
            return CompileResult(**outcome, status="unchanged", version=version, draft=stats)
        spans = store.get_spans([*cited, *(s.id for s in new_spans)])
        if draft is None or drafted != [s.id for s in new_spans]:
            try:
                draft = compose(title, new_spans)
            except ComposeError as error:
                return CompileResult(
                    **outcome,
                    status="rejected",
                    version=version,
                    gate=GateResult(passed=False, violations=[error.violation]),
                    draft=_add_stats(stats, error.stats),
                )
            drafted, stats = [s.id for s in new_spans], _add_stats(stats, draft.stats)
        candidate = candidate_page(
            slug, title, existing, new_spans, existing_spans=spans, claims=draft.claims
        )
        result = _offered_only(gate(candidate, spans), draft.claims, drafted)
        judged = {**outcome, "gate": result, "draft": stats, "candidate": candidate}
        if not result.passed:
            return CompileResult(**judged, status="rejected", version=version)
        try:
            page = store.save_page(
                note.project_id,
                slug,
                title,
                candidate.body,
                candidate.claims,
                metrics=result.metrics,
                expected_version=version or 0,
                topic=key,
                filed=note.id,
            )
        except StalePage:  # the page moved, or its slug or key is another topic's now
            continue
        return CompileResult(**judged, status="saved", version=page.version)
    latest = store.get_page(note.project_id, slug)
    stale = f"{STALE_PAGE}: -: the page changed while it was compiled, {SAVE_TRIES} times in a row"
    return CompileResult(
        **outcome,
        status="rejected",
        version=latest.version if latest else None,
        gate=GateResult(passed=False, violations=[stale]),
        draft=stats,
        candidate=candidate,
    )


def _verbatim_gate() -> Gate:
    return verbatim_gate


def _offered_only(
    result: GateResult, drafted: Sequence[Claim], offered: Sequence[str]
) -> GateResult:
    """The gate's result, failed with provenance.passage_not_offered when it passed a drafted
    claim that cites a span the composer was not offered.

    A failed gate's result is returned as it is: T4's frozen tests pin a rejection's exact
    violations (test_t4_coverage_guard.py), so this rule adds a reason only where it alone
    changes the outcome. Either way such a draft is never saved."""
    if not result.passed:
        return result
    allowed = set(offered)
    violations = [
        f"provenance.passage_not_offered: {claim.id}: {span_id} was not offered to the composer"
        for claim in drafted
        for span_id in claim.span_ids
        if span_id not in allowed
    ]
    if not violations:
        return result
    return GateResult(passed=False, violations=violations, metrics=result.metrics)


def _add_stats(total: DraftStats | None, more: DraftStats) -> DraftStats:
    """`more`, with the attempts, tokens and seconds of the drafts before it added in."""
    if total is None:
        return more
    return more.model_copy(
        update={
            "attempts": total.attempts + more.attempts,
            "prompt_tokens": _add(total.prompt_tokens, more.prompt_tokens),
            "completion_tokens": _add(total.completion_tokens, more.completion_tokens),
            "latency_s": total.latency_s + more.latency_s,
            "usage_complete": total.usage_complete and more.usage_complete,
        }
    )


def _add(total: int | None, value: int | None) -> int | None:
    return total if value is None else value if total is None else total + value
