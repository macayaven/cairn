"""Sentences of the guides and of the server's contract on what people are shown of related
pages (KG2), each pinned to what the web view does. The world is that of
tests/unit/test_related_page_unit.py: a real store in a temporary file, made-up names."""

import re

import pytest
from guide_source import guide_source
from test_design_system_unit import tree
from test_related_page_unit import HOSTILE, build, get, main_of, note, quoted, shown

import cairn.server
from cairn import relations
from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.store import Claim
from cairn.web.ui import RELATED_SHOWN


def flat(text: str) -> str:
    return " ".join(text.split())


def says(guide: str, *sentences: str) -> None:
    text = flat(guide_source(guide))
    for sentence in sentences:
        assert text.count(flat(sentence)) == 1, sentence


def contract_says(*sentences: str) -> None:
    text = flat(cairn.server.__doc__)
    for sentence in sentences:
        assert text.count(flat(sentence)) == 1, sentence


@pytest.fixture
def w(tmp_path):
    made = build(tmp_path)
    with made.client:
        yield made
    made.store.close()


@pytest.fixture
def world(w):
    note(w, "Deploy", "Run the job with `--audit-keep` set to 7.\n")
    note(w, "Retention", "`--audit-keep` says how many; see Deploy.\n")
    return w


# security.md, "The web pages"


def test_a_page_links_its_related_pages_the_most_related_first_eight_unless_asked(w):
    says(
        "security",
        "A page links its **Related pages**, for its latest version: the pages of its project"
        " that state one of its terms or name its title, the most related first, 8 of them"
        " unless the address asks for up to 20 (`?limit=20`).",
    )
    assert (RELATED_SHOWN, relations.RELATED_MAX) == (8, 20)
    terms = [f"`t{n}.cfg`" for n in range(10)]
    note(w, "Asked", "Use " + ", ".join(terms) + " and `both.cfg`.\n")
    for n, term in enumerate(terms):
        more = " with `both.cfg`" if n == 9 else ""
        note(w, f"Page {n}", f"Brook sets {term}{more}.\n")
    note(w, "Asked", "Then stop.\n")  # version 2 of the page
    viewed = get(w, "/b/ops/p/asked")
    assert '<a href="/b/ops/p/asked/related">Related pages</a>' in viewed.text
    assert "Related pages" not in get(w, "/b/ops/p/asked?version=1").text
    answer = Memory(w.ada.view, lint_gate).related("ops", "asked", RELATED_SHOWN)
    eight = [page["page"] for page in shown(get(w, "/b/ops/p/asked/related").text)]
    assert eight == [page["page"] for page in answer["related"]] and len(eight) == 8
    assert eight[0] == "page-9"  # it states two of the page's terms: the most related
    twenty = shown(get(w, "/b/ops/p/asked/related?limit=20").text)
    assert len(twenty) == 10 and [page["page"] for page in twenty][:8] == eight


def test_a_relation_is_worked_out_when_the_page_is_opened_and_comes_with_its_evidence(world):
    w = world
    says(
        "security",
        "Each relation is worked out when the page is opened, from the pages as they are, and"
        " comes with its evidence: the reason in words and the claims it rests on, each linked"
        " to its place on its page, with who wrote the notes it cites, when, and whether any is"
        " outside text.",
    )
    r = get(w, "/b/ops/p/deploy/related")
    (page,) = shown(r.text)
    mention, shares = page["why"]
    assert mention["said"] == "A claim of Retention names this page's title, Deploy."
    assert shares["said"] == "Both pages state the code --audit-keep. 2 pages of ops hold it."
    for claim, slug in (
        (mention["there"], "retention"),
        (shares["here"], "deploy"),
        (shares["there"], "retention"),
    ):
        assert claim["links"] == [f"/b/ops/p/{slug}#{claim['claim_id']}"]
        held = {c.id: c.text for c in w.store.get_page(w.ops.id, slug).claims}
        assert held[claim["claim_id"]] == claim["text"]
    told = [flat(p.text()) for p in tree(main_of(r.text)).all("p", "written")]
    assert told == ["written by Ada \N{MIDDLE DOT} written 3 February 2099, 04:05 UTC"] * 3
    # worked out when opened: a page filed since is there at the next opening, and one whose
    # note is forgotten is gone at the next, with nothing kept between the two
    mail = w.store.writing_as("source", "team-mail")
    brought, _ = mail.add_note(w.ops.id, "# Mail\n\nPass `--audit-keep` as told.\n", via="mail")
    span = next(s.id for s in w.store.note_spans(brought.id) if s.kind == "paragraph")
    told = Claim(id="cl_mail", text="Pass `--audit-keep` as told.", span_ids=(span,))
    w.ada.view.save_page(w.ops.id, "mail", "Mail", "", [told])
    again = get(w, "/b/ops/p/deploy/related")
    assert [page["page"] for page in shown(again.text)] == ["retention", "mail"]
    assert again.text.count('<span class="outside">outside text</span>') == 1
    assert "from the source team-mail" in again.text
    Memory(w.ada.view, lint_gate).forget("ops", brought.id)
    after = get(w, "/b/ops/p/deploy/related")
    assert [page["page"] for page in shown(after.text)] == ["retention"]
    assert "team-mail" not in after.text and "outside text" not in after.text


def test_a_claim_is_quoted_as_plain_text_and_a_link_a_list_or_a_fence_is_not_drawn(w):
    says(
        "security",
        "A claim is quoted there as plain text, white space kept, and never drawn as Markdown: a"
        " link, a list or a code fence that a note holds is shown as the characters it was"
        " written in.",
    )
    held = HOSTILE | {"weblink": "See [Asked](https://example.invalid/asked)\n\n  twice."}
    note(w, "Asked", "Brook asked first.\n")
    for slug, text in held.items():
        claim = Claim(id=f"cl_{slug}", text=text, span_ids=())
        w.ada.view.save_page(w.ops.id, slug, f"Page {slug}", "", [claim])
    r = get(w, "/b/ops/p/asked/related")
    body = tree(main_of(r.text))
    texts = {figure.attrs["data-claim"]: figure for figure in quoted(body)}
    for slug, text in held.items():
        (line,) = texts[f"cl_{slug}"].all(cls="source-line")
        assert line.text() == text and line.kids() == [], slug  # its characters, and no element
    assert body.all("pre") == [] and body.all("img") == []
    assert {tuple(sorted(ul.classes)) for ul in body.all("ul")} == {("hits",), ("sources",)}
    assert all(a.attrs["href"].startswith("/") for a in body.all("a"))

    # the page view, which draws a claim's Markdown, makes each of them of the same claims
    def drawn(slug: str, tag: str) -> list:
        (claim,) = tree(main_of(get(w, f"/b/ops/p/{slug}").text)).all("div", "claim-text")
        return claim.all(tag)

    assert drawn("fence", "pre") and drawn("heading", "ul")
    assert [a.attrs["href"] for a in drawn("weblink", "a")] == ["https://example.invalid/asked"]


def test_the_page_says_in_one_sentence_what_its_read_left_out(w):
    says("security", "When the read left something out, the page says what in one sentence.")
    note(w, "Asked", "Use `a.cfg`, `b.cfg` and `c.cfg`.\n")
    note(w, "Two", "Use `a.cfg` and `b.cfg`.\n")
    note(w, "One", "Use `c.cfg`.\n")
    whole = tree(main_of(get(w, "/b/ops/p/asked/related").text))
    assert whole.all("p", "searched") == []
    (said,) = tree(main_of(get(w, "/b/ops/p/asked/related?limit=1").text)).all("p", "searched")
    sentence = flat(said.text()).removesuffix(" Show up to 20")
    assert sentence == "This is not all of it: 1 more related page is not shown."
    assert len(re.findall(r"[.!?](?= |$)", sentence)) == 1  # one sentence


# connect.md


def test_the_web_view_shows_a_person_the_answer_of_related(world):
    w = world
    says(
        "connect",
        "In the web view a page's **Related pages** link shows a person what `related` answers"
        " for the page, each claim quoted as plain text.",
    )
    answer = Memory(w.ada.view, lint_gate).related("ops", "deploy", RELATED_SHOWN)
    pages = shown(get(w, "/b/ops/p/deploy/related").text)
    assert [(p["page"], p["title"], p["more"]) for p in pages] == [
        (p["page"], p["title"], p["more"]) for p in answer["related"]
    ]
    for page, given in zip(pages, answer["related"], strict=True):
        for reason, why in zip(page["why"], given["why"], strict=True):
            assert (reason["kind"], reason["term"]) == (why["kind"], why["term"])
            for side in ("here", "there"):
                assert (side in reason) is (side in why)
                if side in why:
                    assert reason[side]["text"] == why[side]["text"]
                    assert reason[side]["claim_id"] == why[side]["claim_id"]


# The server's contract (src/cairn/server.py)


def test_the_contract_has_the_related_pages_address_and_what_it_is_recorded_as(world):
    w = world
    contract_says(
        "- GET /b/{project}/p/{slug}/related[?limit=N] (KG2), linked from the page's head as"
        ' "Related pages" for its latest version: what `related` answers for the page',
        "N is 1 to 20 (8 when left out), and any other is a 400 page that says so in REST's"
        " sentence; a project out of reach or a page that is not there is the 404 the page view"
        " gives.",
        '/b/<project>/p/<slug> "page", /b/<project>/p/<slug>/related "related", /search "recall")',
        "A claim that is cut (`text_cut`) is never taken to hold a text: the page says it is"
        " longer than what is shown and shows the line its answer gives (the line of its passage"
        " that states the term, or the line that names the title) wherever it holds one, else"
        " the head it holds as its text, else nothing, and its link reads the whole claim on its"
        " page.",
    )
    assert get(w, "/b/ops/p/deploy/related?limit=21").status_code == 400
    missing, viewed = get(w, "/b/ops/p/none/related"), get(w, "/b/ops/p/none")
    assert missing.status_code == viewed.status_code == 404
    assert main_of(missing.text) == main_of(viewed.text)
    # the cut claim, in each shape: tests/unit/test_related_page_unit.py; here, one of a note
    lines = [f"Brook checks ledger {n} at dawn." for n in range(relations.TEXT_MAX // 20)]
    note(w, "Asked", "Ada reads `quill.lock` daily.\n")
    note(w, "Long", "\n".join([*lines, "Ada pins `quill.lock`."]) + "\n")
    (page,) = shown(get(w, "/b/ops/p/asked/related").text)
    there = page["why"][0]["there"]
    assert (there["part"], there["text"]) == ("line", "Ada pins `quill.lock`.")
    assert there["says"].startswith("This claim is longer than what is shown")
    assert there["link_says"] == ["Read the whole claim on Long"]
