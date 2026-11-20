"""A project's graph (cairn.graph.project_graph), registered by cairn.web.app beside the REST
routes and the project pages, behind the same admission as every other read:
GET /api/graph?project=<name or id>[&limit=N] takes an API key, never an agent's token (Auth),
and GET /b/<project>/graph a signed-in browser. `limit` is the number of pages, 1 to 20 (20
when left out); anything else is 400. /api/graph refuses a query parameter it does not take
(X1); the page ignores one, as every web page does. A project out of the caller's reach is
answered as one that does not exist (404); a reach larger than cairn.graph's bounds is 503,
the same for every project. Each request is one access-log record named
`graph`. No MCP tool draws the graph.

With the explorer on (G2, `serve`'s default unless `--no-graph-explorer`;
graph_routes(explorer=True)) /api/graph is the same route and code as above, and GET
(cairn.graph.graph_neighbourhood, drawn by cairn.web.graph_view.render_explorer): `focus`
is the view, "project" (its pages, the default), "notes", "page:<slug>" (a page's claims) or
a claim's own focus (its passages and their notes); `q` keeps what matches in the batch;
`limit` is the batch, 1 to 20 (20 when left out; anything else is 400); `after` is the
continuation "Next batch" links. Any other parameter is ignored. GET
/b/<project>/graph/notes/<id> is a note's page, its text read a part at a time (`after`).
A project out of reach is answered as one that does not exist, on every view, continuation
and note page: 404, with the asked name and nothing read from the project; a note that is not
the project's is 404 as an address that is not a page. A stale continuation is 409 and one
that cannot be read is 400, each with a page that links the graph's start. Every other request
is one access-log record named `graph`, and the explorer's script and stylesheet are served
(graph_view.explorer_files). With the explorer off neither the note route nor the files
exist: each is 404 as an address that is not a page.

One view of the explorer is not a batch of the graph (KG2): `focus` "related:<slug>" is a
page and its related pages, what `Memory.related` answers for the page and nothing else
(cairn.web.graph_view.related_graph): the page, at most `limit` related pages in the
answer's order (1 to 20, 20 when left out; anything else is the 400 the other views give),
and from the page to each of them an edge that says the first reason (it shares a term with
the other, or is named by it) and counts the others, linked to that page's entry on GET
/b/<project>/p/<slug>/related for the same limit, where the claims are. No claim's text is
drawn. Each related page's node opens its own related view, and every page node of the
other views links its page's ("Related pages"). `q` and `after` are ignored: the view is one
answer, and what its read left out is said in the sentence the related pages say it in.
With no related page the view says so, and of an answer that is not complete only that none
was found in what was looked at.
It is two reads, neither inside the other: the reach check the other views make
(cairn.graph.project_in_reach), then `Memory.related`, which rechecks the reach in the
snapshot it reads the pages in. So a project out of reach is the 404 of one that does not
exist, a reach past the graph's bounds is 503 with nothing read, and a page that is not
there is the 404 the view of a page's claims gives. The request is one access-log record
named `related`, not `graph`, and with reads audited one read.page event of that page, as
GET /b/<project>/p/<slug>/related is. The view of a project's pages draws no relation among
them and says so: working them out for a batch is one read of `related` for each page.
/api/graph and the agent's resource hold no relation. With the explorer off the view does
not exist: the graph page ignores `focus`."""

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from cairn.graph import (
    PAGE_LIMIT,
    GraphStale,
    GraphUnavailable,
    PageMissing,
    graph_neighbourhood,
    graph_note_detail,
    project_graph,
    project_in_reach,
)
from cairn.memory import BadRequest, NotFound, Unavailable
from cairn.web.auth import _memory
from cairn.web.graph_view import (
    RELATED_FOCUS,
    explorer_files,
    page_limit,
    page_query,
    related_graph,
    render_explorer,
    render_graph,
    render_note,
    render_restart,
)
from cairn.web.rest import _logged, _query, _whole_number
from cairn.web.templating import brand_of


def _graph(request: Request, project: str, limit: int | None) -> dict:
    if not project:
        raise BadRequest("'project' is required")
    memory = _memory(request)
    try:
        graph = project_graph(memory.store, project, 20 if limit is None else limit)
    except KeyError:
        raise NotFound(f"no project {project!r}") from None
    except GraphUnavailable:
        raise Unavailable("The graph is unavailable for this connection.") from None
    except ValueError as error:
        raise BadRequest(str(error)) from None
    # The project this request touched, for its access-log record. Memory's own reads set it
    # in Memory.project; that would look the project up a second time, outside the snapshot
    # project_graph read it in, so the graph sets it here, once it is read.
    memory._touched = graph["nodes"][0]["label"]
    return graph


UNAVAILABLE = "The graph is unavailable for this connection."
FOCUS_MAX = 512  # the characters of a focus, as cairn.graph.graph_neighbourhood bounds one
STALE = "The page changed while you were exploring it."
NOTE_STALE = "The note changed while you were reading it."
UNREADABLE = "This continuation cannot be read, or was made before the server restarted."


def _restart(request: Request, project: str, message: str, status: int) -> Response:
    return render_restart(
        project, message, status, brand=brand_of(request), person=_memory(request).person
    )


def graph_routes(explorer: bool = False) -> list[Route]:
    """GET /api/graph and GET /b/<project>/graph, each logged as `graph`; with `explorer`,
    the graph page is the explorer, and its note pages and its two files are added."""

    def api(request: Request) -> Response:
        query = _query(request, "project", "limit")
        graph = _graph(request, query.get("project", ""), _whole_number(query, "limit"))
        return JSONResponse(graph, headers={"cache-control": "no-store"})

    def page(request: Request) -> Response:
        graph = _graph(request, request.path_params["project"], page_limit(request))
        memory = _memory(request)
        return render_graph(
            graph["nodes"][0]["label"], graph, brand=brand_of(request), person=memory.person
        )

    def explore(request: Request) -> Response:
        project = request.path_params["project"]
        memory = _memory(request)
        query = page_query(request)  # a web page: what it does not take, it ignores
        limit = page_limit(request)
        search = query.get("q", "")
        try:
            graph = graph_neighbourhood(
                memory.store,
                project,
                focus=query.get("focus") or "project",
                after=query.get("after"),
                limit=20 if limit is None else limit,
                search=search,
            )
        except PageMissing as missing:
            raise NotFound(f"no page {missing.args[0]!r} in project {project!r}") from None
        except KeyError:
            raise NotFound(f"no project {project!r}") from None
        except GraphUnavailable:
            raise Unavailable("The graph is unavailable for this connection.") from None
        except GraphStale:
            return _restart(request, project, STALE, 409)
        except ValueError as error:
            if "after" in query:
                return _restart(request, project, UNREADABLE, 400)
            raise BadRequest(str(error)) from None
        name = graph["nodes"][0]["label"]
        memory._touched = name  # for the access-log record, as _graph does
        return render_explorer(
            name, graph, brand=brand_of(request), person=memory.person, limit=limit, search=search
        )

    def related(request: Request) -> Response:
        """The view of a page and its related pages: the reach check the other views make
        (cairn.graph.project_in_reach), then one read of `Memory.related`, at most `limit`
        pages (the batch: 1 to 20, 20 when left out), drawn by related_graph. A limit, a
        project out of reach and a page that is gone are answered as on the other views."""
        project = request.path_params["project"]
        memory = _memory(request)
        focus = page_query(request).get("focus", "")
        limit = page_limit(request)
        if limit is not None and not 1 <= limit <= PAGE_LIMIT:
            raise BadRequest(f"limit must be a whole number from 1 to {PAGE_LIMIT}")
        if len(focus) > FOCUS_MAX:
            raise BadRequest("invalid graph focus")
        slug = focus[len(RELATED_FOCUS) :]

        def reached() -> tuple[str, str]:
            try:
                return project_in_reach(memory.store, project)
            except KeyError:
                raise NotFound(f"no project {project!r}") from None
            except GraphUnavailable:
                raise Unavailable(UNAVAILABLE) from None

        project_id, name = reached()
        try:
            answer = memory.related(project_id, slug, PAGE_LIMIT if limit is None else limit)
        except NotFound:
            reached()  # out of reach since the check: said as a project that does not exist
            raise NotFound(f"no page {slug!r} in project {project!r}") from None
        return render_explorer(
            name, related_graph(answer), brand=brand_of(request), person=memory.person, limit=limit
        )

    def note(request: Request) -> Response:
        project = request.path_params["project"]
        memory = _memory(request)
        after = page_query(request).get("after")
        try:
            detail = graph_note_detail(
                memory.store, project, request.path_params["note"], after=after
            )
        except KeyError:  # out of reach, missing, or not this project's: one answer
            raise NotFound("Not Found") from None
        except GraphUnavailable:
            raise Unavailable("The graph is unavailable for this connection.") from None
        except GraphStale:
            return _restart(request, project, NOTE_STALE, 409)
        except ValueError:
            if after is not None:
                return _restart(request, project, UNREADABLE, 400)
            raise NotFound("Not Found") from None
        memory._touched = detail["project"]
        memory._read = {"note": request.path_params["note"]}  # the audit event's (READ_EVENTS)
        return render_note(detail["project"], detail, brand=brand_of(request), person=memory.person)

    if not explorer:
        return [
            Route("/api/graph", _logged("graph", api)),
            Route("/b/{project}/graph", _logged("graph", page)),
        ]
    a_view, related_view = _logged("graph", explore), _logged("related", related)

    async def explorer_page(request: Request) -> Response:
        """The explorer's one address: a view of the graph, recorded as `graph`; or a page's
        related view, a read of `related` and recorded as one."""
        focus = page_query(request).get("focus") or ""
        return await (related_view if focus.startswith(RELATED_FOCUS) else a_view)(request)

    return [
        Route("/api/graph", _logged("graph", api)),
        Route("/b/{project}/graph", explorer_page),
        Route("/b/{project}/graph/notes/{note}", _logged("graph", note)),
        *explorer_files(),
    ]
