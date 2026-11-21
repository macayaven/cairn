"""Schema step 13: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

# When the subscription held began, as its provider said (which of two subscriptions was
# bought later), and when it became past due (the seven days count from there).
_V13_SUBSCRIPTIONS = """CREATE TABLE subscriptions (
    account_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,  -- the BillingProvider's name
    plan TEXT NOT NULL,  -- a name in the server's plans (cairn.plans)
    status TEXT NOT NULL CHECK (status IN ('active', 'past_due', 'canceled')),
    period_end TEXT,  -- the end of the paid period; NULL: the provider named none
    since TEXT NOT NULL,  -- when the provider made the newest message applied
    customer_ref TEXT,  -- the provider's own references, shown nowhere
    subscription_ref TEXT,
    started TEXT,  -- when the subscription began; NULL: the provider did not say
    past_due_since TEXT  -- when it became past due; NULL unless it is
) WITHOUT ROWID"""


def _add_subscription_times(con: sqlite3.Connection) -> None:
    """Schema 12 to _SUBSCRIPTION_TIMES: each subscription gains when it started and when
    it became past due, both unknown for the rows that exist (a past-due one keeps counting
    its seven days from its message, as before). Every row is kept."""
    columns = "account_id, provider, plan, status, period_end, since, customer_ref,"
    columns += " subscription_ref"
    con.execute("ALTER TABLE subscriptions RENAME TO subscriptions_v12")
    con.execute(_V13_SUBSCRIPTIONS)
    con.execute(f"INSERT INTO subscriptions ({columns}) SELECT {columns} FROM subscriptions_v12")
    con.execute("DROP TABLE subscriptions_v12")
