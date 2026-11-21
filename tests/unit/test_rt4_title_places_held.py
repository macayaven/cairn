"""What an adversarial read of the title rule of related pages attacked, past the fix that
judges each place a title stands by itself, and found to hold (RT4, fourth round).

The oracle here is independent of cairn's reader: it does not use SQLite's highlight,
relations.prose_runs, spans.code_read or anything of cairn.store.relating. It tokenises a claim
and a title as FTS5's unicode61 tokenizer is documented to for the index's declaration
(no options: categories "L* N* Co", case folded, remove_diacritics 1, a diacritic going on
with a word and starting none, a code point SQLite's older Unicode does not assign read as a
word's), finds every run of
the title's tokens in the claim, and calls a run prose when none of its characters lies in
code as the page renders the claim (cairn.web.markdown.render, page.html's `md`: every
character of the run's words swapped for a private-use marker, the claim rendered, and each
marker looked for inside <code> or <pre>, or a code element between two of them), nor in a
fact the rule says names nothing (cairn.facts.extract_facts: a url, a path, a flag or a
version; the coordinator's ruling of 2026-11-21: the page alone says what is code, so the
facts' inline code and code blocks are not asked). The facts are cairn's own reading: the
oracle does not check them. Two tests check the oracle: its tokens against the index's own
(fts5vocab, never highlight) and its parser against the page's renderer.

The cases found one step past each fix, now fixed and kept as regression tests, are in
test_rt4_title_places_found.py and test_rt4_title_places_found2.py. Made-up names."""

import json
import random
import sqlite3
import unicodedata
from contextlib import closing
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
import test_related_unit as api
from markdown_it import MarkdownIt
from test_relating_cost_unit import watched

from cairn import relations
from cairn.compiler import compile_note
from cairn.facts import extract_facts
from cairn.gate import lint_gate
from cairn.llm import FakeModel
from cairn.llm_compiler import LLMComposer
from cairn.memory import Memory
from cairn.spans import Span, code_read
from cairn.store import Store, relating, schema
from cairn.web.markdown import render

env = api.env
TITLE = "Step by Step"

# The oracle: the index's tokens, as documented

DIACRITICS = frozenset(  # remove_diacritics=1: the marks Latin letters decompose into
    mark
    for code in [*range(0xC0, 0x250), *range(0x1E00, 0x1F00)]
    for mark in unicodedata.normalize("NFD", chr(code))[1:]
    if unicodedata.combining(mark)
)


def token_char(ch: str) -> bool:
    """Whether `ch` starts a word, or goes on with one (a diacritic only goes on with one)."""
    if ch in DIACRITICS:
        return False
    if ord(ch) < 128:
        return ch.isalnum()
    category = unicodedata.category(ch)
    # SQLite's tables are of an older Unicode: a code point unassigned there is read as a
    # word's (the emoji modifiers came later); the calibration tests guard the rest
    return category[0] in "LN" or category in ("Co", "Cn") or 0x1F3FB <= ord(ch) <= 0x1F3FF


def folded(ch: str) -> str:
    if ch in DIACRITICS:
        return ""
    parts = unicodedata.normalize("NFD", ch)
    if len(parts) == 2 and parts[0].isascii() and unicodedata.combining(parts[1]):
        return parts[0].lower()
    return ch.casefold() if len(ch.casefold()) == 1 else ch


def tokens(text: str) -> list[tuple[str, int, int]]:
    found, at = [], 0
    while at < len(text):
        if not token_char(text[at]):
            at += 1
            continue
        end = at
        while end < len(text) and (token_char(text[end]) or text[end] in DIACRITICS):
            end += 1  # a diacritic goes on with the word before it, and starts none
        found.append(("".join(map(folded, text[at:end])), at, end))
        at = end
    return found


def occurrences(text: str, title: str) -> list[tuple[int, int]]:
    want, have = [t for t, _, _ in tokens(title)], tokens(text)
    n = len(want)
    return [
        (have[i][1], have[i + n - 1][2])
        for i in range(len(have) - n + 1)
        if n and [t for t, _, _ in have[i : i + n]] == want
    ]


# The oracle: code as the page renders it

PAGE = (
    MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    .enable("table")
    .disable(["image", "heading", "lheading"])
)


class Walk(HTMLParser):
    """The rendered page as events: a code element opened or closed, and each character
    shown, with whether it stands in code (an attribute's characters count, in code when the
    element is <code> or <pre> or stands in one)."""

    def __init__(self, attributes: bool = True) -> None:
        super().__init__(convert_charrefs=True)
        self.depth, self.events, self.attributes = 0, [], attributes

    def handle_starttag(self, tag, attrs):
        if tag in ("code", "pre"):
            self.events.append(("open", tag))
            self.depth += 1
        for _, value in attrs if self.attributes else ():
            self.events += [("char", ch, self.depth > 0) for ch in value or ""]

    def handle_endtag(self, tag):
        if tag in ("code", "pre"):
            self.depth -= 1
            self.events.append(("close", tag))

    def handle_data(self, data):
        self.events += [("char", ch, self.depth > 0) for ch in data]


def walked(html: str, attributes: bool = True) -> list:
    walk = Walk(attributes)
    walk.feed(html)
    walk.close()
    return walk.events


def blocks(text: str) -> list:
    return [(t.type, t.map) for t in PAGE.parse(text) if t.nesting >= 0]


def in_page_code(text: str, start: int, end: int) -> bool:
    markers, chars, code = {}, list(text), 0xF0000
    for at in range(start, end):
        if token_char(text[at]):
            chars[at], markers[chr(code)] = chr(code), at
            code += 1
    swapped = "".join(chars)
    assert blocks(swapped) == blocks(text), "the oracle abstains: the swap moved a block"
    events = walked(str(render(swapped)))  # the page's own rendering is the standard
    seen: dict[str, list[bool]] = {}
    places = [n for n, e in enumerate(events) if e[0] == "char" and e[1] in markers]
    for n in places:
        seen.setdefault(events[n][1], []).append(events[n][2])
    code_lines = {
        line
        for t in PAGE.parse(text)
        if t.type in ("fence", "code_block") and t.map
        for line in range(*t.map)
    }
    for marker, at in markers.items():
        if marker not in seen and text.count("\n", 0, at) in code_lines:
            return True  # a fence's opening line, not shown: code
        if any(seen.get(marker, ())):
            return True
    return bool(places) and any(e[0] != "char" for e in events[places[0] : places[-1]])


def fact_stretches(text: str) -> list[tuple[int, int]]:
    span = Span(
        id="sp_oracle",
        source_id="n",
        kind="paragraph",
        heading_path=(),
        start_line=1,
        end_line=1 + text.count("\n"),
        text=text,
    )
    return [
        (fact.start, fact.end)
        for fact in extract_facts(span)
        if fact.kind in ("url", "path", "flag", "version")
    ]


def prose_places(text: str, title: str) -> list[tuple[int, int]]:
    facts = fact_stretches(text)
    return [
        (s, e)
        for s, e in occurrences(text, title)
        if not any(a < e and s < b for a, b in facts) and not in_page_code(text, s, e)
    ]


# Cases decided by hand: (title, claim, whether it names the title in prose)

NFC, NFD = (unicodedata.normalize(form, "Café Noir") for form in ("NFC", "NFD"))
CASES = [
    # overlapping, adjacent, code and prose in either order, straddling a code span's edge
    (TITLE, "Read `Step` by Step by Step today.", True),
    (TITLE, "Step by Step by Step by Step.", True),
    (TITLE, "Step by Step Step by Step.", True),
    ("Echo Echo", "Say Echo Echo Echo aloud.", True),
    ("Echo Echo", "Say Echo Echo Echo Echo aloud.", True),
    ("Echo Echo", "Say `Echo` Echo `Echo` aloud.", False),
    ("Echo Echo", "Say `Echo` Echo Echo `Echo` aloud.", True),
    (TITLE, "Read `Step by Step` and Step by Step.", True),
    (TITLE, "Read Step by Step and `Step by Step`.", True),
    (TITLE, "Read Step `by Step` today.", False),
    (TITLE, "Read `Step by` Step today.", False),
    (TITLE, "Read `Step` by Step by Step by `Step` today.", True),
    # split by a code span, a line break, punctuation, an entity, invisible characters
    ("Alpha Beta", "Alpha `x` Beta.", False),
    ("Alpha Beta", "Alpha `x` Beta and `Alpha Beta`.", False),
    ("Alpha Beta", "Alpha` `Beta.", False),
    ("Alpha Beta", "Alpha`x`Beta today.", False),
    ("Alpha Beta", "Alpha\nBeta today.", True),
    ("Alpha Beta", "Alpha, Beta today.", True),
    ("Alpha Beta", "Alpha&amp;Beta today.", False),
    ("Alpha Beta", "Alpha&nbsp;Beta today.", False),
    ("Alpha Beta", "Alpha­Beta today.", True),
    ("Alpha Beta", "Alpha​Beta today.", True),
    # a fact or a code span against the title's first or last word
    ("Alpha Beta", "Alpha Beta`x` today.", True),
    ("Alpha Beta", "`x`Alpha Beta today.", True),
    ("Alpha Beta", "Alpha Beta/usr/lib today.", True),
    ("Alpha Beta", "Alpha Beta--audit-keep today.", True),
    ("Alpha Beta", "see Alpha Beta:https://example.com/x today.", True),
    ("Alpha Beta", "Alpha Beta-v1.2.3 today.", True),
    ("Alpha Beta", "Alpha Beta`--audit-keep` today.", True),
    # digits, an apostrophe, a hyphen, letters with marks, one word, a prefix
    ("Route 66", "Take Route 66 west.", True),
    ("O'Brien Notes", "Read O'Brien Notes today.", True),
    ("Well-Known Path", "Take the well-known path.", True),
    ("Well-Known Path", "Take the `well`-known path.", False),
    (NFC, f"Order {NFD} today.", True),
    (NFD, f"Order {NFC} today.", True),
    (NFC, f"Order `{NFD}` today.", False),
    ("Ñandú Über", "Saw ñandú über the hill.", True),
    ("Ñandú Über", unicodedata.normalize("NFD", "Saw Ñandú Über and `Ñandú Über`."), True),
    ("Echo", "Say Echoes and Echo.", True),
    ("Echo", "Say Echoes.", False),
    # every kind of code
    (TITLE, "Read ``Step by Step`` today.", False),
    (TITLE, "Read ```Step by Step``` today.", False),
    (TITLE, "Read `` `Step by Step` `` today.", False),
    (TITLE, "Read ``Step ` by Step`` today.", False),
    (TITLE, "Read ` Step by Step ` today.", False),
    # the page: <p>Read `Step by Step` today.</p>; the coordinator's ruling, 2026-11-21: the page alone says what is code  # noqa: E501
    (TITLE, "Read \\`Step by Step\\` today.", True),
    (TITLE, "Read `Step by Step\\` today` x.", False),
    (TITLE, "```text\nStep by Step\n```", False),
    (TITLE, "```Step by Step\nx\n```", False),
    (TITLE, "~~~ Step by Step\nx\n~~~", False),
    (TITLE, "```\nStep by Step", False),
    (TITLE, "Intro line.\n    Step by Step", True),  # a lazy continuation
    (TITLE, "- Item.\n\n    Step by Step", True),
    (TITLE, "- Item.\n\n        Step by Step", False),
    (TITLE, "- Item.\n\n\t\tStep by Step", False),
    (TITLE, "\tStep by Step", False),
    (TITLE, "> Quote.\n>\n>     Step by Step", False),
    (TITLE, "> Quote.\n>     Step by Step", True),
    (TITLE, "Notes\n---\n    Step by Step", False),
    (TITLE, "Step by Step\n---", True),
    (TITLE, "`Step by Step`\n---", False),
    (TITLE, "<code>Step by Step</code>", True),  # raw HTML is text on the page
    (TITLE, "<pre>\nStep by Step\n</pre>", True),
    (TITLE, "[Step by Step](https://example.com/a)", True),
    (TITLE, "[docs](<Step by Step>)", True),
    (TITLE, '[docs](https://example.com/a "Step by Step")', True),
    (TITLE, "![Step by Step](https://example.com/a.png)", True),
    (TITLE, "![`Step by Step`](https://example.com/a.png)", False),
    (TITLE, "![a `b](v) Step by Step` x", False),
    (TITLE, "<https://example.com/Step-by-Step>", False),
    (TITLE, "| a | b |\n|---|---|\n| `x` | Step by Step |", True),
    (TITLE, "| a | b |\n|---|---|\n| `Step by Step` | x |", False),
    (TITLE, "| a | b |\n|---|---|\n| `x \\| y` | Step by Step |", True),
    (TITLE, "| a | `b` |\n|---|---|\n| Step by Step | `Step by Step` |", True),
    (TITLE, "1. `x`\n   Step by Step", True),
    (TITLE, "*Step* by **Step** today.", True),
]


def store_at(tmp_path, name: str) -> tuple[Store, Memory]:
    store = Store(tmp_path / f"{name}.db", clock=lambda: api.WRITTEN)
    store.create_project("ops")
    return store, Memory(store, lint_gate)


def names_by_cairn(memory: Memory, title: str, claim: str) -> bool:
    focus = memory.note("ops", "The field guide is on the shelf.", title=title, topic=title)
    other = memory.note("ops", claim, title="Reader", topic="Reader")
    (stated,) = memory.page("ops", other["page"])["claims"]
    assert stated["text"] == claim  # the note is one passage, and the claim is its text
    answer = memory.related("ops", focus["page"], 20)
    assert answer["searched"]["complete"]
    return any(why["kind"] == "mentioned_by" for p in answer["related"] for why in p["why"])


def test_the_oracles_tokens_are_the_index_tokens_on_every_text_it_judges():
    texts = [text for case in CASES for text in case[:2]]
    texts += ["a" + ch + "b" for ch in sorted(set("".join(texts)))]
    with sqlite3.connect(":memory:") as con:
        con.execute(schema._CLAIM_INDEX.format(index="t"))
        con.execute("CREATE VIRTUAL TABLE v USING fts5vocab(t, instance)")
        con.executemany("INSERT INTO t (rowid, text) VALUES (?, ?)", enumerate(texts, 1))
        index: dict[int, list[str]] = {}
        for doc, term in con.execute(
            "SELECT doc, term FROM v WHERE col = 'text' ORDER BY doc, offset"
        ):
            index.setdefault(doc, []).append(term)
    for n, text in enumerate(texts, 1):
        assert index.get(n, []) == [t for t, _, _ in tokens(text)], text


def test_the_oracles_parser_shows_code_where_the_page_does():
    for _, claim, _ in CASES:
        assert walked(PAGE.render(claim), False) == walked(str(render(claim)), False), claim


@pytest.mark.parametrize(("title", "claim", "names"), CASES)
def test_each_place_of_a_title_is_judged_as_the_oracle_judges_it(tmp_path, title, claim, names):
    assert bool(prose_places(claim, title)) is names  # the oracle agrees with the hand
    store, memory = store_at(tmp_path, "case")
    try:
        assert names_by_cairn(memory, title, claim) is names
    finally:
        store.close()


ATOMS = ["Step", "by", "Step by Step", " ", " ", "\n", "\n\n", "`", "``", "x", "- ", "> ", "    "]
ATOMS += ["\t", "1. ", "|", "\\|", "[", "](u)", "*", "~~~", "```"]


def test_generated_claims_are_judged_as_the_oracle_judges_them(tmp_path):
    """Seeded: 1,500 draws of the atoms above, those that hold the title kept (no heading
    mark, no picture: those are test_rt4_title_places_found.py's), each judged by cairn and
    by the oracle; the oracle abstains on none here."""
    rng, store, memory = random.Random(20261121), *store_at(tmp_path, "generated")
    try:
        project, verdicts = 0, {True: 0, False: 0}
        for _ in range(1500):
            claim = "".join(rng.choice(ATOMS) for _ in range(rng.randint(3, 14))).strip()
            if not occurrences(claim, TITLE) or not claim:
                continue
            project += 1
            name = f"p{project}"
            store.create_project(name)
            focus = memory.note(name, "The guide.", title=TITLE, topic=TITLE)
            other = memory.note(name, claim, title="Reader", topic="Reader")
            texts = [c["text"] for c in memory.page(name, other["page"])["claims"]]
            answer = memory.related(name, focus["page"], 20)
            got = any(w["kind"] == "mentioned_by" for p in answer["related"] for w in p["why"])
            assert got is any(prose_places(text, TITLE) for text in texts), claim
            verdicts[got] += 1
        assert verdicts[True] >= 250 and verdicts[False] >= 60, verdicts
    finally:
        store.close()


# The cutoffs


def plain(env, n: int) -> str:
    return api.note(env, f"Group {n}", f"Group {n} reads Step by Step today.")["page"]


def surfaces(env, focus: str) -> dict[str, tuple[bool, list[str]]]:
    response = api.rest(env, focus, "?limit=20")
    assert response.status_code == 200
    answers = {
        "Memory": Memory(env.store, lint_gate).related("ops", focus, 20),
        "REST": response.json(),
        "MCP": api.answer(env.client, env.key, "related", project="ops", slug=focus, limit=20),
    }
    return {
        reader: (
            answer["searched"]["complete"],
            sorted(
                page["page"]
                for page in answer["related"]
                if any(why["kind"] == "mentioned_by" for why in page["why"])
            ),
        )
        for reader, answer in answers.items()
    }


@pytest.mark.parametrize("named", [5, 6])
def test_five_pages_naming_a_title_relate_and_six_do_not_whatever_holds_it_in_code(env, named):
    focus = api.note(env, TITLE, "The field guide is on the shelf.")["page"]
    pages = [plain(env, n) for n in range(named)]
    for n in range(3):  # beside them, pages that hold it only in code: they never count
        api.note(env, f"Code {n}", f"Run `Step by Step` {n} and ```Step by Step``` again.")
    expected = sorted(pages) if named <= relations.COMMON else []
    assert surfaces(env, focus) == dict.fromkeys(("Memory", "REST", "MCP"), (True, expected))


@pytest.mark.parametrize("in_code", [True, False], ids=["in-code", "in-prose"])
@pytest.mark.parametrize("held", [63, 64])
def test_claims_that_hold_the_title_count_in_prose_or_not_at_the_bound(env, held, in_code):
    """CLAIMS_COMMON counts the claims of other pages that hold the title's words, in prose or
    not (the ruling): 63 on one page and one more elsewhere relate; 64 and one more do not,
    and the answer is complete and holds no mention."""
    focus = api.note(env, TITLE, "The field guide is on the shelf.")["page"]
    said = "Ada reads `Step by Step` {n}." if in_code else "Ada reads Step by Step {n}."
    many = api.note(env, "Many", "\n\n".join(said.format(n=n) for n in range(held)))["page"]
    claims = api.answer(env.client, env.key, "page", project="ops", slug=many)["claims"]
    assert len(claims) == held
    one = plain(env, 0)
    assert held + 1 - relations.CLAIMS_COMMON in (0, 1)
    expected = (
        [] if held + 1 > relations.CLAIMS_COMMON else sorted([one, *([] if in_code else [many])])
    )
    assert surfaces(env, focus) == dict.fromkeys(("Memory", "REST", "MCP"), (True, expected))


def test_the_tool_and_the_contract_say_the_claims_count_in_prose_or_not():
    import cairn.server

    for text in (api.tools()["related"].description, cairn.server.__doc__):
        assert "in prose or not" in " ".join(text.split())


# What a reason shows of a claim cut


@pytest.mark.parametrize(
    "prose",
    ["Then Step by Step again.", "Then `Step` by Step by Step again."],
    ids=["plain", "overlap"],
)
def test_a_claim_cut_gives_the_line_of_its_first_place_in_prose_never_one_in_code(env, prose):
    focus = api.note(env, TITLE, "The field guide is on the shelf.")["page"]
    lines = ["Read `Step by Step` first.", "Read `Step` by `Step by Step` too."]
    lines += [f"Filler {n} keeps the claim long enough to be cut." for n in range(14)]
    text = "\n".join([*lines, prose, "And `Step by Step` once more."])
    assert len(text) > relations.TEXT_MAX
    reader = api.note(env, "Reader", text)["page"]
    (claim,) = api.answer(env.client, env.key, "page", project="ops", slug=reader)["claims"]
    assert claim["text"] == text
    (page,) = api.answer(env.client, env.key, "related", project="ops", slug=focus)["related"]
    (why,) = page["why"]
    assert (why["kind"], why["there"]["text_cut"], why["there"]["line"]) == (
        "mentioned_by",
        True,
        prose,
    )
    assert "text" not in why["there"]


# The database in memory: cost, closing, locks, reach


@pytest.fixture
def scratch_watch(monkeypatch):
    """Every database in memory a read opens: its statements, and whether it was closed."""
    seen = SimpleNamespace(connections=[], statements=[], fail=False, during=None)
    opened = relating._scratch

    def watched_scratch():
        con = opened()
        con.set_trace_callback(seen.statements.append)
        seen.connections.append(con)
        if seen.during is not None:
            seen.during()
        if seen.fail:
            con.set_authorizer(
                lambda action, *_: (
                    sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT else sqlite3.SQLITE_OK
                )
            )
        return con

    monkeypatch.setattr(relating, "_scratch", watched_scratch)
    return seen


def closed(con: sqlite3.Connection) -> bool:
    try:
        con.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


def test_a_read_opens_one_database_in_memory_runs_three_statements_and_closes_it(
    env, scratch_watch
):
    focus = api.note(env, TITLE, "The field guide is on the shelf.")["page"]
    for n in range(4):
        api.note(env, f"Group {n}", f"Group {n} reads `Step` by Step by Step {n}.\n\nAnd again.")
    with watched(env.store) as seen:
        Memory(env.store, lint_gate).related("ops", focus, 20)
    (con,) = scratch_watch.connections
    assert closed(con)
    statements = [s for s in scratch_watch.statements if not s.lstrip().startswith("--")]
    assert len(statements) == 3, statements
    assert len(seen.statements) <= 17


def test_an_error_inside_the_database_in_memory_closes_it_and_the_next_read_runs(
    env, scratch_watch
):
    focus = api.note(env, TITLE, "The field guide is on the shelf.")["page"]
    api.note(env, "Reader", "Reader reads `Step` by Step by Step.")
    scratch_watch.fail = True
    with pytest.raises(sqlite3.DatabaseError):
        Memory(env.store, lint_gate).related("ops", focus, 20)
    assert closed(scratch_watch.connections[-1])
    scratch_watch.fail = False
    api.note(env, "Other", "Other reads Step by Step.")  # the store writes again
    answer = Memory(env.store, lint_gate).related("ops", focus, 20)
    assert answer["searched"]["complete"] and len(answer["related"]) == 2
    assert all(closed(con) for con in scratch_watch.connections)


def test_a_read_writes_nothing_holds_no_write_lock_and_waits_on_none(env, tmp_path, scratch_watch):
    focus = api.note(env, TITLE, "The field guide is on the shelf.")["page"]
    api.note(env, "Reader", "Reader reads `Step` by Step by Step.")
    side = sqlite3.connect(tmp_path / "cairn.db", timeout=0, isolation_level=None)
    try:
        version = side.execute("PRAGMA data_version").fetchone()
        taken = []

        def write_beside():  # mid-read: another connection takes the write lock at once
            side.execute("BEGIN IMMEDIATE")
            taken.append(True)
            side.execute("ROLLBACK")

        scratch_watch.during = write_beside
        Memory(env.store, lint_gate).related("ops", focus, 20)
        assert taken == [True]
        assert side.execute("PRAGMA data_version").fetchone() == version  # nothing written
        scratch_watch.during = None
        side.execute("BEGIN IMMEDIATE")  # and the reverse: a writer holds it while a read runs
        try:
            answer = Memory(env.store, lint_gate).related("ops", focus, 20)
        finally:
            side.execute("ROLLBACK")
        assert [p["page"] for p in answer["related"]] == ["reader"]
    finally:
        side.close()


def test_the_database_in_memory_is_given_only_passages_of_the_project_asked(env, scratch_watch):
    vault = env.store.create_project("vault")
    for n in range(3):  # before the project's rows, and below between them: inside its span
        api.note(env, f"Cellar {n}", f"Juniper {n} reads Step by Step in the cellar.", "vault")
    api.note(env, TITLE, "Juniper's own guide.", "vault")
    focus = api.note(env, TITLE, "The field guide is on the shelf.")["page"]
    for n in range(3, 6):
        api.note(env, f"Cellar {n}", f"Juniper {n} reads Step by Step in the cellar.", "vault")
    api.note(env, "Reader", "Reader reads `Step` by Step by Step.")
    answer = Memory(env.store, lint_gate).related("ops", focus, 20)
    assert [p["page"] for p in answer["related"]] == ["reader"]
    assert "Juniper" not in json.dumps(answer) and vault.id not in json.dumps(answer)
    given = " ".join(s for s in scratch_watch.statements if s.lstrip().upper().startswith("INSERT"))
    assert "Reader reads" in given and "Juniper" not in given and "Cellar" not in given


# A claim that is not its passage's text: stated to mention as the index has it


def test_a_claim_a_model_compiled_apart_from_its_passage_mentions_as_the_index_has_it(env):
    """Stated (cairn.server's contract: a claim that is not its passage's text mentions as
    the index has it). Not only a page saved by hand reaches it: the product's own compile
    with a model does, when the model leaves out the blanks at a line's end, which the
    verbatim gate does not read; the page then shows the title only in code, and the
    claim mentions it."""
    focus = api.note(env, TITLE, "The field guide is on the shelf.")["page"]
    note, _ = env.store.add_note(env.ops.id, "Read `Step by Step` today.  \nBrook keeps it.")
    (span,) = [s for s in env.store.note_spans(note.id) if s.kind == "paragraph"]
    claim = {"text": "Read `Step by Step` today.\nBrook keeps it.", "span_ids": [span.id]}
    model = FakeModel([json.dumps({"claims": [claim]})])
    assert compile_note(env.store, note, compose=LLMComposer(model)).status == "saved"
    (page,) = [p for p in env.store.list_pages(env.ops.id) if p.slug != focus]
    assert [c.text for c in page.claims] == [claim["text"]] != [span.text]
    assert not prose_places(claim["text"], TITLE)  # the oracle: the title stands in code only
    assert surfaces(env, focus) == dict.fromkeys(("Memory", "REST", "MCP"), (True, [page.slug]))


# Fifth round: the shapes past the shared parser's agreement texts, the page's rendering
# (cairn.web.markdown.render) the standard for code. Each verdict decided by hand.

SHAPES = [
    ("| a | b |\n|---|---|\n| `x \\| Step by Step` | y |", False),
    ("| a | b |\n|---|---|\n| `x \\|` Step by Step | y |", True),
    ("| a | b |\n|---|---|\n| `x \\| y` Step by Step | `z` |", True),
    ("| a | b |\n|---|---|\n| `a | b` | Step by Step |", True),
    ("| a | `b` |\n| `Step by Step` | c |", False),  # no delimiter row: no table
    ("| a | b |\n| Step by Step | `c` |", True),
    ("[x]: <Step by Step> `t`", True),  # a definition the page does not show
    ("[Step by Step]: /u", True),
    ("[docs][Step `by` Step]", False),
    ("See [Step by Step] and `x`.", True),
    ("[docs](Step-by-Step) and `x`", True),
    ("Read `Step by  \nStep` today.", False),
    ("Read `Step by\\\nStep` today.", False),
    ("Read Step by  \nStep `x` today.", True),
    ("> > ```\n> > Step by Step\n> x", False),
    # the page: <blockquote><blockquote><pre><code>x</code></pre></blockquote><p>Step by Step</p></blockquote>; the coordinator's ruling, 2026-11-21: the page alone says what is code  # noqa: E501
    ("> > ```\n> > x\n> Step by Step", True),
    ("- Item.\n\n    Step by Step `x`", True),
    ("- Item.\n\n        Step by Step", False),
    ("- Item\n\n  ```\n  Step by Step", False),
    # the page: <ul><li>Item<pre><code>x</code></pre></li></ul><p>Step by Step <code>y</code></p>; the coordinator's ruling, 2026-11-21: the page alone says what is code  # noqa: E501
    ("- Item\n  ```\n  x\nStep by Step `y`", True),
    ("- ```\n  x\n- Step by Step `y`", True),
    ("-\tStep by Step `x`", True),
    ("-\t\tStep by Step", False),
    ("1.\tStep by Step `x`", True),
    ("-\tItem\n\n\t\tStep by Step", False),
    ("&#96;Step by Step` x", True),
    ("&amp;`Step by Step` x", False),
    ("\\\\`Step by Step` x", False),
    ("<a@b.c> Step by Step `x`", True),
    ("<`Step by Step`>", False),
    ("    Step by Step", False),
    ("    x\nStep by Step `y`", True),
    ("   Step by Step `x`", True),
    ("* * *\n    Step by Step", False),
    ("Read `Step by Step`` today.", True),
    ("Read ``Step by Step` today`` x.", False),
    ("<div>\n`Step by Step`\n</div>", False),
    ("Step by Step <span>`x`</span>", True),
    ("[d`](u) Step by Step `x", False),
    ('<span title="`">Step by Step</span> `x', False),  # raw HTML is text: a code span
]


@pytest.mark.parametrize(("claim", "names"), SHAPES)
def test_a_shape_past_the_agreement_texts_is_judged_as_the_page_shows_it(tmp_path, claim, names):
    assert bool(prose_places(claim, TITLE)) is names
    store, memory = store_at(tmp_path, "shape")
    try:
        assert names_by_cairn(memory, TITLE, claim) is names
    finally:
        store.close()


@pytest.mark.parametrize("fill", ["a", "\U0001d400"], ids=["ascii", "astral"])
def test_a_claim_of_max_rendered_characters_is_read_and_one_more_is_code_whole(tmp_path, fill):
    from cairn.spans import MAX_RENDERED

    store, memory = store_at(tmp_path, "long")
    try:
        for size, names in ((MAX_RENDERED, True), (MAX_RENDERED + 1, False)):
            head = "Read Step by Step today. "
            text = (head + (fill + " ") * size)[: size - 1] + "x"
            assert len(text) == size
            assert str(render(text)).startswith("<pre>") is not names  # as the page shows it
            store.create_project(f"p{size}")
            focus = memory.note(f"p{size}", "The guide.", title=TITLE, topic=TITLE)
            other = memory.note(f"p{size}", text, title="Reader", topic="Reader")
            (stated,) = memory.page(f"p{size}", other["page"])["claims"]
            assert stated["text"] == text
            answer = memory.related(f"p{size}", focus["page"], 20)
            assert answer["searched"]["complete"]
            got = any(w["kind"] == "mentioned_by" for p in answer["related"] for w in p["why"])
            assert got is names is bool(prose_places(text, TITLE))
    finally:
        store.close()


PIECES = ["Step", "by", "Step by Step", " ", " ", "\n", "\n\n", "`", "``", "x", "|", "\\|"]
PIECES += ["|---|---|\n", "[x]: /u\n", "[d][x]", "[Step by Step]", "  \n", "\\\n", "> ", "> > "]
PIECES += ["```", "~~~", "- ", "1. ", "    ", "        ", "\t", "-\t", "&#96;", "\\`", "&amp;"]
PIECES += ["<https://e.x/", "<", ">", "\r\n"]


def test_generated_claims_of_the_new_pieces_are_judged_as_the_page_shows_them(tmp_path):
    """Seeded: 1,500 draws of the pieces above, those that hold the title kept; no picture
    or heading mark (pinned, test_rt4_title_places_found.py), and no backtick in a link's
    title or address (test_rt4_title_places_found2.py's)."""
    rng, store, memory = random.Random(20261122), *store_at(tmp_path, "generated2")
    try:
        verdicts = {True: 0, False: 0}
        for n in range(1500):
            claim = "".join(rng.choice(PIECES) for _ in range(rng.randint(3, 16))).strip()
            if not claim or not occurrences(claim, TITLE):
                continue
            store.create_project(f"g{n}")
            focus = memory.note(f"g{n}", "The guide.", title=TITLE, topic=TITLE)
            other = memory.note(f"g{n}", claim, title="Reader", topic="Reader")
            texts = [c["text"] for c in memory.page(f"g{n}", other["page"])["claims"]]
            answer = memory.related(f"g{n}", focus["page"], 20)
            got = any(w["kind"] == "mentioned_by" for p in answer["related"] for w in p["why"])
            assert got is any(prose_places(text, TITLE) for text in texts), claim
            verdicts[got] += 1
        assert verdicts[True] >= 300 and verdicts[False] >= 60, verdicts
    finally:
        store.close()


@pytest.mark.parametrize("named", [5, 6])
def test_the_cutoffs_hold_for_a_title_beside_an_escaped_bar_in_a_cell(env, named):
    focus = api.note(env, TITLE, "The field guide is on the shelf.")["page"]
    cell = "| a | b |\n|---|---|\n| `x \\|` {n} Step by Step | y |"
    pages = [api.note(env, f"Cell {n}", cell.format(n=n))["page"] for n in range(named)]
    for n in range(2):  # and the title inside the cell's code span: never counted
        api.note(env, f"Code {n}", f"| a | b |\n|---|---|\n| `x \\| Step by Step {n}` | y |")
    expected = sorted(pages) if named <= relations.COMMON else []
    seen = surfaces(env, focus)
    assert {k: seen[k] for k in ("Memory", "REST")} == dict.fromkeys(
        ("Memory", "REST"), (True, expected)
    )


# Sixth round: the ruling (the page alone says what is code) at the five-page cutoff


@pytest.mark.parametrize(
    ("title", "shape"),
    [
        (TITLE, "Group {n} reads \\`Step by Step\\` today."),
        ("Deploy", '[d{n}](u "`") /srv/deploy/x `x'),  # a path the facts do not read: stated
    ],
    ids=["between-escaped-backticks", "path-between-unpaired-backticks"],
)
@pytest.mark.parametrize("named", [5, 6])
def test_the_cutoffs_hold_for_titles_the_page_shows_as_prose_by_the_ruling(
    env, title, shape, named
):
    focus = api.note(env, title, "The field guide is on the shelf.")["page"]
    pages = []
    for n in range(named):
        text = shape.format(n=n)
        assert bool(prose_places(text, title))  # the oracle: in prose on the page
        pages.append(api.note(env, f"Shape {n}", text)["page"])
    for n in range(2):  # beside them, the title only in code: never counted
        api.note(env, f"Code {n}", f"Run `{title} {n}` here.")
    expected = sorted(pages) if named <= relations.COMMON else []
    assert surfaces(env, focus) == dict.fromkeys(("Memory", "REST", "MCP"), (True, expected))


def test_a_path_after_a_fence_a_list_item_closes_names_the_page_as_the_facts_read_it(tmp_path):
    """The facts read the rest of the claim as the fence's body, so no path: the title in it
    names the page, as the connect guide's general clause says (a path is what the facts
    read as one), though its list of such places names only unpaired backticks. The page
    shows the path as prose after the list."""
    claim = "- Item\n  ```\n  x\nSee /srv/deploy/x today."
    assert "<p>See /srv/deploy/x today.</p>" in str(render(claim))
    assert fact_stretches(claim) == [] and prose_places(claim, "Deploy")
    store, memory = store_at(tmp_path, "fence")
    try:
        assert names_by_cairn(memory, "Deploy", claim) is True
    finally:
        store.close()


# Seventh round: masking that moves no word (then relations.blanked, with the index's own reading
# of each character, relating._kinds), attacked flush against every kind of stretch

NFD_CAFE = unicodedata.normalize("NFD", "Café")
FLUSH_TITLES = [
    "Cafe", "Route 66", "66 Route", NFD_CAFE, "Café", "東京", "東京 Tower", "O'Brien", "Rock'",
    "Well-Known", "snake_case", "Ab", "Ab", "Wave 👋🏽", "Team 👩‍💻",
    "Ange︎", unicodedata.normalize("NFD", "Noël"), "x́", "Ünder",
]  # fmt: skip
FLUSH_AFTER = ["/notes", "/srv/x", "https://e.x/a", "--force", "-v1.2", "v1.2.3", ".1.2.3"]
FLUSH_AFTER += ["`x`", "``x``", "\n```\nx\n```", "\n    x", "`́`", "́`x`", "/́x"]
FLUSH_AFTER += ["'s", "-x"]
FLUSH_BEFORE = ["https://e.x/a/", "/srv/x/", "--force", "--force=", "v1.2.3", "1.2.3", "`x`"]
FLUSH_BEFORE += ["``x``", "<https://e.x/>", "`́`"]


def flush_claims(title: str) -> list[str]:
    claims = [f"Read {title}{after} today." for after in FLUSH_AFTER]
    claims += [f"Read {before}{title} today." for before in FLUSH_BEFORE]
    claims += [f"```\nx\n```\n{title} today.", f"    x\n\n{title} today."]
    claims += [
        f"Read {b}{title}{a} today." for b in ("/srv/x/", "`x`", "--y") for a in FLUSH_AFTER[:8]
    ]
    return [claim for claim in claims if occurrences(claim, title)]


def index_tokens(texts: list[str]) -> list[list[str]]:
    with closing(sqlite3.connect(":memory:")) as con:
        con.execute(schema._CLAIM_INDEX.format(index="t"))
        con.execute("CREATE VIRTUAL TABLE v USING fts5vocab(t, instance)")
        con.executemany("INSERT INTO t (rowid, text) VALUES (?, ?)", enumerate(texts, 1))
        found: dict[int, list[str]] = {}
        for doc, term in con.execute(
            "SELECT doc, term FROM v WHERE col = 'text' ORDER BY doc, offset"
        ):
            found.setdefault(doc, []).append(term)
    return [found.get(n, []) for n in range(1, len(texts) + 1)]


def test_a_title_flush_against_every_kind_of_stretch_is_judged_as_the_page_shows_it(tmp_path):
    """Every fact kind and every kind of code flush before and after a title's first and
    last word, for titles that end and start in each kind of character; the oracle's tokens
    are the index's on every text judged (fts5vocab), so it abstains on none."""
    store, memory = store_at(tmp_path, "flush")
    try:
        judged, verdicts = 0, {True: 0, False: 0}
        for title in FLUSH_TITLES:
            claims = flush_claims(title)
            for text, terms in zip([title, *claims], index_tokens([title, *claims]), strict=True):
                assert terms == [t for t, _, _ in tokens(text)], text
            for claim in claims:
                judged += 1
                store.create_project(f"f{judged}")
                focus = memory.note(f"f{judged}", "The guide.", title=title, topic=title)
                other = memory.note(f"f{judged}", claim, title="Reader", topic="Reader")
                texts = [c["text"] for c in memory.page(f"f{judged}", other["page"])["claims"]]
                answer = memory.related(f"f{judged}", focus["page"], 20)
                assert answer["searched"]["complete"]
                got = any(w["kind"] == "mentioned_by" for p in answer["related"] for w in p["why"])
                assert got is any(prose_places(text, title) for text in texts), (title, claim)
                verdicts[got] += 1
        assert judged > 500 and verdicts[False] > 60, (judged, verdicts)
    finally:
        store.close()


@pytest.mark.parametrize(
    ("title", "shape"),
    [
        (NFD_CAFE, "Group {n} reads " + NFD_CAFE + "/notes today."),  # the reviewer's
        ("東京", "Group {n} reads 東京`x` today."),
        ("Ab", "Group {n} reads /srv/x/ Ab--force today."),
    ],
    ids=["nfd-before-a-path", "cjk-before-a-code-span", "fill-character-title"],
)
@pytest.mark.parametrize("named", [5, 6])
def test_the_cutoffs_hold_for_a_title_flush_against_a_stretch(env, title, shape, named):
    focus = api.note(env, title, "The field guide is on the shelf.")["page"]
    pages = []
    for n in range(named):
        assert prose_places(shape.format(n=n), title)
        pages.append(api.note(env, f"Flush {n}", shape.format(n=n))["page"])
    api.note(env, "Code", f"Run `{title}` here.")  # only in code: never counted
    expected = sorted(pages) if named <= relations.COMMON else []
    assert surfaces(env, focus) == dict.fromkeys(("Memory", "REST", "MCP"), (True, expected))


MASK_PIECES = ["Alpha", "Beta", "Alpha Beta", NFD_CAFE, "Café", "東京", "Ab", "Route", "66"]
MASK_PIECES += ["O'Brien", "snake_case", " ", " ", "\n", "`", "``", "/notes", "/srv/x", "--force"]
MASK_PIECES += ["v1.2.3", "https://e.x/a", "-", "/", ".", "'", "_", "́", "    ", "```\n", "~~~"]
MASK_PIECES += ["- ", "> ", "x", "é", ""]


# The masking the test here checked (relations.blanked: one text, what is not prose blanked out
# in place) is gone: a path that holds no word, `/_`, was given no fill, and the index read a
# title across it (the reviewer's ninth report, must-fix 1). The index is given each run of
# prose as a text of its own (relations.prose_runs); the same property, asked of the runs.
def test_every_word_of_a_run_of_prose_is_a_word_of_the_passage_and_no_run_spans_a_stretch():
    rng, fill = random.Random(1122), "\ue000"  # relations.fill_for(""); no piece holds it
    for _ in range(1500):
        text = "".join(rng.choice(MASK_PIECES) for _ in range(rng.randint(2, 18)))
        span = Span(
            id="sp", source_id="n", kind="paragraph", heading_path=(), start_line=1,
            end_line=1 + text.count("\n"), text=text,
        )  # fmt: skip
        stretches = [(a, b) for a, b in [*relations.covered(span), *code_read(text)] if a < b]
        had = {(s, e): t for t, s, e in tokens(text)}
        for at, run in relations.prose_runs(text, stretches, "", relating._kinds(text + fill)):
            inner = (at + (run[0] == fill), at + len(run) - (run[-1] == fill))
            assert not any(a < inner[1] and inner[0] < b for a, b in stretches), (text, run)
            for term, s, e in tokens(run):
                assert fill in term or had.get((at + s, at + e)) == term, (text, stretches, run)


@pytest.mark.parametrize("claim", ["Alpha`-`Beta today.", "Alpha`/`Beta today.", "Alpha` `Beta."])
def test_a_code_span_of_a_separator_between_two_title_words_names_nothing(tmp_path, claim):
    assert "<code>" in str(render(claim))  # the page: Alpha<code>-</code>Beta: code inside it
    assert not prose_places(claim, "Alpha Beta")
    store, memory = store_at(tmp_path, "wordless")
    try:
        assert names_by_cairn(memory, "Alpha Beta", claim) is False
    finally:
        store.close()


def test_every_character_sampled_is_read_by_relating_as_the_index_reads_it():
    """relating._kinds against fts5vocab on the claims' own declaration (no tokenize option
    is declared): every 97th code point, the index's marks, the fill characters, the
    noncharacters and every character of this file's corpora."""
    chars = {chr(c) for c in range(1, 0x110000, 97) if not 0xD800 <= c <= 0xDFFF}
    chars |= set(relating.MARKS) | {chr(c) for c in range(0xE000, 0xE010)}
    chars |= {chr(c) for c in range(0xFDD0, 0xFDF0)} | {"￾", "￿", "\U0010ffff"}
    chars |= set("".join([*FLUSH_TITLES, *MASK_PIECES, *(c for _, c, _ in CASES)]))
    chars = sorted(chars)
    kind = relating._kinds(chars)
    read = index_tokens([text for char in chars for text in (char + "z", "y" + char + "z")])
    for n, char in enumerate(chars):
        alone, inside = read[2 * n], read[2 * n + 1]
        expected = (
            relations.SEPARATOR
            if len(inside) == 2
            else relations.MARK
            if alone == ["z"]
            else relations.TOKEN
        )
        assert kind(char) == expected, hex(ord(char))


@pytest.mark.parametrize(
    ("lines", "first"),
    [
        (["Say `Echo Echo` first.", "Say `Echo` Echo Echo Echo now."], 1),  # two and three deep
        (["Say `Echo Echo` first.", "Say Echo", "Echo Echo again."], (1, 2)),  # across lines
        (
            ["Say `Echo Echo` first.", "Say `Echo` Echo", "Echo `Echo` now.", "Echo Echo Echo."],
            (1, 2),
        ),
    ],
    ids=["overlapping-on-one-line", "across-two-lines", "first-across-lines-before-a-later"],
)
def test_the_line_given_is_the_first_places_in_prose_however_its_places_overlap(env, lines, first):
    focus = api.note(env, "Echo Echo", "The field guide is on the shelf.")["page"]
    filler = [f"Filler {n} keeps the claim long enough to be cut." for n in range(14)]
    text = "\n".join([*lines[:1], *filler, *lines[1:]])
    assert len(text) > relations.TEXT_MAX
    reader = api.note(env, "Reader", text)["page"]
    (claim,) = api.answer(env.client, env.key, "page", project="ops", slug=reader)["claims"]
    assert claim["text"] == text
    (page,) = api.answer(env.client, env.key, "related", project="ops", slug=focus)["related"]
    (why,) = page["why"]
    rest = lines[1:]
    expected = (
        rest[first - 1] if isinstance(first, int) else "\n".join(rest[first[0] - 1 : first[1]])
    )
    assert (why["kind"], why["there"]["text_cut"], why["there"]["line"]) == (
        "mentioned_by", True, expected,
    )  # fmt: skip
