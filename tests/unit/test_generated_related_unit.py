"""Related pages, over generated projects: every reason an answer gives is checked again by
other means, the answers are compared with a search of the whole project made here, and a
relation holds from both sides.

A few hundred small made-up projects (seeded: `projects()` builds the same ones on every run).
Each has a pool of a few facts, drawn by the fact generator of
tests/unit/test_generated_copy_passes_fact_check_unit.py, and two to nine pages; each page
is one note of one to four passages, each holding a fact of the pool where that generator
puts a fact: every character it stands a fact before and after, emphasis around it and
across its edges, a backslash at an edge, in a paragraph, a list item, a quote, a table cell
or a fence; and as drawn, in capitals or without its commas. So pages share terms, written
differently on each. A page's title is one or two of the words the generator's prose is made
of, so titles occur in other pages' prose, and several titles share a word ("Quill" and
"Quill log").

What is checked of every answer of `Memory.related`, for every page:
- each claim it gives is a claim of the latest version of its page, word for word, with the
  passages that version cites;
- a `shares` reason's term is a fact of a passage each of its two claims cites, by a fresh
  cairn.facts.extract_facts on that passage, equal by kind and by facts.compared;
- a `mentioned_by` reason's claim holds the title's words side by side as the index reads
  them. The reading is FTS5's own, asked another way: the tokens it makes of a text, from an
  index of that text alone (its `fts5vocab` table), never a MATCH;
- where the answer says it is complete, its reasons are exactly those a search of every
  claim of the project finds, made here from the rule's words: a page holds a term when a
  claim's first passage has the fact and the claim's text holds the term's words side by
  side; two to COMMON pages that hold a term are related by it, each saying how many do;
- symmetry: where both answers are complete, B related to A by a term means A related to B
  by that term, held by as many pages.
A failure names project and page numbers and term kinds, never a note's text.
"""

import random
import re
import sqlite3
from collections import Counter
from contextlib import closing

import pytest
import test_generated_copy_passes_fact_check_unit as shapes

from cairn import relations
from cairn.facts import compared, extract_facts
from cairn.gate import lint_gate
from cairn.memory import Memory, RequestError
from cairn.store import Store

SEED, PROJECTS = 20261120, 300
TITLES = [
    "Quill",
    "Quill log",
    "Ada",
    "Ada keeps",
    "Nightly log",
    "Fable Works",
    "Port",
    "Brook",
    "The log",
    "Works",
]
KINDS = ("url", "path", "flag", "version", "inline_code")
EVERY_REASON = 1_000  # WHY_MAX for these tests: an answer lists every reason, to compare them


def passage(rng: random.Random, held: str) -> str:
    """A passage that holds `held` where the fact generator puts a fact (its `mixed`)."""
    drawn = shapes.fact
    shapes.fact = lambda _rng: held
    try:
        return shapes.mixed(rng)[1]
    finally:
        shapes.fact = drawn


def spelled(rng: random.Random, held: str) -> str:
    """`held` as one page writes it: as drawn, in capitals, or without its commas. Two
    spellings can be one term (facts.compared folds case and takes a thousands separator
    out) that the index reads as the same words, or as other words."""
    return rng.choice([held, held, held.upper(), held.replace(",", "")])


def projects() -> list[dict[str, str]]:
    """Each project: its pages, title to the text of the page's one note."""
    rng = random.Random(SEED)
    made = []
    for _ in range(PROJECTS):
        pool = [shapes.fact(rng) for _ in range(rng.randint(1, 4))]
        titles = rng.sample(TITLES, rng.randint(2, 9))
        made.append(
            {
                title: "\n\n".join(
                    passage(rng, spelled(rng, rng.choice(pool))) for _ in range(rng.randint(1, 4))
                )
                for title in titles
            }
        )
    return made


class Reading:
    """FTS5's own reading of a text: the tokens it indexes it as, in order."""

    def __init__(self) -> None:
        self.con = sqlite3.connect(":memory:")
        self.con.execute("CREATE VIRTUAL TABLE read USING fts5 (text)")
        self.con.execute("CREATE VIRTUAL TABLE tokens USING fts5vocab (read, instance)")
        self.known: dict[str, tuple[str, ...]] = {}

    def of(self, text: str) -> tuple[str, ...]:
        if text not in self.known:
            self.con.execute("DELETE FROM read")
            self.con.execute("INSERT INTO read (rowid, text) VALUES (1, ?)", (text,))
            rows = self.con.execute("SELECT term FROM tokens ORDER BY offset").fetchall()
            self.known[text] = tuple(term for (term,) in rows)
        return self.known[text]

    def holds(self, text: str, words: list[str]) -> bool:
        """Whether `text` holds the words side by side and in order, as the index reads both."""
        wanted, held = self.of(" ".join(words)), self.of(text)
        return bool(wanted) and any(
            held[at : at + len(wanted)] == wanted for at in range(len(held) - len(wanted) + 1)
        )


def relates(kind: str, text: str) -> bool:
    """The rule in the brief's words: every url, path, flag and version; inline code only with
    a character that is not a letter, or 12 characters long; none with no letter or digit."""
    if kind not in KINDS or not any(char.isalnum() for char in text):
        return False
    return kind != "inline_code" or len(text) >= 12 or any(not c.isalpha() for c in text)


def searched_here(store: Store, project_id: str, reading: Reading) -> tuple[dict, dict]:
    """The whole project, read here: for each page, the terms it holds, and the pages whose
    claims hold each page's title."""
    pages = {
        head.slug: store.get_page(project_id, head.slug) for head in store.list_pages(project_id)
    }
    holds: dict[str, set[tuple[str, str]]] = {}
    for slug, page in pages.items():
        held = set()
        for claim in page.claims:
            spans = store.get_spans(claim.span_ids[:1])
            for span in spans.values():
                for fact in extract_facts(span):
                    term = (fact.kind, compared(fact.as_read))
                    if relates(*term) and reading.holds(claim.text, re.findall(r"\w+", term[1])):
                        held.add(term)
        holds[slug] = held
    mentions = {
        slug: {
            other
            for other, theirs in pages.items()
            if other != slug
            and any(reading.holds(c.text, re.findall(r"\w+", page.title)) for c in theirs.claims)
        }
        for slug, page in pages.items()
    }
    return holds, mentions


@pytest.fixture(scope="module")
def world():
    """Every project filed, with every page's answer and what a search here finds."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(relations, "WHY_MAX", EVERY_REASON)
    reading = Reading()
    found = []
    with closing(Store(":memory:")) as store:
        memory = Memory(store, lint_gate)
        for n, pages in enumerate(projects()):
            name = f"p{n:03d}"
            project = store.create_project(name)
            for title, text in pages.items():
                try:
                    memory.note(name, text, title=title, topic=title)
                except RequestError:  # no passage to file, or the same text as another page
                    continue
            slugs = [head.slug for head in store.list_pages(project.id)]
            answers = {slug: memory.related(name, slug, relations.RELATED_MAX) for slug in slugs}
            latest = {slug: store.get_page(project.id, slug) for slug in slugs}
            cited = {i for page in latest.values() for c in page.claims for i in c.span_ids}
            found.append(
                {
                    "name": name,
                    "answers": answers,
                    "latest": latest,
                    "spans": store.get_spans(cited),
                    "here": searched_here(store, project.id, reading),
                }
            )
    monkeypatch.undo()
    return {"projects": found, "reading": reading}


def reasons(answer: dict, kind: str):
    for page in answer["related"]:
        for reason in page["why"]:
            if reason["kind"] == kind:
                yield page, reason


def test_the_generated_projects_relate_pages_in_every_way(world):
    """So the properties below are not kept by projects in which nothing could go wrong."""
    shared, mentions, common, complete, answers = Counter(), 0, 0, 0, 0
    for project in world["projects"]:
        holds, _ = project["here"]
        by_term = Counter(term for held in holds.values() for term in held)
        common += sum(pages > relations.COMMON for pages in by_term.values())
        for answer in project["answers"].values():
            answers += 1
            complete += answer["searched"]["complete"]
            mentions += sum(1 for _ in reasons(answer, "mentioned_by"))
            shared.update(reason["term_kind"] for _, reason in reasons(answer, "shares"))
    assert set(shared) == set(KINDS), shared  # each kind of term relates pages somewhere
    assert min(shared.values()) >= 20 and mentions >= 200, (shared, mentions)
    assert common >= 20, common  # terms more than COMMON pages hold, which relate none
    assert answers >= 1_000 and complete == answers, (answers, complete)


def test_every_claim_given_is_a_claim_of_its_pages_latest_version(world):
    wrong = []
    for project in world["projects"]:
        for slug, answer in project["answers"].items():
            for page in answer["related"]:
                for reason in page["why"]:
                    sides = [(page["page"], reason["there"])]
                    if reason["kind"] == "shares":
                        sides.append((slug, reason["here"]))
                    for at, claim in sides:
                        held = {c.id: c for c in project["latest"][at].claims}.get(
                            claim["claim_id"]
                        )
                        if (
                            held is None
                            or held.text != claim["text"]
                            or list(held.span_ids) != claim["passage_ids"]
                        ):
                            wrong.append((project["name"], slug, at, reason["kind"]))
            assert answer["version"] == project["latest"][slug].version
            assert all(
                page["version"] == project["latest"][page["page"]].version
                and page["title"] == project["latest"][page["page"]].title
                for page in answer["related"]
            )
    assert wrong == []


def test_a_shared_term_is_a_fact_of_a_passage_each_claim_cites(world):
    """A fresh extract_facts on each cited passage, whole: the term is one of its facts."""
    wrong, checked = [], 0
    for project in world["projects"]:
        for slug, answer in project["answers"].items():
            for page, reason in reasons(answer, "shares"):
                term = (reason["term_kind"], reason["term"])
                assert reason["term_kind"] in KINDS and relates(*term)
                assert 2 <= reason["pages"] <= relations.COMMON
                for claim in (reason["here"], reason["there"]):
                    checked += 1
                    facts = {
                        (fact.kind, compared(fact.as_read))
                        for span_id in claim["passage_ids"]
                        for fact in extract_facts(project["spans"][span_id])
                    }
                    if term not in facts:
                        wrong.append((project["name"], slug, page["page"], reason["term_kind"]))
    assert wrong == [] and checked >= 1_000, (len(wrong), wrong[:5], checked)


def test_a_mention_is_a_claim_that_holds_the_title_as_the_index_reads_it(world):
    wrong, checked = [], 0
    for project in world["projects"]:
        for slug, answer in project["answers"].items():
            title = re.findall(r"\w+", answer["title"])
            for page, reason in reasons(answer, "mentioned_by"):
                checked += 1
                assert (reason["term"], reason["term_kind"]) == (answer["title"], "title")
                assert "here" not in reason and "pages" not in reason and page["page"] != slug
                if not world["reading"].holds(reason["there"]["text"], title):
                    wrong.append((project["name"], slug, page["page"]))
    assert wrong == [] and checked >= 200, (wrong[:5], checked)


def test_a_complete_answer_gives_exactly_what_a_search_of_the_whole_project_finds(world):
    """Nothing more and nothing less: the pages related by each term, how many pages hold it,
    and the pages that mention the title. A title in another page's title alone is no mention,
    since the search here reads claims' text only."""
    differ = []
    for project in world["projects"]:
        holds, mentions = project["here"]
        by_term = Counter(term for held in holds.values() for term in held)
        for slug, answer in project["answers"].items():
            if not answer["searched"]["complete"]:
                continue
            expected = {
                (other, *term, by_term[term])
                for term in holds[slug]
                if by_term[term] <= relations.COMMON
                for other, held in holds.items()
                if other != slug and term in held
            }
            given = {
                (page["page"], reason["term_kind"], reason["term"], reason["pages"])
                for page, reason in reasons(answer, "shares")
            }
            mentioned = {page["page"] for page, _ in reasons(answer, "mentioned_by")}
            if given != expected or mentioned != mentions[slug]:
                kinds = sorted({kind for _, kind, _, _ in given ^ expected})
                differ.append((project["name"], slug, kinds, sorted(mentioned ^ mentions[slug])))
            assert all(page["more"] == 0 for page in answer["related"])
    assert differ == [], (len(differ), differ[:5])


def test_a_relation_holds_from_both_sides(world):
    """Where both answers are complete: B related to A by a term, A related to B by it, and
    both say as many pages hold it."""
    one_sided, pairs = [], 0
    for project in world["projects"]:
        answers = project["answers"]
        shares = {
            slug: {
                (page["page"], reason["term_kind"], reason["term"]): reason["pages"]
                for page, reason in reasons(answer, "shares")
            }
            for slug, answer in answers.items()
        }
        for slug, given in shares.items():
            for (other, kind, term), pages in given.items():
                if not (
                    answers[slug]["searched"]["complete"] and answers[other]["searched"]["complete"]
                ):
                    continue
                pairs += 1
                if shares[other].get((slug, kind, term)) != pages:
                    one_sided.append((project["name"], slug, other, kind))
    assert one_sided == [] and pairs >= 1_000, (one_sided[:5], pairs)


def test_pages_stand_heaviest_first_and_equal_weights_by_slug(world):
    """The order is the answer: no weight is given, and each can be worked out from `pages`."""
    from fractions import Fraction

    for project in world["projects"]:
        for answer in project["answers"].values():
            weights = [
                (
                    -sum(Fraction(1, reason.get("pages", 2) - 1) for reason in page["why"]),
                    page["page"],
                )
                for page in answer["related"]
            ]
            assert weights == sorted(weights)
            assert "weight" not in str(answer)
