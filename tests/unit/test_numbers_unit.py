"""Q1 C in the store: the install's numbers, one part at a time (cairn.store.numbers). Each
part is one statement over every workspace, grouped by workspace, and reads no note's text,
no page's body, no name, label, address or reason; the operator's alone."""

from datetime import UTC, datetime, timedelta

import pytest

from cairn.store import Store
from cairn.store.numbers import PARTS, part_rows

START = datetime(2099, 9, 1, 9, 0, tzinfo=UTC)
PAD = "z" * 200_000  # in every text and body: a part that reads one is seen


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db", clock=Clock())
    yield s
    s.close()


def stocked(store: Store) -> str:
    north = store.create_project("north")
    for i in range(3):
        store.add_note(north.id, f"Note {i}.\n\n{PAD}")
    for _ in range(2):
        store.save_page(north.id, "a", "A", PAD, [])
    store.save_page(north.id, "b", "B", PAD, [])
    store.create_key("desk")
    other = store.create_account("Other")
    theirs = store.for_account(other.id)
    theirs.create_project("south")
    return other.id


def watched(store: Store, part: str):
    seen: list[str] = []
    size = {"bytes": 0}

    def rows(cursor, row):
        size["bytes"] += sum(len(v) if isinstance(v, str) else 8 for v in row)
        return row

    def traced(statement: str) -> None:
        word = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
        if not statement.lstrip().startswith("--") and word not in ("BEGIN", "COMMIT"):
            seen.append(statement)

    store._observe(trace=traced)
    store._observe(rows=rows)
    try:
        answer = store.numbers_part(part)
    finally:
        store._observe(trace=None)
        store._observe(rows=None)
    return answer, seen, size["bytes"]


@pytest.mark.parametrize("part", PARTS)
def test_each_part_is_one_statement_that_reads_no_text(store, part):
    stocked(store)
    _, seen, size = watched(store, part)
    assert len(seen) == 1 and size < 2_000, (part, size)


def test_the_parts_count_by_workspace(store):
    other = stocked(store)
    mine = store.account_id
    assert dict(store.numbers_part("accounts")) == {mine: None, other: None}
    assert dict(store.numbers_part("projects")) == {mine: 1, other: 1}
    assert dict(store.numbers_part("notes")) == {mine: 3}
    assert dict(store.numbers_part("pages")) == {mine: 2}
    assert dict(store.numbers_part("page_versions")) == {mine: 3}
    assert dict(store.numbers_part("waiting")) == {mine: 3}  # stored, never filed
    assert dict(store.numbers_part("keys")) == {mine: 1}
    assert dict(store.numbers_part("agents")) == {}
    newest = max(c.created_at for c in store.list_notes(store.get_project("north").id))
    assert dict(store.numbers_part("newest_note")) == {mine: newest}
    assert store.numbers_part("sources") == []


def test_an_unknown_part_is_a_value_error(store):
    with pytest.raises(ValueError, match="part"):
        store.numbers_part("texts")
    with pytest.raises(ValueError, match="part"):
        part_rows(store._con, "texts", "2099-01-01")


def test_the_numbers_are_the_operators(store):
    person = store.sign_in(
        "sso", "sub-n", "n@example.test", "Nia", issuer="https://id.example.test/n",
        join="personal", ttl_seconds=86_400,
    )  # fmt: skip
    hers = store.for_account(person.account_id, person.user.id)
    with pytest.raises(PermissionError):
        hers.numbers_part("projects")
    with pytest.raises(PermissionError):
        hers.database_file()
    assert store.database_file() == str(store._con.execute("PRAGMA database_list").fetchone()[2])
