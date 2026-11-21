"""Unit tests for the bound on a passage's headings (H6c follow-up): every span's heading path
takes at most cairn.store.HEADINGS_MAX (400) UTF-8 bytes, on every install. A heading is
stored with every span under it, so without this bound a note of long headings over many
list items took hundreds of times its storage cap on disk."""

import io
import json
import sqlite3
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import cairn.store.memory
from cairn.gate import lint_gate
from cairn.memory import LongHeadings, Malformed, Memory
from cairn.plans import Plan, Plans
from cairn.server import create_app
from cairn.store import HEADINGS_MAX, FieldTooLong, HeadingsTooLong, Store

BASE = "http://127.0.0.1:8765"
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


def heading(size: int, mark: str = "h", level: int = 1) -> str:
    """A heading line whose text is `size` UTF-8 bytes."""
    return "#" * level + " " + mark * (size // len(mark.encode())) + "\n\n"


def test_the_bound_is_four_hundred_bytes():
    assert HEADINGS_MAX == 400


@pytest.mark.parametrize(
    "text",
    [
        heading(401) + "- one\n",  # one heading
        heading(200) + heading(201, "g", 2) + "- one\n",  # a path of two
        heading(402, "é") + "Text.\n",  # bytes, not characters: 201 of them
        heading(390) + heading(11, "g", 2),  # a heading's own path counts, passage or not
    ],
    ids=["one", "nested", "bytes", "heading-only"],
)
def test_a_note_under_headings_past_the_bound_is_refused_before_anything(store, monkeypatch, text):
    project = store.create_project("ops")
    screened = []
    monkeypatch.setattr(Store, "_screen", lambda self, fields: screened.append(1) or [])
    with pytest.raises(HeadingsTooLong) as long:
        store.add_note(project.id, text)
    said = str(long.value)
    assert isinstance(long.value, FieldTooLong) and long.value.field == "headings"
    assert "headings" in said and "400" in said and "hhhh" not in said and "é" not in said
    assert screened == [] and store.list_notes(project.id) == []  # nothing screened or stored


def test_headings_of_exactly_the_bound_pass(store):
    project = store.create_project("ops")
    store.add_note(project.id, heading(200) + heading(200, "é", 2) + "- one\n")


def test_memory_refuses_it_as_malformed(store):
    store.create_project("ops")
    with pytest.raises(LongHeadings) as long:
        Memory(store, lint_gate).note("ops", heading(401) + "- one\n")
    assert isinstance(long.value, Malformed) and long.value.status == 400
    assert long.value.reason == "headings past their bound"


def test_rest_mcp_and_the_composer_answer_it(tmp_path):
    store = Store(tmp_path / "web.db")
    try:
        key = store.create_key("laptop")
        store.create_project("ops")
        app = create_app(store, public_url=BASE)
        text = heading(401) + "- one, kingfisher\n"
        with TestClient(app, base_url=BASE) as client:
            rest = client.post(
                "/api/projects/ops/notes", headers={"x-api-key": key}, json={"text": text}
            )
            assert rest.status_code == 400 and "headings" in rest.json()["error"]
            assert "kingfisher" not in rest.json()["error"]
            call = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "note", "arguments": {"project": "ops", "text": text}},
            }
            headers = {**MCP_HEADERS, "x-api-key": key}
            answer = client.post("/mcp", headers=headers, json=call).json()["result"]
            assert answer["isError"] is True and "headings" in answer["content"][0]["text"]
            client.post("/login", data={"key": key}, headers=SAME_ORIGIN)
            form = client.post("/b/ops/notes", data={"text": text}, headers=SAME_ORIGIN)
            assert form.status_code == 400 and "headings" in form.text
            assert "kingfisher" in form.text  # what was typed stays in the form
        assert store.list_notes(store.get_project("ops").id) == []
    finally:
        store.close()


def test_an_import_with_a_note_under_headings_past_the_bound_imports_nothing(
    store, tmp_path, monkeypatch
):
    source = Store(tmp_path / "source.db")
    try:
        project = source.create_project("ops")
        monkeypatch.setattr(cairn.store.memory, "HEADINGS_MAX", 10_000)  # as an older build
        source.add_note(project.id, heading(401) + "- one\n")
        monkeypatch.undo()
        archive = io.BytesIO()
        source.export(archive)
    finally:
        source.close()
    target = store.for_account(store.create_account("Brook").id)
    with pytest.raises(ValueError, match="headings") as refused:
        target.import_export(archive)
    assert "400" in str(refused.value) and "hhhh" not in str(refused.value)
    assert target.list_projects() == []


# What the bounds buy: a workspace filled through REST to a cap with any of these shapes (the
# ones that took 198 to 710 times its cap before the heading bound and the heading charge,
# those found after, and tests/acceptance/test_h6c_disk.py's five) takes at most 30 times its
# storage cap plus PER_NOTE bytes for each note it holds, after compact(). The first term is
# what the caps on bytes and passages bound; the second is what every note makes whatever
# its length (its note row, its audit event, up to eight heading spans more than its
# passages), which a plan's notes_per_month bounds as it grows. A heading's path is charged
# against the passages cap (charge_of), so the long-heading shapes hold 60 list items a
# note, not 300: two notes fit.

CAP = 60_000
TINY_CAP = 6_000  # for the shapes of hundreds of tiny notes, so they run in seconds
PER_NOTE = 1_500
ITEMS = 60


def long_heading(n: int, level: int = 1) -> str:
    return "#" * level + f" H{n} " + "h" * (390 - len(f"H{n} ")) + "\n\n"


def one_long_heading(n: int) -> str:
    return long_heading(n) + "".join(f"- w{n}x{i}\n" for i in range(ITEMS))


def six_nested_headings(n: int) -> str:
    heads = "".join(
        "#" * level + f" L{level}n{n} " + "h" * (60 - len(f"L{level}n{n} ")) + "\n\n"
        for level in range(1, 7)
    )
    return heads + "".join(f"- w{n}x{i}\n" for i in range(ITEMS))


def six_accented_headings(n: int) -> str:  # 2 UTF-8 bytes a character
    heads = "".join(
        "#" * level + f" L{level}n{n} " + "é" * ((60 - len(f"L{level}n{n} ")) // 2) + "\n\n"
        for level in range(1, 7)
    )
    return heads + "".join(f"- w{n}x{i}\n" for i in range(ITEMS))


def a_heading_per_passage(n: int) -> str:
    return "".join(
        f"# H{n}x{i} " + "h" * (390 - len(f"H{n}x{i} ")) + f"\n\n- w{n}x{i}\n\n"
        for i in range(35)  # 19 each: 9 for the heading, 10 for its passage
    )


def short_headings_under_a_long_one(n: int) -> str:
    """The most short headings a note may have under a 390-byte one: eight more than its
    passages, which follow them."""
    heads = "".join(f"## s{n}x{i}\n\n" for i in range(30))
    return long_heading(n) + heads + "".join(f"- w{n}x{i}\n" for i in range(30 + 1 - 8))


def alternating_under_a_long_one(n: int) -> str:
    return long_heading(n) + "".join(f"## s{n}x{i}\n\n- w{n}x{i}\n\n" for i in range(35))


def short_headings_and_words(n: int) -> str:
    return "".join(f"## s{n}x{i}\n\n- w{n}x{i}\n\n" for i in range(300))


def bullets(n: int, items: int = 375) -> str:  # tests/acceptance/test_h6c_disk.py's
    return "".join(f"- w{n}x{i}\n" for i in range(items))


def prose(n: int, lines: int = 36) -> str:  # tests/acceptance/test_h6c_disk.py's
    return "".join(
        f"Entry {n}-{i}: the retry count for job {i} moved from {i % 7} to {i % 5} today.\n\n"
        for i in range(lines)
    )


SHAPES = {
    "one long heading": one_long_heading,
    "six nested headings": six_nested_headings,
    "six accented": six_accented_headings,
    "a heading per passage": a_heading_per_passage,
    "short headings under a long one": short_headings_under_a_long_one,
    "heading and passage under a long one": alternating_under_a_long_one,
    "short headings and one-word passages": short_headings_and_words,
    # tests/acceptance/test_h6c_disk.py's SHAPES
    "list items, all on one page": lambda n: "# Log\n\n" + bullets(n),
    "prose, all on one page": lambda n: "# Log\n\n" + prose(n),
    "list items, a page per note": lambda n: f"# Log {n}\n\n" + bullets(n),
    "prose, a page per note": lambda n: f"# Log {n}\n\n" + prose(n),
    "small dense notes, one page": lambda n: "# Log\n\n" + bullets(n, items=60),
}


def nine_letters(n: int) -> str:
    """The most headings a note of one passage may have (eight over its passages), over a
    passage of one letter: a note with no passage is refused (NoPassage)."""
    return f"# {n}\n" + "# a\n" * 8 + "a\n"


def one_heading(n: int) -> str:  # the smallest note there is: a heading over one letter
    return f"# {n}\n\na\n"


# Each shape: its notes, the notes it goes on with once the passages cap refuses one (None:
# it stops there), and the storage cap.
FILLS = {
    **{name: (make, None, CAP) for name, make in SHAPES.items()},
    "nine one-letter headings over a letter": (nine_letters, None, TINY_CAP),
    "dense items, then nine one-letter headings over a letter": (
        SHAPES["small dense notes, one page"],
        nine_letters,
        TINY_CAP,
    ),
    "one heading": (one_heading, None, TINY_CAP),
}


def size_of(db: Path) -> int:
    return sum(p.stat().st_size for p in db.parent.iterdir() if p.name.startswith(db.name))


def fill(tmp_path: Path, make, then, cap: int) -> tuple[int, int, list[int]]:
    """A workspace at a storage cap of `cap`, filled through REST until a cap refuses a note
    (`make`'s, then `then`'s once the passages cap refused one): the database's growth
    after compact(), the notes it holds, and each answer's status."""
    db = tmp_path / "cairn.db"
    store = Store(db)
    try:
        key = store.create_key("operator")
        project = store.create_project("ops")
        store.compact()
        empty = size_of(db)
        plans = Plans({"small": Plan("Small", storage_bytes=cap)}, default="small")
        app = create_app(store, public_url=BASE, plans=plans)
        statuses = []
        with TestClient(app, base_url=BASE) as client:
            for n in range(5_000):
                answer = client.post(
                    "/api/projects/ops/notes", headers={"x-api-key": key}, json={"text": make(n)}
                )
                statuses.append(answer.status_code)
                if answer.status_code != 402:
                    continue
                if then is not None and answer.json()["limit"] == "passages":
                    make, then = then, None
                    continue
                break
        store.compact()
        return size_of(db) - empty, len(store.list_notes(project.id)), statuses
    finally:
        store.close()


@pytest.mark.parametrize("make, then, cap", FILLS.values(), ids=FILLS.keys())
def test_a_workspace_takes_thirty_times_its_cap_and_a_bound_per_note(tmp_path, make, then, cap):
    assert max(len(h.encode()) for h in make(0).splitlines() if h.startswith("#")) <= 400
    grown, notes, statuses = fill(tmp_path, make, then, cap)
    assert statuses.count(201) >= 2 and statuses[-1] == 402, statuses  # filled to a cap
    bound = 30 * cap + PER_NOTE * notes
    assert grown <= bound, f"{grown:,} bytes: {30 * cap:,} + {PER_NOTE:,} x {notes} notes"


def test_many_short_headings_under_a_long_one_are_refused(tmp_path):
    """The shape that took 57.7 times its cap: short headings with nothing under them, each
    stored with the long path above it."""
    store = Store(tmp_path / "cairn.db")
    try:
        key = store.create_key("operator")
        store.create_project("ops")
        text = long_heading(0) + "".join(f"## s{i}\n\n" for i in range(2_000))
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            answer = client.post(
                "/api/projects/ops/notes", headers={"x-api-key": key}, json={"text": text}
            )
        assert answer.status_code == 400 and "headings" in answer.json()["error"]
        assert store.list_notes(store.get_project("ops").id) == []
    finally:
        store.close()


# What is counted is what is stored: a heading path is stored as JSON, which writes ordinary
# text as its UTF-8 bytes and escapes quotes, backslashes and control characters.


def stored_path_bytes(db: Path) -> int:
    con = sqlite3.connect(db)
    try:
        (most,) = con.execute(
            "SELECT MAX(LENGTH(CAST(heading_path AS BLOB))) FROM spans"
        ).fetchone()
    finally:
        con.close()
    return most


def test_a_heading_path_is_stored_as_its_utf8_bytes(tmp_path):
    store = Store(tmp_path / "cairn.db")
    try:
        project = store.create_project("ops")
        store.add_note(project.id, heading(400, "é") + "- one\n")  # 200 characters
        assert stored_path_bytes(tmp_path / "cairn.db") == 400 + len('[""]')
    finally:
        store.close()


@pytest.mark.parametrize("mark", ['"', "\\", "\x01"], ids=["quote", "backslash", "control"])
def test_characters_json_escapes_count_as_stored(store, mark):
    project = store.create_project("ops")
    escaped = len(json.dumps(mark, ensure_ascii=False)) - 2  # 2 for a quote, 6 for \u0001
    fits = HEADINGS_MAX // escaped
    store.add_note(project.id, "# " + mark * fits + "\n\n- one\n")
    with pytest.raises(HeadingsTooLong):
        store.add_note(project.id, "# " + mark * (fits + 1) + "\n\n- two\n")
