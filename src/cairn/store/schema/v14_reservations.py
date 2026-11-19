"""Schema step 14: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

# Model-use reservations (T4b; store/reservations.py). What a model call may cost, booked into
# its month's usage.model_micros before the call and replaced by what it spent after; a row
# is a reservation not settled yet, counted as spent in full.
_V14_MODEL_RESERVATIONS = """CREATE TABLE model_reservations (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    period TEXT NOT NULL,  -- the UTC month it is booked in, "YYYY-MM"
    micros INTEGER NOT NULL,  -- millionths of a US dollar
    reserved_at TEXT NOT NULL
) WITHOUT ROWID"""
_V14_MODEL_RESERVATIONS_BY_ACCOUNT = (
    "CREATE INDEX model_reservations_by_account ON model_reservations (account_id)"
)
_V14_RESERVATIONS = (_V14_MODEL_RESERVATIONS, _V14_MODEL_RESERVATIONS_BY_ACCOUNT)


def _add_reservations(con: sqlite3.Connection) -> None:
    """Schema 13 to 14: model-use reservations, empty. Nothing that existed changes."""
    for statement in _V14_RESERVATIONS:
        con.execute(statement)
