"""Render a graph without client code; its list is the keyboard-accessible equivalent."""

from typing import Any
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import HTMLResponse

from cairn.web.rest import _whole_number
from cairn.web.templating import _page

_COLUMNS = {"project": 0, "page": 1, "claim": 2, "passage": 3, "note": 4}


def page_limit(request: Request) -> int | None:
    """The page's `limit`, None when left out. A web page: what it does not know it ignores
    (X1), as cairn.web.ui's pages do; so it reads its query here, outside the module of
    /api/graph, where only cairn.web.rest's helpers read one."""
    return _whole_number(request.query_params, "limit")


def render_graph(project: str, graph: dict[str, Any], *, brand: Any, person: Any) -> HTMLResponse:
    """Render the exact nodes and edges of a projection, with only same-server page links."""
    rows = [0] * 5
    positions = {}
    by_id = {}
    nodes = []
    for number, item in enumerate(graph["nodes"]):
        column = _COLUMNS[item["kind"]]
        rows[column] += 1
        x, y = 130 + column * 260, 55 + (rows[column] - 1) * 80
        positions[item["id"]] = (x, y)
        href = None
        if item["kind"] in {"page", "claim"}:
            href = "/b/" + quote(project, safe="") + "/p/" + quote(item["slug"], safe="")
        label = " ".join(f"{item['kind']}: {item['label']}".split())
        display = label if len(label) <= 24 else label[:23] + "…"
        prepared = {
            **item,
            "x": x,
            "y": y,
            "href": href,
            "anchor": f"graph-node-{number}",
            "display": display,
        }
        nodes.append(prepared)
        by_id[item["id"]] = prepared
    edges = []
    for number, item in enumerate(graph["edges"]):
        x1, y1 = positions[item["from"]]
        x2, y2 = positions[item["to"]]
        edges.append(
            {
                **item,
                "number": number,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "source": by_id[item["from"]],
                "target": by_id[item["to"]],
            }
        )
    return _page(
        "graph.html",
        brand=brand,
        person=person,
        signed_in=True,
        project=project,
        nodes=nodes,
        edges=edges,
        truncated=graph["truncated"],
        height=max(150, max(rows, default=0) * 80 + 40),
    )
