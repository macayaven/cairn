"""A page's history bound without plans (Q1 B): how many versions of each page a save keeps
when no plan says, the record of what the last `serve` ran with, and the removal a
compaction makes by that record.

- `bound_history(N)`: every save on any view of this connection keeps the newest N versions
  of the page it saved, the oldest removed in the save's own transaction (usage's
  _keep_versions), unless a plan resolves the view's caps (a view limited with `resolve`):
  then the plan's versions_per_page rules, as before Q1. None: every version. Reading never
  removes a version. It is held in memory, for this connection only, and written nowhere.
- `record_history_bound(N)` or `record_history_bound(plans={name: N or None}, default=name)`:
  what a server runs with, a row of the default workspace's settings no setting call reads
  or changes (base.KEEP_VERSIONS). Each record replaces the one before.
- `prune_by_record()`: removes, in one transaction, the versions the last record says no
  page keeps: every page's newest N; with plans, each workspace's by the plan in force
  (Store.plan_in_force's rule; the record's default for none). A workspace on a plan the
  record does not name keeps every version and is named in what it answers. With no record
  it removes nothing. Never a page's latest version.
- `pages_over(N)`: how many pages hold more than N versions, from one statement that reads
  no body.

All four are the operator's (cairn.store.reach: MANAGEMENT and OTHER_WORKSPACES): a view
acting for a person, or narrowed, is refused them.
"""

import dataclasses
import json
import sqlite3
from collections.abc import Mapping

from cairn.store.base import DEFAULT_ACCOUNT_ID, KEEP_VERSIONS, StoreBase
from cairn.store.usage import _plan_in_force

_BOUND = "how many versions each page keeps is a whole number, 1 or more"

# The versions of a page past the newest ?1, in the projects of workspace ?2 (every project when
# ?2 is NULL): the statement a save runs for one page (usage._keep_versions), for many.
_PAST_THE_BOUND = (
    "DELETE FROM page_versions WHERE (?2 IS NULL OR project_id IN"
    " (SELECT id FROM projects WHERE account_id = ?2))"
    " AND version <= (SELECT MAX(p.version) FROM page_versions AS p"
    " WHERE p.project_id = page_versions.project_id AND p.slug = page_versions.slug) - ?1"
)


@dataclasses.dataclass(frozen=True)
class Pruned:
    """What prune_by_record did: whether a server had recorded a bound, how many versions it
    removed, the workspaces it left alone because the record does not name their plan, and
    the record it went by: `keep` (None: every version) or `by_plans`."""

    recorded: bool
    removed: int
    left_alone: tuple[str, ...] = ()
    keep: int | None = None
    by_plans: bool = False


def _checked(keep: object) -> int | None:
    if keep is None:
        return None
    if type(keep) is not int or keep < 1:
        raise ValueError(_BOUND)
    return keep


class HistoryMethods(StoreBase):
    def bound_history(self, keep: int | None) -> None:
        """From now on a save on any view of this connection keeps the newest `keep`
        versions of its page when no plan resolves the view's caps; None: every version.
        Removes nothing now. ValueError: not None or a whole number of at least 1."""
        self._state.keep_versions = _checked(keep)

    def record_history_bound(
        self,
        keep: int | None = None,
        *,
        plans: Mapping[str, int | None] | None = None,
        default: str | None = None,
    ) -> None:
        """Record what a server runs with: `keep` (None: every version), or with `plans`
        each plan's versions_per_page by name and the name of the `default` plan. It replaces
        the last record. ValueError: a bound that is not 1 or more, `keep` beside `plans`,
        or a default that is not one of the plans."""
        if plans is None:
            record: dict = {"keep": _checked(keep)}
        else:
            if keep is not None:
                raise ValueError("with plans, each plan says how many versions it keeps")
            if default not in plans:
                raise ValueError("the default plan is one of the plans")
            record = {
                "plans": {str(name): _checked(n) for name, n in plans.items()},
                "default": default,
            }
        value = json.dumps(record, sort_keys=True)
        with self._write() as con:
            con.execute(
                "INSERT INTO settings (account_id, name, value, updated_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (account_id, name)"
                " DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (DEFAULT_ACCOUNT_ID, KEEP_VERSIONS, value, self._stamp()),
            )

    def pages_over(self, keep: int) -> int:
        """How many pages, in every workspace, hold more than `keep` versions. One read."""
        keep = _checked(keep)  # type: ignore[assignment]
        if keep is None:
            raise ValueError(_BOUND)
        with self._read() as con:
            return con.execute(
                "SELECT COUNT(*) FROM (SELECT 1 FROM page_versions GROUP BY project_id, slug"
                " HAVING COUNT(*) > ?)",
                (keep,),
            ).fetchone()[0]

    def prune_by_record(self) -> Pruned:
        """Remove what the last record_history_bound says no page keeps, in one
        transaction: a failure removes nothing. With no record, nothing."""
        with self._write() as con:
            row = con.execute(
                "SELECT value FROM settings WHERE account_id = ? AND name = ?",
                (DEFAULT_ACCOUNT_ID, KEEP_VERSIONS),
            ).fetchone()
            if row is None:
                return Pruned(recorded=False, removed=0)
            record = json.loads(row[0])
            if "plans" not in record:
                keep = record["keep"]
                removed = 0 if keep is None else _remove(con, keep, None)
                return Pruned(recorded=True, removed=removed, keep=keep)
            return self._prune_by_plans(con, record["plans"], record["default"])

    def _prune_by_plans(
        self, con: sqlite3.Connection, plans: dict[str, int | None], default: str
    ) -> Pruned:
        removed, left_alone = 0, []
        for (account_id,) in con.execute("SELECT id FROM accounts ORDER BY rowid").fetchall():
            name = _plan_in_force(con, account_id) or default
            if name not in plans:
                left_alone.append(account_id)
            elif plans[name] is not None:
                removed += _remove(con, plans[name], account_id)
        return Pruned(True, removed, tuple(left_alone), by_plans=True)


def _remove(con: sqlite3.Connection, keep: int, account_id: str | None) -> int:
    return con.execute(_PAST_THE_BOUND, (keep, account_id)).rowcount
