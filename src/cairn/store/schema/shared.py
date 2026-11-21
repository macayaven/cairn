"""What the schema's steps and the store share: the clock a step stamps rows with
while the upgrade runs, and the search index of each account, made and set to delete
securely."""

import sqlite3
from collections.abc import Callable
from contextvars import ContextVar

_FTS5_SECURE_DELETE = sqlite3.sqlite_version_info >= (3, 42, 0)  # the option's first release
# One search index per account, made by the account's first save_page, where its searches
# match (cairn.store.ranking scores them with the statistics above, never FTS5's own). One row
# per claim of each page's latest version; save_page replaces the page's rows.
_CLAIM_INDEX = """CREATE VIRTUAL TABLE {index} USING fts5 (
    project_id UNINDEXED, slug UNINDEXED, claim_id UNINDEXED, span_ids UNINDEXED, title, text
)"""


# The store's clock (StoreBase._stamp) while _upgrade runs: a step takes the connection
# alone, and the one that stamps a row (the default account's creation) reads it from here.
_STAMP: ContextVar[Callable[[], str]] = ContextVar("cairn_store_upgrade_stamp")
# What a step says once it has committed: each step's own list while _upgrade runs it, which
# _upgrade calls in order after the step's transaction, floor and version included, commits,
# and drops when it rolls back. A step never writes to the log itself.
_AFTER: ContextVar[list[Callable[[], None]]] = ContextVar("cairn_store_upgrade_after")


def _create_index(con: sqlite3.Connection, index: str) -> None:
    con.execute(_CLAIM_INDEX.format(index=index))
    _delete_securely(con, index)


def _secure_index(con: sqlite3.Connection, index: str) -> None:
    """Turn FTS5's secure-delete on for an index made without it (by an SQLite older than
    3.42), at a write to it, never at open: opening a current database writes nothing."""
    if (
        _FTS5_SECURE_DELETE
        and not con.execute(
            f"SELECT 1 FROM {index}_config WHERE k = 'secure-delete' AND v = 1"
        ).fetchone()
    ):
        _delete_securely(con, index)


def _delete_securely(con: sqlite3.Connection, index: str) -> None:
    """Make the index remove a replaced claim's terms instead of marking them deleted. An
    SQLite without the option keeps the marks until FTS5 merges the segments."""
    if _FTS5_SECURE_DELETE:
        con.execute(f"INSERT INTO {index} ({index}, rank) VALUES ('secure-delete', 1)")
