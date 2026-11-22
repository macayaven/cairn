"""What people are shown of related pages, over generated projects: the web page of a page's
related pages, and the explorer's view of them, show exactly what `Memory.related` answers,
in its order.

The projects are those of tests/unit/test_generated_related_unit.py (its `projects()`: seeded,
the same on every run), which checks the answers themselves; here each answer is compared
with the page made from it, read from the page's markup alone: the related pages in order,
each with its title, its reasons in order (kind, term, kind of term, how many pages hold it),
the claim on each side by its id and what is quoted of it, character for character (all of
it; or, of a claim that is cut, the line its answer gives, else its head), and the count of the
reasons not listed. Asked at the most pages an answer holds, and at 2, where pages are left
out. The explorer's view is compared the same way: its nodes are the page and the answer's
pages in order, in the drawing and in the list, and its edges go from the page to each of
them, each saying the first reason's kind and term and counting every other reason. A
failure names project and page numbers, never a note's text.
"""

import logging

import pytest
from starlette.testclient import TestClient

from cairn import relations
from cairn.gate import lint_gate
from cairn.memory import Memory, RequestError
from cairn.server import create_app
from cairn.store import Store
from support.cookie_names import session_cookie
from support.fuzz import is_full
from support.store.generated_related import projects
from support.store.related_page import given_of, shown
from support.web.related_graph import drawn

pytestmark = pytest.mark.fuzz

BASE = "http://127.0.0.1:8765"
TEXT_MAX, LINE_MAX = relations.TEXT_MAX, relations.LINE_MAX  # as shipped
FEW = 2  # a limit small enough to leave pages out
TEXT_GIVEN, LINE_GIVEN = 24, 34  # bounds on a claim's text and line small enough to be met
# Each pass over every page: (the limit asked, the reasons an answer gives for a page, whether
# claims are cut at the small bounds). The third gives one reason a page, so that pages
# count the reasons they do not list: with the three an answer gives, no generated page has
# more. The last cuts the claims, which are all shorter than the bounds an answer has.
ASKED = (
    (relations.RELATED_MAX, relations.WHY_MAX, False),
    (FEW, relations.WHY_MAX, False),
    (FEW, 1, False),
    (relations.RELATED_MAX, relations.WHY_MAX, True),
)


def bounds(monkeypatch, reasons_given: int, cut: bool) -> None:
    """One pass's bounds, for the answer and for the page made of it alike."""
    monkeypatch.setattr(relations, "WHY_MAX", reasons_given)
    monkeypatch.setattr(relations, "TEXT_MAX", TEXT_GIVEN if cut else TEXT_MAX)
    monkeypatch.setattr(relations, "LINE_MAX", LINE_GIVEN if cut else LINE_MAX)


def answered(answer: dict) -> list[dict]:
    """A `related` answer as a page of it can show it: what `shown` reads from the markup."""
    return [
        {
            "page": page["page"],
            "title": page["title"],
            "why": [
                {
                    "kind": reason["kind"],
                    "term": reason["term"],
                    "term_kind": reason["term_kind"],
                    "pages": reason.get("pages"),
                    **{
                        side: (reason[side]["claim_id"], *given_of(reason[side]))
                        for side in ("here", "there")
                        if side in reason
                    },
                }
                for reason in page["why"]
            ],
            "more": page["more"],
        }
        for page in answer["related"]
    ]


def as_shown(pages: list[dict]) -> list[dict]:
    """What `shown` read of a page, in the shape of `answered`."""
    return [
        {
            "page": page["page"],
            "title": page["title"],
            "why": [
                {
                    "kind": reason["kind"],
                    "term": reason["term"],
                    "term_kind": reason["term_kind"],
                    "pages": None if reason["pages"] is None else int(reason["pages"]),
                    **{
                        side: (reason[side]["claim_id"], reason[side]["part"], reason[side]["text"])
                        for side in ("here", "there")
                        if side in reason
                    },
                }
                for reason in page["why"]
            ],
            "more": page["more"],
        }
        for page in pages
    ]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """Every project filed in one store, the app over it, and a session opened with a key."""
    logging.getLogger("cairn.access").disabled = True  # one record a page: thousands
    store = Store(tmp_path_factory.mktemp("generated-views") / "cairn.db")
    memory = Memory(store, lint_gate)
    made = []
    for n, pages in enumerate(projects()):
        name = f"p{n:03d}"
        project = store.create_project(name)
        for title, text in pages.items():
            try:
                memory.note(name, text, title=title, topic=title)
            except RequestError:  # no passage to file, or the same text as another page
                continue
        made.append((name, [head.slug for head in store.list_pages(project.id)]))
    key = store.create_key("laptop")
    app = create_app(store, public_url=BASE, graph_explorer=True)
    with TestClient(app, base_url=BASE) as client:
        opened = client.post("/login", data={"key": key}, follow_redirects=False)
        assert opened.status_code == 303
        client.cookies.clear()
        cookie = {"cookie": f"{session_cookie()}={opened.cookies[session_cookie()]}"}
        yield {"projects": made, "memory": memory, "client": client, "cookie": cookie}
    store.close()
    logging.getLogger("cairn.access").disabled = False


def test_the_web_page_shows_the_pages_and_reasons_of_the_answer_in_its_order(world, monkeypatch):
    differ = []
    seen = dict.fromkeys(("pages", "reasons", "mentions", "counted", "left out", "cut"), 0)
    for limit, reasons_given, cut in ASKED:
        bounds(monkeypatch, reasons_given, cut)
        for name, slugs in world["projects"]:
            for slug in slugs:
                answer = world["memory"].related(name, slug, limit)
                r = world["client"].get(
                    f"/b/{name}/p/{slug}/related?limit={limit}", headers=world["cookie"]
                )
                assert r.status_code == 200, (name, slug, limit)
                if as_shown(shown(r.text)) != answered(answer):
                    differ.append((name, slug, limit, reasons_given, cut))
                said = "This is not all of it" in r.text
                if said is answer["searched"]["complete"]:
                    differ.append((name, slug, limit, reasons_given, "what was left out"))
                seen["pages"] += len(answer["related"])
                seen["reasons"] += sum(len(page["why"]) for page in answer["related"])
                seen["left out"] += answer["searched"]["pages_left_out"] > 0
                seen["counted"] += sum(page["more"] > 0 for page in answer["related"])
                for page in answer["related"]:
                    for reason in page["why"]:
                        seen["mentions"] += reason["kind"] == relations.MENTIONED_BY
                        seen["cut"] += sum(
                            bool(reason[side].get("text_cut"))
                            for side in ("here", "there")
                            if side in reason
                        )
    assert differ == [], (len(differ), differ[:5])
    # so the comparison is not kept by answers in which nothing could differ
    least = {
        "pages": 10_000,
        "reasons": 12_000,
        "mentions": 6_000,
        "counted": 400,
        "left out": 1_000,
        "cut": 5_000,
    }
    scale = 1 if is_full() else 8
    assert all(seen[what] >= max(1, count // scale) for what, count in least.items()), seen


def test_the_explorers_view_draws_the_nodes_and_edges_of_the_answer_in_its_order(
    world, monkeypatch
):
    differ, seen = [], dict.fromkeys(("nodes", "edges", "mentions", "more", "left out"), 0)
    for limit, reasons_given, cut in ASKED:
        bounds(monkeypatch, reasons_given, cut)
        for name, slugs in world["projects"]:
            for slug in slugs:
                answer = world["memory"].related(name, slug, limit)
                r = world["client"].get(
                    f"/b/{name}/graph?focus=related%3A{slug}&limit={limit}",
                    headers=world["cookie"],
                )
                assert r.status_code == 200, (name, slug, limit)
                view = drawn(r.text)
                here = "page:" + slug
                nodes = [here, *("page:" + page["page"] for page in answer["related"])]
                edges = [
                    (
                        here,
                        "page:" + page["page"],
                        page["why"][0]["kind"],
                        page["why"][0]["term"],
                        len(page["why"]) - 1 + page["more"],
                    )
                    for page in answer["related"]
                ]
                given = [
                    (edge["from"], edge["to"], edge["reason"], edge["term"], int(edge["more"]))
                    for edge in view.edges
                ]
                where = (name, slug, limit, reasons_given, cut)
                if list(view.nodes) != nodes or view.svg_nodes != nodes:
                    differ.append((*where, "nodes"))
                if given != edges or view.lines != [edge[:2] for edge in edges]:
                    differ.append((*where, "edges"))
                if view.focus != here:
                    differ.append((*where, "the node it is laid out around"))
                titles = [answer["title"], *(page["title"] for page in answer["related"])]
                if [next(iter(links)) for links in view.nodes.values()] != titles:
                    differ.append((*where, "titles"))
                if ("This is not all of it" in view.text) is answer["searched"]["complete"]:
                    differ.append((*where, "what was left out"))
                seen["nodes"] += len(nodes)
                seen["edges"] += len(edges)
                seen["mentions"] += sum(edge[2] == relations.MENTIONED_BY for edge in edges)
                seen["more"] += sum(edge[4] > 0 for edge in edges)
                seen["left out"] += answer["searched"]["pages_left_out"] > 0
    assert differ == [], (len(differ), differ[:5])
    # so the comparison is not kept by views in which nothing could differ
    least = {"nodes": 17_000, "edges": 10_000, "mentions": 6_000, "more": 1_500, "left out": 1_000}
    scale = 1 if is_full() else 8
    assert all(seen[what] >= max(1, count // scale) for what, count in least.items()), seen
