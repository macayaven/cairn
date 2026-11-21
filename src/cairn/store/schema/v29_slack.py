"""Schema step 29: its own statements and the function the upgrade runs for it.
A step is history: nothing here changes once a database outside the tests ran it."""

import sqlite3

# A workspace's Slack install (K1, cairn.store.slack): one a workspace, keyed by an id of its
# own that the three addresses Slack calls carry. Its signing secret and bot token are sealed
# with AES-GCM under the key in the server's environment (CAIRN_SLACK_KEY), a fresh nonce
# each time, and the install, the workspace and which of the two it is as associated data,
# so a copy of the database opens neither and a sealed value moved does not open. Removing
# it deletes both at once. Never exported.
_V29_SLACK_INSTALLS = """CREATE TABLE slack_installs (
    id TEXT PRIMARY KEY,  -- "sl_" and 12 hexadecimal characters
    account_id TEXT NOT NULL UNIQUE,  -- the workspace: one install for each
    state TEXT NOT NULL,  -- pending, ready, on, off or removed
    team_id TEXT,  -- the Slack team the bot token belongs to; kept when removed
    enterprise_id TEXT,
    team_domain TEXT,
    bot_user_id TEXT,  -- the bot's own Slack user id, as auth.test names it
    signing_secret TEXT,  -- sealed; NULL until ready, and once removed
    bot_token TEXT,  -- sealed; NULL until ready, once removed, and once Slack revoked it
    command TEXT NOT NULL,  -- the slash command's name, "/cairn" unless changed
    made_by TEXT,  -- the person who made it
    made_at TEXT NOT NULL,
    removed_at TEXT,  -- when it was last removed; NULL while it is not
    checked_at TEXT,  -- when Slack was last asked whether the bot token still works
    dead_checks INTEGER NOT NULL DEFAULT 0,  -- answers in a row that said it does not
    bot_gone_at TEXT  -- since when no bot token is held because Slack ended it; else NULL
) WITHOUT ROWID"""
# A Slack user linked to a person of the install's workspace, and the project their notes go to.
_V29_SLACK_PEOPLE = """CREATE TABLE slack_people (
    install_id TEXT NOT NULL,
    slack_user_id TEXT NOT NULL,
    user_id TEXT NOT NULL,  -- the person
    project_id TEXT,  -- their Slack project; NULL: none chosen, or it was deleted
    linked_at TEXT NOT NULL,
    PRIMARY KEY (install_id, slack_user_id)
) WITHOUT ROWID"""
# A one-time offer of a link, kept as the SHA-256 digest of the value the Slack user is given;
# the person who said yes to it in the browser, and the digest of the code they were shown,
# which the same Slack user types back to be linked. A used offer is deleted.
_V29_SLACK_LINK_OFFERS = """CREATE TABLE slack_link_offers (
    digest TEXT PRIMARY KEY,
    install_id TEXT NOT NULL,
    slack_user_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,  -- ten minutes after it was made, for both steps
    user_id TEXT,  -- the person who said yes in the browser; NULL until then
    code_digest TEXT,  -- of the code that person was shown; NULL until then
    tries INTEGER NOT NULL DEFAULT 0,  -- wrong codes this Slack user typed since
    UNIQUE (install_id, slack_user_id)  -- one offer for a Slack user at a time
) WITHOUT ROWID"""
# The event ids an install has seen, so an event is done once; swept after a day.
_V29_SLACK_EVENTS = """CREATE TABLE slack_events (
    install_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (install_id, event_id)
) WITHOUT ROWID"""
# Every note an install created: how (a filed message, with its conversation and time, unique
# within the install; or a typed note), the Slack team the install held when the row was made,
# who brought it and when, and when it is to be forgotten once the install was removed (NULL:
# not due; setting the install up again for that same team makes it NULL again). A filed
# message is claimed first, a row with no note yet, so two filings of it store one note; a claim
# with no note is swept after ten minutes.
_V29_SLACK_NOTES = """CREATE TABLE slack_notes (
    id INTEGER PRIMARY KEY,
    note_id TEXT UNIQUE,  -- NULL while a filed message is claimed and not yet stored
    install_id TEXT NOT NULL,
    how TEXT NOT NULL,  -- filed or typed
    team_id TEXT,  -- the Slack team the install held when this row was made
    conversation_id TEXT,  -- a filed message's
    message_time TEXT,  -- a filed message's time, as Slack writes it
    brought_by TEXT,  -- the person
    brought_at TEXT NOT NULL,
    forget_at TEXT,  -- NULL until its install is removed
    owns INTEGER NOT NULL DEFAULT 1,  -- 0: the project held this text already; a tie, no more
    UNIQUE (install_id, conversation_id, message_time)
)"""
_V29_SLACK_NOTES_DUE = "CREATE INDEX slack_notes_due ON slack_notes (forget_at)"
# One Slack team, one workspace: an install that is pending or removed holds no team.
_V29_SLACK_TEAM = """CREATE UNIQUE INDEX slack_installs_team ON slack_installs (team_id)
    WHERE state IN ('ready', 'on', 'off')"""
_V29_SLACK = (
    _V29_SLACK_INSTALLS,
    _V29_SLACK_PEOPLE,
    _V29_SLACK_LINK_OFFERS,
    _V29_SLACK_EVENTS,
    _V29_SLACK_NOTES,
    _V29_SLACK_NOTES_DUE,
    _V29_SLACK_TEAM,
)


def _add_slack(con: sqlite3.Connection) -> None:
    """Schema _CREDENTIAL_TOKENS to _SLACK (K1): the five Slack tables, empty, whether or not
    the server is ever started with --slack. Nothing that existed changes. It only adds
    (_ONLY_ADDS): a build of _CREDENTIAL_TOKENS would run on it, leaving the Slack tables
    alone (a workspace it deleted would leave its rows there)."""
    for statement in _V29_SLACK:
        con.execute(statement)
