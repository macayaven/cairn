"""The explorer's view of a page and its related pages: GET /b/<project>/graph?focus=related:<slug>.

It draws what `Memory.related` answers and nothing else: the page in focus, the related pages
in the answer's order, and from the first to each of the others an edge that says the first
reason, with how many more there are and a link to that page's entry on the page's related
pages, where the claims are. Each related page's node opens its own related view. The view is
ordinary nodes and edges in the markup the explorer's script already reads; without the
script everything is there. It is one read of `related`, recorded as one, behind the reach
check the explorer's other views make.

The world is that of tests/unit/test_related_page_unit.py, with the explorer on.
"""

import json
import logging
from types import SimpleNamespace

import pytest
from test_design_system_unit import tree
from test_related_page_unit import (
    ADMISSION,
    RLO,
    Watch,
    build,
    flat,
    get,
    main_of,
    note,
    read_alone,
    unisolated,
)

from cairn import graph as graphs
from cairn import relations
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Store
from cairn.store.reach import REACH_MAX
from cairn.web import graph_routes
from cairn.web.graph_view import related_graph, render_explorer

RELATED = "/b/ops/graph?focus=related%3A"


@pytest.fixture
def w(tmp_path):
    made = build(tmp_path, graph_explorer=True)
    with made.client:
        yield made
    made.store.close()


@pytest.fixture
def world(w):
    """Deploy states a flag that Retention states too, and Retention's claim names Deploy. The
    vault holds a page of the same title, and a page that states the flag and names it."""
    note(w, "Deploy", "Run the job with `--audit-keep` set to 7.\n")
    note(w, "Retention", "`--audit-keep` says how many; see Deploy.\n")
    note(w, "Queue", "Brook drains the queue at noon.\n")
    note(w, "Deploy", "Juniper runs it with `--audit-keep` set to 9.\n", project="vault")
    note(w, "Cellar", "Juniper keeps `--audit-keep` low; see Deploy.\n", project="vault")
    return w


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


# What it draws


def test_it_draws_the_page_its_related_pages_and_an_edge_that_says_the_first_reason(w, world):
    r = get(w, RELATED + "deploy")
    assert r.status_code == 200
    view = drawn(r.text)
    assert list(view.nodes) == ["page:deploy", "page:retention"]  # the page, then the answer's
    assert view.svg_nodes == list(view.nodes) and view.focus == "page:deploy"
    assert view.lines == [("page:deploy", "page:retention")]
    (edge,) = view.edges
    assert (edge["from"], edge["to"]) == ("page:deploy", "page:retention")
    # Retention's claim names Deploy, and both state the flag: the first reason, and one more
    answer = Memory(w.ada.view, lint_gate).related("ops", "deploy", 20)
    assert [reason["kind"] for reason in answer["related"][0]["why"]] == ["mentioned_by", "shares"]
    assert (edge["reason"], edge["term"], edge["more"]) == ("mentioned_by", "Deploy", "1")
    assert edge["said"] == (
        "page: Deploy is named by page: Retention, and 1 more reason \N{MIDDLE DOT} See the claims"
    )
    # the relationship links that page's entry on the related pages, where the evidence is
    assert edge["evidence"] == ["/b/ops/p/deploy/related?limit=20#related-retention"]
    evidence = get(w, "/b/ops/p/deploy/related?limit=20")
    assert evidence.status_code == 200 and 'id="related-retention"' in evidence.text
    assert "1 related page shown." in view.text and "audit-keep" not in view.text
    assert "says how many" not in r.text  # no claim's text: the evidence is on the other page


def test_an_edge_for_a_shared_term_says_the_term_as_code(w):
    note(w, "Asked", "Use `a.cfg`, `b.cfg` and `c.cfg`.\n")
    note(w, "Two", "Use `a.cfg` and `b.cfg`.\n")
    note(w, "One", "Use `c.cfg`.\n")
    view = drawn(get(w, RELATED + "asked").text)
    assert list(view.nodes) == ["page:asked", "page:two", "page:one"]
    two, one = view.edges
    assert (two["reason"], two["term"], two["more"], two["code"]) == (
        "shares",
        "a.cfg",
        "1",
        ["a.cfg"],
    )
    assert two["said"] == (
        "page: Asked shares a.cfg with page: Two, and 1 more reason \N{MIDDLE DOT} See the claims"
    )
    assert (one["reason"], one["term"], one["more"]) == ("shares", "c.cfg", "0")
    assert one["said"] == "page: Asked shares c.cfg with page: One \N{MIDDLE DOT} See the claims"
    assert view.lines == [("page:asked", "page:two"), ("page:asked", "page:one")]


def test_and_n_more_counts_every_reason_but_the_first(w):
    terms = " ".join(f"`t{n}.cfg`" for n in range(5))
    note(w, "Asked", f"Use {terms}.\n")
    note(w, "Twin", f"Brook uses {terms} too; see Asked.\n")
    answer = Memory(w.ada.view, lint_gate).related("ops", "asked", 20)
    (page,) = answer["related"]
    assert (len(page["why"]), page["more"]) == (relations.WHY_MAX, 3)
    (edge,) = drawn(get(w, RELATED + "asked").text).edges
    assert edge["more"] == "5" and ", and 5 more reasons" in edge["said"]


def test_the_view_is_made_of_the_answer_and_of_nothing_else():
    """related_graph, on an answer made by hand: the page in focus, each related page in the
    answer's order, an edge to each from the first reason, and what the read left out."""

    def claim(n: int) -> dict:
        return {"claim_id": f"cl_{n}", "text": "never drawn", "passage_ids": []}

    answer = {
        "project": "ops",
        "page": "deploy",
        "title": "Deploy",
        "version": 4,
        "related": [
            {
                "page": "b",
                "title": "B",
                "version": 1,
                "why": [
                    {
                        "kind": "shares",
                        "term": "x.cfg",
                        "term_kind": "inline_code",
                        "pages": 3,
                        "here": claim(1),
                        "there": claim(2),
                    }
                ],
                "more": 2,
            },
            {
                "page": "a",
                "title": "A",
                "version": 9,
                "why": [
                    {
                        "kind": "mentioned_by",
                        "term": "Deploy",
                        "term_kind": "title",
                        "there": claim(3),
                    }
                ],
                "more": 0,
            },
        ],
        "searched": {
            "complete": False,
            "terms_looked_up": 1,
            "terms_not_looked_up": 0,
            "claims_read": 2,
            "claims_not_read": 0,
            "pages_left_out": 4,
        },
    }
    made = related_graph(answer)
    assert made == {
        "nodes": [
            {"id": "page:deploy", "kind": "page", "label": "Deploy", "slug": "deploy",
             "focus": "page:deploy", "column": 1},
            {"id": "page:b", "kind": "page", "label": "B", "slug": "b", "focus": "page:b",
             "column": 2},
            {"id": "page:a", "kind": "page", "label": "A", "slug": "a", "focus": "page:a",
             "column": 2},
        ],
        "edges": [
            {"kind": "shares", "from": "page:deploy", "to": "page:b", "reason": "shares",
             "term": "x.cfg", "more": 2},
            {"kind": "is named by", "from": "page:deploy", "to": "page:a",
             "reason": "mentioned_by", "term": "Deploy", "more": 0},
        ],
        "truncated": False,
        "focus": "related:deploy",
        "centre": "page:deploy",
        "next": None,
        "left_out": "This is not all of it: 4 more related pages are not shown.",
    }  # fmt: skip
    assert "never drawn" not in json.dumps(made)


def test_every_text_of_a_note_in_the_explorers_list_is_set_apart_for_direction(w):
    """Titles, a term, a claim's text and a note's source hold an override that nothing
    closes: in the list under the drawing each stands in a <bdi>, in every view."""
    note(w, f"Quill{RLO} ledger", f"Use `a{RLO}b1.cfg` here.\n")
    note(w, f"Lo{RLO}ng", f"Also `a{RLO}b1.cfg` there; {RLO}see the Quill ledger.\n")
    note(w, f"Sh{RLO}ort", f"Then `a{RLO}b1.cfg` again.\n")
    w.ada.writes.add_note(
        w.ops.id, f"A {RLO}loose note.\n", title=f"Lo{RLO}ose", source_ref=f"desk{RLO}:1"
    )
    related = get(w, RELATED + "short")
    view = drawn(related.text)
    assert list(view.nodes) == ["page:short", "page:long", "page:quill-ledger"]
    assert view.edges[0]["code"] == [f"a{RLO}b1.cfg"]
    assert f"shares <bdi><code>a{RLO}b1.cfg</code></bdi> with" in related.text
    assert f"page: <bdi>Sh{RLO}ort</bdi></a>" in related.text
    assert unisolated(related.text) == []
    claims = get(w, "/b/ops/graph?focus=page%3Along")
    for path in ("/b/ops/graph", "/b/ops/graph?focus=notes", RELATED + "quill-ledger"):
        r = get(w, path)
        assert r.status_code == 200 and RLO in main_of(r.text), path
        assert unisolated(r.text) == [], path
    assert RLO in main_of(claims.text) and unisolated(claims.text) == []


def test_a_long_term_wraps_in_the_explorers_list(w):
    from test_design_system_unit import rule

    from cairn.web.templating import HERE

    term = "https://docs.example.invalid/" + "a" * 150
    note(w, "Asked", f"Read {term} first.\n")
    note(w, "Other", f"Then read {term} again.\n")
    r = get(w, RELATED + "asked")
    (edge,) = drawn(r.text).edges
    assert edge["code"] == [term]
    (item,) = [n for n in tree(r.text).walk() if n.tag == "li" and "data-graph-edge" in n.attrs]
    assert item.within("graph-explorer")
    sheet = (HERE / "static" / "graph-view.css").read_text("utf-8")
    assert "overflow-wrap: anywhere" in rule(sheet, ".graph-explorer li")


# Walking from page to page


def test_each_related_pages_node_opens_its_own_related_view(w, world):
    view = drawn(get(w, RELATED + "deploy").text)
    walk = RELATED + "retention"
    assert view.svg_links["page:retention"] == walk
    assert view.nodes["page:retention"]["Retention"] == walk
    back = drawn(get(w, walk).text)
    assert list(back.nodes) == ["page:retention", "page:deploy"]
    assert back.svg_links["page:deploy"] == RELATED + "deploy"
    assert [(e["reason"], e["term"]) for e in back.edges] == [("shares", "--audit-keep")]
    # the page in focus links its own page, and its claims view
    assert view.svg_links["page:deploy"] == "/b/ops/p/deploy"
    assert view.nodes["page:deploy"] == {
        "Deploy": "/b/ops/p/deploy",
        "Open its view": "/b/ops/graph?focus=page%3Adeploy",
    }
    # a batch's limit is kept from view to view
    kept = drawn(get(w, RELATED + "deploy&limit=5").text)
    assert kept.svg_links["page:retention"] == walk + "&limit=5"
    assert kept.nodes["page:deploy"]["Open its view"] == "/b/ops/graph?focus=page%3Adeploy&limit=5"
    assert kept.edges[0]["evidence"] == ["/b/ops/p/deploy/related?limit=5#related-retention"]


def test_a_pages_claims_view_links_its_related_view_and_back(w, world):
    claims = drawn(get(w, "/b/ops/graph?focus=page%3Adeploy").text)
    assert claims.nodes["page:deploy"]["Related pages"] == RELATED + "deploy"
    related = drawn(get(w, RELATED + "deploy").text)
    assert related.nodes["page:deploy"]["Open its view"] == "/b/ops/graph?focus=page%3Adeploy"
    assert "Related pages" not in related.nodes["page:deploy"]  # it is the view shown


def test_the_pages_view_offers_each_pages_related_view_and_says_it_draws_no_relation(w, world):
    r = get(w, "/b/ops/graph")
    view = drawn(r.text)
    for slug in ("deploy", "queue", "retention"):
        assert view.nodes[f"page:{slug}"]["Related pages"] == RELATED + slug
        assert view.nodes[f"page:{slug}"]["Open its view"] == f"/b/ops/graph?focus=page%3A{slug}"
    assert view.svg_links["page:deploy"] == "/b/ops/p/deploy"  # as before
    # no edge among its pages: only the project's
    assert {source for source, _ in view.lines} == {f"project:{w.ops.id}"}
    said = "This view draws no relation between its pages: open a page's related pages to see them."
    assert said in view.text
    for other in ("?focus=notes", "?focus=page%3Adeploy", "?focus=related%3Adeploy"):
        assert said not in drawn(get(w, "/b/ops/graph" + other).text).text, other


# Without the script


def test_without_the_script_every_node_edge_and_link_is_in_the_markup(w, world):
    note(w, "Asked", "Use `a.cfg`, `b.cfg` and `c.cfg`.\n")
    note(w, "Two", "Use `a.cfg` and `b.cfg`.\n")
    note(w, "One", "Use `c.cfg`.\n")
    for slug in ("deploy", "asked", "queue"):
        r = get(w, RELATED + slug)
        view = drawn(r.text)
        assert len(view.hidden) == 1, slug  # the controls only the script uses
        assert view.svg_nodes == list(view.nodes), slug  # the drawing and the list, in order
        assert view.lines == [(edge["from"], edge["to"]) for edge in view.edges], slug
        assert view.focus == f"page:{slug}" and view.focus in view.nodes  # what it is laid around
        assert all(len(edge["evidence"]) == 1 for edge in view.edges), slug
        for node in view.svg_nodes:
            assert view.svg_links[node] == view.nodes[node][next(iter(view.nodes[node]))], node
        (script,) = view.scripts  # the explorer's one script, as on its other views
        assert script.attrs["src"] == "/static/graph-view.js" and "integrity" in script.attrs
        assert " style=" not in r.text
    alone = drawn(get(w, RELATED + "queue").text)
    assert list(alone.nodes) == ["page:queue"] and alone.edges == [] and alone.lines == []
    assert "0 related pages shown. No page is related to the page in focus." in alone.text
    assert "Each relationship below" not in alone.text
    said = get(w, "/b/ops/p/queue/related?limit=20")  # where that sentence's link leads
    assert 'href="/b/ops/p/queue/related?limit=20"' in get(w, RELATED + "queue").text
    assert "two pages are related when" in said.text


def test_the_drawing_sent_puts_the_page_beside_its_related_pages_not_among_them(w):
    """Without the script the drawing is the one the server sent: the page in focus stands in
    a column of its own, so no edge runs along another, and no name covers a mark."""
    note(w, "Asked", "Use `a.cfg`, `b.cfg` and `c.cfg`.\n")
    note(w, "Two", "Use `a.cfg` and `b.cfg`.\n")
    note(w, "One", "Use `c.cfg`.\n")
    page = tree(get(w, RELATED + "asked").text)
    at = {
        g.attrs["data-graph-node"]: (int(g.attrs["data-x"]), int(g.attrs["data-y"]))
        for g in page.walk()
        if g.tag == "g" and "data-graph-node" in g.attrs
    }
    assert at["page:asked"][0] < at["page:two"][0] == at["page:one"][0]
    assert at["page:two"][1] != at["page:one"][1]
    icons = [(x - 7, y - 7, x + 7, y + 7) for x, y in at.values()]
    labels = [(x + 9, y - 8, x + 9 + 190, y - 8 + 22) for x, y in at.values()]
    for label in labels:
        for icon in icons:
            apart = (
                label[2] <= icon[0] or icon[2] <= label[0]
                or label[3] <= icon[1] or icon[3] <= label[1]
            )  # fmt: skip
            assert apart, (label, icon)


def test_render_explorer_is_called_as_it_was_and_draws_the_view_it_is_given():
    answer = {
        "project": "alpha",
        "page": "x",
        "title": "Page <b>x</b>",
        "version": 1,
        "related": [],
        "searched": {
            "complete": True,
            "terms_looked_up": 0,
            "terms_not_looked_up": 0,
            "claims_read": 0,
            "claims_not_read": 0,
            "pages_left_out": 0,
        },
    }
    html = render_explorer("alpha", related_graph(answer), brand=None, person=None).body.decode()
    assert "0 related pages shown." in html and "Page &lt;b&gt;x&lt;/b&gt;" in html
    assert 'data-graph-focus="page:x"' in html and "<b>" not in html
    assert 'name="q"' not in html  # nothing to find in: the view is one answer
    assert 'href="/b/alpha/p/x/related?limit=20"' in html


# Limits, refusals and reach


def test_limit_is_the_batch_and_what_is_left_out_is_said_in_the_related_pages_sentence(w):
    note(w, "Asked", "Use `a.cfg`, `b.cfg` and `c.cfg`.\n")
    note(w, "Two", "Use `a.cfg` and `b.cfg`.\n")
    note(w, "One", "Use `c.cfg`.\n")
    r = get(w, RELATED + "asked&limit=1")
    view = drawn(r.text)
    assert list(view.nodes) == ["page:asked", "page:two"]
    assert "This is not all of it: 1 more related page is not shown." in view.text
    assert 'rel="next"' not in r.text  # one answer: no batch follows
    whole = drawn(get(w, RELATED + "asked").text)
    assert list(whole.nodes) == ["page:asked", "page:two", "page:one"]
    assert "This is not all of it" not in whole.text


def test_with_no_related_page_found_a_view_that_did_not_look_at_all_does_not_say_there_is_none(
    w, monkeypatch
):
    from cairn.store import relating

    monkeypatch.setattr(relating, "TERMS_MAX", 1)
    note(w, "Asked", "Use `a.cfg` and `long-name.cfg`.\n")
    note(w, "Other", "Brook sets `long-name.cfg`.\n")
    view = drawn(get(w, RELATED + "asked").text)
    assert list(view.nodes) == ["page:asked"] and view.edges == []
    assert (
        "0 related pages shown. No related page was found in what was looked at. This is not all"
        " of it: 1 of the page's 2 terms was not looked up." in view.text
    )
    assert "No page is related to the page in focus" not in view.text
    monkeypatch.undo()
    assert list(drawn(get(w, RELATED + "asked").text).nodes) == ["page:asked", "page:other"]


@pytest.mark.parametrize("limit", ["0", "21", "-1", "many"])
def test_a_limit_the_explorer_refuses_is_refused_as_on_its_other_views(w, world, limit):
    refused = get(w, RELATED + f"deploy&limit={limit}")
    other = get(w, f"/b/ops/graph?focus=page%3Adeploy&limit={limit}")
    assert refused.status_code == other.status_code == 400
    assert main_of(refused.text) == main_of(other.text)


def test_a_focus_longer_than_the_explorer_takes_is_refused_as_on_its_other_views(w, world):
    long = "x" * 600
    refused = get(w, RELATED + long)
    other = get(w, "/b/ops/graph?focus=page%3A" + long)
    assert refused.status_code == other.status_code == 400
    assert main_of(refused.text) == main_of(other.text)
    assert get(w, RELATED + "x" * 400).status_code == 404  # within the bound: no such page


def test_a_kind_of_reason_the_view_has_no_words_for_is_said_by_its_name():
    searched = dict.fromkeys(
        ("terms_looked_up", "terms_not_looked_up", "claims_read", "claims_not_read"), 0
    ) | {"complete": True, "pages_left_out": 0}
    why = {"kind": "cites_same", "term": "RFC 9110", "term_kind": "source", "there": {}}
    answer = {
        "project": "ops",
        "page": "deploy",
        "title": "Deploy",
        "version": 1,
        "related": [{"page": "b", "title": "B", "version": 1, "why": [why], "more": 0}],
        "searched": searched,
    }
    (edge,) = related_graph(answer)["edges"]
    assert (edge["kind"], edge["reason"], edge["term"]) == ("cites same", "cites_same", "RFC 9110")
    html = render_explorer("ops", related_graph(answer), brand=None, person=None).body.decode()
    assert "page: <bdi>Deploy</bdi></a> cites same <a" in html and "<code>" not in html


def test_a_page_that_is_gone_and_a_project_out_of_reach_answer_as_the_other_views(w, world):
    gone, claims = get(w, RELATED + "nowhere"), get(w, "/b/ops/graph?focus=page%3Anowhere")
    assert gone.status_code == claims.status_code == 404
    assert main_of(gone.text) == main_of(claims.text) and "nearest" not in gone.text
    for who in (w.brook, w.dee):  # the vault is Ada's alone
        hidden = get(w, "/b/vault/graph?focus=related%3Adeploy", who)
        missing = get(w, "/b/nowhere/graph?focus=related%3Adeploy", who)
        other = get(w, "/b/vault/graph?focus=page%3Adeploy", who)
        assert hidden.status_code == missing.status_code == other.status_code == 404
        assert hidden.text.replace("vault", "X") == missing.text.replace("nowhere", "X")
        assert main_of(hidden.text) == main_of(other.text)
        assert "Juniper" not in hidden.text and "<script" not in hidden.text
    assert get(w, "/b/vault/graph?focus=related%3Adeploy", w.ada).status_code == 200
    for who in (w.cy, w.brook, w.dee):  # manages, writes, only reads
        seen = drawn(get(w, RELATED + "deploy", who).text)
        assert list(seen.nodes) == ["page:deploy", "page:retention"], who.name
    # Given the vault, Brook sees its view; once it is taken back, the next request refuses
    w.ada.view.set_project_access(w.vault.id, people={w.brook.user.id: "read"})
    assert get(w, "/b/vault/graph?focus=related%3Adeploy", w.brook).status_code == 200
    w.ada.view.set_project_access(w.vault.id, people={})
    assert get(w, "/b/vault/graph?focus=related%3Adeploy", w.brook).status_code == 404


def test_another_projects_page_of_the_same_title_and_term_is_never_drawn(w, world):
    view = drawn(get(w, RELATED + "deploy", w.ada).text)  # Ada reaches the vault too
    assert list(view.nodes) == ["page:deploy", "page:retention"]
    assert "Cellar" not in view.text and "Juniper" not in view.text
    theirs = drawn(get(w, "/b/vault/graph?focus=related%3Adeploy", w.ada).text)
    assert list(theirs.nodes) == ["page:deploy", "page:cellar"]
    assert "Retention" not in theirs.text


def test_the_reach_check_is_the_explorers_own_and_fails_closed_as_it_does(tmp_path):
    store = Store(tmp_path / "reach.db")
    try:
        alpha = store.create_project("alpha")
        beta = store.create_project("beta")
        assert graphs.project_in_reach(store, "alpha") == (alpha.id, "alpha")
        assert graphs.project_in_reach(store.narrow([alpha.id]), alpha.id) == (alpha.id, "alpha")
        for ref in ("beta", beta.id, "nowhere"):
            with pytest.raises(KeyError):
                graphs.project_in_reach(store.narrow([alpha.id]), ref)
        deep = store
        for _ in range(graphs.SCOPE_DEPTH_LIMIT + 1):
            deep = deep.narrow([alpha.id])
        wide = store.narrow([alpha.id, *(f"x{n}" for n in range(REACH_MAX))])
        for view in (deep, wide):
            with pytest.raises(graphs.GraphUnavailable):
                graphs.project_in_reach(view, "alpha")
            with pytest.raises(graphs.GraphUnavailable):  # as the other views' read
                graphs.graph_neighbourhood(view, "alpha", focus="page:x")
    finally:
        store.close()


def test_a_reach_the_explorer_cannot_check_is_503_and_nothing_is_read(w, world, monkeypatch):
    def too_wide(store, ref):
        raise graphs.GraphUnavailable

    def never(*args, **kwargs):
        raise AssertionError("read past a reach that was not checked")

    monkeypatch.setattr(graph_routes, "project_in_reach", too_wide)
    monkeypatch.setattr(Memory, "related", never)
    refused = get(w, RELATED + "deploy")
    assert refused.status_code == 503
    assert "The graph is unavailable for this connection." in refused.text
    assert "Retention" not in refused.text


# The read is recorded as the related page records it


def test_the_view_is_one_read_of_the_page_recorded_as_related(w, world, caplog):
    def reads() -> list:  # oldest first
        return [e for e in w.store.list_audit(1000) if e.action.startswith("read.")][::-1]

    w.store.reads_audited = True
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        assert get(w, RELATED + "deploy", w.dee).status_code == 200
        assert get(w, "/b/ops/p/deploy/related", w.dee).status_code == 200
        assert get(w, "/b/ops/graph?focus=page%3Adeploy", w.dee).status_code == 200
        assert get(w, RELATED + "nowhere", w.dee).status_code == 404
        assert get(w, "/b/nowhere/graph?focus=related%3Adeploy", w.dee).status_code == 404
    view, page, claims = reads()
    assert (view.action, view.project, view.detail) == (page.action, page.project, page.detail)
    assert (view.action, view.detail["page"]) == ("read.page", "deploy")
    assert (claims.action, claims.project) == ("read.graph", "ops")
    assert "audit-keep" not in json.dumps([e.model_dump() for e in w.store.list_audit(1000)])
    records = [r for r in caplog.records if r.name == "cairn.access"]
    assert [(r.action, r.project, r.status) for r in records] == [
        ("related", "ops", 200),
        ("related", "ops", 200),
        ("graph", "ops", 200),
        ("related", "ops", 404),
        ("related", None, 404),
    ]


# A forget


def test_forgetting_a_note_takes_its_relation_from_the_view_in_the_next_request(w):
    note(w, "Asked", "Use `a.cfg` here.\n\nBrook reads `b.cfg` daily.\n")
    held = note(w, "Canary", "The canary line sets `a.cfg`; see Asked.\n")
    note(w, "Held", "Ada also keeps `b.cfg` warm.\n")
    before = drawn(get(w, RELATED + "asked").text)
    assert list(before.nodes) == ["page:asked", "page:canary", "page:held"]
    assert [edge["term"] for edge in before.edges] == ["Asked", "b.cfg"]
    Memory(w.ada.view, lint_gate).forget("ops", held["note_id"])
    r = get(w, RELATED + "asked")
    after = drawn(r.text)
    assert list(after.nodes) == ["page:asked", "page:held"]
    assert [edge["term"] for edge in after.edges] == ["b.cfg"]
    assert "Canary" not in r.text and "canary" not in r.text and held["note_id"] not in r.text
    assert get(w, RELATED + "canary").status_code == 404  # its page went with its one note


# Statements, never time


# What each view ran for this world before the explorer had the related view, counted at
# b1f04444: for the owner, and for a member who only reads the project.
EXPLORER_STATEMENTS = {
    "/b/ops/graph": {"Ada": 11, "Dee": 12},
    "/b/ops/graph?focus=notes": {"Ada": 11, "Dee": 12},
    "/b/ops/graph?focus=page%3Adeploy": {"Ada": 15, "Dee": 16},
}
CLAIM_VIEW_STATEMENTS = {"Ada": 18, "Dee": 19}
PAGE_VIEW_STATEMENTS = {"Ada": 19, "Dee": 27}
# What the related view runs more than the page's related pages do: the explorer's reach check
# (cairn.graph.project_in_reach), 3 statements for an owner and 4 for a member.
REACH_CHECK = {"Ada": 3, "Dee": 4}


def counted(w, path: str, who) -> int:
    assert get(w, path, who).status_code == 200  # once before: what a first call does once
    with Watch(w.store) as seen:
        assert get(w, path, who).status_code == 200
    return len(seen.statements)


def test_the_other_views_run_the_statements_they_ran_and_the_related_view_a_fixed_number(w, world):
    import re

    claims = get(w, "/b/ops/graph?focus=page%3Adeploy").text
    focus = re.search(r'href="/b/ops/graph\?(focus=claim%3A[^"&]*)', claims)[1]
    expected = EXPLORER_STATEMENTS | {
        f"/b/ops/graph?{focus}": CLAIM_VIEW_STATEMENTS,
        "/b/ops/p/deploy": PAGE_VIEW_STATEMENTS,
    }
    for path, ran in expected.items():
        for who in (w.ada, w.dee):
            assert counted(w, path, who) == ran[who.name], (path.split("%3A")[0], who.name)
    # the related view: its admission, the explorer's reach check and the read of `related`,
    # and nothing else: 23 statements here for the owner and 26 for the reader
    for who in (w.ada, w.dee):
        alone = read_alone(w, who, "deploy", graphs.PAGE_LIMIT)
        ran = counted(w, RELATED + "deploy", who)
        assert ran == ADMISSION + REACH_CHECK[who.name] + alone, who.name


def test_the_related_view_costs_the_same_in_a_larger_project(tmp_path):
    counts = []
    for size in (4, 40):
        made = build(tmp_path / str(size), graph_explorer=True)
        try:
            with made.client:
                note(made, "Asked", "Use `a.cfg` and `b.cfg`; see https://example.invalid/q.\n")
                note(made, "Twin", "Brook sets `a.cfg`; see Asked.\n")
                for n in range(size):
                    note(made, f"Shelf {n}", f"Shelf {n} holds quill spares and `s{n}.cfg`.\n")
                counts.append(counted(made, RELATED + "asked", made.ada))
        finally:
            made.store.close()
    most = ADMISSION + REACH_CHECK["Ada"] + 2 + 1 + 16  # as the related page, and the check
    assert counts[0] == counts[1] and counts[1] <= most, counts


# With the explorer off


def test_without_the_explorer_the_view_does_not_exist_and_nothing_links_it(tmp_path):
    made = build(tmp_path, graph_explorer=False)
    try:
        with made.client:
            note(made, "Deploy", "Run the job with `--audit-keep` set to 7.\n")
            note(made, "Retention", "`--audit-keep` says how many; see Deploy.\n")
            sheet = get(made, "/b/ops/graph")
            asked = get(made, RELATED + "deploy")
            assert asked.status_code == 200 and asked.text == sheet.text  # the static sheet
            assert "<script" not in asked.text and "focus=related" not in asked.text
            for path in ("/b/ops", "/b/ops/p/deploy", "/b/ops/p/deploy/related"):
                page = get(made, path)
                assert page.status_code == 200 and "focus=" not in page.text, path
    finally:
        made.store.close()
