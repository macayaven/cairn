# Reference copy for the P1 differential test (tests/unit/test_facts_lint_differential_unit.py).
# Below these three lines: src/cairn/lint.py byte for byte as of commit 0856d6b, before P1
# made it near-linear. Nothing imports it but that test. Do not edit, format or fix it.
"""`cairn lint`: score compiled pages against their source spans.

The rules are frozen in tests/acceptance/test_t3_lint.py (inputs, guards, warnings, exit codes)
and tests/acceptance/test_t3_multisource.py (spans from several sources). `lint_pages` is the
in-memory core; `lint` loads one source document and a directory of pages, then calls it.

Matching
- A fact occurs in a text when its norm is a whole token of normalize(text): the characters
  around it are not [0-9a-z_], so "--json" is not in "--jsonl" and "5" is not in "15". An
  empty norm always occurs.
- A code_block fact is matched line by line: each entry of its `lines`.
- A claim is in the page body when its whitespace-collapsed text is a substring of the
  whitespace-collapsed body.

Pages and spans
- The body is the page text minus front matter. The title is the front-matter `title`, else
  the first H1, else the slug. Front-matter `aliases` is a list or a single string.
- A normalised title or alias is case folded, loses a trailing "-N", and has every run of
  characters other than letters, digits and spaces turned into one space.
- Content spans are paragraph, bullet, code, table and quote spans; heading and front-matter
  spans are structure. A span's source is only its id: grounding reads a claim's cited spans.

Findings
- Each finding carries code, page (the slug; "" for coverage.passage_unaccounted, which belongs
  to no page) and message, plus claim_id, span_id and fact where they apply. `fact` is the
  fact value, or the normalised line for a code line.
- One finding per (claim, span id) for provenance and attribution, per (claim, fact) for
  grounding, per (page, cited span, fact) for retention, and per (page, fact) for placement.
  Grounding is skipped for a claim with no known cited span; provenance already fails it.
- Sorted by page, code, claim_id, span_id, fact and message.

Metrics. An empty denominator gives 1.0 for retention and coverage fractions and 0.0 for
rates and counts. A span's measurable units are its non-code facts and the lines of its
code_block facts; a unit is found when it occurs in at least one page body.
- plain (every page, both modes):
  - retention: {"overall": found non-code facts of content spans / all of them,
    "by_kind": the same per fact kind present in the content spans}.
  - code_line_retention: found code lines / all code lines.
  - span_coverage: among content spans with a measurable unit, the fraction with one found.
  - zero_fact_spans: content spans without a measurable unit.
  - duplicate_entity_rate: (pages - distinct normalised titles) / pages.
  - compression_ratio: total body characters / total content-span characters.
  - placement_candidates: the number of placement.candidate warnings.
- cairn (cairn-format pages only; None when there are none):
  - pages: their count.
  - omission_rate: omitted_facts entries that name a non-code fact of a span the page cites,
    each counted once per page / the non-code facts of the spans each page cites.
  - span_coverage: content spans cited by a cairn page or listed in unassigned_spans / all
    content spans.
  - provenance_violations, grounding_violations, attribution_violations,
    retention_violations, coverage_violations: the violations of each rule family.

Known limit: facts.py rejects only digits around a date, so the date in
"2026-03-14T10:00:00Z" or "v2026-03-14" is extracted but never occurs as a whole token, not
even in its own span. A verbatim claim of such a span fails grounding and retention.
"""

import json
import re
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from cairn.facts import Fact, extract_facts, normalize
from cairn.spans import Span, chunk

CONTENT_KINDS = frozenset({"paragraph", "bullet", "code", "table", "quote"})
PLACEMENT_KINDS = frozenset({"flag", "path", "inline_code"})
PLACEMENT_MIN_CHARS = 6
FAMILIES = ("provenance", "grounding", "attribution", "retention", "coverage")

_TRAILING_NUMBER = re.compile(r"-\d+$")
_PUNCTUATION = re.compile(r"[\W_]+")


class Finding(BaseModel):
    code: str
    page: str
    message: str
    claim_id: str | None = None
    span_id: str | None = Field(default=None, serialization_alias="passage_id")
    fact: str | None = None


class LintReport(BaseModel):
    violations: list[Finding]
    warnings: list[Finding]
    metrics: dict[str, Any]
    modes: dict[str, str]


class ClaimInput(BaseModel):
    id: str
    text: str
    span_ids: list[str]


class OmittedFact(BaseModel):
    fact_id: str
    span_id: str
    reason: str


class PageInput(BaseModel):
    """One page as the core sees it: `text` is the page file (optional front matter, then the
    body); `claims` None means a plain page, a list means a cairn-format page."""

    slug: str
    text: str
    claims: list[ClaimInput] | None = None
    omitted_facts: list[OmittedFact] = Field(default_factory=list)


class Entity(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)


class LintInputError(ValueError):
    """Bad input or schema. The CLI maps it to exit code 2."""


def fact_id(span_id: str, start: int) -> str:
    """Identifier used in omitted_facts: "<span_id>#<Fact.start>"."""
    return f"{span_id}#{start}"


def lint_pages(
    pages: Sequence[PageInput],
    spans: Sequence[Span],
    entities: Sequence[Entity] = (),
    unassigned_spans: Collection[str] = (),
) -> LintReport:
    """The in-memory core: the same rules as `lint`, over spans from any number of sources.

    `lint` loads its files and calls this. Contract: tests/acceptance/test_t3_lint.py and
    tests/acceptance/test_t3_multisource.py. Raises LintInputError for unreadable front
    matter or two pages with one slug."""
    source = _Source(spans, entities)
    parsed = _parse_pages(pages)
    violations: list[Finding] = []
    warnings: list[Finding] = []
    for page in parsed:
        entity = source.page_entity(page)
        if page.claims is not None:
            violations += _check_claims(page, entity, source, warnings)
        warnings += _placement(page, entity, source)
    warnings += _duplicates(parsed)
    cairn_pages = [p for p in parsed if p.claims is not None]
    accounted = {s.id for p in cairn_pages for s in p.cited} | set(unassigned_spans)
    if cairn_pages:
        violations += [
            Finding(
                code="coverage.passage_unaccounted",
                page="",
                span_id=s.id,
                message=f"{s.kind} passage at {s.source_id} lines {s.start_line}-{s.end_line} is "
                "neither cited by a cairn page nor declared unassigned",
            )
            for s in source.content
            if s.id not in accounted
        ]
    placements = sum(w.code == "placement.candidate" for w in warnings)
    return LintReport(
        violations=_sorted(violations),
        warnings=_sorted(warnings),
        metrics={
            "plain": _plain_metrics(parsed, source, placements),
            "cairn": _cairn_metrics(cairn_pages, source, accounted, violations),
        },
        modes={p.slug: "plain" if p.claims is None else "cairn" for p in _by_slug(parsed)},
    )


def lint(source: Path, pages: Path, entities: Path | None = None) -> LintReport:
    """Lint every DIR/<slug>.md page against one source document, chunked with source_id = the
    file stem. A page is cairn-format when DIR/<slug>.claims.json exists; DIR/manifest.json and
    the entities YAML are optional. Raises LintInputError for an unreadable file or a schema
    error."""
    source, pages = Path(source), Path(pages)
    spans = chunk(source.stem, _read(source))
    if not pages.is_dir():
        raise LintInputError(f"pages directory not found: {pages}")
    loaded = [_load_page(path) for path in sorted(pages.glob("*.md")) if path.is_file()]
    manifest = pages / "manifest.json"
    unassigned = (
        _load(manifest, _Manifest, json.loads).unassigned_passages if manifest.exists() else []
    )
    known = []
    if entities is not None:
        known = _load(Path(entities), _Entities, yaml.safe_load).entities
    return lint_pages(loaded, spans, known, unassigned)


class _SidecarClaim(BaseModel):
    """A claim of DIR/<slug>.claims.json, with the ids of the passages it cites."""

    id: str
    text: str
    passage_ids: list[str]


class _SidecarOmission(BaseModel):
    """A fact DIR/<slug>.claims.json declares omitted, with the passage it is in."""

    fact_id: str
    passage_id: str
    reason: str


class _Sidecar(BaseModel):
    """DIR/<slug>.claims.json."""

    claims: list[_SidecarClaim]
    omitted_facts: list[_SidecarOmission] = Field(default_factory=list)


class _Manifest(BaseModel):
    """DIR/manifest.json."""

    unassigned_passages: list[str] = Field(default_factory=list)


class _Entities(BaseModel):
    """The --entities YAML file."""

    entities: list[Entity]


def _load_page(path: Path) -> PageInput:
    page = PageInput(slug=path.stem, text=_read(path))
    sidecar = path.with_name(f"{path.stem}.claims.json")
    if sidecar.exists():
        loaded = _load(sidecar, _Sidecar, json.loads)
        page.claims = [
            ClaimInput(id=c.id, text=c.text, span_ids=c.passage_ids) for c in loaded.claims
        ]
        page.omitted_facts = [
            OmittedFact(fact_id=o.fact_id, span_id=o.passage_id, reason=o.reason)
            for o in loaded.omitted_facts
        ]
    return page


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise LintInputError(f"cannot read {path}: {error}") from error


def _load[M: BaseModel](path: Path, model: type[M], parse: Callable[[str], Any]) -> M:
    """Parse a JSON or YAML file and validate it; any failure is a LintInputError."""
    text = _read(path)
    try:
        return model.model_validate(parse(text))
    except (ValueError, yaml.YAMLError) as error:  # JSON and pydantic errors are ValueErrors
        raise LintInputError(f"{path}: {error}") from error


# Matching


_TOKEN_CHARS = frozenset("0123456789abcdefghijklmnopqrstuvwxyz_")
_NEAR_MISSES = 32  # occurrences inside longer tokens looked at one by one before a pattern scans


def _occurs(norm: str, normalized_text: str) -> bool:
    """True when `norm` is a whole token of an already normalised text: it occurs with no
    digit, lower-case ASCII letter or underscore right before or right after it.

    The usual case is decided by `str.find` at the first occurrence, with no pattern
    compiled. A value that mostly occurs inside longer tokens ("1" in a page of numbers) is
    handed to a compiled pattern after a few near misses, so the scan stays in C. Both
    answer exactly what the pattern alone answers (tests/unit/test_lint_speed_unit.py)."""
    if not norm:
        return True
    text, size = normalized_text, len(norm)
    at = text.find(norm)
    for _ in range(_NEAR_MISSES):
        if at == -1:
            return False
        end = at + size
        if (at == 0 or text[at - 1] not in _TOKEN_CHARS) and (
            end == len(text) or text[end] not in _TOKEN_CHARS
        ):
            return True
        at = text.find(norm, at + 1)
    return at != -1 and bool(_token(norm).search(text))


@lru_cache(maxsize=4096)
def _token(norm: str) -> re.Pattern[str]:
    return re.compile(r"(?<![0-9a-z_])" + re.escape(norm) + r"(?![0-9a-z_])")


def _units(fact: Fact) -> list[tuple[str, str]]:
    """(norm, shown value) of each measurable unit: a code block's lines, else the fact."""
    if fact.kind == "code_block":
        return [(line, line) for line in fact.lines]
    return [(fact.norm, fact.value)]


def _label(fact: Fact) -> str:
    return "code line" if fact.kind == "code_block" else fact.kind.replace("_", " ")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _fold(text: str) -> str:
    return _flat(text).casefold()


def _title_key(text: str) -> str:
    """Normalised title or alias: case folded, trailing "-N" and punctuation dropped."""
    text = _TRAILING_NUMBER.sub("", _fold(text))
    return _flat(_PUNCTUATION.sub(" ", text))


def _ratio(part: int, whole: int, empty: float) -> float:
    return part / whole if whole else empty


def _sorted(findings: Iterable[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda f: (f.page, f.code, f.claim_id or "", f.span_id or "", f.fact or "", f.message),
    )


# Entities and spans


@dataclass(frozen=True)
class _Entity:
    index: int
    name: str
    keys: frozenset[str]  # normalised name and aliases, compared with page titles
    pattern: re.Pattern[str] | None  # the name or an alias as a whole word, case folded

    @classmethod
    def of(cls, index: int, entity: Entity) -> "_Entity":
        names = (entity.name, *entity.aliases)
        folded = sorted({f for n in names if (f := _fold(n))}, key=len, reverse=True)
        pattern = None
        if folded:
            pattern = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, folded)) + r")(?!\w)")
        return cls(index, entity.name, frozenset(k for n in names if (k := _title_key(n))), pattern)

    def named_in(self, text: str) -> bool:
        return self.pattern is not None and bool(self.pattern.search(_fold(text)))


class _Source:
    """The spans under lint, with their facts, normalised texts and entities."""

    def __init__(self, spans: Iterable[Span], entities: Sequence[Entity]) -> None:
        self.by_id = {s.id: s for s in spans}
        self.spans = list(self.by_id.values())
        self.content = [s for s in self.spans if s.kind in CONTENT_KINDS]
        self.facts = {s.id: extract_facts(s) for s in self.spans}
        self.norm = {s.id: normalize(s.text) for s in self.spans}
        self.entities = [_Entity.of(i, e) for i, e in enumerate(entities)]
        self.owners = {s.id: self._span_entities(s) for s in self.spans}

    def _span_entities(self, span: Span) -> frozenset[int]:
        """Entities named in the deepest heading-path segment that names at least one."""
        for segment in reversed(span.heading_path):
            named = frozenset(e.index for e in self.entities if e.named_in(segment))
            if named:
                return named
        return frozenset()

    def page_entity(self, page: "_Page") -> int | None:
        """The first entity whose normalised name or alias equals the page's normalised title,
        else one of its aliases; None for a topic page."""
        for name in (page.title, *page.aliases):
            key = _title_key(name)
            for entity in self.entities:
                if key in entity.keys:
                    return entity.index
        return None

    def about(self, owners: frozenset[int]) -> str:
        return " and ".join(self.entities[i].name for i in sorted(owners)) or "no entity"


# Pages


@dataclass
class _Page:
    slug: str
    title: str
    aliases: list[str]
    body: str
    body_spans: list[Span]
    claims: list[ClaimInput] | None
    omitted: set[str]  # fact ids declared omitted
    norm_body: str = ""
    flat_body: str = ""
    cited: list[Span] = field(default_factory=list)  # distinct known spans its claims cite

    def __post_init__(self) -> None:
        self.norm_body = normalize(self.body)
        self.flat_body = _flat(self.body)


def _parse_pages(pages: Sequence[PageInput]) -> list[_Page]:
    slugs = Counter(p.slug for p in pages)
    if duplicated := sorted(s for s, n in slugs.items() if n > 1):
        raise LintInputError(f"duplicate page slug: {', '.join(duplicated)}")
    return [_parse_page(p) for p in pages]


def _parse_page(page: PageInput) -> _Page:
    spans = chunk(page.slug, page.text)
    meta: dict[Any, Any] = {}
    if spans and spans[0].kind == "frontmatter":
        meta = _front_matter(page.slug, spans[0])
        body = "".join(page.text.splitlines(keepends=True)[spans[0].end_line :])
        spans = spans[1:]
    else:
        body = page.text
    title = meta.get("title")
    if isinstance(title, list | dict):
        raise LintInputError(f"page {page.slug}: the front-matter title must be text")
    return _Page(
        slug=page.slug,
        title=(str(title) if title is not None else "") or _first_h1(spans) or page.slug,
        aliases=_aliases(page.slug, meta.get("aliases")),
        body=body,
        body_spans=spans,
        claims=page.claims,
        omitted={o.fact_id for o in page.omitted_facts},
    )


def _front_matter(slug: str, span: Span) -> dict[Any, Any]:
    try:
        meta = yaml.safe_load("\n".join(span.text.split("\n")[1:-1]))
    except yaml.YAMLError as error:
        raise LintInputError(f"page {slug}: front matter is not valid YAML: {error}") from error
    if meta is None:
        return {}
    if not isinstance(meta, dict):
        raise LintInputError(f"page {slug}: front matter must be a mapping")
    return meta


def _aliases(slug: str, value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    if any(isinstance(item, list | dict) for item in items):
        raise LintInputError(f"page {slug}: front-matter aliases must be text or a list of text")
    return [str(item) for item in items if item is not None]


def _first_h1(spans: Iterable[Span]) -> str | None:
    for span in spans:
        marks = span.text.lstrip(" ")
        if span.kind == "heading" and len(marks) - len(marks.lstrip("#")) == 1:
            return span.heading_path[-1]
    return None


def _by_slug(pages: Iterable[_Page]) -> list[_Page]:
    return sorted(pages, key=lambda p: p.slug)


# Cairn-format guards


def _check_claims(
    page: _Page, entity: int | None, source: _Source, warnings: list[Finding]
) -> list[Finding]:
    """Provenance, grounding and attribution per claim, then retention per cited span. Fills
    `page.cited`; attribution warnings go to `warnings`."""
    violations: list[Finding] = []
    cited: dict[str, Span] = {}
    for claim in page.claims or ():
        known = []
        if not claim.span_ids:
            violations.append(
                Finding(
                    code="provenance.missing_passages",
                    page=page.slug,
                    claim_id=claim.id,
                    message="the claim cites no passage",
                )
            )
        for span_id in dict.fromkeys(claim.span_ids):
            if span_id in source.by_id:
                known.append(source.by_id[span_id])
                continue
            violations.append(
                Finding(
                    code="provenance.unknown_passage",
                    page=page.slug,
                    claim_id=claim.id,
                    span_id=span_id,
                    message=f"the claim cites {span_id}, which is not a source passage",
                )
            )
        if _flat(claim.text) not in page.flat_body:
            violations.append(
                Finding(
                    code="provenance.claim_not_in_body",
                    page=page.slug,
                    claim_id=claim.id,
                    message="the claim text is not in the page body",
                )
            )
        if known:
            violations += _grounding(page.slug, claim, known, source)
        if entity is not None:
            violations += _attribution(page.slug, claim, known, entity, source, warnings)
        cited.update((s.id, s) for s in known)
    page.cited = list(cited.values())
    return violations + _retention(page, source)


def _grounding(slug: str, claim: ClaimInput, cited: list[Span], source: _Source) -> list[Finding]:
    texts = [source.norm[s.id] for s in cited]
    claim_span = Span(
        id=f"{slug}#{claim.id}",
        source_id=slug,
        kind="paragraph",
        heading_path=(),
        start_line=1,
        end_line=1 + claim.text.count("\n"),
        text=claim.text,
    )
    out, seen = [], set()
    for fact in extract_facts(claim_span):
        for norm, shown in _units(fact):
            if norm not in seen and not any(_occurs(norm, text) for text in texts):
                out.append(
                    Finding(
                        code="grounding.fact_not_in_cited_passages",
                        page=slug,
                        claim_id=claim.id,
                        fact=shown,
                        message=f"{_label(fact)} {shown!r} is in none of the claim's cited"
                        " passages",
                    )
                )
            seen.add(norm)
    return out


def _attribution(
    slug: str,
    claim: ClaimInput,
    cited: list[Span],
    entity: int,
    source: _Source,
    warnings: list[Finding],
) -> list[Finding]:
    violations = []
    page_entity = source.entities[entity]
    for span in cited:
        owners = source.owners[span.id]
        if entity in owners:
            continue
        about = source.about(owners)
        if page_entity.named_in(span.text):
            warnings.append(
                Finding(
                    code="attribution.text_mention_only",
                    page=slug,
                    claim_id=claim.id,
                    span_id=span.id,
                    message=f"the cited passage is about {about}; only its text mentions "
                    f"{page_entity.name}",
                )
            )
        elif owners:
            violations.append(
                Finding(
                    code="attribution.other_entity",
                    page=slug,
                    claim_id=claim.id,
                    span_id=span.id,
                    message=f"the cited passage is about {about} and never mentions "
                    f"{page_entity.name}",
                )
            )
    return violations


def _retention(page: _Page, source: _Source) -> list[Finding]:
    out = []
    on_page: dict[str, bool] = {}

    def kept(norm: str) -> bool:
        """Whether the page's body holds `norm`: looked for once per distinct value, so a
        page's check grows with the page and not with the page squared."""
        if norm not in on_page:
            on_page[norm] = _occurs(norm, page.norm_body)
        return on_page[norm]

    for span in page.cited:
        for fact in source.facts[span.id]:
            if fact.kind == "code_block":
                out += [
                    Finding(
                        code="retention.code_line_dropped",
                        page=page.slug,
                        span_id=span.id,
                        fact=line,
                        message=f"code line {line!r} of the cited passage is not on the page",
                    )
                    for line in fact.lines
                    if not kept(line)
                ]
                continue
            fid = fact_id(span.id, fact.start)
            if not kept(fact.norm) and fid not in page.omitted:
                out.append(
                    Finding(
                        code="retention.fact_dropped",
                        page=page.slug,
                        span_id=span.id,
                        fact=fact.value,
                        message=f"{_label(fact)} {fact.value!r} ({fid}) of the cited passage is "
                        "neither on the page nor declared omitted",
                    )
                )
    return out


# Warnings for every page


def _placement(page: _Page, entity: int | None, source: _Source) -> list[Finding]:
    """Flag, path and long inline-code facts on entity page X whose every source span is about
    one other entity Y and never mentions X."""
    if entity is None:
        return []
    page_entity = source.entities[entity]
    out, seen = [], set()
    for fact in (f for s in page.body_spans for f in extract_facts(s)):
        if fact.kind not in PLACEMENT_KINDS or len(fact.value) < PLACEMENT_MIN_CHARS:
            continue
        if not fact.norm or fact.norm in seen:
            continue
        seen.add(fact.norm)
        holders = [s for s in source.spans if _occurs(fact.norm, source.norm[s.id])]
        owners = {source.owners[s.id] for s in holders}
        if len(owners) != 1:
            continue  # in no source span, or in spans about different entities
        (owner,) = owners
        if len(owner) != 1 or entity in owner:
            continue
        if any(page_entity.named_in(s.text) for s in holders):
            continue
        out.append(
            Finding(
                code="placement.candidate",
                page=page.slug,
                span_id=holders[0].id,
                fact=fact.value,
                message=f"{_label(fact)} {fact.value!r}: every source passage containing it "
                f"({len(holders)}) is about {source.about(owner)} and none mentions "
                f"{page_entity.name}",
            )
        )
    return out


def _duplicates(pages: list[_Page]) -> list[Finding]:
    groups: dict[str, list[_Page]] = {}
    for page in pages:
        groups.setdefault(_title_key(page.title), []).append(page)
    return [
        Finding(
            code="entity.duplicate",
            page=page.slug,
            message=f"title {page.title!r} normalises to {key!r}, as does the title of: "
            + ", ".join(sorted(other.slug for other in group if other is not page)),
        )
        for key, group in groups.items()
        if len(group) > 1
        for page in group
    ]


# Metrics


def _plain_metrics(pages: list[_Page], source: _Source, placements: int) -> dict[str, Any]:
    bodies = [p.norm_body for p in pages]
    seen: dict[str, bool] = {}

    def found(norm: str) -> bool:
        if norm not in seen:
            seen[norm] = any(_occurs(norm, body) for body in bodies)
        return seen[norm]

    facts, facts_found = Counter[str](), Counter[str]()
    lines = lines_found = measurable = covered = 0
    for span in source.content:
        hits = []
        for fact in source.facts[span.id]:
            for norm, _ in _units(fact):
                hit = found(norm)
                hits.append(hit)
                if fact.kind == "code_block":
                    lines += 1
                    lines_found += hit
                else:
                    facts[fact.kind] += 1
                    facts_found[fact.kind] += hit
        measurable += bool(hits)
        covered += any(hits)
    distinct_titles = len({_title_key(p.title) for p in pages})
    return {
        "retention": {
            "overall": _ratio(facts_found.total(), facts.total(), 1.0),
            "by_kind": {kind: facts_found[kind] / facts[kind] for kind in sorted(facts)},
        },
        "code_line_retention": _ratio(lines_found, lines, 1.0),
        "passage_coverage": _ratio(covered, measurable, 1.0),
        "zero_fact_passages": len(source.content) - measurable,
        "duplicate_entity_rate": _ratio(len(pages) - distinct_titles, len(pages), 0.0),
        "compression_ratio": _ratio(
            sum(len(p.body) for p in pages), sum(len(s.text) for s in source.content), 0.0
        ),
        "placement_candidates": placements,
    }


def _cairn_metrics(
    pages: list[_Page], source: _Source, accounted: set[str], violations: list[Finding]
) -> dict[str, Any] | None:
    if not pages:
        return None
    owed = excused = 0
    for page in pages:
        ids = {
            fact_id(s.id, f.start)
            for s in page.cited
            for f in source.facts[s.id]
            if f.kind != "code_block"
        }
        owed += len(ids)
        excused += len(ids & page.omitted)
    families = Counter(v.code.split(".", 1)[0] for v in violations)
    return {
        "pages": len(pages),
        "omission_rate": _ratio(excused, owed, 0.0),
        "passage_coverage": _ratio(
            sum(s.id in accounted for s in source.content), len(source.content), 1.0
        ),
        **{f"{family}_violations": families[family] for family in FAMILIES},
    }
