"""Model-use reservations (T4b): what a model call may cost, taken from the workspace's
budget before the call in one transaction, and replaced by what it spent after.

A reservation is booked into the month's `usage.model_micros` when it is made, so
`model_budget_left` and every reader of usage count it as spent until it is settled; a
reservation never settled (its process stopped, or the call raised something cairn could
not count) stays spent in full. Settling adds the call's tokens and the difference between
what was spent and what was reserved to the month the reservation was booked in, and drops
the reservation, in one transaction. Reserving reads the month's use and writes in one
BEGIN IMMEDIATE transaction, so callers in parallel, in one process or several on one file,
cannot take the same micros twice.
"""

import secrets
import sqlite3
from collections.abc import Callable
from typing import NamedTuple

from cairn.store.access import MODELS_PREFIX, KeptFromModels
from cairn.store.base import StoreBase, _require_account
from cairn.store.usage import Limits, _whole, period_of

RESERVATION_ID_PREFIX = "rs_"


class Reservation(NamedTuple):
    id: str
    period: str  # the UTC month it is booked in, "YYYY-MM"
    micros: int  # millionths of a US dollar, counted as spent until settled
    reserved_at: str  # ISO 8601 UTC


class ReservationMethods(StoreBase):
    _caps: Callable[[sqlite3.Connection], tuple[Limits, str | None]]  # UsageMethods'

    def reserve_model_use(self, micros: int, project_id: str | None = None) -> str | None:
        """Take `micros` from what is left of this month's model budget, for a call about to
        be made: the reservation's id, or None, writing nothing, when nothing is left or
        `micros` is more than what is left. Without a budget it is always granted. Counted
        as spent at once (`usage`, `model_budget_left`). ValueError: not a whole number of at
        least 0. KeyError: the view's workspace is gone. The budget is the view's caps for
        this write (UsageMethods._caps): on a view held to its plans (Plans.limit), the plan
        in force is read inside this transaction, so a downgrade made after the view was
        resolved, even to a budget of 0, is refused here. The month is the month of the
        write: a call that waits for the write lock across a month's end is charged to the
        new month.

        With `project_id`, the project whose page the call sends (M4): KeptFromModels, writing
        nothing, when that project is at "never", read in this transaction before the budget,
        so no call is admitted after a change to "never" has committed, with or without a
        budget. It costs one read of one row. A call admitted before the change commits may
        still be under way at the provider when it does."""
        amount = _whole(micros, "micros")
        with self._write() as con:
            period = period_of(self._instant())  # once the write lock is held
            _require_account(con, self._account_id)
            if (
                project_id is not None
                and con.execute(
                    "SELECT 1 FROM settings WHERE account_id = ? AND name = ? AND value = 'never'",
                    (self._account_id, MODELS_PREFIX + project_id),
                ).fetchone()
            ):
                raise KeptFromModels(project_id)
            budget = self._caps(con)[0].model_budget_micros
            if budget is not None:
                row = con.execute(
                    "SELECT model_micros FROM usage WHERE account_id = ? AND period = ?",
                    (self._account_id, period),
                ).fetchone()
                left = max(0, budget - (row[0] if row else 0))
                if left == 0 or amount > left:
                    return None
            reservation = RESERVATION_ID_PREFIX + secrets.token_hex(8)
            con.execute(
                "INSERT INTO usage (account_id, period, model_micros) VALUES (?, ?, ?)"
                " ON CONFLICT (account_id, period) DO UPDATE SET"
                " model_micros = model_micros + excluded.model_micros",
                (self._account_id, period, amount),
            )
            con.execute(
                "INSERT INTO model_reservations (id, account_id, period, micros, reserved_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (reservation, self._account_id, period, amount, self._stamp()),
            )
        return reservation

    def settle_model_use(
        self, reservation: str, tokens_in: int, tokens_out: int, micros: int
    ) -> None:
        """Replace the reservation with what the call spent: its tokens, and `micros` instead
        of the amount reserved, in the month it was booked in. Recorded whatever the budget:
        what was spent was spent. KeyError: no open reservation of this workspace by that id
        (settled already, or another's). ValueError: a count that is not a whole number of
        at least 0. Nothing is written on a refusal."""
        counts = (
            _whole(tokens_in, "tokens_in"),
            _whole(tokens_out, "tokens_out"),
            _whole(micros, "micros"),
        )
        with self._write() as con:
            row = con.execute(
                "SELECT period, micros FROM model_reservations WHERE id = ? AND account_id = ?",
                (reservation, self._account_id),
            ).fetchone()
            if row is None:
                raise KeyError(reservation)
            period, reserved = row
            con.execute(
                "UPDATE usage SET model_tokens_in = model_tokens_in + ?,"
                " model_tokens_out = model_tokens_out + ?,"
                " model_micros = model_micros + ? - ?"
                " WHERE account_id = ? AND period = ?",
                (*counts, reserved, self._account_id, period),
            )
            con.execute("DELETE FROM model_reservations WHERE id = ?", (reservation,))

    def model_reservations(self) -> list[Reservation]:
        """The workspace's reservations not yet settled, oldest first: calls in flight, and
        calls whose spending was never known, each counted in full. A read."""
        with self._read() as con:
            rows = con.execute(
                "SELECT id, period, micros, reserved_at FROM model_reservations"
                " WHERE account_id = ? ORDER BY reserved_at, id",
                (self._account_id,),
            ).fetchall()
        return [Reservation(*row) for row in rows]
