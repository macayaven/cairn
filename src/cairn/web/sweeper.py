"""`serve --sweep-every SECONDS`: remove what has run out (Store.sweep_expired) in the
background, every that many seconds (600 by default; 0: never).

The first sweep runs when the app starts, then one every interval. Each runs on a worker
thread, off the event loop, never two at once. A sweep that removed rows is one line on the
access log with how many, and nothing else: never a digest, an address or an id; one that
removed nothing writes no line. A sweep that fails is logged by the error's type, never its
text, and the next one runs on time: no failure stops the loop. Nothing is audited."""

import asyncio
import contextlib
import math
import threading

from cairn.memory import access_log
from cairn.store import Store


class Sweeper:
    def __init__(self, store: Store, every: float) -> None:
        if not 0 < every < math.inf:  # nan fails this too
            raise ValueError("sweep every more than 0 seconds")
        self.every = every
        self._store = store
        self._running = threading.Lock()
        self._stopped = threading.Event()

    def run_once(self) -> int | None:
        """One sweep, unless one is running already or the sweeper was stopped (None).
        Returns how many rows it removed, or None when it failed."""
        if self._stopped.is_set() or not self._running.acquire(blocking=False):
            return None
        try:
            if self._stopped.is_set():  # stopped while this run waited for a thread
                return None
            try:
                removed = self._store.sweep_expired()
            except Exception as error:  # its type only: a message can quote the database
                _log("sweep failed: %s", type(error).__name__, status=500)
                return None
            if removed:
                _log("sweep removed %d expired rows", removed, status=200)
            return removed
        finally:
            self._running.release()

    async def loop(self) -> None:
        """Sweep on a worker thread now and then every `every` seconds. Cancelled with the
        app's lifespan."""
        while True:
            await asyncio.to_thread(self.run_once)
            await asyncio.sleep(self.every)

    def stop(self) -> None:
        """No sweep starts from now on, and one running ends before this returns: the store
        must not close under it."""
        self._stopped.set()
        with self._running:
            pass


def _log(message: str, value: object, *, status: int) -> None:
    with contextlib.suppress(Exception):  # a log handler that raises stops nothing
        access_log.info(
            message,
            value,
            extra={
                "actor_kind": "system",
                "actor": None,
                "actor_id": None,
                "action": "sweep",
                "project": None,
                "status": status,
            },
        )
