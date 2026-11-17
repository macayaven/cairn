"""`serve` files the notes that wait for their page (N1), in the background of the app it
runs (create_app's `file_every`; a plain `create_app` files nothing).

A note waits from the moment it is stored until its outcome is recorded (cairn.store,
`waiting_notes`): a server stopped between the two, or a note whose page kept moving under
it (cairn.memory.WAITING), leaves it so. The filer:

- At start, once the app runs (the lifespan's task is made when uvicorn runs the app, and
  every note is filed on a worker thread, never on the event loop, so /healthz and every
  request answer meanwhile), it reads the notes waiting then, once, and files each of them
  once. Then every `every` seconds (FILE_EVERY, 180, for `serve`) it looks again and files
  the notes that were waiting at the look before too: a note a request stored a moment ago
  is that request's to file, not the filer's.
- Workspaces take turns, PER_TURN notes each, so one workspace's backlog does not hold up
  another's; the loop yields between two notes. Each note takes one of the server's turns
  of compiles (G1; cairn.web.turns: `compiles`, the app's), waited for on the event loop
  like a request's note; a Filer given none takes no turn.
- Each note is filed as a request's note is (Memory.file_waiting): by its workspace's
  view, acting as the system, under the plan in force (Plans.in_force, Plans.limit) and
  the app's gate, and a page it saves is queued for the app's arranger. Its outcome is
  recorded as a request's is, and ends its wait: saved, unchanged, rejected, or rejected for
  a full page.
- A note whose page moved under it again, or whose filing raised for a reason nobody
  foresaw, keeps waiting and has one more try counted (Store.tried_waiting). One that raised
  is logged at WARNING with its id, its workspace's id and the error's type, never its text,
  the error's message or a traceback (they can quote the note), and is not tried again until
  the next start. A note with TRIES_MAX tries is not tried again: it stays listed by
  `python -m cairn.server waiting` for the operator, and each start logs it once.
- `stop()` (the app's shutdown) takes no more notes and waits for the one in hand to end,
  so nothing is written once `serve` returns: a note in hand is then filed or left waiting.
"""

import asyncio
import contextlib
import logging
import threading
from collections.abc import Iterator, Sequence
from itertools import islice
from typing import TYPE_CHECKING

from cairn.memory import WAITING
from cairn.store import WaitingNote

if TYPE_CHECKING:
    from cairn.web.auth import Accounts
    from cairn.web.turns import Compiles

FILING_LOGGER = "cairn.filing"
FILE_EVERY = 180.0  # seconds between two looks at the notes waiting, while the server runs
PER_TURN = 4  # notes of one workspace filed before the next workspace's turn
TRIES_MAX = 5  # tries with no outcome after which a note is left for the operator
log = logging.getLogger(FILING_LOGGER)


class Filer:
    def __init__(
        self, accounts: "Accounts", every: float, compiles: "Compiles | None" = None
    ) -> None:
        if not 0 < every < float("inf"):  # nan fails this too
            raise ValueError("file waiting notes every more than 0 seconds")
        self.every = every
        self._accounts = accounts
        self._compiles = compiles
        self._in_hand = threading.Lock()
        self._stopped = threading.Event()
        self._failed: set[str] = set()  # notes whose filing raised since this start

    async def loop(self) -> None:
        """The start's pass, then a look every `every` seconds, until cancelled. A look whose
        read raised is logged by the error's type and skipped; when it was the start's, the
        next look that reads is the start's in its place."""
        seen: set[str] | None = None  # None: no look has read yet; the next is the start's
        while True:
            notes = await asyncio.to_thread(self._waiting)
            if notes is not None:
                if seen is None:
                    self._left(notes)
                    await self._pass(notes)
                else:
                    await self._pass([note for note in notes if note.note_id in seen])
                seen = {note.note_id for note in notes}
            if self._stopped.is_set():
                return
            await asyncio.sleep(self.every)

    def _waiting(self) -> list[WaitingNote] | None:
        """The notes waiting now, or None when the read raised (logged by the type only)."""
        try:
            return self._accounts.waiting_notes()
        except Exception as error:
            log.warning("the notes waiting could not be read: %s", type(error).__name__)
            return None

    def _left(self, notes: Sequence[WaitingNote]) -> None:
        """Say once, at start, each note left waiting with every try spent."""
        for note in notes:
            if note.tries >= TRIES_MAX:
                log.warning(
                    "note %s of workspace %s is left waiting after %d tries",
                    note.note_id,
                    note.account_id,
                    note.tries,
                )

    async def _pass(self, notes: Sequence[WaitingNote]) -> None:
        for note in in_turns(notes):
            if self._stopped.is_set():
                return
            if note.tries >= TRIES_MAX or note.note_id in self._failed:
                continue
            turn = self._compiles.turn() if self._compiles else contextlib.nullcontext()
            async with turn:  # G1: one of the server's N, as any note
                await asyncio.to_thread(self.file_one, note)  # stopped meanwhile: it files none

    def file_one(self, note: WaitingNote) -> str | None:
        """File one waiting note, unless the filer was stopped (None). Its outcome: "saved",
        "unchanged", "rejected", "full", WAITING, "failed" (it raised), or None for a note
        no longer stored."""
        if self._stopped.is_set() or not self._in_hand.acquire(blocking=False):
            return None
        try:
            if self._stopped.is_set():  # stopped while this note waited for a thread
                return None
            try:
                outcome = self._accounts.file_waiting(note.account_id, note.note_id)
            except Exception as error:  # its type only: a message can quote the note
                self._failed.add(note.note_id)
                log.warning(
                    "note %s of workspace %s could not be filed: %s",
                    note.note_id,
                    note.account_id,
                    type(error).__name__,
                )
                self._tried(note)
                return "failed"
            if outcome == WAITING:  # its page moved again: one more try, counted
                self._tried(note)
            return outcome
        finally:
            self._in_hand.release()

    def _tried(self, note: WaitingNote) -> None:
        try:
            self._accounts.tried_waiting(note.account_id, note.note_id)
        except Exception as error:
            log.warning("a try of note %s was not counted: %s", note.note_id, type(error).__name__)

    def stop(self) -> None:
        """No note is taken from now on, and the one in hand ends before this returns."""
        self._stopped.set()
        with self._in_hand:
            pass


def in_turns(notes: Sequence[WaitingNote], per_turn: int = PER_TURN) -> Iterator[WaitingNote]:
    """The notes, workspaces taking turns (in the order each first appears), `per_turn` of
    one workspace's at a time, each workspace's in the order given."""
    grouped: dict[str, list[WaitingNote]] = {}
    for note in notes:
        grouped.setdefault(note.account_id, []).append(note)
    queues = {account_id: iter(group) for account_id, group in grouped.items()}
    while queues:
        for account_id in list(queues):
            turn = list(islice(queues[account_id], per_turn))
            if len(turn) < per_turn:
                del queues[account_id]
            yield from turn
