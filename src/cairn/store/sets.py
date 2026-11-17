"""Project sets: named groups of a workspace's projects. A connection whose reach names a set
covers whatever the set holds when each request is served (cairn.store.reach), so creating,
changing and deleting a set is recorded in the audit log in the same write, under whoever
did it: set.created, set.changed and set.deleted, each with the set's name and the ids of
the projects it holds (after the change; when deleted, those it held). list_audit leaves out
of `projects` any project the reader does not reach."""

import json
import secrets
import sqlite3
from collections.abc import Iterable

from cairn.store.base import StoreBase, _require_account
from cairn.store.memory import PROJECT_NAME
from cairn.store.models import ProjectSet

SET_ID_PREFIX = "set_"
_SET_COLUMNS = "id, name, project_ids, created_at"
NOT_A_SET_MANAGER = "only the workspace's owners and admins make, change or delete its sets"


class SetMethods(StoreBase):
    def create_set(self, name: str, project_ids: Iterable[str] = ()) -> ProjectSet:
        """A new set of this workspace's projects, kept in the order given (once each).
        ValueError: a name that breaks the project-name rules, starts with "set_" (the id
        prefix), or is taken in this workspace ignoring case; an id that is not a project of
        this workspace or not in the view's reach. Creating, changing and deleting sets is a
        manager's call (StoreBase._manages; PermissionError otherwise)."""
        _check_set_name(name)
        wanted = list(dict.fromkeys(project_ids))
        made = ProjectSet(
            id=SET_ID_PREFIX + secrets.token_hex(6),
            name=name,
            project_ids=tuple(wanted),
            created_at=self._stamp(),
        )
        with self._write() as con:
            _require_account(con, self._account_id)
            self._require_manager(con, NOT_A_SET_MANAGER)
            _require_projects(con, self._account_id, wanted, self._reach(con))
            taken = con.execute(
                "SELECT 1 FROM project_sets WHERE account_id = ? AND name = ?",
                (self._account_id, name),
            )
            if taken.fetchone():
                raise ValueError(f"set name already used: {name!r}")
            con.execute(
                f"INSERT INTO project_sets ({_SET_COLUMNS}, account_id) VALUES (?, ?, ?, ?, ?)",
                (made.id, made.name, json.dumps(wanted), made.created_at, self._account_id),
            )
            self._audit(con, "set.created", detail={"set": made.name, "projects": wanted})
        return made

    def list_sets(self) -> list[ProjectSet]:
        """This workspace's sets, by name ignoring case, each showing the projects of it the
        view reaches."""
        with self._read() as con:
            rows = con.execute(
                f"SELECT {_SET_COLUMNS} FROM project_sets WHERE account_id = ? ORDER BY name",
                (self._account_id,),
            ).fetchall()
            reach = self._reach(con)
            return [_set(con, self._account_id, row, reach) for row in rows]

    def get_set(self, ref: str) -> ProjectSet | None:
        """By id or by name (ignoring case); an id match wins."""
        with self._read() as con:
            row = con.execute(
                f"SELECT {_SET_COLUMNS} FROM project_sets"
                " WHERE account_id = ? AND (id = ? OR name = ?) ORDER BY id = ? DESC LIMIT 1",
                (self._account_id, ref, ref, ref),
            ).fetchone()
            return _set(con, self._account_id, row, self._reach(con)) if row else None

    def update_set(self, set_id: str, project_ids: Iterable[str]) -> ProjectSet:
        """Replace the set's projects the view reaches; the projects it holds that the view does
        not reach stay in it. KeyError: no such set in this workspace. ValueError: an id that
        is not a project of this workspace or not in the view's reach."""
        wanted = list(dict.fromkeys(project_ids))
        with self._write() as con:
            self._require_manager(con, NOT_A_SET_MANAGER)
            row = con.execute(
                f"SELECT {_SET_COLUMNS} FROM project_sets WHERE id = ? AND account_id = ?",
                (set_id, self._account_id),
            ).fetchone()
            if row is None:
                raise KeyError(set_id)
            reach = self._reach(con)
            _require_projects(con, self._account_id, wanted, reach)
            if reach is not None:  # what the view cannot see of the set, it cannot drop
                held = _set(con, self._account_id, row, None).project_ids
                wanted += [project_id for project_id in held if project_id not in reach]
            stored = json.dumps(wanted)
            con.execute("UPDATE project_sets SET project_ids = ? WHERE id = ?", (stored, set_id))
            self._audit(con, "set.changed", detail={"set": row[1], "projects": wanted})
            return _set(con, self._account_id, (row[0], row[1], stored, row[3]), reach)

    def delete_set(self, set_id: str) -> bool:
        """Remove a set of this workspace; True when there was one. A reach that named it
        simply no longer covers its projects."""
        with self._write() as con:
            self._require_manager(con, NOT_A_SET_MANAGER)
            row = con.execute(
                "SELECT name, project_ids FROM project_sets WHERE id = ? AND account_id = ?",
                (set_id, self._account_id),
            ).fetchone()
            if row is None:
                return False
            con.execute(
                "DELETE FROM project_sets WHERE id = ? AND account_id = ?",
                (set_id, self._account_id),
            )
            detail = {"set": row[0], "projects": json.loads(row[1])}
            self._audit(con, "set.deleted", detail=detail)
            return True


def _check_set_name(name: str) -> None:
    if not isinstance(name, str) or not PROJECT_NAME.fullmatch(name):
        raise ValueError(f"invalid set name: {name!r}")
    if name.casefold().startswith(("br_", SET_ID_PREFIX)):  # a name never reads as an id
        raise ValueError(f"set names may not start with 'br_' or 'set_': {name!r}")


def _require_projects(
    con: sqlite3.Connection,
    account_id: str,
    project_ids: list[str],
    reach: frozenset[str] | None,
) -> None:
    """ValueError unless every id is a project of the account in `reach` (None: every one)."""
    found = {
        project_id
        for (project_id,) in con.execute(
            "SELECT id FROM projects WHERE account_id = ?"
            " AND id IN (SELECT value FROM json_each(?))",
            (account_id, json.dumps(project_ids)),
        )
        if reach is None or project_id in reach
    }
    if missing := [project_id for project_id in project_ids if project_id not in found]:
        raise ValueError(f"not a project of this workspace: {missing[0]!r}")


def _set(
    con: sqlite3.Connection, account_id: str, row: tuple, reach: frozenset[str] | None
) -> ProjectSet:
    """A stored set, holding only the projects that still exist and are in `reach` (None:
    every project)."""
    set_id, name, project_ids, created_at = row
    existing = {
        project_id
        for (project_id,) in con.execute(
            "SELECT id FROM projects WHERE account_id = ?"
            " AND id IN (SELECT value FROM json_each(?))",
            (account_id, project_ids),
        )
        if reach is None or project_id in reach
    }
    kept = tuple(project_id for project_id in json.loads(project_ids) if project_id in existing)
    return ProjectSet(id=set_id, name=name, project_ids=kept, created_at=created_at)
