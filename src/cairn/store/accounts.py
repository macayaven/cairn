"""Accounts: not scoped for the store and a view acting for no person, which manage every
account; a view acting for a person, or narrowed, reads and deletes its own only
(cairn.store.reach.OTHER_WORKSPACES)."""

import logging
import secrets
import sqlite3

from cairn.store.base import (
    _ACCOUNT_SELECT,
    COMPACT_DUE,
    DEFAULT_ACCOUNT_ID,
    RECORD_DELETION,
    StoreBase,
    _account,
    _find_account,
    _index_name,
    _insert_account,
)
from cairn.store.models import Account
from cairn.store.people import _FORGET_MEMBERS

ACCOUNT_NAME_MAX = 80
# A workspace's Slack rows (K1): what each of its install's tables holds, then the install.
_INSTALLS = "(SELECT id FROM slack_installs WHERE account_id = ?)"
_SLACK_ROWS = (
    *(
        f"DELETE FROM {table} WHERE install_id IN {_INSTALLS}"
        for table in ("slack_notes", "slack_people", "slack_link_offers", "slack_events")
    ),
    "DELETE FROM slack_installs WHERE account_id = ?",
)
NOT_AN_OWNER = "only an owner of a workspace can delete it"
NOT_YOUR_WORKSPACE = "a view acting for a person deletes no workspace but its own"
store_log = logging.getLogger("cairn.store")


class AccountMethods(StoreBase):
    # Accounts: not scoped for a view acting for no person (cairn.store.reach)
    def create_account(self, name: str) -> Account:
        """Raises ValueError for a blank name or one over 80 characters (after stripping)."""
        name = name.strip()
        if not name or len(name) > ACCOUNT_NAME_MAX:
            raise ValueError(f"an account name has 1 to {ACCOUNT_NAME_MAX} characters: {name!r}")
        account = Account(id="acct_" + secrets.token_hex(6), name=name, created_at=self._stamp())
        with self._write() as con:
            _insert_account(con, account)
        return account

    def get_account(self, account_id: str) -> Account | None:
        with self._read() as con:
            return _find_account(con, account_id)

    def list_accounts(self) -> list[Account]:
        """In creation order, the default account first."""
        with self._read() as con:
            rows = con.execute(f"{_ACCOUNT_SELECT} ORDER BY accounts.seq").fetchall()
        return [_account(row) for row in rows]

    def delete_account(self, account_id: str) -> bool:
        """Remove the account and everything it owns, its search index included, and its
        memberships: a member left with no workspace is removed too, with their identities.
        False for an unknown id. ValueError: the default account. What it deletes is zeroed
        (secure_delete), and it ends with a truncating checkpoint, so its text leaves the
        files at once; it costs what the workspace holds, not the whole database. When another
        connection keeps the log from being truncated, the deletion stands and a warning on
        "cairn.store" says so.

        A view acting for a person deletes its own workspace only, and only while the person
        is an owner of it, decided inside the write that deletes: PermissionError
        (NOT_YOUR_WORKSPACE, NOT_AN_OWNER, or NOT_A_MEMBER for one removed meanwhile) and
        nothing written otherwise. A view acting for no person deletes any workspace."""
        if account_id == DEFAULT_ACCOUNT_ID:
            raise ValueError("the default account cannot be deleted")
        if self._user_id is not None and account_id != self._account_id:
            raise PermissionError(NOT_YOUR_WORKSPACE)
        with self._write() as con:
            if self._user_id is not None and self._role_in(con) != "owner":
                raise PermissionError(NOT_AN_OWNER)
            account = _find_account(con, account_id)
            if account is None:
                return False
            dropped = self._hosted_tokens(con, account.id)
            projects = "(SELECT id FROM projects WHERE account_id = ?)"
            for statement in (
                f"DROP TABLE IF EXISTS {_index_name(account.id)}",
                f"DELETE FROM page_heads WHERE project_id IN {projects}",  # Q1: first, so its
                f"DELETE FROM page_versions WHERE project_id IN {projects}",  # trigger finds none
                f"DELETE FROM page_keys WHERE project_id IN {projects}",  # N1
                f"DELETE FROM waiting WHERE project_id IN {projects}",
                "DELETE FROM note_writers WHERE note_id IN"  # A1
                f" (SELECT id FROM notes WHERE project_id IN {projects})",
                "DELETE FROM spans WHERE note_id IN"
                f" (SELECT id FROM notes WHERE project_id IN {projects})",
                f"DELETE FROM notes WHERE project_id IN {projects}",
                f"DELETE FROM project_people WHERE project_id IN {projects}",
                f"DELETE FROM forgotten WHERE project_id IN {projects}",
                "DELETE FROM projects WHERE account_id = ?",
                "DELETE FROM invites WHERE account_id = ?",
                "DELETE FROM removals WHERE account_id = ?",
                "DELETE FROM project_sets WHERE account_id = ?",
                "DELETE FROM search_claims WHERE account_id = ?",
                "DELETE FROM search_projects WHERE account_id = ?",
                "DELETE FROM api_keys WHERE account_id = ?",
                "DELETE FROM sessions WHERE account_id = ?",
                "DELETE FROM oauth_tokens WHERE grant_id IN"
                " (SELECT id FROM oauth_grants WHERE account_id = ?)",
                "DELETE FROM oauth_codes WHERE account_id = ?",
                "DELETE FROM oauth_grants WHERE account_id = ?",
                "DELETE FROM made_with WHERE account_id = ?",  # A1
                "DELETE FROM settings WHERE account_id = ?",
                "DELETE FROM credential_tokens WHERE account_id = ?",  # connected Google accounts
                *_SLACK_ROWS,  # its Slack install and all it holds (cairn.store.slack)
                "DELETE FROM audit_events WHERE account_id = ?",
                "DELETE FROM source_items WHERE source IN"  # before the sources they name
                " (SELECT seq FROM sources WHERE account_id = ?)",
                "DELETE FROM sources WHERE account_id = ?",
                "DELETE FROM usage WHERE account_id = ?",
                "DELETE FROM account_quota WHERE account_id = ?",
                "DELETE FROM account_disk WHERE account_id = ?",  # after its spans and pages
                "DELETE FROM model_reservations WHERE account_id = ?",
                *_FORGET_MEMBERS,
                "DELETE FROM accounts WHERE id = ?",
            ):
                con.execute(statement, (account.id,) * statement.count("?"))
            con.execute(  # a compaction is due (cairn.store.portable.compaction_state)
                RECORD_DELETION,
                (DEFAULT_ACCOUNT_ID, COMPACT_DUE, self._stamp(), self._stamp()),
            )
        self._queue_revocations(dropped)  # Google, by the rule
        self._truncate_log(f"workspace {account.id}")
        return True

    def _truncate_log(self, what: str) -> None:
        """Copy the write-ahead log into the file and truncate it to nothing: the log still
        holds the deleted rows' pages as they were before the deletion zeroed them. When
        another connection holds the file and the log cannot be truncated, the deletion
        stands all the same, and a warning on the "cairn.store" logger says so (the next
        truncating checkpoint, or `compact`, finishes the job). `what`: what was removed, by
        id ("workspace acct_...", "project br_...", "note cap_..."), never by name."""
        try:
            with self._locked() as con:  # StoreUnusable goes on: it is not a busy file
                busy = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
        except sqlite3.OperationalError:
            busy = 1
        if busy:
            store_log.warning(
                "%s is deleted, but its old pages stay in the write-ahead log until"
                " a truncating checkpoint: another connection holds the database",
                what,
            )
