"""OAuth clients, authorization codes, grants and tokens."""

import json
import secrets
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta

from cairn.store.base import StoreBase, _digest, _iso, _require_account
from cairn.store.credentials import MADE_CODE, record_made_with
from cairn.store.models import CodeInfo, GrantInfo, OAuthClient, Principal, TokenPair
from cairn.store.reach import (
    TooWide,
    _admitted_reach,
    _reach_detail,
    _read_stored_reach,
    _shown_reach,
    _stored_reach,
)

# OAuth lifetimes, in seconds. A grant lives as long as its refresh token, which is replaced,
# with a new 30 days, every time it is used. A replaced (rotated) refresh token's digest is
# remembered for REFRESH_TOKEN_TTL after its rotation, the lifetime it would have had:
# presenting it in that time ends the grant (rotate_refresh_token says why).
CODE_TTL = 120
ACCESS_TOKEN_TTL = 3_600
REFRESH_TOKEN_TTL = 30 * 86_400
# A grant rotates at most ROTATIONS times in any ROTATION_WINDOW seconds, counted from its
# remembered rotated tokens inside the rotating write. An access token lasts an hour, so a
# client rotates about once an hour. So one grant remembers at most
# ROTATIONS x (REFRESH_TOKEN_TTL / ROTATION_WINDOW) = 6 x 24 x 30 = 4,320 rotated tokens.
ROTATIONS = 6
ROTATION_WINDOW = 3_600
UNUSED_CLIENT_TTL = 7 * 86_400  # a client that never got a grant is removed after this
CLIENT_NAME_MAX = 80
REDIRECT_URIS_MAX = 10  # per client
REDIRECT_URI_MAX = 255  # characters
# Both prefixes hold a ".", which no API key or session token can (token_urlsafe's alphabet
# is A-Z a-z 0-9 - _), so a bearer value names its own lookup and no key is ever misread.
ACCESS_TOKEN_PREFIX = "cairn.at."
REFRESH_TOKEN_PREFIX = "cairn.rt."
# A code and the grant it is redeemed for are bound to an audience: the MCP address (the
# OAuth resource) the person approved the agent at. A call that names an audience finds only
# a code or a grant bound to it, or to none: one made through the store without an audience
# (the server always names one, and the schema step that added the column ended every grant
# that had none). A mismatch is an unknown code or token, and nothing is written.
_AUDIENCE = "(? IS NULL OR {row}.audience IS NULL OR {row}.audience = ?)"


class TooManyRotations(Exception):
    """A grant asked for a rotation past ROTATIONS in ROTATION_WINDOW seconds; nothing was
    written."""


class GrantMethods(StoreBase):
    # OAuth clients: not scoped, a client belongs to no account
    def register_client(self, name: str, redirect_uris: Sequence[str]) -> OAuthClient:
        """Store a client. Whether a redirect URI is acceptable is the caller's policy; the
        store bounds what it keeps. ValueError: a blank name or one over 80 characters (after
        stripping), no redirect URI, more than 10, or one that is empty or over 255
        characters. Every registration also removes the clients that were registered 7 days
        ago or earlier and never got a grant (one with a code still waiting is left alone)."""
        name, uris = name.strip(), tuple(redirect_uris)
        if not name or len(name) > CLIENT_NAME_MAX:
            raise ValueError(f"a client name has 1 to {CLIENT_NAME_MAX} characters")
        if not 1 <= len(uris) <= REDIRECT_URIS_MAX:
            raise ValueError(f"a client has 1 to {REDIRECT_URIS_MAX} redirect URIs")
        if not all(isinstance(uri, str) and 1 <= len(uri) <= REDIRECT_URI_MAX for uri in uris):
            raise ValueError(f"a redirect URI has 1 to {REDIRECT_URI_MAX} characters")
        with self._write() as con:
            now = self._instant()  # once the write lock is held: see _expire
            client = OAuthClient(
                id="client_" + secrets.token_hex(16),
                name=name,
                redirect_uris=uris,
                created_at=_iso(now),
            )
            _expire(con, now)
            con.execute(
                "DELETE FROM oauth_clients WHERE granted_at IS NULL AND created_at <= ?"
                " AND id NOT IN (SELECT client_id FROM oauth_codes)",
                (_iso(now - timedelta(seconds=UNUSED_CLIENT_TTL)),),
            )
            con.execute(
                "INSERT INTO oauth_clients (id, name, redirect_uris, created_at)"
                " VALUES (?, ?, ?, ?)",
                (client.id, client.name, json.dumps(uris), client.created_at),
            )
        return client

    def get_client(self, client_id: str) -> OAuthClient | None:
        with self._read() as con:
            row = con.execute(
                "SELECT id, name, redirect_uris, created_at FROM oauth_clients WHERE id = ?",
                (client_id,),
            ).fetchone()
        if row is None:
            return None
        return OAuthClient(
            id=row[0], name=row[1], redirect_uris=tuple(json.loads(row[2])), created_at=row[3]
        )

    # OAuth codes, grants and tokens. Like keys: what an account approves belongs to the
    # view's account, and whoever presents a code or a token is looked up in every account.
    def create_code(
        self,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        *,
        redirect_uri_explicit: bool = True,
        projects: Iterable[str] | None = None,
        access: str = "write",
        audience: str | None = None,
    ) -> str:
        """This account approves the client, for the person the view acts for and with the
        reach they chose (as `create_key` takes one), which the grant keeps for its life, at
        `audience` (the MCP address; see _AUDIENCE): a new authorization code, valid for 2
        minutes. KeyError: an unknown client, or a view whose account is gone. ValueError: a
        reach `create_key` would refuse; then nothing is written. An approval made by a view
        that came with a key (`made_through`, A1) records that key's id; the grant the code
        is redeemed for keeps it. Records agent.approved under this view's actor (A1;
        detail: the agent's name, the client's id, the redirect URI and the reach when not
        every project to write), whether or not the code is ever redeemed."""
        code = secrets.token_urlsafe(32)
        with self._write() as con:
            now = self._instant()
            _require_account(con, self._account_id)
            if not con.execute("SELECT 1 FROM oauth_clients WHERE id = ?", (client_id,)).fetchone():
                raise KeyError(client_id)
            reach = _stored_reach(
                con, self._account_id, projects, access, self._reach(con), "an agent"
            )
            _expire(con, now)
            con.execute(
                "INSERT INTO oauth_codes (digest, account_id, client_id, redirect_uri,"
                " redirect_uri_explicit, code_challenge, expires_at, created_at, user_id,"
                " projects, access, audience) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _digest(code),
                    self._account_id,
                    client_id,
                    redirect_uri,
                    int(redirect_uri_explicit),
                    code_challenge,
                    _iso(now + timedelta(seconds=CODE_TTL)),
                    _iso(now),
                    self._user_id,
                    *reach,
                    audience,
                ),
            )
            record_made_with(con, self._account_id, MADE_CODE + _digest(code), self.made_through)
            (name,) = con.execute(
                "SELECT name FROM oauth_clients WHERE id = ?", (client_id,)
            ).fetchone()
            # A1: the approval is its approver's act, recorded as it is given; no grant yet.
            detail = {
                "agent": name,
                "client": client_id,
                "redirect_uri": redirect_uri,
                **_reach_detail(*reach),
            }
            self._audit(con, "agent.approved", detail=detail, at=_iso(now))
        return code

    def find_code(self, code: str) -> CodeInfo | None:
        """A code that has not expired, redeemed or not: redeem_code decides whether it still
        buys anything."""
        with self._read() as con:
            row = con.execute(
                "SELECT client_id, redirect_uri, redirect_uri_explicit, code_challenge, expires_at"
                " FROM oauth_codes WHERE digest = ? AND expires_at > ?",
                (_digest(code), self._stamp()),
            ).fetchone()
        if row is None:
            return None
        return CodeInfo(
            client_id=row[0],
            redirect_uri=row[1],
            redirect_uri_explicit=bool(row[2]),
            code_challenge=row[3],
            expires_at=datetime.fromisoformat(row[4]),
        )

    def redeem_code(
        self, code: str, client_id: str, *, audience: str | None = None
    ) -> TokenPair | None:
        """Exchange a code for a new grant and its first token pair, once; the grant is bound
        to the code's audience. None: an unknown or expired code, another client's, or one
        approved at another audience (nothing is written: the code is not spent). Presenting
        a code that was already redeemed also ends the grant it made (RFC 6749, 4.1.2)."""
        with self._write() as con:
            now = self._instant()
            _expire(con, now)
            row = con.execute(
                "SELECT account_id, grant_id, user_id, projects, access, audience FROM oauth_codes"
                f" WHERE digest = ? AND client_id = ? AND {_AUDIENCE.format(row='oauth_codes')}",
                (_digest(code), client_id, audience, audience),
            ).fetchone()
            if row is None:
                return None
            account_id, grant_id, user_id, projects, access, bound = row
            if grant_id is not None:
                self._disconnect(con, grant_id, by_agent=True, reason="code replayed")
                return None
            grant_id = "grant_" + secrets.token_hex(6)
            con.execute(
                "INSERT INTO oauth_grants (id, account_id, client_id, created_at, user_id,"
                " projects, access, audience) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (grant_id, account_id, client_id, _iso(now), user_id, projects, access, bound),
            )
            con.execute(
                "UPDATE oauth_codes SET grant_id = ? WHERE digest = ?", (grant_id, _digest(code))
            )
            con.execute(  # A1: the key the approval was made with, if any, made the grant
                "UPDATE made_with SET id = ? WHERE id = ?", (grant_id, MADE_CODE + _digest(code))
            )
            con.execute(
                "UPDATE oauth_clients SET granted_at = ? WHERE id = ? AND granted_at IS NULL",
                (_iso(now), client_id),
            )
            (name,) = con.execute(
                "SELECT name FROM oauth_clients WHERE id = ?", (client_id,)
            ).fetchone()
            detail = {"grant": grant_id, "client": client_id, **_reach_detail(projects, access)}
            if user_id is not None:
                detail["user"] = user_id
            # The agent connects itself, in the workspace that approved it: this call runs on
            # whatever view the token endpoint holds, so neither its account nor its actor.
            self._audit(
                con,
                "agent.connected",
                detail=detail,
                account_id=account_id,
                actor=("token", name),
                actor_id=grant_id,
            )
            return _issue(con, grant_id, now)

    def refresh_token_client(self, refresh_token: str) -> str | None:
        """The client a refresh token was issued to, whether it is the grant's current one or
        one it replaced in the last 30 days: rotate_refresh_token tells them apart. One
        rotated longer ago is unknown."""
        with self._read() as con:
            row = con.execute(
                "SELECT g.client_id FROM oauth_tokens AS t"
                " JOIN oauth_grants AS g ON g.id = t.grant_id"
                " WHERE t.digest = ? AND t.kind IN ('refresh', 'rotated') AND t.expires_at > ?",
                (_digest(refresh_token), _iso(self._instant())),
            ).fetchone()
        return row[0] if row else None

    def rotate_refresh_token(
        self,
        refresh_token: str,
        client_id: str,
        *,
        audience: str | None = None,
        allowed: Callable[[str], bool] | None = None,
    ) -> TokenPair | None:
        """Replace the grant's token pair: the old access token stops working, and the old
        refresh token's digest is remembered (kind `rotated`) for REFRESH_TOKEN_TTL from now,
        the 30 days it would have lived. Presenting any remembered token ends the whole grant:
        two parties hold tokens of one grant, and one of them stole it. However many times a
        thief who copied a refresh token rotates before the client refreshes, the client's
        token is one the grant rotated, and is remembered until it would have expired anyway.
        A token rotated more than 30 days ago is unknown (None; no grant changes, though the
        write may remove OAuth rows that had already expired, as any call here does), and the
        grant's next rotation removes its row, in the same write; ending the grant removes them
        all. None: no new pair (unknown or expired token, another client's, a grant bound to
        another audience, or the grant was just ended). A token of a grant bound to another
        audience is unknown, a rotated one included: no grant changes and none ends (the same
        removal of expired rows aside).

        A grant rotates at most ROTATIONS (6) times in any ROTATION_WINDOW (an hour), counted
        from its remembered rows in this same write: one more raises TooManyRotations, and
        nothing is written (not even the removal of what had expired). Each rotation is
        remembered for 30 days, so a grant holds at most 6 x 24 x 30 = 4,320 rotated rows. The
        count lives in the database, so it holds across restarts and processes. A rotated
        token presented again ends the grant whatever the count; an unknown one counts
        nothing."""
        with self._write() as con:
            now = self._instant()
            _expire(con, now)
            row = con.execute(
                "SELECT t.grant_id, t.kind, g.user_id, u.email FROM oauth_tokens AS t"
                " JOIN oauth_grants AS g ON g.id = t.grant_id"
                " LEFT JOIN users AS u ON u.id = g.user_id"
                " WHERE t.digest = ? AND t.kind IN ('refresh', 'rotated') AND g.client_id = ?"
                f" AND t.expires_at > ? AND {_AUDIENCE.format(row='g')}",
                (_digest(refresh_token), client_id, _iso(now), audience, audience),
            ).fetchone()
            if row is None:
                return None
            grant_id, kind, user_id, email = row
            if kind == "rotated":
                self._disconnect(con, grant_id, by_agent=True, reason="refresh token replayed")
                return None
            if (
                user_id is not None
                and allowed is not None
                and (email is None or not allowed(email))
            ):
                return None
            # A token rotated at r is remembered until r + REFRESH_TOKEN_TTL: those rotated in
            # the last ROTATION_WINDOW are remembered past now + TTL - WINDOW.
            recent = now + timedelta(seconds=REFRESH_TOKEN_TTL - ROTATION_WINDOW)
            ((rotations,),) = con.execute(
                "SELECT count(*) FROM oauth_tokens"
                " WHERE grant_id = ? AND kind = 'rotated' AND expires_at > ?",
                (grant_id, _iso(recent)),
            )
            if rotations >= ROTATIONS:
                raise TooManyRotations(grant_id)
            con.execute(
                "DELETE FROM oauth_tokens WHERE grant_id = ?"
                " AND (kind = 'access' OR (kind = 'rotated' AND expires_at <= ?))",
                (grant_id, _iso(now)),
            )
            con.execute(
                "UPDATE oauth_tokens SET kind = 'rotated', expires_at = ? WHERE digest = ?",
                (_iso(now + timedelta(seconds=REFRESH_TOKEN_TTL)), _digest(refresh_token)),
            )
            return _issue(con, grant_id, now)

    def revoke_token(self, token: str, client_id: str) -> bool:
        """End the grant that an access or refresh token of this client belongs to; True when
        there was one."""
        with self._write() as con:
            now = self._instant()
            _expire(con, now)
            row = con.execute(
                "SELECT t.grant_id FROM oauth_tokens AS t"
                " JOIN oauth_grants AS g ON g.id = t.grant_id"
                " WHERE t.digest = ? AND g.client_id = ? AND t.expires_at > ?",
                (_digest(token), client_id, _iso(now)),
            ).fetchone()
            if row is None:
                return False
            self._disconnect(con, row[0], by_agent=True)
            return True

    def resolve_token(self, access_token: str) -> Principal | None:
        """Whose live access token this is, looking in every account; the label is the name
        of the client it was issued to, and the user the person who approved it."""
        found = self.identify_token(access_token)
        return found[0] if found else None

    def identify_token(
        self, access_token: str, *, audience: str | None = None
    ) -> tuple[Principal, str] | None:
        """resolve_token, and the grant's id, which tells apart agents that registered the
        same client name: the actor id of what the agent does (acting_as). With an
        `audience` (the server's MCP address), only a token of a grant bound to it."""
        with self._read() as con:
            row = con.execute(
                "SELECT g.account_id, c.name, g.user_id,"
                " g.rowid, typeof(g.projects) = 'null',"
                " CASE WHEN typeof(g.projects) = 'null' THEN g.access END, g.id"
                " FROM oauth_tokens AS t"
                " JOIN oauth_grants AS g ON g.id = t.grant_id"
                " JOIN oauth_clients AS c ON c.id = g.client_id"
                " WHERE t.digest = ? AND t.kind = 'access' AND t.expires_at > ?"
                " AND CASE WHEN typeof(g.projects) = 'null'"
                f" THEN {_AUDIENCE.format(row='g')} ELSE 1 END",
                (_digest(access_token), self._stamp(), audience, audience),
            ).fetchone()
            if row is None:
                return None
            account_id, name, user_id, rowid, is_null, access, grant_id = row
            try:
                projects, length = _read_stored_reach(con, "oauth_grants", rowid, is_null)
                if not is_null:
                    # Access and audience follow projects in the record: SQLite must not
                    # fetch either until its scope fits, even for a wrong audience.
                    row = con.execute(
                        "SELECT g.access FROM oauth_grants AS g WHERE g.rowid = ?"
                        f" AND {_AUDIENCE.format(row='g')}",
                        (rowid, audience, audience),
                    ).fetchone()
                    if row is None:
                        return None
                    (access,) = row
                reach = _admitted_reach(projects, length)
            except TooWide:  # a reach no cap allows: the token is refused, as one not known
                return None
        principal = Principal(
            account_id=account_id,
            kind="token",
            label=name,
            user_id=user_id,
            projects=reach,
            access=access,
        )
        return principal, grant_id

    def list_grants(self) -> list[GrantInfo]:
        """This account's live grants, oldest first: every one for a view that manages the
        workspace, else only those its person approved; each with its reach as far as the
        view reaches, and the key its approval was made with (A1, as list_keys)."""
        with self._read() as con:
            rows = con.execute(
                "SELECT g.id, g.client_id, c.name, g.created_at, g.projects, g.access,"
                " g.user_id, maker.label, m.key_id IS NOT NULL AND maker.id IS NULL"
                " FROM oauth_grants AS g"
                " JOIN oauth_clients AS c ON c.id = g.client_id"
                " LEFT JOIN made_with AS m ON m.id = g.id"
                " LEFT JOIN api_keys AS maker ON maker.id = m.key_id"
                " WHERE g.account_id = ? AND (? IS NULL OR g.user_id = ?)"
                " AND EXISTS (SELECT 1 FROM oauth_tokens"
                "  WHERE grant_id = g.id AND kind = 'refresh' AND expires_at > ?)"
                " ORDER BY g.seq",
                (self._account_id, *self._approver(con), self._stamp()),
            ).fetchall()
            reach = self._reach(con)
            return [
                GrantInfo(
                    id=grant_id,
                    client_id=client_id,
                    client_name=name,
                    created_at=created_at,
                    projects=_shown_reach(con, self._account_id, projects, reach),
                    access=access,
                    made_with=made_with,
                    made_with_revoked=bool(revoked),
                    approver_id=user_id,
                )
                for (
                    grant_id,
                    client_id,
                    name,
                    created_at,
                    projects,
                    access,
                    user_id,
                    made_with,
                    revoked,
                ) in rows
            ]

    def revoke_grant(self, grant_id: str) -> bool:
        """End a grant of this account, and with it every token it issued; True when there
        was one. A person who does not manage the workspace ends only the agents they
        approved: another's grant is as unknown (False)."""
        with self._write() as con:
            _expire(con, self._instant())
            owned = con.execute(
                "SELECT 1 FROM oauth_grants WHERE id = ? AND account_id = ?"
                " AND (? IS NULL OR user_id = ?)",
                (grant_id, self._account_id, *self._approver(con)),
            ).fetchone()
            if owned is None:
                return False
            self._disconnect(con, grant_id, by_agent=False)
            return True

    def _approver(self, con: sqlite3.Connection) -> tuple[str | None, str | None]:
        """Whose grants this view sees and ends, twice, for `(? IS NULL OR user_id = ?)`:
        None for a view that manages the workspace (every grant), else its person."""
        mine = None if self._manages(con) else self._user_id
        return mine, mine

    def _disconnect(
        self, con: sqlite3.Connection, grant_id: str, *, by_agent: bool, reason: str | None = None
    ) -> None:
        """End a grant and record agent.disconnected in its workspace: under the agent when
        the agent's own call ended it (it revoked a token, or a code or refresh token was
        presented again), else under this view's actor (a person disconnected it)."""
        row = con.execute(
            "SELECT g.account_id, c.name FROM oauth_grants AS g"
            " JOIN oauth_clients AS c ON c.id = g.client_id WHERE g.id = ?",
            (grant_id,),
        ).fetchone()
        _end_grant(con, grant_id)
        if row is None:  # already ended
            return
        account_id, name = row
        detail = {"grant": grant_id, "agent": name, **({"reason": reason} if reason else {})}
        if not by_agent:
            self._audit(con, "agent.disconnected", detail=detail)
            return
        agent = ("token", name)
        self._audit(
            con,
            "agent.disconnected",
            detail=detail,
            account_id=account_id,
            actor=agent,
            actor_id=grant_id,
        )


def _issue(con: sqlite3.Connection, grant_id: str, now: datetime) -> TokenPair:
    """A new access token and refresh token for the grant."""
    pair = TokenPair(
        access_token=ACCESS_TOKEN_PREFIX + secrets.token_urlsafe(32),
        refresh_token=REFRESH_TOKEN_PREFIX + secrets.token_urlsafe(32),
        expires_in=ACCESS_TOKEN_TTL,
    )
    con.executemany(
        "INSERT INTO oauth_tokens (digest, grant_id, kind, expires_at, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            (_digest(token), grant_id, kind, _iso(now + timedelta(seconds=ttl)), _iso(now))
            for token, kind, ttl in (
                (pair.access_token, "access", ACCESS_TOKEN_TTL),
                (pair.refresh_token, "refresh", REFRESH_TOKEN_TTL),
            )
        ],
    )
    return pair


def _end_grant(con: sqlite3.Connection, grant_id: str) -> None:
    """Remove a grant and every token digest it holds, the rotated ones included."""
    con.execute("DELETE FROM oauth_tokens WHERE grant_id = ?", (grant_id,))
    con.execute("DELETE FROM oauth_grants WHERE id = ?", (grant_id,))
    con.execute("DELETE FROM made_with WHERE id = ?", (grant_id,))


def _expire(con: sqlite3.Connection, now: datetime) -> None:
    """Remove what has run out: codes, access tokens, and every grant whose refresh token has
    expired, with all it holds. Writes call this; reads only skip expired rows. A write reads
    `now` once it holds the write lock, never before: a code or token that expires while the
    call waits for the lock is expired when the write happens."""
    moment = _iso(now)
    con.execute("DELETE FROM oauth_codes WHERE expires_at <= ?", (moment,))
    con.execute(
        "DELETE FROM oauth_grants WHERE id IN"
        " (SELECT grant_id FROM oauth_tokens WHERE kind = 'refresh' AND expires_at <= ?)",
        (moment,),
    )
    con.execute(
        "DELETE FROM oauth_tokens WHERE (kind = 'access' AND expires_at <= ?)"
        " OR grant_id NOT IN (SELECT id FROM oauth_grants)",
        (moment,),
    )
    # A1: what an expired approval or an ended grant was made with goes with it.
    con.execute(
        "DELETE FROM made_with WHERE (id LIKE ? AND substr(id, ?) NOT IN"
        " (SELECT digest FROM oauth_codes)) OR (id LIKE 'grant\\_%' ESCAPE '\\'"
        " AND id NOT IN (SELECT id FROM oauth_grants))",
        (MADE_CODE + "%", len(MADE_CODE) + 1),
    )
