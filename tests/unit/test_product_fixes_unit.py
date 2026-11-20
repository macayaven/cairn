"""Regression tests for the seven product fix-now items on release/0.1.0."""

from __future__ import annotations

import re
from itertools import pairwise

from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Claim, Store

BASE = "https://cairn.example.test"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}


def _login(client: TestClient, key: str) -> None:
    r = client.post("/login", data={"key": key}, headers=SAME_ORIGIN, follow_redirects=False)
    assert r.status_code == 303


def test_checks_table_formats_ratios_as_percentages_under_counts(tmp_path):
    store = Store(tmp_path / "cairn.db")
    try:
        key = store.create_key("owner")
        project = store.create_project("ops")
        note, _ = store.add_note(project.id, "# Tools\n\nAlpha runs.\n\nBeta stops.\n")
        spans = [s for s in store.note_spans(note.id) if s.kind == "paragraph"]
        claims = [Claim(id="cl_" + s.id[3:], text=s.text, span_ids=(s.id,)) for s in spans]
        metrics = {
            "claims": 2,
            "spans_cited": 2,
            "retention": 1.0,
            "code_line_retention": 0.875,
            "passage_coverage": 1.0,
            "omission_rate": 0.0,
        }
        store.save_page(project.id, "tools", "Tools", "# Tools\n", claims, metrics)
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            _login(client, key)
            html = client.get("/b/ops/p/tools").text
        checks_section = html.split("<h2>Checks</h2>", 1)[1].split("<h2>Sources</h2>", 1)[0]
        assert "<details" not in checks_section
        rows = dict(
            re.findall(
                r'<tr data-metric="([^"]+)">.*?<td data-label="Value" class="num">([^<]+)</td>',
                checks_section,
                re.S,
            )
        )
        assert rows["claims"] == "2"
        assert rows["passages_cited"] == "2"
        assert rows["retention"] == "100%"
        assert rows["code_line_retention"] == "87.5%"
        assert rows["passage_coverage"] == "100%"
        assert rows["omission_rate"] == "0%"
        assert html.index('data-metric="passages_cited"') < html.index('data-metric="retention"')
    finally:
        store.close()


def _segments_intersect_rect(
    x1: float, y1: float, x2: float, y2: float, rx1: float, ry1: float, rx2: float, ry2: float
) -> bool:
    """True when the closed segment passes strictly inside the open interior of a node box."""
    min_x, max_x = min(x1, x2), max(x1, x2)
    min_y, max_y = min(y1, y2), max(y1, y2)
    if max_x <= rx1 or min_x >= rx2 or max_y <= ry1 or min_y >= ry2:
        return False
    if x1 == x2 or y1 == y2:
        return True
    for t in (0.1, 0.25, 0.5, 0.75, 0.9):
        px = x1 + (x2 - x1) * t
        py = y1 + (y2 - y1) * t
        if rx1 < px < rx2 and ry1 < py < ry2:
            return True
    return False


def test_static_graph_scales_with_viewbox_avoids_crossing_labels_and_omits_blank_source_ref():
    from pathlib import Path

    from cairn.web.graph_view import render_graph

    graph = {
        "nodes": [
            {"id": "project:1", "kind": "project", "label": "alpha"},
            {"id": "page:a", "kind": "page", "label": "Backup Verification", "slug": "a"},
            {"id": "claim:0:a", "kind": "claim", "label": "Run verify first.", "slug": "a"},
            {"id": "passage:1", "kind": "passage", "label": "Run verify first."},
            {
                "id": "note:blank",
                "kind": "note",
                "label": "Local Note",
                "source_ref": None,
                "via": "web",
            },
            {
                "id": "note:sourced",
                "kind": "note",
                "label": "Mail Note",
                "source_ref": "mail:thread-42",
                "via": "web",
            },
        ],
        "edges": [
            {"kind": "has page", "from": "project:1", "to": "page:a"},
            {"kind": "has claim", "from": "page:a", "to": "claim:0:a"},
            {"kind": "has note", "from": "project:1", "to": "note:blank"},
            {"kind": "has note", "from": "project:1", "to": "note:sourced"},
            {"kind": "has passage", "from": "note:blank", "to": "passage:1"},
            {"kind": "cites", "from": "claim:0:a", "to": "passage:1"},
        ],
        "truncated": False,
    }
    html = render_graph("alpha", graph, brand=None, person=None).body.decode()

    svg_open = re.search(r'<svg xmlns="http://www.w3.org/2000/svg"([^>]*)>', html)
    assert svg_open is not None
    w = re.search(r'\bwidth="(\d+)"', svg_open.group(1))
    h = re.search(r'\bheight="(\d+)"', svg_open.group(1))
    vb = re.search(r'\bviewBox="([^"]+)"', svg_open.group(1))
    assert w and h and vb
    assert vb.group(1) == f"0 0 {w.group(1)} {h.group(1)}"

    boxes = [
        (float(x), float(y), float(x) + float(bw), float(y) + float(bh))
        for x, y, bw, bh in re.findall(
            r'<rect[^>]*class="graph-node-box"[^>]*x="([0-9.]+)"'
            r' y="([0-9.]+)" width="([0-9.]+)" height="([0-9.]+)"',
            html,
        )
    ]
    assert len(boxes) == len(graph["nodes"])

    paths = re.findall(r'<path[^>]*data-graph-area="svg"[^>]*d="([^"]+)"', html)
    assert len(paths) == len(graph["edges"])
    for d in paths:
        pts = [(float(x), float(y)) for x, y in re.findall(r"[ML]\s*([0-9.]+)\s+([0-9.]+)", d)]
        assert len(pts) >= 2
        for (x1, y1), (x2, y2) in pairwise(pts):
            for rx1, ry1, rx2, ry2 in boxes:
                assert not _segments_intersect_rect(x1, y1, x2, y2, rx1, ry1, rx2, ry2), (
                    d,
                    rx1,
                    ry1,
                    rx2,
                    ry2,
                )

    assert "Local Note; via: web" in html
    assert "Mail Note; source_ref: mail:thread-42; via: web" in html
    assert "source_ref: ;" not in html

    css = (Path(__file__).resolve().parents[2] / "src/cairn/static/cairn.css").read_text(
        encoding="utf-8"
    )
    assert ".graph-diagram > svg" in css and "max-width: 100%" in css and "height: auto" in css
