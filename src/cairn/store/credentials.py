"""API keys and web UI sessions."""

import json
import secrets
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timedelta

from cairn.store.base import StoreBase, _digest, _iso, _require_account
from cairn.store.models import KeyInfo, Made, Principal
from cairn.store.reach import (
    TooWide,
    _admitted_reach,
    _reach_detail,
    _read_stored_reach,
    _shown_reach,
    _stored_reach,
)

NOT_A_KEY_MANAGER = "only the workspace's owners and admins make or revoke its keys"
KEY_REVOKED = "this key was revoked: it opens no session"
# Every key made from now on starts with this, so the note guard and anyone's secret scanner
# can recognise one. It holds no ".", so a key is never taken for an OAuth token (grants'
# prefixes hold one). It is the software's under any brand, as the token prefixes are
# (cairn.web.brand). A key made before has no prefix and keeps working: a key is looked up
# by the digest of the whole value, whatever its shape.
KEY_PREFIX = "cairn_"


# A1: what a key made. A row of made_with names a key, an agent's grant, or an approval no
# agent has redeemed yet (MADE_CODE + the code's digest; redeem_code moves it to the grant),
# and the id of the key it was made with. Rows of keys outlive the keys (a revoke walks
# through them); an approval's and a grant's go with them (grants._expire, _end_grant).
MADE_CODE = "code:"


def record_made_with(
    con: sqlite3.Connection, account_id: str, made_id: str, key_id: str | None
) -> None:
    """Record that `made_id` was made with the key `key_id`; nothing when it was not."""
    if key_id is not None:
        con.execute(
            "INSERT INTO made_with (id, account_id, key_id) VALUES (?, ?, ?)",
            (made_id, account_id, key_id),
        )


def _made_ids(con: sqlite3.Connection, account_id: str, roots: list[str]) -> list[str]:
    """Every id made with one of the keys `roots`, or with a key made so, at any depth, in
    the order found; the roots left out."""
    seen, found, frontier = set(roots), [], list(roots)
    while frontier:
        rows = con.execute(
            "SELECT id FROM made_with WHERE account_id = ?"
            " AND key_id IN (SELECT value FROM json_each(?)) ORDER BY id",
            (account_id, json.dumps(frontier)),
        ).fetchall()
        frontier = []
        for (made_id,) in rows:
            if made_id not in seen:
                seen.add(made_id)
                found.append(made_id)
                if made_id.startswith("key_"):
                    frontier.append(made_id)
    return found


class CredentialMethods(StoreBase):
    # API keys
    def create_key(
        self, label: str, projects: Iterable[str] | None = None, access: str = "write"
    ) -> str:
        """A new key of this account that reaches `projects` (None: every project, now and later;
        else ids of this account's projects and sets) to write, or only to read. ValueError: a
        reach that names nothing, an id that is not a project or set of this account, or an
        access other than "write" or "read". A manager's call (StoreBase._manages;
        PermissionError otherwise). A key made by a view acting for a person is theirs: it
        acts for them, and is revoked when they leave the workspace. A key made by a view
        that came with a key (`made_through`, A1: a web session opened with one) records
        that key's id, in the same write; revoke_key(..., with_made=True) takes it."""
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        key_id = "key_" + secrets.token_hex(6)
        now = self._stamp()
        with self._write() as con:
            _require_account(con, self._account_id)
            self._require_manager(con, NOT_A_KEY_MANAGER)
            reach = _stored_reach(con, self._account_id, projects, access, self._reach(con))
            record_made_with(con, self._account_id, key_id, self.made_through)
            con.execute(
                "INSERT INTO api_keys"
                " (id, digest, account_id, label, created_at, projects, access, user_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    key_id,
                    _digest(key),
                    self._account_id,
                    label,
                    now,
                    *reach,
                    self._user_id,
                ),
            )
            detail = {"label": label, **_reach_detail(*reach)}
            self._audit(con, "key.created", detail=detail, at=now)
        return key

    def check_key(self, key: str) -> bool:
        with self._read() as con:
            found = con.execute(
                "SELECT 1 FROM api_keys WHERE digest = ? AND account_id = ?",
                (_digest(key), self._account_id),
            )
            return found.fetchone() is not None

    def revoke_key(self, label: str, *, with_made: bool = False) -> bool:
        """Revoke every key of this account with this label; True when one existed. A
        manager's call (PermissionError otherwise). The browser sessions opened with those
        keys end in the same write, found by the key's id; sessions opened through an
        identity provider stay. Records key.revoked.

        `with_made` (A1): in the same write, also every key made with one of those keys or
        with a key they made, at any depth (made_by: by the ids recorded, through keys
        already revoked), with the sessions opened with each; every agent's grant approved
        with any of them (agent.disconnected each, under this view); and every approval made
        with them that no agent has redeemed yet. Each key's label is recorded as its own
        key.revoked, and, when anything was taken, one key.revoked_with_made names them
        together (detail: label, and the labels of the `keys` and names of the `agents`
        taken, sorted). Keys, grants and approvals that record no key (a person's, the
        command line's, or any from before the recording began) are never taken. Any
        failure writes nothing."""
        with self._write() as con:
            self._require_manager(con, NOT_A_KEY_MANAGER)
            ids = [
                key_id
                for (key_id,) in con.execute(
                    "SELECT id FROM api_keys WHERE account_id = ? AND label = ?",
                    (self._account_id, label),
                )
            ]
            if not ids:
                return False
            made = _made_ids(con, self._account_id, ids) if with_made else []
            keys = con.execute(
                "SELECT id, label FROM api_keys WHERE account_id = ?"
                " AND id IN (SELECT value FROM json_each(?)) ORDER BY seq",
                (self._account_id, json.dumps(made)),
            ).fetchall()
            grants = con.execute(
                "SELECT g.id, c.name FROM oauth_grants AS g"
                " JOIN oauth_clients AS c ON c.id = g.client_id"
                " WHERE g.account_id = ? AND g.id IN (SELECT value FROM json_each(?))"
                " ORDER BY g.seq",
                (self._account_id, json.dumps(made)),
            ).fetchall()
            gone = ids + [key_id for key_id, _ in keys]
            con.execute(
                "DELETE FROM api_keys WHERE account_id = ?"
                " AND id IN (SELECT value FROM json_each(?))",
                (self._account_id, json.dumps(gone)),
            )
            con.execute(
                "DELETE FROM sessions WHERE account_id = ?"
                " AND key_id IN (SELECT value FROM json_each(?))",
                (self._account_id, json.dumps(gone)),
            )
            codes = [i for i in made if i.startswith(MADE_CODE)]
            con.execute(
                "DELETE FROM oauth_codes WHERE account_id = ? AND grant_id IS NULL"
                " AND digest IN (SELECT substr(value, ?) FROM json_each(?))",
                (self._account_id, len(MADE_CODE) + 1, json.dumps(codes)),
            )
            con.execute(
                "DELETE FROM made_with WHERE id IN (SELECT value FROM json_each(?))",
                (json.dumps(codes),),
            )
            self._audit(con, "key.revoked", detail={"label": label})
            labels = sorted({taken for _, taken in keys})
            for taken in labels:
                if taken != label:
                    self._audit(con, "key.revoked", detail={"label": taken})
            for grant_id, _ in grants:
                self._disconnect(con, grant_id, by_agent=False)  # type: ignore[attr-defined]
            if keys or grants:
                detail = {"label": label, "keys": labels, "agents": sorted(n for _, n in grants)}
                self._audit(con, "key.revoked_with_made", detail=detail)
            return True

    def made_by(self, label: str) -> Made:
        """What the keys of this account labelled `label` made (A1): the live keys made with
        one of them, or with a key they made, at any depth, and the live agents whose
        approval was made with any of those, each oldest first (Made). Walked by the ids
        recorded, never by label: a key that took a revoked key's label made nothing of
        what that key made, and a key revoked along the way still leads to what it made.
        `unrecorded_before` is the moment the recording began (the schema step's, or a new
        database's) when one of the keys was made before it. A manager's read
        (PermissionError otherwise, as revoke_key); nothing for a label no key has."""
        with self._read() as con:
            self._require_manager(con, NOT_A_KEY_MANAGER)
            roots = con.execute(
                "SELECT id, created_at FROM api_keys WHERE account_id = ? AND label = ?",
                (self._account_id, label),
            ).fetchall()
            if not roots:
                return Made()
            made = json.dumps(_made_ids(con, self._account_id, [i for i, _ in roots]))
            keys = con.execute(
                "SELECT label FROM api_keys WHERE account_id = ?"
                " AND id IN (SELECT value FROM json_each(?)) ORDER BY seq",
                (self._account_id, made),
            ).fetchall()
            agents = con.execute(
                "SELECT c.name FROM oauth_grants AS g"
                " JOIN oauth_clients AS c ON c.id = g.client_id"
                " WHERE g.account_id = ? AND g.id IN (SELECT value FROM json_each(?))"
                " AND EXISTS (SELECT 1 FROM oauth_tokens"
                "  WHERE grant_id = g.id AND kind = 'refresh' AND expires_at > ?)"
                " ORDER BY g.seq",
                (self._account_id, made, self._stamp()),
            ).fetchall()
            (since,) = con.execute("SELECT at FROM recorded_since").fetchone()
        return Made(
            keys=tuple(k for (k,) in keys),
            agents=tuple(a for (a,) in agents),
            unrecorded_before=since if any(at < since for _, at in roots) else None,
        )

    def list_keys(self) -> list[KeyInfo]:
        """This account's keys, oldest first, each with who made it, the key it was made with
        (A1: its label, or made_with_revoked when that key is gone), the moment recording
        began when it was made before it (unrecorded_before, as made_by's), and its reach as
        far as the view reaches (a project out of it is left out, as if it did not exist)."""
        with self._read() as con:
            (since,) = con.execute("SELECT at FROM recorded_since").fetchone()
            rows = con.execute(
                "SELECT k.label, k.created_at, k.projects, k.access, k.user_id, maker.label,"
                " m.key_id IS NOT NULL AND maker.id IS NULL FROM api_keys AS k"
                " LEFT JOIN made_with AS m ON m.id = k.id"
                " LEFT JOIN api_keys AS maker ON maker.id = m.key_id"
                " WHERE k.account_id = ? ORDER BY k.seq",
                (self._account_id,),
            ).fetchall()
            reach = self._reach(con)
            return [
                KeyInfo(
                    label=label,
                    created_at=created_at,
                    projects=_shown_reach(con, self._account_id, projects, reach),
                    access=access,
                    maker_id=maker_id,
                    made_with=made_with,
                    made_with_revoked=bool(revoked),
                    unrecorded_before=since if created_at < since else None,
                )
                for label, created_at, projects, access, maker_id, made_with, revoked in rows
            ]

    def resolve_key(self, key: str) -> Principal | None:
        """Whose key this is, looking in every account."""
        found = self.identify_key(key)
        return found[0] if found else None

    def identify_key(self, key: str) -> tuple[Principal, str] | None:
        """resolve_key, and the key's id ("key_" + 12 hex chars), which tells apart keys
        that share a label: the actor id of what the key does (acting_as)."""
        with self._read() as con:
            row = con.execute(
                "SELECT account_id, label, rowid, typeof(projects) = 'null',"
                " CASE WHEN typeof(projects) = 'null' THEN access END, id,"
                " CASE WHEN typeof(projects) = 'null' THEN user_id END"
                " FROM api_keys WHERE digest = ?",
                (_digest(key),),
            ).fetchone()
            if row is None:
                return None
            account_id, label, rowid, is_null, access, key_id, maker_id = row
            try:
                projects, length = _read_stored_reach(con, "api_keys", rowid, is_null)
                if not is_null:
                    # SQLite can copy the whole record to read a field after projects.
                    # Fetch those scalars only after the reach is known to fit.
                    row = con.execute(
                        "SELECT access, user_id FROM api_keys WHERE rowid = ?", (rowid,)
                    ).fetchone()
                    access, maker_id = row
                reach = _admitted_reach(projects, length)
            except TooWide:  # a reach no cap allows: the key is refused, as one not known
                return None
        principal = Principal(
            account_id=account_id,
            kind="key",
            label=label,
            user_id=maker_id,  # a key a person made acts for them
            projects=reach,
            access=access,
        )
        return principal, key_id

    # Web UI sessions
    def create_session(self, ttl_seconds: int = 43_200, made_from: str | None = None) -> str:
        """A new session of this account, for the person the view acts for. A view acting as
        a key (`acting_as("key", label, key_id)`) opens it with that key: the session carries
        its label and id, and acts as "session" with them in the audit log. PermissionError
        (KEY_REVOKED) when that key was revoked since the view was made, decided inside the
        write, so a key revoked between admission and this call opens no session.
        `made_from` (B1): the token of the session the person came with (switching workspace,
        taking an invitation); the new one carries the address that session's sign-in was
        vouched with, read inside the write and only from a session of the same person. A
        session opened with a key records session.opened under the key (A1; detail: `how`,
        "key")."""
        token = secrets.token_urlsafe(32)
        kind, name = self._actor
        label, key_id = (name, self._actor_id) if kind == "key" else (None, None)
        with self._write() as con:
            now = self._instant()  # once the write lock is held: the life starts at the write
            expires_at = _iso(now + timedelta(seconds=ttl_seconds))
            _require_account(con, self._account_id)
            vouched = None
            if made_from is not None and self._user_id is not None:
                found = con.execute(
                    "SELECT vouched_email FROM sessions WHERE digest = ? AND user_id = ?",
                    (_digest(made_from), self._user_id),
                ).fetchone()
                vouched = found[0] if found else None
            if (
                key_id is not None
                and not con.execute(
                    "SELECT 1 FROM api_keys WHERE id = ? AND account_id = ?",
                    (key_id, self._account_id),
                ).fetchone()
            ):
                raise PermissionError(KEY_REVOKED)
            con.execute(
                "INSERT INTO sessions (digest, account_id, expires_at, created_at, user_id,"
                " label, key_id, vouched_email) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _digest(token),
                    self._account_id,
                    expires_at,
                    _iso(now),
                    self._user_id,
                    label,
                    key_id,
                    vouched,
                ),
            )
            if key_id is not None:  # A1: the key opened a session (sign_in records a person's)
                self._audit(con, "session.opened", detail={"how": "key"}, at=_iso(now))
        return token

    def check_session(self, token: str) -> bool:
        with self._read() as con:
            found = con.execute(
                "SELECT 1 FROM sessions WHERE digest = ? AND account_id = ? AND expires_at > ?",
                (_digest(token), self._account_id, self._stamp()),
            )
            return found.fetchone() is not None

    def end_session(self, token: str) -> None:
        """Ends the session; also for a person removed meanwhile (it only deletes)."""
        with self._write(member=False) as con:
            con.execute(
                "DELETE FROM sessions WHERE digest = ? AND account_id = ?",
                (_digest(token), self._account_id),
            )

    def renew_session(self, token: str, ttl_seconds: int, after_seconds: int) -> bool:
        """B1 (`serve --session-days`): the live session `token` of a person, made by signing
        in, lasts `ttl_seconds` from now when its last renewal was more than `after_seconds`
        ago; True when this call renewed it. The last renewal is not stored: a session is made
        and renewed to last exactly `ttl_seconds`, so it is its expiry less `ttl_seconds`, and
        the session is due when it expires sooner than `ttl_seconds - after_seconds` from now.
        A read first, so a use that is not due takes no write lock; then one guarded UPDATE,
        whose WHERE holds the whole rule again inside the write, so of two requests that both
        read it due, one renews and the other changes nothing. A session that ended (deleted),
        ran out, has no person or was opened with a key is never touched. Only the store that
        opened the database renews (PermissionError on any view): Accounts holds it."""
        if not self._owner:
            raise PermissionError("only the store that opened the database renews a session")
        digest = _digest(token)
        rule = (
            "digest = ? AND user_id IS NOT NULL AND key_id IS NULL AND expires_at > ?"
            " AND expires_at < ?"
        )

        def due(now: datetime) -> tuple[str, str, str]:
            return digest, _iso(now), _iso(now + timedelta(seconds=ttl_seconds - after_seconds))

        with self._read() as con:
            found = con.execute(f"SELECT 1 FROM sessions WHERE {rule}", due(self._instant()))
            if found.fetchone() is None:
                return False
        with self._write(member=False) as con:
            now = self._instant()
            renewed = con.execute(
                f"UPDATE sessions SET expires_at = ? WHERE {rule}",
                (_iso(now + timedelta(seconds=ttl_seconds)), *due(now)),
            )
            return renewed.rowcount == 1

    def cap_web_sessions(self, ttl_seconds: int) -> int:
        """B1 (`serve`, when it starts): every person's web session that was not opened with
        a key ends at most `ttl_seconds` from now, its expiry the earlier of the two; how many
        were shortened. Renewal only ever extends, so without this a session made under a
        longer `--session-days` would keep its length after the option was lowered or
        removed. It never lengthens a session, and never touches one opened with a key, a key
        or a grant. A read first, so a start with nothing to cap takes no write lock; then one
        UPDATE whose WHERE holds the rule again. The operator's call, like sweep_expired: only
        the store that opened the database (PermissionError on any view)."""
        if not self._owner:
            raise PermissionError("only the store that opened the database caps sessions")
        rule = "user_id IS NOT NULL AND key_id IS NULL AND expires_at > ?"
        with self._read() as con:
            cap = _iso(self._instant() + timedelta(seconds=ttl_seconds))
            if con.execute(f"SELECT 1 FROM sessions WHERE {rule}", (cap,)).fetchone() is None:
                return 0
        with self._write(member=False) as con:
            cap = _iso(self._instant() + timedelta(seconds=ttl_seconds))
            return con.execute(
                f"UPDATE sessions SET expires_at = ? WHERE {rule}", (cap, cap)
            ).rowcount

    def end_person_sessions(self, email: str) -> dict[str, int]:
        """B1 (`session-end`): end every web session of the person whose address is `email`,
        matched as the allowlist matches one (the whole address, stripped, in any case), in
        every workspace, in one write. A session is theirs when its person's stored address
        is that one (the address they first signed in with), or when the sign-in it came from
        was vouched with it (`vouched_email`: the provider's address may have changed since,
        and the allowlist checks the one it vouches for now). A session made before schema 22
        records no vouched address and is found by its person's stored address only. How
        many ended in each workspace, each session counted once; {} for nobody. Each
        workspace where one ended gets a "sessions.ended" event per person, as ("system",
        None), with the member's id and the count, never the address. No stored address is
        changed. API keys and agents' grants are not sessions and are not touched. The
        operator's call, like sweep_expired: only the store that opened the database may run
        it (PermissionError on any view)."""
        if not self._owner:
            raise PermissionError("only the store that opened the database ends sessions")
        wanted = email.strip().lower()

        def matches(address: str | None) -> bool:
            return address is not None and address.strip().lower() == wanted

        with self._write(member=False) as con:
            people = {
                user_id
                for user_id, address in con.execute("SELECT id, email FROM users")
                if matches(address)
            }
            found = [
                (digest, account_id, user_id)
                for digest, account_id, user_id, vouched in con.execute(
                    "SELECT digest, account_id, user_id, vouched_email FROM sessions"
                    " WHERE user_id IS NOT NULL ORDER BY account_id, user_id"
                ).fetchall()
                if user_id in people or matches(vouched)
            ]
            counts: dict[tuple[str, str], int] = {}
            for digest, account_id, user_id in found:
                con.execute("DELETE FROM sessions WHERE digest = ?", (digest,))
                counts[account_id, user_id] = counts.get((account_id, user_id), 0) + 1
            ended: dict[str, int] = {}
            for (account_id, user_id), count in counts.items():
                ended[account_id] = ended.get(account_id, 0) + count
                self._audit(
                    con,
                    "sessions.ended",
                    detail={"member": user_id, "sessions": count},
                    account_id=account_id,
                    actor=("system", None),
                )
            return ended

    def resolve_session(self, token: str) -> Principal | None:
        """Whose live session this is, looking in every account: the account, the person
        when one signed in, and the label of the key it was opened with, when it was."""
        found = self.identify_session(token)
        return found[0] if found else None

    def session_key(self, token: str) -> str | None:
        """The id of the key the live session `token` was opened with; None for a session
        opened any other way, or no live session (A1: what a session opened with a key a
        person made writes and makes is that key's, though it acts for the person). Looked up
        in every account, by the secret, as identify_session."""
        with self._read() as con:
            row = con.execute(
                "SELECT key_id FROM sessions WHERE digest = ? AND expires_at > ?",
                (_digest(token), self._stamp()),
            ).fetchone()
        return row[0] if row else None

    def identify_session(self, token: str) -> tuple[Principal, str | None] | None:
        """resolve_session, and its actor id: the person's id, else the id of the key it was
        opened with, else None (a session made by a bare view)."""
        with self._read() as con:
            row = con.execute(
                "SELECT account_id, user_id, label, key_id FROM sessions"
                " WHERE digest = ? AND expires_at > ?",
                (_digest(token), self._stamp()),
            ).fetchone()
        if row is None:
            return None
        account_id, user_id, label, key_id = row
        principal = Principal(account_id=account_id, kind="session", user_id=user_id, label=label)
        return principal, user_id or key_id
