"""Faults of related pages found by an adversarial read (RT4), kept as regression tests: each
test states what the feature promises, and every test here passes on the release's tree.
Ordinary inputs through the public path (Memory.related, REST, the web page); the oracle
is the search index's own matching asked directly (a quoted FTS5 phrase of the text as
written, which FTS5 itself tokenizes), never the product's word splitter.

Sections 1 to 10 are the first round's: each failed on the tree it was written against, and
each passes since kg3/0.1.0 (d66b7d67) answered it; two are restated to the decisions taken
(a term keeps its case; the guide says what the link and the explorer's view each show).
Sections 11 to 13 are the second round's: each failed on d66b7d67 and passes since kg3/0.1.0
(1ce891f5) answered it, section 13 restated to the decisions. Sections 14 to 16 are the
third round's, restated to the rulings on them: 14 and 15 pin what the contract states, and
16 passes since the sentence was added.

Made-up names; no real notes."""

import asyncio
import re
import sqlite3
import unicodedata
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from test_relating_cost_unit import watched
from test_search_steps_unit import PERIOD, merge

from cairn.gate import lint_gate
from cairn.memory import Memory
from cairn.server import create_app
from cairn.store import Claim, Store

BASE = "http://127.0.0.1:8765"
SRC = Path(__file__).resolve().parents[2] / "src" / "cairn"


@pytest.fixture
def ops(tmp_path):
    store = Store(tmp_path / "cairn.db")
    store.create_project("ops")
    memory = Memory(store, lint_gate)
    yield store, memory
    store.close()


def note(memory: Memory, title: str, text: str, project: str = "ops") -> dict:
    answer = memory.note(project, text, title=title, topic=title)
    assert answer["status"] == "saved", answer
    return answer


def reasons(memory: Memory, slug: str) -> tuple[list[tuple[str, str, str, str]], dict]:
    """(other page, kind, term, term kind) for every reason given, and `searched`."""
    found = memory.related("ops", slug, 20)
    return [
        (page["page"], why["kind"], why["term"], why["term_kind"])
        for page in found["related"]
        for why in page["why"]
    ], found["searched"]


def index_matches(text: str, phrase: str) -> bool:
    """The search index's own matching: the phrase as written, quoted (FTS5 tokenizes it as
    it tokenizes the text), against the text alone, in a table made as the product's index
    is (FTS5's default unicode61 tokenizer)."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE VIRTUAL TABLE t USING fts5 (text)")
    con.execute("INSERT INTO t (text) VALUES (?)", (text,))
    quoted = '"' + phrase.replace('"', '""') + '"'
    return (
        con.execute("SELECT count(*) FROM t WHERE t MATCH ?", (f"text : {quoted}",)).fetchone()[0]
        == 1
    )


# 1. Two flags that differ only in case are made one term (facts.compared folds case), and
# the evidence then says both pages state a term one of them never writes.


def test_flags_that_differ_only_in_case_are_not_one_shared_term(ops):
    _store, memory = ops
    note(memory, "Tunnel", "Forward a local port with `ssh -L 8080:db:5432`, that is `-L`.\n")
    note(memory, "Login", "Pass the remote login name with `-l`.\n")
    given, searched = reasons(memory, "tunnel")
    assert searched["complete"]
    # `-L` (a local forward) and `-l` (a login name) are two flags; neither page states the
    # other's. The answer relates them by "-l", a term Tunnel never writes.
    assert given == [], given


def test_the_web_page_does_not_say_a_page_states_a_flag_it_does_not_write(tmp_path):
    # Restated to the decision (a term keeps its case): Tunnel and Login are not related, and
    # where a third page writes `-L` as Tunnel does, each claim of the reason holds the term
    # as the reason gives it.
    store = Store(tmp_path / "cairn.db")
    store.create_project("ops")
    key = store.create_key("laptop")
    memory = Memory(store, lint_gate)
    note(memory, "Tunnel", "Forward a local port with `-L`.\n")
    note(memory, "Login", "Pass the remote login name with `-l`.\n")
    note(memory, "Socks", "Open the forward with `-L` too.\n")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        found = client.get("/api/projects/ops/pages/tunnel/related", headers={"x-api-key": key})
    store.close()
    (page,) = found.json()["related"]
    assert page["page"] == "socks"
    (why,) = page["why"]
    assert (why["term"], why["pages"]) == ("-L", 2)
    assert why["term"] in why["here"]["text"] and why["term"] in why["there"]["text"], why


# 2. A title or a term written with combining marks (decomposed, as macOS writes file names)
# is split by Python's \w where the index reads one word: the index's own matching finds
# the title in the claim, the read never asks for it, and the answer says it is complete.

TITLE_NFD = unicodedata.normalize("NFD", "Résumé")


def test_the_oracle_the_index_matches_a_decomposed_title_as_written():
    claim = unicodedata.normalize("NFD", "Send the Résumé to Ada.")
    assert index_matches(claim, TITLE_NFD)
    # what the product asks instead (ranking.phrase of ranking.words): "Re sume"
    assert re.findall(r"\w+", TITLE_NFD) == ["Re", "sume"]
    assert not index_matches(claim, " ".join(re.findall(r"\w+", TITLE_NFD)))


def test_a_decomposed_title_is_mentioned_where_the_index_matches_it(ops):
    store, memory = ops
    asked = note(memory, TITLE_NFD, "Brook keeps the template.\n")
    note(memory, "Hiring", unicodedata.normalize("NFD", "Send the Résumé to Ada.\n"))
    page = store.get_page(store.list_projects()[0].id, asked["page"])
    assert page.title == TITLE_NFD  # the title is kept as written
    given, searched = reasons(memory, asked["page"])
    assert searched["complete"] is True
    assert given == [("hiring", "mentioned_by", TITLE_NFD, "title")], given


def test_a_decomposed_term_relates_the_two_pages_that_state_it(ops):
    _store, memory = ops
    path = unicodedata.normalize("NFD", "`Résumé.cfg`")
    note(memory, "Draft", f"Brook edits {path} first.\n")
    note(memory, "Review", f"Ada reads {path} after.\n")
    given, searched = reasons(memory, "draft")
    assert searched["complete"] is True
    assert [(page, kind) for page, kind, _, _ in given] == [("review", "shares")], given


# 3. A term of more parts than a search takes (65 distinct parts split at "_", 194
# characters, within TERM_MAX) is neither looked up nor counted: `terms_looked_up` and
# `terms_not_looked_up` are both 0 and the answer says it is complete, while two pages state
# it. With 64 parts the same two pages are related.


def test_a_term_of_more_parts_than_a_search_takes_is_related_or_counted(ops):
    _store, memory = ops
    parts = [chr(97 + i // 26) + chr(97 + i % 26) for i in range(65)]
    term = "_".join(parts)
    assert len(term) == 194 and len(set(parts)) == 65
    note(memory, "Keys one", f"The key is `{term}` here.\n")
    note(memory, "Keys two", f"Set `{term}` there too.\n")
    given, searched = reasons(memory, "keys-one")
    related = [(page, kind) for page, kind, _, _ in given] == [("keys-two", "shares")]
    counted = searched["terms_not_looked_up"] >= 1 and searched["complete"] is False
    assert related or counted, (given, searched)


# 4. Cost: claims of ANOTHER project of the same workspace that hold the page's words are
# walked for every term looked up (256 at most) and for the title, so one read's work grows
# with what other people wrote in projects the caller may not reach. The store's own test
# pins "the same steps" for claims of the project that hold none of the page's words; this
# is the same claim for claims elsewhere that hold them.


def build_other(folder: Path, other_claims: int) -> tuple[Store, str]:
    store = Store(folder / "cairn.db")
    ops = store.create_project("ops")
    other = store.create_project("elsewhere")
    memory = Memory(store, lint_gate)
    lines = [
        "- Step: " + " ".join(f"`t{n:05d}.cfg`" for n in range(first, first + 10))
        for first in range(0, 256, 10)
    ]
    memory.note("ops", "\n".join(lines) + "\n", title="Asked", topic="Asked")
    held = " ".join(f"t{n:05d}.cfg" for n in range(256)) + " Asked"
    for first in range(0, other_claims, 100):
        claims = [
            Claim(id=f"cl_{n}", text=f"{held} {n}", span_ids=())
            for n in range(first, min(other_claims, first + 100))
        ]
        store.save_page(other.id, f"bulk-{first}", f"Ledger {first}", "", claims)
    merge(store)
    return store, ops.id


def test_claims_of_another_project_that_hold_the_words_add_no_work(tmp_path):
    (tmp_path / "few").mkdir()
    (tmp_path / "many").mkdir()
    few, few_id = build_other(tmp_path / "few", 0)
    many, many_id = build_other(tmp_path / "many", 1_000)
    try:
        with watched(few) as seen:
            alone = few.related_pages(few_id, "asked", 20)
        with watched(many) as seen_many:
            beside = many.related_pages(many_id, "asked", 20)
    finally:
        few.close()
        many.close()
    assert alone.left == beside.left and alone.pages == beside.pages == ()
    assert len(seen.statements) == len(seen_many.statements)
    # measured on this tree: 24,600 steps alone, 2,132,350 beside the other project's 1,000
    # claims (87x, and linear: 8,000 such claims took 2.2 s for one read on an Apple laptop,
    # holding one of the store's four readers; a recall of 32 words over them, 0.14 s)
    assert seen_many.steps <= 1.1 * seen.steps + 10 * PERIOD, (seen.steps, seen_many.steps)


# 5. The web page: a term is said inside a sentence of the product's own words, unisolated,
# so a bidirectional control in it turns the words after it; and a long term has no rule
# that wraps it, so it runs past a narrow screen (the page view wraps claims: .claim-text).


def related_html(tmp_path, first: str, second: str) -> str:
    from test_related_page_unit import build, get

    w = build(tmp_path / "web")
    with w.client:
        for title, text in (("Bidi one", first), ("Bidi two", second)):
            Memory(w.ada.writes, lint_gate).note("ops", text, title=title, topic=title)
        html = get(w, "/b/ops/p/bidi-one/related").text
    w.store.close()
    return html


def test_a_term_with_a_bidirectional_control_is_isolated_in_its_sentence(tmp_path):
    html = related_html(tmp_path, "Use `a‮b1` here.\n", "Also `a‮b1` there.\n")
    (sentence,) = re.findall(r"<p>Both pages state the code (.*?)</p>", html, re.S)
    # the term opens a right-to-left override that nothing closes: "2 pages of ops hold it."
    # after it is drawn reversed. An isolate (<bdi>, dir=, or a closing U+202C/U+2069) keeps
    # the sentence the product's.
    assert "<bdi" in sentence or "dir=" in sentence or "‬" in sentence, sentence


def test_a_long_term_in_a_reason_wraps_on_a_narrow_screen(tmp_path):
    term = "https://docs.example.com/" + "a" * 150
    html = related_html(tmp_path, f"Read {term} first.\n", f"Then read {term} again.\n")
    assert f"<code>{term}</code>" in html
    sheet = (SRC / "static" / "cairn.css").read_text()
    wrapping = [
        selector.strip()
        for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", sheet)
        if "overflow-wrap: anywhere" in body or "word-break" in body
        for selector in selectors.split(",")
    ]
    # the sentence is <p> in li.hit in ul.hits in <section> in <main>, the term a <code>:
    # overflow-wrap is inherited, so any rule on one of them would do. None has one.
    ancestors = {".hit", ".hits", ".hit p", ".hits p", ".hit code", ".hits code", "main", "body"}
    assert ancestors & set(wrapping), sorted(wrapping)


# 6. A sentence of the tool's description: "`claims_not_read`: such claims not read, ...; a
# term with a claim not read gives no reason." A term one of whose claims was not read
# still gives a reason when the page of that claim is known to hold it by another claim
# (cairn.store.relating says so: "settled"). The bound on bytes is made small enough to be
# met; with the shipped million bytes the read is the same, with larger passages.


def test_the_tools_sentence_a_term_with_a_claim_not_read_gives_no_reason(ops, monkeypatch):
    from cairn.store import relating
    from cairn.web.mcp import build_mcp

    _store, memory = ops
    note(memory, "Deploy", "Run the job with `--audit-keep` set.\n")
    long = "Keep `--audit-keep` " + "and the ledger " * 200 + "for Ada.\n"
    note(memory, "Retention", "Brook sets `--audit-keep` to 7.\n\n" + long)
    first = len(b"Brook sets `--audit-keep` to 7.")
    monkeypatch.setattr(relating, "BYTES_MAX", first + 8)  # the first passage, not the second
    found = memory.related("ops", "deploy", 20)
    assert found["searched"]["claims_not_read"] == 1 and not found["searched"]["complete"]
    tools = {tool.name: tool for tool in asyncio.run(build_mcp().list_tools())}
    said = " ".join(tools["related"].description.split())
    assert "a term with a claim not read gives no reason" in said
    given = [(p["page"], w["term"]) for p in found["related"] for w in p["why"]]
    assert given == [], given  # the tool's sentence; the answer gives ("retention", ...)


# 7. The web page and the explorer say "No page ... is related" of an answer that says it is
# not complete, where a page the rule relates was not looked at. Shipped bounds: 256 short
# terms are looked up first, and the 257th, the one a second page states, is not.


def test_a_page_with_no_related_page_found_does_not_say_there_is_none_when_it_did_not_look(
    tmp_path,
):
    from test_related_page_unit import build, get, main_of

    w = build(tmp_path / "web", graph_explorer=True)
    with w.client:
        memory = Memory(w.ada.writes, lint_gate)
        lines = [
            "- Step: " + " ".join(f"`a{n:03d}.x`" for n in range(first, first + 8))
            for first in range(0, 256, 8)
        ]
        lines.append("- Last: `the-long-shared-term.cfg`")
        memory.note("ops", "\n".join(lines) + "\n", title="Asked", topic="Asked")
        memory.note("ops", "Brook sets `the-long-shared-term.cfg`.\n", title="Other", topic="Other")
        answer = memory.related("ops", "asked")
        page = main_of(get(w, "/b/ops/p/asked/related").text)
        drawn = main_of(get(w, "/b/ops/graph?focus=related%3Aasked").text)
    w.store.close()
    assert answer["related"] == [] and answer["searched"]["terms_not_looked_up"] == 1
    # "Other" states the 257th term: the rule relates it; the read did not look.
    assert "This is not all of it" in page
    assert "No page of ops is related to Asked" not in page
    assert "No page is related to the page in focus" not in drawn


# 8. The security guide said the explorer's view draws "the answer its **Related pages** link
# shows"; the link shows 8 and the view up to 20. Restated to the guide as rewritten: "up to
# 20 of the pages `related` gives for it, where its **Related pages** link shows 8 unless
# asked for more". The link's 8 are the first 8 the view draws, in its order.


def test_the_explorers_view_draws_the_answer_the_related_pages_link_shows(tmp_path):
    from test_related_page_unit import build, get, main_of

    guide = " ".join((SRC / "guides" / "security.md").read_text().split())
    assert (
        "up to 20 of the pages `related` gives for it, where its **Related pages** link shows 8"
        " unless asked for more" in guide
    )
    w = build(tmp_path / "web", graph_explorer=True)
    with w.client:
        memory = Memory(w.ada.writes, lint_gate)
        terms = [f"`s{n:02d}.cfg`" for n in range(10)]
        memory.note("ops", "Ada sets " + " ".join(terms) + ".\n", title="Asked", topic="Asked")
        for n, term in enumerate(terms):
            memory.note("ops", f"Brook {n} sets {term}.\n", title=f"Other {n}", topic=f"Other {n}")
        linked = main_of(get(w, "/b/ops/p/asked/related").text)
        drawn = main_of(get(w, "/b/ops/graph?focus=related%3Aasked").text)
    w.store.close()
    shown = re.findall(r'data-related-page="([^"]+)"', linked)
    nodes = re.findall(r'data-graph-area="list" data-graph-node="page:([^"]+)"', drawn)
    nodes = [slug for slug in nodes if slug != "asked"]  # the page itself
    assert (len(shown), len(nodes)) == (8, 10), (shown, nodes)
    assert set(shown) <= set(nodes), (shown, nodes)


# 10. A document: cairn.graph's docstring says the read runs "up to 16 statements";
# cairn.store.relating says 13, and 17 with its caller's, and the contract text says 17.


def test_the_graph_modules_docstring_states_the_reads_statements_as_relating_does():
    graph = (SRC / "graph.py").read_text()
    relating_doc = (SRC / "store" / "relating.py").read_text()
    server = (SRC / "server.py").read_text()
    assert "and 17 with the caller's" in relating_doc
    assert "at most 17 statements" in server
    assert "up to 16 statements" not in graph


# Second round, against kg3/0.1.0 (d66b7d67) merged.
#
# 11. Cost: the index is asked once a run of the project's rows (cairn.store.relating._RUNS),
# and a run ends wherever a row of another project was written between two of the project's
# pages. The module bounds the runs by the project's pages; that bound grows with the
# project, and where in it a read stands is set by the order other projects wrote in. The
# same pages, claims and answer cost 10x and more when two projects were written in turn,
# as two teams write; nothing of the other project matches any word. relating's docstring:
# "The work is the page's and its project's, whatever other projects hold." Pages built
# with save_page. Measured beside this test (Apple laptop, one store read, index merged):
# 1,000 pages 0.02 s together, 0.88 s in turn; 4,000 pages 0.05 s and 3.0 to 3.6 s.


def build_turns(folder: Path, pages: int, in_turn: bool) -> tuple[Store, str]:
    store = Store(folder / "cairn.db")
    ops = store.create_project("ops")
    other = store.create_project("elsewhere")
    memory = Memory(store, lint_gate)
    lines = [
        "- Step: " + " ".join(f"`t{n:05d}.cfg`" for n in range(first, first + 10))
        for first in range(0, 256, 10)
    ]
    memory.note("ops", "\n".join(lines) + "\n", title="Asked", topic="Asked")

    def mine(n: int) -> None:
        claim = Claim(id=f"cl_p{n}", text=f"Brook writes line {n} of the book.", span_ids=())
        store.save_page(ops.id, f"page-{n}", f"Page {n}", "", [claim])

    def theirs(n: int) -> None:
        claim = Claim(id=f"cl_q{n}", text=f"Ada keeps {n}.", span_ids=())
        store.save_page(other.id, f"their-{n}", f"Theirs {n}", "", [claim])

    if in_turn:
        for n in range(pages):
            mine(n)
            theirs(n)
    else:
        for n in range(pages):
            mine(n)
        for n in range(pages):
            theirs(n)
    merge(store)
    return store, ops.id


def test_another_projects_writes_between_a_projects_pages_add_no_work(tmp_path):
    (tmp_path / "together").mkdir()
    (tmp_path / "in-turn").mkdir()
    together, together_id = build_turns(tmp_path / "together", 120, in_turn=False)
    in_turn, in_turn_id = build_turns(tmp_path / "in-turn", 120, in_turn=True)
    try:
        together.related_pages(together_id, "asked", 20)  # FTS5 loads its settings once
        in_turn.related_pages(in_turn_id, "asked", 20)
        with watched(together) as alone:
            first = together.related_pages(together_id, "asked", 20)
        with watched(in_turn) as beside:
            second = in_turn.related_pages(in_turn_id, "asked", 20)
    finally:
        together.close()
        in_turn.close()
    assert first.left == second.left and first.pages == second.pages == ()
    assert len(alone.statements) == len(beside.statements)
    assert beside.steps <= 1.1 * alone.steps + 10 * PERIOD, (alone.steps, beside.steps)


# 12. The turn: one read of related pages at a time in a process, in the order asked, with
# no bound on who waits or how many (cairn.web.turns.RELATED_READS). The contract says a
# read waits "however long that takes"; this test asks for more than any document promises:
# that one key's waiting reads do not all go before another key's. As shipped, one
# read-only key of one project holds the related pages of every project the process
# serves. Measured beside this test through REST (index not merged): ten reads of a
# 1,000-page project whose rows interleave another's (1.6 s each), and a two-page project's
# related read, alone 0.0015 s, took 16.3 s; its page view was not held (0.002 s). At the
# documented worst read (0.40 s here), ten queued reads hold the next one about 4 s.


def test_one_keys_waiting_reads_do_not_hold_another_projects_related_read(tmp_path, monkeypatch):
    import httpx

    from cairn.store import relating
    from cairn.web.turns import RELATED_READS

    store = Store(tmp_path / "cairn.db")
    ops = store.create_project("ops")
    quiet = store.create_project("quiet")
    memory = Memory(store, lint_gate)
    note(memory, "Deploy", "Ada sets `--audit-keep`.\n")
    note(memory, "Retention", "Brook reads `--audit-keep`.\n")
    note(memory, "Deploy", "Cy sets `--dry-run`.\n", project="quiet")
    note(memory, "Retention", "Dee reads `--dry-run`.\n", project="quiet")
    hog = store.create_key("hog", projects=[ops.id], access="read")
    calm = store.create_key("calm", projects=[quiet.id], access="read")
    order: list[str] = []
    real = relating.related

    def recorded(con, account_id, index, project_id, *rest):
        order.append(project_id)
        return real(con, account_id, index, project_id, *rest)

    monkeypatch.setattr(relating, "related", recorded)
    app = create_app(store, public_url=BASE)

    async def waiting(n: int) -> None:
        for _ in range(500):
            if RELATED_READS.waiting >= n:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"{RELATED_READS.waiting} waiting, not {n}")

    async def main() -> list[int]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:

            def ask(key: str, project: str) -> asyncio.Task:
                path = f"/api/projects/{project}/pages/deploy/related"
                return asyncio.create_task(client.get(path, headers={"x-api-key": key}))

            async with RELATED_READS.turn():  # a read under way
                hogs = []
                for n in range(8):
                    hogs.append(ask(hog, "ops"))
                    await waiting(n + 1)
                last = ask(calm, "quiet")
                await waiting(9)
            answers = await asyncio.gather(*hogs, last)
        return [answer.status_code for answer in answers]

    statuses = asyncio.run(main())
    store.close()
    assert statuses == [200] * 9
    assert order.count(ops.id) == 8 and order.count(quiet.id) == 1, order
    # the other key's read waited for the first key's 8: it should wait for one at most
    assert order.index(quiet.id) <= 1, order


# 13. The prose rule (second round). Restated to the decisions of kg3/0.1.0 (1ce891f5): a
# fence's opening line is code and a claim that holds a mark of the index names no page
# (fixed); an email address, a bare path, a Windows path and a host name are prose to the
# fact reader and name a page, as the sentences now say (pinned).


def mentions_of_ledger(memory: Memory, text: str) -> tuple[list, dict]:
    note(memory, "Ledger", "Ada keeps the books.\n")
    note(memory, "Other", text)
    note(memory, "Control", "Brook reads ~/ledger/notes.md daily.\n")  # a rooted path: none
    return reasons(memory, "ledger")


@pytest.mark.parametrize(
    "text",
    ["```ledger\nrun now\n```\n", "Brook keeps the ledger \ufdd0 daily.\n"],
    ids=["fence-tag", "index-mark-in-prose"],
)
def test_a_title_in_a_fence_tag_or_in_a_claim_with_an_index_mark_names_no_page(ops, text):
    server = " ".join((SRC / "server.py").read_text().split())
    assert "opening line included" in server
    assert "a claim that holds a mark of the index or a null character names none" in server
    _store, memory = ops
    given, searched = mentions_of_ledger(memory, text)
    assert searched["complete"]
    assert given == [], given


@pytest.mark.parametrize(
    "text",
    [
        "Write to ada@ledger.dev daily.\n",
        "Brook edits ledger/notes.md daily.\n",
        "Brook edits C:\\Ledger\\notes.txt daily.\n",
        "Brook opens ledger.example.com daily.\n",
    ],
    ids=["email", "rootless-path", "windows-path", "host"],
)
def test_a_title_in_what_the_facts_read_as_prose_names_the_page_as_the_sentences_say(ops, text):
    connect = " ".join((SRC / "guides" / "connect.md").read_text().split())
    assert "so a bare `ledger/notes.md` still names a page Ledger" in connect
    relations_doc = " ".join((SRC / "relations.py").read_text().split())
    assert "an email address, a host with no scheme, a Windows path" in relations_doc
    _store, memory = ops
    given, searched = mentions_of_ledger(memory, text)
    assert searched["complete"]
    assert given == [("other", "mentioned_by", "Ledger", "title")], given


# Third round, against kg3/0.1.0 (1ce891f5) merged.
#
# 14. ROWS_MAX, as ruled: another project can make an answer incomplete, and an incomplete
# answer can hold other true reasons than the complete one would show; no reason or count
# given is ever wrong. Where the read under the cap was itself not complete for want of room
# among the claims read (CLAIMS_MAX), a term the cap leaves out frees that room, and a term
# that gave no reason under the cap gives one over it. Smallest input, the bounds made
# small: CLAIMS_MAX 2, and the cap reached inside the walk of the second term by 20 claims
# of another project, written among the project's own rows, that hold its words. Over the
# cap the answer says it is not complete, and every reason it gives re-checks true by the
# oracle of tests/unit/test_rt4_related_held_unit.py, worked out claim by claim.


def test_an_answer_over_the_rows_cap_is_not_complete_and_every_reason_is_true(ops, monkeypatch):
    from test_rt4_related_held_unit import oracle

    from cairn.store import relating

    store, memory = ops
    elsewhere = store.create_project("elsewhere")
    note(memory, "Asked", "Ada sets `--ab` and `--bravo-two` before the run.\n")
    claims = [
        Claim(id=f"cl_e{n}", text=f"Eve sets --bravo-two at {n}.", span_ids=()) for n in range(20)
    ]
    store.save_page(elsewhere.id, "theirs", "Theirs", "", claims)  # among ops's rows
    note(memory, "X one", "Brook sets `--ab` first.\n")
    note(memory, "X two", "Cy sets `--ab` second.\n")
    note(memory, "Y one", "Dee sets `--bravo-two` last.\n")
    monkeypatch.setattr(relating, "CLAIMS_MAX", 2)
    true = set(oracle(store, store.get_project("ops").id)["asked"])

    def answer() -> tuple[list[str], dict, list[tuple]]:
        found = memory.related("ops", "asked", 20)
        given = [
            (
                page["page"],
                why["kind"],
                why["term"],
                why["term_kind"],
                why.get("pages", 2),
                why["there"]["claim_id"],
                why["here"]["claim_id"] if "here" in why else None,
            )
            for page in found["related"]
            for why in page["why"]
        ]
        return [page["page"] for page in found["related"]], found["searched"], given

    under, under_searched, under_given = answer()
    monkeypatch.setattr(relating, "ROWS_MAX", 8)  # `--ab`'s 3 rows, then 5 of `--bravo-two`
    over, over_searched, over_given = answer()
    assert under == ["y-one"] and under_searched["claims_not_read"] == 2, under_searched
    assert over == ["x-one", "x-two"]  # other true reasons than the read under the cap
    assert over_searched["terms_not_looked_up"] == 2 and not over_searched["complete"]
    assert over_given and set(over_given) <= true, (over_given, true)
    assert set(under_given) <= true, (under_given, true)


# 15. The per-caller turn, as ruled and as the contract (src/cairn/server.py) states it: "A
# caller (a key, an agent, or a person whichever session they use) has one read in that line
# at a time". A key is a caller, a key a person made among them (cairn.web.turns.caller_of),
# so one person's three keys hold three places in the process's line, and another caller's
# read waits behind the three.


def test_one_persons_several_keys_are_several_callers_as_the_contract_states(tmp_path, monkeypatch):
    import httpx
    from test_related_page_unit import build

    from cairn.store import relating
    from cairn.web.turns import RELATED_READS

    w = build(tmp_path / "web")
    memory = Memory(w.ada.writes, lint_gate)
    for project, flag in (("ops", "`--audit-keep`"), ("vault", "`--dry-run`")):
        memory.note(project, f"Ada sets {flag}.\n", title="Deploy", topic="Deploy")
        memory.note(project, f"Ada reads {flag}.\n", title="Retention", topic="Retention")
    keys = [w.ada.view.create_key(f"laptop {n}", [w.ops.id], "read") for n in range(3)]
    machine = w.store.create_key("machine", projects=[w.vault.id], access="read")
    order: list[str] = []
    real = relating.related

    def recorded(con, account_id, index, project_id, *rest):
        order.append(project_id)
        return real(con, account_id, index, project_id, *rest)

    monkeypatch.setattr(relating, "related", recorded)

    async def waiting(n: int) -> None:
        for _ in range(500):
            if RELATED_READS.waiting >= n:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"{RELATED_READS.waiting} waiting, not {n}")

    async def main() -> list[int]:
        transport = httpx.ASGITransport(app=w.client.app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:

            def ask(key: str, project: str) -> asyncio.Task:
                path = f"/api/projects/{project}/pages/deploy/related"
                return asyncio.create_task(client.get(path, headers={"x-api-key": key}))

            async with RELATED_READS.process.turn():  # a read under way
                asked = []
                for n, key in enumerate(keys):
                    asked.append(ask(key, "ops"))
                    await waiting(n + 1)
                asked.append(ask(machine, "vault"))
                await waiting(4)
            answers = await asyncio.gather(*asked)
        return [answer.status_code for answer in answers]

    contract = " ".join((SRC / "server.py").read_text().split())
    assert (
        "A caller (a key, an agent, or a person whichever session they use) has one read in"
        " that line at a time" in contract
    )
    statuses = asyncio.run(main())
    w.store.close()
    assert statuses == [200] * 4
    # Ada's three keys went first, one place each: the machine's read ran fourth
    assert order.index(w.vault.id) == 3, order


# 16. A sentence of the connect guide and of the tool's description says when a claim names
# a page's title in its prose; the rule decided since (a claim that holds a mark of the index
# or a null character names no page) is in the contract's text alone. A claim that names
# Ledger in its prose and holds U+FDD0 names no page (section 13), where both sentences say
# it does.


def test_the_guide_and_the_tool_say_a_claim_with_a_mark_of_the_index_names_no_page():
    for said in (SRC / "guides" / "connect.md", SRC / "web" / "mcp.py"):
        text = " ".join(said.read_text().split())
        assert "names this page's title in its prose" in text, said
        assert "mark of the index" in text, said
