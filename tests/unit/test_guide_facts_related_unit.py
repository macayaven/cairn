"""Sentences of the connect and privacy guides on related pages (KG1), each pinned to what
cairn does. Real store in a temporary file, notes filed as `serve` files them, made-up names."""

import ast
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from guide_source import guide_source
from starlette.testclient import TestClient

from cairn import relations
from cairn.arrange import tidy_page
from cairn.gate import lint_gate
from cairn.memory import BadRequest, Memory
from cairn.server import create_app
from cairn.store import Store, relating

BASE = "http://127.0.0.1:8765"


def flat(text: str) -> str:
    return " ".join(text.split())


def says(guide: str, *sentences: str) -> None:
    text = flat(guide_source(guide))
    for sentence in sentences:
        assert text.count(flat(sentence)) == 1, sentence


def dump(db: Path) -> str:
    with closing(sqlite3.connect(db)) as con:
        return "\n".join(con.iterdump())


@pytest.fixture
def db(tmp_path) -> Path:
    return tmp_path / "cairn.db"


@pytest.fixture
def store(db):
    with closing(Store(db)) as made:
        made.create_project("ops")
        yield made


@pytest.fixture
def memory(store) -> Memory:
    return Memory(store, lint_gate)


def file(memory: Memory, title: str, text: str, project: str = "ops") -> str:
    answer = memory.note(project, text, title=title, topic=title)
    assert answer["status"] == "saved", answer
    return answer["page"]


def pages(answer: dict) -> list[str]:
    return [page["page"] for page in answer["related"]]


def why(answer: dict) -> dict[str, list[tuple[str, str]]]:
    return {
        page["page"]: [(reason["kind"], reason["term"]) for reason in page["why"]]
        for page in answer["related"]
    }


# connect.md


def test_related_gives_the_best_pages_five_unless_limit_says(store, memory):
    says(
        "connect",
        "`related` gives the pages of a project most related to one of its pages, best first: 5"
        " of them unless `limit` asks for another number from 1 to 20.",
        "Over REST it is `GET /api/projects/<project>/pages/<slug>/related`.",
    )
    terms = [f"`t{n}.cfg`" for n in range(10)]
    file(memory, "Asked", "Use " + ", ".join(terms) + " and `both.cfg`.\n")
    for n, term in enumerate(terms):
        more = " with `both.cfg`" if n == 9 else ""
        file(memory, f"Page {n}", f"Brook sets {term}{more}.\n")
    file(memory, "Other", "Ada sets `both.cfg` too.\n")  # both.cfg: three pages hold it
    answer = memory.related("ops", "asked")
    assert len(pages(answer)) == 5 and answer["searched"]["pages_left_out"] == 6
    assert relations.RELATED_DEFAULT == 5
    # best first: the page that states two of its terms, then those that state one only two
    # pages hold, by slug; the page that states one three pages hold comes last of all
    assert pages(answer)[:3] == ["page-9", "page-0", "page-1"]
    every = memory.related("ops", "asked", 20)
    assert len(pages(every)) == 11 and pages(every)[-1] == "other"
    assert pages(memory.related("ops", "asked", 1)) == ["page-9"]
    for limit in (0, 21):
        with pytest.raises(BadRequest, match="1 to 20"):
            memory.related("ops", "asked", limit)
    key = store.create_key("laptop")
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        got = client.get("/api/projects/ops/pages/asked/related", headers={"x-api-key": key})
        assert (got.status_code, got.json()) == (200, answer)


def test_each_related_page_says_why_with_the_claims_it_rests_on(memory):
    says(
        "connect",
        "Each related page says why, with the claims the reason rests on: both pages state the"
        " same term (a URL, a path, a flag, a version, or code in backticks that is not a plain"
        " word), or a claim of the other page holds this page's title.",
    )
    file(
        memory,
        "Runbook",
        "See https://example.invalid/quill and ~/quill/notes; pass --dry-run to v2.4.1; then run"
        " `quill sync --all` with `retries` on.\n",
    )
    file(memory, "Address", "The page is https://example.invalid/quill now.\n")
    file(memory, "Folder", "Brook keeps ~/quill/notes tidy.\n")
    file(memory, "Flag", "Ada passes --dry-run first.\n")
    file(memory, "Release", "Quill shipped v2.4.1 on a Tuesday.\n")
    file(memory, "Sync", "Nightly, `quill sync --all` runs.\n")
    file(memory, "Word", "Set `retries` to three.\n")  # a plain word in backticks
    file(memory, "Pointer", "Read the runbook before a deploy.\n")
    answer = memory.related("ops", "runbook", 20)  # six pages: one more than the default gives
    assert why(answer) == {
        "address": [("shares", "https://example.invalid/quill")],
        "flag": [("shares", "--dry-run")],
        "folder": [("shares", "~/quill/notes")],
        "pointer": [("mentioned_by", "Runbook")],
        "release": [("shares", "v2.4.1")],
        "sync": [("shares", "quill sync --all")],
    }
    for page in answer["related"]:
        for reason in page["why"]:
            assert reason["there"]["text"] and reason["there"]["passage_ids"]
            if reason["kind"] == "shares":
                assert reason["term"] in reason["here"]["text"].casefold()
                assert reason["term"] in reason["there"]["text"].casefold()
            else:
                assert "runbook" in reason["there"]["text"]


def test_a_term_more_than_five_pages_hold_relates_none(memory):
    says("connect", "A term that more than 5 pages of the project hold relates none.")
    assert relations.COMMON == 5
    for n in range(5):
        file(memory, f"Page {n}", f"Page {n} sets `os_login_2fa`.\n")
    assert len(pages(memory.related("ops", "page-0"))) == 4  # five hold it
    file(memory, "Page 5", "Page 5 sets `os_login_2fa`.\n")
    assert all(pages(memory.related("ops", f"page-{n}")) == [] for n in range(6))  # six do


def imports_of(module) -> set[str]:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def test_relations_are_worked_out_when_asked_and_no_model_is_asked(db, store, memory):
    says(
        "connect",
        "The relations are worked out by rule when they are asked for, from the pages as they"
        " are; no model is asked.",
    )
    file(memory, "Asked", "Use `a.cfg` here.\n")
    assert pages(memory.related("ops", "asked")) == []
    file(memory, "Later", "Brook sets `a.cfg` later.\n")  # the next answer has it, at once
    before = dump(db)
    assert pages(memory.related("ops", "asked")) == ["later"]
    assert dump(db) == before  # asking wrote nothing
    # the code that works them out imports no model, and nothing that could ask one
    asked = imports_of(relations) | imports_of(relating)
    assert not [name for name in asked if "llm" in name or "arrange" in name or "httpx" in name]


def test_searched_says_whether_the_answer_is_complete_and_counts_what_it_left_out(memory):
    says("connect", "`searched` says whether the answer is complete and counts what it left out.")
    file(memory, "Asked", "Use `a.cfg` and `b.cfg`.\n")
    file(memory, "First", "Set `a.cfg`.\n")
    file(memory, "Second", "Set `b.cfg`.\n")
    whole = memory.related("ops", "asked")["searched"]
    assert whole == {
        "complete": True,
        "terms_looked_up": 2,
        "terms_not_looked_up": 0,
        "claims_read": 2,
        "claims_not_read": 0,
        "pages_left_out": 0,
    }
    cut = memory.related("ops", "asked", 1)["searched"]
    assert cut == {**whole, "complete": False, "pages_left_out": 1}


def test_what_this_version_does_not_do(store, memory):
    says(
        "connect",
        "This version relates pages of one project only; it lists the pages that mention a page,"
        " not the pages it mentions; it relates no pages by a term a search does not find as it"
        " is written (`1,250x`, which is compared without its separator); it reads a claim that"
        " cites several passages by its first; it follows no path of several steps, groups"
        " nothing over a whole project, and infers nothing from meaning.",
    )
    store.create_project("other")
    file(memory, "Asked", "Use `a.cfg`; see the Queue notes.\n")
    file(memory, "Step", "Set `a.cfg` and `b.cfg`.\n")
    file(memory, "Far", "Only `b.cfg` here.\n")  # two steps from Asked, by way of Step
    file(memory, "Queue notes", "Brook drains it at noon.\n")
    file(memory, "Elsewhere", "Set `a.cfg` there.\n", project="other")
    asked = memory.related("ops", "asked")
    assert pages(asked) == ["step"]  # not the other project's page, not the page two steps away
    assert pages(memory.related("other", "elsewhere")) == []
    # Asked mentions "Queue notes": that page's answer says so, and Asked's does not list it
    assert why(memory.related("ops", "queue-notes")) == {"asked": [("mentioned_by", "Queue notes")]}
    assert "queue-notes" not in pages(asked)
    # one page is asked about, and its answer holds pages, each with its reasons: no group
    assert set(asked) == {"project", "page", "title", "version", "related", "searched"}
    assert set(asked["related"][0]) == {"page", "title", "version", "why", "more"}
    # a term written with its thousands separator relates none, from either page
    file(memory, "Comma", "The cap is `1,250x` here.\n")
    file(memory, "Plain", "The cap is `1250x` here.\n")
    file(memory, "Twin", "Brook saw `1250x` twice.\n")
    assert pages(memory.related("ops", "comma")) == []
    assert (pages(memory.related("ops", "plain")), pages(memory.related("ops", "twin"))) == (
        ["twin"],
        ["plain"],
    )
    # a claim of several passages is read by its first: the line eleven notes of one page
    # hold, folded into one claim by a tidy, relates by the term of that line
    for n in range(11):
        file(memory, "Fold", f"Ada pins `quill.lock` daily.\n\nBrook checks ledger {n} at dawn.\n")
    file(memory, "Lock", "Quill reads `quill.lock` at noon.\n")
    assert tidy_page(store, store.get_project("ops").id, "fold").status == "arranged"
    (folded,) = [
        c for c in store.get_page(store.get_project("ops").id, "fold").claims if "pins" in c.text
    ]
    assert len(folded.span_ids) == 11
    (lock,) = memory.related("ops", "lock")["related"]
    assert (lock["page"], lock["why"][0]["there"]["claim_id"]) == ("fold", folded.id)
    assert lock["why"][0]["there"]["passage_ids"][0] == folded.span_ids[0]
    # the same thing said in other words relates nothing
    file(memory, "Retries", "Quill retries three times before it gives up.\n")
    file(memory, "Attempts", "Quill tries again 3 times, then stops.\n")
    assert pages(memory.related("ops", "retries")) == []
    assert pages(memory.related("ops", "attempts")) == []


# privacy.md


def test_nothing_of_a_relation_is_stored_and_forgetting_a_note_changes_them_at_once(
    db, store, memory
):
    says(
        "privacy",
        "**No record of how pages relate.** The pages related to a page are worked out when"
        " someone asks, from the pages as they are at that moment: nothing of a relation is"
        " stored, and forgetting a note changes them at once.",
    )
    file(memory, "Asked", "Use `a.cfg` here.\n")
    file(memory, "Held", "Brook sets `a.cfg` on the heron host.\n")
    (theirs,) = [
        note for note in store.list_notes(store.get_project("ops").id) if "heron" in note.text
    ]
    before = dump(db)
    answer = memory.related("ops", "asked")
    assert pages(answer) == ["held"] and "heron" in str(answer)
    assert dump(db) == before  # nothing of it was stored: not a table, not a row
    with closing(sqlite3.connect(db)) as con:
        tables = [name for (name,) in con.execute("SELECT name FROM sqlite_master")]
    assert not [name for name in tables if "relat" in name]
    memory.forget("ops", theirs.id)
    after = memory.related("ops", "asked")  # the next call, with nothing between
    assert pages(after) == [] and "heron" not in str(after)
    assert "heron" not in dump(db)
