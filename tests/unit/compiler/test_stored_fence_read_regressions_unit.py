"""Read a version made by the older writer through both public page endpoints."""

from contextlib import closing

import pytest
from markdown_it import MarkdownIt
from starlette.testclient import TestClient

from cairn.server import create_app
from cairn.store import Store
from support.compiler.assembled_fence import saved_by_23c24229, triples
from support.compiler.core_promise_title import BASE, mcp_page


@pytest.fixture
def pages(tmp_path):
    with closing(Store(tmp_path / "old.db")) as store:
        store.create_project("alpha")
        key = store.create_key("reader")
        safe, _ = saved_by_23c24229(store, triples(16))
        old, _ = saved_by_23c24229(store, triples(17))
        other = [text.replace("item", "row").replace("sample", "entry") for text in triples(18)]
        latest, _ = saved_by_23c24229(store, other)
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            yield client, key, safe, old, latest


def read(client, key, endpoint, version):
    if endpoint == "mcp":
        return mcp_page(client, key, "ops", version)
    response = client.get(
        "/api/projects/alpha/pages/ops",
        params={} if version is None else {"version": version},
        headers={"x-api-key": key},
    )
    assert response.is_success
    return response.json()


def check_claims(answer, page):
    assert answer["version"] == page.version
    assert [(c["id"], c["text"], tuple(c["passage_ids"])) for c in answer["claims"]] == [
        (c.id, c.text, c.span_ids) for c in page.claims
    ]


def check_fence_lines(body, claims):
    lines = body.splitlines()
    owners = {}
    for claim in claims:
        # Each whole passage is unique, including the sample line following a common opener.
        passage = claim.text.splitlines()
        for at in range(len(lines)):
            if lines[at : at + len(passage)] == passage:
                owners.update(dict.fromkeys(range(at, at + len(passage)), claim.id))
    for token in MarkdownIt("commonmark").parse(body):
        if token.type == "fence":
            first, end = token.map
            for line in range(first, end):
                if line in owners:
                    assert owners[line] == owners.get(first), (token.map, lines[line])


@pytest.mark.parametrize("endpoint", ["rest", "mcp"])
def test_sixteen_crossings_read_byte_for_byte(pages, endpoint):
    client, key, safe, _, _ = pages
    answer = read(client, key, endpoint, safe.version)
    check_claims(answer, safe)
    expected = (
        "# Ops\n\n"
        + "\n\n".join(
            f"- item {n}\n\n    ~~~\n    sample {n}\n    ~~~\n\n  After {n}." for n in range(16)
        )
        + "\n"
    )
    assert answer["body"].encode() == expected.encode()
    check_fence_lines(answer["body"], safe.claims)


@pytest.mark.parametrize("endpoint", ["rest", "mcp"])
@pytest.mark.parametrize("versioned", [True, False], ids=["historical-17", "latest-18"])
def test_overflow_returns_all_evidence_and_one_fixed_notice(pages, endpoint, versioned):
    client, key, safe, old, latest = pages
    page, version = (old, old.version) if versioned else (latest, None)
    answer = read(client, key, endpoint, version)
    check_claims(answer, page)
    body = answer["body"]
    check_fence_lines(body, page.claims)
    assert body.strip() and all(c.text not in body for c in page.claims)
    paragraphs = [t for t in MarkdownIt("commonmark").parse(body) if t.type == "paragraph_open"]
    assert len(paragraphs) == 1
    normal = read(client, key, endpoint, safe.version)
    other = read(client, key, endpoint, None if versioned else old.version)
    assert body == other["body"]
    assert any(
        type(value) is bool and other.get(name) is value and normal.get(name) is not value
        for name, value in answer.items()
    )
