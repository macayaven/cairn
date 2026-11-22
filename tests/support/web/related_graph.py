"""Shared test helpers extracted from test_related_graph_unit."""

from types import SimpleNamespace

from support.store.related_page import (
    flat,
)
from support.web.design_system import tree

RELATED = "/b/ops/graph?focus=related%3A"


def drawn(html: str) -> SimpleNamespace:
    """What a response of the explorer draws, read from its markup alone: the node the script
    lays the view out around, the nodes of the drawing and of the list, each with its links,
    and the edges of the drawing and of the list, each relationship with what it says."""
    page = tree(html)
    (root,) = [n for n in page.walk() if "data-graph-interactive" in n.attrs]
    svg_nodes, links = [], {}
    for g in [n for n in page.walk() if n.tag == "g" and "data-graph-node" in n.attrs]:
        svg_nodes.append(g.attrs["data-graph-node"])
        if g.parent.tag == "a":
            links[g.attrs["data-graph-node"]] = g.parent.attrs["href"]
    listed, anchors = {}, {}
    for li in [n for n in page.walk() if n.tag == "li" and "data-graph-node" in n.attrs]:
        anchors["#" + li.attrs["id"]] = li.attrs["data-graph-node"]
        listed[li.attrs["data-graph-node"]] = {a.text(): a.attrs["href"] for a in li.all("a")}
    lines = [
        (n.attrs["data-graph-source"], n.attrs["data-graph-target"])
        for n in page.walk()
        if n.tag == "line" and "data-graph-edge" in n.attrs
    ]
    relationships = []
    for li in [n for n in page.walk() if n.tag == "li" and "data-graph-edge" in n.attrs]:
        source, target, *more = li.all("a")
        relationships.append(
            {
                "from": anchors[source.attrs["href"]],
                "to": anchors[target.attrs["href"]],
                "reason": li.attrs.get("data-graph-reason"),
                "term": li.attrs.get("data-graph-term"),
                "more": li.attrs.get("data-graph-more"),
                "said": flat(li.text()),
                "code": [code.text() for code in li.all("code")],
                "evidence": [a.attrs["href"] for a in more],
            }
        )
    return SimpleNamespace(
        focus=root.attrs["data-graph-focus"],
        svg_nodes=svg_nodes,
        svg_links=links,
        nodes=listed,
        lines=lines,
        edges=relationships,
        text=flat(page.text()),
        scripts=page.all("script"),
        hidden=[n for n in page.walk() if "data-graph-controls" in n.attrs and "hidden" in n.attrs],
    )
