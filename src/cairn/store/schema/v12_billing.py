"""Schema step 12: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

# The tables billing kept (subscriptions, billing_messages and, above, checkouts): nothing
# read or wrote them once cairn sold no plan, and _OLDEST_BUILD drops them. Their statements
# stay, for the steps that made them (history: an older database still runs those steps).
# Billing (H7). The subscription a workspace holds, as its payment provider last said, with
# the provider's references for the customer and the subscription (the portal needs the
# customer's; nothing about a payment method is kept). And every provider message applied
# or refused as out of order, by (provider, id), so a message counts once; each names its
# workspace, so deleting the workspace deletes them. Seen messages are never pruned.
_V12_SUBSCRIPTIONS = """CREATE TABLE subscriptions (
    account_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,  -- the BillingProvider's name
    plan TEXT NOT NULL,  -- a name in the server's plans (cairn.plans)
    status TEXT NOT NULL CHECK (status IN ('active', 'past_due', 'canceled')),
    period_end TEXT,  -- the end of the paid period; NULL: the provider named none
    since TEXT NOT NULL,  -- when the provider made the newest message applied
    customer_ref TEXT,  -- the provider's own references, shown nowhere
    subscription_ref TEXT
) WITHOUT ROWID"""
_V12_BILLING_MESSAGES = """CREATE TABLE billing_messages (
    provider TEXT NOT NULL,
    id TEXT NOT NULL,  -- the provider's message id
    account_id TEXT NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY (provider, id)
) WITHOUT ROWID"""
_V12_BILLING_MESSAGES_BY_ACCOUNT = (
    "CREATE INDEX billing_messages_by_account ON billing_messages (account_id)"
)
_V12_BILLING = (_V12_SUBSCRIPTIONS, _V12_BILLING_MESSAGES, _V12_BILLING_MESSAGES_BY_ACCOUNT)


def _add_billing(con: sqlite3.Connection) -> None:
    """Schema 11 to 12: subscriptions and the provider messages seen, both empty. Every
    workspace stays on the plan the operator set. Nothing that existed changes."""
    for statement in _V12_BILLING:
        con.execute(statement)
