"""A Google account a person connected in the browser: the custody of its refresh token.

The flow that obtains the token is cairn.web.google_connect; this module keeps what it got.

The registry and the row
- Connecting writes the credential's settings row (cairn.store.sources, CREDENTIAL_PREFIX), as
  `credential-add` does, with `"hosted": true`, its owner the person, and the kind's credential
  settings naming HOSTED_ENV as `credentials_env`: a name a source's config may hold, which a
  run never reads from the environment (cairn.sources.runner hands the kind the document
  under a name no real variable can have). Beside it, one row of `credential_tokens`
  (cairn.store.schema): the workspace, the credential's name, kind, owner, the Google
  account's address, the scopes Google granted, the refresh token sealed, and when it was
  added and last issued.
- Sealing (Sealer): AES-GCM (`cryptography`) under the 32-byte key from the server's
  environment, a fresh 12-byte nonce each time, and the workspace, the credential's name
  (case-folded) and the owner as associated data (`associated`, a JSON list, so no part can
  run into the next). A sealed value copied to another row, or read under another key, does
  not open: TokenUnusable(UNOPENED).
- The key and the OAuth client live in this process only (GoogleCustody, on the root store as
  `google_custody`, which every view copies). Without them nothing here reads the table:
  TokenUnusable(NOT_HERE).
- A token, the client's secret and the key are never in an exception's message, a repr, the
  audit log (its events name the credential and the kind) or an export (the table is not
  exported). Deleting the workspace deletes its rows (cairn.store.accounts); disconnecting
  deletes the row in the write that deletes the settings row (Store.remove_credential with
  `hosted=True`, which only `disconnect_google` passes).

Who
- `connect_google`: a person who owns or administers the workspace, for themselves. The
  credential's name comes from the kind, the person and the address (`name_for`): letters,
  digits and dots only, so it cannot inject, and one Google account connected again by the same
  person for the same kind is the same credential (the token is replaced). With `expect`
  (reconnect), a consent given as another Google account is refused and changes nothing.
- `google_accounts`, `google_token`, `disconnect_google`: the person's own (a view acting for
  no person: any). Another person's credential is unknown (KeyError).
- `hosted_document(source)`: for a run (a view acting for no person, as a run's is): the
  `authorized_user` document of the account the source was made from.
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict

from cairn.store.base import StoreBase, _iso, _require_account
from cairn.store.sources import (
    CREDENTIAL_PREFIX,
    CREDENTIALS_MAX,
    SOURCE_CREDENTIAL_PREFIX,
    _credential,
    _rows,
    _set_row,
)

HOSTED_KINDS = ("gmail", "google_calendar", "google_drive", "google_meet")
# What the config of a source made from a connected account names as `credentials_env`: a
# name the kinds' check accepts. A run never reads a variable of this name from the
# environment: it hands the kind the document under HOSTED_VARIABLE instead.
HOSTED_ENV = "CAIRN_CONNECTED_GOOGLE_ACCOUNT"
KEY_BYTES = 32
NONCE_BYTES = 12
ADDRESS_MAX = 320
LOCAL_MAX = 20  # characters of the address's local part a credential's name keeps
DIGEST = 10  # hex characters of the name's digest (40 bits: one person's accounts never meet)
# Three failures, three sentences: Google refused the token (a run's refresh said so), the
# token does not open here (the sealing key was changed), the account's row is gone.
RECONNECT = (
    "Google no longer accepts this connection: reconnect your Google account on the Sources page"
)
UNOPENED = (
    "this server cannot open the token of this connection, because its sealing key was"
    " changed: reconnect your Google account on the Sources page"
)
GONE = (
    "the Google account this source was made from is no longer connected here: connect it"
    " again on the Sources page and make the source from it"
)
NOT_HERE = (
    "this server does not hold connected Google accounts now: it reads them only when it is"
    " started with --google-connect"
)
OTHER_GOOGLE_ACCOUNT = (
    "you consented as another Google account than the one this connection reads, so nothing"
    " was changed: sign in to Google as that account and reconnect"
)
NOT_A_PERSON = "a Google account is connected by a person, for themselves"
NAME_TAKEN = "the name this connection would have is used by another account here"
NOT_ONE_OF_YOURS = "a credential of that name is not a Google account you connected"


class TokenUnusable(Exception):
    """A connected account's token cannot be used here: NOT_HERE (no key in this process) or
    UNOPENED (it does not open) or GONE (its row is gone). The message is one of those."""


class Sealer:
    """AES-GCM under one 32-byte key, the current one, and any earlier keys beside it
    (`previous`, from <VARIABLE>_PREVIOUS: `previous_keys`). `seal` gives base64url of the nonce
    and the sealed text, always under the current key; `open` gives the text back, or None
    when it does not open (no key given, other associated data, a value changed or not one of
    these). A value names no key: `open` tries the current key, then each earlier one in the
    order given, so a copy of the database tells no one which key sealed which value, and a
    value sealed before earlier keys could be given opens as any other. `current`: whether the
    current key opens a value (what `reseal` leaves as it is)."""

    __slots__ = ("_aead", "_previous")

    def __init__(self, key: bytes, previous: Sequence[bytes] = ()) -> None:
        if not isinstance(key, bytes) or len(key) != KEY_BYTES:
            raise ValueError(f"the sealing key is {KEY_BYTES} bytes")
        for earlier in previous:
            if not isinstance(earlier, bytes) or len(earlier) != KEY_BYTES:
                raise ValueError(f"an earlier sealing key is {KEY_BYTES} bytes")
            if hmac.compare_digest(earlier, key):
                raise ValueError("an earlier sealing key is the current one")
        self._aead = AESGCM(key)
        self._previous = tuple(AESGCM(earlier) for earlier in previous)

    def __repr__(self) -> str:  # never a key
        return "Sealer()"

    def seal(self, plain: str, associated: bytes) -> str:
        nonce = os.urandom(NONCE_BYTES)
        sealed = self._aead.encrypt(nonce, plain.encode("utf-8"), associated)
        return base64.urlsafe_b64encode(nonce + sealed).decode("ascii")

    def open(self, sealed: object, associated: bytes) -> str | None:
        for aead in (self._aead, *self._previous):
            plain = _opened(aead, sealed, associated)
            if plain is not None:
                return plain
        return None

    def current(self, sealed: object, associated: bytes) -> bool:
        return _opened(self._aead, sealed, associated) is not None


PREVIOUS = "_PREVIOUS"  # <VARIABLE>_PREVIOUS: the earlier keys beside the key <VARIABLE>
PREVIOUS_SEPARATOR = ","  # between earlier keys: never in base64


def previous_keys(environ: Mapping[str, str], variable: str, key: bytes) -> tuple[bytes, ...]:
    """The earlier keys `variable`_PREVIOUS holds beside the key `key` of `variable`: none when
    it is unset or blank, else KEY_BYTES-byte keys in base64 separated by commas (spaces around
    each allowed). ValueError naming the variable, never a value: an item that is empty or not
    such a key, or one equal to `key`."""
    name = variable + PREVIOUS
    raw = environ.get(name, "").strip()
    if not raw:
        return ()
    found = []
    for item in raw.split(PREVIOUS_SEPARATOR):
        try:
            earlier = base64.b64decode(item.strip(), validate=True)
        except (binascii.Error, ValueError):
            earlier = b""
        if len(earlier) != KEY_BYTES:
            raise ValueError(
                f"{name} is not one or more keys of {KEY_BYTES} bytes in base64, separated by"
                " commas"
            )
        if hmac.compare_digest(earlier, key):
            raise ValueError(f"{name} holds the key {variable} holds: an earlier key is another")
        found.append(earlier)
    return tuple(found)


def _opened(aead: AESGCM, sealed: object, associated: bytes) -> str | None:
    if not isinstance(sealed, str):
        return None
    try:
        raw = base64.urlsafe_b64decode(sealed.encode("ascii"))
    except (binascii.Error, ValueError, UnicodeError):
        return None
    if len(raw) <= NONCE_BYTES + 16:
        return None
    try:
        plain = aead.decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], associated)
    except InvalidTag:
        return None
    return plain.decode("utf-8")


def associated(account_id: str, credential: str, owner: str | None) -> bytes:
    """The associated data a token is sealed with: its workspace, its credential's name
    (case-folded, as the settings row is keyed) and its owner."""
    return json.dumps([account_id, credential.casefold(), owner]).encode("utf-8")


@dataclass(frozen=True)
class GoogleCustody:
    """What this process holds to keep and use connected Google accounts: the sealer, and the
    Web application OAuth client the tokens were issued to."""

    sealer: Sealer = field(repr=False)
    client_id: str
    client_secret: str = field(repr=False)
    # this process's loop over the google_revocations table (cairn.web.google_connect.
    # Revoker): a drop wakes it
    waiting: "Waiting" = field(default_factory=lambda: Waiting(), repr=False, compare=False)


@dataclass
class Waiting:
    """`draining`: a pass over the table is under way in this process. `wake`: set by the
    server's loop while it runs (cairn.web.google_connect.Revoker), to pass at once."""

    draining: bool = False
    wake: Callable[[], None] | None = None

    def woken(self) -> None:
        if self.wake is not None:
            self.wake()


class Connected(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    reconnected: bool


class HostedAccount(BaseModel):
    """A connected account as its person sees it: never its token."""

    model_config = ConfigDict(frozen=True)

    name: str
    kind: str
    address: str
    scopes: tuple[str, ...]
    added_at: str
    refreshed_at: str
    owner: str  # the person who connected it
    ended: bool = False  # a revocation of its address may have ended it: reconnect (RECONNECT)


@dataclass(frozen=True)
class Dropped:
    """A connected account just deleted: its address, and its token when it opened here
    (None: no key in this process, or it does not open), for ending Google's permission."""

    name: str
    kind: str
    address: str | None
    token: str | None = field(repr=False)
    switched: list[str] = field(default_factory=list)
    pending: str | None = None  # its google_revocations row, until Google's answer settles it
    subject: str | None = None  # Google's id of the account (the ID token's sub): for the rule
    claim: str | None = None  # the pending row's claim, when the dropper itself sends it


log = logging.getLogger("cairn.google_connect")  # the rule's own log: codes only

# Every dropped account's token waits until Google's answer settles it: one row per dropped
# account, never one replacing another (an older token may be the live one when a newer one
# has ended on its own). Rows come only from dropped accounts, each a person's connection
# (CONNECTED_PER_PERSON in a workspace); the table holds at most REVOCATIONS_MAX, and at that
# a newly dropped token is not kept (logged "revocations_full"), sent only once, at once.
REVOCATIONS_MAX = 10_000
# A waiting row is sent only by whoever claimed it (one UPDATE takes it): the disconnect that
# dropped it, which writes it claimed, or one pass of a server's loop. A claim not settled or
# released in CLAIM_STALE seconds (its process stopped) is taken again; far longer than a send.
CLAIM_STALE = 300
# A send Google refuses (neither 200 nor 400, nor unanswered: after the OAuth client was
# deleted, say) is tried again after BACKOFF_FIRST * 2**tries seconds, at most BACKOFF_MAX, and
# after REVOKE_TRIES refusals not again: the row is kept, and the operator ends the permission.
BACKOFF_FIRST = 60
BACKOFF_MAX = 86_400
REVOKE_TRIES = 20
NOT_REFUSED = ("unreachable", "kept")  # outcomes that wait for the next pass, without a try


def _waiting_for(revocation: str) -> bytes:
    """The associated data of a token waiting for its revocation: its row's own id."""
    return json.dumps(["google-revocation", revocation]).encode("utf-8")


# Each person connects at most CONNECTED_PER_PERSON accounts in a workspace (the four kinds,
# and room for a second address), so no one fills the workspace's CREDENTIALS_MAX, which the
# operator's accounts share. Each refusal says only what is the person's to know.
CONNECTED_PER_PERSON = 8
MINE_FULL = (
    f"you have {CONNECTED_PER_PERSON} Google accounts connected here already: disconnect one"
    " of yours first"
)
FULL = "no more accounts can be connected here now"


def name_for(kind: str, owner: str, address: str) -> str:
    """The credential's name: the kind, the address's local part in letters and digits, and a
    digest of the kind, the owner and the address (case-folded)."""
    folded = address.strip().casefold()
    local = re.sub(r"[^a-z0-9]", "", folded.partition("@")[0])[:LOCAL_MAX] or "account"
    digest = hashlib.sha256(json.dumps([kind, owner, folded]).encode()).hexdigest()[:DIGEST]
    return f"{kind}.{local}.{digest}"


SUBJECT_MAX = 255  # characters of Google's id of an account (OpenID Connect Core 2: 255 ASCII)


def _subject(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= SUBJECT_MAX
        or not value.isascii()
        or not value.isprintable()
        or any(c.isspace() for c in value)
    ):
        raise ValueError("Google named no usable id for the account")
    return value


def _address(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 3 <= len(value.strip()) <= ADDRESS_MAX
        or "@" not in value
        or not value.isprintable()
        or any(c.isspace() for c in value.strip())
    ):
        raise ValueError("Google named no usable address for the account")
    return value.strip()


class HostedMethods(StoreBase):
    google_custody: GoogleCustody | None = None  # set on the root store by create_app

    def _custody(self) -> GoogleCustody:
        custody = self.google_custody
        if custody is None:
            raise TokenUnusable(NOT_HERE)
        return custody

    def connect_google(
        self,
        kind: str,
        address: str,
        scopes: list[str],
        refresh_token: str,
        *,
        subject: str,
        expect: str | None = None,
    ) -> Connected:
        """Keep the Google account `subject` (Google's own id of it, the ID token's sub), at
        `address`, of `kind`, which Google granted `scopes`, as the view's person's credential,
        its token sealed. A new one is recorded as source.credential_added; one that exists
        (the same person, kind and Google account, whatever its address now) gets the new
        token, the address, the scopes and refreshed_at, and keeps its name, its sources and
        their settings (source.credential_reconnected). `expect`: the credential being
        reconnected; another Google account is PermissionError(OTHER_GOOGLE_ACCOUNT). Refused,
        nothing written: TokenUnusable(NOT_HERE), ValueError (another kind, no address or id,
        or the name another account's), PermissionError
        (no person), KeyError (`expect` is not the person's). Any member of the workspace
        connects their own (decision 145: a member's own account, the sources made from it
        into projects they manage; who is offered it is the server's list)."""
        from cairn.sources import CHOOSING, KINDS

        custody = self._custody()
        if kind not in HOSTED_KINDS:
            raise ValueError(f"only {', '.join(HOSTED_KINDS)} are connected in the browser")
        address = _address(address)
        subject = _subject(subject)
        if not isinstance(refresh_token, str) or not refresh_token:
            raise ValueError("Google gave no refresh token")
        if self._user_id is None:
            raise PermissionError(NOT_A_PERSON)
        owner = self._user_id
        name = name_for(kind, owner, address)
        choosing = CHOOSING[kind]
        checked = KINDS[kind].check(
            {"credentials_env": HOSTED_ENV, **choosing.sample, "projects": ["sample"]}
        )
        settings = {key: checked[key] for key in choosing.credential if key in checked}
        stamp = self._stamp()
        granted = " ".join(dict.fromkeys(s for s in scopes if isinstance(s, str) and s))
        with self._write() as con:
            _require_account(con, self._account_id)
            same = con.execute(  # the same Google account, of this kind, of this person
                "SELECT credential FROM credential_tokens"
                " WHERE account_id = ? AND owner = ? AND kind = ? AND subject = ?",
                (self._account_id, owner, kind, subject),
            ).fetchone()
            if expect is not None:
                wanted = self._own_hosted(con, expect)
                if same is None or wanted["name"].casefold() != same[0].casefold():
                    raise PermissionError(OTHER_GOOGLE_ACCOUNT)
            held = _credential(con, self._account_id, same[0] if same else name)
            if held is not None and same is not None:
                sealed = custody.sealer.seal(
                    refresh_token, associated(self._account_id, held["name"], owner)
                )
                con.execute(
                    "UPDATE credential_tokens SET sealed = ?, address = ?, scopes = ?,"
                    " refreshed_at = ?, ended_at = NULL WHERE account_id = ? AND credential = ?",
                    (sealed, address, granted, stamp, self._account_id, held["name"]),
                )
                detail = {"credential": held["name"], "kind": kind}
                self._audit(con, "source.credential_reconnected", detail=detail)
                return Connected(name=held["name"], reconnected=True)
            if held is not None:  # the name is another account's (an address passed on)
                raise ValueError(NAME_TAKEN)
            sealed = custody.sealer.seal(refresh_token, associated(self._account_id, name, owner))
            refused = self._room(con, owner)
            if refused is not None:
                raise ValueError(MINE_FULL if refused == "mine_full" else FULL)
            value = {
                "name": name, "kind": kind, "owner": owner, "settings": settings,
                "added_at": stamp, "hosted": True,
            }  # fmt: skip
            _set_row(con, self._account_id, CREDENTIAL_PREFIX + name.casefold(), value, stamp)
            con.execute(
                "INSERT INTO credential_tokens (account_id, credential, kind, owner, address,"
                " subject, scopes, sealed, added_at, refreshed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._account_id,
                    name,
                    kind,
                    owner,
                    address,
                    subject,
                    granted,
                    sealed,
                    stamp,
                    stamp,
                ),
            )
            self._audit(con, "source.credential_added", detail={"credential": name, "kind": kind})
            return Connected(name=name, reconnected=False)

    def google_room(self) -> str | None:
        """None when the view's person may connect one more account; else "mine_full" (they
        hold CONNECTED_PER_PERSON here) or "full" (the workspace holds CREDENTIALS_MAX):
        asked before the browser goes to Google, so neither gets that far."""
        with self._read() as con:
            return self._room(con, self._user_id)

    def _room(self, con: sqlite3.Connection, owner: str | None) -> str | None:
        mine = con.execute(
            "SELECT COUNT(*) FROM credential_tokens WHERE account_id = ? AND owner = ?",
            (self._account_id, owner),
        ).fetchone()[0]
        if mine >= CONNECTED_PER_PERSON:
            return "mine_full"
        if len(_rows(con, self._account_id, CREDENTIAL_PREFIX)) >= CREDENTIALS_MAX:
            return "full"
        return None

    def google_accounts(self) -> list[HostedAccount]:
        """The connected accounts of the view's person (every one for a view acting for no
        person), by name. Reads no token."""
        return self._google_accounts(everyone=False)

    def _google_accounts(self, *, everyone: bool) -> list[HostedAccount]:
        with self._read() as con:
            mine = None if everyone else self._user_id
            found = con.execute(
                "SELECT credential, kind, address, scopes, added_at, refreshed_at, owner,"
                " ended_at FROM credential_tokens WHERE account_id = ? AND (? IS NULL OR owner = ?)"
                " ORDER BY credential",
                (self._account_id, mine, mine),
            ).fetchall()
        return [
            HostedAccount(
                name=name,
                kind=kind,
                address=address,
                scopes=tuple(scopes.split()),
                added_at=added,
                refreshed_at=refreshed,
                owner=owner,
                ended=ended is not None,
            )
            for name, kind, address, scopes, added, refreshed, owner, ended in found
        ]

    def google_token(self, credential: str) -> str:
        """The refresh token of the person's own connected account `credential`, unsealed (to
        revoke it). KeyError: none of theirs. TokenUnusable: NOT_HERE, GONE, UNOPENED."""
        custody = self._custody()
        with self._read() as con:
            held = self._own_hosted(con, credential)
            return self._unsealed(con, custody, held["name"])

    def disconnect_google(self, credential: str) -> Dropped:
        """Delete the person's own connected account `credential`: its row and its settings
        row in one write, its sources switched off (as Store.remove_credential). What was
        dropped, its token unsealed when it opens here, for the caller to end Google's
        permission by the rule (cairn.web.google_connect.revoke_unless_held), after this
        write. KeyError: none of theirs."""
        with self._write() as con:
            held = _credential(con, self._account_id, credential)
            if held is None or not held.get("hosted"):
                raise KeyError(NOT_ONE_OF_YOURS)
            if not (
                self._user_id is None
                or held["owner"] == self._user_id
                or self._role_in(con) == "owner"
            ):
                raise KeyError(NOT_ONE_OF_YOURS)  # an owner disconnects any; others their own
            return self._drop_hosted(con, held, claimed=True)

    def google_accounts_to_manage(self) -> list[HostedAccount]:
        """What the Sources page lists with a Disconnect: the person's own connected accounts,
        and for an owner of the workspace every one in it."""
        with self._read() as con:
            everyone = self._user_id is None or self._role_in(con) == "owner"
        return self._google_accounts(everyone=everyone)

    def _drop_hosted_of(self, con: sqlite3.Connection, owner: str) -> list[Dropped]:
        """Inside a write: every connected account of the person `owner` in this workspace
        dropped (a removal: their sources switched off, as a disconnect does)."""
        names = [
            row[0]
            for row in con.execute(
                "SELECT credential FROM credential_tokens WHERE account_id = ? AND owner = ?",
                (self._account_id, owner),
            )
        ]
        dropped = []
        for name in names:
            held = _credential(con, self._account_id, name)
            if held is not None and held.get("hosted"):
                dropped.append(self._drop_hosted(con, held))
        return dropped

    def _hosted_tokens(self, con: sqlite3.Connection, account_id: str) -> list[Dropped]:
        """Inside a write that deletes the workspace `account_id`: each of its connected
        accounts, its token unsealed when it opens here and set to wait for its revocation
        (the rows go with the workspace)."""
        custody = self.google_custody
        found = con.execute(
            "SELECT credential, kind, owner, address, subject, sealed FROM credential_tokens"
            " WHERE account_id = ?",
            (account_id,),
        ).fetchall()
        dropped = []
        for name, kind, owner, address, subject, sealed in found:
            token = None
            if custody is not None:
                token = custody.sealer.open(sealed, associated(account_id, name, owner))
            pending, _ = self._wait_for_revocation(con, subject, address, token)
            dropped.append(Dropped(name, kind, address, token, pending=pending, subject=subject))
        return dropped

    def _wait_for_revocation(
        self,
        con: sqlite3.Connection,
        subject: str | None,
        address: str | None,
        token: str | None,
        *,
        claimed: bool = False,
    ) -> tuple[str | None, str | None]:
        """Inside the write that drops an account: its token, sealed again under a row of its
        own, until Google's answer settles it (settle_google_revocation); (its id, the claim
        it was written with when `claimed`: the dropper sends it). (None, None), and nothing
        written, without a token that opened here (nothing could be sent), or with the table
        at REVOCATIONS_MAX (the caller sends it once, at once)."""
        custody = self.google_custody
        if custody is None or token is None or subject is None or address is None:
            return None, None
        (count,) = con.execute("SELECT COUNT(*) FROM google_revocations").fetchone()
        if count >= REVOCATIONS_MAX:
            log.warning("google-connect: a dropped account's token was not kept: revocations_full")
            return None, None
        revocation = secrets.token_urlsafe(16)
        claim = secrets.token_urlsafe(16) if claimed else None
        stamp = self._stamp()
        con.execute(
            "INSERT INTO google_revocations (id, subject, address, sealed, ended_at, claimed_by,"
            " claimed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (revocation, subject, address, custody.sealer.seal(token, _waiting_for(revocation)),
             stamp, claim, stamp if claimed else None),
        )  # fmt: skip
        return revocation, claim

    def _queue_revocations(self, dropped: list[Dropped]) -> None:
        """After the write that dropped them: tell this process a revocation waits, which it
        sends by the rule after the request (cairn.web.google_connect.Block)."""
        custody = self.google_custody
        if custody is not None and any(one.pending for one in dropped):
            custody.waiting.woken()

    def pending_google_revocations(self) -> list[tuple[str, str, str | None]]:
        """(id, Google's id of the account, token) of each dropped account whose Google
        permission is still to be ended, that no one is sending (unclaimed, or its claim stale),
        and that is due (not backing off, not stopped after REVOKE_TRIES), oldest first; the
        token None where it does not open here (another key). Each is sent only once claimed
        (claim_google_revocation)."""
        custody = self._custody()
        with self._read() as con:
            found = con.execute(
                "SELECT id, subject, sealed FROM google_revocations"
                " WHERE (claimed_by IS NULL OR claimed_at < ?) AND tries < ?"
                " AND (due_at IS NULL OR due_at <= ?) ORDER BY ended_at, id",
                (self._stale(), REVOKE_TRIES, self._stamp()),
            ).fetchall()
        return [
            (revocation, subject, custody.sealer.open(sealed, _waiting_for(revocation)))
            for revocation, subject, sealed in found
        ]

    def _stale(self) -> str:
        return _iso(self._instant() - timedelta(seconds=CLAIM_STALE))

    def claim_google_revocation(self, revocation: str) -> str | None:
        """Take the waiting row `revocation` to send it, in one statement: its claim, or None
        when someone else holds it (a claim not yet stale) or it is gone."""
        claim = secrets.token_urlsafe(16)
        with self._write() as con:
            taken = con.execute(
                "UPDATE google_revocations SET claimed_by = ?, claimed_at = ?"
                " WHERE id = ? AND (claimed_by IS NULL OR claimed_at < ?)",
                (claim, self._stamp(), revocation, self._stale()),
            ).rowcount
        return claim if taken == 1 else None

    def settle_google_revocation(self, revocation: str, claim: str | None) -> None:
        """Google answered (or nothing can be sent): the waiting token `revocation` is deleted,
        by the holder of its `claim` only."""
        with self._write() as con:
            con.execute(
                "DELETE FROM google_revocations WHERE id = ? AND claimed_by = ?",
                (revocation, claim),
            )

    def release_google_revocation(
        self, revocation: str, claim: str | None, left: str = "unreachable"
    ) -> tuple[bool, bool]:
        """Not settled, by the holder of its `claim` only: the row `revocation` waits again,
        with the outcome `left`. Unanswered or kept (NOT_REFUSED): for the next pass. Any other
        is a refusal: one try more, and not due for BACKOFF_FIRST * 2**tries seconds (at most
        BACKOFF_MAX); at REVOKE_TRIES, never again. (whether `left` differs from the outcome
        before, to be logged; whether this refusal was the last try)."""
        with self._write() as con:
            row = con.execute(
                "SELECT tries, said FROM google_revocations WHERE id = ? AND claimed_by = ?",
                (revocation, claim),
            ).fetchone()
            if row is None:
                return False, False
            tries, said = row
            due = None
            if left not in NOT_REFUSED:
                tries += 1
                wait = min(BACKOFF_FIRST * 2**tries, BACKOFF_MAX)
                due = _iso(self._instant() + timedelta(seconds=wait))
            con.execute(
                "UPDATE google_revocations SET claimed_by = NULL, claimed_at = NULL, tries = ?,"
                " due_at = ?, said = ? WHERE id = ? AND claimed_by = ?",
                (tries, due, left, revocation, claim),
            )
        return left != said, left not in NOT_REFUSED and tries == REVOKE_TRIES

    def google_account_ended(self, subject: str) -> int:
        """After a revocation of the Google account `subject` (Google's id of it) was sent:
        every connected account on this server of it, connected while the revocation was under
        way, is marked to be reconnected (RECONNECT on its next run and on the page). How
        many."""
        with self._write() as con:
            return con.execute(
                "UPDATE credential_tokens SET ended_at = ? WHERE subject = ?",
                (self._stamp(), subject),
            ).rowcount

    def google_account_held(self, subject: str) -> bool:
        """Whether any connected account on this server, in any workspace, is of the Google
        account `subject` (Google's id of it, whatever its address): Google's permission is
        the project's, for that account, so it is ended only when no row holds it any more."""
        with self._read() as con:
            return (
                con.execute(
                    "SELECT 1 FROM credential_tokens WHERE subject = ? LIMIT 1", (subject,)
                ).fetchone()
                is not None
            )

    def _drop_hosted(
        self, con: sqlite3.Connection, held: dict, *, claimed: bool = False
    ) -> Dropped:
        """Inside a write: the account `held` (its settings row) dropped as remove_credential
        drops one, its token unsealed first when this process holds the key and it opens.
        `claimed`: its waiting row is written claimed, for the caller to send it itself."""
        row = con.execute(
            "SELECT owner, sealed, address, subject FROM credential_tokens"
            " WHERE account_id = ? AND credential = ?",
            (self._account_id, held["name"]),
        ).fetchone()
        token = None
        if row is not None and self.google_custody is not None:
            token = self.google_custody.sealer.open(
                row[1], associated(self._account_id, held["name"], row[0])
            )
        switched = self._drop_credential(con, held)  # type: ignore[attr-defined]
        address, subject = (row[2], row[3]) if row is not None else (None, None)
        pending, claim = self._wait_for_revocation(con, subject, address, token, claimed=claimed)
        return Dropped(
            name=held["name"],
            kind=held["kind"],
            address=address,
            token=token,
            switched=switched,
            pending=pending,
            subject=subject,
            claim=claim,
        )

    def hosted_document(self, source: str, *, account: str | None = None) -> str:
        """The `authorized_user` document, as text, of the connected account the source
        `source` was made from: what a run hands its kind. `account`: the account an unsaved
        source would be made from (a preview before saving), read in place of the source's
        link: only the view's person's own, checked here as Store.source_from_to_preview
        checked it first, so another person's account is as if it did not exist.
        TokenUnusable: NOT_HERE (checked before anything is read), GONE (no such link,
        account or row, or another person's account to preview) or UNOPENED (a token that
        does not open)."""
        custody = self._custody()
        with self._read() as con:
            previewed = account is not None
            if account is None:
                linked = con.execute(
                    "SELECT value FROM settings WHERE account_id = ? AND name = ?",
                    (self._account_id, SOURCE_CREDENTIAL_PREFIX + source.casefold()),
                ).fetchone()
                account = json.loads(linked[0]) if linked else None
            held = _credential(con, self._account_id, account)
            if held is None or not held.get("hosted"):
                raise TokenUnusable(GONE)
            if previewed and self._user_id is not None and held["owner"] != self._user_id:
                raise TokenUnusable(GONE)
            token = self._unsealed(con, custody, held["name"])
        return json.dumps(
            {
                "type": "authorized_user",
                "client_id": custody.client_id,
                "client_secret": custody.client_secret,
                "refresh_token": token,
            }
        )

    def _own_hosted(self, con: sqlite3.Connection, credential: object) -> dict:
        held = _credential(con, self._account_id, credential)
        if (
            held is None
            or not held.get("hosted")
            or (self._user_id is not None and held["owner"] != self._user_id)
        ):
            raise KeyError(NOT_ONE_OF_YOURS)
        return held

    def _unsealed(self, con: sqlite3.Connection, custody: GoogleCustody, credential: str) -> str:
        row = con.execute(
            "SELECT owner, sealed, ended_at FROM credential_tokens"
            " WHERE account_id = ? AND credential = ?",
            (self._account_id, credential),
        ).fetchone()
        if row is None:
            raise TokenUnusable(GONE)
        if row[2] is not None:  # a revocation of its address may have ended it
            raise TokenUnusable(RECONNECT)
        token = custody.sealer.open(row[1], associated(self._account_id, credential, row[0]))
        if token is None:
            raise TokenUnusable(UNOPENED)
        return token
