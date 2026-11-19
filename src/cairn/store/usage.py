"""Metering and limits (H6): what a workspace stores and uses, counted in the transaction
of the write it belongs to, by UTC month; the name of the plan a workspace is on; and the
limited view, which refuses a write that would pass a cap inside that same transaction.

Metering is always on, hosted or not: it costs a stored note two statements (the month's row
and the workspace's stored bytes, both upserts) and a read nothing. A limited view adds one
read to a new note (the month's notes and the stored bytes, in one statement) and one to a
new project (the workspace's projects), in the write's transaction, so writers in parallel, in
one process or several on one file, cannot pass a cap between them. Which plan a name
stands for, and what it allows, is the server's configuration (cairn.plans), never the
database's.
"""

import copy
import dataclasses
import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cairn.spans import Span, chunk
from cairn.store.base import StoreBase, _find_account, _require_account
from cairn.store.models import AccountSummary, Usage

if TYPE_CHECKING:
    from cairn.store import Store

PERIOD = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])")  # a UTC month, "YYYY-MM"
PLAN_NAME_MAX = 64  # characters of a plan's name, as a workspace records it
_USAGE_FIELDS = tuple(Usage.model_fields)[1:]  # the counts, after the period


def period_of(moment: datetime) -> str:
    """The UTC month of an aware datetime, "YYYY-MM"."""
    return f"{moment.astimezone(UTC):%Y-%m}"


def month_after(period: str) -> datetime:
    """The first instant of the UTC month after `period`: when that month's counts reset."""
    year, month = (int(part) for part in period.split("-"))
    return datetime(year + month // 12, month % 12 + 1, 1, tzinfo=UTC)


def check_plan_name(plan: object) -> None:
    """ValueError unless `plan` is a plan's name as a workspace records it: 1 to
    PLAN_NAME_MAX characters without surrounding blanks."""
    if (
        not isinstance(plan, str)
        or not plan.strip()
        or plan != plan.strip()
        or len(plan) > PLAN_NAME_MAX
    ):
        raise ValueError(
            f"a plan's name has 1 to {PLAN_NAME_MAX} characters and no blank at either end:"
            f" {plan!r}"
        )


def _plan_in_force(con: sqlite3.Connection, account_id: str) -> str | None:
    """Store.plan_in_force on `con`: the one rule, read by plan_in_force and, inside each
    write a limited view admits, by _caps and by the history's prune. KeyError: no such
    workspace."""
    row = con.execute(
        "SELECT account_quota.plan FROM accounts"
        " LEFT JOIN account_quota ON account_quota.account_id = accounts.id"
        " WHERE accounts.id = ?",
        (account_id,),
    ).fetchone()
    if row is None:
        raise KeyError(account_id)
    return row[0]


def passages_of(text: str) -> int:
    """How many passages a note's text holds: its spans of PASSAGE_KINDS (each a claim once
    compiled). What the passages cap charges a note is charge_of its spans."""
    return sum(span.kind in PASSAGE_KINDS for span in chunk("cap_000000000000", text))


def path_bytes(heading_path: Sequence[str]) -> int:
    """The bytes of a heading path as stored, less JSON's brackets, quotes and separators:
    each heading as JSON writes it in UTF-8, which is its UTF-8 bytes for ordinary text and
    more for what JSON escapes (a quote or backslash 2, a control character 6). What
    HEADINGS_MAX bounds and what the passages cap charges."""
    return sum(
        len(json.dumps(heading, ensure_ascii=False).encode()) - 2 for heading in heading_path
    )


def charge_of(spans: Iterable[Span]) -> int:
    """What the passages cap charges for these spans (what `passages()` counts once they are
    written): one for each passage (PASSAGE_KINDS), and one for every full BYTES_PER_PASSAGE
    bytes of the heading path stored with each span (path_bytes), passages and headings
    alike. Every row a note makes is so paid for, by the note's bytes or by this."""
    return sum(
        (span.kind in PASSAGE_KINDS) + path_bytes(span.heading_path) // BYTES_PER_PASSAGE
        for span in spans
    )


def _whole(value: object, name: str) -> int:
    """`value` when it is a whole number of at least 0 (a bool is not one); ValueError."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} is a whole number of at least 0, not {value!r}")
    return value


# The kinds of span that are passages: what the compiler makes a claim of (its CONTENT_KINDS,
# which builds on the store), as the schema's counters count them (schema._V16_PASSAGE).
PASSAGE_KINDS = frozenset({"paragraph", "bullet", "code", "table", "quote"})
# Under a storage cap of S bytes a workspace holds at most S // BYTES_PER_PASSAGE passages
# (the limit "passages"): what is built of each passage (its span, its claim, its search
# rows) costs about the same whatever its length, so a note of tiny passages is bounded by
# their number, not their bytes. Ordinary notes run 80 to 300 bytes a passage. The heading
# path stored with every span is charged too, one for each full BYTES_PER_PASSAGE bytes of
# it (charge_of), so what `passages()` counts is charge_of every span the workspace holds.
BYTES_PER_PASSAGE = 40
# Under a storage cap of S bytes the page versions a workspace keeps take at most
# HISTORY_FACTOR * S bytes, each counted as its body and claims are stored: past it a save
# drops the workspace's oldest versions, never a page's latest (_keep_history).
HISTORY_FACTOR = 4
LIMITS = ("notes_per_month", "storage_bytes", "projects", "model_budget_micros")  # metered
CAPS = (*LIMITS, "versions_per_page")  # every field of Limits: the metered caps and a bound


@dataclasses.dataclass(frozen=True)
class Limits:
    """The caps a limited view holds a workspace to; None is no cap. ValueError: a cap that
    is not a whole number of at least 0 (at least 1 for versions_per_page, since the latest
    version of a page is always kept)."""

    notes_per_month: int | None = None  # new notes in a UTC month
    storage_bytes: int | None = None  # the UTF-8 size of every note the workspace holds
    projects: int | None = None  # projects the workspace has
    model_budget_micros: int | None = None  # model use a month, in millionths of a US dollar
    versions_per_page: int | None = None  # the versions of each page kept, the latest first

    def __post_init__(self) -> None:
        for name in CAPS:
            if (value := getattr(self, name)) is not None:
                _whole(value, name)
        if self.versions_per_page == 0:
            raise ValueError("versions_per_page is at least 1: the latest version is kept")

    def tighter(self, other: "Limits") -> "Limits":
        """Each cap the lower of the two; a cap of None gives way to the other's."""
        caps = {}
        for name in CAPS:
            given = [cap for cap in (getattr(self, name), getattr(other, name)) if cap is not None]
            caps[name] = min(given, default=None)
        return Limits(**caps)


NO_LIMITS = Limits()


class OverLimit(Exception):
    """A limited view refused a write: it would pass the cap `limit` (one of LIMITS, or
    "passages": the storage cap's passages, storage_bytes // BYTES_PER_PASSAGE), with
    `used` already used of `cap`. `resets_at`: the first instant of the next UTC month for
    the monthly cap, None for the others. `plan`: the name of the plan in force that the
    write read (Store.plan_in_force; None: the server's default) when a resolver gave the
    cap, else None, and `resolved` says which. Nothing was written."""

    def __init__(
        self,
        limit: str,
        used: int,
        cap: int,
        resets_at: datetime | None,
        plan: str | None = None,
        resolved: bool = False,
    ) -> None:
        self.limit, self.used, self.cap, self.resets_at = limit, used, cap, resets_at
        self.plan, self.resolved = plan, resolved
        super().__init__(f"over the workspace's {limit} limit: {used} of {cap} used")


Resolver = Callable[[str | None], Limits]  # a plan's name (None: the default) to its caps


class UsageMethods(StoreBase):
    _limits: Limits = NO_LIMITS  # the caps this view refuses past (`limited`)
    _resolvers: tuple[Resolver, ...] = ()  # the server's plans, asked inside each write

    def limited(self, limits: Limits = NO_LIMITS, *, resolve: Resolver | None = None) -> "Store":
        """A view of the same workspace, with every narrowing it has, that also refuses
        (OverLimit), inside the write's own transaction, a new note when the month's notes
        have reached `notes_per_month` or when it would take the stored bytes past
        `storage_bytes`, and a new project when the workspace has `projects` of them already.
        Limits only tighten: on a limited view each cap is the lower of the two. A note the
        workspace already holds is "unchanged", never over a limit. A limited view is bound
        (_bound): for_account returns it for its own workspace and refuses any other, so the
        caps cannot be shed. The checks and the meter run inside the store's own `_write`,
        after its membership check; no layer here opens a transaction of its own.

        `resolve` maps a plan's name to its Limits (the server's table: Plans.limits_of).
        With it, each write the view admits reads the workspace's plan in force inside its
        own transaction (the rule of Store.plan_in_force, on the same connection) and asks
        `resolve` for its caps, so a plan changed while a request or a source pass runs
        holds its next write; those caps tighten with `limits` like any other. TypeError:
        `limits` not Limits, or `resolve` not callable."""
        if not isinstance(limits, Limits):
            raise TypeError("limited takes a cairn.store.Limits")
        if resolve is not None and not callable(resolve):
            raise TypeError("limited's resolve maps a plan's name to its Limits")
        view = copy.copy(self)  # the same connection and lock, as for_account
        view._owner = False
        view._limits = self._limits.tighter(limits)
        if resolve is not None:
            view._resolvers = (*self._resolvers, resolve)
        return view  # type: ignore[return-value]

    def _bound(self) -> bool:
        """StoreBase._bound, and any cap: a limited view is held to its workspace, so
        for_account returns it for its own and refuses another (VIEW_IS_BOUND)."""
        return super()._bound() or self._limits != NO_LIMITS or bool(self._resolvers)

    def _caps(self, con: sqlite3.Connection) -> tuple[Limits, str | None]:
        """This view's caps for a write on `con`, inside its transaction: its own Limits,
        tightened by what each resolver gives for the plan in force read now; with the name
        of that plan (None without a resolver, or on the default)."""
        if not self._resolvers:
            return self._limits, None
        plan = _plan_in_force(con, self._account_id)
        caps = self._limits
        for resolve in self._resolvers:
            caps = caps.tighter(resolve(plan))
        return caps, plan

    def model_budget_left(self) -> int | None:
        """None without a model budget; else the month's budget less the model use recorded
        this month, never below 0. A read."""
        budget = self._limits.model_budget_micros
        if budget is None:
            return None
        return max(0, budget - self.usage().model_micros)

    def usage(self, period: str | None = None) -> Usage:
        """The workspace's use in the UTC month `period` ("YYYY-MM"; the current one when
        None): zero for a month with none. ValueError: another shape of period. A read."""
        if period is None:
            period = period_of(self._instant())
        if not isinstance(period, str) or not PERIOD.fullmatch(period):
            raise ValueError(f"a period is a month written YYYY-MM, not {period!r}")
        with self._read() as con:
            row = con.execute(
                f"SELECT {', '.join(_USAGE_FIELDS)} FROM usage WHERE account_id = ? AND period = ?",
                (self._account_id, period),
            ).fetchone()
        return Usage(period=period, **dict(zip(_USAGE_FIELDS, row or (0,) * 5, strict=True)))

    def stored_bytes(self) -> int:
        """The UTF-8 size of every note the workspace holds, whenever it was written: a
        running total kept by each write, never a count of the notes. A read."""
        with self._read() as con:
            return _stored(con, self._account_id)

    def passages(self) -> int:
        """What the passages cap charges for the workspace's notes, in every project: one for
        each passage (a span of a kind the compiler makes a claim of, PASSAGE_KINDS) and one
        for every full BYTES_PER_PASSAGE bytes of each span's heading path as stored
        (charge_of). A running total kept by each write. A read."""
        with self._read() as con:
            return _disk(con, self._account_id)[0]

    def history_bytes(self) -> int:
        """The bytes of every page version the workspace keeps, its pages' latest included:
        each version's body and claims as stored (UTF-8). A running total kept by each
        write. A read."""
        with self._read() as con:
            return _disk(con, self._account_id)[1]

    def project_count(self) -> int:
        """How many projects the workspace has, every one of them, whoever this view acts for
        reaches: what the projects cap counts. A read."""
        with self._read() as con:
            (count,) = con.execute(
                "SELECT COUNT(*) FROM projects WHERE account_id = ?", (self._account_id,)
            ).fetchone()
        return count

    def record_model_use(self, tokens_in: int, tokens_out: int, micros: int) -> None:
        """Add one model call to the current month: tokens in and out, and its price in
        millionths of a US dollar. Recorded whatever the budget: what was spent was spent.
        ValueError: a count that is not a whole number of at least 0, and nothing is
        written. KeyError: the view's workspace is gone."""
        counts = (
            _whole(tokens_in, "tokens_in"),
            _whole(tokens_out, "tokens_out"),
            _whole(micros, "micros"),
        )
        with self._write() as con:
            _require_account(con, self._account_id)
            con.execute(
                "INSERT INTO usage"
                " (account_id, period, model_tokens_in, model_tokens_out, model_micros)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT (account_id, period) DO UPDATE SET"
                " model_tokens_in = model_tokens_in + excluded.model_tokens_in,"
                " model_tokens_out = model_tokens_out + excluded.model_tokens_out,"
                " model_micros = model_micros + excluded.model_micros",
                (self._account_id, period_of(self._instant()), *counts),
            )

    def set_plan(self, account_id: str, plan: str | None) -> None:
        """Put a workspace on the plan named `plan`, or back on the default (None). Not
        scoped, like the other account methods: the operator sets plans. Which plans exist
        is the server's configuration, so any name is stored; one the server does not know
        reads as its default. KeyError: an unknown account. ValueError: a name that is not
        1 to PLAN_NAME_MAX characters without surrounding blanks. PermissionError: a view
        acting for a person, an owner included, and nothing is written."""
        if self._user_id is not None:
            raise PermissionError("only the operator sets a workspace's plan")
        if plan is not None:
            check_plan_name(plan)
        with self._write() as con:
            account = _find_account(con, account_id)
            if account is None:
                raise KeyError(account_id)
            con.execute(
                "INSERT INTO account_quota (account_id, plan) VALUES (?, ?)"
                " ON CONFLICT (account_id) DO UPDATE SET plan = excluded.plan",
                (account.id, plan),
            )

    def plan_in_force(self, account_id: str) -> str | None:
        """The name of the plan a workspace is held to: the one the operator set with
        set_plan (Account.plan), None for the server's default. One read, by the
        workspace's id; it writes nothing. Not scoped, like get_account. KeyError: an
        unknown workspace."""
        with self._read() as con:
            return _plan_in_force(con, account_id)

    def list_account_summaries(self) -> list[AccountSummary]:
        """Every workspace, in creation order (the default one first), with how many people
        belong to it and the plan it is held to (plan_in_force's rule). One read for them
        all; it writes nothing. Not scoped: the operator's, like list_accounts. Never an
        address."""
        with self._read() as con:
            rows = con.execute(
                "SELECT accounts.id, accounts.name, accounts.created_at,"
                " (SELECT COUNT(*) FROM memberships AS m WHERE m.account_id = accounts.id),"
                " account_quota.plan FROM accounts"
                " LEFT JOIN account_quota ON account_quota.account_id = accounts.id"
                " ORDER BY accounts.seq"
            ).fetchall()
        fields = ("id", "name", "created_at", "members", "plan")
        return [AccountSummary(**dict(zip(fields, row, strict=True))) for row in rows]

    def _admit_note(
        self, con: sqlite3.Connection, size: int, at: datetime, new: int = 1, passages: int = 0
    ) -> None:
        """OverLimit when `new` notes of `size` UTF-8 bytes and `passages` passages in all,
        stored at `at`, would pass a cap of this view, checked on `con` inside the write that
        would store them (one note for add_note, an archive's notes for import_export):
        the month's notes, the stored bytes, then the passages the storage cap allows. The
        caps are the workspace's: what is counted is every note of the workspace, whatever
        projects the view reaches."""
        caps, plan = self._caps(con)
        if caps.notes_per_month is None and caps.storage_bytes is None:
            return
        period = period_of(at)
        notes, stored = con.execute(
            "SELECT (SELECT notes FROM usage WHERE account_id = ?1 AND period = ?2),"
            " (SELECT stored_bytes FROM account_quota WHERE account_id = ?1)",
            (self._account_id, period),
        ).fetchone()
        notes, stored = notes or 0, stored or 0
        resolved = bool(self._resolvers)
        if caps.notes_per_month is not None and notes + new > caps.notes_per_month:
            reset = month_after(period)
            raise OverLimit("notes_per_month", notes, caps.notes_per_month, reset, plan, resolved)
        if caps.storage_bytes is not None and stored + size > caps.storage_bytes:
            raise OverLimit("storage_bytes", stored, caps.storage_bytes, None, plan, resolved)
        if caps.storage_bytes is not None:
            held, cap = _disk(con, self._account_id)[0], caps.storage_bytes // BYTES_PER_PASSAGE
            if held + passages > cap:
                raise OverLimit("passages", held, cap, None, plan, resolved)

    def _look_before_screening(self, project_id: str, text: str, source_ref: str | None) -> None:
        """OverLimit, before the guard screens a note, when this view's caps already refuse
        it: one read of the counters, so a workspace at its cap is told so cheaply, however
        often it asks. A note the project already holds (same text and source_ref) passes, as
        it is "unchanged", and so does a project out of reach (the write says KeyError). Only
        a look: the check inside the write's own transaction stays the authority."""
        if self._limits == NO_LIMITS and not self._resolvers:
            return
        encoded = text.encode()
        with self._read() as con:
            try:
                self._require_project(con, project_id)
            except (KeyError, PermissionError):
                return  # the write says so, as it would without a cap
            held = con.execute(
                "SELECT 1 FROM notes WHERE project_id = ? AND text_sha256 = ?"
                " AND source_ref IS ? LIMIT 1",
                (project_id, hashlib.sha256(encoded).hexdigest(), source_ref),
            ).fetchone()
            if not held:
                charge = charge_of(chunk("cap_000000000000", text))
                self._admit_note(con, len(encoded), self._instant(), 1, charge)

    def _admit_projects(self, con: sqlite3.Connection, new: int) -> None:
        """OverLimit when `new` more projects would pass this view's cap on projects, checked
        on `con` inside the write that would make them."""
        if new == 0:
            return
        caps, plan = self._caps(con)
        if caps.projects is None:
            return
        (count,) = con.execute(
            "SELECT COUNT(*) FROM projects WHERE account_id = ?", (self._account_id,)
        ).fetchone()
        if count + new > caps.projects:
            raise OverLimit("projects", count, caps.projects, None, plan, bool(self._resolvers))

    def _keep_versions(self, con: sqlite3.Connection, project_id: str, slug: str | None) -> None:
        """Drop, on `con` inside the write that saved them, the versions of page `slug` of
        the project (of each of its pages when None) beyond this view's versions_per_page,
        the oldest first: the latest is always kept, and since a page's next version is its
        highest plus one, version numbers never start again. Without a plan (no resolver),
        the server's bound (Q1; cairn.store.history.bound_history) holds too, the lower of
        the two; with one, the plan's alone."""
        keep = self._caps(con)[0].versions_per_page
        bound = self._state.keep_versions
        if bound is not None and not self._resolvers:
            keep = bound if keep is None else min(keep, bound)
        if keep is None:
            return
        con.execute(
            "DELETE FROM page_versions WHERE project_id = ?1 AND (?2 IS NULL OR slug = ?2)"
            " AND version <= (SELECT MAX(p.version) FROM page_versions AS p"
            " WHERE p.project_id = page_versions.project_id AND p.slug = page_versions.slug) - ?3",
            (project_id, slug, keep),
        )

    def _keep_history(self, con: sqlite3.Connection) -> None:
        """Under a storage cap S, drop on `con`, inside the write that saved them, the
        workspace's oldest page versions (by when each was saved, across its projects) until
        what its versions take is at most HISTORY_FACTOR * S bytes, or only each page's
        latest is left: a page's latest is never dropped, and since a page's next version is
        its highest plus one, version numbers never start again."""
        storage = self._caps(con)[0].storage_bytes
        if storage is None:
            return
        budget = HISTORY_FACTOR * storage
        held = _disk(con, self._account_id)[1]
        if held <= budget:
            return
        older = con.execute(
            "SELECT v.project_id, v.slug, v.version,"
            " LENGTH(CAST(v.body AS BLOB)) + LENGTH(CAST(v.claims AS BLOB))"
            " FROM page_versions AS v JOIN projects AS b ON b.id = v.project_id"
            " WHERE b.account_id = ? AND v.version < (SELECT MAX(p.version)"
            " FROM page_versions AS p WHERE p.project_id = v.project_id AND p.slug = v.slug)"
            " ORDER BY v.created_at, v.project_id, v.slug, v.version",
            (self._account_id,),
        ).fetchall()
        dropped = []
        for project_id, slug, version, size in older:
            if held <= budget:
                break
            dropped.append((project_id, slug, version))
            held -= size
        con.executemany(
            "DELETE FROM page_versions WHERE project_id = ? AND slug = ? AND version = ?", dropped
        )

    def _meter_note(self, con: sqlite3.Connection, size: int, at: datetime, notes: int = 1) -> None:
        """Count `notes` notes of `size` UTF-8 bytes in all, stored at `at`, on `con`, inside
        the write transaction that stores them (one note for add_note, every note of an
        archive for import_export): the notes and their bytes in the month of `at`, and
        their bytes in the workspace's total."""
        con.execute(
            "INSERT INTO usage (account_id, period, notes, note_bytes) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (account_id, period) DO UPDATE SET"
            " notes = notes + excluded.notes, note_bytes = note_bytes + excluded.note_bytes",
            (self._account_id, period_of(at), notes, size),
        )
        con.execute(
            "INSERT INTO account_quota (account_id, stored_bytes) VALUES (?, ?)"
            " ON CONFLICT (account_id) DO UPDATE SET"
            " stored_bytes = stored_bytes + excluded.stored_bytes",
            (self._account_id, size),
        )


def _disk(con: sqlite3.Connection, account_id: str) -> tuple[int, int]:
    """(passages, history bytes) of a workspace: what the schema's triggers count."""
    row = con.execute(
        "SELECT passages, history_bytes FROM account_disk WHERE account_id = ?", (account_id,)
    ).fetchone()
    return (row[0], row[1]) if row else (0, 0)


def _stored(con: sqlite3.Connection, account_id: str) -> int:
    row = con.execute(
        "SELECT stored_bytes FROM account_quota WHERE account_id = ?", (account_id,)
    ).fetchone()
    return row[0] if row else 0
