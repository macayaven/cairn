"""What an adversarial read of related pages attacked and found to hold (RT4). Each test
passes on the tree it was written against. Ordinary inputs through the public path: keys
narrowed away, a person out of reach, another project with the same title and terms, a
forget and a project deleted between two requests, a write landing inside one read, claims
of multi-byte text cut at the bound, and an oracle of its own over generated projects at
the shipped constants (the weights, the three reasons given, `more`, and which claim each
side is).

Second round, against kg3/0.1.0 (d66b7d67) merged: the oracle restated to the two rules
decided since (a term keeps its case; a claim names a title only in its prose), and what was
attacked past each fix and held: a claim past the bytes bound that holds the title, terms
that differ by case, titles of index syntax and of no word, each passage read once, the turn
given back by a read that raises, and rows of a project deleted and written again.

The world is that of tests/unit/test_related_page_unit.py (Ada owns the workspace; "ops" is
read by every member; "vault" is Ada's alone), with the explorer on. Made-up names."""

import asyncio
import json
import random
import re
import threading
from contextlib import closing
from fractions import Fraction
from types import SimpleNamespace

import pytest

from cairn import relations
from cairn.gate import lint_gate
from cairn.memory import Memory, RequestError
from cairn.store import Claim, Store
from support.store.related_page import build, get, main_of
from support.store.relating_cost import watched
from support.store.rt4_related_held import oracle
from support.web.at_rest import at_rest

MCP = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


@pytest.fixture
def w(tmp_path):
    made = build(tmp_path / "w", graph_explorer=True)
    with made.client:
        at_rest(made.client.app)  # no pass of the keeper lands in a count
        yield made
    made.store.close()


def note(w, title: str, text: str, project: str = "ops") -> dict:
    answer = Memory(w.ada.writes, lint_gate).note(project, text, title=title, topic=title)
    assert answer["status"] == "saved", answer
    return answer


def world(w, vault: bool = True) -> SimpleNamespace:
    deploy = note(w, "Deploy", "Run the job with `--audit-keep` set to 7.\n")
    retention = note(w, "Retention", "`--audit-keep` says how many; see Deploy.\n")
    note(w, "Queue", "Brook drains the queue at noon.\n")
    if vault:  # the same title and term, thirty times, where ops's people cannot read
        for n in range(30):
            note(w, f"Cellar {n}", f"Juniper keeps `--audit-keep` at {n}; see Deploy.\n", "vault")
        note(w, "Deploy", "Juniper runs it with `--audit-keep` set to 9.\n", "vault")
    return SimpleNamespace(deploy=deploy, retention=retention)


def rest(w, key: str, path: str):
    return w.client.get(path, headers={"x-api-key": key})


def tool(w, key: str, name: str, **arguments) -> dict:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
    body["params"] = {"name": name, "arguments": arguments}
    r = w.client.post("/mcp", headers={**MCP, "x-api-key": key}, json=body)
    assert r.status_code == 200, r.text
    return r.json()["result"]


# Reach


def test_a_key_narrowed_away_from_a_project_gets_from_related_what_page_gives(w):
    world(w)
    for reach, asked in (([w.vault.id], "ops"), ([w.ops.id], "vault")):
        key = w.store.create_key("narrow", projects=reach)
        page = rest(w, key, f"/api/projects/{asked}/pages/deploy")
        related = rest(w, key, f"/api/projects/{asked}/pages/deploy/related")
        assert (related.status_code, related.json()) == (page.status_code, page.json())
        by_tool = tool(w, key, "related", project=asked, slug="deploy")
        by_page = tool(w, key, "page", project=asked, slug="deploy")
        assert by_tool["isError"] and by_page["isError"]
        said = by_tool["content"][0]["text"].replace("tool related:", "tool page:")
        assert said == by_page["content"][0]["text"]
    # and by id: the vault's id says no more than its name
    key = w.store.create_key("by-id", projects=[w.ops.id])
    page = rest(w, key, f"/api/projects/{w.vault.id}/pages/deploy")
    related = rest(w, key, f"/api/projects/{w.vault.id}/pages/deploy/related")
    assert (related.status_code, related.json()) == (page.status_code, page.json())


def test_a_person_out_of_reach_gets_the_page_views_and_the_explorers_answers(w):
    world(w)
    for path, same_as in (
        ("/b/vault/p/deploy/related", "/b/vault/p/deploy"),
        ("/b/vault/graph?focus=related%3Adeploy", "/b/vault/graph?focus=page%3Adeploy"),
        ("/b/vault/p/nothing/related", "/b/vault/p/nothing"),
    ):
        seen, page = get(w, path, w.dee), get(w, same_as, w.dee)
        assert seen.status_code == page.status_code == 404, path
        assert main_of(seen.text) == main_of(page.text), path


def test_another_projects_title_and_terms_change_no_count_and_no_row(tmp_path):
    answers, costs = [], []
    for held in (False, True):
        w = build(tmp_path / str(held))
        with w.client:
            at_rest(w.client.app)  # an app of its own: no pass of its keeper lands in the count
            world(w, vault=held)
            key = w.store.create_key("laptop", projects=[w.ops.id], access="read")
            store = w.store
            with watched(store) as seen:
                found = rest(w, key, "/api/projects/ops/pages/deploy/related").json()
            ops_id = w.ops.id
        store.close()
        for page in found["related"]:  # what differs between two stores: ids and times
            for why in page["why"]:
                for side in ("here", "there"):
                    if side in why:
                        why[side] = (why[side]["text"], why[side]["outside"])
        answers.append(found)
        costs.append((len(seen.statements), seen.rows, seen.claims))
        assert ops_id
    assert answers[0] == answers[1]
    (page,) = answers[1]["related"]
    assert [(w["kind"], w.get("pages")) for w in page["why"]] == [
        ("mentioned_by", None),
        ("shares", 2),
    ]
    # the same statements, rows handed back and claims FTS5 read by row, though the vault
    # holds the term and the title in 31 claims
    assert costs[0] == costs[1], costs


# The contract text (src/cairn/server.py): "The store's read runs at most 17 statements,
# whatever the page and the project hold". A person's view and a narrowed key's resolve their
# reach first (the person's role, then one statement a narrowing: 18 and 20 in all); the
# read proper, past that, is the operator's 17 for every view, at the bound on terms with a
# long claim that names the page.

REACH = ("SELECT role FROM memberships", "SELECT id FROM projects WHERE account_id")


def test_the_store_read_runs_the_contracts_17_statements_for_every_view(w):
    from support.store.relating_cost import settings_loaded

    memory = Memory(w.ada.writes, lint_gate)
    lines = [
        "- Step: " + " ".join(f"`t{n:05d}.cfg`" for n in range(first, first + 10))
        for first in range(0, 260, 10)
    ]
    memory.note("ops", "\n".join(lines) + "\n", title="Asked", topic="Asked")
    long = "Brook sets `t00001.cfg`; " + "the ledger " * 80 + "then see Asked.\n"
    memory.note("ops", long, title="Other", topic="Other")
    views = {
        "operator": w.store,
        "owner": w.ada.view,
        "narrowed twice": w.ada.view.narrow([w.ops.id, w.vault.id]).narrow([w.ops.id], "read"),
    }
    reach, read = {}, {}
    for name, view in views.items():
        view.related_pages(w.ops.id, "asked", 20)  # FTS5 loads its settings once
        with watched(w.store) as seen:
            found = view.related_pages(w.ops.id, "asked", 20)
        assert [p.slug for p in found.pages] == ["other"]
        assert found.pages[0].why[0].there.cut  # the mention is given by its line
        said = [" ".join(str(s).split()) for s in seen.statements]
        reach[name] = sum(s.startswith(REACH) for s in said)
        read[name] = len(said) - reach[name] - settings_loaded(seen.statements)
    assert read == {"operator": 17, "owner": 17, "narrowed twice": 17}, read
    assert reach == {"operator": 0, "owner": 1, "narrowed twice": 3}, reach


# Forget, a project deleted, and a write inside one read


def test_a_forget_takes_the_relation_from_every_surface_in_the_next_request(w):
    made = world(w)
    key = w.store.create_key("laptop")
    surfaces = {
        "rest": lambda: rest(w, key, "/api/projects/ops/pages/deploy/related").text,
        "mcp": lambda: json.dumps(tool(w, key, "related", project="ops", slug="deploy")),
        "web": lambda: get(w, "/b/ops/p/deploy/related").text,
        "explorer": lambda: get(w, "/b/ops/graph?focus=related%3Adeploy").text,
    }
    before = {name: read() for name, read in surfaces.items()}
    assert all("retention" in text.lower() for text in before.values()), before.keys()
    Memory(w.ada.view, lint_gate).forget("ops", made.retention["note_id"])
    for name, read in surfaces.items():
        after = read()
        assert "Retention" not in after and "says how many" not in after, name
        assert made.retention["note_id"] not in after, name


def test_deleting_another_project_changes_nothing_of_an_answer(w):
    world(w)
    key = w.store.create_key("laptop")
    before = rest(w, key, "/api/projects/ops/pages/deploy/related").json()
    assert Memory(w.ada.view, lint_gate).delete_project("vault", "vault")
    assert rest(w, key, "/api/projects/ops/pages/deploy/related").json() == before


def test_a_write_that_lands_inside_one_read_changes_nothing_of_it(w):
    """Between the read's listing of the claims that hold each term and its reading of those
    claims, another thread forgets Retention's note and files a page that states the term
    and names Deploy. The answer is the moment the read began; the next read is the moment
    after the write."""
    made = world(w, vault=False)
    store = w.store
    expected = Memory(store, lint_gate).related("ops", "deploy")
    fired = threading.Event()

    def write() -> None:
        memory = Memory(w.ada.view, lint_gate)
        memory.forget("ops", made.retention["note_id"])
        memory.note("ops", "Cy sets `--audit-keep`; see Deploy.\n", title="Late", topic="Late")

    def traced(statement: str) -> None:
        if not fired.is_set() and "LIMIT 65" in statement and "UNION ALL" not in statement:
            fired.set()  # the title's listing: the read is under way, its snapshot taken
            writer = threading.Thread(target=write)
            writer.start()
            writer.join()

    store._observe(trace=traced)
    try:
        during = Memory(store, lint_gate).related("ops", "deploy")
    finally:
        store._observe(trace=None)
    assert fired.is_set()
    assert during == expected
    after = Memory(store, lint_gate).related("ops", "deploy")
    assert [page["page"] for page in after["related"]] == ["late"]


# A claim cut at the bound, in text of several bytes a character


def test_a_long_claim_is_given_by_the_exact_line_wherever_the_term_stands(w):
    filler = "\U0001f600 é́ " * 160  # 4-byte, 2-byte and combining characters
    first = f"`k.cfg` {filler}"
    last = f"{filler} `z.cfg`"
    note(w, "Asked", "Ada sets `k.cfg` and `z.cfg`.\n")
    note(w, "Long", f"{first}\n\n{last}\n")
    found = Memory(w.store, lint_gate).related("ops", "asked")
    (page,) = found["related"]
    lines = {why["term"]: why["there"] for why in page["why"]}
    assert set(lines) == {"k.cfg", "z.cfg"}
    for term, written in (("k.cfg", first), ("z.cfg", last)):
        claim = lines[term]
        assert claim["text_cut"] is True and "text" not in claim
        assert claim["line_cut"] is True and len(claim["line"]) == relations.LINE_MAX
        assert claim["line"] in written and f"`{term}`" in claim["line"]
        assert written.startswith(claim["line"]) or written.endswith(claim["line"])


def test_a_long_claim_that_names_the_title_after_many_wide_characters_is_given_its_line(w):
    before = "\U0001f600" * 700
    note(w, "Ledger", "Brook keeps the books.\n")
    note(w, "Books", f"{before} then see Ledger for the rest.\n")
    found = Memory(w.store, lint_gate).related("ops", "ledger")
    ((page, why),) = [(p, y) for p in found["related"] for y in p["why"]]
    claim = why["there"]
    assert (page["page"], why["kind"]) == ("books", "mentioned_by")
    assert claim["text_cut"] is True and claim["line_cut"] is True
    assert claim["line"].endswith(" then see Ledger for the rest.") or "Ledger" in claim["line"]
    assert claim["line"] in f"{before} then see Ledger for the rest."
    assert len(claim["line"]) == relations.LINE_MAX


# An oracle of its own, over generated projects, at the shipped constants

VOCABULARY = [
    "`--dry-run`",
    "--keep-days",
    "`-v`",
    "/etc/quill.toml",
    "~/ledger/notes.md",
    "https://docs.example.com/quill",
    "v1.2.3",
    "2.0.11",
    "`make lint`",
    "`os_login_2fa`",
    "`restartpolicy`",
    "`name`",
    "`QUILL_HOME=/srv`",
]
TITLES = ["Quill", "Ledger", "Brook keeps", "Night run", "Port", "Ada", "The log"]
PROSE = ["Brook keeps", "the Quill", "Night run", "Port of Ada", "the log", "a note"]


def generated(seed: int, projects: int) -> list[dict[str, str]]:
    rng = random.Random(seed)
    made = []
    for _ in range(projects):
        pool = rng.sample(VOCABULARY, rng.randint(2, 6))
        pages = {}
        for title in rng.sample(TITLES, rng.randint(3, 7)):
            passages = []
            for _ in range(rng.randint(1, 3)):
                terms = rng.sample(pool, rng.randint(1, min(3, len(pool))))
                spelled = [t.upper() if rng.random() < 0.2 else t for t in terms]
                words = rng.sample(PROSE, 2)
                body = f"{words[0]} uses {' and '.join(spelled)} with {words[1]}."
                passages.append(("- " if rng.random() < 0.3 else "") + body)
            pages[title] = "\n\n".join(passages) + "\n"
        made.append(pages)
    return made


def test_every_answer_is_the_rule_worked_out_here_at_the_shipped_constants(tmp_path):
    checked = Fraction(0)
    reasons_seen = 0
    with closing(Store(tmp_path / "gen.db")) as store:
        memory = Memory(store, lint_gate)
        for n, pages in enumerate(generated(20261121, 60)):
            name = f"g{n:02d}"
            project = store.create_project(name)
            for title, text in pages.items():
                try:
                    memory.note(name, text, title=title, topic=title)
                except RequestError:
                    continue
            expected = oracle(store, project.id)
            for slug, reasons in expected.items():
                answer = memory.related(name, slug, relations.RELATED_MAX)
                assert answer["searched"]["complete"], (name, slug)
                by_page: dict[str, list[tuple]] = {}
                for reason in reasons:
                    by_page.setdefault(reason[0], []).append(reason)

                def weight(found: list[tuple]) -> Fraction:
                    return sum((Fraction(1, r[4] - 1) for r in found), Fraction(0))

                order = sorted(by_page, key=lambda s: (-weight(by_page[s]), s))
                assert [p["page"] for p in answer["related"]] == order, (name, slug)
                for page in answer["related"]:
                    mine = sorted(
                        by_page[page["page"]],
                        key=lambda r: (-Fraction(1, r[4] - 1), r[1], r[3], r[2]),
                    )
                    top = mine[: relations.WHY_MAX]
                    got = [
                        (
                            page["page"],
                            why["kind"],
                            why["term"],
                            why["term_kind"],
                            why.get("pages", 2),
                            why["there"]["claim_id"],
                            why["here"]["claim_id"] if "here" in why else None,
                        )
                        for why in page["why"]
                    ]
                    assert got == top, (name, slug, page["page"])
                    assert page["more"] == len(mine) - len(top), (name, slug)
                    reasons_seen += len(mine)
                checked += 1
    assert checked >= 250 and reasons_seen >= 500, (checked, reasons_seen)


# Second round: past each fix


def test_a_title_held_by_a_claim_past_the_bytes_bound_says_the_answer_is_not_complete(
    w, monkeypatch
):
    from cairn.store import relating

    note(w, "Ledger", "Ada keeps the books.\n")
    note(w, "Night", "Brook closes the Ledger at night, " + "and counts the till " * 40 + ".\n")
    monkeypatch.setattr(relating, "BYTES_MAX", 200)  # the claim's passage does not fit
    memory = Memory(w.ada.writes, lint_gate)
    found = memory.related("ops", "ledger", 20)
    assert found["related"] == []
    assert found["searched"]["claims_not_read"] == 1 and not found["searched"]["complete"]
    monkeypatch.setattr(relating, "BYTES_MAX", 750_000)
    (page,) = memory.related("ops", "ledger", 20)["related"]
    assert [why["kind"] for why in page["why"]] == ["mentioned_by"]


def test_terms_that_differ_by_case_are_two_and_each_is_counted_exactly(w):
    note(w, "Docs", "Read https://Docs.example.com/quill first.\n")
    note(w, "Help", "Read https://docs.example.com/quill first.\n")
    note(w, "Both", "Mount `/srv/Quill` and `/srv/quill` both.\n")
    note(w, "Lower", "Brook mounts `/srv/quill`.\n")
    note(w, "Upper", "Cy mounts `/SRV/QUILL`.\n")
    memory = Memory(w.ada.writes, lint_gate)
    assert memory.related("ops", "docs", 20)["related"] == []  # decided: hosts keep case too
    (page,) = memory.related("ops", "both", 20)["related"]
    (why,) = page["why"]
    assert (page["page"], why["term"], why["pages"]) == ("lower", "/srv/quill", 2)
    assert memory.related("ops", "upper", 20)["related"] == []


@pytest.mark.parametrize(
    "title", ['Ada "the" log', "NEAR AND OR NOT", "title : quill", "Quill*", "^Ledger", "!!!"]
)
def test_a_title_of_index_syntax_or_of_no_word_is_asked_as_written(w, title):
    words = re.findall(r"\w+", title)
    note(w, title, "Ada keeps the books.\n")
    note(w, "Other", f"Brook wrote {title} on the board.\n")
    memory = Memory(w.ada.writes, lint_gate)
    slug = memory.overview("ops")["pages"][0]["slug"] if not words else None
    slug = slug or next(p["slug"] for p in memory.overview("ops")["pages"] if p["title"] == title)
    found = memory.related("ops", slug, 20)
    assert found["searched"]["complete"]
    named = [p["page"] for p in found["related"]]
    assert named == (["other"] if words else []), (title, found)


def test_each_passage_is_read_for_its_facts_once_a_read(w, monkeypatch):
    note(w, "Asked", "Ada sets `--alpha-one`, `--beta-two` and `--gamma-three`; see Night.\n")
    for n in range(3):
        note(w, f"Night {n}", f"Brook {n} sets `--alpha-one`, `--beta-two`, `--gamma-three`.\n")
    note(w, "Night", "Cy sets `--alpha-one` at night.\n")
    read: list[str] = []
    real = relations.extract_facts

    def counted(span):
        read.append(span.id)
        return real(span)

    monkeypatch.setattr(relations, "extract_facts", counted)
    found = Memory(w.ada.writes, lint_gate).related("ops", "asked", 20)
    assert len(found["related"]) == 4 and found["searched"]["complete"]
    assert len(read) == len(set(read)) == 5, read  # the page's own and four others, once each


def test_a_read_that_raises_gives_the_turn_back(w, monkeypatch):
    from cairn.store import relating
    from cairn.web.turns import RELATED_READS

    world(w, vault=False)
    key = w.store.create_key("laptop", projects=[w.ops.id], access="read")
    real, calls = relating.related, []

    def failing(*arguments):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("a read that fails")
        return real(*arguments)

    monkeypatch.setattr(relating, "related", failing)
    with pytest.raises(RuntimeError):
        rest(w, key, "/api/projects/ops/pages/deploy/related")
    assert RELATED_READS.held == 0 and not w.store._state.related_turn.locked()
    again = rest(w, key, "/api/projects/ops/pages/deploy/related")
    assert again.status_code == 200 and [p["page"] for p in again.json()["related"]] == [
        "retention"
    ]
    assert RELATED_READS.held == 0 and RELATED_READS.waiting == 0


def test_rows_of_a_project_deleted_and_written_again_change_nothing_of_an_answer(w):
    world(w, vault=False)
    memory = Memory(w.ada.writes, lint_gate)
    gone = w.ada.view.create_project("gone", everyone="read")
    for n in range(5):  # rows of another project between the pages of ops
        note(w, f"Gone {n}", f"Dee keeps `--audit-keep` at {n}; see Deploy.\n", "gone")
        note(w, f"Ops {n}", f"Brook keeps step {n}; see Deploy.\n")
    before = memory.related("ops", "deploy", 20)
    w.ada.view.delete_project(gone.id)
    later = w.ada.view.create_project("later", everyone="read")
    for n in range(5):  # rows written after: the index's row numbers go on from the last
        note(w, f"Later {n}", f"Cy keeps `--audit-keep` at {n}; see Deploy.\n", "later")
    note(w, "Ops 2", "Brook keeps step 2 again.\n")  # a page saved again: its rows move last
    after = memory.related("ops", "deploy", 20)

    def shape(answer):
        return [
            (p["page"], [(y["kind"], y["term"], y.get("pages")) for y in p["why"]], p["more"])
            for p in answer["related"]
        ], answer["searched"]

    assert shape(before) == shape(after)
    assert later.id


def first_claim(w, slug: str):
    (claim, *_) = w.store.get_page(w.ops.id, slug).claims
    return claim


def test_a_passage_two_claims_cite_one_of_eight_and_the_pages_own_are_charged_once(w, monkeypatch):
    from cairn.store import relating

    note(w, "Asked", "Ada sets `--alpha-one` and `--beta-two` before the run.\n")
    note(w, "Source", "Brook sets `--alpha-one` in the night run, " + "slowly " * 30 + ".\n")
    for n in range(7):
        note(w, f"Spare {n}", f"Cy keeps spare line {n}, " + "quietly " * 30 + ".\n")
    source = first_claim(w, "source")
    spares = [first_claim(w, f"spare-{n}").span_ids[0] for n in range(7)]
    (passage,) = source.span_ids
    size = len(w.store.get_spans([passage])[passage].text.encode())
    # Another page whose claim cites the one passage, and one whose claim cites eight: the
    # passage first, then seven others; and a page whose claim cites the asked page's own.
    for slug in ("copy",):  # five pages hold the term: not common
        w.store.save_page(
            w.ops.id,
            slug,
            slug.title(),
            "",
            [Claim(id=f"cl_{slug}", text=source.text, span_ids=(passage,))],
        )
    eight = Claim(id="cl_eight", text=source.text, span_ids=(passage, *spares))
    w.store.save_page(w.ops.id, "eight", "Eight", "", [eight])
    asked = first_claim(w, "asked")
    w.store.save_page(
        w.ops.id, "own", "Own", "", [Claim(id="cl_own", text=asked.text, span_ids=asked.span_ids)]
    )

    read: list[str] = []
    real = relations.extract_facts

    def counted(span):
        read.append(span.id)
        return real(span)

    monkeypatch.setattr(relations, "extract_facts", counted)
    monkeypatch.setattr(relating, "BYTES_MAX", size + 10)  # one passage of that size, not two
    found = Memory(w.ada.writes, lint_gate).related("ops", "asked", 20)
    assert found["searched"]["claims_not_read"] == 0 and found["searched"]["complete"], found
    assert sorted(p["page"] for p in found["related"]) == [
        "copy",
        "eight",
        "own",
        "source",
    ]
    assert sorted(read) == sorted({asked.span_ids[0], passage}), read  # each once; no spare


# Third round: the per-caller turn, and the two no-mention rules


def test_a_caller_whose_read_is_cancelled_in_the_process_line_gives_both_turns_back():
    from cairn.web.turns import RelatedReads

    reads = RelatedReads()
    ran: list[str] = []

    async def read(caller: str, name: str) -> None:
        async with reads.turn(caller):
            ran.append(name)

    async def main() -> None:
        async with reads.process.turn():  # another caller's read under way
            first = asyncio.create_task(read("ada", "first"))
            second = asyncio.create_task(read("ada", "second"))
            other = asyncio.create_task(read("brook", "other"))
            await asyncio.sleep(0.05)
            assert (reads.waiting, reads.callers) == (3, 2)
            first.cancel()  # holds Ada's turn, waits for the process's
            await asyncio.sleep(0.05)
            assert (reads.waiting, reads.callers) == (2, 2)
        await asyncio.gather(second, other)
        with pytest.raises(asyncio.CancelledError):
            await first

    asyncio.run(main())
    assert ran == ["second", "other"] or ran == ["other", "second"]
    assert (reads.held, reads.waiting, reads.callers) == (0, 0, 0)


def test_a_claim_with_a_mark_of_the_index_hides_no_other_claims_mention(w):
    note(w, "Ledger", "Ada keeps the books.\n")
    for n in range(6):  # six pages hold the title beside a mark: none names it
        note(w, f"Marked {n}", f"Brook {n} keeps the ledger ﷐ here.\n")
    note(w, "Plain", "Cy keeps the ledger ﷐ here.\n\nDee closes the ledger at night.\n")
    found = Memory(w.ada.writes, lint_gate).related("ops", "ledger", 20)
    assert found["searched"]["complete"]
    (page,) = found["related"]
    assert page["page"] == "plain" and page["why"][0]["kind"] == "mentioned_by"
    assert "Dee closes" in page["why"][0]["there"]["text"]


def test_a_title_past_the_rows_cap_is_counted_as_a_term_and_mentions_nothing(w, monkeypatch):
    from cairn.store import relating

    note(w, "Asked", "Ada sets `--audit-keep` before the run.\n")
    note(w, "Keeper", "Brook reads `--audit-keep` after it.\n")
    for n in range(10):
        note(w, f"Mention {n}", f"Cy {n} looked at what Ada asked.\n")
    memory = Memory(w.ada.writes, lint_gate)
    whole = memory.related("ops", "asked", 20)
    assert whole["searched"]["complete"]  # the title: more than 5 pages name it, common
    assert [p["page"] for p in whole["related"]] == ["keeper"]
    monkeypatch.setattr(relating, "ROWS_MAX", 5)  # the term's 2 rows, then the title's walk
    cut = memory.related("ops", "asked", 20)
    assert [p["page"] for p in cut["related"]] == ["keeper"]
    assert cut["searched"]["terms_not_looked_up"] == 1 and not cut["searched"]["complete"]
