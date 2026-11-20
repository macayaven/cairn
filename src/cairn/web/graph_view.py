"""Render a graph without client code; its list is the keyboard-accessible equivalent.

With the explorer on (G2, `serve`'s default unless `--no-graph-explorer`), render_explorer
draws and lists the graph the same way, one view and one batch at a time, and its page is the
loads a script: GRAPH_SCRIPT, the file src/cairn/static/graph-view.js, by a tag that carries
the SHA-256 of the file's bytes as `integrity` and a nonce made for that response with
`secrets`; that response's policy adds `script-src 'nonce-...'` and nothing else, and no
other response's policy changes. The script only lays out, moves and zooms the drawing:
without it every node, link and continuation is in the markup, and the controls it needs
stay `hidden` until it shows them. The explorer's stylesheet, GRAPH_STYLESHEET, is linked by
the explorer's pages alone. Neither file names the product, so both are served, by their
exact names, under a brand too (explorer_files). With the explorer off neither is served and
render_graph alone draws the graph page, as before."""

import base64
import hashlib
import secrets
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

from cairn.web.rest import _whole_number
from cairn.web.templating import HERE, UI_HEADERS, _page

_COLUMNS = {"project": 0, "page": 1, "claim": 2, "passage": 3, "note": 4}
# What one batch of each view lists: the project's pages, its notes, a page's claims, a
# claim's passages (with the notes they come from).
_VIEW_ITEM = {"project": "page", "notes": "note", "page": "claim", "claim": "passage"}
GRAPH_SCRIPT = "graph-view.js"  # in src/cairn/static/, served only with the explorer on
GRAPH_STYLESHEET = "graph-view.css"  # the same
_SCRIPT_BYTES = (HERE / "static" / GRAPH_SCRIPT).read_bytes()  # read once: what is hashed
_SHEET_BYTES = (HERE / "static" / GRAPH_STYLESHEET).read_bytes()  # is what is served
SCRIPT_INTEGRITY = "sha256-" + base64.b64encode(hashlib.sha256(_SCRIPT_BYTES).digest()).decode()
SCRIPT_ADDRESS = "/static/" + GRAPH_SCRIPT
# The stylesheet's address changes with its content, as the main stylesheet's does.
STYLESHEET_ADDRESS = f"/static/{GRAPH_STYLESHEET}?v={hashlib.sha256(_SHEET_BYTES).hexdigest()[:12]}"
NONCE_BYTES = 18  # 144 bits from `secrets`, a new one for every explorer page


def page_limit(request: Request) -> int | None:
    """The page's `limit`, None when left out. A web page: what it does not know it ignores
    (X1), as cairn.web.ui's pages do; so it reads its query here, outside the module of
    /api/graph, where only cairn.web.rest's helpers read one."""
    return _whole_number(request.query_params, "limit")


def page_query(request: Request) -> Mapping[str, str]:
    """The explorer's query (`focus`, `after`, `q`), read here for the reason page_limit is:
    a web page ignores what it does not take, and in the module of /api/graph only
    cairn.web.rest's helpers read a query."""
    return request.query_params


def render_graph(project: str, graph: dict[str, Any], *, brand: Any, person: Any) -> HTMLResponse:
    """Render the exact nodes and edges of a projection, with only same-server page links."""
    box_w, box_h = 216, 32
    col_step, margin_x = 280, 24
    width = margin_x * 2 + 4 * col_step + box_w  # 5 columns fit in 1384 with symmetric margins
    col_by_id = {item["id"]: _COLUMNS[item["kind"]] for item in graph["nodes"]}
    skip_total = sum(
        1 for item in graph["edges"] if abs(col_by_id[item["to"]] - col_by_id[item["from"]]) > 1
    )
    top_pad = 32 if skip_total == 0 else 24 + min(skip_total, 20) * 6 + 12

    rows = [0] * 5
    positions = {}
    by_id = {}
    nodes = []
    for number, item in enumerate(graph["nodes"]):
        column = _COLUMNS[item["kind"]]
        rows[column] += 1
        box_x = margin_x + column * col_step
        box_y = top_pad + (rows[column] - 1) * 72
        x, y = box_x + 12, box_y + box_h // 2
        positions[item["id"]] = (x, y)
        href = None
        if item["kind"] in {"page", "claim"}:
            href = "/b/" + quote(project, safe="") + "/p/" + quote(item["slug"], safe="")
        label = " ".join(f"{item['kind']}: {item['label']}".split())
        display = label if len(label) <= 24 else label[:23] + "…"
        prepared = {
            **item,
            "column": column,
            "x": x,
            "y": y,
            "box_x": box_x,
            "box_y": box_y,
            "box_w": box_w,
            "box_h": box_h,
            "label_x": box_x + 22,
            "label_y": box_y + 4,
            "label_w": 186,
            "href": href,
            "anchor": f"graph-node-{number}",
            "display": display,
        }
        nodes.append(prepared)
        by_id[item["id"]] = prepared

    edges = []
    skip_idx = 0
    for number, item in enumerate(graph["edges"]):
        source, target = by_id[item["from"]], by_id[item["to"]]
        c1, c2 = source["column"], target["column"]
        if c1 == c2:
            x1, y1 = source["box_x"] + box_w, source["y"]
            x2, y2 = target["box_x"] + box_w, target["y"]
            gutter = x1 + 16
            path = f"M {x1} {y1} L {gutter} {y1} L {gutter} {y2} L {x2} {y2}"
        elif abs(c2 - c1) == 1:
            if c1 < c2:
                x1, y1 = source["box_x"] + box_w, source["y"]
                x2, y2 = target["box_x"], target["y"]
            else:
                x1, y1 = source["box_x"], source["y"]
                x2, y2 = target["box_x"] + box_w, target["y"]
            path = f"M {x1} {y1} L {x2} {y2}"
        else:
            lane_y = 14 + (skip_idx % 20) * 6
            slot = 14 + (skip_idx % 5) * 6
            skip_idx += 1
            if c1 < c2:
                x1, y1 = source["box_x"] + box_w, source["y"]
                x2, y2 = target["box_x"], target["y"]
                x_exit, x_enter = x1 + 14, x2 - slot
            else:
                x1, y1 = source["box_x"], source["y"]
                x2, y2 = target["box_x"] + box_w, target["y"]
                x_exit, x_enter = x1 - 14, x2 + slot
            path = (
                f"M {x1} {y1} L {x_exit} {y1} L {x_exit} {lane_y}"
                f" L {x_enter} {lane_y} L {x_enter} {y2} L {x2} {y2}"
            )
        edges.append(
            {
                **item,
                "number": number,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "path": path,
                "source": source,
                "target": target,
            }
        )
    height = max(180, top_pad + max(rows, default=0) * 72 + 24)
    return _page(
        "graph.html",
        brand=brand,
        person=person,
        signed_in=True,
        project=project,
        nodes=nodes,
        edges=edges,
        truncated=graph["truncated"],
        width=width,
        height=height,
    )


# The explorer (G2)


def graph_href(
    project: str,
    *,
    focus: str = "",
    after: str | None = None,
    search: str = "",
    limit: int | None = None,
) -> str:
    """The address of one view of the explorer: `focus` ("project", the default, when empty),
    a continuation `after`, the words `search` looks for and the batch's `limit`."""
    path = "/b/" + quote(project, safe="") + "/graph"
    query = {
        key: value
        for key, value in (("focus", focus), ("after", after), ("q", search), ("limit", limit))
        if value not in (None, "")
    }
    return path + "?" + urlencode(query) if query else path


def note_href(project: str, note_id: str) -> str:
    """The explorer's page of one note of `project`."""
    return "/b/" + quote(project, safe="") + "/graph/notes/" + quote(note_id, safe="")


def _with_script(response: HTMLResponse, nonce: str) -> HTMLResponse:
    """This response, and no other, may run the script whose tag carries `nonce`."""
    response.headers["content-security-policy"] = (
        UI_HEADERS["content-security-policy"] + f"; script-src 'nonce-{nonce}'"
    )
    return response


def render_explorer(
    project: str,
    graph: dict[str, Any],
    *,
    brand: Any,
    person: Any,
    limit: int | None = None,
    search: str = "",
) -> HTMLResponse:
    """One batch of one view (cairn.graph.graph_neighbourhood) as the explorer's page: the
    drawing, the same nodes and edges listed as text with a link to each node's own page and
    to the view it opens, the views, the search, the legend and "Next batch" while there is
    more. Links lead only to this server's pages."""
    focus = graph["focus"]
    rows = [0] * 5
    project_href = "/b/" + quote(project, safe="")
    items = {item["id"]: item for item in graph["nodes"]}
    passage_hrefs = {}
    for edge in graph["edges"]:  # a passage's place is on the page of a claim that cites it
        source, target = items.get(edge["from"], {}), items.get(edge["to"], {})
        if edge["kind"] == "cites" and target.get("kind") == "passage":
            passage_hrefs.setdefault(
                target["id"],
                f"{project_href}/p/{quote(source['slug'], safe='')}"
                f"#{quote(target['id'].removeprefix('passage:'), safe='')}",
            )
    positions = {}
    by_id = {}
    nodes = []
    for number, item in enumerate(graph["nodes"]):
        column = _COLUMNS[item["kind"]]
        rows[column] += 1
        x, y = 130 + column * 260, 55 + (rows[column] - 1) * 80
        positions[item["id"]] = (x, y)
        href = None
        if item["kind"] == "project":
            href = project_href
        elif item["kind"] in {"page", "claim"}:
            href = project_href + "/p/" + quote(item["slug"], safe="")
            if item["kind"] == "claim" and item.get("claim_id"):
                href += "#" + quote(item["claim_id"], safe="")
        elif item["kind"] == "passage":
            href = passage_hrefs.get(item["id"])
        elif item["kind"] == "note":
            href = note_href(project, item["note_id"])
        label = " ".join((item["label"] or item["kind"]).split())
        opens = item.get("focus")
        prepared = {
            **item,
            "x": x,
            "y": y,
            "z": column * 110,
            "href": href,
            "explore_href": graph_href(project, focus=opens, limit=limit)
            if opens and opens != focus
            else None,
            "anchor": f"graph-node-{number}",
            "display": label if len(label) <= 24 else label[:23] + "…",
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
    after = graph["next"]
    navigation = {
        "href": graph_href(project),
        "notes_href": graph_href(project, focus="notes", limit=limit),
        "pages_href": graph_href(project, limit=limit),
        "focus": focus,
        "item": _VIEW_ITEM[focus.partition(":")[0]],
        "limit": limit,
        "search": search,
        "next": graph_href(project, focus=focus, after=after, search=search, limit=limit)
        if after is not None
        else None,
    }
    nonce = base64.urlsafe_b64encode(secrets.token_bytes(NONCE_BYTES)).decode()
    response = _page(
        "graph_explorer.html",
        brand=brand,
        person=person,
        signed_in=True,
        project=project,
        nodes=nodes,
        edges=edges,
        truncated=graph["truncated"],
        navigation=navigation,
        counts=dict(zip(_COLUMNS, rows, strict=True)),
        height=max(150, max(rows, default=0) * 80 + 40),
        script=SCRIPT_ADDRESS,
        integrity=SCRIPT_INTEGRITY,
        nonce=nonce,
        explorer_stylesheet=STYLESHEET_ADDRESS,
    )
    return _with_script(response, nonce)


def render_note(project: str, note: dict[str, Any], *, brand: Any, person: Any) -> HTMLResponse:
    """A note's page in the explorer (cairn.graph.graph_note_detail): its title, `source_ref`
    (a link when it is an http or https address with a host), `via`, one part of its text
    and, while there is more, a link to the next part. It runs no script."""
    after = note["next"]
    return _page(
        "graph_note.html",
        brand=brand,
        person=person,
        signed_in=True,
        note=note,
        graph_link=graph_href(project, focus="notes"),
        source_link=_source_href(note["source_ref"]),
        next_href=note_href(project, note["note_id"]) + "?" + urlencode({"after": after})
        if after is not None
        else None,
        explorer_stylesheet=STYLESHEET_ADDRESS,
    )


def _source_href(value: str | None) -> str | None:
    """`source_ref` as a link when it is an http or https address with a host and no user
    name; None for anything else, which is shown as text."""
    if value is None or "\\" in value or any(char.isspace() for char in value):
        return None
    try:
        uri = urlsplit(value)
        if uri.scheme in {"http", "https"} and uri.hostname and uri.username is None:
            return value
    except ValueError:
        pass
    return None


def render_restart(
    project: str, message: str, status: int, *, brand: Any, person: Any
) -> HTMLResponse:
    """The page that says a continuation is stale (409) or cannot be read (400) and links the
    start of the project's graph. It runs no script."""
    return _page(
        "graph_restart.html",
        status,
        brand=brand,
        person=person,
        signed_in=True,
        href=graph_href(project),
        message=message,
    )


def explorer_files() -> list[Route]:
    """GET /static/graph-view.js and /static/graph-view.css, each by its exact name, with the
    bytes that were hashed at start and nosniff. Registered only with the explorer on;
    cairn.web.app's /static/ never serves either file, by any spelling."""
    kept = {"x-content-type-options": "nosniff", "cache-control": "no-cache"}

    def script(request: Request) -> Response:
        return Response(_SCRIPT_BYTES, media_type="text/javascript; charset=utf-8", headers=kept)

    def stylesheet(request: Request) -> Response:
        return Response(_SHEET_BYTES, media_type="text/css; charset=utf-8", headers=kept)

    return [
        Route(SCRIPT_ADDRESS, script, methods=["GET"]),
        Route("/static/" + GRAPH_STYLESHEET, stylesheet, methods=["GET"]),
    ]
