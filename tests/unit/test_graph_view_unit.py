"""The server-rendered picture and keyboard list expose the same safe graph."""

import importlib.util
from html.parser import HTMLParser


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.nodes = {"svg": [], "list": []}
        self.edges = {"svg": [], "list": []}
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "data-graph-node" in attrs:
            self.nodes[attrs["data-graph-area"]].append(attrs["data-graph-node"])
        if "data-graph-edge" in attrs:
            self.edges[attrs["data-graph-area"]].append(attrs["data-graph-edge"])
        if tag == "a":
            self.links.append(attrs.get("href", ""))


def test_svg_and_accessible_list_contain_the_same_nodes_and_edges_without_injection():
    assert importlib.util.find_spec("cairn.web.graph_view") is not None, "graph view missing"
    from cairn.web.graph_view import render_graph

    graph = {
        "nodes": [
            {"id": "project:1", "kind": "project", "label": "alpha"},
            {"id": "page:x", "kind": "page", "label": "<script>alert(1)</script>", "slug": "x"},
            {
                "id": "note:1",
                "kind": "note",
                "label": "Note",
                "source_ref": "javascript:alert(1)",
                "via": "mail",
            },
        ],
        "edges": [{"kind": "has page", "from": "project:1", "to": "page:x"}],
        "truncated": False,
    }
    response = render_graph("alpha", graph, brand=None, person=None)
    html = response.body.decode()
    parser = Elements()
    parser.feed(html)
    assert parser.nodes["svg"] == parser.nodes["list"] == ["project:1", "page:x", "note:1"]
    assert parser.edges["svg"] == parser.edges["list"] == ["0"]
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "javascript:alert(1)" in html
    assert all(not href.startswith("javascript:") for href in parser.links)
    assert "/b/alpha/p/x" in parser.links
    assert "default-src 'none'" in response.headers["content-security-policy"]


def test_relationship_endpoints_link_to_labeled_nodes_without_the_svg():
    from cairn.web.graph_view import render_graph

    graph = {
        "nodes": [
            {"id": "project:1", "kind": "project", "label": "Alpha"},
            {"id": "claim:0:x", "kind": "claim", "label": "The claim", "slug": "x"},
        ],
        "edges": [{"kind": "has claim", "from": "project:1", "to": "claim:0:x"}],
        "truncated": False,
    }

    class RelationshipLinks(HTMLParser):
        def __init__(self):
            super().__init__()
            self.node_anchors = set()
            self.inside_edge = False
            self.current = None
            self.links = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if attrs.get("data-graph-area") == "list" and "data-graph-node" in attrs:
                self.node_anchors.add(attrs.get("id"))
            if attrs.get("data-graph-area") == "list" and "data-graph-edge" in attrs:
                self.inside_edge = True
            if self.inside_edge and tag == "a":
                self.current = [attrs.get("href"), ""]

        def handle_data(self, data):
            if self.current is not None:
                self.current[1] += data

        def handle_endtag(self, tag):
            if tag == "a" and self.current is not None:
                self.links.append(tuple(self.current))
                self.current = None
            if tag == "li" and self.inside_edge:
                self.inside_edge = False

    parser = RelationshipLinks()
    parser.feed(render_graph("alpha", graph, brand=None, person=None).body.decode())
    assert [text for _, text in parser.links] == ["project: Alpha", "claim: The claim"]
    assert all(
        href and href.startswith("#") and href[1:] in parser.node_anchors
        for href, _ in parser.links
    )


def test_long_labels_fit_their_svg_columns_and_keep_full_text_in_the_list():
    from cairn.web.graph_view import render_graph

    sentence = "A long explanatory label with wide letters and many details. " * 4
    graph = {
        "nodes": [
            {"id": "project:1", "kind": "project", "label": "Alpha"},
            {"id": "page:a", "kind": "page", "label": sentence, "slug": "a"},
            {"id": "claim:0:a", "kind": "claim", "label": sentence, "slug": "a"},
            {"id": "passage:1", "kind": "passage", "label": sentence},
            {"id": "note:1", "kind": "note", "label": sentence, "source_ref": None, "via": None},
        ],
        "edges": [],
        "truncated": False,
    }

    class Picture(HTMLParser):
        def __init__(self):
            super().__init__()
            self.width = None
            self.viewports = []
            self.texts = []
            self.current = None
            self.in_text = False

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "svg" and self.width is None:
                self.width = int(attrs["width"])
            elif tag == "svg":
                self.current = attrs
                self.viewports.append(attrs)
            if tag == "text":
                self.in_text = True
                self.texts.append([self.current, attrs, ""])

        def handle_data(self, data):
            if self.texts and self.in_text:
                self.texts[-1][2] += data

        def handle_endtag(self, tag):
            if tag == "svg" and self.current:
                self.current = None
            if tag == "text":
                self.in_text = False

    html = render_graph("alpha", graph, brand=None, person=None).body.decode()
    picture = Picture()
    picture.feed(html)
    assert len(picture.texts) == 5
    for viewport, attrs, text in picture.texts:
        assert viewport is not None and viewport["overflow"] == "hidden"
        assert int(viewport["x"]) + int(viewport["width"]) <= picture.width
        assert int(attrs["x"]) == 0
        assert len(text) <= 24
    assert all(text.endswith("…") for _, _, text in picture.texts[1:])
    assert all(
        int(a["x"]) + int(a["width"]) < int(b["x"])
        for a, b in zip(picture.viewports, picture.viewports[1:], strict=False)
    )
    # each complete label is in the HTML list; the drawing holds no <title>, which a parser of
    # the page's own title would read as part of it
    assert html.count(sentence) >= 4
    assert html.count("<title>") == 1


def test_graph_diagram_has_a_keyboard_focusable_scroll_region():
    from cairn.web.graph_view import render_graph

    graph = {
        "nodes": [{"id": "project:1", "kind": "project", "label": "Alpha"}],
        "edges": [],
        "truncated": False,
    }

    class Regions(HTMLParser):
        def __init__(self):
            super().__init__()
            self.regions = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if attrs.get("role") == "region":
                self.regions.append(attrs)

    parser = Regions()
    parser.feed(render_graph("alpha", graph, brand=None, person=None).body.decode())
    assert any(
        r.get("tabindex") == "0" and r.get("aria-label") == "Graph diagram" for r in parser.regions
    )


def test_graph_labels_use_the_current_foreground_color_in_dark_mode():
    from cairn.web.graph_view import render_graph

    graph = {
        "nodes": [
            {"id": "project:1", "kind": "project", "label": "Alpha"},
            {"id": "note:1", "kind": "note", "label": "Note", "source_ref": None, "via": None},
        ],
        "edges": [],
        "truncated": False,
    }

    class LabelColors(HTMLParser):
        def __init__(self):
            super().__init__()
            self.fill = []

        def handle_starttag(self, tag, attrs):
            if tag == "text":
                self.fill.append(dict(attrs).get("fill"))

    labels = LabelColors()
    labels.feed(render_graph("alpha", graph, brand=None, person=None).body.decode())
    assert labels.fill == ["currentColor", "currentColor"]
