"""Unit tests for what the passages cap charges (H6c, finding 2): every row a note makes is paid
for, by the note's own bytes or by a unit of the passages cap. A span's heading path is stored
with it, so `passages()` counts one for each passage plus one for every full
BYTES_PER_PASSAGE (40) bytes of the heading path stored with a span, passages and heading
spans alike; and a note's heading spans may outnumber its passages by at most
HEADINGS_OVER_PASSAGES (8)."""

import io
import json
import zipfile

import pytest
from starlette.testclient import TestClient

import cairn.store.memory
from cairn.gate import lint_gate
from cairn.memory import LongHeadings, Memory
from cairn.server import create_app
from cairn.spans import chunk
from cairn.store import (
    BYTES_PER_PASSAGE,
    HEADINGS_OVER_PASSAGES,
    HeadingsTooLong,
    Limits,
    OverLimit,
    Store,
    TooManyHeadings,
    charge_of,
    path_bytes,
)

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    yield s
    s.close()


def heading(size: int, level: int = 1, mark: str = "h") -> str:
    """A heading line whose text is `size` UTF-8 bytes."""
    return "#" * level + " " + mark * (size // len(mark.encode())) + "\n\n"


def charged(text: str) -> int:
    return charge_of(chunk("cap_000000000000", text))


# What a span costs


def test_the_unit_is_forty_bytes_and_eight_headings_over_the_passages():
    assert (BYTES_PER_PASSAGE, HEADINGS_OVER_PASSAGES) == (40, 8)


@pytest.mark.parametrize(
    "text, cost",
    [
        (heading(390) + "- one\n", 9 + 10),  # the heading span 9, the passage 1 + 9
        ("# A\n\n" + "".join(f"- a{i}\n" for i in range(60)), 60),  # short: nothing changes
        (heading(39) + "- one\n", 1),  # under 40 bytes: nothing
        (heading(40) + "- one\n", 1 + 2),  # a full 40 bytes: one each
        (heading(30) + heading(30, 2) + "- x\n", 0 + 1 + 2),  # nested: 30, then 60 bytes
        (heading(80, mark="é") + "- x\n", 2 + 3),  # é is 2 bytes
        (heading(40, mark='"') + "- x\n", 2 + 3),  # 20 quotes, stored as 40 bytes
        ("Just prose.\n\nMore.\n", 2),  # no heading: as before
        (heading(390), 9),  # a heading alone still costs its path
    ],
    ids=[
        "390 B heading over a passage",
        "one-letter heading",
        "39 B",
        "40 B",
        "nested",
        "accented",
        "escaped",
        "no heading",
        "heading alone",
    ],
)
def test_a_note_costs_its_passages_and_every_full_forty_bytes_of_its_heading_paths(
    store, text, cost
):
    project = store.create_project("ops")
    assert charged(text) == cost
    store.add_note(project.id, text)
    assert store.passages() == cost


def test_path_bytes_is_what_the_heading_bound_counts():
    assert path_bytes(()) == 0
    assert path_bytes(("ab", "é")) == 4
    assert path_bytes(('"',)) == 2  # as stored: an escaped quote


def test_the_cap_refuses_on_the_charge_before_screening(store, monkeypatch):
    project = store.create_project("ops")
    limited = store.limited(Limits(storage_bytes=15 * BYTES_PER_PASSAGE))  # 15 units
    screened = []
    monkeypatch.setattr(Store, "_screen", lambda self, fields: screened.append(1) or [])
    with pytest.raises(OverLimit) as over:
        limited.add_note(project.id, heading(390) + "- one\n")  # 19
    assert (over.value.limit, over.value.used, over.value.cap) == ("passages", 0, 15)
    assert screened == [] and store.passages() == 0 and store.list_notes(project.id) == []
    limited.add_note(project.id, heading(200) + "- one\n")  # 5 + 6
    assert store.passages() == 11


def test_the_write_itself_refuses_on_the_charge(store, monkeypatch):
    """The look before screening is a look: the write's own check counts the same."""
    project = store.create_project("ops")
    limited = store.limited(Limits(storage_bytes=15 * BYTES_PER_PASSAGE))
    monkeypatch.setattr(Store, "_look_before_screening", lambda self, *args: None)
    with pytest.raises(OverLimit) as over:
        limited.add_note(project.id, heading(390) + "- one\n")
    assert over.value.limit == "passages" and store.list_notes(project.id) == []


def test_forgetting_a_note_gives_its_charge_back(store):
    project = store.create_project("ops")
    note, _ = store.add_note(project.id, heading(390) + "- one\n- two\n")
    assert store.passages() == 9 + 10 + 10
    store.forget_note(note.id)
    assert store.passages() == 0


def test_an_import_is_charged_as_the_notes_were(store, tmp_path):
    source = Store(tmp_path / "source.db")
    try:
        project = source.create_project("ops")
        source.add_note(project.id, heading(390) + "- one\n")
        archive = io.BytesIO()
        source.export(archive)
    finally:
        source.close()
    target = store.for_account(store.create_account("Brook").id)
    with pytest.raises(OverLimit) as over:
        target.limited(Limits(storage_bytes=18 * BYTES_PER_PASSAGE)).import_export(
            io.BytesIO(archive.getvalue())
        )
    assert (over.value.limit, over.value.cap) == ("passages", 18)
    target.limited(Limits(storage_bytes=19 * BYTES_PER_PASSAGE)).import_export(archive)
    assert target.passages() == 19


# A note may not be mostly headings


def outline(headings: int, passages: int) -> str:
    return "".join(f"## Part {i}\n\n" for i in range(headings)) + "".join(
        f"- item {i}\n" for i in range(passages)
    )


@pytest.mark.parametrize("headings, passages", [(8, 0), (9, 1), (20, 12), (1, 0), (3, 40)], ids=str)
def test_headings_at_most_eight_over_the_passages_go_in(store, headings, passages):
    project = store.create_project("ops")
    note, created = store.add_note(project.id, outline(headings, passages))
    assert created and note.id


@pytest.mark.parametrize("headings, passages", [(9, 0), (10, 1), (21, 12)], ids=str)
def test_more_headings_are_refused_before_anything(store, monkeypatch, headings, passages):
    project = store.create_project("ops")
    screened = []
    monkeypatch.setattr(Store, "_screen", lambda self, fields: screened.append(1) or [])
    text = outline(headings, passages)
    with pytest.raises(TooManyHeadings) as many:
        store.add_note(project.id, text)
    refused = many.value
    assert isinstance(refused, HeadingsTooLong) and isinstance(refused, ValueError)
    assert refused.field == "headings" and (refused.length, refused.bound) == (
        headings,
        passages + HEADINGS_OVER_PASSAGES,
    )
    assert "Part" not in str(refused) and "item" not in str(refused)  # quotes nothing
    assert str(HEADINGS_OVER_PASSAGES) in str(refused)
    assert screened == [] and store.list_notes(project.id) == [] and store.passages() == 0


def test_memory_rest_and_the_composer_refuse_it_like_long_headings(tmp_path):
    store = Store(tmp_path / "web.db")
    try:
        key = store.create_key("laptop")
        store.create_project("ops")
        with pytest.raises(LongHeadings) as long:
            Memory(store, lint_gate).note("ops", outline(9, 0))
        assert long.value.status == 400 and "Part" not in str(long.value)
        app = create_app(store, public_url=BASE)
        with TestClient(app, base_url=BASE) as client:
            answer = client.post(
                "/api/projects/ops/notes", headers={"x-api-key": key}, json={"text": outline(9, 0)}
            )
            assert answer.status_code == 400 and "headings" in answer.json()["error"]
            assert "Part" not in answer.text
            client.post("/login", data={"key": key}, headers={"sec-fetch-site": "same-origin"})
            page = client.post(
                "/b/ops/notes",
                data={"text": outline(9, 0)},
                headers={"sec-fetch-site": "same-origin"},
            )
            assert page.status_code == 400 and "headings" in page.text
    finally:
        store.close()


def test_an_import_with_a_mostly_headings_note_imports_nothing(store, tmp_path, monkeypatch):
    source = Store(tmp_path / "source.db")
    try:
        project = source.create_project("ops")
        monkeypatch.setattr(cairn.store.memory, "HEADINGS_OVER_PASSAGES", 100)
        source.add_note(project.id, outline(9, 0))  # as an older build could
        monkeypatch.undo()
        archive = io.BytesIO()
        source.export(archive)
    finally:
        source.close()
    assert (
        "Part 0"
        in zipfile.ZipFile(archive)
        .read(next(n for n in zipfile.ZipFile(archive).namelist() if n.endswith(".md")))
        .decode()
    )
    with pytest.raises(ValueError, match=r"note 1 of projects/ops/notes\.jsonl") as refused:
        store.import_export(io.BytesIO(archive.getvalue()))
    assert "headings" in str(refused.value) and "Part" not in str(refused.value)
    assert store.list_projects() == []


def test_the_charge_equals_the_counter_for_every_shape(store):
    """What the Python side charges a note before writing is what the schema's triggers
    count once it is written, for headings long, nested, accented and escaped."""
    project = store.create_project("ops")
    texts = [
        heading(390) + "- one\n",
        heading(60) + heading(60, 2) + heading(60, 3) + "Para.\n\n- a\n- b\n",
        heading(200, mark="é") + "> quoted\n",
        heading(120, mark='"') + "```\ncode\n```\n",
        heading(80, mark="\\") + "| a |\n|---|\n| 1 |\n",
        "---\ntitle: T\n---\n" + heading(50) + "- fm\n",
        heading(5) + heading(390, 2) + heading(3, 3),
    ]
    total = 0
    for text in texts:
        store.add_note(project.id, text)
        total += charged(text)
        assert store.passages() == total, text[:30]
    assert json.dumps(total)  # some charge at all
    assert total > sum(
        span.kind in {"paragraph", "bullet", "code", "table", "quote"}
        for text in texts
        for span in chunk("cap_000000000000", text)
    )
