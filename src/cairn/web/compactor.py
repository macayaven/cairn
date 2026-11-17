"""`serve --compact-every HOURS`: compact the database in the background when a workspace has
been deleted since the last compaction and the interval has passed (Store.compaction_state).

Compaction (Store.compact) rewrites the whole file and holds the store's lock while it runs,
so every request waits for it; it runs on a worker thread, off the event loop, never two at
once, and each run is one line on the access log with how long it took. A run or a
decision that fails is logged (the error's type, never its text) and the next poll tries
again: no failure stops the loop. One exception: a run that did not finish because another
connection held the file (beside a replica service one always does) is tried again once the
interval has passed since it began, as after one that finished, never at the next poll: each
try rewrites the whole file under the lock. What it remembers is this process's own, so a
restarted server tries once more at its first poll. 0 hours: never."""

import asyncio
import contextlib
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from cairn.memory import access_log
from cairn.store import CompactIncomplete, Store

POLL_SECONDS = 600.0  # how often the decision is taken, at most


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Compactor:
    def __init__(
        self, store: Store, hours: float, clock: Callable[[], datetime] = _utc_now
    ) -> None:
        if hours < 0:
            raise ValueError("compact every 0 hours (never) or more")
        self.every = timedelta(hours=hours)
        self._store = store
        self._clock = clock
        self._running = threading.Lock()
        self._tried: datetime | None = None  # when this process's last unfinished run began

    def due(self) -> bool:
        """A workspace was deleted since the last compaction, and the interval has passed
        since that one began (at once when there was none) and since this process's last
        run that did not finish began."""
        if not self.every:
            return False
        if self._tried is not None and self._clock() - self._tried < self.every:
            return False
        state = self._store.compaction_state()
        if state.deleted_at is None:
            return False
        if state.compacted_at is None:
            return True
        return self._clock() - datetime.fromisoformat(state.compacted_at) >= self.every

    def run_once(self) -> bool:
        """One compaction, unless one is running already. Returns whether it finished."""
        if not self._running.acquire(blocking=False):
            return False
        try:
            start = time.perf_counter()
            status = 200
            began = self._clock()
            try:
                self._store.compact()
                outcome = "done"
            except CompactIncomplete:
                outcome, status = "not finished: another connection held the file", 503
                self._tried = began  # the next try waits the interval, not the next poll
            except Exception as error:  # its type only: a message can quote the database
                outcome, status = f"failed: {type(error).__name__}", 500
            elapsed = (time.perf_counter() - start) * 1000
            with contextlib.suppress(Exception):  # a log handler that raises stops nothing
                access_log.info(
                    "compact %s in %.0f ms",
                    outcome,
                    elapsed,
                    extra={
                        "actor_kind": "system",
                        "actor": None,
                        "actor_id": None,
                        "action": "compact",
                        "project": None,
                        "status": status,
                    },
                )
            return status == 200
        finally:
            self._running.release()

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
