"""Schema step 28: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

# A Google account a person connected in the browser (cairn.store.hosted): one row per
# credential whose settings row (cairn.store.sources, CREDENTIAL_PREFIX) says it is hosted.
# The refresh token is sealed with AES-GCM under the key in the server's environment, a fresh
# nonce each time, and the workspace, credential name and owner as associated data, so a
# sealed value copied to another row does not open. The address and the granted scopes are in
# clear. A row goes with its credential and with its workspace. Never exported.
_V28_CREDENTIAL_TOKENS = """CREATE TABLE credential_tokens (
    account_id TEXT NOT NULL,  -- the workspace
    credential TEXT NOT NULL,  -- the credential's name, as its settings row names it
    kind TEXT NOT NULL,  -- gmail, google_calendar, google_drive or google_meet
    owner TEXT NOT NULL,  -- the person's user id
    address TEXT NOT NULL,  -- the Google account's address, as its ID token said: for people
    subject TEXT NOT NULL,  -- Google's own id of the account (the ID token's sub): the rule's
    scopes TEXT NOT NULL,  -- the scopes Google granted, separated by spaces
    sealed TEXT NOT NULL,  -- base64url of the nonce, then the sealed refresh token and its tag
    added_at TEXT NOT NULL,  -- when it was first connected
    refreshed_at TEXT NOT NULL,  -- when Google last issued the token held (connect, reconnect)
    ended_at TEXT,  -- when a revocation of the same Google account may have ended it
    PRIMARY KEY (account_id, credential)
) WITHOUT ROWID"""
# A dropped connected account whose Google permission is still to be ended (the revoking
# rule, cairn.web.google_connect): its token sealed again under the row's own id as
# associated data, so a restart finds it and sends it. No workspace and no person: a deleted
# workspace leaves only the account's id, its address and the sealed token here, until
# Google answers the revocation with 200 or 400.
_V28_GOOGLE_REVOCATIONS = """CREATE TABLE google_revocations (
    id TEXT PRIMARY KEY,  -- random, the sealed token's associated data
    subject TEXT NOT NULL,  -- Google's id of the account (the ID token's sub), for the rule
    address TEXT NOT NULL,  -- its address when it was dropped
    sealed TEXT NOT NULL,  -- base64url of the nonce, then the sealed refresh token and its tag
    ended_at TEXT NOT NULL,  -- when its account was dropped here
    claimed_by TEXT,  -- the one sending it now (random), so no two send it at once
    claimed_at TEXT,  -- when; a claim older than CLAIM_STALE is taken again
    tries INTEGER NOT NULL DEFAULT 0,  -- Google's refusals so far (REVOKE_TRIES: no more sent)
    due_at TEXT,  -- not sent again before this (a refusal backs off); NULL: on the next pass
    said TEXT  -- the outcome last logged, so each change of state is logged once
) WITHOUT ROWID"""


def _add_credential_tokens(con: sqlite3.Connection) -> None:
    """Schema _CHECKOUTS to _CREDENTIAL_TOKENS: the credential_tokens and google_revocations
    tables, empty. The settings rows of the credentials registered before stay as they are.
    Nothing that existed changes. One step for both: google_revocations joined it before any
    database outside the tests ran its earlier form, and a step runs in one transaction with
    its version, so a database at 28 holds both tables; one that does not is refused on
    opening (_check_tables). The same holds for the columns the step's tables gained before
    any database outside the tests ran it (credential_tokens.subject and ended_at; the
    waiting rows' subject, claim and back-off): a database at 28 that lacks a column of them
    is refused too."""
    con.execute(_V28_CREDENTIAL_TOKENS)
    con.execute(_V28_GOOGLE_REVOCATIONS)
