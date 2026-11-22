"""A threaded soak of the store's readers (review, architecture 1, point 9): several threads
read while one writes a few hundred notes. The threads race on purpose; what the test
asserts does not depend on how they interleave: no read fails, every read sees a whole
write or none of it, each reader's counts never go back, and at the end every count holds.
Fixtures are synthetic."""

import threading
from datetime import UTC, datetime

from cairn.store import Store
from cairn.store.base import READERS

NOTES = 300
READING = READERS + 2  # more threads than readers: some wait for one
SAFETY = 120.0


def fixed() -> datetime:
    return datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def test_reads_beside_a_writer_see_whole_writes_and_end_consistent(tmp_path):
    store = Store(tmp_path / "cairn.db", clock=fixed)
    try:
        project = store.create_project("fable-works")
        view = store.for_account("acct_default")
        start = threading.Barrier(READING + 1)
        written = threading.Event()
        errors: list[BaseException] = []
        seen: list[list[int]] = [[] for _ in range(READING)]
        lent: dict[int, int] = {}
        overlap = [False]
        mark = threading.Lock()

        def write() -> None:
            try:
                start.wait(SAFETY)
                for n in range(NOTES):
                    store.add_note(project.id, f"- Row {n} holds pears for Ada.\n")
            except BaseException as error:
                errors.append(error)
            finally:
                written.set()

        def read(k: int) -> None:
            reader = view if k % 2 else store
            try:
                start.wait(SAFETY)
                while True:
                    last = written.is_set()  # one more pass after the writer is done
                    with reader._read() as con:
                        with mark:
                            lent[id(con)] = lent.get(id(con), 0) + 1
                            overlap[0] |= lent[id(con)] > 1
                        notes = con.execute(
                            "SELECT count(*) FROM notes WHERE project_id = ?", (project.id,)
                        ).fetchone()[0]
                        spanned = con.execute(
                            "SELECT count(DISTINCT note_id) FROM spans WHERE note_id IN"
                            " (SELECT id FROM notes WHERE project_id = ?)",
                            (project.id,),
                        ).fetchone()[0]
                        with mark:
                            lent[id(con)] -= 1
                    assert notes == spanned, "a note without its passages: a torn write"
                    seen[k].append(notes)
                    assert reader.count_notes(project.id) >= notes  # never goes back
                    if last:
                        return
            except BaseException as error:
                errors.append(error)

        threads = [threading.Thread(target=write)] + [
            threading.Thread(target=read, args=(k,)) for k in range(READING)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(SAFETY)
        assert not any(thread.is_alive() for thread in threads)
        assert errors == []
        assert not overlap[0]  # no reader was lent to two reads at once
        for counts in seen:
            assert counts == sorted(counts)  # each thread's counts never go back
            assert counts[-1] == NOTES  # its last read began after the last write returned
        assert store.count_notes(project.id) == NOTES
        assert len(store.list_notes(project.id)) == NOTES
        assert len(store._state.readers.connections()) <= READERS
        assert not any(con.in_transaction for con in store._state.readers.connections())
    finally:
        store.close()
