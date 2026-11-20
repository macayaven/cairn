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
  side; two to COMMON pages that hold a term are related by it, each saying how many do,
  unless more than CLAIMS_COMMON claims of the project hold its words; a page is mentioned
  by the other pages a claim of which names its title in its prose (the title's words side
  by side, as the index reads them, in the passage with its code, paths, flags, versions
  and addresses taken out: `prose_of`, a fresh extraction, never the index's marks), unless
  more than COMMON pages name it, or more than CLAIMS_COMMON of the other pages' claims
  hold its words;
- symmetry: where both answers are complete, B related to A by a term means A related to B
  by that term, held by as many pages;
- and with the bounds on what is read made small enough to be met: every reason still given
  is one the whole read gives, with the same count of pages, and an answer that gives fewer
  counts claims it did not read;
- and with the bounds on what is given of a claim made small enough to be met (TEXT_GIVEN
  and LINE_GIVEN characters for relations.TEXT_MAX and LINE_MAX): the answer is the whole
  answer but for its long claims; a claim has its text whole, or `text_cut` with a line and
  no text, or `text_cut` with its first TEXT_GIVEN characters and no line (only a claim
  that mentions the page and holds a mark of the index or a null: a project made by hand
  has some), never a head and a line; a line is the line of the claim's first passage on
  which the term's first
  fact stands, by a fresh extract_facts and the passage split at its line ends here, whole
  or LINE_GIVEN characters of it that hold the fact as it is written there; and the line of
  a claim that mentions the page is a piece of the claim's text, the first of its lines
  that holds the title's words side by side as the index reads them (its tokens of the
  line alone, as for a mention above), whole or LINE_GIVEN characters of it.
CLAIMS_COMMON is 7 for these projects, which hold at most 36 claims, so that the rule decides
some relations; the first test counts how many.
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
from cairn.store import Store, relating

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
CLAIMS_COMMON = 7  # for these projects: more claims than this hold a term's words, and it is common
CLAIMS_READ, BYTES_READ = (
    3,
    120,
)  # the bounds of the second read of each page, small enough to be met
TEXT_GIVEN, LINE_GIVEN = 24, 34  # the bounds of the third: a claim's text given, and its line


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
    a character that is not a letter, or 12 characters long; none with no letter or digit,
    and none longer than 200 characters."""
    if kind not in KINDS or not any(char.isalnum() for char in text) or len(text) > 200:
        return False
    return kind != "inline_code" or len(text) >= 12 or any(not c.isalpha() for c in text)


def prose_of(span) -> str:
    """The passage's text with every character of every stretch that is code, a path, a flag,
    a version or an address made a "z" (a fresh extract_facts: the facts of those kinds and
    the code blocks; line ends kept), so each character stays where it was and no word of a
    title is left in such a stretch. A word that stands partly in one is no longer the word:
    what the index then reads of the text is what stands in its prose."""
    text = list(span.text)
    for fact in extract_facts(span):
        if fact.kind in KINDS or fact.kind == "code_block":
            for at in range(fact.start, fact.end):
                text[at] = "z" if text[at] != "\n" else "\n"
    return "".join(text)


def searched_here(store: Store, project_id: str, reading: Reading) -> dict:
    """The whole project, read here, claim by claim: `holds`, for each page, the terms it
    holds; `claims`, for each of those terms, how many claims of the project hold its words;
    `named_by`, for each page, the other pages a claim of which holds its title in its
    prose (`prose_of` of the passage the claim is the text of); `held_by`, those whose
    claims hold its words anywhere; and `naming`, how many claims of theirs do."""
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
    every = [claim for page in pages.values() for claim in page.claims]
    claims = {
        term: sum(reading.holds(claim.text, re.findall(r"\w+", term[1])) for claim in every)
        for term in set().union(*holds.values())
    }
    prose = {}
    for claim in every:  # every generated claim is the text of the one passage it cites
        (span,) = store.get_spans(claim.span_ids).values()
        assert span.text == claim.text
        prose[claim.id] = prose_of(span)
    named_by, held_by, naming = {}, {}, {}
    for slug, page in pages.items():
        title = re.findall(r"\w+", page.title)
        theirs = {
            other: sum(reading.holds(claim.text, title) for claim in found.claims)
            for other, found in pages.items()
            if other != slug
        }
        held_by[slug] = {other for other, count in theirs.items() if count}
        named_by[slug] = {
            other
            for other in held_by[slug]
            if any(reading.holds(prose[claim.id], title) for claim in pages[other].claims)
        }
        naming[slug] = sum(theirs.values())
    return {
        "holds": holds,
        "claims": claims,
        "named_by": named_by,
        "held_by": held_by,
        "naming": naming,
    }


def expected(here: dict, slug: str) -> tuple[set, set]:
    """What a complete answer for the page gives, by the rules in their own words: (the pages
    related by a term, with the term and how many pages hold it; the pages that mention it)."""
    holds = here["holds"]
    pages = Counter(term for held in holds.values() for term in held)
    shares = {
        (other, *term, pages[term])
        for term in holds[slug]
        if pages[term] <= relations.COMMON and here["claims"][term] <= CLAIMS_COMMON
        for other, held in holds.items()
        if other != slug and term in held
    }
    named_by = here["named_by"][slug]
    common = len(named_by) > relations.COMMON or here["naming"][slug] > CLAIMS_COMMON
    return shares, set() if common else named_by


@pytest.fixture(scope="module")
def world():
    """Every project filed, with every page's answer, the same answer read within small
    bounds, the same answer with little given of each claim, and what a search here finds."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(relations, "WHY_MAX", EVERY_REASON)
    monkeypatch.setattr(relations, "CLAIMS_COMMON", CLAIMS_COMMON)
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
            with pytest.MonkeyPatch.context() as bounds:
                bounds.setattr(relating, "CLAIMS_MAX", CLAIMS_READ)
                bounds.setattr(relating, "BYTES_MAX", BYTES_READ)
                bounded = {
                    slug: memory.related(name, slug, relations.RELATED_MAX) for slug in slugs
                }
            with pytest.MonkeyPatch.context() as bounds:
                bounds.setattr(relations, "TEXT_MAX", TEXT_GIVEN)
                bounds.setattr(relations, "LINE_MAX", LINE_GIVEN)
                cut = {slug: memory.related(name, slug, relations.RELATED_MAX) for slug in slugs}
            latest = {slug: store.get_page(project.id, slug) for slug in slugs}
            cited = {i for page in latest.values() for c in page.claims for i in c.span_ids}
            found.append(
                {
                    "name": name,
                    "answers": answers,
                    "bounded": bounded,
                    "cut": cut,
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
    """So the properties below are not kept by projects in which nothing could go wrong: each
    kind of term relates pages, and each rule that says a term or a title relates none decides
    some relation that would otherwise be given."""
    shared, mentions, complete, answers = Counter(), 0, 0, 0
    decided = Counter()
    for project in world["projects"]:
        here = project["here"]
        pages = Counter(term for held in here["holds"].values() for term in held)
        for term, count in pages.items():
            if count > relations.COMMON:
                decided["a term more than COMMON pages hold"] += 1
            elif count >= 2 and here["claims"][term] > CLAIMS_COMMON:
                decided["a term few pages hold and many claims"] += 1
        for slug, named_by in here["named_by"].items():
            decided["a title a page holds in code alone"] += len(here["held_by"][slug] - named_by)
            if len(named_by) > relations.COMMON:
                decided["a title more than COMMON pages name"] += 1
            elif named_by and here["naming"][slug] > CLAIMS_COMMON:
                decided["a title few pages name in many claims"] += 1
            elif len(here["held_by"][slug]) > relations.COMMON:
                decided["a title many pages hold and few name"] += 1
        for answer in project["answers"].values():
            answers += 1
            complete += answer["searched"]["complete"]
            mentions += sum(1 for _ in reasons(answer, "mentioned_by"))
            shared.update(reason["term_kind"] for _, reason in reasons(answer, "shares"))
    assert set(shared) == set(KINDS), shared  # each kind of term relates pages somewhere
    assert min(shared.values()) >= 20 and mentions >= 200, (shared, mentions)
    assert len(decided) == 6, decided
    few = "a title many pages hold and few name"  # relates now: the pages that count name it
    assert decided[few] >= 3 and decided["a title a page holds in code alone"] >= 200, decided
    assert min(count for rule, count in decided.items() if rule != few) >= 20, decided
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


def test_a_mention_is_a_claim_that_names_the_title_in_its_prose_as_the_index_reads_it(world):
    """Every mention returned, again and by other means than the index's marks: the claim's
    text holds the title's words side by side as the index reads them; and so does the
    passage the claim is the text of once its code, paths, flags, versions and addresses are
    taken out of it (`prose_of`, a fresh extraction): a match of the title stands outside
    every one of those stretches."""
    wrong, checked = [], 0
    for project in world["projects"]:
        for slug, answer in project["answers"].items():
            title = re.findall(r"\w+", answer["title"])
            for page, reason in reasons(answer, "mentioned_by"):
                checked += 1
                assert (reason["term"], reason["term_kind"]) == (answer["title"], "title")
                assert "here" not in reason and "pages" not in reason and page["page"] != slug
                claim = reason["there"]
                (passage_id,) = claim["passage_ids"]
                span = project["spans"][passage_id]
                assert span.text == claim["text"]  # so a place of the one is a place of the other
                if not world["reading"].holds(claim["text"], title):
                    wrong.append((project["name"], slug, page["page"], "not held"))
                elif not world["reading"].holds(prose_of(span), title):
                    wrong.append((project["name"], slug, page["page"], "held in code alone"))
    assert wrong == [] and checked >= 200, (wrong[:5], checked)


def test_a_complete_answer_gives_exactly_what_a_search_of_the_whole_project_finds(world):
    """Nothing more and nothing less: the pages related by each term, how many pages hold it,
    and the pages that mention the title. A title in another page's title alone is no mention,
    since the search here reads claims' text only."""
    differ = []
    for project in world["projects"]:
        for slug, answer in project["answers"].items():
            if not answer["searched"]["complete"]:
                continue
            shares, mentioned_by = expected(project["here"], slug)
            given = {
                (page["page"], reason["term_kind"], reason["term"], reason["pages"])
                for page, reason in reasons(answer, "shares")
            }
            mentioned = {page["page"] for page, _ in reasons(answer, "mentioned_by")}
            if given != shares or mentioned != mentioned_by:
                kinds = sorted({kind for _, kind, _, _ in given ^ shares})
                differ.append((project["name"], slug, kinds, sorted(mentioned ^ mentioned_by)))
            assert all(page["more"] == 0 for page in answer["related"])
    assert differ == [], (len(differ), differ[:5])


def given_by(answer: dict) -> set:
    return {
        (page["page"], reason["kind"], reason["term_kind"], reason["term"], reason.get("pages"))
        for page in answer["related"]
        for reason in page["why"]
    }


def test_a_read_within_small_bounds_gives_only_what_the_whole_read_gives_and_counts_the_rest(
    world,
):
    """No reason carries a mark of how far it was read, because none needs one: with the claims
    and bytes read made small, every reason still given is the whole read's, with the same
    count of pages, a mention like a shared term (the claims that hold a title are read to
    know which name it); and an answer that gives fewer says how many claims it did not
    read."""
    wrong, fewer, bounded_answers, lost = [], 0, 0, Counter()
    for project in world["projects"]:
        for slug, whole in project["answers"].items():
            bounded = project["bounded"][slug]
            searched = bounded["searched"]
            assert searched["claims_read"] <= CLAIMS_READ
            got, all_of_it = given_by(bounded), given_by(whole)
            if not got <= all_of_it:
                wrong.append((project["name"], slug, "a reason the whole read does not give"))
            lost["mentions"] += sum(1 for reason in all_of_it - got if reason[1] == "mentioned_by")
            lost["terms"] += sum(1 for reason in all_of_it - got if reason[1] == "shares")
            if got != all_of_it:
                fewer += 1
                if not searched["claims_not_read"] or searched["complete"]:
                    wrong.append((project["name"], slug, "fewer, and nothing counted"))
            bounded_answers += searched["claims_not_read"] > 0
            if searched["claims_not_read"] == 0 and bounded != whole:
                wrong.append((project["name"], slug, "every claim read, another answer"))
    assert wrong == [], (len(wrong), wrong[:5])
    assert bounded_answers >= 200 and fewer >= 100, (bounded_answers, fewer)
    assert lost["mentions"] >= 500 and lost["terms"] >= 300, lost


def first_line_of(span, kind: str, term: str) -> tuple[str, str] | None:
    """Read here: the line of the passage on which the first fact that is the term stands,
    and the fact as it is written there."""
    for fact in extract_facts(span):  # in the order they stand
        if (fact.kind, compared(fact.as_read)) == (kind, term):
            before = span.text[: fact.start].count("\n")
            return span.text.split("\n")[before], span.text[fact.start : fact.end]
    return None


def mention_fault(
    reading: Reading, given: dict, text: str, prose: str, title: str, seen: Counter
) -> str:
    """What is wrong with the line of a claim cut that mentions the page titled `title`, the
    claim's whole text being `text` and `prose` that text with its code, paths, flags,
    versions and addresses taken out (`prose_of`: character for character); nothing when it
    is right. The line is a piece of the text; where it stands, the prose holds the title's
    words side by side as the index reads them; no line of the prose before it does, so it
    is the first place the title is named in prose; and it is whole lines of the text, or
    LINE_GIVEN characters of longer ones. (No generated claim holds a title in code before
    it names it in prose: tests/unit/test_relating_unit.py has that claim.)"""
    words = re.findall(r"\w+", title)
    line = given.get("line")
    if line is None or line not in text:
        return "no line, or not a piece of the claim's text"
    stands = [at for at in range(len(text)) if text.startswith(line, at)]
    named = [at for at in stands if reading.holds(prose[at : at + len(line)], words)]
    if not named:
        return "the line does not name the title in prose as the index reads it"
    before = prose[: prose.rfind("\n", 0, named[0]) + 1]  # the whole lines before it
    seen["a mention after a line that holds none"] += bool(before.strip())
    if reading.holds(before, words):
        return "a line before it names the title in prose"
    if "line_cut" in given:
        seen["a mention's line cut"] += 1
        return "" if len(line) == LINE_GIVEN else "cut, and not to the bound"
    seen["a mention's line whole"] += 1
    seen["a mention's line of two lines"] += "\n" in line
    whole_lines = ("\n" + line + "\n") in ("\n" + text + "\n")
    return "" if whole_lines and len(line) <= LINE_GIVEN else "not whole lines of the text"


def shape_of(given: dict, text: str, mention: bool) -> tuple[str, str]:
    """The shape a claim is given in, its whole text being `text`, and what is wrong with it
    (nothing when it is right). One of three: its text whole, when that is within TEXT_GIVEN
    characters; of a longer text, `text_cut` with a line and no text; or `text_cut` with its
    first TEXT_GIVEN characters and no line, which only a claim that mentions the page and
    holds a mark of the index or a null character is given as. Never a head and a line."""
    if "text_cut" not in given:
        alone = "line" not in given and "line_cut" not in given
        right = alone and given.get("text") == text and len(text) <= TEXT_GIVEN
        return "whole", "" if right else "not its text whole, alone, within the bound"
    if given["text_cut"] is not True or len(text) <= TEXT_GIVEN:
        return "none", "marked cut, and its text is within the bound"
    if "line" in given:
        return "a line", "" if "text" not in given else "a head beside a line"
    told = not any(mark in text for mark in (*relating.MARKS, "\x00"))
    right = given.get("text") == text[:TEXT_GIVEN] and "line_cut" not in given
    if not right:
        return "a head", "not its first characters, alone"
    return "a head", "a head where a line can be told" if told or not mention else ""


def claims_checked(whole: dict, cut: dict, spans: dict, reading: Reading, seen: Counter) -> list:
    """What is wrong with the answer `cut`, the answer `whole` with little given of a claim:
    as (the related page, the term's kind, the side, the fault). Nothing but its long claims
    differs; each claim is in one of the three shapes (`shape_of`); the line of a claim
    that states a term is the passage's own line that holds the term's first fact, whole or
    a window of it around the fact as written; and the line of a claim that mentions the
    page is the first of the claim's text that names the title, as the index reads it
    (`mention_fault`)."""
    wrong = []
    assert {**cut, "related": None} == {**whole, "related": None}
    for page, full in zip(cut["related"], whole["related"], strict=True):
        assert {**page, "why": None} == {**full, "why": None}
        for reason, entire in zip(page["why"], full["why"], strict=True):
            assert {**reason, "here": None, "there": None} == {
                **entire,
                "here": None,
                "there": None,
            }
            for side in ("here", "there") if reason["kind"] == "shares" else ("there",):
                given, claim = reason[side], entire[side]
                where = (page["page"], reason["term_kind"], side)
                added = {k: given[k] for k in ("text_cut", "line", "line_cut") if k in given}
                if {**given, "text": claim["text"]} != {**claim, **added}:
                    wrong.append((*where, "another field differs"))
                shape, fault = shape_of(given, claim["text"], reason["kind"] != "shares")
                seen[shape] += 1
                if fault:
                    wrong.append((*where, fault))
                if fault or shape != "a line":
                    continue
                if reason["kind"] != "shares":
                    seen["a line, of a mention"] += 1
                    prose = prose_of(spans[claim["passage_ids"][0]])
                    fault = mention_fault(
                        reading, given, claim["text"], prose, whole["title"], seen
                    )
                    if fault:
                        wrong.append((*where, fault))
                    continue
                span = spans[claim["passage_ids"][0]]
                line, written = first_line_of(span, reason["term_kind"], reason["term"])
                seen["of a passage of several lines"] += "\n" in span.text
                if len(line) <= LINE_GIVEN:
                    seen["line whole"] += 1
                    if given["line"] != line or "line_cut" in given:
                        wrong.append((*where, "not the line, whole"))
                    continue
                seen["line cut"] += 1
                seen["line cut to the term's start"] += len(written) > LINE_GIVEN
                # LINE_GIVEN characters of the line that hold the term as written; of a term
                # written longer (none is, at the shipped bounds, where TERM_MAX is under
                # LINE_MAX), its first LINE_GIVEN
                if not (
                    given.get("line_cut") is True
                    and len(given["line"]) == LINE_GIVEN
                    and given["line"] in line
                    and (written in given["line"] or given["line"] == written[:LINE_GIVEN])
                ):
                    wrong.append((*where, "not a window of the line that holds the term"))
    return wrong


def test_a_claim_is_given_whole_or_by_its_line_or_by_its_head_and_never_by_both(world):
    """Over every answer of the generated projects, with little given of a claim
    (`claims_checked`): a claim has its text whole, or `text_cut` with a line that holds the
    term or names the title, or `text_cut` with a head and no line; never a head and a line."""
    wrong, seen = [], Counter()
    for project in world["projects"]:
        for slug, whole in project["answers"].items():
            found = claims_checked(
                whole, project["cut"][slug], project["spans"], world["reading"], seen
            )
            wrong += [(project["name"], slug, *fault) for fault in found]
    assert wrong == [], (len(wrong), wrong[:5])
    each = ("whole", "a line", "a line, of a mention", "line whole", "line cut")
    assert min(seen[k] for k in each) >= 100, seen
    assert seen["a head"] == seen["none"] == 0, seen  # no generated claim holds a mark
    assert seen["a mention's line whole"] >= 200 and seen["a mention's line cut"] >= 500, seen
    assert seen["a mention after a line that holds none"] >= 200, seen
    assert seen["a mention's line of two lines"] >= 5, seen
    assert seen["of a passage of several lines"] >= 300, seen
    assert seen["line cut to the term's start"] >= 30, seen


def test_the_same_holds_of_claims_that_hold_a_mark_of_the_index_or_a_null(monkeypatch):
    """The third shape, which no generated claim is given in: a project made here, of a page
    that five claims mention. Three of them hold a mark of the index or a null character,
    and are given by their head; the others by the line that names the title. The same
    check as above says so (`claims_checked`), and nothing else of the answers is wrong."""
    marked = [relating.MARKS[0], relating.MARKS[1], "\x00"]
    seen, reading = Counter(), Reading()
    with closing(Store(":memory:")) as store:
        memory = Memory(store, lint_gate)
        project = store.create_project("marks")
        memory.note("marks", "Brook balances it; see `quill.lock`.\n", title="Quill ledger")
        for n, mark in enumerate(marked):
            text = f"Page {n}: Ada waits. She opens {mark} the Quill ledger at noon.\n"
            memory.note("marks", text, title=f"Marked {n}")
        memory.note("marks", "Ada waits.\nThen she opens the Quill ledger.\n", title="Plain")
        memory.note("marks", "Quill pins `quill.lock`, far from the ledger.\n", title="Keeper")
        slugs = [head.slug for head in store.list_pages(project.id)]
        whole = {slug: memory.related("marks", slug, relations.RELATED_MAX) for slug in slugs}
        monkeypatch.setattr(relations, "TEXT_MAX", TEXT_GIVEN)
        monkeypatch.setattr(relations, "LINE_MAX", LINE_GIVEN)
        cut = {slug: memory.related("marks", slug, relations.RELATED_MAX) for slug in slugs}
        latest = [store.get_page(project.id, slug) for slug in slugs]
        spans = store.get_spans({i for page in latest for c in page.claims for i in c.span_ids})
    wrong = [
        (slug, *fault)
        for slug in slugs
        for fault in claims_checked(whole[slug], cut[slug], spans, reading, seen)
    ]
    assert wrong == [], wrong
    assert len(whole["quill-ledger"]["related"]) == 5
    assert (seen["a head"], seen["a line, of a mention"], seen["none"]) == (3, 1, 0), seen
    assert seen["a line"] >= 3 and seen["line whole"] + seen["line cut"] >= 2, seen
    heads = [
        page["why"][0]["there"]
        for page in cut["quill-ledger"]["related"]
        if page["page"].startswith("marked")
    ]
    assert [sorted(set(claim) & {"text", "text_cut", "line", "line_cut"}) for claim in heads] == [
        ["text", "text_cut"]
    ] * 3


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
    assert one_sided == [] and pairs >= 500, (one_sided[:5], pairs)


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
