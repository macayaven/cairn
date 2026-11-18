"""A workspace's sources (cairn.sources): their configs, and the runs that read them.

A config is checked by its kind (cairn.sources.KINDS), not here, so a new kind needs no
change in the store. A run claims its source with a compare-and-set recorded in the
database, so every process on the file (the command line, the server's background sync)
sees the claim; a claim older than SOURCE_CLAIM_TTL may be taken by another run. Only the
holder of the current claim stores a cursor, a report or an error.

On and off (S2; frozen by tests/acceptance/test_s2_source_switch.py)
- Only the operator adds a source from a config (decision 25: a config names the server's
  environment); a source made from an account the operator registered (R2, below) is
  added by a person, naming no variable. A source of a kind whose `starts_off`
  is set is recorded off by `add_source`, and its config may name no project yet when the
  kind's check allows it; any other is on from the moment it is added. SourceInfo says
  `enabled` and `enabled_at`: when it was last switched on (the stamp of add_source for a
  kind that starts on; None while it never was on).
- `enable_source(name, projects=None, *, new_project=None)` switches it on. `projects` (names)
  replace the config's first; without them the config's own are kept. Either way the kind
  checks the config with that list (ValueError in its words), it must name at least one
  project (ValueError), and each project must exist and be in the caller's reach (ValueError
  "this workspace has no project named ...") and be one the caller may write
  (PermissionError READ_ONLY_PROJECT), as for add_source. Where a source may write
  (findings 2 and 3): an admin sends it only into projects every member of the workspace
  reaches (the project's rule gives everyone "read" or "write") and that the admin may write;
  making a project for it (`new_project`), or choosing a project private to some people, is for an
  owner and for a view acting for no person (the operator), and an admin who tries gets
  PermissionError(OWNER_DECIDES), decided before a new project's name is looked at. Switched
  on with its own projects, the same rule holds for the projects its config names, and one the
  admin does not reach is refused in the same words, never by its name. `new_project`: a
  project of that name is
  made in the same transaction, of the general kind, private (its rule gives members
  nothing), made by the view's person, and added to the list; the project is made only when
  the source is switched on with it, so a name that is taken or invalid (ValueError), a cap
  on projects (OverLimit) or any refusal after it leaves nothing behind. Switching on a source
  that is on keeps `enabled_at`; switching on again after off moves it to now and keeps the
  cursor. Audited as source.enabled with the source and its projects. A call that writes
  (other projects for a source that is on) lets go of the claim of a run under way, exactly
  as disable_source does: that run writes nothing more and stores nothing, so the next run
  writes into the new projects from the cursor the last finished run left.
- `disable_source(name)` switches it off: its cursor, last report and notes stay. Audited as
  source.disabled with the source. It also lets go of the claim of a run under way: that
  run finds the claim gone before its next draft (holds_source_claim), writes nothing more
  and stores nothing (`superseded`), so no note lands and no cursor moves once the source
  is off, beyond a note already being written; switched on again, it is not busy.
- Both: KeyError for no such source (by name, ignoring case, as get_source finds it); only a
  view acting for no person, an owner or an admin, or a member for the sources of their own
  account (R4, below; PermissionError NOT_A_SOURCE_MANAGER, NotASourceManager, for a member
  with no account); refused on a narrowed view (cairn.store.reach.MANAGEMENT). A call
  that changes nothing writes nothing, no audit event either, and a refused one writes
  nothing.
- Every manager sees every source and can stop it (findings 2 and 3). list_sources and
  get_source show every source of the workspace to every owner and admin, and to a view
  acting for no person: its name, kind, state, last run and failure. Where it writes is
  shown project by project only for the projects the viewer reaches; SourceInfo.unreached says it
  also writes into a project the viewer does not reach, never which. Any owner or admin
  switches any source off, whatever it writes into. Switching it on, or changing where it
  writes, follows the rule above, and an admin may do neither to a source whose config
  names a project they do not reach, whatever projects they name (OWNER_DECIDES): an owner put
  it there, and only an owner moves it. One whose config names a project they may only read
  is refused as before (READ_ONLY_PROJECT). A member sees only the sources of their own
  account (R4, below), and a narrowed connection neither lists nor manages sources. A run
  (claim_source and what follows) still finds only a source whose every project the view
  reaches.
- `claim_source` on a source that is off raises SourceOff and writes nothing: a run of it
  reads nothing and records nothing (cairn.sources.runner answers SyncReport(off=True)).
  The claim holds the id of each project the config names, read in the claim's write
  (SourceClaim.project_ids): the run writes into those ids only, never by name.

Credentials, and sources made from one (R2; tests/unit/test_credentials_unit.py and
tests/unit/test_sources_from_a_credential_unit.py)
- A credential is an account at a service: one settings row, CREDENTIAL_PREFIX and its name,
  holding its kind, its owner (a member's user id, or None for the workspace's own), its
  settings (the kind's credential settings only, cairn.sources.CHOOSING: names and
  addresses, never a secret) and when it was added. A source made from one has a second
  row, SOURCE_CREDENTIAL_PREFIX and its name, naming the credential. Both are written here
  alone, are no setting get_setting or set_setting knows, and are never exported;
  remove_source deletes its row in its own write, and delete_account every row.
- `add_credential` (the command line only), `list_credentials` (managers every one, a member
  their own), `remove_credential` (an owner of the workspace, the operator, or its owner
  when they own or administer the workspace; a member asks an owner, A_MEMBERS_ACCOUNT):
  disconnecting switches each source made from it off in the same write (DISCONNECTED),
  and the source's row then names a credential no name can be, so none of it can be
  switched on again, even when a credential is registered later under that name.
- `add_source_from` merges the credential's settings with a person's selector, options and
  one project (cairn.sources.config_from), checks it with the kind and adds it off.
- A person's credential is routed by that person alone (NOT_YOUR_ACCOUNT): only they add,
  switch on, re-route, preview or remove its sources, into projects they manage
  (NOT_MANAGED); any manager switches one off. The operator's credential is routed as any
  source is. A view acting for no person may do all of it.
- A run of a source made from a person's credential (claim_source, and holds_source_claim
  before each note) needs that person still a member who writes into each of its
  projects, read from the person's id; otherwise nothing is read or written and the claim
  is let go. Refused at the claim, the source is switched off in that one write, with
  OWNER_CANNOT_WRITE as its last error and as source.disabled's reason (claim_source raises
  OwnerCannotWrite); a pass after it finds it off. Found so mid-run (holds_source_claim),
  its last error says so. Switching it on again is its owner's, as for any source.
- Two sources of one account never write one item (R3, invariant 4). `add_source_from`
  refuses a source that can select an item another source made from the same account
  selects, on or off, or that cairn cannot show selects none of its items
  (cairn.sources.overlaps, OVERLAP). `held_selectors(claim)` names what the account's
  other sources that are on select, so a run holds a Gmail message carrying
  two of their labels (cairn.sources.runner). The same check runs when a source is switched
  on or moved (enable_source) and for a preview before saving. A pair made before its kind's
  rule refused it stays as it is (nothing re-checks it when the server starts), neither is
  switched on again or moved while the other exists, and `overlapping_source(claim)` names
  the other while both are on, so a run of either reads and writes nothing (OVERLAP_ON). A
  source added with source-add has none of it.

A member's own sources (R4; tests/acceptance/test_r4_a_members_own_sources.py)
- Who handles which source is decided in one place (_may_handle_sources, _own_sources,
  _handled): a view acting for no person, an owner or an admin, every source; a member who
  owns an account, the sources made from it, exactly as an owner routes their own account;
  a member with none, nothing (NotASourceManager, as S2 pins). For a member, any other
  source is as a name that does not exist (KeyError, None, False, as each call answers
  one), and the workspace's accounts and other people's are out of sight. The calls of a
  run (claim_source and what follows) and add_source stay a manager's.

A preview (R1; tests/acceptance/test_r1_preview.py)
- `source_to_preview(name)` hands a source's whole config to a view that may switch it on
  with the projects it names, and `source_items_written(name, items)` reads which items it
  wrote, without a claim. Both only read, and both are refused on a narrowed view.
- `source_from_to_preview(...)` (R4) hands the config add_source_from would store, checked
  by the same code (_checked_from) in the same words, and writes nothing: a preview before
  saving. It takes no name, so it checks none.
- A source name is one per workspace. Saving checks it last, after the account, what it
  reads and where it writes; a name held by a source the caller does not see is refused as
  NAME_TAKEN, the same whatever holds it.

Items (I1; frozen by tests/acceptance/test_i1_source_items.py)
- The items each source wrote (cairn.sources.NoteDraft.item) are kept in the table
  `source_items`, one row per source and item: the first ITEM_DIGEST hex characters of the
  item's SHA-256, never the item, and when it was recorded. The size of a row and of a
  million is worked out where the table is defined (cairn.store.schema).
- `written_source_items(claim, items)` says which of `items` the claim's source recorded:
  one read for a whole pull, in statements of at most ITEM_BATCH digests each.
  `record_source_item(claim, item)` records one, while the claim is still the run's (False,
  and nothing written, once it is not: a superseded run records nothing). Both are refused
  on a narrowed view, as the other calls of a run (cairn.store.reach.MANAGEMENT).
- A source switched off and on keeps its items. `remove_source` removes them in its own
  write, so a source added again under the same name (which may get the same `seq`) starts
  with none; `delete_account` removes those of every source of the workspace.
- An export carries no source (cairn.store.portable), so it carries no item either, and an
  import records none: a source in the workspace imported into writes each item once more.

A project that stops existing
- A source names its projects by name, so a project's name must never outlive the project:
  `delete_project` (cairn.store.erasure) takes the name out of the config of every source
  that names it (ignoring case), in the same write, and lets go of a run under way. A
  source left with no project is switched off, recorded as source.disabled with the reason
  PROJECT_DELETED (and not the project's name), before the project.deleted event. A project made
  later under that name gets nothing until someone allowed to switches the source on and
  chooses it. Projects are never renamed; a deleted workspace takes its sources with it, and
  an import never replaces a project (it refuses a name that is taken).
- A project that stops being every member's is treated the same way when anyone but an owner
  or a view acting for no person narrows it: set_project_access (cairn.store.access) taking
  `everyone` to "none" takes it out of every source that names it, in the same write, with
  the reason PROJECT_PRIVATE. Otherwise its maker could make a project every member reaches,
  have a source sent there, then make it private, which rule (a) leaves to an owner.
"""

import hashlib
import json
import secrets
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from cairn.kinds import GENERAL
from cairn.store.access import _LEVELS, READ_ONLY_PROJECT
from cairn.store.audit import REMOVED_SOURCE
from cairn.store.base import MANAGER_ROLES, StoreBase, _iso, _require_account
from cairn.store.memory import PROJECT_NAME, NameTaken, _check_new_project
from cairn.store.models import CredentialInfo, SourceClaim, SourceInfo

SOURCE_CLAIM_TTL = timedelta(minutes=15)  # after this, another run may take a claim
ERROR_MAX = 1_000  # characters kept of a run's error
NOT_A_SOURCE_MANAGER = (
    "only the workspace's owners and admins manage its sources, and a member those made from"
    " their own account"
)
NO_PROJECT_NAMED = "a source writes into at least one project: name one to switch it on"
PROJECT_DELETED = "the project it wrote into was deleted"  # why a source went off by itself
OWNER_DECIDES = (
    "only an owner decides where a source writes when not every member of the workspace"
    " would reach it: choose projects every member reaches, or ask an owner"
)
_COLUMNS = (
    "name, kind, config, cursor, created_at, last_run_at, last_report, last_error, enabled,"
    " enabled_at"
)
# Credentials (R2): two kinds of settings row, written by this module alone, refused by
# get_setting and set_setting (they are no known setting), never exported. The names after
# the prefixes are case-folded: a source's and a credential's names are unique ignoring case.
CREDENTIAL_PREFIX = "credential:"  # + the credential's name: {"name", "kind", "owner", ...}
SOURCE_CREDENTIAL_PREFIX = "source.credential:"  # + the source's name: its credential's name
NOT_YOUR_ACCOUNT = "only the person whose account it is chooses what it reads and where"
NOT_YOURS_TO_DISCONNECT = (
    "only the person whose account it is, or an owner of the workspace, disconnects it"
)
# R4: a member's account was registered for them by the operator; an owner disconnects it.
A_MEMBERS_ACCOUNT = "an owner of the workspace disconnects a member's account: ask one"
# A source name is one per workspace: a name held by a source the caller does not see is
# refused in these words, whatever holds it, which neither repeat nor confirm the name.
NAME_TAKEN = "that name is taken: choose another"
NOT_MANAGED = (
    "a source made from your account writes only into a project you manage: one you made,"
    " one whose rule gives you 'manage', or a new private one"
)
OWNER_CANNOT_WRITE = "the person whose account it reads no longer writes into where it writes"
# Whose a credential is when its person is gone: still theirs, never the workspace's.
FORMER_MEMBER = "a former member"
DISCONNECTED = "its account was disconnected"  # why a source went off, and why it stays off
CREDENTIALS_MAX = 100  # accounts a workspace holds (add_credential)
SOURCES_MAX = 100  # sources a workspace holds when one is made from an account (add_source_from)
HOSTED_DISCONNECT = (
    "a Google account connected in the browser is disconnected with the Disconnect under Google"
    " on the Sources page, which also ends Google's permission when it is the last connection"
    " here of that Google address"
)
HOSTED_LEFT = (  # removing one with the switch off: nothing here can end Google's permission
    "This account was connected in the browser: its row is removed here, and Google's"
    " permission stays until its person ends it in their Google account's settings, among the"
    " third-party apps and services with access to their account."
)
ACCOUNT_GONE = "the account it reads is gone"  # linked to a credential that does not exist
NOT_THE_OPERATOR = "only the command line registers an account (credential-add)"
OPERATORS_SOURCE = (
    "a source added on the command line is removed there (source-remove), by whoever runs"
    " the server"
)
NO_PROJECT_CHOSEN = "a source writes into at least one project: name one, or a new one"
ONE_PROJECT = "a source made from an account writes into one project: name it, or a new one"
# What a source's link names once its credential is disconnected: a space, which no
# credential's name holds, so a credential registered later under the name finds none of it.
_DISCONNECTED_LINK = "{} (disconnected)"
_SHOWN_NAME = "a credential name is 1 to 64 letters, digits, '.', '_' or '-'"


class NotASourceManager(PermissionError):
    """NOT_A_SOURCE_MANAGER: the view's person neither manages the workspace nor owns an
    account in it (R4), so no source is theirs to handle; nothing was read or written."""


class OwnerCannotWrite(PermissionError):
    """A run was refused because the person whose credential the source reads is no longer
    a member, or no longer writes into a project it writes into (invariant 5), or because
    the credential it is linked to does not exist (ACCOUNT_GONE): nothing was read or
    written, and the source was switched off with the reason as its `last_error`."""


ITEM_BATCH = 500  # item digests one statement of written_source_items looks up, at most
ITEM_DIGEST = 32  # hex characters of an item's SHA-256 that source_items keeps (128 bits)


def _item_digest(item: str) -> str:
    """What source_items keeps of an item: never the item itself."""
    return hashlib.sha256(item.encode()).hexdigest()[:ITEM_DIGEST]


class SourceOff(Exception):
    """A run was asked of a source that is off (Store.claim_source): nothing was written."""


# The counts a run's report holds, as its audit event shows them.
_COUNTS = ("saved", "unchanged", "refused", "rejected", "skipped", "malformed")


class SourceMethods(StoreBase):
    def add_source(self, name: str, kind: str, config: dict[str, Any]) -> SourceInfo:
        """A new source of this workspace, recorded as source.added. ValueError: a name
        that breaks the project-name rules or is taken in this workspace (ignoring case), a
        kind that is not one of cairn.sources.KINDS, a config that kind refuses, or a project
        the config names that the workspace does not have; then nothing is written."""
        from cairn.sources import KINDS  # cairn.sources imports this package

        if not isinstance(name, str) or not PROJECT_NAME.fullmatch(name):
            raise ValueError("a source name is 1 to 64 letters, digits, '.', '_' or '-'")
        if kind not in KINDS:
            raise ValueError(f"no kind of source named {kind!r}: use {', '.join(KINDS)}")
        checked = KINDS[kind].check(config)
        on = not KINDS[kind].starts_off
        stamp = self._stamp()
        made = SourceInfo(
            name=name,
            kind=kind,
            config=checked,
            cursor=None,
            created_at=stamp,
            last_run_at=None,
            last_report=None,
            last_error=None,
            enabled=on,
            enabled_at=stamp if on else None,
        )
        with self._write() as con:
            _require_account(con, self._account_id)
            self._may_manage_sources(con)
            self._require_writable(con, checked["projects"])
            if con.execute(  # a name is taken whoever's source holds it
                "SELECT 1 FROM sources WHERE account_id = ? AND name = ?", (self._account_id, name)
            ).fetchone():
                raise ValueError(f"source name already used: {name!r}")
            con.execute(
                "INSERT INTO sources (account_id, name, kind, config, created_at, enabled,"
                " enabled_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (self._account_id, name, kind, json.dumps(checked), stamp, int(on),
                 made.enabled_at),
            )  # fmt: skip
            self._audit(con, "source.added", detail={"source": name, "kind": kind})
        return made

    def enable_source(
        self, name: str, projects: list[str] | None = None, *, new_project: str | None = None
    ) -> SourceInfo:
        """Switch the source on, writing into `projects` (names) when given, else into the
        projects its config names, plus `new_project` when given (made here). The rules are in
        this module's docstring."""
        from cairn.sources import KINDS  # cairn.sources imports this package

        if projects is not None and (
            isinstance(projects, str) or not all(isinstance(b, str) for b in projects)
        ):
            raise ValueError("projects lists the names of projects")
        if new_project is not None:
            _check_new_project(new_project, GENERAL)
        with self._write() as con:
            row = self._handled(con, name)
            if row is None:
                raise KeyError(name)
            stored, kind, config = row[0], row[1], json.loads(row[2])
            was_on = bool(row[8])
            if kind not in KINDS:
                raise ValueError(f"no kind of source named {kind!r}: use {', '.join(KINDS)}")
            own = self._routes_own(con, stored)  # R2: a person's account is theirs to route
            wanted = list(config.get("projects", []) if projects is None else projects)
            if new_project is not None:
                wanted.append(new_project)
            checked = KINDS[kind].check({**config, "projects": wanted})
            if not checked.get("projects"):
                raise ValueError(NO_PROJECT_NAMED)
            decides = own or self._decides_destinations(con)
            if not decides and self._writes_out_of_reach(con, config.get("projects", [])):
                raise PermissionError(OWNER_DECIDES)  # where it writes now is not theirs
            if new_project is not None:
                if not decides:  # before the name is looked at: it tells nothing of others
                    raise PermissionError(OWNER_DECIDES)
                if self._project_named(con, new_project) is not None:  # type: ignore[attr-defined]
                    raise NameTaken(new_project)
                self._admit_projects(con, 1)  # type: ignore[attr-defined]  # OverLimit
                self._insert_project(con, new_project, "", GENERAL, "none")  # type: ignore[attr-defined]
            if own:
                self._require_managed(con, checked["projects"])
            if not decides and projects is None:  # its own projects, which the caller did not name
                self._require_shared(con, checked["projects"], named=False)
            self._require_writable(con, checked["projects"])
            if not decides:
                self._require_shared(con, checked["projects"], named=True)
            self._require_no_overlap(con, stored, kind, checked)  # R3: as when it was made
            if was_on and checked == config:
                return self._shown(con, row)  # as it is: nothing to write
            con.execute(  # a run under way is let go, as disable_source lets it go
                "UPDATE sources SET config = ?, enabled = 1,"
                " enabled_at = CASE WHEN enabled THEN enabled_at ELSE ? END,"
                " claim = NULL, claimed_at = NULL"
                " WHERE account_id = ? AND name = ?",
                (json.dumps(checked), self._stamp(), self._account_id, stored),
            )
            self._audit(
                con, "source.enabled", detail={"source": stored, "projects": checked["projects"]}
            )
            return self._shown(con, self._find_any(con, stored))  # type: ignore[arg-type]

    def disable_source(self, name: str) -> SourceInfo:
        """Switch the source off: no run reads it until it is switched on again. Its cursor,
        its last report and what it noted stay. The rules are in this module's docstring."""
        with self._write() as con:
            row = self._handled(con, name)  # whatever it writes into: any manager stops it
            if row is None:
                raise KeyError(name)
            if row[8]:
                con.execute(
                    "UPDATE sources SET enabled = 0, claim = NULL, claimed_at = NULL"
                    " WHERE account_id = ? AND name = ?",
                    (self._account_id, row[0]),
                )
                self._audit(con, "source.disabled", detail={"source": row[0]})
                row = self._find_any(con, row[0])
            return self._shown(con, row)  # type: ignore[arg-type]

    def list_sources(self) -> list[SourceInfo]:
        """This workspace's sources, by name ignoring case: every one of them, for a view
        acting for no person and for every owner and admin; for a member, those made from
        their own account (R4); none on a narrowed view (cairn.store.reach). Where a source
        writes is shown only as far as the view reaches: its config's `projects` keeps the
        projects the view reaches, and `unreached` says that it also writes into a project
        the view does not reach, never which. The last run's `declined` list keeps only the
        drafts whose project the view reaches."""
        with self._read() as con:
            own = self._own_sources(con)
            if own is not None and not own:
                return []
            rows = [
                row
                for row in con.execute(
                    f"SELECT {_COLUMNS} FROM sources WHERE account_id = ? ORDER BY name",
                    (self._account_id,),
                )
                if own is None or row[0].casefold() in own
            ]
            reach = self._reach(con)  # once, and the names it reaches once, whatever the rows
            reached = self._reached_names(con, reach)
            return [_shown_in(row, reach, reached) for row in rows]

    def get_source(self, name: str) -> SourceInfo | None:
        """By name, ignoring case, as list_sources shows it; None for no such source or for
        a view list_sources shows none to."""
        with self._read() as con:
            own = self._own_sources(con)
            row = self._find_any(con, name)
            if row is None or (own is not None and row[0].casefold() not in own):
                return None
            return self._shown(con, row)

    def remove_source(self, name: str) -> bool:
        """Remove a source of this workspace, recorded as source.removed; True when there was
        one. What it noted stays: notes are removed in cairn, one by one. In the same write,
        every earlier event of the source, and source.removed itself (whose detail keeps the
        name), name audit.REMOVED_SOURCE instead of it, so a source added later under that
        name starts with none of them (list_audit shows them to owners and to views acting
        for no person only). Who: a view acting for no person removes any source; a person
        only one made from an account (add_source_from), and one made from a person's account
        only that person (PermissionError OPERATORS_SOURCE, NOT_YOUR_ACCOUNT)."""
        with self._write() as con:
            own = self._may_handle_sources(con)
            row = self._find(con, name)
            if row is None or (own is not None and row[0].casefold() not in own):
                return False
            linked, held = self._linked(con, row[0])
            if not linked and self._user_id is not None:
                raise PermissionError(OPERATORS_SOURCE)  # source-add's: the command line's
            owner = held["owner"] if held is not None else None
            if owner is not None and self._user_id not in (None, owner):
                raise PermissionError(NOT_YOUR_ACCOUNT)  # R2: a person's account is theirs
            con.execute(  # its link to its credential (R2), in the same write
                "DELETE FROM settings WHERE account_id = ? AND name = ?",
                (self._account_id, _link_name(row[0])),
            )
            con.execute(  # its items first: they name it by its seq, which may be reused
                "DELETE FROM source_items WHERE source ="
                " (SELECT seq FROM sources WHERE account_id = ? AND name = ?)",
                (self._account_id, name),
            )
            con.execute(
                "DELETE FROM sources WHERE account_id = ? AND name = ?", (self._account_id, name)
            )
            gone = REMOVED_SOURCE.format(row[0], secrets.token_hex(4))
            con.execute(
                "UPDATE audit_events SET detail = json_set(detail, '$.source', ?)"
                " WHERE account_id = ? AND action LIKE 'source.%'"
                " AND json_extract(detail, '$.source') = ? COLLATE NOCASE",
                (gone, self._account_id, row[0]),
            )
            self._audit(con, "source.removed", detail={"source": gone, "name": row[0]})
        return True

    # A preview (R1)

    def source_to_preview(self, name: str) -> SourceInfo:
        """The source by name (ignoring case) with its whole config, for a preview, to a view
        that may switch it on with the projects it names (enable_source's rule, without the
        need for a project): a view acting for no person, an owner, or an admin when every
        project it names is one every member reaches and the admin may write. KeyError: no
        such source, or for a member one not made from their own account. PermissionError:
        NOT_A_SOURCE_MANAGER, OWNER_DECIDES (in those words
        whatever project is out of reach, never its name), READ_ONLY_PROJECT, or a narrowed
        view (cairn.store.reach.MANAGEMENT). Writes nothing."""
        with self._read() as con:
            row = self._handled(con, name)
            if row is None:
                raise KeyError(name)
            projects = json.loads(row[2]).get("projects", [])
            if self._routes_own(con, row[0]):
                self._require_managed(con, projects)
            elif not self._decides_destinations(con):
                if self._writes_out_of_reach(con, projects):
                    raise PermissionError(OWNER_DECIDES)
                self._require_shared(con, projects, named=False)
            self._require_writable(con, projects)
            return _info(row)

    def source_items_written(self, name: str, items: Iterable[str]) -> set[str]:
        """Which of `items` the source `name` recorded as written, as written_source_items
        answers a run, without a claim: what a preview counts as written before. Empty for no
        such source, or one a member does not handle. Who: as source_to_preview
        (PermissionError NOT_A_SOURCE_MANAGER); refused on a narrowed view. Writes nothing."""
        wanted = {_item_digest(item): item for item in items}
        found: set[str] = set()
        with self._read() as con:
            row = self._handled(con, name)
            if row is None or not wanted:
                return set()
            digests = list(wanted)
            for start in range(0, len(digests), ITEM_BATCH):
                batch = json.dumps(digests[start : start + ITEM_BATCH])
                found.update(
                    digest
                    for (digest,) in con.execute(
                        "SELECT item FROM source_items WHERE source ="
                        " (SELECT seq FROM sources WHERE account_id = ? AND name = ?)"
                        " AND item IN (SELECT value FROM json_each(?))",
                        (self._account_id, row[0], batch),
                    )
                )
        return {wanted[digest] for digest in found}

    # Credentials (R2)

    def add_credential(
        self,
        name: str,
        kind: str,
        settings: dict[str, Any],
        *,
        owner: str | None = None,
        secrets: tuple[str, ...] = (),
    ) -> CredentialInfo:
        """Register an account at a service as the credential `name` (credential-add), owned
        by the member whose address is `owner`, or by no person (the operator's). Its
        settings are the kind's credential settings only (cairn.sources.CHOOSING), checked by
        the kind with a stand-in selector and kept as the check gave them (its defaults
        included). It reads nothing and makes no source. PermissionError: a view acting for
        a person (NOT_THE_OPERATOR) or a narrowed one. ValueError, and nothing written: a name
        that breaks the project-name rules or is taken (ignoring case), an unknown kind, a
        selector, a reading option, `projects` or another name among the settings, settings
        the kind's check refuses, a value that holds one of `secrets` (holds_secret), or an
        `owner` who is no member. Recorded as source.credential_added."""
        from cairn.sources import CHOOSING, KINDS, holds_secret

        if self._user_id is not None:
            raise PermissionError(NOT_THE_OPERATOR)
        if not isinstance(name, str) or not PROJECT_NAME.fullmatch(name):
            raise ValueError(_SHOWN_NAME)
        if kind not in KINDS:
            raise ValueError(f"no kind of source named {kind!r}: use {', '.join(KINDS)}")
        if not isinstance(settings, dict):
            raise ValueError("a credential's settings are a JSON object")
        choosing = CHOOSING[kind]
        for setting in settings:
            if setting == "projects":
                raise ValueError(
                    "projects is chosen by the person who makes a source: a credential names"
                    " no project"
                )
            if setting in choosing.selector:
                raise ValueError(
                    f"{setting} is a selector: the person who makes a source chooses it, and a"
                    " credential holds no selector"
                )
            if setting in choosing.options:
                raise ValueError(
                    f"{setting} is a reading option: the person who makes a source sets it"
                )
            if setting not in choosing.credential:
                raise ValueError(f"a {kind} credential holds only {', '.join(choosing.credential)}")
        if holds_secret(settings, tuple(secrets)):
            raise ValueError(
                "one of the settings holds a credential: a credential names the variable that"
                " holds a secret, never the secret"
            )
        sample = {**settings, **choosing.sample, "projects": ["sample"]}
        checked = KINDS[kind].check(sample)
        kept = {key: checked[key] for key in choosing.credential if key in checked}
        stamp = self._stamp()
        with self._write() as con:
            _require_account(con, self._account_id)
            owner_id = None if owner is None else self._member_by_address(con, owner)
            if _credential(con, self._account_id, name) is not None:
                raise ValueError(f"credential name already used: {name!r}")
            if len(_rows(con, self._account_id, CREDENTIAL_PREFIX)) >= CREDENTIALS_MAX:
                raise ValueError(
                    f"this workspace holds {CREDENTIALS_MAX} accounts already: disconnect one"
                    " first (credential-remove)"
                )
            value = {
                "name": name, "kind": kind, "owner": owner_id, "settings": kept,
                "added_at": stamp,
            }  # fmt: skip
            _set_row(con, self._account_id, CREDENTIAL_PREFIX + name.casefold(), value, stamp)
            self._audit(con, "source.credential_added", detail={"credential": name, "kind": kind})
            return self._credential_info(con, value)

    def list_credentials(self) -> list[CredentialInfo]:
        """The workspace's credentials by name: every one for a view acting for no person,
        an owner or an admin; a member's own for a member. Refused on a narrowed view."""
        with self._read() as con:
            manages = self._manages(con)
            found = [
                held
                for _, held in _rows(con, self._account_id, CREDENTIAL_PREFIX)
                if manages or (self._user_id is not None and held["owner"] == self._user_id)
            ]
            return self._credential_infos(con, found)

    def credential_hosted(self, name: str) -> bool:
        """Whether the credential `name` is a Google account connected in the browser
        (cairn.store.hosted); False for any other, or none. Reads its settings row only."""
        with self._read() as con:
            held = _credential(con, self._account_id, name)
        return bool(held and held.get("hosted"))

    def remove_credential(self, name: str, *, hosted: bool = False) -> list[str]:
        """Disconnect the credential `name`: in the same write, each source made from it that
        is on is switched off (source.disabled, reason DISCONNECTED) and every source made
        from it stays listed, off, and can no longer be switched on; their notes stay. The
        names of the sources it switched off. KeyError: no such credential, or one out of the
        view's sight (list_credentials). PermissionError: neither the person whose it is, nor
        an owner of the workspace, nor a view acting for no person (NOT_YOURS_TO_DISCONNECT);
        its person when a member (A_MEMBERS_ACCOUNT, R4: an owner disconnects it).
        Recorded as source.credential_removed. A Google account connected in the browser
        (cairn.store.hosted) is refused (PermissionError HOSTED_DISCONNECT) unless `hosted`
        (Store.disconnect_google) or the switch is off (no google_custody: nothing here could
        revoke it); its sealed token's row is deleted in the same write."""
        with self._write() as con:
            held = _credential(con, self._account_id, name)
            if held is None or not self._sees_credential(con, held):
                raise KeyError(name)
            if held.get("hosted") and not hosted and getattr(self, "google_custody", None):
                raise PermissionError(HOSTED_DISCONNECT)  # with the switch off, it can go
            role = self._role_in(con)
            if not (self._user_id is None or role == "owner"):
                if self._user_id != held["owner"]:
                    raise PermissionError(NOT_YOURS_TO_DISCONNECT)
                if role not in MANAGER_ROLES:  # R4: a member's own, which an owner disconnects
                    raise PermissionError(A_MEMBERS_ACCOUNT)
            switched = self._drop_credential(con, held)
        return switched

    def _drop_credential(self, con: sqlite3.Connection, held: dict) -> list[str]:
        """Inside a write: remove_credential's work once it is allowed (also used for a
        connected Google account, cairn.store.hosted). The sources it switched off."""
        switched: list[str] = []
        stamp = self._stamp()
        for row_name, source in self._links_to(con, held["name"]):
            found = con.execute(
                "SELECT name, enabled FROM sources WHERE account_id = ? AND name = ?",
                (self._account_id, source),
            ).fetchone()
            if found is not None and found[1]:
                con.execute(
                    "UPDATE sources SET enabled = 0, claim = NULL, claimed_at = NULL"
                    " WHERE account_id = ? AND name = ?",
                    (self._account_id, found[0]),
                )
                detail = {"source": found[0], "reason": DISCONNECTED}
                self._audit(con, "source.disabled", detail=detail)
                switched.append(found[0])
            gone = _DISCONNECTED_LINK.format(held["name"])
            _set_row(con, self._account_id, row_name, gone, stamp)
        con.execute(
            "DELETE FROM settings WHERE account_id = ? AND name = ?",
            (self._account_id, CREDENTIAL_PREFIX + held["name"].casefold()),
        )
        con.execute(  # a connected Google account's sealed token goes with it
            "DELETE FROM credential_tokens WHERE account_id = ? AND credential = ?",
            (self._account_id, held["name"]),
        )
        detail = {"credential": held["name"], "kind": held["kind"]}
        self._audit(con, "source.credential_removed", detail=detail)
        return sorted(switched, key=str.casefold)

    def add_source_from(
        self,
        name: str,
        credential: str,
        select: object,
        options: object,
        project: str | None,
        *,
        new_project: str | None = None,
    ) -> SourceInfo:
        """A new source `name` made from the credential `credential`: its config is the
        credential's settings with `select` (what it reads), `options` (how) and one project,
        `project` or `new_project` (made here, private, of the general kind), checked by the
        kind as today (cairn.sources.config_from, then KINDS[kind].check). It is added off,
        whatever its kind's `starts_off`, and linked to the credential (a settings row).

        Who: a view acting for no person, an owner or an admin, and a member who owns an
        account here (R4; NOT_A_SOURCE_MANAGER for a member with none). A person's credential
        is used by that person alone (NOT_YOUR_ACCOUNT; a member sees no other's), into a
        project they manage (NOT_MANAGED) or a new private one of their own. The operator's
        credential is routed as enable_source routes today: an admin into a project every
        member reaches (OWNER_DECIDES). KeyError: no such credential in the view's sight.
        ValueError: a name that breaks the rules or is taken, what config_from or the kind
        refuses, no project or two (NO_PROJECT_CHOSEN, ONE_PROJECT), a project the view does
        not reach. PermissionError READ_ONLY_PROJECT. Nothing is written on a refusal.
        Recorded as source.added with the credential."""
        _check_source_from(name, project, new_project)
        stamp = self._stamp()
        with self._write() as con:
            held, kind, checked = self._checked_from(
                con, name, credential, select, options, project, new_project, make=True
            )
            con.execute(
                "INSERT INTO sources (account_id, name, kind, config, created_at, enabled,"
                " enabled_at) VALUES (?, ?, ?, ?, ?, 0, NULL)",
                (self._account_id, name, kind, json.dumps(checked), stamp),
            )
            _set_row(con, self._account_id, _link_name(name), held["name"], stamp)
            detail = {"source": name, "kind": kind, "credential": held["name"]}
            self._audit(con, "source.added", detail=detail)
            return self._shown(con, self._find_any(con, name))  # type: ignore[arg-type]

    def source_from_to_preview(
        self,
        name: str,
        credential: str,
        select: object,
        options: object,
        project: str | None,
        *,
        new_project: str | None = None,
    ) -> SourceInfo:
        """The source add_source_from would make, with its whole config, for a preview before
        saving (R4, part 2): every check add_source_from makes but those on the source's
        name, in the same words (a new project's name included: taken, invalid, or past the
        plan's cap), and nothing written: no source, no link, no project, no audit event. The
        answer is off, with no cursor and no run. Refused on a narrowed view. It takes no
        name, so it checks none: neither the name's rules nor whether it is taken."""
        _check_source_from(name, project, new_project, naming=False)
        with self._read() as con:
            _, kind, checked = self._checked_from(
                con, name, credential, select, options, project, new_project, make=False
            )
        return SourceInfo(
            name=name, kind=kind, config=checked, cursor=None, created_at=self._stamp(),
            last_run_at=None, last_report=None, last_error=None, enabled=False,
            enabled_at=None,
        )  # fmt: skip

    def _checked_from(
        self,
        con: sqlite3.Connection,
        name: str,
        credential: str,
        select: object,
        options: object,
        project: str | None,
        new_project: str | None,
        *,
        make: bool,
    ) -> tuple[dict, str, dict]:
        """What saving a source made from an account checks, on `con`: (the credential as
        stored, its kind, the config the kind checked). `make`: the new project is made here,
        in the caller's write; otherwise (a preview) everything that making it would refuse is
        refused, and it is not made. The one place add_source_from and a preview before saving
        ask, so they refuse the same things in the same words."""
        from cairn.sources import KINDS, OVERLAP, config_from, overlaps

        _require_account(con, self._account_id)
        self._may_handle_sources(con)
        held = _credential(con, self._account_id, credential)
        if held is None or not self._sees_credential(con, held):
            raise KeyError(credential)
        own = held["owner"] is not None and self._user_id is not None
        if own and self._user_id != held["owner"]:
            raise PermissionError(NOT_YOUR_ACCOUNT)
        kind = held["kind"]
        target = project if new_project is None else new_project
        checked = KINDS[kind].check(
            config_from(kind, held["settings"], select, options, [target])  # type: ignore[list-item]
        )
        (held_now,) = con.execute(
            "SELECT count(*) FROM sources WHERE account_id = ?", (self._account_id,)
        ).fetchone()
        if held_now >= SOURCES_MAX:
            raise ValueError(f"this workspace has {SOURCES_MAX} sources already: remove one first")
        for other, config in self._made_from(con, held["name"]):  # R3: on or off
            if overlaps(kind, checked, config):
                raise ValueError(OVERLAP.format(other))
        if new_project is not None:
            if not own and not self._decides_destinations(con):
                raise PermissionError(OWNER_DECIDES)
            if self._project_named(con, new_project) is not None:  # type: ignore[attr-defined]
                raise NameTaken(new_project)
            self._admit_projects(con, 1)  # type: ignore[attr-defined]  # OverLimit
            if not make:  # its maker writes into it: nothing more to check
                return held, kind, checked
            self._insert_project(con, new_project, "", GENERAL, "none")  # type: ignore[attr-defined]
        elif own:
            self._require_managed(con, checked["projects"])
        self._require_writable(con, checked["projects"])  # as enable_source: first
        if not own and not self._decides_destinations(con):
            self._require_shared(con, checked["projects"], named=True)
        if make:  # last, so a request refused anyway learns nothing of the name
            self._name_free(con, name)
        return held, kind, checked

    def _name_free(self, con: sqlite3.Connection, name: str) -> None:
        """ValueError when a source of this name (ignoring case) is in the workspace, whoever's
        it is: one name per workspace. A caller who sees that source is told its name; one who
        does not (a member, for any source not made from their own account) is told NAME_TAKEN,
        the same whatever holds it."""
        holder = con.execute(
            "SELECT name FROM sources WHERE account_id = ? AND name = ?", (self._account_id, name)
        ).fetchone()
        if holder is None:
            return
        own = self._own_sources(con)
        if own is not None and holder[0].casefold() not in own:
            raise ValueError(NAME_TAKEN)
        raise ValueError(f"source name already used: {name!r}")

    def source_credentials(self) -> dict[str, str | None]:
        """For each source the view sees (list_sources), by its name case-folded, the name of
        the credential it was made from (None once that credential was disconnected); a source
        added with source-add has none. A read."""
        with self._read() as con:
            own = self._own_sources(con)
            if own is not None and not own:
                return {}
            names = {
                held["name"].casefold(): held["name"]
                for _, held in _rows(con, self._account_id, CREDENTIAL_PREFIX)
            }
            return {
                row_name[len(SOURCE_CREDENTIAL_PREFIX) :]: (
                    names.get(linked.casefold()) if isinstance(linked, str) else None
                )
                for row_name, linked in _rows(con, self._account_id, SOURCE_CREDENTIAL_PREFIX)
                if own is None or row_name[len(SOURCE_CREDENTIAL_PREFIX) :] in own
            }

    # Runs

    def claim_source(self, name: str) -> SourceClaim | None:
        """Take the source for one run: a compare-and-set on its claim, which succeeds when
        no run holds it or the holder's claim is SOURCE_CLAIM_TTL old. None: another run
        holds it. KeyError: no such source in this workspace, or one that names a project the
        view does not reach (as get_source, decided in this transaction): its config is
        never handed out. SourceOff: the source is off, and nothing is written.
        OwnerCannotWrite (R2, invariant 5): switched off in this write, with the reason as its
        last error. The claim's age and its new stamp are read once the write lock is held."""
        token = secrets.token_hex(16)
        with self._write() as con:
            now = self._instant()
            self._may_manage_sources(con)
            if self._find(con, name) is None:
                raise KeyError(name)
            row = con.execute(
                "SELECT name, kind, config, cursor, claimed_at, enabled, enabled_at FROM sources"
                " WHERE account_id = ? AND name = ?",
                (self._account_id, name),
            ).fetchone()
            if row is None:
                raise KeyError(name)
            stored, kind, config, cursor, claimed_at, enabled, enabled_at = row
            if not enabled:
                raise SourceOff(stored)
            names = json.loads(config).get("projects", [])
            refused = self._owner_refuses(con, stored, names)
            if refused:  # invariant 5: switched off with the reason, in this one write
                self._switch_off_refused(con, stored, refused)
            elif claimed_at and datetime.fromisoformat(claimed_at) > now - SOURCE_CLAIM_TTL:
                return None
            else:
                con.execute(
                    "UPDATE sources SET claim = ?, claimed_at = ?"
                    " WHERE account_id = ? AND name = ?",
                    (token, _iso(now), self._account_id, stored),
                )
            found = con.execute(
                "SELECT name, id FROM projects WHERE account_id = ? AND name IN"
                " (SELECT value FROM json_each(?))",
                (self._account_id, json.dumps(names)),
            ).fetchall()
        if refused:  # raised once the write that records it is committed
            raise OwnerCannotWrite(refused)
        return SourceClaim(
            name=stored,
            token=token,
            kind=kind,
            config=json.loads(config),
            cursor=cursor,
            enabled_at=enabled_at,
            project_ids={project.casefold(): project_id for project, project_id in found},
        )

    def finish_source_run(self, claim: SourceClaim, cursor: str | None, report: dict) -> bool:
        """Store a run's new cursor and report, clear the last error and release the claim,
        recording source.synced with the counts unless every one is zero. False, and
        nothing written: the claim is no longer this run's (another took it after the
        limit, or the source was removed, or it names a project the view does not reach)."""
        with self._write() as con:
            self._may_manage_sources(con)
            if self._find(con, claim.name) is None:
                return False
            done = con.execute(
                "UPDATE sources SET cursor = ?, last_report = ?, last_error = NULL,"
                " last_run_at = ?, claim = NULL, claimed_at = NULL"
                " WHERE account_id = ? AND name = ? AND claim = ?",
                (cursor, json.dumps(report), self._stamp(), self._account_id, claim.name,
                 claim.token),
            )  # fmt: skip
            if done.rowcount == 0:
                return False
            counts = {count: int(report.get(count, 0)) for count in _COUNTS}
            if any(counts.values()):
                self._audit(con, "source.synced", detail={"source": claim.name, **counts})
        return True

    def fail_source_run(self, claim: SourceClaim, error: str) -> bool:
        """Record why a run failed and release its claim; the cursor and the last report stay
        as they were. False, and nothing written, as for finish_source_run."""
        with self._write() as con:
            self._may_manage_sources(con)
            if self._find(con, claim.name) is None:
                return False
            done = con.execute(
                "UPDATE sources SET last_error = ?, last_run_at = ?, claim = NULL,"
                " claimed_at = NULL WHERE account_id = ? AND name = ? AND claim = ?",
                (str(error)[:ERROR_MAX] or "the run failed", self._stamp(), self._account_id,
                 claim.name, claim.token),
            )  # fmt: skip
            return done.rowcount > 0

    # Items (I1)

    def written_source_items(self, claim: SourceClaim, items: Iterable[str]) -> set[str]:
        """Which of `items` the claim's source recorded as written (record_source_item): one
        read, whatever their number, in statements of at most ITEM_BATCH digests each.
        Empty for a source that is gone or names a project the view does not reach, as
        holds_source_claim. Writes nothing."""
        wanted = {_item_digest(item): item for item in items}
        if not wanted:
            return set()
        digests, found = list(wanted), set()
        with self._read() as con:
            self._may_manage_sources(con)
            if self._find(con, claim.name) is None:
                return set()
            for start in range(0, len(digests), ITEM_BATCH):
                batch = json.dumps(digests[start : start + ITEM_BATCH])
                found.update(
                    digest
                    for (digest,) in con.execute(
                        "SELECT item FROM source_items WHERE source ="
                        " (SELECT seq FROM sources WHERE account_id = ? AND name = ?)"
                        " AND item IN (SELECT value FROM json_each(?))",
                        (self._account_id, claim.name, batch),
                    )
                )
        return {wanted[digest] for digest in found}

    def record_source_item(self, claim: SourceClaim, item: str) -> bool:
        """Record that the claim's source wrote `item` (its digest and now), unless it was
        recorded already. False, and nothing written: the claim is no longer this run's, as
        for finish_source_run. No audit event: the run's own counts say what it wrote."""
        digest = _item_digest(item)
        with self._write() as con:
            self._may_manage_sources(con)
            if self._find(con, claim.name) is None:
                return False
            held = con.execute(
                "SELECT seq FROM sources WHERE account_id = ? AND name = ? AND claim = ?",
                (self._account_id, claim.name, claim.token),
            ).fetchone()
            if held is None:
                return False
            con.execute(
                "INSERT INTO source_items (source, item, written_at) VALUES (?, ?, ?)"
                " ON CONFLICT (source, item) DO NOTHING",
                (held[0], digest, self._stamp()),
            )
        return True

    def held_selectors(self, claim: SourceClaim) -> frozenset[str]:
        """R3: the selectors (cairn.sources.marks) of every other source made from the same
        account as the claim's source that is on: what a run of it holds a draft for
        (NoteDraft.selectors), whichever source runs first. Empty for a source added with
        source-add, or with no such other source. Two range reads and one of the sources,
        whatever their number. Writes nothing."""
        from cairn.sources import marks

        with self._read() as con:
            self._may_manage_sources(con)
            if self._find(con, claim.name) is None:
                return frozenset()
            linked, held = self._linked(con, claim.name)
            if not linked or held is None:
                return frozenset()
            found: set[str] = set()
            for other, config in self._made_from(con, held["name"], on=True):
                if other.casefold() != claim.name.casefold():
                    found |= marks(held["kind"], config)
            return frozenset(found)

    def overlapping_source(self, claim: SourceClaim) -> str | None:
        """R3: the name of another source made from the same account as the claim's source,
        that is on and can select what it selects (cairn.sources.overlaps); None when there
        is none, and for a source added with source-add. Such a pair was made before its
        kind's rule refused it: a run of either reads and writes nothing (the runner says
        OVERLAP_ON) until one is switched off. Writes nothing."""
        from cairn.sources import overlaps

        with self._read() as con:
            self._may_manage_sources(con)
            if self._find(con, claim.name) is None:
                return None
            linked, held = self._linked(con, claim.name)
            if not linked or held is None:
                return None
            for other, config in self._made_from(con, held["name"], on=True):
                if other.casefold() != claim.name.casefold() and overlaps(
                    claim.kind, claim.config, config
                ):
                    return other
            return None

    def holds_source_claim(self, claim: SourceClaim) -> bool:
        """Whether `claim` is still the source's: False once another run took it after the
        limit, or the source was removed, or it names a project the view does not reach, or
        its owner check fails (invariant 5: its person no longer writes where it writes, or
        its account is gone). A run asks before each note it writes. Writes nothing, but
        for that last: then, in one write, the reason becomes the source's last error and
        the claim is let go."""
        with self._read() as con:
            self._may_manage_sources(con)
            if self._find(con, claim.name) is None:
                return False
            row = con.execute(
                "SELECT 1 FROM sources WHERE account_id = ? AND name = ? AND claim = ?",
                (self._account_id, claim.name, claim.token),
            ).fetchone()
            if row is None:
                return False
            refused = self._owner_refuses(con, claim.name, claim.config.get("projects", []))
            if refused is None:
                return True
        with self._write() as con:  # invariant 5, mid-run: recorded, and the claim let go
            self._refuse_run(con, claim.name, refused, claim.token)
        return False

    # A project that stops existing

    def _forget_destination(
        self, con: sqlite3.Connection, project: str, reason: str = PROJECT_DELETED
    ) -> None:
        """Take the project named `project` (being deleted, or made private, in `con`'s write) out
        of the config of every source that names it, ignoring case, and let go of a run
        under way. A source left with no project is switched off, recorded as source.disabled
        with `reason` (PROJECT_DELETED, PROJECT_PRIVATE) and never the project's name."""
        rows = con.execute(
            "SELECT name, config, enabled FROM sources WHERE account_id = ? AND EXISTS"
            " (SELECT 1 FROM json_each(sources.config, '$.projects') WHERE value = ?"
            " COLLATE NOCASE) ORDER BY name",
            (self._account_id, project),
        ).fetchall()
        for name, config, enabled in rows:
            settings = json.loads(config)
            left = [b for b in settings["projects"] if b.casefold() != project.casefold()]
            off = enabled and not left
            con.execute(
                "UPDATE sources SET config = ?, enabled = ?, claim = NULL, claimed_at = NULL"
                " WHERE account_id = ? AND name = ?",
                (json.dumps({**settings, "projects": left}), int(enabled and bool(left)),
                 self._account_id, name),
            )  # fmt: skip
            if off:
                self._audit(con, "source.disabled", detail={"source": name, "reason": reason})

    # Helpers: credentials (R2)

    def _linked(self, con: sqlite3.Connection, source: str) -> tuple[bool, dict | None]:
        """Whether the source was made from a credential, and that credential as stored
        (None once it was disconnected)."""
        row = con.execute(
            "SELECT value FROM settings WHERE account_id = ? AND name = ?",
            (self._account_id, _link_name(source)),
        ).fetchone()
        if row is None:
            return False, None
        return True, _credential(con, self._account_id, json.loads(row[0]))

    def _routes_own(self, con: sqlite3.Connection, source: str) -> bool:
        """Whether the view's person routes the source as their own account's (True), or as
        today (False: no credential, the operator's, or a view acting for no person).
        ValueError: its credential was disconnected. PermissionError(NOT_YOUR_ACCOUNT): it
        reads another person's account."""
        linked, held = self._linked(con, source)
        if not linked:
            return False
        if held is None:
            raise ValueError(f"{DISCONNECTED}: make a source from an account to read it again")
        if held["owner"] is None or self._user_id is None:
            return False
        if self._user_id != held["owner"]:
            raise PermissionError(NOT_YOUR_ACCOUNT)
        return True

    def _require_managed(self, con: sqlite3.Connection, projects: list[str]) -> None:
        """Each project exists in the view's reach (ValueError, as if it did not exist) and the
        view's person manages it: an owner, its maker, or "manage" in its rule (NOT_MANAGED)."""
        reach = self._reach(con)
        owner = self._role_in(con) == "owner"
        for project in projects:
            found = con.execute(
                "SELECT id, maker_id FROM projects WHERE account_id = ? AND name = ?",
                (self._account_id, project),
            ).fetchone()
            if found is None or (reach is not None and found[0] not in reach):
                raise ValueError(f"this workspace has no project named {project!r}")
            if owner or found[1] == self._user_id:
                continue
            held = con.execute(
                "SELECT access FROM project_people WHERE project_id = ? AND user_id = ?",
                (found[0], self._user_id),
            ).fetchone()
            if held != ("manage",):
                raise PermissionError(NOT_MANAGED)

    def _owner_refuses(
        self, con: sqlite3.Connection, source: str, projects: list[str]
    ) -> str | None:
        """Invariant 5: why a run of the source may not write, or None when it may.
        ACCOUNT_GONE: it is linked to a credential that does not exist (it never runs as
        the workspace's). OWNER_CANNOT_WRITE: it was made from a person's credential and that
        person is no longer a member, or no longer writes into one of `projects`. Read from
        the owner's id, never the view's: a run acts for no person."""
        linked, held = self._linked(con, source)
        if linked and held is None:
            return ACCOUNT_GONE
        owner = held["owner"] if held else None
        if owner is None:
            return None
        role = con.execute(
            "SELECT role FROM memberships WHERE account_id = ? AND user_id = ?",
            (self._account_id, owner),
        ).fetchone()
        if role is None:
            return OWNER_CANNOT_WRITE
        for project in projects:
            found = con.execute(
                "SELECT id FROM projects WHERE account_id = ? AND name = ?",
                (self._account_id, project),
            ).fetchone()
            if found is None:
                return OWNER_CANNOT_WRITE
            if role[0] == "owner":
                continue
            level = con.execute(
                _LEVELS + " AND b.id = ?3", (owner, self._account_id, found[0])
            ).fetchone()
            if level is None or level[1] != "write":
                return OWNER_CANNOT_WRITE
        return None

    def _refuse_run(
        self, con: sqlite3.Connection, source: str, reason: str, token: str | None = None
    ) -> None:
        """Record `reason` as the source's last error and let go of its claim (only while
        `token` holds it, when given)."""
        con.execute(
            "UPDATE sources SET last_error = ?, last_run_at = ?, claim = NULL, claimed_at = NULL"
            " WHERE account_id = ? AND name = ? AND (? IS NULL OR claim = ?)",
            (reason, self._stamp(), self._account_id, source, token, token),
        )

    def _switch_off_refused(self, con: sqlite3.Connection, source: str, reason: str) -> None:
        """Switch the source off, with `reason` as its last error and as source.disabled's
        reason, and let go of its claim: a refused claim writes once, and the passes after
        it find the source off and leave it alone. Switching it on again is its owner's."""
        con.execute(
            "UPDATE sources SET enabled = 0, last_error = ?, last_run_at = ?, claim = NULL,"
            " claimed_at = NULL WHERE account_id = ? AND name = ?",
            (reason, self._stamp(), self._account_id, source),
        )
        self._audit(con, "source.disabled", detail={"source": source, "reason": reason})

    def _sees_credential(self, con: sqlite3.Connection, held: dict) -> bool:
        """A manager sees every credential (and a view acting for no person); a member their
        own."""
        return self._manages(con) or (self._user_id is not None and held["owner"] == self._user_id)

    def _member_by_address(self, con: sqlite3.Connection, address: str) -> str:
        """The user id of the member of this workspace whose address is `address`, compared
        as users.email is; ValueError for none."""
        found = con.execute(
            "SELECT u.id FROM users AS u JOIN memberships AS m"
            " ON m.user_id = u.id AND m.account_id = ? WHERE u.email = ?",
            (self._account_id, address.strip() if isinstance(address, str) else ""),
        ).fetchone()
        if found is None:
            raise ValueError("no member of this workspace has that address")
        return found[0]

    def _links_to(self, con: sqlite3.Connection, credential: str) -> list[tuple[str, str]]:
        """(settings row name, source name case-folded) of each source made from
        `credential`, by name ignoring case."""
        return [
            (row_name, row_name[len(SOURCE_CREDENTIAL_PREFIX) :])
            for row_name, linked in _rows(con, self._account_id, SOURCE_CREDENTIAL_PREFIX)
            if isinstance(linked, str) and linked.casefold() == credential.casefold()
        ]

    def _require_no_overlap(
        self, con: sqlite3.Connection, source: str, kind: str, config: dict
    ) -> None:
        """R3: ValueError (OVERLAP) when `config`, the config the source `source` is to have,
        can select what another source made from the same account selects, on or off
        (cairn.sources.overlaps). Nothing for a source with no live link (source-add's)."""
        from cairn.sources import OVERLAP, overlaps

        linked, held = self._linked(con, source)
        if not linked or held is None:
            return
        for other, theirs in self._made_from(con, held["name"]):
            if other.casefold() != source.casefold() and overlaps(kind, config, theirs):
                raise ValueError(OVERLAP.format(other))

    def _made_from(
        self, con: sqlite3.Connection, credential: str, *, on: bool = False
    ) -> list[tuple[str, dict]]:
        """(name, config) of each source made from `credential` (only those on, with `on`):
        the links in one range read and the sources in one statement."""
        names = [source for _, source in self._links_to(con, credential)]
        return [
            (name, json.loads(config))
            for name, config in con.execute(
                "SELECT name, config FROM sources WHERE account_id = ? AND lower(name) IN"
                " (SELECT value FROM json_each(?)) AND (? = 0 OR enabled = 1) ORDER BY name",
                (self._account_id, json.dumps(names), int(on)),
            )
        ]

    def _credential_info(self, con: sqlite3.Connection, held: dict) -> CredentialInfo:
        return self._credential_infos(con, [held])[0]

    def _credential_infos(self, con: sqlite3.Connection, helds: list[dict]) -> list[CredentialInfo]:
        """The credentials as CredentialInfo, in two statements whatever their number: one
        range read of the links (how many sources each was made into) and one of the names of
        their owners who are members here (None for one who left)."""
        counts: dict[str, int] = {}
        for _, linked in _rows(con, self._account_id, SOURCE_CREDENTIAL_PREFIX):
            if isinstance(linked, str):
                counts[linked.casefold()] = counts.get(linked.casefold(), 0) + 1
        owners = sorted({held["owner"] for held in helds if held["owner"] is not None})
        named = dict(
            con.execute(
                "SELECT u.id, u.name FROM users AS u JOIN memberships AS m"
                " ON m.user_id = u.id AND m.account_id = ?"
                " WHERE u.id IN (SELECT value FROM json_each(?))",
                (self._account_id, json.dumps(owners)),
            )
        )
        return [
            CredentialInfo(
                name=held["name"],
                kind=held["kind"],
                owner=held["owner"],
                owner_name=named.get(held["owner"]),
                account=held["settings"].get("subject"),
                added_at=held["added_at"],
                sources=counts.get(held["name"].casefold(), 0),
            )
            for held in helds
        ]

    # Helpers

    def _decides_destinations(self, con: sqlite3.Connection) -> bool:
        """Whether the view may send a source into a project that not every member reaches,
        or make one for it: a view acting for no person (the operator), or an owner."""
        return self._user_id is None or self._role_in(con) == "owner"

    def _writes_out_of_reach(self, con: sqlite3.Connection, projects: list[str]) -> bool:
        """Whether one of `projects` (a stored config's) is a project out of the view's reach."""
        reach = self._reach(con)
        if reach is None or not projects:
            return False
        found = con.execute(
            "SELECT id FROM projects WHERE account_id = ? AND name IN"
            " (SELECT value FROM json_each(?))",
            (self._account_id, json.dumps(projects)),
        ).fetchall()
        return any(project_id not in reach for (project_id,) in found)

    def _require_shared(self, con: sqlite3.Connection, projects: list[str], *, named: bool) -> None:
        """PermissionError(OWNER_DECIDES) unless every member reaches each project (its rule
        gives everyone "read" or "write"). `named`: the caller named them, so one out of
        their reach was refused already (ValueError, as if it did not exist); otherwise
        (the config's own) one out of reach is refused here, in the same words, so its
        name is never said."""
        reach = self._reach(con)
        for project in projects:
            found = con.execute(
                "SELECT id, everyone FROM projects WHERE account_id = ? AND name = ?",
                (self._account_id, project),
            ).fetchone()
            if found is None:
                if named:
                    raise ValueError(f"this workspace has no project named {project!r}")
                continue  # gone since: _require_writable says so
            if (reach is not None and found[0] not in reach) or found[1] == "none":
                raise PermissionError(OWNER_DECIDES)

    def _require_writable(self, con: sqlite3.Connection, projects: list[str]) -> None:
        """Each project a source would write into exists in the view's reach (ValueError, as
        if it did not exist) and the view's person may write there (PermissionError)."""
        reach = self._reach(con)
        for project in projects:
            found = con.execute(
                "SELECT id FROM projects WHERE account_id = ? AND name = ?",
                (self._account_id, project),
            ).fetchone()
            if found is None or (reach is not None and found[0] not in reach):
                raise ValueError(f"this workspace has no project named {project!r}")
            if self._level(con, found[0]) != "write":  # type: ignore[attr-defined]
                raise PermissionError(READ_ONLY_PROJECT)  # a source writes there

    def _find_any(self, con: sqlite3.Connection, name: str) -> tuple | None:
        """The source with this name, whatever it writes into: what a manager sees and
        switches (list_sources, get_source, enable_source, disable_source)."""
        return con.execute(
            f"SELECT {_COLUMNS} FROM sources WHERE account_id = ? AND name = ?",
            (self._account_id, name),
        ).fetchone()

    def _shown(self, con: sqlite3.Connection, row: tuple) -> SourceInfo:
        """A stored source as this view is shown it (list_sources): the projects of its config
        the view does not reach left out, and `unreached` set when there were any."""
        reach = self._reach(con)
        return _shown_in(row, reach, self._reached_names(con, reach))

    def _reached_names(
        self, con: sqlite3.Connection, reach: frozenset[str] | None
    ) -> set[str] | None:
        """The names (case-folded) of the projects in `reach`; None for every project."""
        if reach is None:
            return None
        return {
            project.casefold()
            for (project,) in con.execute(
                "SELECT name FROM projects WHERE account_id = ? AND id IN"
                " (SELECT value FROM json_each(?))",
                (self._account_id, json.dumps(sorted(reach))),
            )
        }

    def _find(self, con: sqlite3.Connection, name: str) -> tuple | None:
        """The source with this name, unless it names a project out of the view's reach: what
        a run may claim and store through (claim_source and the calls after it), so a run
        on a view never gets the config of a source it could not have switched on."""
        row = con.execute(
            f"SELECT {_COLUMNS} FROM sources WHERE account_id = ? AND name = ?",
            (self._account_id, name),
        ).fetchone()
        return row if row and self._shows(con, row, self._reach(con)) else None

    def _shows(self, con: sqlite3.Connection, row: tuple, reach: frozenset[str] | None) -> bool:
        """Whether every project a stored source's config names is in `reach`."""
        if reach is None:
            return True
        names = json.loads(row[2]).get("projects", [])
        found = con.execute(
            "SELECT id FROM projects WHERE account_id = ? AND name IN"
            " (SELECT value FROM json_each(?))",
            (self._account_id, json.dumps(names)),
        ).fetchall()
        return len(found) == len(set(n.casefold() for n in names)) and all(
            project_id in reach for (project_id,) in found
        )

    def _own_sources(self, con: sqlite3.Connection) -> set[str] | None:
        """The one rule for who handles which source (R4): None for a view that handles every
        one (StoreBase._manages: a view acting for no person, an owner or an admin); else the
        names, case-folded, of the sources made from the person's own accounts, which a
        disconnected account no longer names. Two range reads, whatever their number."""
        if self._manages(con):
            return None
        mine = {
            held["name"].casefold()
            for _, held in _rows(con, self._account_id, CREDENTIAL_PREFIX)
            if held["owner"] is not None and held["owner"] == self._user_id
        }
        if not mine:
            return set()
        return {
            row_name[len(SOURCE_CREDENTIAL_PREFIX) :]
            for row_name, linked in _rows(con, self._account_id, SOURCE_CREDENTIAL_PREFIX)
            if isinstance(linked, str) and linked.casefold() in mine
        }

    def _may_handle_sources(self, con: sqlite3.Connection) -> set[str] | None:
        """_own_sources, for a call that handles a source: PermissionError
        (NOT_A_SOURCE_MANAGER) for a person who neither manages the workspace nor owns an
        account in it, so a member with no account is refused as before R4."""
        if self._manages(con):
            return None
        if not any(
            held["owner"] is not None and held["owner"] == self._user_id
            for _, held in _rows(con, self._account_id, CREDENTIAL_PREFIX)
        ):
            raise NotASourceManager(NOT_A_SOURCE_MANAGER)
        return self._own_sources(con)

    def _handled(self, con: sqlite3.Connection, name: str) -> tuple | None:
        """The source `name` as _find_any finds it, for a view that may handle it
        (_may_handle_sources); None for a member when it is not made from their own account,
        exactly as for a name that does not exist."""
        own = self._may_handle_sources(con)
        row = self._find_any(con, name)
        if row is None or (own is not None and row[0].casefold() not in own):
            return None
        return row

    def _may_manage_sources(self, con: sqlite3.Connection) -> None:
        """For the calls that only a manager makes: add_source (a config names the server's
        environment) and a run's (a run acts for no person)."""
        if not self._manages(con):
            raise NotASourceManager(NOT_A_SOURCE_MANAGER)


def _check_source_from(
    name: object, project: object, new_project: object, *, naming: bool = True
) -> None:
    """What add_source_from refuses before it reads anything (ValueError). `naming`: the
    source's name is checked too (a preview before saving takes none)."""
    if naming and (not isinstance(name, str) or not PROJECT_NAME.fullmatch(name)):
        raise ValueError("a source name is 1 to 64 letters, digits, '.', '_' or '-'")
    if project is None and new_project is None:
        raise ValueError(NO_PROJECT_CHOSEN)
    if project is not None and new_project is not None:
        raise ValueError(ONE_PROJECT)
    if project is not None and not isinstance(project, str):
        raise ValueError("project is a project's name")
    if new_project is not None:
        _check_new_project(new_project, GENERAL)  # type: ignore[arg-type]


def _link_name(source: str) -> str:
    """The settings row that names the credential a source was made from."""
    return SOURCE_CREDENTIAL_PREFIX + source.casefold()


def _credential(con: sqlite3.Connection, account_id: str, name: object) -> dict | None:
    """The credential `name` as stored (ignoring case), or None."""
    if not isinstance(name, str):
        return None
    row = con.execute(
        "SELECT value FROM settings WHERE account_id = ? AND name = ?",
        (account_id, CREDENTIAL_PREFIX + name.casefold()),
    ).fetchone()
    return json.loads(row[0]) if row else None


def _rows(con: sqlite3.Connection, account_id: str, prefix: str) -> list[tuple[str, Any]]:
    """(name, value) of each settings row of this module's whose name begins with `prefix`
    (ending in ":"), by name: one range read the primary key serves."""
    return [
        (name, json.loads(value))
        for name, value in con.execute(
            "SELECT name, value FROM settings WHERE account_id = ? AND name >= ? AND name < ?"
            " ORDER BY name",
            (account_id, prefix, prefix[:-1] + ";"),
        )
    ]


def _shown_in(row: tuple, reach: frozenset[str] | None, reached: set[str] | None) -> SourceInfo:
    """A stored source as a view whose reach is `reach` (the names it reaches: `reached`) is
    shown it: list_sources' rule, reading nothing."""
    if reach is None or reached is None:
        return _info(row)
    names = json.loads(row[2]).get("projects", [])
    return _info(row, reach, [project for project in names if project.casefold() in reached])


def _set_row(con: sqlite3.Connection, account_id: str, name: str, value: Any, now: str) -> None:
    """Write one settings row of this module's (R2), whatever it held."""
    con.execute(
        "INSERT INTO settings (account_id, name, value, updated_at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (account_id, name)"
        " DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (account_id, name, json.dumps(value), now),
    )


def _info(
    row: tuple, reach: frozenset[str] | None = None, projects: list[str] | None = None
) -> SourceInfo:
    """A stored source; `reach` (None: every project) trims its last run's `declined` list to
    the drafts whose project it holds. One with no project recorded is shown to a whole reach
    only: a trace link says what a project was sent. `projects`: the config's projects the viewer
    reaches (None: all of them); the others are left out and `unreached` is set."""
    name, kind, config, cursor, created_at, last_run_at, last_report, last_error = row[:8]
    enabled, enabled_at = row[8:]
    report = json.loads(last_report) if last_report else None
    if report and reach is not None:  # a link, or a held item's title, only where it reaches
        for listed in ("declined", "held_items"):
            if listed in report:
                report[listed] = [d for d in report[listed] if d.get("project") in reach]
    settings = json.loads(config)
    unreached = projects is not None and len(projects) < len(settings.get("projects", []))
    if projects is not None:
        settings["projects"] = projects
    return SourceInfo(
        name=name,
        kind=kind,
        config=settings,
        unreached=unreached,
        cursor=cursor,
        created_at=created_at,
        last_run_at=last_run_at,
        last_report=report,
        last_error=last_error,
        enabled=bool(enabled),
        enabled_at=enabled_at,
    )
