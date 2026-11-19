"""Schema step 27: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

# Open checkouts (R3). At most one row per workspace: the checkout the
# payment provider is asked for or gave, while it is open. Its idempotency key is made with
# the row, before the provider is asked, and sent with every ask for it, so the provider
# answers a repeated ask with the same checkout. A row goes when it ends (a later write
# removes it), when a provider's message is applied for its workspace, when the provider
# answers the ask with a final failure, and with its workspace. Never exported.
_V27_CHECKOUTS = """CREATE TABLE checkouts (
    account_id TEXT PRIMARY KEY,  -- one open checkout per workspace, whatever the plan
    plan TEXT NOT NULL,  -- the plan it buys
    idempotency_key TEXT NOT NULL,  -- sent to the provider with each ask for it
    url TEXT,  -- the address the provider gave; NULL until it answered
    opened_at TEXT NOT NULL,  -- when it was opened, by the web service's clock
    ends_at TEXT NOT NULL  -- when it stops counting as open
) WITHOUT ROWID"""


def _add_checkouts(con: sqlite3.Connection) -> None:
    """Schema _PAGE_HEADS to _CHECKOUTS (R3): the checkouts table, empty. A checkout open
    in a server's memory when it stopped is not carried over. Nothing that existed
    changes."""
    con.execute(_V27_CHECKOUTS)
