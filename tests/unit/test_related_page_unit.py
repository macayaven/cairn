"""A page's related pages, as a web page: GET /b/<project>/p/<slug>/related.

It shows what `Memory.related` answers and nothing else: the related pages best first, each
reason in words, the claims a reason rests on quoted as plain text (never drawn as Markdown),
who wrote the notes each claim cites and when, what the read left out, and the refusals. The
page view links it for its latest version and costs no statement more.

The app runs in process over a real store in a temporary file. Ada owns the workspace; "ops"
is hers, read by every member, written by Brook and managed by Cy, so Dee only reads it;
"vault" is Ada's alone and holds the same title and the same term. Made-up names.
"""

import json
import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient
from test_design_system_unit import Node, tree

from cairn import relations
from cairn.gate import lint_gate
from cairn.memory import RELATED_RANGE, Memory
from cairn.server import create_app
from cairn.store import Claim, Store, relating
from cairn.web.ui import RELATED_SHOWN, related_left_out, related_view
from cookie_names import session_cookie

BASE = "http://127.0.0.1:8765"
ISSUER = "https://id.acme.example/pool-1"
WRITTEN = datetime(2099, 2, 3, 4, 5, tzinfo=UTC)
WHEN = "2099-02-03T04:05:00.000000+00:00"
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


@pytest.fixture
def world(w):
    """Deploy states a flag that Retention states too, and Retention's claim names Deploy. The
    vault holds a page of the same title, and a page that states the flag and names it."""
    deploy = note(w, "Deploy", "Run the job with `--audit-keep` set to 7.\n")
    retention = note(w, "Retention", "`--audit-keep` says how many; see Deploy.\n")
    note(w, "Queue", "Brook drains the queue at noon.\n")
    note(w, "Deploy", "Juniper runs it with `--audit-keep` set to 9.\n", project="vault")
    note(w, "Cellar", "Juniper keeps `--audit-keep` low; see Deploy.\n", project="vault")
    return SimpleNamespace(deploy=deploy, retention=retention)


def claim_of(w, slug: str, text: str, project=None) -> Claim:
    page = w.store.get_page((project or w.ops).id, slug)
    (held,) = [claim for claim in page.claims if claim.text == text]
    return held


# What it shows


def test_it_shows_each_related_page_and_each_reason_in_words_with_the_claims(w, world):
    r = get(w, "/b/ops/p/deploy/related")
    assert r.status_code == 200
    ours = claim_of(w, "deploy", "Run the job with `--audit-keep` set to 7.")
    theirs = claim_of(w, "retention", "`--audit-keep` says how many; see Deploy.")
    (page,) = shown(r.text)
    assert (page["page"], page["title"], page["href"]) == (
        "retention",
        "Retention",
        "/b/ops/p/retention",
    )
    assert (page["id"], page["more"]) == ("related-retention", 0)
    mention, shares = page["why"]
    assert (mention["kind"], mention["term"], mention["term_kind"]) == (
        "mentioned_by",
        "Deploy",
        "title",
    )
    assert mention["said"] == "A claim of Retention names this page's title, Deploy."
    assert "here" not in mention and mention["pages"] is None
    assert mention["there"]["claim_id"] == theirs.id and mention["there"]["text"] == theirs.text
    assert (shares["kind"], shares["term"], shares["term_kind"], shares["pages"]) == (
        "shares",
        "--audit-keep",
        "inline_code",
        "2",
    )
    assert shares["said"] == "Both pages state the code --audit-keep. 2 pages of ops hold it."
    assert (shares["here"]["claim_id"], shares["here"]["text"]) == (ours.id, ours.text)
    assert (shares["there"]["claim_id"], shares["there"]["text"]) == (theirs.id, theirs.text)
    # each claim is linked to its claim on its page
    assert shares["here"]["links"] == [f"/b/ops/p/deploy#{ours.id}"]
    assert shares["there"]["links"] == [f"/b/ops/p/retention#{theirs.id}"]
    assert mention["there"]["links"] == [f"/b/ops/p/retention#{theirs.id}"]
    # the term is code, and the page says what it is the related pages of
    html = main_of(r.text)
    assert "Both pages state the code <code>--audit-keep</code>." in html
    assert "<h1>Pages related to Deploy</h1>" in html
    assert '<a href="/b/ops/p/deploy">Deploy</a>' in html
    assert "1 related page, for version 1 of Deploy, its latest." in flat(tree(html).text())
    assert "This is not all of it" not in html and "limit=" not in html


def test_it_is_what_memory_related_answers_in_order(w, world):
    note(w, "Asked", "Use `a.cfg`, `b.cfg` and `c.cfg`.\n")
    note(w, "Two", "Use `a.cfg` and `b.cfg`.\n")
    note(w, "One", "Use `c.cfg`; see Asked.\n")
    answer = Memory(w.ada.view, lint_gate).related("ops", "asked", RELATED_SHOWN)
    pages = shown(get(w, "/b/ops/p/asked/related").text)
    assert [page["page"] for page in pages] == [page["page"] for page in answer["related"]]
    for page, given in zip(pages, answer["related"], strict=True):
        assert (page["title"], page["more"]) == (given["title"], given["more"])
        assert [(r["kind"], r["term"], r["term_kind"]) for r in page["why"]] == [
            (r["kind"], r["term"], r["term_kind"]) for r in given["why"]
        ]
        for reason, why in zip(page["why"], given["why"], strict=True):
            assert reason["there"]["claim_id"] == why["there"]["claim_id"]
            assert reason["there"]["text"] == why["there"]["text"]
            assert ("here" in reason) is ("here" in why)


@pytest.mark.parametrize(
    ("kind", "text", "word"),
    [
        ("url", "See https://example.invalid/quill first.", "URL"),
        ("path", "Brook keeps ~/quill/notes tidy.", "path"),
        ("flag", "Ada passes --dry-run first.", "flag"),
        ("version", "Quill is at v2.4.1 now.", "version"),
    ],
)
def test_a_shared_term_is_said_with_its_kind(w, kind, text, word):
    note(w, "Asked", text + "\n")
    note(w, "Other", text.replace("first", "again").replace("now", "today") + " Noted.\n")
    (page,) = shown(get(w, "/b/ops/p/asked/related").text)
    (reason,) = page["why"]
    assert reason["term_kind"] == kind
    said = f"Both pages state the {word} {reason['term']}. 2 pages of ops hold it."
    assert reason["said"] == said


def test_a_page_counts_the_reasons_it_does_not_list(w):
    terms = " ".join(f"`t{n}.cfg`" for n in range(5))
    note(w, "Asked", f"Use {terms}.\n")
    note(w, "Twin", f"Brook uses {terms} too.\n")
    note(w, "Near", "Brook also keeps `t0.cfg` and `t1.cfg` and `t2.cfg` and `t3.cfg`.\n")
    r = get(w, "/b/ops/p/asked/related")
    twin, near = shown(r.text)
    assert (twin["page"], len(twin["why"]), twin["more"]) == ("twin", relations.WHY_MAX, 2)
    assert (near["page"], near["more"]) == ("near", 1)
    said = flat(tree(main_of(r.text)).text())
    assert "and 2 more reasons" in said and "and 1 more reason " in said + " "
    assert "2 related pages, for version 1 of Asked, its latest." in said


# Who wrote it, and when


def test_under_each_claim_who_wrote_its_notes_and_when_in_the_page_views_words(w, world):
    r = get(w, "/b/ops/p/deploy/related")
    told = [flat(p.text()) for p in tree(main_of(r.text)).all("p", "written")]
    assert told == ["written by Ada \N{MIDDLE DOT} written 3 February 2099, 04:05 UTC"] * 3
    assert f'<time datetime="{WHEN}">3 February 2099, 04:05 UTC</time>' in r.text
    # the same words as the page view says under the claim's source
    assert '<span class="writer">written by Ada</span>' in get(w, "/b/ops/p/deploy").text
    assert '<span class="writer">written by Ada</span>' in r.text
    assert "outside text" not in r.text


def test_a_claim_of_outside_text_says_so_and_names_each_writer_once(w):
    mail = w.store.writing_as("source", "team-mail")
    brought, _ = mail.add_note(w.ops.id, "# Mail\n\nSet `x.cfg` first.\n", via="mail")
    ours = [w.ada.writes.add_note(w.ops.id, f"# N{n}\n\nSet `x.cfg` first.\n")[0] for n in range(4)]
    spans = [
        next(s.id for s in w.store.note_spans(held.id) if s.kind == "paragraph")
        for held in (brought, *ours)
    ]
    folded = Claim(id="cl_folded", text="Set `x.cfg` first.", span_ids=tuple(spans))
    w.ada.view.save_page(w.ops.id, "setup", "Setup", "", [folded])
    note(w, "Asked", "Then read `x.cfg` again.\n")
    r = get(w, "/b/ops/p/asked/related")
    here, there = (flat(p.text()) for p in tree(main_of(r.text)).all("p", "written"))
    assert here == "written by Ada \N{MIDDLE DOT} written 3 February 2099, 04:05 UTC"
    assert there == (
        "from the source team-mail, written by Ada and 2 more notes \N{MIDDLE DOT} the newest"
        " written 3 February 2099, 04:05 UTC \N{MIDDLE DOT} outside text"
    )
    assert r.text.count('<span class="outside">outside text</span>') == 1


def test_a_claim_whose_notes_are_out_of_reach_names_no_one(w):
    note(w, "Quill", "Ada wrote it.\n")
    w.ada.view.save_page(
        w.ops.id,
        "bare",
        "Bare",
        "",
        [Claim(id="cl_bare", text="Quill ships on Friday.", span_ids=())],
    )
    r = get(w, "/b/ops/p/quill/related")
    (told,) = [flat(p.text()) for p in tree(main_of(r.text)).all("p", "written")]
    assert told == "It cites no note in your reach."
    assert "<time" not in main_of(r.text)


LONGER = "This claim is longer than what is shown: "


def test_the_view_shows_a_claim_by_what_its_answer_holds_and_never_assumes_its_text():
    """The answer is made by hand, in each shape a claim can take: whole, with its text; cut,
    with the line that states the term or names the title and no text; cut, with a head as
    its text where no line could be given; cut, with both, as an earlier answer gave it (the
    line is shown); and cut with neither. And a Slack message someone filed says whose words
    it holds."""
    filed = "A Slack message this writer filed: its words are its author's."

    def claim(n: int, **more) -> dict:
        return {
            "claim_id": f"cl_{n}",
            "passage_ids": [],
            "outside": False,
            "written_by": [],
            "written_at": None,
            **more,
        }

    def reason(kind: str, **sides) -> dict:
        shares = {"term": "x.cfg", "term_kind": "inline_code", "pages": 3}
        mention = {"term": "Deploy", "term_kind": "title"}
        return {"kind": kind, **(shares if kind == "shares" else mention), **sides}

    by = {"note": "n1", "kind": "person", "name": "Ada", "via": "slack", "outside": True}
    whole = claim(
        3,
        text="text 3",
        outside=True,
        written_by=[by | {"filed_message": filed}],
        written_by_more=1,
        written_at=WHEN,
    )
    answer = {
        "project": "ops",
        "page": "deploy",
        "title": "Deploy",
        "version": 4,
        "related": [
            {
                "page": "other",
                "title": "Other",
                "version": 2,
                "why": [
                    reason(
                        "mentioned_by",
                        there=claim(1, text_cut=True, line="see Deploy", line_cut=True),
                    ),
                    reason(
                        "shares",
                        here=claim(2, text_cut=True, line="use `x.cfg`"),
                        there=whole,
                    ),
                    reason(
                        "shares",
                        here=claim(4, text_cut=True, text="a head of the claim"),
                        there=claim(5, text_cut=True, text="its head", line="its line"),
                    ),
                    reason("mentioned_by", there=claim(6, text_cut=True)),
                ],
                "more": 0,
            }
        ],
        "searched": {
            "complete": True,
            "terms_looked_up": 1,
            "terms_not_looked_up": 0,
            "claims_read": 1,
            "claims_not_read": 0,
            "pages_left_out": 0,
        },
    }
    view = related_view(answer, 8)
    (page,) = view["related"]
    mention, lined, headed, bare = page["reasons"]

    def said(quoted: dict) -> tuple:
        return quoted["part"], quoted["text"], quoted["says"]

    assert said(mention["there"]) == (
        "line",
        "see Deploy",
        LONGER + "this is part of the line that names the title.",
    )
    assert said(lined["here"]) == (
        "line",
        "use `x.cfg`",
        LONGER + "this is the line of its passage that states the term.",
    )
    assert said(lined["there"]) == ("whole", "text 3", None)
    assert said(headed["here"]) == (
        "head",
        "a head of the claim",
        LONGER + "these are its first 19 characters.",
    )
    assert said(headed["there"])[:2] == ("line", "its line")  # a line, where there is one
    assert said(bare["there"]) == ("none", None, "This claim is longer than can be shown here.")
    assert [q["cut"] for q in (mention["there"], lined["there"], bare["there"])] == [
        True,
        False,
        True,
    ]
    assert lined["there"]["writers"] == [f"written by Ada: {filed}"]
    assert (lined["there"]["notes"], lined["there"]["notes_more"]) == (2, 1)
    assert mention["there"]["writers"] == [] and mention["there"]["notes"] == 0
    assert (view["left_out"], view["more_href"]) == (None, None)


def test_the_page_draws_each_shape_of_a_claim_and_links_the_whole_one(w, world, monkeypatch):
    """The same shapes through the page: a claim not shown whole says so, quotes the line or
    the head it was given, or nothing, and its one link reads the whole claim on its page."""
    whole = Memory(w.ada.view, lint_gate).related("ops", "deploy", RELATED_SHOWN)
    (page,) = whole["related"]
    mention, shares = page["why"]
    mention["there"] = {**mention["there"], "text_cut": True, "line": "see  Deploy\t<b>now</b>"}
    del mention["there"]["text"]
    shares["here"] = {**shares["here"], "text_cut": True}
    shares["there"] = {**shares["there"], "text_cut": True}
    del shares["there"]["text"]
    monkeypatch.setattr(Memory, "related", lambda self, project, slug, limit: whole)
    r = get(w, "/b/ops/p/deploy/related")
    assert r.status_code == 200
    (entry,) = shown(r.text)
    named, shared = entry["why"]
    assert (named["there"]["part"], named["there"]["text"]) == ("line", "see  Deploy\t<b>now</b>")
    assert named["there"]["says"] == LONGER + "this is the line that names the title."
    assert (shared["here"]["part"], shared["here"]["text"]) == ("head", shares["here"]["text"])
    assert shared["here"]["says"] == (
        LONGER + f"these are its first {len(shares['here']['text'])} characters."
    )
    assert (shared["there"]["part"], shared["there"]["text"]) == ("none", None)
    assert shared["there"]["says"] == "This claim is longer than can be shown here."
    for quoted_claim, title in (
        (named["there"], "Retention"),
        (shared["here"], "Deploy"),
        (shared["there"], "Retention"),
    ):
        assert quoted_claim["link_says"] == [f"Read the whole claim on {title}"]
        assert len(quoted_claim["links"]) == 1 and "#cl_" in quoted_claim["links"][0]


# Evidence is quoted, never rendered


HOSTILE = {
    "html": "Asked <script>alert(1)</script> <img src=x onerror=alert(2)> <b>bold</b> & more",
    "fence": "```sh\nrm -rf Asked\n```",
    "link": '[Asked](javascript:alert(3)) and <a href="javascript:alert(4)">Asked</a>',
    "heading": "# Asked\n\nAsked\n=====\n\n- [ ] Asked",
}
QUOTING = {"figure", "figcaption", "strong", "a", "div", "span"}  # what a quoted claim is made of


def quoted(node: Node) -> list[Node]:
    return [n for n in node.walk() if "data-claim" in n.attrs]


def test_a_claims_text_is_shown_as_plain_text_and_nothing_of_it_is_markup(w):
    note(w, "Asked", "Brook asked first.\n")
    for slug, text in HOSTILE.items():
        claim = Claim(id=f"cl_{slug}", text=text, span_ids=())
        w.ada.view.save_page(w.ops.id, slug, f"Page {slug}", "", [claim])
    r = get(w, "/b/ops/p/asked/related")
    pages = {page["page"]: page for page in shown(r.text)}
    assert sorted(pages) == sorted(HOSTILE)
    for slug, text in HOSTILE.items():
        (reason,) = pages[slug]["why"]
        assert reason["there"]["text"] == text, slug  # every character, white space kept
    body = tree(main_of(r.text))
    for figure in quoted(body):
        (line,) = figure.all(cls="source-line")
        assert line.kids() == [], figure.attrs["data-claim"]  # text alone: no element in it
        assert {n.tag for n in figure.walk()} <= QUOTING, figure.attrs["data-claim"]
    drawn = {"script", "img", "b", "pre", "input", "em", "h3", "h4", "h5", "h6", "blockquote"}
    assert [n.tag for n in body.walk() if n.tag in drawn] == []
    assert len(body.all("h1")) == 1 and len(body.all("h2")) == len(HOSTILE)  # the page's own
    assert not [a for a in body.all("a") if not a.attrs["href"].startswith(("/", "#"))]
    assert "<script" not in r.text and "javascript:" not in " ".join(
        value for n in tree(r.text).walk() for value in n.attrs.values()
    )
    # the page view draws the same claims' Markdown; this page never does
    assert "<pre>" in get(w, "/b/ops/p/fence").text and "<pre" not in r.text


def given_of(claim: dict) -> tuple[str, str | None]:
    """What the page is to show of a claim an answer gives: all of it; or, of a claim that is
    cut, its line where the answer holds one, else the head it holds as its text."""
    if not claim.get("text_cut"):
        return "whole", claim["text"]
    if "line" in claim:
        return "line", claim["line"]
    return ("head", claim["text"]) if "text" in claim else ("none", None)


def test_a_claim_cut_inside_a_fence_or_a_link_draws_nothing_and_says_it_is_longer(w):
    """Claims saved by hand, which cite no passage, so no line of a passage can be given for
    them: what is quoted of each stops inside a fence or a link."""
    note(w, "Asked", "Brook asked first.\n")
    long = {
        "fenced": "<b>Asked</b> ```sh\n" + "quill sync --all\n" * (relations.TEXT_MAX // 8),
        "linked": "See Asked, then [the " + "long " * relations.TEXT_MAX + "](https://x.invalid)",
    }
    for slug, text in long.items():
        claim = Claim(id=f"cl_{slug}", text=text, span_ids=())
        w.ada.view.save_page(w.ops.id, slug, f"Page {slug}", "", [claim])
    answer = Memory(w.ada.view, lint_gate).related("ops", "asked", RELATED_SHOWN)
    given = {page["page"]: page["why"][0]["there"] for page in answer["related"]}
    r = get(w, "/b/ops/p/asked/related")
    pages = {page["page"]: page for page in shown(r.text)}
    body = tree(main_of(r.text))
    figures = {figure.attrs["data-claim"]: figure for figure in quoted(body)}
    for slug, text in long.items():
        (reason,) = pages[slug]["why"]
        there = reason["there"]
        assert given[slug]["text_cut"] is True and len(text) > relations.TEXT_MAX
        assert (there["part"], there["text"]) == given_of(given[slug]), slug
        assert there["text"] and text.startswith(there["text"]) and there["text"] != text
        assert there["says"].startswith(LONGER), slug
        assert there["links"] == [f"/b/ops/p/{slug}#cl_{slug}"]
        assert there["link_says"] == [f"Read the whole claim on Page {slug}"]
        figure = figures[f"cl_{slug}"]
        assert {n.tag for n in figure.walk()} <= QUOTING, slug
    assert not [a for a in body.all("a") if "x.invalid" in a.attrs["href"]]
    assert body.all("b") == [] and body.all("pre") == []


def test_a_claim_cut_is_shown_by_the_line_that_states_the_term(w):
    lines = [f"Brook checks ledger {n} at dawn." for n in range(relations.TEXT_MAX // 20)]
    note(w, "Asked", "Ada reads `quill.lock` daily.\n")
    note(w, "Long", "\n".join([*lines, "Ada pins `quill.lock`."]) + "\n")
    r = get(w, "/b/ops/p/asked/related")
    (page,) = shown(r.text)
    (reason,) = page["why"]
    assert (reason["here"]["part"], reason["here"]["says"]) == ("whole", None)
    assert reason["here"]["link_says"] == ["Open this claim on Asked"]
    there = reason["there"]
    assert (there["part"], there["text"]) == ("line", "Ada pins `quill.lock`.")
    assert there["says"] == LONGER + "this is the line of its passage that states the term."
    assert there["link_says"] == ["Read the whole claim on Long"]
    assert "Brook checks ledger" not in r.text  # the line, and not the claim's head beside it


def test_a_long_claim_that_names_the_title_is_shown_as_its_answer_gives_it(w):
    """A claim of a real note, cut, that mentions the page: the page shows the line that names
    the title where the answer gives one, and else the head it gives."""
    filler = "Ada waits for the nightly log, " * (relations.TEXT_MAX // 20)
    note(w, "Quill ledger", "Brook balances it at dusk.\n")
    note(w, "Long", filler + "then opens the Quill ledger.\n")
    answer = Memory(w.ada.view, lint_gate).related("ops", "quill-ledger", RELATED_SHOWN)
    given = answer["related"][0]["why"][0]["there"]
    assert given["text_cut"] is True
    (page,) = shown(get(w, "/b/ops/p/quill-ledger/related").text)
    (reason,) = page["why"]
    there = reason["there"]
    assert (there["part"], there["text"]) == given_of(given) and there["part"] in ("line", "head")
    wanted = "names the title" if there["part"] == "line" else "these are its first"
    assert there["says"].startswith(LONGER) and wanted in there["says"]
    assert there["link_says"] == ["Read the whole claim on Long"]


# What the read left out, and no related page


def test_pages_past_the_limit_are_counted_with_a_link_to_show_up_to_twenty(w):
    note(w, "Asked", "Use `a.cfg`, `b.cfg` and `c.cfg`.\n")
    note(w, "Two", "Use `a.cfg` and `b.cfg`.\n")
    note(w, "One", "Use `c.cfg`.\n")
    r = get(w, "/b/ops/p/asked/related?limit=1")
    assert [page["page"] for page in shown(r.text)] == ["two"]
    (said,) = tree(main_of(r.text)).all("p", "searched")
    assert flat(said.text()) == (
        "This is not all of it: 1 more related page is not shown. Show up to 20"
    )
    assert [a.attrs["href"] for a in said.all("a")] == ["/b/ops/p/asked/related?limit=20"]
    assert relations.RELATED_MAX == 20
    every = get(w, "/b/ops/p/asked/related?limit=20")
    assert [page["page"] for page in shown(every.text)] == ["two", "one"]
    assert tree(main_of(every.text)).all("p", "searched") == []
    # 8 unless asked
    assert get(w, "/b/ops/p/asked/related").text == get(w, "/b/ops/p/asked/related?limit=8").text


def test_an_answer_that_is_not_complete_says_what_was_not_looked_at_from_its_counts(w, monkeypatch):
    monkeypatch.setattr(relating, "CLAIMS_MAX", 2)
    monkeypatch.setattr(relating, "TERMS_MAX", 2)
    note(w, "Asked", "Use `a.cfg` and `b.cfg` and `long-name.cfg`.\n")
    note(w, "Few", "Set `a.cfg` once.\n")
    note(w, "Many", "\n".join(f"- Line {n} sets `b.cfg`." for n in range(3)) + "\n")
    answer = Memory(w.ada.view, lint_gate).related("ops", "asked", RELATED_SHOWN)
    assert answer["searched"] == {
        "complete": False,
        "terms_looked_up": 2,
        "terms_not_looked_up": 1,
        "claims_read": 1,
        "claims_not_read": 3,
        "pages_left_out": 0,
    }
    r = get(w, "/b/ops/p/asked/related")
    (said,) = tree(main_of(r.text)).all("p", "searched")
    assert flat(said.text()) == (
        "This is not all of it: 1 of the page's 3 terms was not looked up and 3 claims of other"
        " pages that may state one of its terms were not read."
    )
    assert said.all("a") == []  # no page was left out: nothing more to show


@pytest.mark.parametrize(
    ("counts", "said"),
    [
        ((0, 0, 0), None),
        ((2, 0, 0), "This is not all of it: 2 of the page's 258 terms were not looked up."),
        (
            (0, 1, 0),
            "This is not all of it: 1 claim of other pages that may state one of its terms was"
            " not read.",
        ),
        ((0, 0, 4), "This is not all of it: 4 more related pages are not shown."),
        (
            (1, 2, 3),
            "This is not all of it: 1 of the page's 257 terms was not looked up, 2 claims of"
            " other pages that may state one of its terms were not read and 3 more related pages"
            " are not shown.",
        ),
    ],
)
def test_the_sentence_of_what_was_left_out(counts, said):
    terms, claims, pages = counts
    searched = {
        "complete": not any(counts),
        "terms_looked_up": 256,
        "terms_not_looked_up": terms,
        "claims_read": 9,
        "claims_not_read": claims,
        "pages_left_out": pages,
    }
    assert related_left_out(searched) == said


def test_an_answer_that_only_says_it_is_not_complete_is_still_said_not_to_be(w):
    """No count says why: the page does not then pass the answer off as whole. And a kind of
    term this page has no word for is a term."""
    searched = {
        "complete": False,
        "terms_looked_up": 3,
        "terms_not_looked_up": 0,
        "claims_read": 2,
        "claims_not_read": 0,
        "pages_left_out": 0,
    }
    assert related_left_out(searched) == "This is not all of it."
    claim = {
        "claim_id": "cl_1",
        "text": "Quill listens on 8443.",
        "passage_ids": [],
        "outside": False,
        "written_by": [],
        "written_at": None,
    }
    reason = {"kind": "shares", "term": "8443", "term_kind": "port", "pages": 2}
    answer = {
        "project": "ops",
        "page": "deploy",
        "title": "Deploy",
        "version": 1,
        "related": [
            {
                "page": "other",
                "title": "Other",
                "version": 1,
                "why": [reason | {"here": claim, "there": claim}],
                "more": 0,
            }
        ],
        "searched": searched,
    }
    view = related_view(answer, RELATED_SHOWN)
    assert view["left_out"] == "This is not all of it." and view["more_href"] is None
    assert view["related"][0]["reasons"][0]["term_word"] == "term"


def test_with_no_related_page_one_sentence_says_so_and_what_relates_two_pages(w):
    note(w, "Queue", "Brook drains the queue at noon with `true` set.\n")
    r = get(w, "/b/ops/p/queue/related")
    assert r.status_code == 200 and shown(r.text) == []
    body = tree(main_of(r.text))
    (empty,) = body.all("p", "empty")
    assert flat(empty.text()) == (
        "No page of ops is related to Queue: two pages are related when a claim of each states"
        " the same term (a URL, a path, a flag, a version, or code that is not a plain word), or"
        " when a claim of the other names this page's title."
    )
    assert body.all("ul") == [] and body.all("ol") == []  # no empty list
    assert body.all("p", "searched") == []


# Limits and refusals


@pytest.mark.parametrize("limit", ["0", "21", "-1", "many", "2.5"])
def test_any_other_limit_is_refused_with_rests_sentence_as_a_page(w, world, limit):
    refused = get(w, f"/b/ops/p/deploy/related?limit={limit}")
    by_rest = w.client.get(
        f"/api/projects/ops/pages/deploy/related?limit={limit}",
        headers={"x-api-key": w.store.create_key("laptop")},
    )
    assert (by_rest.status_code, by_rest.json()) == (400, {"error": RELATED_RANGE})
    assert refused.status_code == 400
    assert "text/html" in refused.headers["content-type"]
    (heading,) = tree(main_of(refused.text)).all("h1")
    assert heading.text() == RELATED_RANGE + "."


def test_what_the_page_does_not_take_it_ignores_and_an_empty_limit_is_the_default(w, world):
    plain = get(w, "/b/ops/p/deploy/related")
    for query in ("?version=1", "?extra=1&q=x", "?limit="):
        assert get(w, "/b/ops/p/deploy/related" + query).text == plain.text, query


def test_a_page_that_is_not_there_and_a_project_out_of_reach_answer_as_the_page_view(w, world):
    for path, who in (
        ("/b/ops/p/nowhere", w.ada),  # no such page
        ("/b/ops/p/Deploy", w.ada),  # a page is named by its slug
        ("/b/nowhere/p/deploy", w.ada),  # no such project
        ("/b/vault/p/deploy", w.brook),  # a project out of Brook's reach
        ("/b/vault/p/deploy", w.dee),
    ):
        viewed, related = get(w, path, who), get(w, path + "/related", who)
        assert viewed.status_code == related.status_code == 404, path
        assert main_of(related.text) == main_of(viewed.text), path
        assert "Juniper" not in related.text and "nearest" not in related.text
    hidden, missing = (
        get(w, "/b/vault/p/deploy/related", w.brook),
        get(w, "/b/nowhere/p/deploy/related", w.brook),
    )
    assert main_of(hidden.text).replace("vault", "X") == main_of(missing.text).replace(
        "nowhere", "X"
    )
    assert get(w, "/b/vault/p/deploy/related", w.ada).status_code == 200  # hers


def test_a_page_the_read_does_not_find_is_refused_in_the_page_views_words_whatever_follows(
    w, world, monkeypatch
):
    """The read of related pages says the page is not there, and the page view's own check
    then finds it (a page filed between the two): the answer is still the page view's
    sentence for a page that is not there, never the agents' sentence with the nearest names."""
    monkeypatch.setattr(Store, "related_pages", lambda self, project_id, slug, limit: None)
    refused = get(w, "/b/ops/p/deploy/related")
    assert refused.status_code == 404 and get(w, "/b/ops/p/deploy").status_code == 200
    (heading,) = tree(main_of(refused.text)).all("h1")
    assert heading.text() == "There is no page 'deploy' in the project 'ops'."
    assert "nearest" not in refused.text and "named by its slug" not in refused.text
    missing = get(w, "/b/ops/p/nowhere/related")
    assert main_of(missing.text) == main_of(get(w, "/b/ops/p/nowhere").text)


def test_signed_out_it_leads_to_sign_in_as_the_page_view_does(w, world):
    w.client.cookies.clear()
    related = w.client.get("/b/ops/p/deploy/related", follow_redirects=False)
    viewed = w.client.get("/b/ops/p/deploy", follow_redirects=False)
    assert related.status_code == viewed.status_code == 303
    assert "audit-keep" not in related.text


# Who sees it


def test_a_manager_a_member_a_reader_and_a_keys_session_see_the_same_page(w, world):
    owner = get(w, "/b/ops/p/deploy/related", w.ada)
    assert owner.status_code == 200
    for who in (w.cy, w.brook, w.dee):  # manages, writes, only reads
        seen = get(w, "/b/ops/p/deploy/related", who)
        assert seen.status_code == 200, who.name
        assert main_of(seen.text) == main_of(owner.text), who.name

    def opened_with(key: str):
        w.client.cookies.clear()
        return w.client.post("/login", data={"key": key}, follow_redirects=False)

    opened = opened_with(w.store.create_key("laptop"))
    assert opened.status_code == 303
    session = SimpleNamespace(
        cookie={"cookie": f"{session_cookie()}={opened.cookies[session_cookie()]}"}
    )
    seen = get(w, "/b/ops/p/deploy/related", session)
    assert seen.status_code == 200 and main_of(seen.text) == main_of(owner.text)
    # A key narrowed to some projects, or read-only, opens no session at all (a session has
    # the whole workspace), so no page is shown to one: it reads `related` over REST.
    for label, reach, access in (("narrow", [w.ops.id], "write"), ("reader", None, "read")):
        key = w.store.create_key(label, projects=reach, access=access)
        assert opened_with(key).status_code == 401, label
        by_rest = w.client.get("/api/projects/ops/pages/deploy/related", headers={"x-api-key": key})
        assert by_rest.status_code == 200, label


def test_another_projects_page_of_the_same_title_and_term_never_appears(w, world):
    """Ada reaches the vault too: its Deploy and its Cellar state the flag and name the title,
    and neither is shown, read or counted among the pages that hold the term."""
    r = get(w, "/b/ops/p/deploy/related?limit=20", w.ada)
    (page,) = shown(r.text)
    assert page["page"] == "retention" and page["href"] == "/b/ops/p/retention"
    assert [reason["pages"] for reason in page["why"]] == [None, "2"]
    assert "Juniper" not in r.text and "cellar" not in r.text and "/b/vault" not in r.text
    theirs = shown(get(w, "/b/vault/p/deploy/related", w.ada).text)
    assert [page["page"] for page in theirs] == ["cellar"]
    assert "/b/ops" not in main_of(get(w, "/b/vault/p/deploy/related", w.ada).text)


# The read is recorded as Memory.related records it


def test_the_page_is_one_read_of_the_page_on_both_logs_and_names_no_term(w, world, caplog):
    def reads() -> list:
        return [e for e in w.store.list_audit(1000) if e.action.startswith("read.")]

    assert get(w, "/b/ops/p/deploy/related", w.dee).status_code == 200
    assert reads() == []  # off unless the server audits reads
    w.store.reads_audited = True
    with caplog.at_level(logging.INFO, logger="cairn.access"):
        assert get(w, "/b/ops/p/deploy/related", w.dee).status_code == 200
        assert get(w, "/b/ops/p/nowhere/related", w.dee).status_code == 404
        assert get(w, "/b/ops/p/deploy/related?limit=0", w.dee).status_code == 400
    (event,) = reads()
    assert (event.action, event.project) == ("read.page", "ops")
    assert event.detail == {"page": "deploy", "user": w.dee.user.id}  # the page, and who
    assert (event.actor_kind, event.actor) == ("session", "Dee")
    assert "audit-keep" not in json.dumps([e.model_dump() for e in w.store.list_audit(1000)])
    records = [r for r in caplog.records if r.name == "cairn.access"]
    assert [(r.action, r.project, r.status) for r in records] == [
        ("related", "ops", 200),
        ("related", "ops", 404),
        ("related", None, 400),
    ]
    assert not any("audit-keep" in r.getMessage() for r in records)


# A forget


def test_forgetting_a_note_takes_its_words_and_its_relations_in_the_next_request(w):
    note(w, "Asked", "Use `a.cfg` here.\n\nBrook reads `b.cfg` daily.\n")
    held = note(w, "Held", "The canary line sets `a.cfg`; see Asked.\n")
    note(w, "Held", "Ada also keeps `b.cfg` warm.\n")  # the same page, another note
    before = get(w, "/b/ops/p/asked/related")
    (page,) = shown(before.text)
    assert [reason["term"] for reason in page["why"]] == ["Asked", "a.cfg", "b.cfg"]
    assert "canary" in before.text
    Memory(w.ada.view, lint_gate).forget("ops", held["note_id"])
    after = get(w, "/b/ops/p/asked/related")
    (page,) = shown(after.text)
    assert [reason["term"] for reason in page["why"]] == ["b.cfg"]
    assert "canary" not in after.text and held["note_id"] not in after.text
    latest = {c.id for slug in ("asked", "held") for c in w.store.get_page(w.ops.id, slug).claims}
    assert {figure.attrs["data-claim"] for figure in quoted(tree(after.text))} <= latest


# The page view links it, for the latest version only, and costs what it did


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


def test_the_page_view_links_it_from_its_head_for_the_latest_version_only(w, world):
    note(w, "Deploy", "Then tag the release.\n")  # version 2
    latest = get(w, "/b/ops/p/deploy")
    (head,) = tree(latest.text).all("header", "page-head")
    (link,) = [a for a in head.all("a") if a.text() == "Related pages"]
    assert link.attrs["href"] == "/b/ops/p/deploy/related"
    assert "Related pages" in get(w, "/b/ops/p/deploy?version=2").text  # the latest, by number
    older = get(w, "/b/ops/p/deploy?version=1")
    assert older.status_code == 200 and "Related pages" not in older.text
    assert "/related" not in older.text


# What GET /b/<project>/p/<slug> ran for this world before it had the link, counted at
# b1f04444: for the owner, and for a member who only reads the project.
PAGE_VIEW_STATEMENTS = {"Ada": 19, "Dee": 27}
# The statements that admit a person's session, before any page is read: the session, the
# workspace, the membership and the person.
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


def test_the_page_view_runs_the_statements_it_ran_and_the_related_page_a_fixed_number(w, world):
    counts = {}
    for path in ("/b/ops/p/deploy", "/b/ops/p/deploy/related"):
        for who in (w.ada, w.dee):
            assert get(w, path, who).status_code == 200  # once before: what a first call does once
            with Watch(w.store) as seen:
                assert get(w, path, who).status_code == 200
            counts[path, who.name] = len(seen.statements)
    for who, ran in PAGE_VIEW_STATEMENTS.items():
        assert counts["/b/ops/p/deploy", who] == ran, who
    # the related page runs its admission and the read of `related`, and nothing else: 20
    # statements here for the owner and 22 for the reader, about what the page view runs
    for who in (w.ada, w.dee):
        alone = read_alone(w, who, "deploy", RELATED_SHOWN)
        assert counts["/b/ops/p/deploy/related", who.name] == ADMISSION + alone, who.name


def test_the_related_page_costs_the_same_in_a_larger_project(tmp_path):
    """Statements, never time: the page of a project of 4 pages and of one of 40 runs as many,
    and no more than the page's admission (4), its project (2), the owner's reach (1) and the
    most a read of `related` runs (16: cairn.store.relating)."""
    counts = []
    for size in (4, 40):
        made = build(tmp_path / str(size))
        try:
            with made.client:
                note(made, "Asked", "Use `a.cfg` and `b.cfg`; see https://example.invalid/q.\n")
                note(made, "Twin", "Brook sets `a.cfg`; see Asked.\n")
                for n in range(size):
                    note(made, f"Shelf {n}", f"Shelf {n} holds quill spares and `s{n}.cfg`.\n")
                assert get(made, "/b/ops/p/asked/related").status_code == 200
                with Watch(made.store) as seen:
                    r = get(made, "/b/ops/p/asked/related")
                assert [page["page"] for page in shown(r.text)] == ["twin"]
                counts.append(len(seen.statements))
        finally:
            made.store.close()
    assert counts[0] == counts[1] and counts[1] <= ADMISSION + 2 + 1 + 16, counts


# With and without the explorer


@pytest.mark.parametrize("explorer", [False, True])
def test_the_related_page_is_there_and_linked_whether_or_not_the_explorer_is_on(tmp_path, explorer):
    made = build(tmp_path, graph_explorer=explorer)
    try:
        with made.client:
            note(made, "Deploy", "Run the job with `--audit-keep` set to 7.\n")
            note(made, "Retention", "`--audit-keep` says how many; see Deploy.\n")
            assert 'href="/b/ops/p/deploy/related"' in get(made, "/b/ops/p/deploy").text
            r = get(made, "/b/ops/p/deploy/related")
            assert r.status_code == 200 and [p["page"] for p in shown(r.text)] == ["retention"]
            assert "<script" not in r.text and " style=" not in r.text
            assert "script-src" not in r.headers["content-security-policy"]
    finally:
        made.store.close()
