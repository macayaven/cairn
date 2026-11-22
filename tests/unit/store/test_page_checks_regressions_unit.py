"""Read historical metrics in the browser, including after a portable round trip."""

import io
import re
import sys
from contextlib import ExitStack, closing

import pytest
from starlette.testclient import TestClient

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.server import create_app
from cairn.store import Store
from support.web.design_system import tree

BASE = "https://cairn.example.test"
PATH = "/b/ops/p/tools"
FACT = "The caliper measures the rim."
NORMAL = {
    "claims": 1,
    "spans_cited": 1,
    "retention": 0.5,
    "code_line_retention": 0.875,
    "span_coverage": 1.0,
    "omission_rate": 0.0,
}


def checks(metrics, *, restored, historical):
    with ExitStack() as stack:
        store = stack.enter_context(closing(Store(":memory:")))
        project = store.create_project("ops")
        saved = Memory(store, lint_gate).note("ops", FACT, title="Tools", topic="Tools")
        assert saved["status"] == "saved"
        page = store.get_page(project.id, "tools")
        version = store.save_page(
            project.id, page.slug, page.title, page.body, page.claims, metrics
        )
        if historical:
            store.save_page(project.id, page.slug, page.title, page.body, page.claims, page.metrics)
        if restored:
            archive = io.BytesIO()
            store.export(archive)
            archive.seek(0)
            store = stack.enter_context(closing(Store(":memory:")))
            store.import_export(archive)
        stored = store.get_page(store.get_project("ops").id, "tools", version.version)
        assert stored.metrics == metrics
        key = store.create_key("reader")
        with TestClient(
            create_app(store, public_url=BASE), base_url=BASE, raise_server_exceptions=False
        ) as client:
            login = client.post(
                "/login",
                data={"key": key},
                headers={"sec-fetch-site": "same-origin"},
                follow_redirects=False,
            )
            assert login.status_code == 303
            response = client.get(PATH, params={"version": version.version} if historical else {})
        assert response.status_code == 200
        doc = tree(response.text)
        assert FACT in doc.text()
        rows = [r for r in doc.all("tr") if "data-metric" in r.attrs]
        values = {r.attrs["data-metric"]: r.all("td")[-1].text().strip() for r in rows}
        assert values["code_line_retention"] == "87.5%"
        return values


@pytest.mark.parametrize("restored", [False, True], ids=["stored", "restored"])
@pytest.mark.parametrize("historical", [False, True], ids=["latest", "numbered"])
@pytest.mark.parametrize(
    "value",
    [10**400, 1e308, sys.float_info.max, -sys.float_info.max],
    ids=["large-integer", "reported-float", "max-float", "negative-max"],
)
def test_out_of_range_ratio_still_shows_a_readable_page(restored, historical, value):
    rows = checks(NORMAL | {"retention": value}, restored=restored, historical=historical)
    assert rows["claims"] == rows["passages_cited"] == "1"
    infinite = [
        v for v in rows.values() if re.search(r"(?<!\w)[+-]?(?:inf(?:inity)?|∞)(?!\w)", v, re.I)
    ]
    assert not infinite


@pytest.mark.parametrize("restored", [False, True], ids=["stored", "restored"])
@pytest.mark.parametrize(
    "order",
    [
        tuple(NORMAL),
        tuple(reversed(NORMAL)),
        (
            "span_coverage",
            "claims",
            "omission_rate",
            "code_line_retention",
            "spans_cited",
            "retention",
        ),
    ],
    ids=["counts-first", "ratios-first", "interleaved"],
)
def test_counts_precede_the_ratios_in_any_stored_order(restored, order):
    rows = checks({name: NORMAL[name] for name in order}, restored=restored, historical=True)
    assert rows == {
        "claims": "1",
        "passages_cited": "1",
        "retention": "50%",
        "code_line_retention": "87.5%",
        "passage_coverage": "100%",
        "omission_rate": "0%",
    }
    names = list(rows)
    counts = [names.index(k) for k in ("claims", "passages_cited")]
    ratios = [
        names.index(k)
        for k in ("retention", "code_line_retention", "passage_coverage", "omission_rate")
    ]
    assert max(counts) < min(ratios), names
