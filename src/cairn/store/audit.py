"""A workspace's settings and its audit log, and who a view acts as.

The audit log holds a workspace's writes and security events, one row each, written by the
store method that makes the write, in the same transaction (StoreBase._audit). A read is
recorded here only when the server is told to (`reads_audited`; cairn.memory records it,
through `_record_read`, as the read ends), and an export and a download of this log always.
Each row names the view's actor, set with `acting_as` (a bare view acts as "system"). A row
holds names, ids and counts, never note text, a secret or a digest.
"""

import copy
import json
import re
import sqlite3
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from cairn.store.base import AUDIT_LOGGER, MANAGER_ROLES, StoreBase, _require_account
from cairn.store.models import AuditEvent

if TYPE_CHECKING:
    from cairn.guard import Finding, Guard
    from cairn.store import Store

__all__ = ["AUDIT_LOGGER"]  # the logger every stored event is copied to (base.audit_log)

ACTOR_KINDS = ("key", "token", "session", "system")
ACTOR_NAME_MAX = 200  # characters kept of an actor's name
# Who wrote a note (A1): a person in the web composer, an API key (a web session opened with a
# key is that key), an agent connected by URL, a source's run, or the server's command line.
# A note stored before schema step 25 has none recorded, and is read as "unknown".
WRITER_KINDS = ("person", "key", "agent", "source", "operator")
UNKNOWN_WRITER = "unknown"
# An address in a person's stored name, with the brackets around it: "Cy <cy@example.test>".
_ADDRESS = re.compile(r"[<(\[]?\s*([^\s<>()\[\]@]+)@[^\s<>()\[\]]+\s*[>)\]]?")
# Events with no project that describe the whole workspace's content (they count projects,
# notes or pages, private ones included): shown to owners and to views acting for no person.
WHOLE_WORKSPACE_EVENTS = frozenset({"workspace.imported"})
# What a deleted project's events name instead of its name (cairn.store.erasure.delete_project):
# no project name holds a space (memory.PROJECT_NAME), so no project made later can take it. Shown,
# like WHOLE_WORKSPACE_EVENTS, to owners and to views acting for no person only: a member
# who reaches every project there is now may never have reached the one that went.
DELETED_PROJECT = "{} (deleted)"
# What a removed source's events name instead of its name (sources.remove_source): its name
# and a word unique to the removal, with a space no source name holds (a source's name
# follows the project-name rules), so a source added later under that name starts with none
# of them and two removals of one name stay apart. Shown, as DELETED_PROJECT, to owners and to
# views acting for no person only: its config, which named the projects it wrote into, is gone.
REMOVED_SOURCE = "{} (removed {})"
AUDIT_LIMIT_MAX = 1000  # events one list_audit call returns at most
# The note guard's policies (cairn.guard): what a workspace refuses when a note is written.
GUARD_POLICIES = ("off", "secrets", "secrets+personal")
POLICY_SETTING = "guard.policy"
DEFAULT_GUARD_POLICY = "secrets"  # a workspace's policy until it sets one
# The kinds of finding each policy refuses. Each policy's kinds include the one before's,
# so a note screened once under the widest policy is decided for any of them.
POLICY_KINDS: dict[str, tuple[str, ...]] = {
    "off": (),
    "secrets": ("secret",),
    "secrets+personal": ("secret", "personal"),
}
# What add_note screens: every field of a note it stores (the title it may derive
# from the text's first heading is the text's).
SCREENED_FIELDS = ("text", "title", "topic", "label", "source_ref", "via")
# Every setting a workspace can hold, with the values it accepts.
SETTINGS: dict[str, tuple[str, ...]] = {POLICY_SETTING: GUARD_POLICIES}
# The outcomes of a note that no other write records: "saved" is recorded by save_page.
NOTE_OUTCOMES = ("unchanged", "rejected", "refused")
SETTING_ROLES = MANAGER_ROLES  # the people who change a workspace's settings
NOT_A_MANAGER = "only the workspace's owners and admins change its settings"
_EVENT_COLUMNS = "at, actor_kind, actor, actor_id, action, project, detail"


def person_name(name: str) -> str:
    """How a note names the person who wrote it: the store's name for them, less every
    address in it and the brackets around one ("Cy <cy@example.test>" is "Cy"); when nothing
    is left, the part of the first address before its "@" ("cy@example.test", the name a
    sign-in with no name leaves, is "cy"). No "@" is left in what it gives."""
    left = " ".join(_ADDRESS.sub(" ", name).replace("@", " ").split())
    if left:
        return left
    found = _ADDRESS.search(name)
    return found.group(1) if found else ""


def _known(name: str) -> tuple[str, ...]:
    if name not in SETTINGS:
        raise ValueError(f"no setting named {name!r}")
    return SETTINGS[name]


def _shown_detail(
    detail: dict[str, Any], hidden: frozenset[str], action: str, names: frozenset[str] | None
) -> dict[str, Any]:
    """An event's detail as a viewer is shown it. A source event's `projects` are project names
    (sources.enable_source): only those in `names` (casefolded: the projects that exist now
    and the viewer reaches; None, the operator's view: every one) are kept. Any other
    event's `projects` are ids (a reach): those in `hidden` are left out."""
    projects = detail.get("projects")
    if not isinstance(projects, list):
        return detail
    if action.startswith("source."):
        if names is not None:
            detail["projects"] = [
                b for b in projects if isinstance(b, str) and b.casefold() in names
            ]
    elif hidden:
        detail["projects"] = [ref for ref in projects if ref not in hidden]
    return detail


class AuditPart(list[AuditEvent]):
    """A part of the audit log, newest first (AuditMethods.list_audit): the events, and how
    many the viewer sees that match its filters (`total`) and how many of those are newer
    than its first event (`newer`)."""

    def __init__(self, events: Iterable[AuditEvent], *, total: int, newer: int) -> None:
        super().__init__(events)
        self.total = total
        self.newer = newer


class AuditMethods(StoreBase):
    # Who the view acts as
    def acting_as(self, kind: str, name: str | None, actor_id: str | None = None) -> "Store":
        """A view of the same workspace, with every narrowing it has, that records what it
        writes under (kind, name): "key" and the key's label, "token" and the agent's client
        name, "session" and the person (or the label of the key the session was opened with),
        or "system"; and under `actor_id`, which tells apart actors that share a name: the
        key's id, the grant's, the person's (`identify_key`, `identify_token`,
        `identify_session`). ValueError: another kind."""
        if kind not in ACTOR_KINDS:
            raise ValueError(f"an actor is one of {ACTOR_KINDS}, not {kind!r}")
        view = copy.copy(self)  # the same connection and lock, as for_account
        view._owner = False
        view._actor = (kind, None if name is None else str(name)[:ACTOR_NAME_MAX])
        view._actor_id = actor_id
        view._writer, view._made_through = None, None  # a new caller: its own (`writer`)
        return view  # type: ignore[return-value]

    def writing_as(self, kind: str, name: str | None, key_id: str | None = None) -> "Store":
        """A view of the same workspace, with every narrowing it has, whose notes record
        (kind, name) as their writer (A1): one of WRITER_KINDS. A person's name is cut to
        what `person_name` gives, so no address is ever recorded. `key_id`: the id of the key
        the caller came with (a key, or a web session opened with one), which a key it makes
        or an agent it approves records (`made_through`). The server sets it from how the
        caller was admitted, never from anything a request says. ValueError: another kind."""
        if kind not in WRITER_KINDS:
            raise ValueError(f"a writer is one of {WRITER_KINDS}, not {kind!r}")
        if kind == "person" and name is not None:
            name = person_name(name)
        view = copy.copy(self)
        view._owner = False
        view._writer = (kind, None if name is None else str(name)[:ACTOR_NAME_MAX])
        view._made_through = key_id
        return view  # type: ignore[return-value]

    @property
    def writer(self) -> tuple[str, str | None]:
        """(kind, name) a note stored through this view records as its writer: the one
        `writing_as` set, else the actor's own: a key by its label, an agent by its client's
        name, a session by its person's name (`person_name`), and a view acting as the
        system, or a bare one (the command line, an import), as "operator" with no name."""
        if self._writer is not None:
            return self._writer
        kind, name = self._actor
        if kind == "key":
            return ("key", name)
        if kind == "token":
            return ("agent", name)
        if kind == "session" and name is not None:
            return ("person", person_name(name))
        return ("operator", None)

    @property
    def made_through(self) -> str | None:
        """The id of the key this view's caller came with, which a key or an agent's grant
        made through it records (A1): the one `writing_as` set, else the actor's own id when
        it acts as a key; None for anyone else."""
        if self._writer is not None:
            return self._made_through
        kind, _ = self._actor
        return self._actor_id if kind == "key" else None

    # The note guard
    def with_guard(self, guard: "Guard") -> "Store":
        """A view of the same workspace, with every narrowing it has, whose add_note
        screens with `guard` instead of the workspace's "guard.policy"."""
        view = copy.copy(self)
        view._owner = False
        view._guard = guard
        return view  # type: ignore[return-value]

    def _policy(self, con: sqlite3.Connection) -> str:
        """The workspace's guard policy, read on `con` whatever the view's reach: a narrowed
        view cannot read settings, but its notes are screened under them."""
        row = con.execute(
            "SELECT value FROM settings WHERE account_id = ? AND name = ?",
            (self._account_id, POLICY_SETTING),
        ).fetchone()
        return row[0] if row else DEFAULT_GUARD_POLICY

    def _screen(self, fields: dict[str, str | None]) -> list[tuple[str, "Finding"]]:
        """What the view's guard finds in each field, or else the PatternGuard of the widest
        policy (add_note keeps what the workspace's policy refuses), as (field, finding)
        pairs in SCREENED_FIELDS order. Pure: no lock, no database."""
        from cairn.guard import PatternGuard  # cairn.guard imports this package

        guard = self._guard or PatternGuard(GUARD_POLICIES[-1])
        return [
            (name, finding)
            for name in SCREENED_FIELDS
            if fields.get(name)
            for finding in guard.check(fields[name])  # type: ignore[arg-type]
        ]

    def screen(self, text: str) -> list["Finding"]:
        """What the note guard would refuse in `text` now, by add_note's own two steps
        (_screen, then _in_force): the view's guard's findings when it has one, else those
        the workspace's "guard.policy", read now, refuses. For text the store already holds:
        cairn.arrange asks before it sends a page to a model, since the policy may have been
        tightened after the note was taken. A read: nothing is recorded, and it reads no
        project, so a narrowed view answers it as any other."""
        found = self._screen({"text": text})
        with self._read() as con:
            return [finding for _, finding in self._in_force(con, found)]

    def _in_force(
        self, con: sqlite3.Connection, found: list[tuple[str, "Finding"]]
    ) -> list[tuple[str, "Finding"]]:
        """The findings of `_screen` that refuse a note now: all of them under the view's
        own guard, else those of the kinds the workspace's policy, read on `con` inside the
        write, refuses (POLICY_KINDS). add_note and an import both decide with this."""
        if self._guard is not None:
            return found
        kinds = POLICY_KINDS[self._policy(con)]
        return [(name, finding) for name, finding in found if finding.kind in kinds]

    # The audit log
    def list_audit(
        self,
        limit: int = 100,
        *,
        before: int | None = None,
        action: str | None = None,
        project: str | None = None,
        who: str | None = None,
    ) -> "AuditPart":
        """This workspace's events, newest first, at most `limit` (up to AUDIT_LIMIT_MAX), as an
        AuditPart: a list that also says how many events the viewer sees that match (`total`)
        and how many of those are newer than the first it holds (`newer`), one count each.
        Each event carries its number in its workspace (`n`). `before`: only events numbered
        below it (the `n` of the last event of the part before). The filters, each left out
        when None or empty: `action`, an action ("note.saved") or a family of them, the part
        before the dot ("note"); `project`, the project's name as the event names it, ignoring
        case (a deleted project's events: DELETED_PROJECT); `who`, the actor's name ignoring
        case, or its id. A filter never widens what the viewer sees, which is decided first,
        by the rules below, for the events, the counts and every part alike.

        For a view acting for a person who does not reach every project (StoreBase._reach), an
        event of a project they do not reach is left out before the limit is applied (events
        name projects and page slugs, and a slug comes from a note's title); the id of such a
        project is left out of an event's `projects`, as if it did not exist. Source events
        (findings 2, 3 and 16): every owner and admin reads every event of every source
        there is, whatever it writes into, and a member reads none (they see no source). A
        source event's `projects` are names, filtered where the events are read, for every
        event, old or new: only the projects that exist now and the viewer reaches are named,
        so a source moved out of a private project never names it to someone who did not
        reach it, and a project that no longer exists is named to the operator's view only (a
        view acting for no person that reaches every project). A person who does not own the
        workspace but reaches every project now is filtered the same way, with every project
        there is as their reach: an event naming no project that exists is left out for them
        too. remove_source refiles a source's events under REMOVED_SOURCE, which no
        source is named, so a source added later under that name shows none of them. An
        event about the whole workspace's content (WHOLE_WORKSPACE_EVENTS), an event of a
        deleted project (DELETED_PROJECT) and an event of a removed source (REMOVED_SOURCE) are
        left out for a person who does not own the workspace, whatever they reach. An owner,
        and a view acting for no person that reaches every project, read every event."""
        if limit < 1:
            return AuditPart([], total=0, newer=0)
        limit = min(limit, AUDIT_LIMIT_MAX)
        with self._read() as con:
            reach = self._reach(con)
            role = self._role_in(con)
            whole = self._user_id is None or role == "owner"
            # An admin reads every live source's events; a member none (list_sources).
            sources_seen = (
                "instr(json_extract(detail, '$.source'), ' ') = 0"  # REMOVED_SOURCE has one
                if role in MANAGER_ROLES
                else "0"
            )
            unseen = json.dumps([] if whole else sorted(WHOLE_WORKSPACE_EVENTS))
            if reach is None and not whole:
                # A person who reaches every project now is filtered as one who does not: what
                # they reach is every project there is, and no more (a removed source, or one
                # naming a project since deleted, is not shown to them either).
                reach = frozenset(
                    project_id
                    for (project_id,) in con.execute(
                        "SELECT id FROM projects WHERE account_id = ?", (self._account_id,)
                    )
                )
            # Who may see what: one clause, for the events and both counts.
            seen = "account_id = ?"
            params: list[Any] = [self._account_id]
            if reach is None:  # an owner, or a view acting for no person: every event
                hidden: frozenset[str] = frozenset()
            else:
                seen += (
                    " AND (project IS NULL OR project IN (SELECT name FROM projects"
                    " WHERE account_id = ? AND id IN (SELECT value FROM json_each(?))))"
                    f" AND (action NOT LIKE 'source.%' OR {sources_seen})"
                    " AND action NOT IN (SELECT value FROM json_each(?))"
                )
                params += [self._account_id, json.dumps(sorted(reach)), unseen]
                every = con.execute(
                    "SELECT id FROM projects WHERE account_id = ?", (self._account_id,)
                ).fetchall()
                hidden = frozenset(project_id for (project_id,) in every) - reach
            if action:
                seen += " AND (action = ? OR substr(action, 1, length(?) + 1) = ? || '.')"
                params += [action, action, action]
            if project:
                seen += " AND project = ? COLLATE NOCASE"
                params.append(project)
            if who:
                seen += " AND (actor = ? COLLATE NOCASE OR actor_id = ?)"
                params += [who, who]
            older = " AND n < ?" if before is not None else ""
            rows = con.execute(
                f"SELECT {_EVENT_COLUMNS}, n FROM audit_events WHERE {seen}{older}"
                " ORDER BY n DESC LIMIT ?",
                (*params, *(() if before is None else (before,)), limit),
            ).fetchall()
            (total,) = con.execute(
                f"SELECT COUNT(*) FROM audit_events WHERE {seen}", params
            ).fetchone()
            newer = 0
            if rows:
                (newer,) = con.execute(
                    f"SELECT COUNT(*) FROM audit_events WHERE {seen} AND n > ?",
                    (*params, rows[0][-1]),
                ).fetchone()
            names = (
                None  # the operator's view: every name, a deleted project's included
                if self._user_id is None and reach is None
                else frozenset(
                    name.casefold()
                    for project_id, name in con.execute(
                        "SELECT id, name FROM projects WHERE account_id = ?", (self._account_id,)
                    )
                    if reach is None or project_id in reach
                )
            )
        return AuditPart(
            (
                AuditEvent(
                    at=at,
                    actor_kind=kind,
                    actor=actor,
                    actor_id=actor_id,
                    action=action,
                    project=project,
                    detail=_shown_detail(json.loads(detail), hidden, action, names),
                    n=n,
                )
                for at, kind, actor, actor_id, action, project, detail, n in rows
            ),
            total=total,
            newer=newer,
        )

    def _record_read(
        self, action: str, project: str | None, detail: dict[str, Any] | None = None
    ) -> None:
        """One event of what the view read or took (cairn.memory: READ_EVENTS, and an export
        or a download of this log), under the view's actor, in a transaction of its own.
        `project` is the name the read resolved, None for none. Private, so the narrowing
        (cairn.store.reach) leaves it alone: a read-only key, or one limited to some projects,
        is recorded as anyone else; the read itself was already held to its reach. Never a
        query or any text of a note or page: `detail` holds ids and a page's slug only."""
        with self._write() as con:
            self._audit(con, action, project=project, detail=detail)

    def record_note(
        self, project_id: str, outcome: str, detail: dict[str, Any], filed: str | None = None
    ) -> None:
        """Record what became of a note when no write of its own records it: "unchanged" (the
        page already says it), "rejected" (the gate refused the page) or "refused" (the note
        guard refused the note; nothing of it was stored). A write into the project: KeyError for
        a project that is unknown or out of reach, and a read-only view refuses it. ValueError:
        another outcome. `filed` (N1): the id of the stored note this outcome is for, which
        then waits no more, in the same transaction."""
        if outcome not in NOTE_OUTCOMES:
            raise ValueError(f"a recorded note outcome is one of {NOTE_OUTCOMES}, not {outcome!r}")
        with self._write() as con:
            self._require_project(con, project_id)  # type: ignore[attr-defined]
            (name,) = con.execute(
                "SELECT name FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            self._audit(con, f"note.{outcome}", project=name, detail=detail)
            if filed is not None:
                con.execute(
                    "DELETE FROM waiting WHERE note_id = ? AND project_id = ?", (filed, project_id)
                )

    # Settings
    def get_setting(self, name: str, default: str | None = None) -> str | None:
        """The workspace's value of a known setting, or `default` when it was never set.
        ValueError: an unknown setting."""
        _known(name)
        with self._read() as con:
            row = con.execute(
                "SELECT value FROM settings WHERE account_id = ? AND name = ?",
                (self._account_id, name),
            ).fetchone()
        return row[0] if row else default

    def set_setting(self, name: str, value: str) -> None:
        """Set a known setting to one of the values it accepts, recorded as setting.changed;
        setting the value it already has writes nothing. ValueError: an unknown setting or
        value, and nothing is written. A management action: a view acting for a person whose
        role is not owner or admin (SETTING_ROLES) gets PermissionError, decided inside the
        write; a view that acts for no person (a key, the command line) may."""
        if value not in _known(name):
            raise ValueError(f"{name} is one of {_known(name)}, not {value!r}")
        with self._write() as con:
            _require_account(con, self._account_id)
            self._require_manager(con, NOT_A_MANAGER)  # a person: only one who manages it
            row = con.execute(
                "SELECT value FROM settings WHERE account_id = ? AND name = ?",
                (self._account_id, name),
            ).fetchone()
            if row is not None and row[0] == value:
                return
            con.execute(
                "INSERT INTO settings (account_id, name, value, updated_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (account_id, name)"
                " DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (self._account_id, name, value, self._stamp()),
            )
            self._audit(con, "setting.changed", detail={"name": name})
