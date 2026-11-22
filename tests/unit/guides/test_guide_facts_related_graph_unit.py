"""Sentences of the guides and of the contract on the graph explorer's view of a page and its
related pages (KG2), each pinned to what the explorer does. The world is that of
tests/unit/test_related_page_unit.py, with the explorer on; made-up names."""

import json
import logging

import pytest

import cairn.server
from cairn import graph as graphs
from cairn import relations
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.web import graph_routes
from support.guide_source import guide_source
from support.store.related_page import build, get, note, shown
from support.web.design_system import tree
from support.web.related_graph import RELATED, drawn

MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
EXPLICIT = {"has page", "has claim", "has note", "has passage", "cites"}  # the graph's edges


def flat(text: str) -> str:
    return " ".join(text.split())


def says(guide: str, *sentences: str) -> None:
    text = flat(guide_source(guide))
    for sentence in sentences:
        assert text.count(flat(sentence)) == 1, sentence


def doc_says(doc: str, *sentences: str) -> None:
    for sentence in sentences:
        assert flat(doc).count(flat(sentence)) == 1, sentence


@pytest.fixture
def w(tmp_path):
    made = build(tmp_path, graph_explorer=True)
    with made.client:
        note(made, "Deploy", "Run the job with `--audit-keep` set to 7.\n")
        note(made, "Retention", "`--audit-keep` says how many; see Deploy.\n")
        note(made, "Queue", "Brook drains the queue at noon.\n")
        note(made, "Deploy", "Juniper runs it with `--audit-keep` set to 9.\n", project="vault")
        yield made
    made.store.close()


# run.md, "A graph to explore"


def test_the_explorer_has_a_view_of_a_page_and_its_related_pages_to_walk(w):
    says(
        "run",
        "The explorer has one more view, a page and its related pages: in the view of a"
        " project's pages and in the view of a page's claims, **Related pages** opens it. It"
        " draws the page with the pages `related` gives for it, at most 20, and each related"
        " page opens its own, so a person walks from page to page.",
    )
    assert graphs.PAGE_LIMIT == relations.RELATED_MAX == 20
    for view in ("", "?focus=page%3Adeploy"):
        offered = drawn(get(w, "/b/ops/graph" + view).text).nodes["page:deploy"]
        assert offered["Related pages"] == RELATED + "deploy", view
    answer = Memory(w.ada.view, lint_gate).related("ops", "deploy", 20)
    view = drawn(get(w, RELATED + "deploy").text)
    assert list(view.nodes) == ["page:deploy", *("page:" + p["page"] for p in answer["related"])]
    onward = view.svg_links["page:retention"]
    assert onward == RELATED + "retention"
    assert list(drawn(get(w, onward).text).nodes) == ["page:retention", "page:deploy"]


def test_each_relationship_says_the_first_reason_counts_the_others_and_links_the_claims(w):
    says(
        "run",
        "Each relationship says the first reason the two pages are related and how many more"
        " there are, and links the claims they rest on, on the page's **Related pages**.",
    )
    (edge,) = drawn(get(w, RELATED + "deploy").text).edges
    assert edge["said"].startswith("page: Deploy is named by page: Retention, and 1 more reason")
    (href,) = edge["evidence"]
    address, _, entry = href.partition("#")
    (page,) = [p for p in shown(get(w, address).text) if p["id"] == entry]
    assert [reason["kind"] for reason in page["why"]] == ["mentioned_by", "shares"]
    assert all(reason["there"]["text"] for reason in page["why"])  # the claims are there


def test_the_view_of_a_projects_pages_draws_no_relation_between_them(w):
    says(
        "run",
        "The view of a project's pages draws no relation between them, and says so: a relation"
        " is worked out for one page, when its view is opened.",
    )
    pages = drawn(get(w, "/b/ops/graph").text)
    assert {source for source, _ in pages.lines} == {f"project:{w.ops.id}"}
    assert "This view draws no relation between its pages" in pages.text
    assert all(edge["reason"] is None for edge in pages.edges)


def test_without_the_explorer_there_is_no_such_view_and_the_related_pages_link_still_works(
    tmp_path,
):
    says(
        "run",
        "With `--no-graph-explorer` there is no such view; a page's **Related pages** link works"
        " either way.",
    )
    made = build(tmp_path, graph_explorer=False)
    try:
        with made.client:
            note(made, "Deploy", "Run the job with `--audit-keep` set to 7.\n")
            note(made, "Retention", "`--audit-keep` says how many; see Deploy.\n")
            assert get(made, RELATED + "deploy").text == get(made, "/b/ops/graph").text
            assert "Related pages" not in get(made, "/b/ops/graph").text
            assert 'href="/b/ops/p/deploy/related"' in get(made, "/b/ops/p/deploy").text
            listed = shown(get(made, "/b/ops/p/deploy/related").text)
            assert [page["page"] for page in listed] == ["retention"]
    finally:
        made.store.close()


# security.md, "The web pages"


def test_the_related_view_draws_up_to_twenty_where_the_link_shows_eight_and_is_recorded_as_it(
    w, caplog
):
    says(
        "security",
        "With the explorer, a page also has a view of its related pages: up to 20 of the pages"
        " `related` gives for it, where its **Related pages** link shows 8 unless asked for"
        " more, drawn as the page and the pages related to it, without the claims' text, and"
        " recorded as that link's page is, as a read of the page.",
    )
    assert "the answer its **Related pages** link shows" not in guide_source("security")
    terms = [f"`s{n:02d}.cfg`" for n in range(10)]
    note(w, "Asked", "Ada sets " + " ".join(terms) + ".\n")
    for n, term in enumerate(terms):
        note(w, f"Other {n}", f"Brook {n} sets {term}.\n")
    linked = [entry["page"] for entry in shown(get(w, "/b/ops/p/asked/related").text)]
    most = [entry["page"] for entry in shown(get(w, "/b/ops/p/asked/related?limit=20").text)]
    nodes = [n.removeprefix("page:") for n in list(drawn(get(w, RELATED + "asked").text).nodes)]
    assert (len(linked), len(most), len(nodes) - 1) == (8, 10, 10)  # what each shows
    assert nodes[1:] == most and most[:8] == linked  # one answer, to two lengths
    w.store.reads_audited = True
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        view = get(w, RELATED + "deploy", w.dee)
        page = get(w, "/b/ops/p/deploy/related?limit=20", w.dee)
    assert [n.removeprefix("page:") for n in list(drawn(view.text).nodes)[1:]] == [
        entry["page"] for entry in shown(page.text)
    ]
    assert "says how many" in page.text and "says how many" not in view.text
    events = [e for e in w.store.list_audit(100) if e.action.startswith("read.")]
    assert [(e.action, e.project, e.detail["page"]) for e in events] == [
        ("read.page", "ops", "deploy")
    ] * 2
    records = [r for r in caplog.records if r.name == "cairn.access"]
    assert [(r.action, r.project, r.status) for r in records] == [("related", "ops", 200)] * 2


def test_no_view_relates_all_of_a_projects_pages_or_crosses_projects_and_the_graph_holds_none(w):
    says(
        "security",
        "No view draws the relations among all of a project's pages, and none crosses"
        " projects. `GET /api/graph` and the MCP resource hold no relation: they are the pages,"
        " claims, passages and notes as before, and an agent asks `related`.",
    )
    for view in ("", "?focus=notes", "?focus=page%3Adeploy"):
        assert all(
            edge["reason"] is None for edge in drawn(get(w, "/b/ops/graph" + view).text).edges
        )
    assert list(drawn(get(w, RELATED + "deploy").text).nodes) == ["page:deploy", "page:retention"]
    key = w.store.create_key("laptop")
    api = w.client.get("/api/graph?project=ops", headers={"x-api-key": key}).json()
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "resources/read",
        "params": {"uri": "graph://project/ops/20"},
    }
    read = w.client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": key}, json=body)
    resource = json.loads(read.json()["result"]["contents"][0]["text"])
    for answer in (api, resource):
        assert {edge["kind"] for edge in answer["edges"]} <= EXPLICIT
        assert {node["kind"] for node in answer["nodes"]} == {
            "project", "page", "claim", "passage", "note"
        }  # fmt: skip
    assert api == resource


# connect.md


def test_with_the_explorer_the_graph_page_draws_the_answer_of_related(w):
    says(
        "connect",
        "With the graph explorer on, the project's **Graph** page draws up to 20 of them, as a"
        " view of the page and its related pages.",
    )
    assert graphs.PAGE_LIMIT == relations.RELATED_MAX == 20
    answer = Memory(w.ada.view, lint_gate).related("ops", "deploy", 20)
    view = drawn(get(w, RELATED + "deploy").text)
    assert [(e["to"], e["reason"], e["term"]) for e in view.edges] == [
        ("page:" + page["page"], page["why"][0]["kind"], page["why"][0]["term"])
        for page in answer["related"]
    ]


# The contract: src/cairn/web/graph_routes.py and src/cairn/server.py


def test_the_contract_says_what_the_related_view_reads_and_how_it_is_recorded(w, caplog):
    doc_says(
        graph_routes.__doc__,
        '`focus` "related:<slug>" is a page and its related pages, what `Memory.related` answers'
        " for the page and nothing else",
        "It is two reads, neither inside the other: the reach check the other views make"
        " (cairn.graph.project_in_reach), then `Memory.related`, which rechecks the reach in the"
        " snapshot it reads the pages in.",
        "The request is one access-log record named `related`, not `graph`, and with reads"
        " audited one read.page event of that page, as GET /b/<project>/p/<slug>/related is.",
        "Every other request is one access-log record named `graph`",
    )
    doc_says(
        cairn.server.__doc__,
        'The graph page is one record named "graph" (cairn.web.graph_routes), but for its view'
        " of a page's related pages, which reads what `related` reads and is recorded as it.",
    )
    doc_says(
        graphs.__doc__,
        "The explorer's view of a page and its related pages (KG2) is not read here, and no"
        " function of this module reads a relation",
        "(cairn.store.relating: 13 statements at most, 17 with its caller's and 3 in memory,"
        " over the claims of other pages that hold the page's terms)",
    )
    from cairn.store import relating

    doc_says(relating.__doc__, "= 13, and 17 with the caller's.")
    assert "16 statements" not in graphs.__doc__
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        for path in ("/b/ops/graph", "/b/ops/graph?focus=page%3Adeploy", RELATED + "deploy"):
            assert get(w, path).status_code == 200
    records = [r for r in caplog.records if r.name == "cairn.access"]
    assert [r.action for r in records] == ["graph", "graph", "related"]


def test_the_contract_says_no_page_found_is_not_no_page_when_the_answer_is_not_complete(
    w, monkeypatch
):
    from cairn.store import relating

    doc_says(
        cairn.server.__doc__,
        "With no related page it says so in one sentence, with what makes two pages related;"
        " of an answer that is not complete it says only that none was found in what was"
        " looked at, never that there is none.",
    )
    doc_says(
        graph_routes.__doc__,
        "With no related page the view says so, and of an answer that is not complete only that"
        " none was found in what was looked at.",
    )
    monkeypatch.setattr(relating, "TERMS_MAX", 1)
    note(w, "Asked", "Use `a.cfg` and `long-name.cfg`.\n")
    note(w, "Other", "Brook sets `long-name.cfg`.\n")
    page = flat(tree(get(w, "/b/ops/p/asked/related").text).text())
    view = drawn(get(w, RELATED + "asked").text).text
    assert "No page related to Asked was found in what was looked at" in page
    assert "No related page was found in what was looked at." in view
    for said in (page, view):
        assert "is related to" not in said.split("two pages are related")[0]
        assert "This is not all of it: 1 of the page's 2 terms was not looked up." in said
    # complete, and none: said outright
    whole = flat(tree(get(w, "/b/ops/p/queue/related").text).text())
    assert "No page of ops is related to Queue" in whole
    assert "No page is related to the page in focus." in drawn(get(w, RELATED + "queue").text).text


def test_the_contract_says_text_of_a_note_is_set_apart_from_the_pages_own_words(w):
    from support.store.related_page import RLO, unisolated

    doc_says(
        cairn.server.__doc__,
        "Words that come from a note or name its writer (a title, a term, a writer's name)"
        " stand in a <bdi>, and what is quoted of a claim alone in an element of its own, so an"
        " override that nothing in them closes turns none of the page's own words.",
    )
    doc_says(
        graph_routes.__doc__,
        "In the list under the drawing, in every view, each label, a note's `source_ref` and a"
        " relationship's term stand in a <bdi>, so that an override nothing in them closes turns"
        " none of the page's own words.",
    )
    note(w, f"Quill{RLO} ledger", f"Use `a{RLO}b1.cfg` here.\n")
    note(w, f"Lo{RLO}ng", f"Also `a{RLO}b1.cfg` there; see the Quill ledger.\n")
    for path in ("/b/ops/p/quill-ledger/related", RELATED + "quill-ledger", "/b/ops/graph"):
        r = get(w, path)
        assert r.status_code == 200 and RLO in r.text and unisolated(r.text) == [], path
