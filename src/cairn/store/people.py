"""People: users, the identities they sign in with, and their memberships of workspaces.

Users are not scoped: a person can belong to several workspaces, so `get_user` and
`sign_in` look across accounts, like `resolve_session`, for the store and a view acting for
no person; a view acting for a person, or narrowed, reads only its own person and its
workspace's members (cairn.store.reach.OWN_WORKSPACE_ONLY). What a workspace shows of its
people (`list_members`) is the view's own account's."""

import json
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Literal

from cairn.store.base import (
    DEFAULT_ACCOUNT_ID,
    StoreBase,
    _digest,
    _insert_account,
    _iso,
    _require_account,
)
from cairn.store.models import (
    Account,
    Invite,
    InviteInfo,
    Member,
    OpenInvite,
    SignedIn,
    User,
    Workspace,
)

USER_NAME_MAX = 80  # the same as an account's: a personal workspace is named after its owner
JOINS = ("personal", "default")
ROLES = ("owner", "admin", "member")
INVITE_TTL = 7 * 86_400  # seconds an invitation stays open
NOT_AN_OWNER = "only the workspace's owners make, unmake or remove owners"

# A member who is leaving this account and belongs to no other: delete_account removes them.
_LEAVING = (
    "(SELECT user_id FROM memberships WHERE account_id = ?"
    " AND user_id NOT IN (SELECT user_id FROM memberships WHERE account_id != ?))"
)
# What delete_account runs, in this order, each statement with the account id for every "?".
_FORGET_MEMBERS = (
    f"DELETE FROM identities WHERE user_id IN {_LEAVING}",
    f"DELETE FROM users WHERE id IN {_LEAVING}",
    "DELETE FROM memberships WHERE account_id = ?",
)
_USER_COLUMNS = "id, email, name, created_at"


class ProviderChanged(Exception):
    """A sign-in whose (provider, subject) is known under another issuer: the provider was
    replaced under the same name, and the subject may be another person's there. An operator
    must decide what the old identities mean; nothing was written."""


REMOVED = (
    "You were removed from this workspace, so signing in does not put you back. Ask one of its"
    " owners or admins for an invitation, then sign in again."
)


class Removed(PermissionError):
    """A sign-in that would put a person back in a workspace they were removed from (join
    "default"), without an open invitation for their address; nothing was written."""

    def __init__(self) -> None:
        super().__init__(REMOVED)


def _removal(account_id: str, email: str) -> str:
    """A removal marker: a digest of the workspace id and the lower-cased address."""
    return _digest(f"{account_id}\n{email.lower()}")


def _identity_removal(account_id: str, provider: str, issuer: str, subject: str) -> str:
    """The other removal marker: a digest of the workspace id and the stable identity (the
    provider, its issuer and the subject), which outlives a changed address."""
    return _digest(f"{account_id}\nidentity\n{provider}\n{issuer}\n{subject}")


def _markers(con: sqlite3.Connection, account_id: str, user: "User") -> list[str]:
    """Every removal marker that names this user in that workspace: their address's, and
    each of their identities'."""
    rows = con.execute(
        "SELECT provider, issuer, subject FROM identities WHERE user_id = ?", (user.id,)
    ).fetchall()
    return [_removal(account_id, user.email)] + [
        _identity_removal(account_id, *row) for row in rows
    ]


class PeopleMethods(StoreBase):
    def sign_in(
        self,
        provider: str,
        subject: str,
        email: str,
        name: str,
        *,
        issuer: str,
        join: Literal["personal", "default"],
        ttl_seconds: int = 43_200,
    ) -> SignedIn:
        """A person signed in through `provider` as `subject`, with an email the provider has
        verified (the caller's to check), vouched for by `issuer`, the provider's issuer URL.
        In one transaction: the identity names the user, unless it is known under another
        issuer (ProviderChanged, and nothing is written);
        a new identity whose email is a user's (ignoring case) is that user; otherwise a new
        user is made, named `name` (whitespace collapsed, cut to 80 characters; the email
        when blank). A user with no workspace joins one as `join` says: "personal", a new
        workspace named after them, which they own; "default", the default workspace, as
        its owner when it has no member yet and as a member otherwise. Returns a new session
        in the user's first workspace (in joining order), which records `email` as the
        address vouched for at this sign-in (B1: end_person_sessions finds it by that too).
        Nothing about an existing user changes: the stored address stays the first one.
        Records session.opened in that workspace, under the person (A1; detail: `how`, the
        provider, and `user`, their id).
        ValueError: a blank provider, subject or issuer, an email without a local part and a
        domain, or another `join`; then nothing is written."""
        provider, subject, email = provider.strip(), subject.strip(), email.strip()
        issuer = issuer.strip()
        local, _, domain = email.rpartition("@")
        if not provider or not subject or not issuer:
            raise ValueError("a sign-in names its provider, its issuer and the person's subject")
        if not local or not domain:
            raise ValueError("a sign-in carries an email address")
        if join not in JOINS:
            raise ValueError(f"join is one of {', '.join(JOINS)}: {join!r}")
        name = " ".join(name.split())[:USER_NAME_MAX].rstrip() or email[:USER_NAME_MAX]
        token = secrets.token_urlsafe(32)
        with self._write() as con:
            now = self._instant()  # once the write lock is held: an invitation, the session
            created = False
            found = con.execute(
                "SELECT user_id, issuer FROM identities WHERE provider = ? AND subject = ?",
                (provider, subject),
            ).fetchone()
            if found is not None and found[1] != issuer:
                raise ProviderChanged(provider)
            if found is None:
                found = con.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
                if found is None:
                    found, created = ("usr_" + secrets.token_hex(6),), True
                    con.execute(
                        f"INSERT INTO users ({_USER_COLUMNS}) VALUES (?, ?, ?, ?)",
                        (found[0], email, name, _iso(now)),
                    )
                con.execute(
                    "INSERT INTO identities (provider, subject, user_id, created_at, issuer)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (provider, subject, found[0], _iso(now), issuer),
                )
            user = _find_user(con, found[0])
            assert user is not None
            workspace = con.execute(
                "SELECT account_id FROM memberships WHERE user_id = ? ORDER BY seq LIMIT 1",
                (user.id,),
            ).fetchone()
            account_id, invited = (
                (workspace[0], False)
                if workspace
                else self._join_unless_removed(con, user, join, now)
            )
            con.execute(
                "INSERT INTO sessions (digest, account_id, expires_at, created_at, user_id,"
                " vouched_email) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    _digest(token),
                    account_id,
                    _iso(now + timedelta(seconds=ttl_seconds)),
                    _iso(now),
                    user.id,
                    email,  # what the provider vouches for now (B1: session-end finds it)
                ),
            )
            # A1: the person opened a session, through this provider. Never a credential.
            self._audit(
                con,
                "session.opened",
                detail={"how": provider, "user": user.id},
                account_id=account_id,
                actor=("session", user.name),
                actor_id=user.id,
                at=_iso(now),
            )
        return SignedIn(
            token=token, user=user, account_id=account_id, created=created, invited=invited
        )

    def get_user(self, user_id: str) -> User | None:
        with self._read() as con:
            return _find_user(con, user_id)

    def role(self) -> str | None:
        """The role in this account of the person the view acts for: "owner", "admin" or
        "member"; None for a view that acts for no person (a key, or a session opened with
        one)."""
        if self._user_id is None:
            return None
        with self._read() as con:
            row = con.execute(
                "SELECT role FROM memberships WHERE account_id = ? AND user_id = ?",
                (self._account_id, self._user_id),
            ).fetchone()
        return row[0] if row else None

    def list_members(self) -> list[Member]:
        """This account's people, in joining order."""
        with self._read() as con:
            rows = con.execute(
                "SELECT u.id, u.email, u.name, m.role FROM memberships AS m"
                " JOIN users AS u ON u.id = m.user_id WHERE m.account_id = ? ORDER BY m.seq",
                (self._account_id,),
            ).fetchall()
        return [
            Member(user_id=user_id, email=email, name=name, role=role)
            for user_id, email, name, role in rows
        ]

    def list_workspaces(self, user_id: str) -> list[Workspace]:
        """A person's workspaces, in joining order, each with their role there. Not scoped,
        like get_user: what a person belongs to is theirs, wherever the view is."""
        with self._read() as con:
            rows = con.execute(
                "SELECT a.id, a.name, m.role FROM memberships AS m"
                " JOIN accounts AS a ON a.id = m.account_id WHERE m.user_id = ? ORDER BY m.seq",
                (user_id,),
            ).fetchall()
        return [Workspace(account_id=a, name=name, role=role) for a, name, role in rows]

    def manages(self) -> bool:
        """Whether this view manages the workspace: invites and removes people, changes roles,
        makes and revokes keys, changes sets, settings and sources. A view acting for no
        person does; a person when they own or administer the workspace now. The one rule
        (StoreBase._manages) that every one of those calls enforces; pages ask it to decide
        what to show."""
        with self._read() as con:
            return self._manages(con)

    # Invitations

    def invite(self, email: str, role: str = "member", ttl_seconds: int = INVITE_TTL) -> Invite:
        """An invitation to this workspace for `email`, giving `role` to whoever signs in with
        that address (ignoring case) and uses its token once before it expires. The token is in
        the answer only: the database keeps its digest. Inviting an address again replaces its
        open invitation. A manager's call (PermissionError otherwise); only an owner, or a
        view acting for no person, invites an owner. ValueError: no email address, another
        role or a negative lifetime; then nothing is written."""
        email = email.strip()
        local, _, domain = email.rpartition("@")
        if not local or not domain:
            raise ValueError("an invitation is for an email address")
        if role not in ROLES:
            raise ValueError(f"a role is one of {', '.join(ROLES)}, not {role!r}")
        if ttl_seconds < 0:
            raise ValueError("an invitation lasts zero seconds or more")
        token = secrets.token_urlsafe(32)
        with self._write() as con:
            now = self._instant()  # once the write lock is held: the life and the cleanup
            made = Invite(
                id="inv_" + secrets.token_hex(6),
                email=email,
                role=role,  # type: ignore[arg-type]  # checked above
                created_at=_iso(now),
                expires_at=_iso(now + timedelta(seconds=ttl_seconds)),
                token=token,
            )
            _require_account(con, self._account_id)
            self._require_manager(con)
            if role == "owner":
                self._require_owner(con)
            con.execute("DELETE FROM invites WHERE expires_at <= ?", (made.created_at,))
            con.execute(
                "DELETE FROM invites WHERE account_id = ? AND email = ?",
                (self._account_id, email),
            )
            con.execute(
                "INSERT INTO invites"
                " (id, digest, account_id, email, role, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    made.id,
                    _digest(token),
                    self._account_id,
                    email,
                    role,
                    made.created_at,
                    made.expires_at,
                ),
            )
            detail = {"invite": made.id, "email": email, "role": role}
            self._audit(con, "member.invited", detail=detail, at=made.created_at)
        return made

    def list_invites(self) -> list[OpenInvite]:
        """This workspace's open invitations, oldest first, never a token. A manager's call
        (PermissionError otherwise)."""
        with self._read() as con:
            self._require_manager(con)
            rows = con.execute(
                "SELECT id, email, role, created_at, expires_at FROM invites"
                " WHERE account_id = ? AND expires_at > ? ORDER BY seq",
                (self._account_id, self._stamp()),
            ).fetchall()
        return [
            OpenInvite(id=i, email=email, role=role, created_at=created, expires_at=expires)
            for i, email, role, created, expires in rows
        ]

    def revoke_invite(self, invite_id: str) -> bool:
        """End an open invitation of this workspace; True when there was one. A manager's
        call (PermissionError otherwise)."""
        with self._write() as con:
            self._require_manager(con)
            row = con.execute(
                "SELECT email FROM invites WHERE id = ? AND account_id = ? AND expires_at > ?",
                (invite_id, self._account_id, self._stamp()),
            ).fetchone()
            con.execute(
                "DELETE FROM invites WHERE id = ? AND account_id = ?",
                (invite_id, self._account_id),
            )
            if row is None:
                return False
            self._audit(con, "invite.revoked", detail={"invite": invite_id, "email": row[0]})
            return True

    def find_invite(self, token: str) -> InviteInfo | None:
        """The open invitation this token belongs to, in any workspace: where it leads and
        who it is for. None when it is unknown, used, revoked or expired. A read."""
        with self._read() as con:
            row = con.execute(
                "SELECT i.account_id, a.name, i.email, i.role FROM invites AS i"
                " JOIN accounts AS a ON a.id = i.account_id"
                " WHERE i.digest = ? AND i.expires_at > ?",
                (_digest(token), self._stamp()),
            ).fetchone()
        if row is None:
            return None
        account_id, workspace, email, role = row
        return InviteInfo(account_id=account_id, workspace=workspace, email=email, role=role)

    def accept_invite(self, token: str, user_id: str) -> Member:
        """Use an open invitation, once: the user becomes a member of its workspace with its
        role, recorded there as member.joined under this view's actor. Someone who is already
        a member keeps their role (the invitation is used all the same). KeyError: a token
        that is unknown, used, revoked or expired, or an unknown user; PermissionError: the
        user's email is not the invitation's (ignoring case, and nothing else). Nothing
        changes on either error. Expiry is judged when the write happens: an invitation
        that expires while the call waits for the write lock is expired."""
        with self._write() as con:
            now = self._stamp()
            found = con.execute(
                "SELECT id, account_id, email, role FROM invites"
                " WHERE digest = ? AND expires_at > ?",
                (_digest(token), now),
            ).fetchone()
            if found is None:
                raise KeyError("no open invitation with that token")
            invite_id, account_id, email, role = found
            user = _find_user(con, user_id)
            if user is None:
                raise KeyError(user_id)
            matches = con.execute("SELECT ? = ? COLLATE NOCASE", (user.email, email)).fetchone()[0]
            if not matches:
                raise PermissionError("this invitation is for another email address")
            con.execute("DELETE FROM invites WHERE id = ?", (invite_id,))
            con.executemany(  # a fresh invitation, taken, ends a removal: both markers
                "DELETE FROM removals WHERE digest = ?",
                [(marker,) for marker in _markers(con, account_id, user)],
            )
            held = con.execute(
                "SELECT role FROM memberships WHERE account_id = ? AND user_id = ?",
                (account_id, user.id),
            ).fetchone()
            if held is None:
                con.execute(
                    "INSERT INTO memberships (account_id, user_id, role, created_at)"
                    " VALUES (?, ?, ?, ?)",
                    (account_id, user.id, role, now),
                )
                self._audit(
                    con,
                    "member.joined",
                    detail={"member": user.id, "role": role, "invite": invite_id},
                    account_id=account_id,
                    actor=self._actor,
                    actor_id=self._actor_id,
                    at=now,
                )
            kept = held[0] if held else role
            return Member(user_id=user.id, email=user.email, name=user.name, role=kept)

    # Roles and removal

    def set_role(self, user_id: str, role: str) -> None:
        """Give a member of this workspace another role, recorded as role.changed; giving
        the role they have writes nothing. A manager's call; only an owner (or a view acting
        for no person) makes or unmakes an owner (PermissionError otherwise). ValueError:
        another role, or the last owner made something else. KeyError: not a member."""
        if role not in ROLES:
            raise ValueError(f"a role is one of {', '.join(ROLES)}, not {role!r}")
        with self._write() as con:
            self._require_manager(con)
            held = self._member_role(con, user_id)
            if held is None:
                raise KeyError(user_id)
            if "owner" in (held, role):
                self._require_owner(con)
            if held == role:
                return
            if held == "owner":
                self._keep_an_owner(con)
            con.execute(
                "UPDATE memberships SET role = ? WHERE account_id = ? AND user_id = ?",
                (role, self._account_id, user_id),
            )
            detail = {"member": user_id, "from": held, "to": role}
            self._audit(con, "role.changed", detail=detail)

    def remove_member(self, user_id: str) -> bool:
        """Remove a member from this workspace, recorded as member.removed; True when they
        were one. In the same transaction their sessions here end, the agents they approved
        here are disconnected (codes waiting included) and the keys they made here are
        revoked; they leave every project's rule, and the projects they made have no maker any
        more. What they wrote stays. A person left with no workspace is removed, with their
        identities (as delete_account does). Their connected Google accounts here are deleted
        in the same write, their sources switched off, and the tokens handed to the server to
        end Google's permission by the rule (cairn.store.hosted). A manager's call; only an
        owner (or a view acting for no person) removes an owner (PermissionError otherwise).
        ValueError: the last owner."""
        with self._write() as con:
            self._require_manager(con)
            held = self._member_role(con, user_id)
            if held is None:
                return False
            if held == "owner":
                self._require_owner(con)
                self._keep_an_owner(con)
            account = (self._account_id, user_id)
            # their connected Google accounts here go with them, their sources switched off
            # (cairn.store.hosted); Google's permission is ended after this write, by the rule
            dropped = self._drop_hosted_of(con, user_id)  # type: ignore[attr-defined]
            grants = "(SELECT id FROM oauth_grants WHERE account_id = ? AND user_id = ?)"
            keys = con.execute(
                "DELETE FROM api_keys WHERE account_id = ? AND user_id = ?", account
            ).rowcount
            agents = con.execute(
                f"SELECT count(*) FROM oauth_grants WHERE id IN {grants}", account
            ).fetchone()[0]
            for statement in (
                f"DELETE FROM oauth_tokens WHERE grant_id IN {grants}",
                "DELETE FROM oauth_grants WHERE account_id = ? AND user_id = ?",
                "DELETE FROM oauth_codes WHERE account_id = ? AND user_id = ?",
                "DELETE FROM sessions WHERE account_id = ? AND user_id = ?",
                "DELETE FROM project_people WHERE user_id = ?2 AND project_id IN"
                " (SELECT id FROM projects WHERE account_id = ?1)",
                "UPDATE projects SET maker_id = NULL WHERE account_id = ? AND maker_id = ?",
                "DELETE FROM memberships WHERE account_id = ? AND user_id = ?",
            ):
                con.execute(statement, account)
            leaving = _find_user(con, user_id)
            assert leaving is not None
            con.executemany(  # signing in again, by address or identity, does not put them back
                "INSERT OR IGNORE INTO removals (digest, account_id) VALUES (?, ?)",
                [(marker, self._account_id) for marker in _markers(con, self._account_id, leaving)],
            )
            if not con.execute(
                "SELECT 1 FROM memberships WHERE user_id = ?", (user_id,)
            ).fetchone():
                con.execute("DELETE FROM identities WHERE user_id = ?", (user_id,))
                con.execute("DELETE FROM users WHERE id = ?", (user_id,))
            detail = {"member": user_id, "role": held, "keys": keys, "agents": agents}
            self._audit(con, "member.removed", detail=detail)
        self._queue_revocations(dropped)  # type: ignore[attr-defined]
        return True

    def _join_unless_removed(
        self, con: sqlite3.Connection, user: User, join: str, now: datetime
    ) -> tuple[str, bool]:
        """_join, unless the person was removed from the workspace "default" names (a removal
        marker for it and their address, or for it and any identity of theirs, the one
        signing in included): then only an open invitation for their address lets them in,
        with its role; it is used, those markers are deleted and member.joined is recorded
        under the person. Removed otherwise, and the sign-in writes nothing."""
        markers = _markers(con, DEFAULT_ACCOUNT_ID, user)
        removed = con.execute(
            "SELECT 1 FROM removals WHERE digest IN (SELECT value FROM json_each(?))",
            (json.dumps(markers),),
        ).fetchone()
        if join != "default" or not removed:
            return _join(con, user, join, now), False
        invite = con.execute(
            "SELECT id, role FROM invites WHERE account_id = ? AND email = ? AND expires_at > ?",
            (DEFAULT_ACCOUNT_ID, user.email, _iso(now)),
        ).fetchone()
        if invite is None:
            raise Removed()
        invite_id, role = invite
        con.execute("DELETE FROM invites WHERE id = ?", (invite_id,))
        con.executemany("DELETE FROM removals WHERE digest = ?", [(m,) for m in markers])
        con.execute(
            "INSERT INTO memberships (account_id, user_id, role, created_at) VALUES (?, ?, ?, ?)",
            (DEFAULT_ACCOUNT_ID, user.id, role, _iso(now)),
        )
        self._audit(
            con,
            "member.joined",
            detail={"member": user.id, "role": role, "invite": invite_id},
            account_id=DEFAULT_ACCOUNT_ID,
            actor=("session", user.name),
            actor_id=user.id,
            at=_iso(now),
        )
        return DEFAULT_ACCOUNT_ID, True  # the sign-in took the invitation

    def _member_role(self, con: sqlite3.Connection, user_id: str) -> str | None:
        row = con.execute(
            "SELECT role FROM memberships WHERE account_id = ? AND user_id = ?",
            (self._account_id, user_id),
        ).fetchone()
        return row[0] if row else None

    def _require_owner(self, con: sqlite3.Connection) -> None:
        """PermissionError unless this view acts for no person or for an owner: only they
        make, unmake or remove owners."""
        if self._user_id is not None and self._role_in(con) != "owner":
            raise PermissionError(NOT_AN_OWNER)

    def _keep_an_owner(self, con: sqlite3.Connection) -> None:
        """ValueError when the workspace has one owner, who is about to stop being one:
        decided inside the write transaction, so two demotions at once never both pass."""
        (owners,) = con.execute(
            "SELECT count(*) FROM memberships WHERE account_id = ? AND role = 'owner'",
            (self._account_id,),
        ).fetchone()
        if owners <= 1:
            raise ValueError("a workspace always has an owner: make another owner first")


def _find_user(con: sqlite3.Connection, user_id: str) -> User | None:
    row = con.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE id = ?", (user_id,)).fetchone()
    return User(id=row[0], email=row[1], name=row[2], created_at=row[3]) if row else None


def _join(con: sqlite3.Connection, user: User, join: str, now: datetime) -> str:
    """Make `user` a member of the workspace `join` names; returns its id."""
    if join == "personal":
        account = Account(id="acct_" + secrets.token_hex(6), name=user.name, created_at=_iso(now))
        _insert_account(con, account)
        account_id, role = account.id, "owner"
    else:
        account_id = DEFAULT_ACCOUNT_ID
        taken = con.execute(
            "SELECT 1 FROM memberships WHERE account_id = ? LIMIT 1", (account_id,)
        ).fetchone()
        role = "member" if taken else "owner"
    con.execute(
        "INSERT INTO memberships (account_id, user_id, role, created_at) VALUES (?, ?, ?, ?)",
        (account_id, user.id, role, _iso(now)),
    )
    return account_id
