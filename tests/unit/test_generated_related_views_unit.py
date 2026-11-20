"""What people are shown of related pages, over generated projects: the web page of a page's
related pages shows exactly what `Memory.related` answers, in its order.

The projects are those of tests/unit/test_generated_related_unit.py (its `projects()`: seeded,
the same on every run), which checks the answers themselves; here each answer is compared
with the page made from it, read from the page's markup alone: the related pages in order,
each with its title, its reasons in order (kind, term, kind of term, how many pages hold it),
the claim on each side by its id and its text, character for character, and the count of the
reasons not listed. Asked at the most pages an answer holds, and at 2, where pages are left
out. A failure names project and page numbers, never a note's text.
"""

import logging

import pytest
from starlette.testclient import TestClient
from test_generated_related_unit import projects
from test_related_page_unit import shown

from cairn import relations
from cairn.gate import lint_gate
from cairn.memory import Memory, RequestError
from cairn.server import create_app
from cairn.store import Store
from cookie_names import session_cookie

BASE = "http://127.0.0.1:8765"
FEW = 2  # a limit small enough to leave pages out
# Each pass over every page: (the limit asked, the reasons an answer gives for a page). The
# last gives one reason a page, so that pages count the reasons they do not list: with the
# three an answer gives, no generated page has more.
ASKED = ((relations.RELATED_MAX, relations.WHY_MAX), (FEW, relations.WHY_MAX), (FEW, 1))


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
                        side: (reason[side]["claim_id"], reason[side]["text"])
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
                        side: (reason[side]["claim_id"], reason[side]["text"])
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
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        opened = client.post("/login", data={"key": key}, follow_redirects=False)
        assert opened.status_code == 303
        client.cookies.clear()
        cookie = {"cookie": f"{session_cookie()}={opened.cookies[session_cookie()]}"}
        yield {"projects": made, "memory": memory, "client": client, "cookie": cookie}
    store.close()
    logging.getLogger("cairn.access").disabled = False


def test_the_web_page_shows_the_pages_and_reasons_of_the_answer_in_its_order(world, monkeypatch):
    differ, seen = [], dict.fromkeys(("pages", "reasons", "mentions", "counted", "left out"), 0)
    for limit, reasons_given in ASKED:
        monkeypatch.setattr(relations, "WHY_MAX", reasons_given)
        for name, slugs in world["projects"]:
            for slug in slugs:
                answer = world["memory"].related(name, slug, limit)
                r = world["client"].get(
                    f"/b/{name}/p/{slug}/related?limit={limit}", headers=world["cookie"]
                )
                assert r.status_code == 200, (name, slug, limit)
                if as_shown(shown(r.text)) != answered(answer):
                    differ.append((name, slug, limit, reasons_given))
                said = "This is not all of it" in r.text
                if said is answer["searched"]["complete"]:
                    differ.append((name, slug, limit, reasons_given, "what was left out"))
                seen["pages"] += len(answer["related"])
                seen["reasons"] += sum(len(page["why"]) for page in answer["related"])
                seen["left out"] += answer["searched"]["pages_left_out"] > 0
                seen["counted"] += sum(page["more"] > 0 for page in answer["related"])
                seen["mentions"] += sum(
                    reason["kind"] == relations.MENTIONED_BY
                    for page in answer["related"]
                    for reason in page["why"]
                )
    assert differ == [], (len(differ), differ[:5])
    # so the comparison is not kept by answers in which nothing could differ
    least = {"pages": 7_000, "reasons": 8_000, "mentions": 4_000, "counted": 400, "left out": 1_000}
    assert all(seen[what] >= count for what, count in least.items()), seen
