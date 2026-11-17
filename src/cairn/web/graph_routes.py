"""A project's graph (cairn.graph.project_graph), registered by cairn.web.app beside the REST
routes and the project pages, behind the same admission as every other read:
GET /api/graph?project=<name or id>[&limit=N] takes an API key, never an agent's token (Auth),
and GET /b/<project>/graph a signed-in browser. `limit` is the number of pages, 1 to 20 (20
when left out); anything else is 400. /api/graph refuses a query parameter it does not take
(X1); the page ignores one, as every web page does. A project out of the caller's reach is
answered as one that does not exist (404); a reach larger than cairn.graph's bounds is 503,
the same for every project. Each request is one access-log record named
`graph`. No MCP tool draws the graph."""

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from cairn.graph import GraphUnavailable, project_graph
from cairn.memory import BadRequest, NotFound, Unavailable
from cairn.web.auth import _memory
from cairn.web.graph_view import page_limit, render_graph
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


def graph_routes() -> list[Route]:
    """GET /api/graph and GET /b/<project>/graph, each logged as `graph`."""

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

    return [
        Route("/api/graph", _logged("graph", api)),
        Route("/b/{project}/graph", _logged("graph", page)),
    ]
