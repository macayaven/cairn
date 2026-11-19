"""Reach: what one connection (an API key or an agent's grant) may use of its workspace, and
the narrowed view that serves it.

A reach is `projects`, None for every project of the workspace (now and later) or the ids of
projects and sets, and `access`, "write" or "read". It is stored as given and resolved on each
call, so a reach that names a set follows the set, and a project or set deleted later drops
out of it silently.

`view.narrow(projects, access)` is a view of the same kind as `for_account`'s, made the same
way (a copy that shares the connection), that keeps every reach it was narrowed by: the
projects it covers are the intersection of each reach, resolved now, and it is read-only once
any narrowing was. ReachMethods sits first in Store's MRO, so it sees every call first: each
public method of Store is classified below as limited to the reach, refused, or unaffected,
and tests/unit/test_reach_unit.py checks that no method is left out.

A call on a project's data (PROJECT_DATA) is limited by the area's own method, which asks the
seam StoreBase._reach for the projects in reach on the connection of the one section (a read
transaction, or the write transaction) it works in. ReachMethods answers that seam with the
narrowing; a later limit (a person's own access) overrides it again and intersects. Nothing
resolves a reach in one section and uses it in another.
"""

import copy
import json
import sqlite3
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Literal

from cairn.store.base import StoreBase
from cairn.store.models import (
    AuditEvent,
    GrantInfo,
    KeyInfo,
    Note,
    Page,
    Project,
    ProjectSet,
    SourceInfo,
)

if TYPE_CHECKING:
    from cairn.store import Store

ACCESS = ("write", "read")
READ_ONLY = "this connection is read-only: it can read its projects but not write to them"
OTHER_ACCOUNT = "this connection is bound to its own workspace: it cannot act in another"
NOT_ITS_WORKSPACE = (
    "this view acts for a person, or is limited to some projects or to reading: it cannot read"
    " or change another workspace, another person, or what belongs to no workspace"
)
LIMITED = (
    "this connection is limited to chosen projects: only a connection that reaches every project, "
    "with write access, can create projects, keys or sets or manage the workspace"
)

# The ids a reach covers in an account: its projects, and the projects of its sets.
_RESOLVE = (
    "SELECT id FROM projects WHERE account_id = ?1 AND (id IN (SELECT value FROM json_each(?2))"
    " OR id IN (SELECT member.value FROM project_sets AS s, json_each(s.project_ids) AS member"
    "  WHERE s.account_id = ?1 AND s.id IN (SELECT value FROM json_each(?2))))"
)

# Store methods a narrowed view passes on untouched, and why: they read no project's data, or
# they look a credential up by its secret in every account, as authentication must.
UNAFFECTED = frozenset(
    {
        "close",  # a view never closes the connection
        "health",  # whether the store can serve: one read of no workspace's data (R1)
        "narrow",  # can only narrow further
        "acting_as",  # who the view's writes are recorded under: it keeps every narrowing
        "writing_as",  # who the view's notes name as their writer (A1): it keeps every narrowing
        "with_guard",  # the note guard its add_note screens with: it keeps every narrowing
        "get_account",
        "list_accounts",  # accounts are not scoped (H1)
        "check_key",
        "check_session",
        "resolve_key",
        "resolve_session",
        "resolve_token",
        "identify_key",
        "identify_session",
        "identify_token",  # credential lookups: one needs the secret itself
        "renew_session",  # a session found by its secret, as identify_session (B1)
        "session_key",  # the key a session was opened with, by its secret (A1)
        "get_client",
        "find_code",
        "refresh_token_client",  # OAuth reads that belong to no account
        "get_user",
        "role",
        "list_members",  # people, not projects
        "list_workspaces",  # a person's own memberships, as get_user
        "find_invite",  # a lookup by its secret, as the credential lookups
        # The meter of a model call the server made for this view's writes: it names no
        # project and holds no data, and refusing it would leave money spent unrecorded.
        "record_model_use",
        # The same meter, before the call and after it; the project a reservation may name
        # is read for its Models state alone (M4), which only ever refuses the call.
        "reserve_model_use",
        "settle_model_use",
        "limited",  # caps on top of the view, which keeps every narrowing
        "model_budget_left",  # the workspace's budget, asked by whatever writes a note
        # The guard's answer for a text given: it reads the workspace's policy, as
        # add_note does whatever the reach, and no project.
        "screen",
        "plan_in_force",  # what limits every request, a narrowed one's too; as get_account
    }
)
# Bound to the view's own account: a narrowed view refuses any other (a reach names one
# workspace's ids, and "every project" carried into another would be all of that one's), and
# keeps its narrowing for its own.
ACCOUNT_BOUND = frozenset({"for_account"})
# Writes that manage the workspace rather than write into a project, and the workspace's
# settings, read or written: refused unless the view reaches every project with write access
# ("limited" first, then "read-only").
MANAGEMENT = frozenset(
    {
        "get_setting",  # a connection limited in any way learns nothing of the workspace's
        "set_setting",
        "create_project",
        "create_projects",  # the guided setup's projects, all or none
        "create_key",
        "revoke_key",
        "made_by",  # what a key made (A1): the workspace's credentials, like list_keys
        "create_session",  # a web session would have the whole workspace
        "end_session",
        "create_set",
        "update_set",  # a set-scoped connection could otherwise widen itself
        "delete_set",
        "create_account",
        "delete_account",
        "sign_in",
        "register_client",
        "create_code",  # an approval would hand on a reach of its own
        "redeem_code",
        "rotate_refresh_token",
        "revoke_token",
        "revoke_grant",
        "add_source",  # a source names the server's environment variables
        "remove_source",
        "enable_source",  # switching a source on chooses where it writes (S2)
        "disable_source",
        "claim_source",  # a run writes into every project its config names
        "finish_source_run",
        "fail_source_run",
        "holds_source_claim",
        "written_source_items",  # a run's items (I1), as its claim
        "record_source_item",
        "held_selectors",  # R3: what the other sources of a run's account select
        "overlapping_source",  # R3: another source of a run's account that selects its items
        "source_to_preview",  # a source's whole config, for a preview (R1)
        "source_items_written",  # a preview's count of the items written before
        "source_from_to_preview",  # R4: a source not yet saved, its whole config, for a preview
        "add_credential",  # R2: an account at a service, the operator's to register
        "list_credentials",  # whose accounts the workspace holds: nothing for a connection
        "remove_credential",
        "add_source_from",  # a source made from an account chooses where it writes
        "export",  # everything the workspace holds: a reach limited in any way takes nothing
        "import_export",  # makes projects
        "compact",  # the operator's: it rewrites every workspace's file (and only the root may)
        "empty_log",  # the operator's: what a rewrite left due
        "compaction_state",  # the operator's: whether any workspace was deleted
        "bound_history",  # the operator's (Q1): how many versions every page keeps
        "record_history_bound",
        "pages_over",
        "prune_by_record",  # the operator's: it removes versions in every workspace
        "numbers_part",  # the operator's (Q1): counts across every workspace
        "database_file",
        "sweep_expired",  # the operator's: what has run out, in every workspace
        "end_person_sessions",  # the operator's: one person's sessions, in every workspace
        "cap_web_sessions",  # the operator's: every person's session, in every workspace
        "invite",  # people (T1): a connection never manages them
        "list_invites",
        "revoke_invite",
        "accept_invite",  # joins a workspace, as sign_in does
        "set_role",
        "remove_member",
        "set_project_access",  # who reaches a project: a connection never changes it
        "share_project",  # the same, for a list of addresses at once (T2)
        "set_project_models",  # whether a model is sent its pages (M4): decided as its rule is
        "set_plan",  # the operator's, like the other account writes
        "usage",  # the workspace's use, like its settings: nothing for a limited connection
        "stored_bytes",
        "passages",  # the workspace's counts behind its disk bounds (H6c)
        "history_bytes",
        "project_count",
        "model_reservations",  # the workspace's calls in flight, like its usage
        "list_account_summaries",  # the operator's: every workspace's plan
        "waiting_notes",  # the operator's: every workspace's notes not yet on their page (N1)
        "connect_google",  # a Google account connected in the browser (cairn.store.hosted)
        "google_accounts",
        "google_token",
        "disconnect_google",
        "google_accounts_to_manage",
        "google_account_held",  # the revoking rule: whether any row holds a Google account
        "google_account_ended",  # the revoking rule: a connection made while it was sent
        "google_room",  # asked before a Connect goes to Google
        "credential_hosted",
        "claim_google_revocation",  # the server's: one sender of a waiting row at a time
        "release_google_revocation",  # the server's: a row not settled waits again
        "pending_google_revocations",  # the server's: what waits to be revoked at Google
        "settle_google_revocation",  # what removing an account says with the switch off
        "hosted_document",  # a run's: the document of the account a source reads
        # A workspace's Slack install (K1, cairn.store.slack): set up, switched and removed by
        # its owners and admins, used by the server's handlers for a linked person
        "make_slack_install",
        "get_slack_install",
        "ready_slack_install",
        "switch_slack_install",
        "remove_slack_install",
        "revoke_slack_bot",
        "slack_bot_token",
        "record_slack_note",
        "claim_slack_message",  # a filed message claimed before it is written, and released
        "release_slack_claim",
        "slack_note_for",
        "slack_notes_to_forget",
        "forget_slack_notes_now",
        "link_slack_user",
        "unlink_slack_user",
        "slack_person",
        "own_slack_links",  # the view's own person's links, for the Sources page
        "set_slack_project",
        "offer_slack_link",
        "slack_offer",
        "take_slack_offer",
        "claim_slack_offer",
        "slack_inbound",  # the server's, across workspaces (OTHER_WORKSPACES)
        "slack_install_verified",
        "slack_event_seen",
        "forget_slack_event",
        "slack_notes_due",
        "sweep_slack",
        "slack_team_held",  # the server's: whether another workspace holds a Slack team
        "take_slack_checks",  # the server's: the bot tokens to check with Slack
        "slack_token_checked",
        "remove_gone_slack_installs",
    }
)
# Reads limited to the reach: an out-of-reach project, or a note, span or page in one,
# behaves as unknown. The credentials and sets of the workspace name projects a limited view
# must not learn of, so it sees none. The reads of a project's data (PROJECT_DATA) are limited by
# the area's own methods through the seam StoreBase._reach, in the section that reads.
LIMITED_READS = frozenset(
    {
        "list_projects",
        "writable_projects",  # those the view's person may write to: none on a read-only view
        "get_project",
        "get_note",
        "holds_note",  # whether the project holds a note of this text (H6c)
        "list_notes",
        "note_spans",
        "get_spans",
        "spans_within",  # get_spans without the long ones (Help)
        "note_writers",  # who wrote the notes (A1): none out of reach
        "note_origins",  # note_writers, with when and whether a filed Slack message (8.1, 8.5)
        "get_page",
        "page_claims",  # a page without its body (Help)
        "page_for_model",  # M4: the page, and whether its project goes to no model
        "kept_from_models",  # M4: which projects in reach go to no model
        "models_choices",  # Privacy 4: which projects in reach their people decided for, and how
        "list_pages",
        "page_versions",
        "page_counts",  # Q1: the reads that cost what they return, as their neighbours
        "count_notes",
        "page_heads",
        "first_page_heads",  # page_heads without the count (Help)
        "version_numbers",
        "get_notes",
        "note_sources",  # where notes came from, without their text (Help)
        "topic_page",  # the page of a topic key (N1): none for a project out of reach
        "taken_slugs",  # the addresses a new page may not take: none, out of reach
        "search",
        "find",  # Q2: search, with what it left out
        "list_keys",
        "list_grants",
        "list_sets",
        "get_set",
        "list_audit",  # the workspace's log names every project: a narrowed view reads none of it
        "list_sources",  # a source names projects and the server's environment: none either
        "get_source",
        "source_credentials",  # which account each source reads (R2): none either
        "project_access",  # an out-of-reach project's rule is unknown, as the project is
        "project_sharing",
        "projects_sharing",  # Q1: project_sharing for many projects in one read
        "may_erase",  # whether the view may delete the project or forget its notes (H5b)
        "forget_preview",  # what forgetting a note would change: for whoever reaches it
        "manages",  # whether the view manages the workspace: never a narrowed one
    }
)
# Erasure in a project (H5b): an out-of-reach project, or a note in one, is unknown, checked
# first inside the write; then any narrowing refuses (PermissionError), read-only or not,
# through StoreBase._limited_connection, which this class answers.
ERASURES = frozenset({"delete_project", "forget_note"})
# Writes into a project: refused on a read-only view, and an out-of-reach project is unknown.
PROJECT_WRITES = frozenset({"add_note", "save_page", "record_note", "tried_waiting"}) | ERASURES
# The calls that read or write a project's data. Each resolves the view's reach with
# `self._reach(con)` in the one section (a `_read`, or the `_write` transaction) it works in.
PROJECT_DATA = (
    LIMITED_READS
    - {"list_keys", "list_grants", "list_sets", "get_set", "list_audit"}
    - {"list_sources", "get_source", "source_credentials", "manages"}
) | PROJECT_WRITES


# A stored reach (a key's, an agent's code's or grant's) names at most REACH_MAX projects or sets:
# more is refused where it is made, and a set is one entry whatever it holds. Ids are at most
# REACH_ID_MAX characters ("set_" + 12 hex; a project's "br_" + 12 hex is one shorter), so a
# stored reach within the cap is at most REACH_TEXT_MAX characters as json.dumps writes it:
# 64 quoted ids of 18 characters, 63 ", " between them and the two brackets, 1,280. Admission
# reads no more than that of a row (_read_stored_reach).
REACH_MAX = 64
REACH_ID_MAX = 16
REACH_TEXT_MAX = 2 + REACH_MAX * (REACH_ID_MAX + 2) + (REACH_MAX - 1) * 2


def _stored_reach(
    con: sqlite3.Connection,
    account_id: str,
    projects: Iterable[str] | None,
    access: str,
    reach: frozenset[str] | None = None,
    who: str = "a key",
) -> tuple[str | None, str]:
    """A reach as a key, code or grant row holds it: (a JSON array of ids, or None for every
    project; access). ValueError: an access other than "write" or "read", no id at all, more
    than REACH_MAX ids (reach_too_wide, naming `who`), or an id that is not a project or set of
    the account, or a project out of `reach`, the projects the view making it reaches (None:
    every one), as if it did not exist."""
    if access not in ACCESS:
        raise ValueError(f"access is 'write' or 'read', not {access!r}")
    if projects is None:
        return None, access
    if isinstance(projects, str):
        raise ValueError("a reach is a collection of project and set ids, not one string")
    wanted = list(dict.fromkeys(projects))
    if not wanted:
        raise ValueError("a reach names at least one project or set")
    if len(wanted) > REACH_MAX:
        raise ValueError(reach_too_wide(who))
    ids = json.dumps(wanted)
    found = {
        found_id
        for (found_id,) in con.execute(
            "SELECT id FROM projects"
            " WHERE account_id = ?1 AND id IN (SELECT value FROM json_each(?2))"
            " AND (?3 IS NULL OR id IN (SELECT value FROM json_each(?3)))"
            " UNION SELECT id FROM project_sets"
            " WHERE account_id = ?1 AND id IN (SELECT value FROM json_each(?2))",
            (account_id, ids, None if reach is None else json.dumps(sorted(reach))),
        )
    }
    if missing := [ref for ref in wanted if ref not in found]:
        raise ValueError(f"not a project or set of this workspace: {missing[0]!r}")
    return ids, access


def reach_too_wide(who: str = "a key") -> str:
    """The one sentence for a reach of more than REACH_MAX projects or sets."""
    return (
        f"{who} reaches at most {REACH_MAX} projects or sets; make a set of them, or leave it"
        " out to reach every project"
    )


class TooWide(Exception):
    """A stored reach longer than REACH_MAX entries could be: its credential is not admitted."""


def _admitted_reach(projects: str | bytes | None, length: int | None) -> tuple[str, ...] | None:
    """A bounded stored reach as admission reads it: None for every project, else its
    ids. TooWide when it is longer than REACH_TEXT_MAX bytes (it was never read) or names more
    than REACH_MAX ids (a row from before the cap): a credential whose reach cannot be within
    the cap reaches nothing, so it is refused as a credential that is not known."""
    if length is None:
        return None
    if projects is None or length > REACH_TEXT_MAX:
        raise TooWide
    loaded = _loaded_reach(projects)
    if loaded is None or len(loaded) > REACH_MAX:
        raise TooWide
    return loaded


def _read_stored_reach(
    con: sqlite3.Connection,
    table: Literal["api_keys", "oauth_grants"],
    rowid: int,
    is_null: bool,
) -> tuple[bytes | None, int | None]:
    """Read bounded scope bytes in the metadata lookup's _read snapshot. NULL means every
    project; a non-NULL reach must fit before any bytes are read. Callers name their literal
    table, then check scalar metadata/audience before decoding these bytes. Blob length
    needs no SQL value or cast allocation."""
    if is_null:
        return None, None
    with con.blobopen(table, "projects", rowid, readonly=True) as blob:
        length = len(blob)
        if length > REACH_TEXT_MAX:
            raise TooWide
        return blob.read(REACH_TEXT_MAX), length


def _loaded_reach(projects: str | bytes | None) -> tuple[str, ...] | None:
    return None if projects is None else tuple(json.loads(projects))


def _shown_reach(
    con: sqlite3.Connection, account_id: str, projects: str | None, reach: frozenset[str] | None
) -> tuple[str, ...] | None:
    """A stored reach as a view may see it: the ids of its sets, and of the projects in
    `reach` (None: every project, and every id is shown as stored). A project the view does not
    reach is left out, as if it did not exist."""
    loaded = _loaded_reach(projects)
    if loaded is None or reach is None:
        return loaded
    sets = {
        set_id
        for (set_id,) in con.execute(
            "SELECT id FROM project_sets WHERE account_id = ? AND id IN"
            " (SELECT value FROM json_each(?))",
            (account_id, projects),
        )
    }
    return tuple(ref for ref in loaded if ref in reach or ref in sets)


def _reach_detail(projects: str | None, access: str) -> dict[str, Any]:
    """A stored reach as an audit event's detail shows it: nothing for every project to write,
    else the ids it names (None: every project) and its access."""
    if projects is None and access == "write":
        return {}
    loaded = _loaded_reach(projects)
    return {"projects": None if loaded is None else list(loaded), "access": access}


class ReachMethods(StoreBase):
    """The narrowing of a view. Not narrowed (no reach and write access), every method is
    the one of the area it belongs to, unchanged."""

    _reaches: tuple[tuple[str, ...], ...] = ()  # every reach this view was narrowed by
    _read_only = False

    def narrow(self, projects: Iterable[str] | None = None, access: str = "write") -> "Store":
        """A view of the same workspace limited to this reach as well as to any it already
        has: it can only narrow. Ids are not checked here: an id that is not a project or set
        of the workspace covers nothing. ValueError: another access; TypeError: one string
        given as `projects`."""
        if access not in ACCESS:
            raise ValueError(f"access is 'write' or 'read', not {access!r}")
        if isinstance(projects, str):
            raise TypeError("projects is a collection of project and set ids, not one string")
        view = copy.copy(self)  # the same connection and lock, as for_account
        view._owner = False
        if projects is not None:
            view._reaches = (*self._reaches, tuple(projects))
        view._read_only = self._read_only or access == "read"
        return view  # type: ignore[return-value]

    @property
    def _narrowed(self) -> bool:
        return bool(self._reaches) or self._read_only

    def _limited_connection(self) -> bool:
        """StoreBase._limited_connection: any narrowing, to some projects or to reading."""
        return self._narrowed or super()._limited_connection()  # type: ignore[misc]

    def _bound(self) -> bool:
        """StoreBase._bound, and any narrowing: a narrowed view is held to its workspace."""
        return super()._bound() or self._narrowed  # type: ignore[misc]

    def for_account(self, account_id: str, *args: Any, **kwargs: Any) -> "Store":
        """As StoreBase.for_account, keeping every narrowing. PermissionError: a narrowed
        view asked for an account other than its own."""
        if self._narrowed and account_id != self._account_id:
            raise PermissionError(OTHER_ACCOUNT)
        return super().for_account(account_id, *args, **kwargs)

    # The reach, resolved

    def _visible(self, con: sqlite3.Connection) -> set[str] | None:
        """The ids of the projects in reach now; None when the view reaches every project."""
        if not self._reaches:
            return None
        visible: set[str] | None = None
        for reach in self._reaches:
            found = {row[0] for row in con.execute(_RESOLVE, (self._account_id, json.dumps(reach)))}
            visible = found if visible is None else visible & found
        return visible

    def _reach(self, con: sqlite3.Connection) -> frozenset[str] | None:
        """The seam (StoreBase._reach): what the next class answers, intersected with every
        reach of this view, resolved on `con`, inside the caller's section."""
        reach = super()._reach(con)
        visible = self._visible(con)
        if visible is None:
            return reach
        return frozenset(visible if reach is None else reach & visible)

    def _may_manage(self) -> None:
        if self._reaches:
            raise PermissionError(LIMITED)
        if self._read_only:
            raise PermissionError(READ_ONLY)

    def _may_write(self) -> None:
        if self._read_only:
            raise PermissionError(READ_ONLY)

    # Reads limited to the reach. The reads of a project's data (PROJECT_DATA) are the area's own
    # methods, unchanged here: each takes the reach from the seam inside its own section.

    def list_keys(self, *args: Any, **kwargs: Any) -> list[KeyInfo]:
        return [] if self._reaches else super().list_keys(*args, **kwargs)  # type: ignore[misc]

    def list_grants(self, *args: Any, **kwargs: Any) -> list[GrantInfo]:
        return [] if self._reaches else super().list_grants(*args, **kwargs)  # type: ignore[misc]

    def list_sets(self, *args: Any, **kwargs: Any) -> list[ProjectSet]:
        return [] if self._reaches else super().list_sets(*args, **kwargs)  # type: ignore[misc]

    def get_set(self, *args: Any, **kwargs: Any) -> ProjectSet | None:
        return None if self._reaches else super().get_set(*args, **kwargs)  # type: ignore[misc]

    def list_audit(self, *args: Any, **kwargs: Any) -> list[AuditEvent]:
        """Empty on any narrowed view, read-only included: the log is the workspace's."""
        return [] if self._narrowed else super().list_audit(*args, **kwargs)  # type: ignore[misc]

    def list_sources(self, *args: Any, **kwargs: Any) -> list[SourceInfo]:
        """Empty on any narrowed view, read-only included: sources are the workspace's."""
        return [] if self._narrowed else super().list_sources(*args, **kwargs)  # type: ignore[misc]

    def get_source(self, *args: Any, **kwargs: Any) -> SourceInfo | None:
        return None if self._narrowed else super().get_source(*args, **kwargs)  # type: ignore[misc]

    def source_credentials(self, *args: Any, **kwargs: Any) -> dict[str, str | None]:
        return {} if self._narrowed else super().source_credentials(*args, **kwargs)  # type: ignore[misc]

    def writable_projects(self, *args: Any, **kwargs: Any) -> list[Project]:
        """None on a read-only view: it writes nowhere."""
        return [] if self._read_only else super().writable_projects(*args, **kwargs)  # type: ignore[misc]

    def manages(self) -> bool:
        """False on any narrowed view: it manages nothing (MANAGEMENT)."""
        return False if self._narrowed else super().manages()  # type: ignore[misc]

    # Writes into a project: read-only is refused here, before any transaction (it is fixed
    # for the view's life, so nothing can change it between check and write); the project's
    # reach is checked by the area's method inside its own transaction, through the seam.

    def add_note(self, *args: Any, **kwargs: Any) -> tuple[Note, bool]:
        self._may_write()
        return super().add_note(*args, **kwargs)  # type: ignore[misc]

    def save_page(self, *args: Any, **kwargs: Any) -> Page:
        self._may_write()
        return super().save_page(*args, **kwargs)  # type: ignore[misc]

    def record_note(self, *args: Any, **kwargs: Any) -> None:
        self._may_write()
        return super().record_note(*args, **kwargs)  # type: ignore[misc]

    def tried_waiting(self, *args: Any, **kwargs: Any) -> int:
        self._may_write()
        return super().tried_waiting(*args, **kwargs)  # type: ignore[misc]


def _managing(name: str) -> Any:
    """A management method of the next class in Store's MRO, refused on a narrowed view."""

    def method(self: ReachMethods, *args: Any, **kwargs: Any) -> Any:
        self._may_manage()
        return getattr(super(ReachMethods, self), name)(*args, **kwargs)

    method.__name__ = method.__qualname__ = name
    method.__doc__ = f"Refused on a view limited to named projects or read-only; else {name}."
    return method


for _name in MANAGEMENT:
    setattr(ReachMethods, _name, _managing(_name))


# Operations that are not about the view's own workspace: other workspaces (create, list,
# plan), every person, sign-in, OAuth's clients, codes and tokens, the credential lookups
# across workspaces, and the file itself. The server and the command line call them on a
# view acting for no person. A view acting for a person, or narrowed to some projects or to
# reading, is refused them (PermissionError, NOT_ITS_WORKSPACE) before anything is read or
# written: a view is what is handed to code trusted less. This rule is orthogonal to the
# classes above, which say what narrowing to projects does.
OTHER_WORKSPACES = frozenset(
    {
        "create_account",
        "list_accounts",
        "list_account_summaries",
        "waiting_notes",
        "sign_in",
        "set_plan",
        "register_client",
        "get_client",
        "find_code",
        "redeem_code",
        "refresh_token_client",
        "rotate_refresh_token",
        "revoke_token",
        "resolve_key",
        "resolve_session",
        "resolve_token",
        "identify_key",
        "identify_session",
        "identify_token",
        "session_key",
        "compact",
        "empty_log",
        "compaction_state",
        "bound_history",
        "record_history_bound",
        "pages_over",
        "prune_by_record",
        "numbers_part",
        "database_file",
        "sweep_expired",
        "renew_session",
        "end_person_sessions",
        "cap_web_sessions",
        "slack_inbound",  # K1: the install a request to one of Slack's addresses names
        "slack_install_verified",
        "slack_event_seen",
        "forget_slack_event",
        "slack_notes_due",
        "sweep_slack",
        "slack_team_held",
        "take_slack_checks",
        "slack_token_checked",
        "remove_gone_slack_installs",
    }
)
# The same kind of operation, kept by such a view when it asks about its own workspace or
# its own person, and refused (NOT_ITS_WORKSPACE) for any other: the argument that must be
# its own, by position. get_user also answers for a member of the view's workspace.
# find_invite stays open to any view: it looks an invitation up by its secret, which is what
# a person needs to join another workspace (accept_invite, for themselves only).
OWN_WORKSPACE_ONLY: dict[str, tuple[str, int]] = {
    "get_account": ("account_id", 0),
    "delete_account": ("account_id", 0),  # and an owner now, inside its write (accounts.py)
    "plan_in_force": ("account_id", 0),
    "list_workspaces": ("user_id", 0),
    "accept_invite": ("user_id", 1),
    "get_user": ("user_id", 0),
}


def _held(self: ReachMethods) -> bool:
    """Whether the view is held to its own workspace for OTHER_WORKSPACES: it acts for a
    person, or is narrowed to some projects or to reading."""
    return self._user_id is not None or self._narrowed


def _own(self: ReachMethods, name: str, args: tuple, kwargs: dict) -> bool:
    """Whether the call `name(*args, **kwargs)` asks about the view's own workspace or
    person (OWN_WORKSPACE_ONLY)."""
    field, position = OWN_WORKSPACE_ONLY[name]
    value = kwargs.get(field, args[position] if len(args) > position else None)
    if field == "account_id":
        return value == self._account_id
    if value is not None and value == self._user_id:
        return True
    if name != "get_user" or not isinstance(value, str):
        return False
    with self._read() as con:  # a member of the view's workspace
        return (
            con.execute(
                "SELECT 1 FROM memberships WHERE account_id = ? AND user_id = ?",
                (self._account_id, value),
            ).fetchone()
            is not None
        )


def _workspace_bound(name: str) -> Any:
    """A method of the next class in Store's MRO, refused on a view held to its workspace
    unless it asks about that workspace or person (and, when it manages, refused on a
    narrowed view first, as _managing)."""

    def method(self: ReachMethods, *args: Any, **kwargs: Any) -> Any:
        if name in MANAGEMENT:
            self._may_manage()
        if _held(self) and (name not in OWN_WORKSPACE_ONLY or not _own(self, name, args, kwargs)):
            raise PermissionError(NOT_ITS_WORKSPACE)
        return getattr(super(ReachMethods, self), name)(*args, **kwargs)

    method.__name__ = method.__qualname__ = name
    method.__doc__ = (
        f"Refused on a view acting for a person or narrowed, unless about its own workspace"
        f" (cairn.store.reach.OTHER_WORKSPACES); else {name}."
    )
    return method


for _name in OTHER_WORKSPACES | set(OWN_WORKSPACE_ONLY):
    setattr(ReachMethods, _name, _workspace_bound(_name))
