"""Who reaches a project (T1): its maker, its rule, and the reach of the person a view acts for.

A project has a maker, the person whose view made it (None for a key, the operator, or once
they leave), and a rule: `everyone`, what any member of the workspace gets ("write", "read"
or "none"), and `people`, user ids with "read", "write" or "manage" (the project_people
table). A person reaches a project with the most that any of these gives: the workspace's
owners write everywhere; the maker writes; then `people`; then `everyone`. A view that acts
for no person (the command line, the workspace's own key) reaches every project.

"manage" (T2) is "write" wherever a level is read (`_level` answers "write" for it, so every
write into a project and every source take it as one), and one thing more: the person reads
and changes that project's rule, as its maker and the owners do (`_may_change_rule`). It is a
person's level only, never `everyone`'s, and it neither deletes a project nor forgets a note
(cairn.store.erasure asks for the maker or an owner).

AccessMethods comes right after ReachMethods in Store's MRO. Its `_reach` answers the seam
StoreBase._reach with the person's reach, which ReachMethods then intersects with each
narrowing of a connection, so a view's reach is account -> person -> connection, each one
narrowing, and nothing else limits a view. It is computed from the data on the connection of
the section that uses it, at every call: a rule or role changed meanwhile is seen at once,
and a person who is no longer a member reaches nothing. Writes into a project ask
`_require_project` (MemoryMethods), which this class extends with the person's level: out of
reach is KeyError, as for an unknown project; reached to read is PermissionError.
"""

import json
import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any

from cairn.store.base import StoreBase
from cairn.store.models import ProjectAccess, ProjectSharing, Shared

LEVELS = ("write", "read", "none")  # what `everyone` can give
PERSON_ACCESS = ("manage", "write", "read")  # what `people` can give
_PERSON_LEVELS = "'manage', 'write' or 'read'"
SHARE_MAX = 200  # addresses in one share_project call
ADDRESS_MAX = 254  # characters in one address, the most an email address can hold
# ASCII letters only, as SQLite's NOCASE folds them (users.email is COLLATE NOCASE).
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
READ_ONLY_PROJECT = "this project is read-only for you: you can read it but not write to it"
# Why a source went off when a project it wrote into was made private (set_project_access).
PROJECT_PRIVATE = "the project it wrote into was made private by someone who is not an owner"
NOT_THE_MAKER = (
    "only the project's maker, the workspace's owners and the people it gives 'manage' see or"
    " change who reaches it"
)
# Whether a project's pages may be sent to a model the operator connects (M4): "allowed" or
# "never", as its people chose, or not decided when nobody did (Privacy 4). A choice is one row
# of the workspace's settings, named MODELS_PREFIX and its id, holding "allowed" or "never";
# a project nobody decided for has no row. Whether a project is kept from every model is
# whether its row holds "never", never whether it has one. Not a setting the setting calls
# know (cairn.store.audit.SETTINGS): get_setting and set_setting refuse the name.
MODELS = ("allowed", "never")
MODELS_PREFIX = "project.models:"


class KeptFromModels(Exception):
    """A model call or the save of what a model answered, refused in its own transaction
    because the project is at "never" (M4): nothing of it was written."""


_MODELS_CHOICES = (  # the ids in a JSON array (?2) with a choice, of the account ?1, and it
    "SELECT substr(name, length(?3) + 1), value FROM settings WHERE account_id = ?1"
    " AND name IN (SELECT ?3 || value FROM json_each(?2))"
)
_MODELS_NAMES = (  # the ids among them at "never"
    "SELECT substr(name, length(?3) + 1) FROM settings WHERE account_id = ?1"
    " AND name IN (SELECT ?3 || value FROM json_each(?2)) AND value = 'never'"
)
# Each project of an account, and the level the person (?1) has there by its maker and rule:
# "manage" is "write" here (deciding who reaches it is _may_change_rule's question).
_LEVELS = (
    "SELECT b.id, CASE"
    " WHEN b.maker_id = ?1 OR p.access IN ('manage', 'write') OR b.everyone = 'write'"
    " THEN 'write'"
    " WHEN p.access = 'read' OR b.everyone = 'read' THEN 'read'"
    " ELSE 'none' END"
    " FROM projects AS b LEFT JOIN project_people AS p ON p.project_id = b.id AND p.user_id = ?1"
    " WHERE b.account_id = ?2"
)


class AccessMethods(StoreBase):
    # The seam: the person's reach, which every narrowing then intersects

    def _reach(self, con: sqlite3.Connection) -> frozenset[str] | None:
        """What the next class answers (the account: every project), intersected with the
        projects the view's person reaches now, read on `con`."""
        reach = super()._reach(con)
        mine = self._person_reach(con)
        if mine is None:
            return reach
        return mine if reach is None else reach & mine

    def _person_reach(self, con: sqlite3.Connection) -> frozenset[str] | None:
        """The projects the view's person reaches, to read or write; None when that is every
        project (no person, an owner, or a member every project's rule lets in), and nothing for
        a person who is no longer a member."""
        if self._user_id is None:
            return None
        role = self._role_in(con)
        if role is None:
            return frozenset()
        if role == "owner":
            return None
        levels = con.execute(_LEVELS, (self._user_id, self._account_id)).fetchall()
        reached = frozenset(project_id for project_id, level in levels if level != "none")
        return None if len(reached) == len(levels) else reached

    def _writable(self, con: sqlite3.Connection) -> frozenset[str] | None:
        """StoreBase._writable: the projects the view's person writes to (as `_level` reads
        "write", "manage" included), in two statements whatever the workspace holds; None
        when that is every project (no person, or an owner); nothing for a person who is no
        longer a member."""
        if self._user_id is None:
            return None
        role = self._role_in(con)
        if role is None:
            return frozenset()
        if role == "owner":
            return None
        levels = con.execute(_LEVELS, (self._user_id, self._account_id)).fetchall()
        return frozenset(project_id for project_id, level in levels if level == "write")

    def _level(self, con: sqlite3.Connection, project_id: str) -> str:
        """What the view's person may do in a project of the account: "write" (for "manage"
        too), "read" or "none" (also for a project that is not the account's)."""
        if self._user_id is None:
            return "write"
        role = self._role_in(con)
        if role is None:
            return "none"
        if role == "owner":
            return "write"
        row = con.execute(
            _LEVELS + " AND b.id = ?3", (self._user_id, self._account_id, project_id)
        ).fetchone()
        return row[1] if row else "none"

    def _require_project(self, con: sqlite3.Connection, project_id: str) -> None:
        """As MemoryMethods._require_project (KeyError unless the project is the account's and in
        the view's reach), and then PermissionError unless the view's person may write there."""
        super()._require_project(con, project_id)
        if self._level(con, project_id) != "write":
            raise PermissionError(READ_ONLY_PROJECT)

    def _reached(self, con: sqlite3.Connection, project_id: str) -> tuple[str, str | None, str]:
        """(name, maker, everyone) of a project in the view's reach; KeyError otherwise, as for
        a project that does not exist."""
        row = con.execute(
            "SELECT name, maker_id, everyone FROM projects WHERE id = ? AND account_id = ?",
            (project_id, self._account_id),
        ).fetchone()
        reach = self._reach(con)
        if row is None or (reach is not None and project_id not in reach):
            raise KeyError(project_id)
        return row

    def _may_change_rule(
        self, con: sqlite3.Connection, project_id: str, maker_id: str | None
    ) -> None:
        """PermissionError unless the view acts for no person, for the project's maker, for an
        owner of the workspace, or for a person the project's rule gives "manage". Asked after
        _reached, so the person is a member who reaches the project."""
        if self._user_id is None or self._user_id == maker_id:
            return
        if self._role_in(con) == "owner":
            return
        held = con.execute(
            "SELECT access FROM project_people WHERE project_id = ? AND user_id = ?",
            (project_id, self._user_id),
        ).fetchone()
        if held != ("manage",):
            raise PermissionError(NOT_THE_MAKER)

    # A project's rule

    def project_access(self, project_id: str) -> ProjectAccess:
        """Who reaches a project besides the owners: its maker, `everyone` and `people`.
        KeyError for a project that is unknown or out of the view's reach; PermissionError for
        a person who is neither its maker, nor an owner, nor at "manage" on it."""
        with self._read() as con:
            _, maker_id, everyone = self._reached(con, project_id)
            self._may_change_rule(con, project_id, maker_id)
            people = _people(con, project_id)
        return ProjectAccess(maker_id=maker_id, everyone=everyone, people=people)  # type: ignore[arg-type]

    def project_sharing(self, project_id: str) -> ProjectSharing:
        """How far a project is shared, for anyone who reaches it: what any member gets, and
        the people its rule names. KeyError for a project unknown or out of the view's reach.
        A read: it writes nothing."""
        with self._read() as con:
            _, maker_id, everyone = self._reached(con, project_id)
            named = {maker_id, *_people(con, project_id)} - {None}
        return ProjectSharing(everyone=everyone, people=frozenset(named))  # type: ignore[arg-type]

    def projects_sharing(self, project_ids: Iterable[str]) -> dict[str, ProjectSharing]:
        """project_sharing for each of `project_ids` in the view's reach, by id, in one read of
        two statements whatever their number (Q1: the projects list); a project unknown or out
        of reach is left out, where project_sharing raises KeyError. A read."""
        wanted = json.dumps(list(dict.fromkeys(project_ids)))
        with self._read() as con:
            reach = self._reach(con)
            rows = con.execute(
                "SELECT id, maker_id, everyone FROM projects"
                " WHERE account_id = ? AND id IN (SELECT value FROM json_each(?))",
                (self._account_id, wanted),
            ).fetchall()
            people: dict[str, set[str]] = {}
            for project_id, user_id in con.execute(
                "SELECT project_id, user_id FROM project_people"
                " WHERE project_id IN (SELECT value FROM json_each(?))",
                (wanted,),
            ):
                people.setdefault(project_id, set()).add(user_id)
        return {
            project_id: ProjectSharing(
                everyone=everyone,
                people=frozenset({maker_id, *people.get(project_id, ())} - {None}),
            )
            for project_id, maker_id, everyone in rows
            if reach is None or project_id in reach
        }

    def set_project_access(
        self,
        project_id: str,
        *,
        everyone: str | None = None,
        people: Mapping[str, str] | None = None,
    ) -> ProjectAccess:
        """Replace what is given of a project's rule (`people` whole), recorded as
        project.access_changed when anything changed. ValueError, before anything is read: an
        `everyone` other than "write", "read" or "none", or a level in `people` other than
        "manage", "write" or "read". Then, in the write: KeyError for a project that is unknown
        or out of reach; PermissionError unless the view acts for no person, the maker, an
        owner or a person at "manage" on it; ValueError for a person in `people` who is not a
        member. Nothing is written on an error. A view that is neither an owner's nor acting
        for no person that takes `everyone` from "write" or "read" to "none" also takes the
        project out of every source that names it, as deleting it does (cairn.store.sources:
        only an owner sends a source where not every member reaches; reason PROJECT_PRIVATE)."""
        if everyone is not None and everyone not in LEVELS:
            raise ValueError(f"everyone is one of {', '.join(LEVELS)}, not {everyone!r}")
        wanted = None if people is None else dict(people)
        for user_id, level in (wanted or {}).items():
            if not isinstance(user_id, str) or level not in PERSON_ACCESS:
                raise ValueError(f"a person gets {_PERSON_LEVELS}, not {level!r}")
        with self._write() as con:
            name, maker_id, held = self._reached(con, project_id)
            self._may_change_rule(con, project_id, maker_id)
            if wanted:
                members = {
                    user_id
                    for (user_id,) in con.execute(
                        "SELECT user_id FROM memberships WHERE account_id = ?"
                        " AND user_id IN (SELECT value FROM json_each(?))",
                        (self._account_id, json.dumps(list(wanted))),
                    )
                }
                if strangers := [user_id for user_id in wanted if user_id not in members]:
                    raise ValueError(f"not a member of this workspace: {strangers[0]!r}")
            before = (held, _people(con, project_id))
            after = (everyone or held, before[1] if wanted is None else wanted)
            narrowed = held != "none" and after[0] == "none"
            if narrowed and not self._decides_destinations(con):
                self._forget_destination(con, name, PROJECT_PRIVATE)
            self._replace_rule(con, project_id, name, before, after)
        return ProjectAccess(maker_id=maker_id, everyone=after[0], people=after[1])  # type: ignore[arg-type]

    def share_project(self, project_id: str, level: str, emails: Iterable[str]) -> Shared:
        """Give `level` ("manage", "write" or "read") on a project to every member of the
        workspace whose address is in `emails` (compared as users.email is: ASCII letters in
        either case, outer spaces dropped), and change nothing else of its rule. The maker
        and the owners, who reach it whatever the rule says, are granted and get no entry.
        An address that is no member's changes nothing and invites nobody: it comes back in
        `unknown`, as typed less its outer spaces. One project.access_changed, as
        set_project_access records it, when anything changed; none otherwise.

        ValueError, before anything is read: a level not one of the three; `emails` one
        string, empty, longer than SHARE_MAX or not a collection; an entry that is not one
        address (not a string, blank, no "@", a space inside, or longer than ADDRESS_MAX),
        named by its position, never by what it holds. Then, as set_project_access: KeyError
        for a project unknown or out of reach, PermissionError unless the view acts for no
        person, the maker, an owner or a person at "manage" on it. A narrowed view is
        refused before all of these (cairn.store.reach: a management write)."""
        if level not in PERSON_ACCESS:
            raise ValueError(f"a person gets {_PERSON_LEVELS}, not {level!r}")
        typed = _addresses(emails)
        with self._write() as con:
            name, maker_id, everyone = self._reached(con, project_id)
            self._may_change_rule(con, project_id, maker_id)
            folded = [address.translate(_ASCII_LOWER) for address in typed]
            members = {
                email.translate(_ASCII_LOWER): (user_id, role)
                for email, user_id, role in con.execute(
                    "SELECT u.email, u.id, m.role FROM users AS u"
                    " JOIN memberships AS m ON m.user_id = u.id AND m.account_id = ?"
                    " WHERE u.email IN (SELECT value FROM json_each(?))",
                    (self._account_id, json.dumps(sorted(set(folded)))),
                )
            }
            granted: dict[str, str] = {}  # user id -> role, in the order typed
            unknown: dict[str, str] = {}  # folded -> as typed, the first spelling
            for address, key in zip(typed, folded, strict=True):
                if key in members:
                    user_id, role = members[key]
                    granted.setdefault(user_id, role)
                else:
                    unknown.setdefault(key, address)
            before = (everyone, _people(con, project_id))
            people = dict(before[1])
            for user_id, role in granted.items():
                if user_id != maker_id and role != "owner":  # they reach it whatever it says
                    people[user_id] = level
            self._replace_rule(con, project_id, name, before, (everyone, people))
        return Shared(granted=tuple(granted), unknown=tuple(unknown.values()))

    # Whether a model may be sent a project's pages (M4)

    def kept_from_models(self, project_ids: Iterable[str]) -> frozenset[str]:
        """The ids among `project_ids`, in the view's reach, whose project is at "never": its
        pages go to no model (cairn.arrange). One read of one statement, besides what the
        view's reach costs, whatever their number; an id unknown or out of reach is left out.
        A read: it writes nothing."""
        wanted = json.dumps(list(dict.fromkeys(project_ids)))
        with self._read() as con:
            reach = self._reach(con)
            found = con.execute(_MODELS_NAMES, (self._account_id, wanted, MODELS_PREFIX))
            kept = frozenset(project_id for (project_id,) in found)
        return kept if reach is None else kept & reach

    def models_choices(self, project_ids: Iterable[str]) -> dict[str, str]:
        """The choice of each project among `project_ids` whose people made one, by id:
        "allowed" or "never" (Privacy 4); a project nobody decided for, and an id unknown or
        out of reach, is left out. One statement, besides what the view's reach costs,
        whatever their number. A read."""
        wanted = json.dumps(list(dict.fromkeys(project_ids)))
        with self._read() as con:
            reach = self._reach(con)
            found = con.execute(_MODELS_CHOICES, (self._account_id, wanted, MODELS_PREFIX))
            chosen = dict(found.fetchall())
        return {k: v for k, v in chosen.items() if reach is None or k in reach}

    def set_project_models(self, project_id: str, models: str) -> str:
        """Set whether a model may be sent the project's pages: "allowed" or "never" (MODELS),
        recorded as project.models_changed (the project by name, as every event of a project
        is filed, and detail {"project_id", "models"}) when it changed; nothing is written when
        the project is at that state already. The state it is at now. ValueError, before
        anything is read: another value. Then, in the write, as set_project_access: KeyError
        for a project unknown or out of reach; PermissionError unless the view acts for no
        person, the maker, an owner or a person at "manage" on it. A narrowed view is refused
        before all of these (cairn.store.reach: a management write). Pages already arranged
        stay as they are: it decides what the next arrangement may send."""
        if not isinstance(models, str) or models not in MODELS:
            raise ValueError(f"a project's models are {MODELS[0]!r} or {MODELS[1]!r}")
        with self._write() as con:
            name, maker_id, _ = self._reached(con, project_id)
            self._may_change_rule(con, project_id, maker_id)
            key = MODELS_PREFIX + project_id
            held = con.execute(
                "SELECT value FROM settings WHERE account_id = ? AND name = ?",
                (self._account_id, key),
            ).fetchone()
            if held is not None and held[0] == models:
                return models
            _choose_models(con, self._account_id, project_id, models, self._stamp())
            detail = {"project_id": project_id, "models": models}
            self._audit(con, "project.models_changed", project=name, detail=detail)
        return models

    def _replace_rule(
        self,
        con: sqlite3.Connection,
        project_id: str,
        name: str,
        before: tuple[str, dict[str, str]],
        after: tuple[str, dict[str, str]],
    ) -> None:
        """Write a project's rule, (everyone, people), and its project.access_changed event, when
        it differs from what it was; nothing otherwise."""
        if after == before:
            return
        con.execute("UPDATE projects SET everyone = ? WHERE id = ?", (after[0], project_id))
        con.execute("DELETE FROM project_people WHERE project_id = ?", (project_id,))
        con.executemany(
            "INSERT INTO project_people (project_id, user_id, access) VALUES (?, ?, ?)",
            [(project_id, user_id, level) for user_id, level in after[1].items()],
        )
        detail: dict[str, Any] = {"everyone": after[0], "people": after[1]}
        self._audit(con, "project.access_changed", project=name, detail=detail)


def _keep_from_models(con: sqlite3.Connection, account_id: str, project_id: str, now: str) -> None:
    """Put a project at "never" (its row of the settings), inside the caller's write."""
    _choose_models(con, account_id, project_id, "never", now)


def _choose_models(
    con: sqlite3.Connection, account_id: str, project_id: str, models: str, now: str
) -> None:
    """Record the project's people's choice, "allowed" or "never", as its row of the settings,
    whatever row it had, inside the caller's write."""
    con.execute(
        "INSERT INTO settings (account_id, name, value, updated_at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (account_id, name) DO UPDATE SET value = excluded.value,"
        " updated_at = excluded.updated_at",
        (account_id, MODELS_PREFIX + project_id, models, now),
    )


def _addresses(emails: Iterable[str]) -> list[str]:
    """The addresses share_project was given, each stripped; ValueError for a list it refuses
    (see share_project), never repeating what an entry holds."""
    if isinstance(emails, (str, bytes)):
        raise ValueError("emails is a collection of addresses, not one string")
    try:
        listed = list(emails)
    except TypeError:
        raise ValueError("emails is a collection of addresses") from None
    if not listed:
        raise ValueError("name at least one address")
    if len(listed) > SHARE_MAX:
        raise ValueError(f"name at most {SHARE_MAX} addresses at once, not {len(listed)}")
    typed = []
    for n, entry in enumerate(listed, 1):
        address = entry.strip() if isinstance(entry, str) else ""
        if (
            not address
            or "@" not in address
            or len(address) > ADDRESS_MAX
            or any(c.isspace() for c in address)
        ):
            raise ValueError(f"entry {n} of {len(listed)} is not one email address")
        typed.append(address)
    return typed


def _people(con: sqlite3.Connection, project_id: str) -> dict[str, str]:
    rows = con.execute(
        "SELECT user_id, access FROM project_people WHERE project_id = ? ORDER BY user_id",
        (project_id,),
    )
    return dict(rows.fetchall())
