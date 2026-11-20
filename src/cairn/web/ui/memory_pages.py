"""The pages that show memory: the projects, a project's pages and notes, a page with its
claims and their passages (page_view), the pages related to a page (related_view), and
search."""

from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import Response

from cairn.compiler import body_to_read, section_headings
from cairn.kinds import GENERAL
from cairn.memory import RELATED_RANGE, SLACK_FILED, Memory, NotFound, is_outside
from cairn.relations import MENTIONED_BY, RELATED_MAX
from cairn.spans import Span
from cairn.store import Page, Project
from cairn.store.audit import UNKNOWN_WRITER
from cairn.store.models import shown_metrics
from cairn.web.auth import _memory
from cairn.web.components import numbered_lines
from cairn.web.rest import _whole_number
from cairn.web.sharing import words_for
from cairn.web.ui.common import _ui

UI_HITS = 30
WEB_PAGES_AT_ONCE = 200  # Q1: the pages a project's page lists at a time, by slug
WEB_NOTES_AT_ONCE = 50  # UI and UX 8: the notes a project's list of notes shows at a time
RELATED_SHOWN = 8  # the related pages a page's related pages show unless `limit` asks
# A shared term's kind (cairn.relations.TERM_KINDS), as the related page says it.
TERM_WORDS = {
    "url": "URL",
    "path": "path",
    "flag": "flag",
    "version": "version",
    "inline_code": "code",
}


def _lines(span: Span) -> str:
    if span.start_line == span.end_line:
        return f"line {span.start_line}"
    return f"lines {span.start_line}\N{EN DASH}{span.end_line}"


# What the gates (cairn.gate) and the arranger record about a version, in words, by the name
# callers meet (cairn.store.models.SHOWN_METRICS); a name without one here shows as it is.
# The rows are shown in this order whatever order a version stored them in, the counts above
# the ratios made of them; a name without words comes after these, by name (page_view).
CHECKS = {
    "claims": "Claims",
    "passages_cited": "Source passages cited",
    "retention": "Facts of the sources kept",
    "code_line_retention": "Code lines of the sources kept",
    "passage_coverage": "Source passages accounted for",
    "omission_rate": "Facts marked as left out",
    "arranged_by": "Arranged by",
}
RATIO_METRICS = frozenset({"retention", "code_line_retention", "passage_coverage", "omission_rate"})


def _metric(value: Any, *, ratio: bool = False) -> str:
    """A check's value as the page shows it. A ratio is a percentage only when it is a number
    from 0 to 1, where times 100 cannot overflow; any other number is shown as the number it
    is, and anything else as its text. An archive brings whole numbers of any size and any
    finite float under any name (portable._metrics), so nothing here turns a whole number
    into a float, and a float is written the short way that reads back as itself."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if ratio and 0 <= value <= 1:
        return f"{abs(value) * 100:,.1f}".rstrip("0").rstrip(".") + "%"  # abs: -0.0 is 0%
    return repr(value)


def writer_words(kind: str, name: str | None) -> str:
    """Who wrote a note, in words, with the kind as a word (A1: the page's sentence under
    each source). A person by name (never an address: the store's writer is already
    audit.person_name's), a key and an agent by label, a source by name ("from a source"
    when the name did not travel, as after an import); the operator's command line in words
    of its own; a note stored before writers were recorded: "writer not recorded"."""
    if kind == "person":
        return f"written by {name}" if name else "written by a person"
    if kind == "key":
        return f"written by the key {name}"
    if kind == "agent":
        return f"written by the agent {name}"
    if kind == "source":
        return f"from the source {name}" if name else "from a source"
    if kind == "operator":
        return "added on the server's command line"
    return "writer not recorded"


def search_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """recall's hits as the search page shows them: each with `filed`, who filed each Slack
    message it holds and that its words are its author's (8.5), as the page view says it."""
    return [
        {
            **hit,
            "filed": [
                f"{writer_words(entry['kind'], entry['name'])}: {entry['filed_message']}"
                for entry in hit.get("written_by", ())
                if entry.get("filed_message")
            ],
        }
        for hit in hits
    ]


def page_view(memory: Memory, project: Project, page: Page) -> dict[str, Any]:
    """The claims in the body's order, each under the section headings the body gives it
    (compiler.section_headings) and linked to numbered sources; then every cited source,
    shown only when it is a span of `project`, with the numbers of the claims that cite it,
    who wrote its note (writer_words), whether that note is outside text and, for a Slack
    message someone filed, that its words are its author's (`filed`: SLACK_FILED), by the
    rule the agent tools use (A1, 8.5: memory.is_outside on Store.note_origins); then the
    checks, in words (CHECKS)."""
    span_ids = list(dict.fromkeys(i for claim in page.claims for i in claim.span_ids))
    found = memory.sources(project, span_ids)
    origins = memory.store.note_origins(note.id for _, note in found.values())
    number = {span_id: n for n, span_id in enumerate(span_ids, 1)}
    cited_by: dict[str, list[tuple[str, int]]] = {span_id: [] for span_id in span_ids}
    top: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    previous: tuple[str, ...] = ()
    for n, claim in enumerate(page.claims, 1):
        cites = list(dict.fromkeys(claim.span_ids))
        for span_id in cites:
            cited_by[span_id].append((claim.id, n))
        first = next((found[i][0] for i in cites if i in found), None)
        # compiler.render_body's rule (section_headings): the claims with no section first,
        # each other under every heading of its path; `n` stays the claim's stored place.
        path = first.heading_path if first else ()
        section, headings = section_headings(page.title, path, previous)
        if section:
            previous = section
        (rest if section else top).append(
            {
                "claim": claim,
                "kind": first.kind if first else "paragraph",
                "headings": headings,
                "cites": [(span_id, number[span_id]) for span_id in cites],
            }
        )
    items = [*top, *rest]
    sources = []
    for span_id in span_ids:
        span, note = found.get(span_id, (None, None))
        origin = origins.get(note.id) if note else None
        kind, name, via = origin[:3] if origin else (UNKNOWN_WRITER, None, note and note.via)
        filed = origin is not None and origin.filed_message
        sources.append(
            {
                "writer": writer_words(kind, name),
                "outside": note is not None and is_outside(kind, via, filed_message=filed),
                "filed": SLACK_FILED if filed else None,
                "id": span_id,
                "n": number[span_id],
                "span": span,
                "note": note,
                "lines": _lines(span) if span else "",
                "numbered": numbered_lines(span, note) if span else [],
                "cited": span.end_line - span.start_line + 1 if span else 0,
                "cited_by": cited_by[span_id],
            }
        )
    by_id = {source["id"]: source for source in sources}
    for item in items:  # each claim's proof: its own sources, in the order it cites them
        item["proof"] = [by_id[span_id] for span_id, _ in item["cites"]]
        item["proof_lines"] = sum(source["cited"] for source in item["proof"])
    shown = shown_metrics(page.metrics)
    metrics = [
        (name, CHECKS.get(name, name), _metric(shown[name], ratio=name in RATIO_METRICS))
        for name in (*(n for n in CHECKS if n in shown), *sorted(shown.keys() - CHECKS.keys()))
    ]
    return {
        "items": items,
        # The Markdown body as REST and MCP give it: rendered when read, never the stored
        # text, and compiler.BODY_NOT_SHOWN where a fence of it would run across claims.
        "body": body_to_read(page.title, page.claims, {i: s for i, (s, _) in found.items()})[0],
        "sources": sources,
        "metrics": metrics,
        "lines_cited": sum(source["cited"] for source in sources),
        "notes": len({note.id for _, note in found.values()}),
    }


def related_href(
    project: str, slug: str, *, limit: int | None = None, entry: str | None = None
) -> str:
    """The address of the related pages of a page: up to `limit` of them when given, and at
    the entry of the related page `entry` (related_entry) when given."""
    href = "/b/" + quote(project, safe="") + "/p/" + quote(slug, safe="") + "/related"
    if limit is not None:
        href += f"?limit={limit}"
    return href if entry is None else href + "#" + quote(related_entry(entry), safe="")


def related_entry(slug: str) -> str:
    """The id of a related page's entry on the related pages of another."""
    return "related-" + slug


def related_left_out(searched: Mapping[str, Any]) -> str | None:
    """One sentence for what a read of related pages left out, from the counts its answer
    gives under `searched` (cairn.memory.related_searched) and nothing else: the page's
    terms that were not looked up, the claims of other pages that were not read, the related
    pages past the limit. None for an answer that is complete."""
    if searched["complete"]:
        return None
    parts = []
    if terms := searched["terms_not_looked_up"]:
        every = terms + searched["terms_looked_up"]
        was = "was" if terms == 1 else "were"
        parts.append(f"{terms:,} of the page's {every:,} terms {was} not looked up")
    if claims := searched["claims_not_read"]:
        were = "claim of other pages that may state one of its terms was"
        if claims != 1:
            were = "claims of other pages that may state one of its terms were"
        parts.append(f"{claims:,} {were} not read")
    if pages := searched["pages_left_out"]:
        are = "page is" if pages == 1 else "pages are"
        parts.append(f"{pages:,} more related {are} not shown")
    if not parts:
        return "This is not all of it."
    said = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"This is not all of it: {said}."


def _quoted(claim: Mapping[str, Any], mention: bool) -> dict[str, Any]:
    """A claim a relation rests on, as the related page quotes it: its text as it is, never
    drawn as Markdown; whether it is cut; the line given beside a claim cut, wherever the
    answer holds one; and who wrote the notes it cites, each said once in the page view's
    words (writer_words; a Slack message someone filed as search_hits says it), how many
    notes that is, when the newest was written and whether any is outside text."""
    entries = claim["written_by"]
    more = claim.get("written_by_more", 0)
    writers = dict.fromkeys(
        writer_words(entry["kind"], entry["name"])
        + (f": {entry['filed_message']}" if entry.get("filed_message") else "")
        for entry in entries
    )
    line = claim.get("line")
    holds = "names the title" if mention else "states the term"
    in_part = ", in part" if claim.get("line_cut") else ""
    return {
        "id": claim["claim_id"],
        "text": claim["text"],
        "cut": bool(claim.get("text_cut")),
        "line": line,
        "line_cut": bool(claim.get("line_cut")),
        "line_says": f"The line of its passage that {holds}{in_part}:",
        "writers": list(writers),
        "notes": len(entries) + more,
        "notes_more": more,
        "written_at": claim["written_at"],
        "outside": claim["outside"],
    }


def related_view(answer: Mapping[str, Any], limit: int) -> dict[str, Any]:
    """What `Memory.related` answered, as the related page shows it, and nothing it did not
    answer: the related pages in its order, each reason with its claims (_quoted; this
    page's own only for a shared term), the count of the reasons it does not list, the
    sentence of what the read left out (related_left_out) and, when it left pages out and
    `limit` asked for fewer than an answer can hold, the address that shows the most."""
    project, slug = answer["project"], answer["page"]
    related = []
    for page in answer["related"]:
        reasons = []
        for why in page["why"]:
            mention = why["kind"] == MENTIONED_BY
            reasons.append(
                {
                    "kind": why["kind"],
                    "term": why["term"],
                    "term_kind": why["term_kind"],
                    "term_word": TERM_WORDS.get(why["term_kind"], "term"),
                    "pages": why.get("pages"),
                    "here": _quoted(why["here"], mention) if "here" in why else None,
                    "there": _quoted(why["there"], mention),
                }
            )
        related.append(
            {
                "slug": page["page"],
                "title": page["title"],
                "entry": related_entry(page["page"]),
                "reasons": reasons,
                "more": page["more"],
            }
        )
    left = answer["searched"]["pages_left_out"]
    return {
        "project": project,
        "slug": slug,
        "title": answer["title"],
        "version": answer["version"],
        "related": related,
        "left_out": related_left_out(answer["searched"]),
        "more_href": related_href(project, slug, limit=RELATED_MAX)
        if left and limit < RELATED_MAX
        else None,
        "most": RELATED_MAX,
    }


def _sees_rule(memory: Memory, project: Project) -> bool:
    """Whether /b/<project>/access opens for the caller: the question that page asks the store
    (Store.project_access, PermissionError for whoever is neither the maker, nor an owner, nor
    at "manage" on the project)."""
    try:
        memory.store.project_access(project.id)
    except (PermissionError, KeyError):
        return False
    return True


def memory_handlers() -> tuple[Callable[..., Any], ...]:
    """The handlers of the pages that show memory, as ui_routes registers them."""

    def home(request: Request) -> Response:
        """The projects, each with its page count and how far it is shared, and the offer to
        write a note when the caller can write to one: a fixed number of reads whatever the
        number of projects (Q1)."""
        memory = _memory(request)
        projects = memory.projects()["projects"]
        sharing = memory.store.projects_sharing(project["id"] for project in projects)
        me = memory.store.user_id
        shared = {project["id"]: words_for(sharing.get(project["id"]), me) for project in projects}
        blank = {"name": "", "description": "", "kind": GENERAL}
        return _ui(
            request,
            "home.html",
            projects=projects,
            shared=shared,
            can_write=bool(memory.store.writable_projects()),
            project_form=blank,
            project_error=None,
        )

    def project(request: Request) -> Response:
        """A project's pages, WEB_PAGES_AT_ONCE at a time by slug (Q1): the part after the slug
        `after`, how many pages there are in all, and a link to the next part."""
        memory = _memory(request)
        target = memory.project(request.path_params["project"])
        after = request.query_params.get("after") or None
        part = memory.pages_part(target, None, after, WEB_PAGES_AT_ONCE)
        try:
            shared = memory.store.project_sharing(target.id)
        except KeyError:
            shared = None
        rule = _sees_rule(memory, target)
        can_write = (
            rule
            or (shared is not None and shared.everyone == "write")
            or any(p.id == target.id for p in memory.store.writable_projects())
        )
        return _ui(
            request,
            "project.html",
            project=target,
            notes=memory.store.count_notes(target.id),
            pages=part["pages"],
            pages_total=part.get("pages_total", len(part["pages"])),
            pages_next=part.get("pages_next"),
            after=after,
            rule=rule,
            erase=memory.may_erase(target),
            can_write=can_write,
            sharing=words_for(shared, memory.store.user_id),
            # M4, Privacy 4: "allowed", "never" or None (nobody decided), in one read;
            # whoever sees `rule` may change it
            models=memory.store.models_choices([target.id]).get(target.id),
        )

    def notes(request: Request) -> Response:
        """A project's notes, WEB_NOTES_AT_ONCE at a time, newest first (Store.notes_part:
        never their text): the part before the note `before`, and a link to the older ones.
        Each links the page it is filed under, or says it waits for one, and, for whoever
        may forget notes, the page that forgets it."""
        memory = _memory(request)
        before = request.query_params.get("before") or None
        target, heads, older = memory.notes_part(
            request.path_params["project"], WEB_NOTES_AT_ONCE, before
        )
        return _ui(
            request,
            "notes.html",
            project=target,
            notes=[(head, writer_words(head.writer, head.writer_name)) for head in heads],
            older=older,
            before=before,
            erase=memory.may_erase(target),
        )

    def page(request: Request) -> Response:
        """A page, with the latest VERSIONS_AT_ONCE of its version numbers, or those below
        `before` (Q1), how many it keeps in all, and a link to the earlier ones. When older
        versions were removed (its kept versions are fewer than its latest number), it says
        it shows the last N and why."""
        memory = _memory(request)
        target = memory.project(request.path_params["project"])
        query = request.query_params  # a web page: what it does not know it ignores (X1)
        version = _whole_number(query, "version")
        before = _whole_number(query, "before")
        shown = memory.find_page(target, request.path_params["slug"], version)
        versions, total, latest, following = memory.versions_of(target, shown.slug, None, before)
        latest = latest or shown.version
        return _ui(
            request,
            "page.html",
            project=target,
            page=shown,
            versions=versions,
            versions_total=total,
            versions_next=following,
            before=before,
            trimmed=total < latest,
            latest=latest,
            erase=memory.may_erase(target),  # each source's note links to its forget page
            **page_view(memory, target, shown),
        )

    def related(request: Request) -> Response:
        """The pages related to a page's latest version, as `Memory.related` answers them
        (related_view): RELATED_SHOWN of them unless `limit` asks, a whole number of 1 to
        RELATED_MAX, any other refused in REST's sentence. A project out of reach and a
        page that is not there are answered as the page view answers them."""
        memory = _memory(request)
        ref, slug = request.path_params["project"], request.path_params["slug"]
        # a web page: what it does not know it ignores (X1); its `limit` it reads as REST does
        limit = _whole_number(request.query_params, "limit", RELATED_RANGE)
        limit = RELATED_SHOWN if limit is None else limit
        try:
            found = memory.related(ref, slug, limit)
        except NotFound:
            memory.find_page(memory.project(ref), slug)  # refuses in the page view's words
            raise  # a page filed since: the first answer stands
        return _ui(request, "related.html", **related_view(found, limit))

    def search(request: Request) -> Response:
        memory = _memory(request)
        query = request.query_params.get("q", "").strip()
        ref = request.query_params.get("project", "")
        project = memory.project(ref) if ref else None  # resolve once; search by id, not by name
        scope = project.name if project else ""
        found = memory.recall(query, project.id if project else None, UI_HITS) if query else {}
        hits = search_hits(found.get("hits", []))
        projects = memory.store.list_projects()
        return _ui(
            request,
            "search.html",
            query=query,
            scope=scope,
            projects=projects,
            hits=hits,
            searched=found.get("searched"),  # Q2: what the search left out, as recall says it
            most=UI_HITS,  # at most this many are asked for, so the page cannot count past it
        )

    return home, project, notes, page, related, search
