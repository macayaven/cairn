"""A page that copies its passages word for word passes the fact check (third report, must-fix 3).

The property: a note filed through `Memory.note` with `lint_gate`, the gate `serve` builds, is
saved, and its page holds each content passage of the note as a claim, word for word, that
cites it. Grounding and retention can only refuse such a page by reading a fact one way in its
passage and another way where they compare it. The report's case: a URL the extractor ended
before `>`, whose final underscore was then read with nothing after it.

Four families of made-up notes, each case filed in a project of its own:
- `ends`, the whole product, nothing drawn: a URL that ends in a number, a version or a
  date, in angle brackets, in a link, in parentheses, in quotes, bare and before a tag,
  ending in or followed by nothing, each of `_ * ~ ` ) ] > . , ; : ! ?`, each pair of them
  and each run of three and four. The report's note is one of its cases.
- `starts`, the whole product: the other edge of the same fact, a URL whose scheme holds a
  version or an underscore, after each character that may stand before it.
- `kinds`, the whole product: a number, a version, a date, a flag, a path and inline code,
  each standing where the URL's number stood: after a character that may stand before it,
  before a run of underscores and a `>` or a `<`.
- `mixed`, seeded: URLs, versions, flags, paths, numbers, dates and inline code with emphasis
  around them, across one of their edges, or a backslash at an edge; in a paragraph, a list
  item, a quote, a table cell, a nested item, and as lines of a fenced block.
- `pairs`, seeded: two passages, a fact at the end of the first and an emphasis character at
  the start of the second, as one note of two paragraphs, as two notes on one topic, and as
  the two lines of one passage; and the first passage twice in one note.

A failure names case ids and violation codes, never a note's text: `cases()` builds the same
notes on every run. The report's note is also filed by name, alone and in the places a
passage can stand, and so is the simplest note of each other shape the families found.

The last tests are the sweep of the other places that read a piece of a passage: what a fact
keeps of its passage, a code block's lines, a claim read as a paragraph whatever its passage
was, the `lint` command's reading of a body, and the one thing the place of a fact still
changes, white space that ends inline code."""

import random
from collections import Counter
from contextlib import closing

import pytest

from cairn.compiler import CONTENT_KINDS, CandidatePage, extractive, render_body
from cairn.facts import as_extracted, compared, extract_facts
from cairn.gate import lint_gate, strict_lint_gate, verbatim_gate
from cairn.lint import ClaimInput, PageInput, lint_pages
from cairn.memory import Memory, RequestError
from cairn.spans import chunk
from cairn.store import Claim, Store

SEED, MIXED, PAIRS = 20261121, 400, 150
TITLE = "Quill notes"
LITERAL = "See <https://example.invalid/(2_> today."  # the third report's note
EDGE = list("_*~`)]>.,;:!?")  # what a fact ends in or is followed by
ENDS = ["", *EDGE, *(a + b for a in EDGE for b in EDGE), *(c * n for c in EDGE for n in (3, 4))]
ROWS = {
    "in angle brackets": "<{url}{end}>",
    "after angle brackets": "<{url}>{end}",
    "in a link": "[link]({url}{end})",
    "after a link": "[link]({url}){end}",
    "in parentheses": "({url}{end})",
    "after parentheses": "({url}){end}",
    "in quotes": '"{url}{end}"',
    "bare": "{url}{end}",
    "before a tag": "{url}{end}<aside>",
}
# A fact at the URL's end, after a parenthesis: a number, a version, a date.
URLS = [f"https://example.invalid/({held}" for held in ("2", "v1.2.3", "2026-04-09")]
REVIEWED = [  # the notes of the reviewer's own test of this fault: four refused, two saved
    "See <https://example.invalid/(2_> today.",
    "See <https://example.invalid/(v1.2.3__> today.",
    "See <https://example.invalid/(2026-04-09___> today.",
    "See https://example.invalid/(2_<aside> today.",
    'See "https://example.invalid/(2_" today.',
    "See <https://example.invalid/(2____> today.",
]
NAMED = {  # the report's note where a passage can stand, and the other shapes found
    "in a list item": f"- {LITERAL}",
    "in an ordered item": f"1. {LITERAL}",
    "in a quote": f"> {LITERAL}",
    "in a nested item": f"- Ada\n  - {LITERAL}",
    "in a table cell": f"| Ada | {LITERAL} |\n| --- | --- |\n| a | b |",
    "on the second line": f"Ada keeps the log.\n{LITERAL}",
    "a date": "See <https://example.invalid/[2026-03-14_> today.",
    "a version": "See <https://example.invalid/{1.2.3__> today.",
    "a number with a comma": "See <https://example.invalid/(2,700_> today.",
    "a period first": "See <https://example.invalid/(2._> today.",
    "after it opened": "See <https://example.invalid/(_2_> today.",
    "before <": "See https://example.invalid/(2_<br> today.",
    "a scheme": "See ~v2_://example.invalid/a today.",
    "a scheme and an end": "See .v2.1__://example.invalid/(2_> today.",
    "commas at edges": "See `1,234`5 and 1,`234`, x://a/1,234,567 and (x://b/1,234). today.",
}
KINDS = ["2", "2,700", "5 min", "v1.2.3", "2026-04-09", "--dry-run", "/var/log/a_b", "`2_`", "`_2`"]
OPENS = ["", "(", "_", "(_", "<", "~", "-", "/"]
RUNS = ["", "_", "__", "___", "._"]
SCHEMES = ["https", "v2", "v2_", "v2__", "v2.1_", "x_", "a2_", "v2-"]
BEFORE = ["", *"([{\"'*`<>~-/|\\.+:!_"]
AFTER = [*EDGE, *"<|\\/-a ", ""]
MARKS = ["_", "__", "___", "*", "**", "~~"]
WORDS = ["Ada", "Brook", "Quill", "keeps", "the", "nightly", "log", "Fable", "Works", "port"]
TAILS = ["a", "(2", "[1.2.3", "{2026-03-14", "*v2", "(2,700", "(2.", "(_2", "x_y", "run_2"]
OTHERS = [
    "1.2.3",
    "v2",
    "0.1.0-rc.1",
    "--dry-run",
    "-x",
    "/var/log/a_b",
    "~/notes",
    "./run_2",
    "2,700",
    "5 min",
    "40-file",
    "$3.50",
    "2026-03-14",
    "2026-03-14T10:00:00Z",
    "`a_b`",
    "`2_`",
    "`_2`",
    "`(2_`",
    "``x`y``",
    "` a `",
    "` `",
    "`İß_2`",
    "`1,234`",
    "1,250x",
    "build_1,250",
    "/logs/batch_10,000/out.txt",
]
CONTEXTS = {
    "paragraph": "{line}",
    "list item": "- {line}",
    "ordered item": "1. {line}",
    "quote": "> {line}",
    "nested item": "- Ada\n  - {line}",
    "table cell": "| Ada | {cell} |\n| --- | --- |\n| a | b |",
    "fence": "```\n{line}\n\n# {edge}\n  {edge}\n{edge}\n```",
    "fence in a quote": "> ~~~\n> {edge}\n> {line}\n> ~~~",
    "fence in an item": "- Ada\n\n  ```\n  {edge}\n  ```",
}


def words(rng: random.Random, n: int | None = None) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(n or rng.randint(1, 3)))


def url(rng: random.Random) -> str:
    scheme = rng.choice(SCHEMES) if rng.random() < 0.3 else "https"
    end = "".join(rng.choice(EDGE) for _ in range(rng.randint(0, 2)))
    row = rng.choice(list(ROWS.values()))
    return row.format(url=f"{scheme}://example.invalid/{rng.choice(TAILS)}", end=end)


def fact(rng: random.Random) -> str:
    return url(rng) if rng.random() < 0.5 else rng.choice(OTHERS)


def edged(rng: random.Random) -> str:
    """A fact with emphasis around it, across one of its edges, or a backslash at an edge."""
    f, mark, word = fact(rng), rng.choice(MARKS), words(rng, 1)
    return rng.choice(
        [
            f"{mark}{f}{mark}",
            f"{mark}{f} {word}{mark}",  # opens at the fact, closes after a word outside it
            f"{mark}{word} {f}{mark}",  # opens before a word outside it, closes at the fact
            f"{f}{mark}{word}{mark}",  # opens at the fact's end, closes outside it
            f"{mark}{word}{mark}{f}",  # closes at the fact's start
            f"{f} {word}{mark}",  # what opens inside the fact closes outside it
            f"{mark}{word} {f}",  # what opens outside the fact closes inside it
            f"{f}{mark}",
            f"{mark}{f}",
            f"\\{mark[0]}{f}",
            f"{f}\\{mark[0]}",
            f"{f}\\",
            f"\\{f}",
            f,  # alone: its edges are what the sentence puts around it
        ]
    )


def sentence(rng: random.Random) -> str:
    after = "".join(rng.choice(AFTER) for _ in range(rng.randint(0, 2)))
    tail = rng.choice(["", " " + words(rng) + rng.choice([".", "", "!"])])
    return f"{words(rng)} {rng.choice(BEFORE)}{edged(rng)}{after}{tail}"


def mixed(rng: random.Random) -> tuple[str, str]:
    """(context, note): a sentence, and a fact alone on a line where the context is a fence."""
    context = rng.choice(list(CONTEXTS))
    line = sentence(rng)
    edge = edged(rng) + rng.choice(AFTER)
    return context, CONTEXTS[context].format(line=line, cell=line.replace("|", "\\|"), edge=edge)


def pair(rng: random.Random) -> tuple[str, str]:
    """Two passages: a fact at the end of the first, an emphasis character starting the next."""
    first = f"{words(rng)} {rng.choice(BEFORE)}{edged(rng)}{rng.choice(['', *EDGE])}"
    mark = rng.choice(MARKS)
    second = f"{mark}{words(rng)}" + rng.choice(["", mark, f" {fact(rng)}{mark}"]) + "."
    return first, second


def cases() -> dict[str, dict[str, list[str]]]:
    """Each family's cases: an id (the name of its project) and the notes filed there."""
    ends = [
        f"See {row.format(url=held, end=end)} today."
        for held in URLS
        for row in ROWS.values()
        for end in ENDS
    ]
    starts = [f"See {b}{s}://example.invalid/a today." for s in SCHEMES for b in BEFORE]
    rng = random.Random(SEED)
    drawn = [mixed(rng)[1] for _ in range(MIXED)]
    pairs: dict[str, list[str]] = {}
    for n in range(PAIRS):
        first, second = pair(rng)
        pairs |= {
            f"two-paragraphs-{n:03d}": [f"{first}\n\n{second}"],
            f"two-notes-{n:03d}": [first, second],
            f"two-lines-{n:03d}": [f"{first}\n{second}"],
            f"same-twice-{n:03d}": [f"{first}\n\n{first}"],
        }
    kinds = [
        f"See {before}{held}{run}{after} today."
        for held in KINDS
        for before in OPENS
        for run in RUNS
        for after in ("", ">", "<")
    ]
    return {
        "ends": {f"ends-{n:04d}": [text] for n, text in enumerate(ends)},
        "kinds": {f"kinds-{n:04d}": [text] for n, text in enumerate(kinds)},
        "starts": {f"starts-{n:03d}": [text] for n, text in enumerate(starts)},
        "mixed": {f"mixed-{n:03d}": [text] for n, text in enumerate(drawn)},
        "pairs": pairs,
    }


def content(text: str) -> list:
    return [s for s in chunk("", text) if s.kind in CONTENT_KINDS]


def reads_differently_alone(notes: list[str]) -> bool:
    """Whether some fact of the notes' passages, read alone, is not in its passage as read: the
    public reader on one value and on a whole text, the comparison the report describes."""
    return any(
        f.kind != "code_block" and as_extracted(f.value) not in as_extracted(span.text)
        for text in notes
        for span in content(text)
        for f in extract_facts(span)
    )


def file(memory: Memory, store: Store, name: str, notes: list[str]) -> list[str]:
    """File the notes in a project of their own, on one topic. What is wrong, as codes: nothing
    when each note is saved and its page holds each content passage of it as a claim, word for
    word, that cites it."""
    project = store.create_project(name)
    filed = []
    for text in notes:
        try:
            answer = memory.note(name, text, title=TITLE, topic=TITLE)
        except RequestError as error:  # nothing stored: the generator made no passage
            return [f"refused: {type(error).__name__}"]
        if answer["status"] not in ("saved", "unchanged"):
            return sorted({v.split(":")[0] for v in answer["violations"]}) or [answer["status"]]
        filed.append(answer["note_id"])
    for note_id in filed:
        slug = store.note_page(project.id, note_id)[1]
        if slug is None:
            return ["no page cites the note"]
        copied = {(c.text, i) for c in store.get_page(project.id, slug).claims for i in c.span_ids}
        passages = [s for s in store.note_spans(note_id) if s.kind in CONTENT_KINDS]
        if not passages or any((s.text, s.id) not in copied for s in passages):
            return ["a passage is not on the page, word for word and cited"]
    return []


def refused(made: dict[str, list[str]]) -> dict[str, list[str]]:
    """The cases `file` finds something wrong with, and what. A store holds 500 projects, then
    the next is opened: filing slows as one store's projects grow."""
    found, names = {}, list(made)
    for at in range(0, len(names), 500):
        with closing(Store(":memory:")) as store:
            memory = Memory(store, lint_gate)
            for name in names[at : at + 500]:
                if faults := file(memory, store, name, made[name]):
                    found[name] = faults
    return found


@pytest.fixture
def store():
    with closing(Store(":memory:")) as store:
        yield store


def test_the_report_note_and_the_reviewer_s_six_are_cases_of_the_whole_product():
    made = cases()
    assert [LITERAL] in made["ends"].values()
    for text in REVIEWED:
        assert [text] in made["ends"].values(), REVIEWED.index(text)
    assert len(made["ends"]) == len(URLS) * len(ROWS) * (1 + 3 * len(EDGE) + len(EDGE) ** 2)
    assert len(made["starts"]) == len(SCHEMES) * len(BEFORE)


def test_every_context_holds_facts_and_most_pairs_are_two_passages():
    rng = random.Random(SEED)
    with_a_fact = Counter(
        context
        for context, text in (mixed(rng) for _ in range(MIXED))
        if any(extract_facts(span) for span in content(text))
    )
    assert all(with_a_fact[context] >= 20 for context in CONTEXTS), with_a_fact
    passages = Counter(
        (name[:-4], len(content(notes[0]))) for name, notes in cases()["pairs"].items()
    )
    assert passages["two-paragraphs", 2] > PAIRS * 0.9, passages
    assert passages["two-lines", 1] == PAIRS, passages


def test_each_family_holds_facts_that_read_differently_alone():
    """So the property is not kept by cases in which nothing could go wrong. These are the
    cases a reader of one value, out of its passage, gets wrong: at 23c24229 each of them was
    a note the gate refused, and no other case was."""
    alone = {
        family: sum(reads_differently_alone(notes) for notes in made.values())
        for family, made in cases().items()
    }
    assert alone["ends"] == 42 and alone["starts"] == 33, alone  # whole products
    assert alone["kinds"] == 0, alone  # a fact of another kind read the same alone
    assert alone["mixed"] >= 3 and alone["pairs"] >= 20, alone  # drawn


@pytest.mark.parametrize("family", ["ends", "starts", "kinds", "mixed", "pairs"])
def test_a_page_that_copies_its_passages_passes_the_fact_check(family):
    found = refused(cases()[family])
    assert found == {}, (len(found), found)


def test_the_report_note_is_saved_and_its_page_cites_it(store):
    project = store.create_project("report")
    answer = Memory(store, lint_gate).note("report", LITERAL, title=TITLE, topic=TITLE)
    assert [v.split(":")[0] for v in answer["violations"]] == []
    assert answer["status"] == "saved"
    (passage,) = store.note_spans(answer["note_id"])
    page = store.get_page(project.id, answer["page"])
    assert [(c.text, list(c.span_ids)) for c in page.claims] == [(LITERAL, [passage.id])]
    assert store.note_page(project.id, answer["note_id"]) == (False, page.slug)


@pytest.mark.parametrize("name", sorted(NAMED))
def test_the_report_note_elsewhere_and_the_other_shapes_are_saved_and_cited(store, name):
    assert file(Memory(store, lint_gate), store, "named", [NAMED[name]]) == []


@pytest.mark.parametrize("gate", [lint_gate, strict_lint_gate, verbatim_gate])
@pytest.mark.parametrize("text", [LITERAL, NAMED["a scheme"]])
def test_every_gate_that_checks_facts_passes_the_copy(gate, text):
    spans = {span.id: span for span in chunk("note", text)}
    claims = extractive(TITLE, list(spans.values())).claims
    body = render_body(TITLE, claims, spans)
    page = CandidatePage(slug="quill-notes", title=TITLE, body=body, claims=claims)
    assert [v.split(":")[0] for v in gate(page, spans).violations] == []


def facts_of_every_case() -> list:
    """(passage, fact) for every fact of every content passage of every case."""
    texts = [text for made in cases().values() for notes in made.values() for text in notes]
    return [(span, f) for text in texts for span in content(text) for f in extract_facts(span)]


def test_a_fact_keeps_its_own_characters_of_the_passage_as_read():
    for span, f in facts_of_every_case():
        assert (f.value, len(f.as_read)) == (span.text[f.start : f.end], len(f.value))
        changed = {(was, now) for was, now in zip(f.value, f.as_read, strict=True) if was != now}
        assert changed <= {("_", "*")}, (f.kind, changed)


def test_only_a_url_reads_differently_alone_and_no_line_of_a_code_block_does():
    """Reading a fact's text alone, as the gate did, against the same text read in its passage:
    of the kinds only a URL ever differs, so nothing else was refused or let through by it;
    and a code block's lines, which were read alone too, each read the same either way."""
    differ, lines = Counter(), Counter()
    for _, f in facts_of_every_case():
        if f.kind != "code_block":
            differ[f.kind] += as_extracted(f.value) != compared(f.as_read)
            continue
        pairs = zip(f.value.split("\n"), f.as_read.split("\n"), strict=True)
        for alone, read in pairs:
            lines["with an underscore"] += "_" in alone
            lines["that differ"] += as_extracted(alone) != compared(read)
    assert {kind for kind, n in differ.items() if n} == {"url"}, differ
    assert set(differ) == {"url", "number", "version", "date", "flag", "path", "inline_code"}
    assert lines["with an underscore"] > 100 and lines["that differ"] == 0, lines


@pytest.mark.parametrize("kind", sorted(CONTENT_KINDS))
def test_a_text_reads_the_same_whatever_kind_of_passage_it_is(kind):
    """lint reads a claim as a paragraph, whatever passage it copies."""
    for span, _ in facts_of_every_case()[::7]:
        other = span.model_copy(update={"kind": kind})
        assert extract_facts(other) == extract_facts(span)


def test_the_lint_command_finds_every_fact_of_passages_copied_into_a_body():
    """The `lint` command reads a page by its body, whole (not by its claims): each passage
    on lines of its own between blank lines reads there as it does alone."""
    found = Counter()
    for family, made in cases().items():
        for notes in made.values():
            spans = [span for n, text in enumerate(notes) for span in chunk(f"note-{n}", text)]
            copied = [span for span in spans if span.kind in CONTENT_KINDS]
            claims = [
                ClaimInput(id=f"c{n}", text=span.text, span_ids=[span.id])
                for n, span in enumerate(copied)
            ]
            body = "# Quill notes\n\n" + "\n\n".join(span.text for span in copied) + "\n"
            report = lint_pages([PageInput(slug="quill", text=body, claims=claims)], spans)
            found.update((family, v.code) for v in report.violations)
    assert found == Counter()


@pytest.mark.parametrize(
    ("passage", "claim"),
    [("Keep `a ` here.", "Keep `a` here."), ("Keep `a` here.", "Keep `a ` here.")],
)
def test_white_space_that_ends_inline_code_is_not_compared_by_the_fact_check(passage, claim):
    """A limit, as before this fix: the white-space rule (compared_text) is applied to the
    fact's own text, whose end it takes for a line's end. The fact check passes the changed
    claim; the verbatim gate, which compares whole texts, refuses it."""
    spans = {span.id: span for span in chunk("note", passage)}
    claims = (Claim(id="cl_a", text=claim, span_ids=tuple(spans)),)
    page = CandidatePage(
        slug="q", title=TITLE, body=render_body(TITLE, claims, spans), claims=claims
    )
    assert lint_gate(page, spans).violations == []
    assert strict_lint_gate(page, spans).violations == []
    assert [v.split(":")[0] for v in verbatim_gate(page, spans).violations] == [
        "grounding.not_verbatim"
    ]
