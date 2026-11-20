"""The pages that show memory: the projects, a project's pages and notes, a page with its
claims and their passages (page_view), and search."""

from collections.abc import Callable
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from cairn.compiler import body_to_read, section_headings
from cairn.kinds import GENERAL
from cairn.memory import SLACK_FILED, Memory, is_outside
from cairn.spans import Span
from cairn.store import Page, Project
from cairn.store.audit import UNKNOWN_WRITER
from cairn.store.models import shown_metrics
from cairn.web.auth import _memory
from cairn.web.components import numbered_lines
from cairn.web.rest import _whole_number
from cairn.web.sharing import sharing_words, words_for
from cairn.web.ui.common import _ui

UI_HITS = 30
WEB_PAGES_AT_ONCE = 200  # Q1: the pages a project's page lists at a time, by slug
WEB_NOTES_AT_ONCE = 50  # UI and UX 8: the notes a project's list of notes shows at a time


def _lines(span: Span) -> str:
    if span.start_line == span.end_line:
        return f"line {span.start_line}"
    return f"lines {span.start_line}\N{EN DASH}{span.end_line}"


# What the gates (cairn.gate) and the arranger record about a version, in words, by the name
# callers meet (cairn.store.models.SHOWN_METRICS); a name without one here shows as it is.
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
    if ratio and isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value * 100:,.1f}".rstrip("0").rstrip(".") + "%"
    if isinstance(value, float):
        return f"{value:,.3f}".rstrip("0").rstrip(".")
    return str(value)


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
    metrics = [
        (name, CHECKS.get(name, name), _metric(value, ratio=name in RATIO_METRICS))
        for name, value in shown_metrics(page.metrics).items()
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
        """The projects, each with its page count and how far it is shared: a fixed number of
        reads whatever the number of projects (Q1)."""
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
        return _ui(
            request,
            "project.html",
            project=target,
            notes=memory.store.count_notes(target.id),
            pages=part["pages"],
            pages_total=part.get("pages_total", len(part["pages"])),
            pages_next=part.get("pages_next"),
            after=after,
            rule=_sees_rule(memory, target),
            erase=memory.may_erase(target),
            sharing=sharing_words(memory, target.id),
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

    return home, project, notes, page, search
