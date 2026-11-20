"""Regression tests for the seven product fix-now items on release/0.1.0."""

from __future__ import annotations

import re

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
