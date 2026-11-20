"""What cairn keeps of a workspace's Slack install (K1, decisions D1 to D12): schema step 29.

The install
- One install a workspace (`slack_installs.account_id` is unique), keyed by its own id,
  INSTALL_ID ("sl_" and 12 hexadecimal characters), which the three addresses Slack calls
  carry (cairn.web.slack). Its state is one of STATES: pending (made, nothing pasted yet),
  ready (Slack accepted its bot token: commands and actions are answered), on (Slack's
  signed challenge was answered), off (switched off, or its bot token revoked) and removed.
  `holds_secrets`: it holds both; `holds_bot_token`; `needs_bot_token`: off because its bot
  token was revoked (off with no bot token), so on again only through ready_slack_install
  with a new one, where an install switched off holds its token and is switched on as it is.
- `make_slack_install`: an owner or admin of the workspace (a view acting for no person
  too). The workspace's install as it is when it has one that is not removed; a removed one
  comes back pending under the same id, so the addresses in the app Slack holds stay right;
  otherwise a new one, pending.
- `ready_slack_install`: an owner or admin, once Slack accepted the token: the team, its
  domain, the bot's user id, and the two secrets sealed (Sealer, AES-GCM, a fresh nonce each
  time) under the key of this process (`slack_sealer`, set by create_app from CAIRN_SLACK_KEY;
  NoSlackKey without it) with the install, the workspace and which secret it is as associated
  data (`_associated`): a copy of the database opens neither, and a sealed value moved to
  another install, or to the other column, does not open. Pending, ready or off becomes ready;
  on stays on; removed is refused (make it pending first). An install that is ready, on or off
  belongs to its team: secrets of another team are NotItsTeam, and nothing changes (connecting
  another team takes removing the install first). Made ready with the team it had when it was
  removed, that team's notes are no longer due to be forgotten (D10), and only that team's:
  each row of slack_notes holds the team its install held when the row was made. Only the
  first time it is made ready after a removal (`removed_at` is cleared then), and only for the
  notes that removal made due: with another team set up in between, the old team's notes stay
  due and are forgotten on their day whatever happens to the install afterwards, a later
  paste of either team's secrets included, and the old team's links go. One Slack team has one
  install: the unique index slack_installs_team counts the installs that are ready, on or off (a
  pending or removed one holds no team), so making ready a team another workspace holds is
  TeamHeld, whatever races;
  `slack_team_held` counts the same states. A new bot token clears `bot_gone_at` and the count
  of ended answers.
- `switch_slack_install(on=)`: an owner or admin. Off from ready or on; on from ready or off
  while it holds its secrets (ValueError otherwise). `slack_install_verified`: Slack's
  challenge answered, ready becomes on (the server's call, on the root store).
- `remove_slack_install` (the owner, or Slack's app_uninstalled through a view acting for no
  person): the sealed secrets, the links and the offers are deleted at once, the install is
  removed, and every note it created and is not due yet is due FORGET_AFTER (10 days)
  later. Removing a removed install changes nothing. `revoke_slack_bot` (Slack's
  tokens_revoked naming the bot): off, the bot token deleted and `bot_gone_at` set (the first
  time is kept); the signing secret stays, sealed, so Slack's app_uninstalled after it is
  still verified (an install that is off answers at its events address, cairn.web.slack);
  nothing is due yet.
- The bot token checked with Slack (the server's, for cairn.web.slack.Keeper):
  `take_slack_checks` gives the installs that hold a bot token, not checked within CHECK_EVERY
  (6 hours), at most CHECKS_AT_ONCE (20), each stamped as checked when taken;
  `slack_token_checked` records what Slack said: two ENDED answers in a row (invalid_auth,
  account_inactive, token_revoked) do what revoke_slack_bot does, a good one starts the count
  again, anything else changes nothing; an answer about a token replaced since changes
  nothing. `remove_gone_slack_installs`: an install with no bot token for more than GONE_DAYS
  (3) since `bot_gone_at` is removed as an owner removes it, the audit record naming no person
  and saying why; one an owner only switched off holds its token and is never removed so.
- What is due (a claim with no note never is): `slack_notes_due` (the server's, every
  workspace, the oldest first) gives (workspace, note) pairs whose time came;
  cairn.web.slack.Keeper forgets each with cairn's own forgetting (Store.forget_note, H5b, by
  a view acting for no person), and the duty in cairn.store.erasure deletes its record.
  `forget_slack_notes_now` (an owner or admin) does it at once for a removed install.
  `slack_notes_to_forget`: how many and when, for the page.

Links, offers, notes, events
- `link_slack_user` / `unlink_slack_user` / `slack_person`: a Slack user of the install linked
  to a person who is a member of the workspace, with the project their notes go to
  (`set_slack_project`: a project of the workspace, or None). A link is never moved to
  another person (ValueError, and the first link stands); linking a Slack user to the person
  they are linked to changes nothing. Whenever a Slack user is linked, by any path, every
  offer of theirs at the install is deleted. `own_slack_links`: the view's own person's
  links, and nobody else's. `slack_team_held` (the server's): whether another workspace's
  install that is ready, on or off is for a Slack team, so one team has one install.
- A link takes two steps, so a Slack user is linked to a person only
  when that Slack user knew a code shown only to that person. `offer_slack_link`: a one-time
  value (kept as its SHA-256 digest) for OFFER_TTL (10 minutes, for both steps), one for a
  Slack user at a time: it replaces their earlier offer, claimed or not. `slack_offer` reads
  it for a view of the install's workspace (None: unknown, run out, said yes to already, or
  another workspace's). `claim_slack_offer`, the signed-in person's yes: ties the offer to
  them and gives the code, CODE_LENGTH (8) of CODE_ALPHABET from `secrets`, shown XXXX-XXXX,
  kept only as a digest bound to the install (KeyError: no such offer; PermissionError: a
  view acting for no person, or a person who is not a member). `take_slack_offer(install,
  Slack user, code)`, the command's, with the workspace's view: the code from the Slack user
  whose offer it is, within its time, links them to the person who said yes; anything else
  is KeyError with one sentence (NO_LINK), a wrong try counted (the TRIES-th, 5, deletes the
  offer), the offer of another Slack user whose code it is deleted, and an offer whose
  person is no longer a member deleted. A used offer is deleted.
- `claim_slack_message`: a filed message is claimed before anything is asked or written for
  it, a row with no note: CLAIMED, FILED (its note) or FILING (someone else's claim, not yet
  stored); `release_slack_claim` undoes a claim whose message was not stored; a claim with no
  note is swept after CLAIM_FOR (10 minutes). `record_slack_note`: the install created a note
  of the workspace, filed (the claim gets its note; the same message is one note, D9, and the
  note it already is comes back) or typed; an install removed meanwhile makes it due with its
  other notes. Given `con`, the connection of a write already open (Store.add_note's
  `within`), the record is in the note's own transaction (cairn.slack.people.write).
  `slack_note_for` finds the note a message already is.
- `slack_event_seen`: True the first time an install sees an event id, False after; ids are
  kept SEEN_FOR (a day). `forget_slack_event`: an id is no longer seen (its handler failed),
  so Slack's next delivery of it is the first time again. `sweep_slack` (also part of
  Store.sweep_expired) removes offers past their time, ids older than that and claims with
  no note older than CLAIM_FOR.

Who may: every method is a management call of cairn.store.reach (a narrowed view gets none).
The cross-workspace ones (`slack_inbound`, `slack_install_verified`, `slack_event_seen`,
`forget_slack_event`, `slack_notes_due`, `sweep_slack`, `slack_team_held`,
`take_slack_checks`, `slack_token_checked`, `remove_gone_slack_installs`) are the server's:
refused to a view acting for a person.
Nothing here holds a secret in a repr, an exception's message or the audit log, and no
table is exported (cairn.store.portable); a deleted workspace, a removed person, a deleted
project and a forgotten note each take their rows with them (cairn.store.accounts, people,
erasure)."""

import copy
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple, cast

from cairn.store.base import StoreBase, _iso, _require_account
from cairn.store.hosted import Sealer

if TYPE_CHECKING:
    from cairn.store import Store

INSTALL_ID = re.compile(r"sl_[0-9a-f]{12}")
STATES = ("pending", "ready", "on", "off", "removed")
ACCEPTING = ("ready", "on")  # the states whose addresses answer Slack
HOWS = ("filed", "typed")
COMMAND = "/cairn"
OFFER_TTL = timedelta(minutes=10)
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"  # no I, L, O, U, 0 or 1
CODE_LENGTH = 8
TRIES = 5  # wrong codes from a Slack user, the fifth of which deletes their offer
SEEN_FOR = timedelta(days=1)
FORGET_AFTER = timedelta(days=10)
DUE_AT_ONCE = 100  # notes slack_notes_due gives at most, oldest first
CLAIM_FOR = timedelta(minutes=10)  # a claim with no note after this is swept
CLAIMED, FILED, FILING = "claimed", "filed", "filing"  # the answers to a claim
CHECK_EVERY = timedelta(hours=6)  # one install's bot token is checked with Slack at most so often
CHECKS_AT_ONCE = 20  # installs checked in one pass, at most
GONE_DAYS = 3  # days an install holds no bot token, as Slack ended it, before it is removed
GONE_AFTER = timedelta(days=GONE_DAYS)
# Slack's answers to auth.test that mean the token is ended for good: invalid credentials, a
# token of a deleted user or workspace, the app removed. Anything else says nothing.
ENDED = frozenset({"invalid_auth", "account_inactive", "token_revoked"})
ANSWERS = ("works", "ended", "unclear")  # what a check of a bot token came to
SLACK_ID = re.compile(r"[A-Za-z0-9]{1,64}")  # a team, user, conversation or event id
MESSAGE_TIME = re.compile(r"[0-9]{1,12}\.[0-9]{1,9}")
DOMAIN = re.compile(r"[A-Za-z0-9-]{1,100}")
COMMAND_NAME = re.compile(r"/[a-z0-9_-]{1,31}")
SECRET_VALUE = re.compile(r"[\x21-\x7e]{1,512}")
NO_KEY = "this server holds no Slack key: it is started with --slack and CAIRN_SLACK_KEY"
NOT_ONE = "there is no such Slack install in this workspace"
NOT_AN_OWNER = "only an owner of the workspace forgets what came through Slack at once"
MEMBERS_ONLY = "a Slack user is linked to a person of this workspace"
LINKED_ELSEWHERE = "this Slack user is linked to another person: unlink it first"
NOT_OPEN = "this link offer is not open"
NO_LINK = "this code links nothing"


class TeamHeld(ValueError):
    """Another workspace's install that is ready, on or off is for this Slack team: one Slack
    team has one install (the unique index slack_installs_team), and nothing is written."""

    def __init__(self) -> None:
        super().__init__("another workspace on this server has Slack set up for this team")


class TeamChanged(ValueError):
    """A write that was Slack's for one team, arriving once the install holds another (it
    was removed and set up again for another Slack team meanwhile): nothing is written, since
    the note would be kept among the new team's, with no day to be forgotten."""

    def __init__(self) -> None:
        super().__init__("this install's Slack team is no longer the one this was written for")


class SlackInstallChanged(Exception):
    """The installation that verified the callback has since changed."""


class NotItsTeam(ValueError):
    """Secrets of another Slack team than the one an install that is ready, on or off holds:
    the install belongs to its team, so nothing is written; connecting another Slack team
    takes removing Slack first, after which the first team's notes are forgotten on their
    day."""

    def __init__(self) -> None:
        super().__init__(
            "this bot token is of another Slack workspace than the one set up here:"
            " remove Slack first to connect that one"
        )


class NoSlackKey(RuntimeError):
    """This process holds no key to seal or open an install's secrets (the switch is off):
    NO_KEY, and nothing written."""

    def __init__(self) -> None:
        super().__init__(NO_KEY)


@dataclass(frozen=True)
class SlackInstall:
    """An install as anyone may see it: never its secrets, only whether it holds them."""

    id: str
    account_id: str
    state: str
    team_id: str | None
    enterprise_id: str | None
    team_domain: str | None
    bot_user_id: str | None
    command: str
    made_by: str | None
    made_at: str
    removed_at: str | None
    holds_secrets: bool
    holds_bot_token: bool
    bot_gone_at: str | None = None  # since when Slack has ended its bot token; else None

    @property
    def needs_bot_token(self) -> bool:
        """Off because Slack revoked its bot token (Store.revoke_slack_bot): on again only once
        a new bot token is kept (Store.ready_slack_install). An install the owner switched off
        holds its token and is switched on as it is."""
        return self.state == "off" and not self.holds_bot_token


@dataclass(frozen=True, eq=False)
class SlackGeneration:
    """The set-up stamp and signing secret used to verify a request."""

    install_id: str
    account_id: str
    made_at: str
    signing_secret: str = field(repr=False)
    sealer: Sealer = field(repr=False)

    def require(self, con: sqlite3.Connection) -> None:
        row = con.execute(
            "SELECT signing_secret FROM slack_installs WHERE id = ? AND account_id = ?"
            " AND made_at = ?",
            (self.install_id, self.account_id, self.made_at),
        ).fetchone()
        if row is None:
            raise SlackInstallChanged()
        associated = _associated("signing_secret", self.install_id, self.account_id)
        current = self.sealer.open(row[0], associated)
        if current is None or not hmac.compare_digest(
            current.encode(), self.signing_secret.encode()
        ):
            raise SlackInstallChanged()


@dataclass(frozen=True)
class SlackInbound:
    """What a request to one of the three addresses needs: the install, and its signing
    secret opened (None: none held, or it does not open under this key)."""

    install: SlackInstall
    signing_secret: str | None = field(repr=False)
    generation: SlackGeneration | None = field(default=None, repr=False)


@dataclass(frozen=True)
class SlackPerson:
    install_id: str
    slack_user_id: str
    user_id: str
    project_id: str | None
    linked_at: str


@dataclass(frozen=True)
class SlackOffer:
    """An offer as the link page reads it: never its value, nor its code."""

    install_id: str
    slack_user_id: str
    expires_at: datetime


class SlackClaim(NamedTuple):
    """The answer to a claim on a message: CLAIMED (now, by this call), FILED (with its note)
    or FILING (claimed by someone else, and not yet stored)."""

    outcome: str
    note_id: str | None


@dataclass(frozen=True)
class SlackCheck:
    """An install whose bot token is to be checked with Slack: the token opened, and the
    sealed value it was read from, so an answer about a token replaced since changes nothing.
    Neither is in a repr."""

    install_id: str
    account_id: str
    token: str = field(repr=False)
    sealed: str = field(repr=False)


@dataclass(frozen=True)
class SlackDue:
    """The notes of a removed install still to be forgotten, and when the first is due."""

    install_id: str
    notes: int
    at: datetime


class SlackForgetting(NamedTuple):
    """One owned note and the deadline and installation seen when it was selected."""

    account_id: str
    note_id: str
    install_id: str
    made_at: str
    forget_at: str


class _ForgettingCancelled(Exception):
    """The selected note is no longer eligible for this erasure."""


_INSTALL_COLUMNS = (
    "id, account_id, state, team_id, enterprise_id, team_domain, bot_user_id, command,"
    " made_by, made_at, removed_at, signing_secret IS NOT NULL AND bot_token IS NOT NULL,"
    " bot_token IS NOT NULL, bot_gone_at"
)
_HOLDS_TEAM = "('ready', 'on', 'off')"  # the states whose install holds its team (the index)


def _install(row: tuple) -> SlackInstall:
    return SlackInstall(
        *row[:11], holds_secrets=bool(row[11]), holds_bot_token=bool(row[12]), bot_gone_at=row[13]
    )


def _associated(which: str, install_id: str, account_id: str) -> bytes:
    """What a secret is sealed with: which of the two it is, its install and its workspace."""
    return json.dumps(["slack", which, install_id, account_id]).encode("utf-8")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_code() -> str:
    """A code: CODE_LENGTH characters of CODE_ALPHABET, each from `secrets`."""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def _shown(code: str) -> str:
    """A code as a person is shown it: XXXX-XXXX."""
    return f"{code[:4]}-{code[4:]}"


def _typed(text: object) -> str | None:
    """The code a person typed, read without regard to case, hyphens or spaces; None when
    it is not one."""
    if not isinstance(text, str) or len(text) > 64:
        return None
    code = "".join(text.replace("-", " ").split()).upper()
    if len(code) != CODE_LENGTH or any(char not in CODE_ALPHABET for char in code):
        return None
    return code


def _code_digest(install_id: str, code: str) -> str:
    return _digest(f"{install_id} {code}")


def _slack_id(value: object, what: str) -> str:
    if not isinstance(value, str) or not SLACK_ID.fullmatch(value):
        raise ValueError(f"not a Slack {what} id")
    return value


def _message(conversation_id: object, message_time: object) -> None:
    _slack_id(conversation_id, "conversation")
    if not isinstance(message_time, str) or not MESSAGE_TIME.fullmatch(message_time):
        raise ValueError("a filed message has its time, as Slack writes it")


def _recordable(how: str, conversation_id: object, message_time: object) -> None:
    if how not in HOWS:
        raise ValueError(f"a note comes through Slack {' or '.join(HOWS)}")
    if how == "filed":
        _message(conversation_id, message_time)
    elif conversation_id is not None or message_time is not None:
        raise ValueError("a typed note has no conversation or message time")


def _when(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _next_slack_setup(now: datetime, previous: str) -> str:
    """Distinguish set-ups even when the wall clock stays put or goes back."""
    return _iso(max(now, _when(previous) + timedelta(microseconds=1)))


class SlackMethods(StoreBase):
    slack_sealer: Sealer | None = None  # set on the root store by create_app; views copy it
    _slack_view_check: Callable[[sqlite3.Connection], None] | None = None

    def _slack_checked_view(self, check: Callable[[sqlite3.Connection], None]) -> "Store":
        """A request's view, with a condition checked inside every write it makes."""
        view = copy.copy(self)
        view._owner = False
        view._slack_view_check = check
        return cast("Store", view)

    def _check_slack_view(self, con: sqlite3.Connection) -> None:
        if self._slack_view_check is not None:
            self._slack_view_check(con)

    @contextmanager
    def _write(self, **options: bool) -> Iterator[sqlite3.Connection]:
        # BEGIN IMMEDIATE is already held, so no other writer can change the checked row
        # before the operation commits. This also covers a note's separate page save.
        with super()._write(**options) as con:
            self._check_slack_view(con)
            yield con

    def _slack_sealer(self) -> Sealer:
        if self.slack_sealer is None:
            raise NoSlackKey()
        return self.slack_sealer

    def _slack_row(self, con: sqlite3.Connection, install_id: str) -> tuple:
        row = con.execute(
            f"SELECT {_INSTALL_COLUMNS} FROM slack_installs WHERE id = ? AND account_id = ?",
            (install_id, self._account_id),
        ).fetchone()
        if row is None:
            raise KeyError(NOT_ONE)
        return row

    def _slack_audit(self, con: sqlite3.Connection, action: str, install_id: str, **detail) -> None:
        self._audit(con, action, detail={"install": install_id, **detail})

    # The install

    def make_slack_install(self, command: str = COMMAND) -> SlackInstall:
        """The workspace's install (see the module): an owner or admin, else PermissionError.
        ValueError: a command name that is not a slash and 1 to 31 of a-z, 0-9, _ and -."""
        if not isinstance(command, str) or not COMMAND_NAME.fullmatch(command):
            raise ValueError("a slash command is / and 1 to 31 of a-z, 0-9, _ and -")
        with self._write() as con:
            _require_account(con, self._account_id)
            self._require_manager(con)
            row = con.execute(
                f"SELECT {_INSTALL_COLUMNS} FROM slack_installs WHERE account_id = ?",
                (self._account_id,),
            ).fetchone()
            if row is not None and row[2] != "removed":
                return _install(row)
            now = self._instant()
            if row is not None:
                install_id = row[0]
                stamp = _next_slack_setup(now, row[9])
                con.execute(
                    "UPDATE slack_installs SET state = 'pending', command = ?, made_by = ?,"
                    " made_at = ?, checked_at = NULL, dead_checks = 0, bot_gone_at = NULL"
                    " WHERE id = ?",
                    (command, self._user_id, stamp, install_id),
                )
            else:
                install_id = "sl_" + secrets.token_hex(6)
                con.execute(
                    "INSERT INTO slack_installs (id, account_id, state, command, made_by, made_at)"
                    " VALUES (?, ?, 'pending', ?, ?, ?)",
                    (install_id, self._account_id, command, self._user_id, _iso(now)),
                )
            self._slack_audit(con, "slack.install_made", install_id)
            return _install(self._slack_row(con, install_id))

    def get_slack_install(self) -> SlackInstall | None:
        """The workspace's install, in whatever state, or None. Reads no secret."""
        with self._read() as con:
            row = con.execute(
                f"SELECT {_INSTALL_COLUMNS} FROM slack_installs WHERE account_id = ?",
                (self._account_id,),
            ).fetchone()
        return _install(row) if row else None

    def ready_slack_install(
        self,
        install_id: str,
        *,
        signing_secret: str,
        bot_token: str,
        team_id: str,
        team_domain: str,
        bot_user_id: str,
        enterprise_id: str | None = None,
    ) -> SlackInstall:
        """Keep the install's two secrets and its team once Slack accepted the token (see
        the module). KeyError: no such install here; NotItsTeam: an install ready, on or off for
        another team; ValueError: a removed install, or a value that is blank or not of its
        shape (never quoted); PermissionError: not an owner or admin; NoSlackKey."""
        sealer = self._slack_sealer()
        for value in (signing_secret, bot_token):
            if not isinstance(value, str) or not SECRET_VALUE.fullmatch(value):
                raise ValueError("the signing secret and the bot token are what Slack shows")
        _slack_id(team_id, "team")
        _slack_id(bot_user_id, "user")
        if enterprise_id is not None:
            _slack_id(enterprise_id, "enterprise")
        if not isinstance(team_domain, str) or not DOMAIN.fullmatch(team_domain):
            raise ValueError("not a Slack workspace's domain")
        with self._write() as con:
            self._require_manager(con)
            row = self._slack_row(con, install_id)
            state, was_team = row[2], row[3]
            if state == "removed":
                raise ValueError("this Slack install was removed: set it up again first")
            if state != "pending" and was_team != team_id:  # it belongs to its team
                raise NotItsTeam()
            account = self._account_id
            kept = con.execute(
                "SELECT signing_secret FROM slack_installs WHERE id = ?", (install_id,)
            ).fetchone()[0]
            held = (
                sealer.open(kept, _associated("signing_secret", install_id, account))
                if kept is not None
                else None
            )
            # on stays on only with the signing secret a signed request proved already
            proven = (
                state == "on" and held is not None and hmac.compare_digest(held, signing_secret)
            )
            sealed = (
                sealer.seal(signing_secret, _associated("signing_secret", install_id, account)),
                sealer.seal(bot_token, _associated("bot_token", install_id, account)),
            )
            now = self._instant()
            made_at = _next_slack_setup(now, row[9])
            try:
                con.execute(
                    "UPDATE slack_installs SET state = ?, team_id = ?, enterprise_id = ?,"
                    " team_domain = ?, bot_user_id = ?, signing_secret = ?, bot_token = ?,"
                    " made_at = ?, removed_at = NULL, checked_at = ?, dead_checks = 0,"
                    " bot_gone_at = NULL"
                    " WHERE id = ?",
                    ("on" if proven else "ready", team_id, enterprise_id, team_domain,
                     bot_user_id, *sealed, made_at, _iso(now), install_id),
                )  # fmt: skip
            except sqlite3.IntegrityError:  # slack_installs_team: another workspace's team
                raise TeamHeld() from None
            if row[10] is not None:  # first made ready since its latest removal (D10)
                con.execute(  # that removal's notes of this same team are not due any more
                    "UPDATE slack_notes SET forget_at = NULL WHERE install_id = ? AND team_id = ?"
                    " AND forget_at = ?",
                    (install_id, team_id, _iso(_when(row[10]) + FORGET_AFTER)),
                )
            if was_team is not None and was_team != team_id:  # the old team's people are not its
                con.execute("DELETE FROM slack_people WHERE install_id = ?", (install_id,))
            self._slack_audit(con, "slack.install_ready", install_id, team=team_id)
            return _install(self._slack_row(con, install_id))

    def switch_slack_install(self, install_id: str, *, on: bool) -> SlackInstall:
        """Switch the install on or off (see the module). KeyError, PermissionError, or
        ValueError for a state it cannot go from."""
        with self._write() as con:
            self._require_manager(con)
            row = self._slack_row(con, install_id)
            state, holds = row[2], bool(row[11])
            if on and (state not in ("ready", "off", "on") or not holds):
                raise ValueError("only an install that holds its secrets is switched on")
            if not on and state not in ("ready", "on", "off"):
                raise ValueError("only an install that is ready or on is switched off")
            con.execute(
                "UPDATE slack_installs SET state = ? WHERE id = ?",
                ("on" if on else "off", install_id),
            )
            self._slack_audit(con, "slack.install_switched", install_id, on=bool(on))
            return _install(self._slack_row(con, install_id))

    def slack_install_verified(self, install_id: str) -> SlackInstall | None:
        """The server's, after Slack's signed challenge was answered: a ready install
        becomes on. The install as it is now; None for one that is not ready or on."""
        with self._write() as con:
            con.execute(
                "UPDATE slack_installs SET state = 'on' WHERE id = ? AND state = 'ready'",
                (install_id,),
            )
            row = con.execute(
                f"SELECT {_INSTALL_COLUMNS} FROM slack_installs WHERE id = ?", (install_id,)
            ).fetchone()
        return _install(row) if row and row[2] in ACCEPTING else None

    def remove_slack_install(self, install_id: str) -> SlackInstall:
        """Remove the install (D10; see the module). KeyError, PermissionError."""
        with self._write() as con:
            self._require_manager(con)
            row = self._slack_row(con, install_id)
            if row[2] == "removed":
                return _install(row)
            notes = self._remove(con, install_id)
            self._slack_audit(con, "slack.install_removed", install_id, notes=notes)
            return _install(self._slack_row(con, install_id))

    def _remove(self, con: sqlite3.Connection, install_id: str) -> int:
        """Removal (D10), in the caller's write: the secrets, links and offers deleted, and
        every note of the install not due yet due FORGET_AFTER from now. How many notes."""
        stamp = self._instant()
        con.execute(
            "UPDATE slack_installs SET state = 'removed', signing_secret = NULL, bot_token = NULL,"
            " removed_at = ?, checked_at = NULL, dead_checks = 0, bot_gone_at = NULL WHERE id = ?",
            (_iso(stamp), install_id),
        )
        con.execute("DELETE FROM slack_people WHERE install_id = ?", (install_id,))
        con.execute("DELETE FROM slack_link_offers WHERE install_id = ?", (install_id,))
        # a message tied to a note the project held already: the tie goes, the note is not Slack's
        con.execute("DELETE FROM slack_notes WHERE install_id = ? AND owns = 0", (install_id,))
        return con.execute(
            "UPDATE slack_notes SET forget_at = ? WHERE install_id = ? AND forget_at IS NULL",
            (_iso(stamp + FORGET_AFTER), install_id),
        ).rowcount

    def revoke_slack_bot(self, install_id: str) -> SlackInstall:
        """Slack revoked the bot's token: the install is off and its bot token deleted; the
        signing secret stays, so what Slack sends next (app_uninstalled) is still verified.
        Nothing is due. A removed install stays as it is. KeyError, PermissionError."""
        with self._write() as con:
            self._require_manager(con)
            row = self._slack_row(con, install_id)
            if row[2] == "removed":
                return _install(row)
            self._revoke(con, install_id)
            self._slack_audit(con, "slack.bot_revoked", install_id)
            return _install(self._slack_row(con, install_id))

    def _revoke(self, con: sqlite3.Connection, install_id: str) -> None:
        con.execute(
            "UPDATE slack_installs SET state = 'off', bot_token = NULL,"
            " bot_gone_at = COALESCE(bot_gone_at, ?) WHERE id = ?",
            (self._stamp(), install_id),
        )

    def slack_bot_token(self, install_id: str) -> str | None:
        """The install's bot token opened, for a call to Slack; None when it holds none or
        it does not open under this key. KeyError: no such install here; NoSlackKey."""
        sealer = self._slack_sealer()
        with self._read() as con:
            self._check_slack_view(con)
            self._slack_row(con, install_id)
            (sealed,) = con.execute(
                "SELECT bot_token FROM slack_installs WHERE id = ?", (install_id,)
            ).fetchone()
        if sealed is None:
            return None
        return sealer.open(sealed, _associated("bot_token", install_id, self._account_id))

    def slack_inbound(self, install_id: str) -> SlackInbound | None:
        """The server's, for a request to one of the three addresses: the install named by
        `install_id` in any workspace and its signing secret, in one statement; None for an id
        of another shape or no install. Without a key, the secret is None."""
        if not isinstance(install_id, str) or not INSTALL_ID.fullmatch(install_id):
            return None
        with self._read() as con:
            row = con.execute(
                f"SELECT {_INSTALL_COLUMNS}, signing_secret FROM slack_installs WHERE id = ?",
                (install_id,),
            ).fetchone()
        if row is None:
            return None
        install = _install(row[:14])
        sealed = row[14]
        sealer = self.slack_sealer
        if sealed is None or sealer is None:
            return SlackInbound(install, None)
        associated = _associated("signing_secret", install.id, install.account_id)
        secret = sealer.open(sealed, associated)
        generation = (
            SlackGeneration(install.id, install.account_id, install.made_at, secret, sealer)
            if secret is not None
            else None
        )
        return SlackInbound(install, secret, generation)

    # Notes, and forgetting them (D10)

    def record_slack_note(
        self,
        install_id: str,
        note_id: str,
        *,
        how: str,
        brought_by: str | None,
        conversation_id: str | None = None,
        message_time: str | None = None,
        con: sqlite3.Connection | None = None,
        team_id: str | None = None,
        owns: bool = True,
    ) -> str:
        """Record that the install created the note `note_id`: `how` one of HOWS, a filed
        message with its conversation and time (D9), a typed note with neither. A filed
        message's claim gets its note (a claim swept meanwhile is made again). The note
        recorded: for a message recorded already, the note it already is (nothing written).
        An install removed meanwhile: the note is due when its other notes are. KeyError: no
        such install or note here; ValueError otherwise. `con`: the connection of a write
        already open (Store.add_note's `within`), so the record is in the note's transaction;
        else it is a write of its own. `owns` False, for a filed message alone: the project held
        the note already, so the row only ties the message to it (D9: a message is filed once)
        and the note is never due to be forgotten. `team_id`: the Slack team the request was
        verified for; when the install holds another by now, TeamChanged and nothing is written (an
        install removed meanwhile, or set up again with no team chosen yet, still holds
        it)."""
        _recordable(how, conversation_id, message_time)
        if not owns and how != "filed":
            raise ValueError("only a filed message is tied to a note it did not create")
        if con is not None:
            return self._record(con, install_id, note_id, how, brought_by, conversation_id,
                                message_time, team_id, owns)  # fmt: skip
        with self._write() as opened:
            return self._record(opened, install_id, note_id, how, brought_by, conversation_id,
                                message_time, team_id, owns)  # fmt: skip

    def _record(
        self,
        con: sqlite3.Connection,
        install_id: str,
        note_id: str,
        how: str,
        brought_by: str | None,
        conversation_id: str | None,
        message_time: str | None,
        team_id: str | None = None,
        owns: bool = True,
    ) -> str:
        row = self._slack_row(con, install_id)
        if team_id is not None and row[3] != team_id:
            raise TeamChanged()
        if how == "filed":
            found = con.execute(
                "SELECT note_id FROM slack_notes WHERE install_id = ? AND conversation_id = ?"
                " AND message_time = ?",
                (install_id, conversation_id, message_time),
            ).fetchone()
            if found is not None and found[0] is not None:
                return found[0]
        held = con.execute(
            "SELECT 1 FROM notes JOIN projects ON projects.id = notes.project_id"
            " WHERE notes.id = ? AND projects.account_id = ?",
            (note_id, self._account_id),
        ).fetchone()
        if held is None:
            raise KeyError(note_id)
        # removed, or set up again with no team chosen yet: the removal's day, as its other notes
        gone = row[2] in ("removed", "pending") and row[10] is not None
        due = _iso(_when(row[10]) + FORGET_AFTER) if gone and owns else None
        if how == "filed":  # the claim, made again when it was swept
            con.execute(
                "INSERT INTO slack_notes (install_id, how, team_id, conversation_id,"
                " message_time, brought_by, brought_at) VALUES (?, 'filed', ?, ?, ?, ?, ?)"
                " ON CONFLICT (install_id, conversation_id, message_time) DO NOTHING",
                (install_id, row[3], conversation_id, message_time, brought_by,
                 self._stamp()),
            )  # fmt: skip
            con.execute(
                # a row that owns its note keeps the day a removal gave its claim; a tie has none
                "UPDATE slack_notes SET note_id = ?,"
                " forget_at = CASE WHEN ? THEN COALESCE(forget_at, ?) ELSE NULL END, owns = ?"
                " WHERE install_id = ? AND conversation_id = ? AND message_time = ?"
                " AND note_id IS NULL AND NOT EXISTS"
                " (SELECT 1 FROM slack_notes WHERE note_id = ?)",
                (
                    note_id,
                    int(owns),
                    due,
                    int(owns),
                    install_id,
                    conversation_id,
                    message_time,
                    note_id,
                ),
            )
            return note_id
        con.execute(
            "INSERT INTO slack_notes (note_id, install_id, how, team_id, brought_by,"
            " brought_at, forget_at) VALUES (?, ?, 'typed', ?, ?, ?, ?)"
            " ON CONFLICT (note_id) DO NOTHING",
            (note_id, install_id, row[3], brought_by, self._stamp(), due),
        )
        return note_id

    def claim_slack_message(
        self,
        install_id: str,
        conversation_id: str,
        message_time: str,
        *,
        brought_by: str | None,
        team_id: str | None = None,
    ) -> SlackClaim:
        """Claim a message before anything is asked or written for it (D9): CLAIMED, a row
        with no note yet; FILED, with the note it is; FILING, claimed by someone else and not
        stored yet. A claim with no note older than CLAIM_FOR is taken over. `team_id`: the
        Slack team the request was verified for; TeamChanged, and nothing written, when the
        install holds another by now. KeyError: no such
        install here; ValueError: a conversation or a time not as Slack writes them."""
        _message(conversation_id, message_time)
        with self._write() as con:
            team = self._slack_row(con, install_id)[3]
            if team_id is not None and team != team_id:  # the request's team, replaced since
                raise TeamChanged()
            key = (install_id, conversation_id, message_time)
            con.execute(
                "DELETE FROM slack_notes WHERE install_id = ? AND conversation_id = ?"
                " AND message_time = ? AND note_id IS NULL AND brought_at <= ?",
                (*key, _iso(self._instant() - CLAIM_FOR)),
            )
            made = con.execute(
                "INSERT INTO slack_notes (install_id, how, team_id, conversation_id,"
                " message_time, brought_by, brought_at) VALUES (?, 'filed', ?, ?, ?, ?, ?)"
                " ON CONFLICT (install_id, conversation_id, message_time) DO NOTHING",
                (install_id, team, conversation_id, message_time, brought_by, self._stamp()),
            ).rowcount
            if made:
                return SlackClaim(CLAIMED, None)
            (note,) = con.execute(
                "SELECT note_id FROM slack_notes WHERE install_id = ? AND conversation_id = ?"
                " AND message_time = ?",
                key,
            ).fetchone()
            return SlackClaim(FILED, note) if note is not None else SlackClaim(FILING, None)

    def release_slack_claim(self, install_id: str, conversation_id: str, message_time: str) -> bool:
        """Undo a claim whose message was not stored, so it can be filed again; a claim that
        has its note stays. Whether one was released."""
        with self._write() as con:
            self._slack_row(con, install_id)
            return bool(
                con.execute(
                    "DELETE FROM slack_notes WHERE install_id = ? AND conversation_id = ?"
                    " AND message_time = ? AND note_id IS NULL",
                    (install_id, conversation_id, message_time),
                ).rowcount
            )

    def slack_note_for(
        self, install_id: str, conversation_id: str, message_time: str
    ) -> str | None:
        """The note a Slack message already is (D9), or None (a claim has none yet)."""
        with self._read() as con:
            self._slack_row(con, install_id)
            found = con.execute(
                "SELECT note_id FROM slack_notes WHERE install_id = ? AND conversation_id = ?"
                " AND message_time = ?",
                (install_id, conversation_id, message_time),
            ).fetchone()
        return found[0] if found else None

    def slack_notes_to_forget(self) -> SlackDue | None:
        """How many notes of the workspace's removed install are due to be forgotten, and
        when the first is; None when none is."""
        with self._read() as con:
            found = con.execute(
                "SELECT slack_notes.install_id, COUNT(*), MIN(forget_at) FROM slack_notes"
                " JOIN slack_installs ON slack_installs.id = slack_notes.install_id"
                " WHERE slack_installs.account_id = ? AND forget_at IS NOT NULL"
                " AND note_id IS NOT NULL AND owns = 1 GROUP BY slack_notes.install_id",
                (self._account_id,),
            ).fetchone()
        return SlackDue(found[0], found[1], _when(found[2])) if found else None

    def slack_notes_due(self, limit: int = DUE_AT_ONCE) -> list[tuple[str, str]]:
        """The server's: (workspace, note) of each note whose time to be forgotten came, the
        oldest first, at most `limit`."""
        return [(note.account_id, note.note_id) for note in self._slack_due_notes(limit=limit)]

    def _slack_due_notes(
        self, *, limit: int = DUE_AT_ONCE, after: tuple[str, str] | None = None
    ) -> list[SlackForgetting]:
        """One page of eligible notes, ordered by deadline then note id."""
        position = " AND (n.forget_at, n.note_id) > (?, ?)" if after is not None else ""
        with self._read() as con:
            found = con.execute(
                "SELECT i.account_id, n.note_id, n.install_id, i.made_at, n.forget_at"
                " FROM slack_notes AS n JOIN slack_installs AS i ON i.id = n.install_id"
                " WHERE n.forget_at <= ? AND n.note_id IS NOT NULL AND n.owns = 1"
                f"{position} ORDER BY n.forget_at, n.note_id LIMIT ?",
                (self._stamp(), *(after or ()), limit),
            ).fetchall()
        return [SlackForgetting(*row) for row in found]

    def _require_slack_forget_owner(self, con: sqlite3.Connection) -> None:
        if self._user_id is not None and self._role_in(con) != "owner":
            raise PermissionError(NOT_AN_OWNER)

    def _forget_slack_note(self, note: SlackForgetting, *, now: bool = False) -> bool:
        """Forget only while Slack still owns the note under the selected deadline."""

        def still_due(con: sqlite3.Connection) -> None:
            params: tuple[str, ...] = (
                note.note_id,
                note.install_id,
                self._account_id,
                note.made_at,
                note.forget_at,
            )
            if now:
                self._require_slack_forget_owner(con)
                condition = "i.state = 'removed'"
            else:
                condition = "n.forget_at <= ?"
                params += (self._stamp(),)
            row = con.execute(
                "SELECT 1 FROM slack_notes AS n JOIN slack_installs AS i ON i.id = n.install_id"
                " WHERE n.note_id = ? AND n.install_id = ? AND i.account_id = ?"
                f" AND i.made_at = ? AND n.forget_at = ? AND n.owns = 1 AND {condition}",
                params,
            ).fetchone()
            if row is None:
                raise _ForgettingCancelled()

        try:
            self._slack_checked_view(still_due).forget_note(note.note_id)
        except (_ForgettingCancelled, KeyError):  # a cancelled deadline or a note gone already
            return False
        return True

    def forget_slack_notes_now(self, install_id: str) -> int:
        """ "Forget them now": every note of the removed install that is due to be forgotten,
        forgotten at once by this view (Store.forget_note), whatever its day. How many.
        KeyError; ValueError (the install is not removed); PermissionError for anyone but an
        owner or a view acting for no person, who may forget any note of the workspace."""
        with self._read() as con:
            self._require_slack_forget_owner(con)
            install = _install(self._slack_row(con, install_id))
            if install.state != "removed":
                raise ValueError("only a removed install's notes are forgotten")
            notes = [
                SlackForgetting(self._account_id, note_id, install_id, install.made_at, deadline)
                for note_id, deadline in con.execute(
                    "SELECT note_id, forget_at FROM slack_notes WHERE install_id = ?"
                    " AND forget_at IS NOT NULL AND note_id IS NOT NULL AND owns = 1"
                    " ORDER BY note_id",
                    (install_id,),
                )
            ]
        return sum(self._forget_slack_note(note, now=True) for note in notes)

    # The bot token checked with Slack, and an install whose token Slack ended

    def take_slack_checks(self, limit: int = CHECKS_AT_ONCE) -> list[SlackCheck]:
        """The server's: the installs that hold a bot token and were not checked within
        CHECK_EVERY, the longest unchecked first, at most `limit`, each with its token opened
        and each stamped as checked now, whatever its check comes to (a token that does not
        open under this key is stamped and left out). NoSlackKey."""
        sealer = self._slack_sealer()
        with self._write() as con:
            found = con.execute(
                "SELECT id, account_id, bot_token FROM slack_installs WHERE bot_token IS NOT NULL"
                " AND state != 'removed' AND (checked_at IS NULL OR checked_at <= ?)"
                " ORDER BY checked_at IS NOT NULL, checked_at, id LIMIT ?",
                (_iso(self._instant() - CHECK_EVERY), limit),
            ).fetchall()
            con.executemany(
                "UPDATE slack_installs SET checked_at = ? WHERE id = ?",
                [(self._stamp(), install_id) for install_id, _, _ in found],
            )
        checks = []
        for install_id, account_id, sealed in found:
            token = sealer.open(sealed, _associated("bot_token", install_id, account_id))
            if token is not None:
                checks.append(SlackCheck(install_id, account_id, token, sealed))
        return checks

    def slack_token_checked(self, check: SlackCheck, answer: str) -> SlackInstall | None:
        """The server's: what Slack said of the token `check` holds (ANSWERS). "works": the
        count of ended answers starts again; "unclear" changes nothing; "ended": counted, and
        the second in a row does what revoke_slack_bot does (bot_gone_at set). An install
        whose token was replaced or deleted since changes nothing. The install as it is now;
        None when it is gone."""
        if answer not in ANSWERS:
            raise ValueError(f"a check comes to {', '.join(ANSWERS)}")
        with self._write() as con:
            same = "id = ? AND bot_token = ?", (check.install_id, check.sealed)
            if answer == "works":
                con.execute(f"UPDATE slack_installs SET dead_checks = 0 WHERE {same[0]}", same[1])
            elif answer == "ended":
                con.execute(
                    f"UPDATE slack_installs SET dead_checks = dead_checks + 1 WHERE {same[0]}",
                    same[1],
                )
                ended = con.execute(
                    f"SELECT 1 FROM slack_installs WHERE {same[0]} AND dead_checks >= 2", same[1]
                ).fetchone()
                if ended:
                    self._revoke(con, check.install_id)
                    self._audit(
                        con, "slack.bot_revoked", account_id=check.account_id,
                        detail={"install": check.install_id, "why": "bot_token_ended"},
                    )  # fmt: skip
            row = con.execute(
                f"SELECT {_INSTALL_COLUMNS} FROM slack_installs WHERE id = ?", (check.install_id,)
            ).fetchone()
        return _install(row) if row else None

    def remove_gone_slack_installs(self) -> list[str]:
        """The server's: every install that has held no bot token for more than GONE_AFTER
        since Slack ended it (bot_gone_at) removed as an owner's removal does it (D10): its
        secrets and links deleted at once, its notes due FORGET_AFTER later. The audit record
        names no person and says why. The installs removed."""
        with self._write() as con:
            found = con.execute(
                "SELECT id, account_id FROM slack_installs WHERE bot_gone_at IS NOT NULL"
                " AND bot_gone_at < ? AND bot_token IS NULL AND state != 'removed' ORDER BY id",
                (_iso(self._instant() - GONE_AFTER),),
            ).fetchall()
            for install_id, account_id in found:
                notes = self._remove(con, install_id)
                self._audit(
                    con, "slack.install_removed", account_id=account_id, actor=("system", None),
                    detail={"install": install_id, "notes": notes, "why": "bot_token_ended"},
                )  # fmt: skip
        return [install_id for install_id, _ in found]

    # Links and offers (D5)

    def link_slack_user(self, install_id: str, slack_user_id: str, user_id: str) -> SlackPerson:
        """Link the install's Slack user to the person `user_id`, a member of the workspace
        (KeyError otherwise). A Slack user linked to that person already stays as they are,
        with their project; one linked to another person is ValueError, and that link stands.
        Every offer of the Slack user at the install is deleted."""
        _slack_id(slack_user_id, "user")
        with self._write() as con:
            self._slack_row(con, install_id)
            self._link(con, install_id, slack_user_id, user_id)
            return self._person(con, install_id, slack_user_id)

    def _link(
        self, con: sqlite3.Connection, install_id: str, slack_user: str, user_id: str
    ) -> None:
        member = con.execute(
            "SELECT 1 FROM memberships WHERE account_id = ? AND user_id = ?",
            (self._account_id, user_id),
        ).fetchone()
        if member is None:
            raise KeyError(MEMBERS_ONLY)
        linked = con.execute(
            "SELECT user_id FROM slack_people WHERE install_id = ? AND slack_user_id = ?",
            (install_id, slack_user),
        ).fetchone()
        if linked is not None and linked[0] != user_id:
            raise ValueError(LINKED_ELSEWHERE)
        if linked is None:
            con.execute(
                "INSERT INTO slack_people (install_id, slack_user_id, user_id, linked_at)"
                " VALUES (?, ?, ?, ?)",
                (install_id, slack_user, user_id, self._stamp()),
            )
            self._slack_audit(con, "slack.linked", install_id, person=user_id)
        con.execute(
            "DELETE FROM slack_link_offers WHERE install_id = ? AND slack_user_id = ?",
            (install_id, slack_user),
        )

    def unlink_slack_user(self, install_id: str, slack_user_id: str) -> bool:
        """Undo a link; whether there was one."""
        with self._write() as con:
            self._slack_row(con, install_id)
            gone = con.execute(
                "DELETE FROM slack_people WHERE install_id = ? AND slack_user_id = ?",
                (install_id, slack_user_id),
            ).rowcount
            if gone:
                self._slack_audit(con, "slack.unlinked", install_id)
            return bool(gone)

    def slack_person(self, install_id: str, slack_user_id: str) -> SlackPerson | None:
        """The person the install's Slack user is linked to, with their project; None."""
        with self._read() as con:
            self._check_slack_view(con)
            self._slack_row(con, install_id)
            return self._person(con, install_id, slack_user_id)

    def _person(self, con: sqlite3.Connection, install_id: str, slack_user: str):
        row = con.execute(
            "SELECT install_id, slack_user_id, user_id, project_id, linked_at FROM slack_people"
            " WHERE install_id = ? AND slack_user_id = ?",
            (install_id, slack_user),
        ).fetchone()
        return SlackPerson(*row) if row else None

    def own_slack_links(self) -> list[SlackPerson]:
        """The view's own person's links: each Slack user of the workspace's installs linked
        to them, by Slack user id; [] for a view acting for no person. One statement."""
        if self._user_id is None:
            return []
        with self._read() as con:
            found = con.execute(
                "SELECT p.install_id, p.slack_user_id, p.user_id, p.project_id, p.linked_at"
                " FROM slack_people AS p JOIN slack_installs AS i ON i.id = p.install_id"
                " WHERE i.account_id = ? AND p.user_id = ? ORDER BY p.slack_user_id",
                (self._account_id, self._user_id),
            ).fetchall()
        return [SlackPerson(*row) for row in found]

    def slack_team_held(self, team_id: str, account_id: str) -> bool:
        """The server's: whether an install of a workspace other than `account_id`, ready,
        on or off (as the index slack_installs_team counts them), is for the Slack team
        `team_id`. One statement, whatever the number of workspaces."""
        with self._read() as con:
            return (
                con.execute(
                    "SELECT 1 FROM slack_installs WHERE team_id = ? AND account_id != ?"
                    f" AND state IN {_HOLDS_TEAM} LIMIT 1",
                    (team_id, account_id),
                ).fetchone()
                is not None
            )

    def set_slack_project(
        self, install_id: str, slack_user_id: str, project_id: str | None
    ) -> None:
        """The project a linked Slack user's notes go to, or None. KeyError: not linked, or
        not a project of the workspace. Whether the person may write there is the caller's
        question, asked when a note is written."""
        with self._write() as con:
            self._slack_row(con, install_id)
            if (
                project_id is not None
                and not con.execute(
                    "SELECT 1 FROM projects WHERE id = ? AND account_id = ?",
                    (project_id, self._account_id),
                ).fetchone()
            ):
                raise KeyError(project_id)
            changed = con.execute(
                "UPDATE slack_people SET project_id = ? WHERE install_id = ? AND slack_user_id = ?",
                (project_id, install_id, slack_user_id),
            ).rowcount
            if not changed:
                raise KeyError(slack_user_id)

    def offer_slack_link(self, install_id: str, slack_user_id: str) -> str:
        """A one-time value for the Slack user to open signed in, within OFFER_TTL. It
        replaces the Slack user's earlier offer at the install, claimed or not. Only its
        digest is kept."""
        _slack_id(slack_user_id, "user")
        value = secrets.token_urlsafe(32)
        with self._write() as con:
            self._slack_row(con, install_id)
            con.execute(
                "INSERT OR REPLACE INTO slack_link_offers"
                " (digest, install_id, slack_user_id, expires_at) VALUES (?, ?, ?, ?)",
                (_digest(value), install_id, slack_user_id, _iso(self._instant() + OFFER_TTL)),
            )
        return value

    def slack_offer(self, value: str) -> SlackOffer | None:
        """The offer `value` for an install of this workspace, within its time, that nobody
        said yes to yet; None."""
        if not isinstance(value, str) or not value:
            return None
        with self._read() as con:
            row = self._offer(con, value)
        return SlackOffer(row[0], row[1], _when(row[2])) if row else None

    def _offer(self, con: sqlite3.Connection, value: str) -> tuple | None:
        return con.execute(
            "SELECT o.install_id, o.slack_user_id, o.expires_at FROM slack_link_offers AS o"
            " JOIN slack_installs AS i ON i.id = o.install_id"
            " WHERE o.digest = ? AND i.account_id = ? AND o.user_id IS NULL"
            " AND o.expires_at > ? AND i.state != 'removed'",
            (_digest(value), self._account_id, self._stamp()),
        ).fetchone()

    def claim_slack_offer(self, value: str) -> str:
        """The yes: the offer `value` tied to the view's person, a member of the install's
        workspace, and the code they are shown (XXXX-XXXX), kept only as a digest. KeyError:
        no such offer, run out, or someone said yes to it already; PermissionError: a view
        acting for no person, or a person who is not a member."""
        if self._user_id is None:
            raise PermissionError(MEMBERS_ONLY)
        code = _new_code()
        with self._write() as con:  # refuses a person who is not a member (PermissionError)
            row = self._offer(con, value) if isinstance(value, str) and value else None
            claimed = (
                row is not None
                and con.execute(
                    "UPDATE slack_link_offers SET user_id = ?, code_digest = ?"
                    " WHERE digest = ? AND user_id IS NULL AND expires_at > ?",
                    (self._user_id, _code_digest(row[0], code), _digest(value), self._stamp()),
                ).rowcount
            )
            if not claimed:
                raise KeyError(NOT_OPEN)
        return _shown(code)

    def take_slack_offer(self, install_id: str, slack_user_id: str, code: str) -> SlackPerson:
        """The last step, `/cairn link <code>` (the workspace's view): the Slack user's own
        offer, said yes to and within its time, whose code this is (digests compared in
        constant time), links them to the person who said yes, and every offer of theirs at
        the install goes. Otherwise KeyError (NO_LINK, whatever the reason) after: a try
        counted on the Slack user's offer, which the TRIES-th deletes; any other Slack
        user's offer whose code this is deleted; an offer whose person is no longer a member
        deleted. A fixed number of statements, whatever the workspace holds."""
        _slack_id(slack_user_id, "user")
        typed = _typed(code)
        given = _code_digest(install_id, typed) if typed else ""
        with self._write() as con:
            self._slack_row(con, install_id)
            row = con.execute(
                "SELECT user_id, code_digest, expires_at FROM slack_link_offers"
                " WHERE install_id = ? AND slack_user_id = ?",
                (install_id, slack_user_id),
            ).fetchone()
            matches = (
                row is not None
                and row[1] is not None
                and bool(given)
                and hmac.compare_digest(row[1], given)
                and row[2] > self._stamp()
            )
            if matches:
                member = con.execute(
                    "SELECT 1 FROM memberships WHERE account_id = ? AND user_id = ?",
                    (self._account_id, row[0]),
                ).fetchone()
                if member is not None:
                    self._link(con, install_id, slack_user_id, row[0])
                    return self._person(con, install_id, slack_user_id)
                con.execute(
                    "DELETE FROM slack_link_offers WHERE install_id = ? AND slack_user_id = ?",
                    (install_id, slack_user_id),
                )
            else:
                con.execute(  # a code that reached another Slack user is dead
                    "DELETE FROM slack_link_offers WHERE install_id = ? AND code_digest = ?"
                    " AND slack_user_id != ?",
                    (install_id, given, slack_user_id),
                )
                con.execute(
                    "UPDATE slack_link_offers SET tries = tries + 1"
                    " WHERE install_id = ? AND slack_user_id = ?",
                    (install_id, slack_user_id),
                )
                con.execute(
                    "DELETE FROM slack_link_offers WHERE install_id = ? AND slack_user_id = ?"
                    " AND tries >= ?",
                    (install_id, slack_user_id, TRIES),
                )
        raise KeyError(NO_LINK)

    # Events, and the sweep

    def slack_event_seen(self, install_id: str, event_id: str) -> bool:
        """The server's: True the first time the install sees `event_id` (it is recorded),
        False when it was seen within SEEN_FOR."""
        with self._write() as con:
            return bool(
                con.execute(
                    "INSERT INTO slack_events (install_id, event_id, seen_at) VALUES (?, ?, ?)"
                    " ON CONFLICT DO NOTHING",
                    (install_id, event_id, self._stamp()),
                ).rowcount
            )

    def forget_slack_event(self, install_id: str, event_id: str) -> None:
        """The server's: the install no longer has seen `event_id` (its handler failed), so
        Slack's next delivery of it is the first time again. One never seen: nothing."""
        with self._write() as con:
            con.execute(
                "DELETE FROM slack_events WHERE install_id = ? AND event_id = ?",
                (install_id, event_id),
            )

    def sweep_slack(self) -> int:
        """The server's: remove the offers past their time and the event ids seen SEEN_FOR
        ago or more, in every workspace, in one write. How many rows."""
        with self._write() as con:
            return self._sweep_slack(con)

    def _sweep_slack(self, con: sqlite3.Connection) -> int:
        now = self._instant()
        offers = con.execute(
            "DELETE FROM slack_link_offers WHERE expires_at <= ?", (_iso(now),)
        ).rowcount
        events = con.execute(
            "DELETE FROM slack_events WHERE seen_at <= ?", (_iso(now - SEEN_FOR),)
        ).rowcount
        claims = con.execute(  # a filed message claimed and never stored
            "DELETE FROM slack_notes WHERE note_id IS NULL AND brought_at <= ?",
            (_iso(now - CLAIM_FOR),),
        ).rowcount
        return offers + events + claims
