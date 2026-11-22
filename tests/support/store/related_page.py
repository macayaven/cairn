"""Shared test helpers extracted from test_related_page_unit."""

import re
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.server import create_app
from cairn.store import Store
from support.cookie_names import session_cookie
from support.web.design_system import Node, tree

BASE = "http://127.0.0.1:8765"

ISSUER = "https://id.acme.example/pool-1"

WRITTEN = datetime(2099, 2, 3, 4, 5, tzinfo=UTC)

QUIET = {"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE"}


def build(folder, **settings) -> SimpleNamespace:
    folder.mkdir(parents=True, exist_ok=True)
    store = Store(folder / "cairn.db", clock=lambda: WRITTEN)

    def person(who: str) -> SimpleNamespace:
        signed = store.sign_in(
            "sso", f"sub-{who}", f"{who.lower()}@acme.example", who, issuer=ISSUER, join="default"
        )
        view = store.for_account(signed.account_id, signed.user.id)
        return SimpleNamespace(
            name=who,
            user=signed.user,
            cookie={"cookie": f"{session_cookie()}={signed.token}"},
            view=view,
            writes=view.writing_as("person", who),  # as the person's session writes
        )

    ada, brook, cy, dee = (person(who) for who in ("Ada", "Brook", "Cy", "Dee"))
    ops = ada.view.create_project("ops", everyone="read")
    ada.view.set_project_access(ops.id, people={brook.user.id: "write", cy.user.id: "manage"})
    vault = ada.view.create_project("vault", everyone="none")
    client = TestClient(create_app(store, public_url=BASE, **settings), base_url=BASE)
    return SimpleNamespace(
        store=store, client=client, ada=ada, brook=brook, cy=cy, dee=dee, ops=ops, vault=vault
    )


@pytest.fixture
def w(tmp_path):
    made = build(tmp_path)
    with made.client:
        yield made
    made.store.close()


def note(w, title: str, text: str, project: str = "ops", who=None) -> dict:
    memory = Memory((who or w.ada).writes, lint_gate)
    answer = memory.note(project, text, title=title, topic=title)
    assert answer["status"] == "saved", answer
    return answer


def get(w, path: str, who=None):
    w.client.cookies.clear()
    return w.client.get(path, headers=(who or w.ada).cookie, follow_redirects=False)


def main_of(html: str) -> str:
    return html.split('<main id="main" tabindex="-1">', 1)[1].split("</main>", 1)[0]


def flat(text: str) -> str:
    return " ".join(text.split())


def shown(html: str) -> list[dict]:
    """The related pages a response shows, read from its markup alone: each entry's slug, its
    title's link, its reasons (kind, term, kind of term, pages, and the claim on each side:
    what is quoted of it, which part of the claim that is, and what the page says of a
    claim it does not show whole) and how many more reasons it counts."""
    entries = []
    for entry in [n for n in tree(html).walk() if "data-related-page" in n.attrs]:
        (heading,) = entry.all("h2")
        (link,) = heading.all("a")
        reasons = []
        for reason in [n for n in entry.walk() if "data-reason" in n.attrs]:
            sides = {}
            for figure in [n for n in reason.walk() if "data-claim" in n.attrs]:
                lines = figure.all(cls="source-line")  # one quotation at most
                longer = [n for n in figure.walk() if "data-cut" in n.attrs]
                sides[figure.attrs["data-side"]] = {
                    "claim_id": figure.attrs["data-claim"],
                    "text": lines[0].text() if lines else None,
                    "part": figure.attrs["data-part"],  # whole, line, head or none
                    "says": flat(longer[0].text()) if longer else None,
                    "links": [a.attrs["href"] for a in figure.all("a")],
                    "link_says": [flat(a.text()) for a in figure.all("a")],
                }
                assert len(lines) <= 1 and len(longer) <= 1
            reasons.append(
                {
                    "kind": reason.attrs["data-reason"],
                    "term": reason.attrs["data-term"],
                    "term_kind": reason.attrs["data-term-kind"],
                    "pages": reason.attrs.get("data-pages"),
                    "said": flat(reason.all("p")[0].text()),
                    **sides,
                }
            )
        counted = [n.attrs["data-more"] for n in entry.walk() if "data-more" in n.attrs]
        entries.append(
            {
                "page": entry.attrs["data-related-page"],
                "id": entry.attrs["id"],
                "title": link.text(),
                "href": link.attrs["href"],
                "why": reasons,
                "more": int(counted[0]) if counted else 0,
            }
        )
    return entries


HOSTILE = {
    "html": "Asked <script>alert(1)</script> <img src=x onerror=alert(2)> <b>bold</b> & more",
    "fence": "```sh\nrm -rf Asked\n```",
    "link": '[Asked](javascript:alert(3)) and <a href="javascript:alert(4)">Asked</a>',
    "heading": "# Asked\n\nAsked\n=====\n\n- [ ] Asked",
}


def quoted(node: Node) -> list[Node]:
    return [n for n in node.walk() if "data-claim" in n.attrs]


def given_of(claim: dict) -> tuple[str, str | None]:
    """What the page is to show of a claim an answer gives: all of it; or, of a claim that is
    cut, its line where the answer holds one, else the head it holds as its text."""
    if not claim.get("text_cut"):
        return "whole", claim["text"]
    if "line" in claim:
        return "line", claim["line"]
    return ("head", claim["text"]) if "text" in claim else ("none", None)


RLO = "\u202e"  # a right-to-left override, which nothing in these texts closes

ISOLATED = "\u2068[^\u2069]*\u2069"  # between a first-strong isolate and its end

BLOCKS = {"p", "li", "div", "h1", "h2", "h3", "figcaption", "td", "th", "nav", "text", "title"}


def unisolated(html: str, mark: str = RLO) -> list[str]:
    """Each place of a response where text holding `mark` shares its line with other words:
    not in a <bdi>, not alone in an element that begins a line of its own (as a claim's text
    is in the page view), and, in the document's title, not between isolate characters."""
    found = []
    for node in tree(html).walk():
        for child in node.children:
            if not isinstance(child, str) or mark not in child:
                continue
            at = node
            while at is not None and at.tag != "bdi" and at.tag not in BLOCKS:
                at = at.parent
            if at is not None and at.tag == "bdi":
                continue
            if at is not None and at.tag == "title":
                if mark not in re.sub(ISOLATED, "", at.text()):
                    continue
            elif at is not None and at.text().strip() == child.strip():
                continue
            found.append(f"<{node.tag}> {flat(child)[:40]!r}")
    return found


class Watch:
    """The statements the store's connection runs during a call."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.statements: list[str] = []

    def __enter__(self):
        def traced(statement: str) -> None:
            text = statement.lstrip()
            if text and not text.startswith("--") and text.split(None, 1)[0].upper() not in QUIET:
                self.statements.append(text)

        self.statements = []
        self.store._observe(trace=traced)
        return self

    def __exit__(self, *exc) -> None:
        self.store._observe(trace=None)


ADMISSION = 4


def read_alone(w, who, slug: str, limit: int) -> int:
    """The statements `Memory.related` runs for the person, asked directly: the project (2
    for an owner, 3 for a member, whose reach is read from the projects' rules) and the read
    of the page's related pages (cairn.store.relating: 13 for a page of one term, after the
    person's reach, 1 statement for an owner and 2 for a member)."""
    memory = Memory(who.view, lint_gate)
    memory.related("ops", slug, limit)  # once before: what a first call does once
    with Watch(w.store) as seen:
        memory.related("ops", slug, limit)
    return len(seen.statements)
