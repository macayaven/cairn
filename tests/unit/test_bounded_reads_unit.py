"""Q1 A in the store: the reads whose cost does not grow with what they count or list
(cairn.store.memory): pages per project, notes per project, a part of a project's pages, a part of a
page's version numbers, notes by id, and how far many projects are shared. Each is one read;
none hands a page's body or a note's text back."""

from datetime import UTC, datetime, timedelta

import pytest

from cairn.store import Claim, Store

START = datetime(2099, 4, 1, 9, 0, tzinfo=UTC)
PAD = "y" * 200_000  # in every body and text: a read that hands it back is seen


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


class Watch:
    """The statements the store's connection runs, and the bytes its rows hand back."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.statements: list[str] = []
        self.bytes = 0

    def __enter__(self):
        def rows(cursor, row):
            self.bytes += sum(len(v) if isinstance(v, str) else 8 for v in row)
            return row

        self.store._observe(trace=self._traced)
        self.store._observe(rows=rows)
        return self

    def _traced(self, statement: str) -> None:
        word = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
        if not statement.lstrip().startswith("--") and word not in ("BEGIN", "COMMIT"):
            self.statements.append(statement)

    def __exit__(self, *exc):
        self.store._observe(trace=None)
        self.store._observe(rows=None)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db", clock=Clock())
    yield s
    s.close()


def save(store: Store, project_id: str, slug: str, times: int = 1, claims: int = 1) -> None:
    listed = [Claim(id=f"cl_{slug}{n}", text=f"{slug} {n}.", span_ids=()) for n in range(claims)]
    for _ in range(times):
        store.save_page(project_id, slug, slug.title(), PAD, listed)


def test_page_counts_count_each_projects_pages_in_one_statement_without_bodies(store):
    north, south, _ = (store.create_project(name) for name in ("north", "south", "empty"))
    for slug in ("a", "b", "c"):
        save(store, north.id, slug, times=2)
    save(store, south.id, "d")
    with Watch(store) as seen:
        counts = store.page_counts()
    assert counts == {north.id: 3, south.id: 1}  # a project with no page is left out
    assert len(seen.statements) == 1 and seen.bytes < 1_000


def test_page_counts_and_notes_follow_the_reach(store):
    north, south = store.create_project("north"), store.create_project("south")
    save(store, north.id, "a")
    save(store, south.id, "b")
    store.add_note(south.id, f"South note.\n\n{PAD}")
    narrowed = store.narrow([north.id], "read")
    assert narrowed.page_counts() == {north.id: 1}
    assert narrowed.count_notes(south.id) == 0
    assert store.count_notes(south.id) == 1


def test_count_notes_reads_no_note_text(store):
    project = store.create_project("north")
    for i in range(3):
        store.add_note(project.id, f"Note {i}.\n\n{PAD}")
    with Watch(store) as seen:
        assert store.count_notes(project.id) == 3
    assert len(seen.statements) == 1 and seen.bytes < 1_000


def test_page_heads_list_a_part_by_slug_with_the_total_and_no_bodies(store):
    project = store.create_project("north")
    for slug in ("e", "a", "d", "b", "c"):
        save(store, project.id, slug, times=2, claims=3)
    with Watch(store) as seen:
        heads, total = store.page_heads(project.id, 2)
    assert total == 5 and [h.slug for h in heads] == ["a", "b"]
    assert len(seen.statements) == 2 and seen.bytes < 2_000
    first = heads[0]
    latest = store.get_page(project.id, "a")
    assert (first.title, first.version, first.claims, first.updated_at) == (
        "A", 2, 3, latest.updated_at,
    )  # fmt: skip
    after, _ = store.page_heads(project.id, 10, after="b")
    assert [h.slug for h in after] == ["c", "d", "e"]
    assert store.page_heads(project.id, 10, after="e") == ([], 5)


def test_version_numbers_come_from_the_end_with_the_total_and_the_latest(store):
    project = store.create_project("north")
    save(store, project.id, "a", times=7)
    with Watch(store) as seen:
        part = store.version_numbers(project.id, "a", 3)
    assert part == ([7, 6, 5], 7, 7)  # newest first, how many, the latest
    assert len(seen.statements) == 2 and seen.bytes < 1_000
    assert store.version_numbers(project.id, "a", 3, before=5) == ([4, 3, 2], 7, 7)
    assert store.version_numbers(project.id, "a", 3, before=2) == ([1], 7, 7)
    assert store.version_numbers(project.id, "none", 3) == ([], 0, None)


def test_get_notes_reads_every_note_asked_in_one_statement(store):
    north, south = store.create_project("north"), store.create_project("south")
    notes = [store.add_note(north.id, f"North {i}.")[0] for i in range(4)]
    theirs = store.add_note(south.id, "South.")[0]
    asked = [notes[2].id, "cap_000000000000", notes[0].id, notes[2].id, theirs.id]
    with Watch(store) as seen:
        found = store.get_notes(asked)
    assert list(found) == [notes[2].id, notes[0].id, theirs.id]  # unknown ids left out
    assert found[notes[0].id] == store.get_note(notes[0].id)
    assert len(seen.statements) == 1
    assert list(store.narrow([north.id], "read").get_notes(asked)) == [notes[2].id, notes[0].id]


def test_projects_sharing_answers_as_project_sharing_for_each_project_in_two_statements(store):
    owner = store.sign_in(
        "sso", "sub-o", "o@example.test", "Ola", issuer="https://id.example.test/q",
        join="personal", ttl_seconds=86_400,
    )  # fmt: skip
    hers = store.for_account(owner.account_id, owner.user.id)
    shared = hers.create_project("open")
    private = hers.create_project("closed", everyone="none")
    read = hers.create_project("seen", everyone="read")
    ids = [shared.id, private.id, read.id, "br_000000000000"]
    with Watch(store) as seen:
        many = hers.projects_sharing(ids)
    statements = len(seen.statements)
    assert many == {i: hers.project_sharing(i) for i in ids[:3]}  # an unknown project: left out
    assert statements <= 4  # the person's reach (2), then the projects and their people
