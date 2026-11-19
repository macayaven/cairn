"""The connection, the lock and the account binding every part of Store builds on, and the
small helpers they share: the clock, digests, transactions and account rows.

A transaction that fails (R1). Beginning, the body and the commit are under one handler
(_transaction): whatever fails, the transaction is rolled back before the error goes on, so
the one connection every view shares is never left inside a transaction and the next call
works as if nothing had happened once the cause is gone; nothing of the failed write is
kept. SQLite rolls some failures back by itself (a full disk, an I/O error); then there is
nothing left to roll back. Whether a rollback is needed, and whether it worked, is read from
the connection (`in_transaction`), never guessed from which statement raised.

If the rollback itself fails, the store does not go on in an unknown state: it is unusable.
The call that found it raises StoreUnusable, the connection is closed (SQLite rolls back
what it held as it closes, so no write lock on the file is left behind for other processes)
and every later call on the store, or on any view of it, made before or after, raises
StoreUnusable at once, without touching SQLite, until the process opens the store again
(the server is restarted). Store.health answers "unavailable" for it. A fresh connection was
not chosen: views hold the connection they were made with, a database in memory would come
back empty, and an I/O error that a rollback cannot get past is one a person should look at.

A write that fails because the disk is full or the file cannot be written (SQLite's
SQLITE_FULL, SQLITE_IOERR, SQLITE_READONLY or SQLITE_CANTOPEN, or one of their fixed
sentences on an error that carries no code) raises CannotWrite, a sqlite3.OperationalError
whose message is one fixed sentence that names no path and no SQL. The web service answers
it with 503 (cairn.memory.Unavailable). Its audit row was in the transaction that was rolled
back, so the audit log holds nothing for it. Reads are not translated: they write nothing."""

import contextlib
import copy
import hashlib
import json
import logging
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from cairn.store.models import Account

if TYPE_CHECKING:
    from cairn.store import Store

store_log = logging.getLogger("cairn.store")
# Every audit event, once stored, is also one JSON line here (StoreBase._audit), with the same
# fields as its row: an operator sends this logger to a store nobody can edit.
AUDIT_LOGGER = "cairn.audit"
audit_log = logging.getLogger(AUDIT_LOGGER)

UNUSABLE = (
    "the store cannot be used: a transaction failed and could not be rolled back, so its"
    " connection was closed; open the store again (restart the server)"
)
CANNOT_WRITE = (
    "the database could not be written: the disk is full or the file cannot be written;"
    " nothing of this write was kept"
)
# SQLite's primary result codes for a file that cannot be written, and the fixed sentence
# SQLite gives each (sqlite3_errstr), for an error made without a code.
_UNWRITABLE_CODES = frozenset(
    {sqlite3.SQLITE_FULL, sqlite3.SQLITE_IOERR, sqlite3.SQLITE_READONLY, sqlite3.SQLITE_CANTOPEN}
)
_UNWRITABLE_SAYS = frozenset(
    {
        "database or disk is full",
        "disk I/O error",
        "attempt to write a readonly database",
        "unable to open database file",
    }
)

DEFAULT_ACCOUNT_ID = "acct_default"
# A workspace's audit log keeps at most its newest AUDIT_KEEP events: anyone who may write makes
# events (a refused note is one), so the log is bounded. Beyond it, an insert deletes the
# oldest, a batch of AUDIT_PRUNE_BATCH at a time, so most inserts delete nothing.
AUDIT_KEEP = 100_000
AUDIT_PRUNE_BATCH = 1_000
UNSET = object()  # ConnectionState.audit_keep before the server sets it
_KEEP = "how many events a workspace keeps is a whole number, 1 or more, or None for every event"
# The store's own record of compaction, two rows of the default workspace's settings that
# no setting call can read or change (they are not in audit.SETTINGS): when a workspace was
# deleted since the last compaction, and when the last compaction began.
COMPACT_DUE = "store.compact_due"
COMPACTED_AT = "store.compacted_at"
# Q1: what the last `serve` was told to keep of each page's history, a row of the same kind
# (cairn.store.history): JSON, {"keep": N or null} or {"plans": {name: N or null}, "default"}.
KEEP_VERSIONS = "store.keep_versions"
ACCOUNT_ID = re.compile(r"acct_(?:default|[0-9a-f]{12})")  # every id the store generates
# The roles that manage a workspace: its people, keys, sets, settings and sources (T1).
MANAGER_ROLES = frozenset({"owner", "admin"})
NOT_A_MANAGER = "only the workspace's owners and admins manage it"
NOT_A_MEMBER = "you are no longer a member of this workspace"
VIEW_IS_BOUND = "a view is bound to its workspace and person: open another from the store"


class StoreUnusable(RuntimeError):
    """The store's connection was left in an unknown state (a rollback failed) and has been
    closed: every call on the store and its views raises this at once, with UNUSABLE, until
    the store is opened again. Not a sqlite3.Error, so nothing that handles SQLite's errors
    (a busy file, an incomplete checkpoint) mistakes it for one of them."""

    def __init__(self) -> None:
        super().__init__(UNUSABLE)


class CannotWrite(sqlite3.OperationalError):
    """A write failed because the disk is full or the file cannot be written; it was rolled
    back and nothing of it was kept. The message is CANNOT_WRITE; `sqlite_errorcode` and
    `sqlite_errorname` are SQLite's, when the error it came from had them."""

    def __init__(self, cause: sqlite3.Error) -> None:
        super().__init__(CANNOT_WRITE)
        self.sqlite_errorcode = getattr(cause, "sqlite_errorcode", None)
        self.sqlite_errorname = getattr(cause, "sqlite_errorname", None)


def _unwritable(error: sqlite3.Error) -> bool:
    """Whether `error` says the file could not be written (the disk is full, an I/O error,
    a read-only or missing file), by SQLite's primary result code, or by its fixed sentence
    when the error carries no code."""
    code = getattr(error, "sqlite_errorcode", None)
    if code is not None:
        return code & 0xFF in _UNWRITABLE_CODES
    return str(error) in _UNWRITABLE_SAYS


class ConnectionState:
    """What every view of one store shares about its one connection (views are copies that
    share this object): whether it was found unusable, and how many versions of each page a
    save keeps when no plan says (Q1; cairn.store.history; None: every version)."""

    __slots__ = ("audit_keep", "audit_out", "keep_versions", "reads_audited", "unusable")

    def __init__(self) -> None:
        self.unusable = False
        self.keep_versions: int | None = None
        # How many events each workspace keeps (StoreBase.audit_keep): UNSET for AUDIT_KEEP,
        # read when an event is stored; None for every event.
        self.audit_keep: int | object | None = UNSET
        # The events stored by the write under way, sent to the "cairn.audit" logger once it
        # commits and dropped if it does not (StoreBase._write).
        self.audit_out: list[str] = []
        self.reads_audited = False  # cairn.memory: whether reads are events (serve --audit-reads)


class StoreBase:
    """One account's view of the database: the connection and lock (made by
    Store.__init__), the clock, the binding to an account and to the person it acts for,
    who the view acts as in the audit log, and transactions."""

    # Store(path, clock=...): what every timestamp and expiry check reads, shared by every
    # view. None: this module's _clock, looked up at each call.
    _clock_fn: Callable[[], datetime] | None = None
    _actor: tuple[str, str | None] = ("system", None)  # a bare view: the command line, tests
    _actor_id: str | None = None  # the actor's id, where names repeat (acting_as)
    _writer: tuple[str, str | None] | None = None  # who its notes name (writing_as), if set
    _made_through: str | None = None  # the id of the key its caller came with (writing_as)
    _guard: Any = None  # a note guard that replaces the workspace's policy (with_guard)

    def _instant(self) -> datetime:
        """Now: the one place a store reads the time. ValueError: a clock that answers
        without a time zone (a naive datetime would be read as local time)."""
        moment = _clock() if self._clock_fn is None else self._clock_fn()
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("the store's clock must give a datetime with a time zone")
        return moment

    def _stamp(self) -> str:
        """Now, as the store writes a timestamp (_iso)."""
        return _iso(self._instant())

    @property
    def actor(self) -> tuple[str, str | None]:
        """(kind, name) as the audit log records this view's writes: ("system", None) for a
        bare view; the server sets the caller with `acting_as`."""
        return self._actor

    @property
    def actor_id(self) -> str | None:
        """The id of the actor, which its name may share with others: a key's id, a grant's
        id or a person's; None for a bare view."""
        return self._actor_id

    @property
    def audit_keep(self) -> int | None:
        """How many of its newest events each workspace keeps: AUDIT_KEEP (100,000) until the
        server sets it for every view of this connection (`serve --audit-keep`); None: every
        event. Held in memory for this connection only. ValueError: not None or a whole
        number of at least 1."""
        keep = self._state.audit_keep
        return AUDIT_KEEP if keep is UNSET else keep  # type: ignore[return-value]

    @audit_keep.setter
    def audit_keep(self, keep: int | None) -> None:
        if keep is not None and (type(keep) is not int or keep < 1):
            raise ValueError(_KEEP)
        self._state.audit_keep = keep

    @property
    def reads_audited(self) -> bool:
        """Whether a read is an event of the workspace (`serve --audit-reads`; cairn.memory),
        for every view of this connection. False until the server says so."""
        return self._state.reads_audited

    @reads_audited.setter
    def reads_audited(self, on: bool) -> None:
        if type(on) is not bool:
            raise ValueError("reads_audited is True or False")
        self._state.reads_audited = on

    def _audit(
        self,
        con: sqlite3.Connection,
        action: str,
        *,
        project: str | None = None,
        detail: dict[str, Any] | None = None,
        account_id: str | None = None,
        actor: tuple[str, str | None] | None = None,
        actor_id: str | None = None,
        at: str | None = None,
    ) -> None:
        """One audit row, on `con`: inside the transaction of the write it describes, so the
        write and its record land together or not at all. It goes to this view's account and
        actor unless the write belongs to another (an OAuth call on the unscoped store names
        the account and agent of the code or grant it settles). A view acting for a person
        names them in the detail. `at`: the write's own timestamp, when it has one. Each event
        gets the next number in its workspace; past `audit_keep` events, the oldest are
        deleted in the same transaction, a batch at a time (both lookups use the number's
        index). A batch is AUDIT_PRUNE_BATCH, or half of what is kept when that is less, so a
        prune never leaves fewer than half. The event, as its row holds it, is also one JSON
        line on the "cairn.audit" logger once the transaction commits (_write)."""
        detail = dict(detail or {})
        if actor is None:
            actor, actor_id = self._actor, self._actor_id
            if self._user_id is not None:
                detail["user"] = self._user_id
        account_id = account_id or self._account_id
        last, oldest = con.execute(
            "SELECT MAX(n), MIN(n) FROM audit_events WHERE account_id = ?", (account_id,)
        ).fetchone()
        number = (last or 0) + 1
        stamp = at or self._stamp()
        stored = json.dumps(detail, sort_keys=True)
        con.execute(
            "INSERT INTO audit_events"
            " (account_id, n, at, actor_kind, actor, actor_id, action, project, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (account_id, number, stamp, *actor, actor_id, action, project, stored),
        )
        keep = self.audit_keep
        if keep is not None and number - (oldest or number) + 1 > keep:  # one too many
            batch = min(AUDIT_PRUNE_BATCH, max(1, keep // 2))
            con.execute(
                "DELETE FROM audit_events WHERE account_id = ? AND n <= ?",
                (account_id, number - keep + batch - 1),
            )
        line = {
            "account_id": account_id,
            "n": number,
            "at": stamp,
            "actor_kind": actor[0],
            "actor": actor[1],
            "actor_id": actor_id,
            "action": action,
            "project": project,
            "detail": json.loads(stored),
        }
        self._state.audit_out.append(json.dumps(line, sort_keys=True))

    def _bind(self, account_id: str, user_id: str | None = None) -> None:
        self._account_id = account_id
        self._user_id = user_id
        self._index = _index_name(account_id)

    @property
    def account_id(self) -> str:
        """The account every call on this store is scoped to."""
        return self._account_id

    @property
    def user_id(self) -> str | None:
        """The member this view acts for: the sessions and agent approvals it creates carry
        them. None: it acts for the account alone, as an API key does."""
        return self._user_id

    def for_account(self, account_id: str, user_id: str | None = None) -> "Store":
        """A view bound to that account over the same database, acting for `user_id` when
        given. KeyError: unknown account, or a user who is not a member of it.

        Only the store, or a plain view of a workspace (no person, no limit), binds an
        identity. A bound view (`_bound`: it acts for a person, or is narrowed or limited in
        any way) returns itself for its own account and person (`user_id` None or its own),
        and refuses another account or another person (PermissionError, VIEW_IS_BOUND): it
        can neither shed its person nor take another's."""
        if self._bound():
            same = account_id == self._account_id and user_id in (None, self._user_id)
            if not same:
                raise PermissionError(VIEW_IS_BOUND)
            return self
        with self._read() as con:
            account = _find_account(con, account_id)
            member = (
                user_id is None
                or con.execute(
                    "SELECT 1 FROM memberships WHERE account_id = ? AND user_id = ?",
                    (account_id, user_id),
                ).fetchone()
            )
        if account is None or not member:
            raise KeyError(account_id)
        view = copy.copy(self)  # the same connection and lock
        view._owner = False
        view._bind(account.id, user_id)
        return view

    def _bound(self) -> bool:
        """Whether this view is held to its workspace and person (for_account): it acts for a
        person. A layer that limits a view (cairn.store.reach) adds its own limit with
        `super()._bound() or ...`, so a new limit cannot be shed by rebinding either."""
        return self._user_id is not None

    def _limited_connection(self) -> bool:
        """Whether this view is a connection limited to some projects or to reading. This view
        is not; a narrowed one is (cairn.store.reach), which erasure refuses whoever approved
        it (cairn.store.erasure)."""
        return False

    def close(self) -> None:
        """Close the database; on a view this does nothing."""
        if self._owner:
            with self._lock:
                self._con.close()

    # Who manages the workspace: the one rule (T1)

    def _role_in(self, con: sqlite3.Connection) -> str | None:
        """The role of the view's person in its account, read on `con`; None for a view that
        acts for no person, or for a person who is no longer a member."""
        if self._user_id is None:
            return None
        row = con.execute(
            "SELECT role FROM memberships WHERE account_id = ? AND user_id = ?",
            (self._account_id, self._user_id),
        ).fetchone()
        return row[0] if row else None

    def _manages(self, con: sqlite3.Connection) -> bool:
        """Whether this view manages the workspace's people, keys, sets, settings and
        sources: a view acting for no person does (the command line, the workspace's own
        key); a person when they own or administer it now. A narrowed view never does
        (cairn.store.reach refuses before this is asked)."""
        return self._user_id is None or self._role_in(con) in MANAGER_ROLES

    def _require_manager(self, con: sqlite3.Connection, refusal: str = NOT_A_MANAGER) -> None:
        """PermissionError(refusal) unless this view manages the workspace, decided on `con`
        inside the caller's transaction."""
        if not self._manages(con):
            raise PermissionError(refusal)

    def _reach(self, con: sqlite3.Connection) -> frozenset[str] | None:
        """The projects of the account this view may use: their ids, or None for every one.

        The one seam every read and write of a project's data goes through. A method calls it
        once, on the connection of the section it works in (a `_read` or a `_write`), and
        uses the answer in that same section, so nothing can change between the check and
        the operation. This view reaches every project; a narrowed view answers its reach
        (cairn.store.reach), and any later limit, such as a person's own access, is one more
        override that intersects with what `super()._reach(con)` answers."""
        return None

    def _writable(self, con: sqlite3.Connection) -> frozenset[str] | None:
        """Of the projects in reach, those the view's person may write to: their ids, or None
        for every one. This view writes wherever it reaches; a person's own access answers
        otherwise (cairn.store.access)."""
        return None

    @contextmanager
    def _locked(self) -> Iterator[sqlite3.Connection]:
        """The connection, under the lock: every use of it goes through here. StoreUnusable
        at once when the store was found unusable, before SQLite is touched; when a
        StoreUnusable is raised inside (a rollback failed), the store becomes unusable for
        every view and the connection is closed (_unusable)."""
        with self._lock:
            if self._state.unusable:
                raise StoreUnusable()
            try:
                yield self._con
            except StoreUnusable:
                self._unusable()
                raise

    def _unusable(self) -> None:
        """Under the lock: mark the store unusable for every view, and close the connection,
        which rolls back what it held and lets go of the file."""
        if not self._state.unusable:
            store_log.error("a rollback failed: the store is unusable until it is opened again")
        self._state.unusable = True
        with contextlib.suppress(Exception):
            self._con.close()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        """One read transaction under the lock: every statement in it sees the database at
        one moment, also against another connection to the file (WAL keeps the snapshot)."""
        with self._locked() as con, _transaction(con, "DEFERRED"):
            yield con

    @contextmanager
    def _write(self, *, member: bool = True) -> Iterator[sqlite3.Connection]:
        """One transaction, holding the lock from the first read to the commit. CannotWrite
        when it failed because the disk is full or the file cannot be written (the module's
        docstring); it was rolled back.

        The one membership check for writes: a view acting for a person who is not a member
        of its workspace now (removed after the request was admitted) is refused
        (PermissionError, NOT_A_MEMBER) inside the transaction, before anything is written,
        whatever the write. `member=False` only for a write that ends what the person
        holds (end_session)."""
        with self._locked() as con:
            out = self._state.audit_out
            out.clear()  # nothing of an earlier write that did not commit
            try:
                with _transaction(con):
                    if member and self._user_id is not None and self._role_in(con) is None:
                        raise PermissionError(NOT_A_MEMBER)
                    yield con
            except sqlite3.Error as error:
                out.clear()  # rolled back: its events were never stored
                if isinstance(error, CannotWrite) or not _unwritable(error):
                    raise
                store_log.warning(
                    "a write failed and was rolled back: the disk is full or the file cannot"
                    " be written (%s)",
                    getattr(error, "sqlite_errorname", None) or "no code",
                )
                raise CannotWrite(error) from error
            except BaseException:
                out.clear()  # rolled back: its events were never stored
                raise
            lines = list(out)
            out.clear()
            for line in lines:  # stored: each is one line on "cairn.audit"
                with contextlib.suppress(Exception):  # the log's trouble, not the write's
                    audit_log.info(line)


@contextmanager
def _transaction(con: sqlite3.Connection, mode: str = "IMMEDIATE") -> Iterator[sqlite3.Connection]:
    """One transaction, committed on exit. A write takes the write lock at once
    (IMMEDIATE); a read starts its snapshot at its first statement.

    The BEGIN, the body and the COMMIT are under one handler: whatever fails, the
    transaction is rolled back before the error goes on, so the connection is never left
    inside one. Nothing to roll back when SQLite already did (a full disk, an I/O error) or
    the BEGIN opened nothing (a busy file). StoreUnusable, from the error that started it,
    when the connection is still inside a transaction after the rollback: its state is
    unknown (the caller makes the store unusable, StoreBase._locked)."""
    try:
        con.execute(f"BEGIN {mode}")
        yield con
        con.execute("COMMIT")
    except BaseException as error:
        if _inside(con):
            with contextlib.suppress(Exception):
                con.execute("ROLLBACK")
            if _inside(con):
                raise StoreUnusable() from error
        raise


def _inside(con: sqlite3.Connection) -> bool:
    """Whether the connection is inside a transaction; False for a closed one, which holds
    nothing to roll back."""
    try:
        return con.in_transaction
    except sqlite3.Error:
        return False


def _index_name(account_id: str) -> str:
    """The name of an account's FTS5 table. This is the one place where an id becomes SQL
    text, so it takes nothing but an id of the shape the store generates."""
    if not ACCOUNT_ID.fullmatch(account_id):
        raise ValueError(f"not an account id: {account_id!r}")
    return f"claim_search_{account_id}"


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    found = con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,))
    return found.fetchone() is not None


def _clock() -> datetime:
    """The real time, for a store opened without a clock (tests may replace it)."""
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    """ISO 8601 UTC with fixed-width microseconds, so the strings sort in time order."""
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


# The columns of the accounts table. A workspace's plan is kept beside it (account_quota,
# schema 11), so _ACCOUNT_SELECT joins it in and every step that writes accounts keeps
# the table it had.
_ACCOUNT_FIELDS = ("id", "name", "created_at")
_ACCOUNT_COLUMNS = ", ".join(_ACCOUNT_FIELDS)
_ACCOUNT_SELECT = (
    "SELECT accounts.id, accounts.name, accounts.created_at, account_quota.plan FROM accounts"
    " LEFT JOIN account_quota ON account_quota.account_id = accounts.id"
)
_PROJECT_FIELDS = ("id", "name", "description", "created_at", "kind", "maker_id")
_PROJECT_COLUMNS = ", ".join(_PROJECT_FIELDS)


def _find_account(con: sqlite3.Connection, account_id: str) -> Account | None:
    """The stored account with this id. Callers build index names from the stored id (through
    _index_name, which checks its shape), never from the id they were asked about."""
    row = con.execute(f"{_ACCOUNT_SELECT} WHERE accounts.id = ?", (account_id,)).fetchone()
    return _account(row) if row else None


def _insert_account(con: sqlite3.Connection, account: Account) -> None:
    con.execute(
        f"INSERT INTO accounts ({_ACCOUNT_COLUMNS}) VALUES (?, ?, ?)",
        tuple(getattr(account, field) for field in _ACCOUNT_FIELDS),
    )


def _require_account(con: sqlite3.Connection, account_id: str) -> None:
    """KeyError when the account is gone: a view can outlive its account, and must not leave
    a project, key or session behind that nobody owns."""
    if not con.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone():
        raise KeyError(account_id)


def _account(row: tuple) -> Account:
    """An account from a row of _ACCOUNT_SELECT."""
    return Account(**dict(zip((*_ACCOUNT_FIELDS, "plan"), row, strict=True)))
