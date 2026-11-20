"""`serve --compiles N`: a server process compiles at most N notes at a time (G1).

A note's compile holds the processor and memory for as long as it takes, so eight large notes
at once each hold their own. `Compiles` gives out N turns; a note that finds them all taken
waits on the event loop, in arrival order, and goes on when one ends. It is taken by every way
a note reaches a compile in the app: a REST note, the MCP `note` tool, the composer's form, a
source's run, the filer (cairn.web.filing) and a note written from Slack
(cairn.slack.people.written), each for one note at a time and in one place
only, so a turn is never asked for twice by one note.

A waiting note holds no thread, no connection to the store and no lock of the store: it is
taken before the note's work is handed to a worker thread, and given back when that work
ends. So reads, /healthz, sign-in and everything else that is not a note's compile are never
behind it. The note is stored and compiled inside its turn, as one piece of work, and is
answered when it ends, as before.

One `Compiles` may be used from several event loops (a test client makes its own): each
waiter is woken on the loop it waits on.
"""

import asyncio
import contextlib
import threading
from collections import deque
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Any

from starlette.requests import HTTPConnection

COMPILES = 2  # notes a server process compiles at once, unless `serve --compiles N` says


def check_compiles(n: object) -> int:
    """`n` when it is a whole number of at least 1 (a bool is not one); ValueError."""
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError("--compiles is a whole number, 1 or more")
    return n


class Compiles:
    """At most `n` turns held at once; the others wait, in the order they asked."""

    def __init__(self, n: int = COMPILES) -> None:
        self.n = check_compiles(n)
        self._lock = threading.Lock()
        self._held = 0
        self._waiting: deque[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]] = deque()

    @property
    def held(self) -> int:
        """Turns held now."""
        return self._held

    @property
    def waiting(self) -> int:
        """Notes waiting for a turn now."""
        return len(self._waiting)

    @contextlib.asynccontextmanager
    async def turn(self) -> AsyncIterator[None]:
        """Hold one turn for the body of the `async with`."""
        await self._take()
        try:
            yield
        finally:
            self._give_back()

    async def _take(self) -> None:
        with self._lock:
            if self._held < self.n and not self._waiting:
                self._held += 1
                return
            loop = asyncio.get_running_loop()
            waiter: asyncio.Future[None] = loop.create_future()
            entry = (loop, waiter)
            self._waiting.append(entry)
        try:
            await waiter
        except asyncio.CancelledError:
            with self._lock:
                queued = entry in self._waiting
                if queued:
                    self._waiting.remove(entry)
            # Handed a turn and cancelled before using it: give it on. A turn handed to a
            # waiter already cancelled is given on where it is handed (_hand).
            if not queued and waiter.done() and not waiter.cancelled():
                self._give_back()
            raise

    def _give_back(self) -> None:
        """End one turn: hand it to the first waiter, else one fewer is held."""
        with self._lock:
            if not self._waiting:
                self._held -= 1
                return
            loop, waiter = self._waiting.popleft()  # the turn passes on: `_held` stays
        try:
            loop.call_soon_threadsafe(self._hand, waiter)
        except RuntimeError:  # that waiter's loop is closed: nobody will take the turn
            self._give_back()

    def _hand(self, waiter: asyncio.Future[None]) -> None:
        """On the waiter's own loop: wake it with the turn, or give the turn on when the
        waiter was cancelled meanwhile."""
        if waiter.done():
            self._give_back()
        else:
            waiter.set_result(None)


SCOPE_KEY = "cairn.compiles"  # where Auth puts the server's Compiles (cairn.web.auth.COMPILES)


def compile_turn(request: HTTPConnection) -> AbstractAsyncContextManager[Any]:
    """The turn a request's note takes: of the Compiles Auth put on the request, or none for
    a request no Auth admitted (nothing reaches a compile without one)."""
    compiles = request.scope.get(SCOPE_KEY)
    return compiles.turn() if isinstance(compiles, Compiles) else contextlib.nullcontext()


# One read of related pages at a time in a process (Store.related_pages), taken the same way:
# over REST and MCP a read waits for it on the event loop, holding no thread and no
# connection, and its worker thread then takes the store's own turn with nobody ahead of it.
# A read that waits long waits; none is refused for it.
RELATED_READS = Compiles(1)
