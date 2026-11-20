"""`serve --compact-every HOURS`: compact the database in the background when a workspace has
been deleted since the last compaction and the interval has passed (Store.compaction_state).

A compaction rewrites the whole file (Store.compact), then empties the write-ahead log;
the two are recorded apart. When the rewrite succeeded and another connection held the log
(beside a replica service one always does), what stays due is emptying the log alone
(Store.empty_log), which costs no rewrite: a rewrite is not repeated for the same deletion
once it is recorded, by the next interval or a restarted server (one killed before its
record runs again). A deletion that comes after starts a new
rewrite. Before a rewrite the store checks for room (twice the database free on its disk);
without it nothing starts, one line says both numbers, and it stays due.

What a write waits for: during a rewrite, the whole of it under the store's lock (the time
to write the file again, which grows with the database), then the attempt to empty the log,
at most about 5 seconds (SQLite's busy wait) plus the copy of the log; during an attempt to
empty the log alone, that last part only. Reads do not wait (they run on the store's
read-only connections).

It runs on a worker thread, off the event loop, never two at once, and each run is one line
on the access log with how long it took. A run or a decision that fails is logged (the
error's type, never its text) and the next poll tries again: no failure stops the loop. A
run that did not finish because another connection held the file is tried again once the
interval has passed since it began, as after one that finished, never at the next poll;
what it remembers is this process's own, so a restarted server tries once more at its first
poll (emptying the log alone, when that is what is due). 0 hours: never.

Clocks: every interval this process measures (since its last run began; the poll) is on a
monotonic clock (`ticks`, time.monotonic), so a wall clock that steps neither suppresses nor
forces a run. The times the database holds are wall-clock times (`clock`, UTC): when a
deletion was recorded and when the last rewrite began (store.compact_due,
store.compacted_at). The second is read on the wall clock once, at the first decision that
sees it, and placed on the monotonic clock from then on."""

import asyncio
import contextlib
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from cairn.memory import access_log
from cairn.store import CompactIncomplete, NoRoom, Store

POLL_SECONDS = 600.0  # how often the decision is taken, at most
ONLY_LOG = "no rewrite: only the write-ahead log was due"
# Who each line of the access log names: the server itself, compacting.
SYSTEM = {"actor_kind": "system", "actor": None, "actor_id": None, "action": "compact",
          "project": None}  # fmt: skip


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Compactor:
    def __init__(
        self,
        store: Store,
        hours: float,
        clock: Callable[[], datetime] = _utc_now,
        ticks: Callable[[], float] = time.monotonic,
    ) -> None:
        if hours < 0:
            raise ValueError("compact every 0 hours (never) or more")
        self.every = timedelta(hours=hours)
        self._store = store
        self._clock = clock  # the wall clock: only to place the stored time of a rewrite
        self._ticks = ticks  # the monotonic clock every interval is measured on
        self._running = threading.Lock()
        self._began: float | None = None  # on `ticks`: this process's last run that ran
        self._placed: tuple[str | None, float] | None = None  # a stored compacted_at, on `ticks`

    def due(self) -> bool:
        """A workspace was deleted since the last compaction and the interval has passed
        since this process's last run began (finished or not), on the monotonic clock. Then:
        at once when a rewrite already covered every deletion (only the log is left) or when
        none ever ran; otherwise once the interval has passed since the last rewrite began.
        That time is stored, on the wall clock: the first decision that sees it places it on
        the monotonic clock (its age on the wall clock, from 0 for a time later than now up
        to the interval), and every later decision measures from there, so a wall clock that
        steps afterwards changes nothing. A rewrite of this process's own is placed where its
        run began, right after it; one by another process (the command line's `compact`) is
        a time not seen before, placed by its age."""
        if not self.every:
            return False
        now, every = self._ticks(), self.every.total_seconds()
        if self._began is not None and now - self._began < every:
            return False
        state = self._store.compaction_state()
        if state.deleted_at is None:
            return False
        if state.rewritten:  # only the log is left, which costs no rewrite
            return True
        if state.compacted_at is None:
            return True
        if self._placed is None or self._placed[0] != state.compacted_at:
            age = (self._clock() - datetime.fromisoformat(state.compacted_at)).total_seconds()
            self._placed = (state.compacted_at, now - min(max(age, 0.0), every))
        return now - self._placed[1] >= every

    def run_once(self) -> bool:
        """One compaction, unless one is running already. Returns whether it finished."""
        if not self._running.acquire(blocking=False):
            return False
        try:
            start = time.perf_counter()
            status = 200
            began = self._ticks()
            only_log = False
            try:
                only_log = self._store.compaction_state().rewritten
                if only_log:
                    self._store.empty_log()
                    outcome = f"done ({ONLY_LOG})"
                else:
                    self._store.compact()
                    self._place(began)
                    outcome = "done"
                self._began = began
            except NoRoom as room:  # nothing was changed; the next poll checks again
                with contextlib.suppress(Exception):
                    access_log.info(
                        "compact not started: the database's disk has %d bytes free,"
                        " a rewrite needs %d",
                        room.free,
                        room.needed,
                        extra={**SYSTEM, "status": 507},
                    )
                return False
            except CompactIncomplete as error:
                outcome, status = "not finished: another connection held the file", 503
                if only_log:
                    outcome += f" ({ONLY_LOG})"
                elif error.rewrote:
                    outcome += " (rewritten; the write-ahead log was not emptied)"
                    self._place(began)
                self._began = began  # the next try waits the interval, not the next poll
            except Exception as error:  # its type only: a message can quote the database
                outcome, status = f"failed: {type(error).__name__}", 500
            elapsed = (time.perf_counter() - start) * 1000
            with contextlib.suppress(Exception):  # a log handler that raises stops nothing
                access_log.info(
                    "compact %s in %.0f ms",
                    outcome,
                    elapsed,
                    extra={**SYSTEM, "status": status},
                )
            return status == 200
        finally:
            self._running.release()

    def _place(self, began: float) -> None:
        """The time the rewrite just made stored (compacted_at), placed where its run began
        on the monotonic clock; left to `due` to place by its age if it cannot be read."""
        with contextlib.suppress(Exception):
            self._placed = (self._store.compaction_state().compacted_at, began)

    async def loop(self) -> None:
        """Take the decision every POLL_SECONDS (or the interval, when shorter), and compact
        on a worker thread when one is due. Cancelled with the app's lifespan."""
        poll = min(POLL_SECONDS, self.every.total_seconds())
        while True:
            await asyncio.sleep(poll)
            try:
                due = await asyncio.to_thread(self.due)
            except Exception as error:  # the next poll decides again
                with contextlib.suppress(Exception):
                    access_log.warning("compact not decided: %s", type(error).__name__)
                continue
            if due:
                await asyncio.to_thread(self.run_once)

    def wait(self) -> None:
        """Until a running compaction ends: the store must not close under it."""
        with self._running:
            pass
