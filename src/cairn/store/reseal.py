"""Every stored value a sealing key sealed, sealed again under the current key (the `reseal`
command, cairn.web.cli_reseal), so that the earlier keys can be removed.

- `reseal_google`: each connected Google account's refresh token (`credential_tokens.sealed`)
  and each dropped account's token waiting for its revocation (`google_revocations.sealed`),
  under the sealer of CAIRN_GOOGLE_CONNECT_KEY.
- `reseal_slack`: each Slack install's signing secret and bot token (`slack_installs`), under
  the sealer of CAIRN_SLACK_KEY. A value not held (NULL: a pending install, a revoked bot
  token) is not counted.

Each value is opened with the associated data it was sealed with, exactly as the store opens
it (cairn.store.hosted.associated and _waiting_for, cairn.store.slack._associated). One that
the current key opens is left as it is (`current`); one that an earlier key opens is sealed
again under the current key (`resealed`); one that no key given opens is left as it is
(`unopened`). Everything happens in one write transaction, and each row is updated only where
it still holds the value that was read, so a server running beside it loses nothing. Run
twice, the second run re-seals nothing and writes nothing. Counts only: no value, key or
plain text is returned, logged or raised.
"""

from collections.abc import Callable
from dataclasses import dataclass

from cairn.store.base import StoreBase
from cairn.store.hosted import Sealer, _waiting_for, associated
from cairn.store.slack import _associated as _slack_associated


@dataclass(frozen=True)
class Resealed:
    """What one key's pass came to: values sealed again under the current key, values no key
    given opens (left as they were), values the current key sealed already."""

    resealed: int = 0
    unopened: int = 0
    current: int = 0


@dataclass
class _Count:
    resealed: int = 0
    unopened: int = 0
    current: int = 0

    def done(self) -> Resealed:
        return Resealed(self.resealed, self.unopened, self.current)


def _renew(
    sealer: Sealer, sealed: object, tied: bytes, count: _Count, write: Callable[[str], None]
) -> None:
    if sealed is None:
        return
    if sealer.current(sealed, tied):
        count.current += 1
        return
    plain = sealer.open(sealed, tied)
    if plain is None:
        count.unopened += 1
        return
    write(sealer.seal(plain, tied))
    count.resealed += 1


def reseal_google(store: StoreBase, sealer: Sealer) -> Resealed:
    """The Google tokens of every workspace re-sealed under `sealer`'s current key."""
    count = _Count()
    with store._write() as con:
        tokens = con.execute(
            "SELECT account_id, credential, owner, sealed FROM credential_tokens"
        ).fetchall()
        for account_id, credential, owner, sealed in tokens:
            _renew(
                sealer, sealed, associated(account_id, credential, owner), count,
                lambda new, a=account_id, c=credential, old=sealed: con.execute(
                    "UPDATE credential_tokens SET sealed = ?"
                    " WHERE account_id = ? AND credential = ? AND sealed = ?",
                    (new, a, c, old),
                ),
            )  # fmt: skip
        waiting = con.execute("SELECT id, sealed FROM google_revocations").fetchall()
        for revocation, sealed in waiting:
            _renew(
                sealer, sealed, _waiting_for(revocation), count,
                lambda new, r=revocation, old=sealed: con.execute(
                    "UPDATE google_revocations SET sealed = ? WHERE id = ? AND sealed = ?",
                    (new, r, old),
                ),
            )  # fmt: skip
    return count.done()


def reseal_slack(store: StoreBase, sealer: Sealer) -> Resealed:
    """The Slack secrets of every workspace's install re-sealed under `sealer`'s current key."""
    count = _Count()
    with store._write() as con:
        installs = con.execute(
            "SELECT id, account_id, signing_secret, bot_token FROM slack_installs"
        ).fetchall()
        for install, account_id, signing_secret, bot_token in installs:
            for column, sealed in (("signing_secret", signing_secret), ("bot_token", bot_token)):
                _renew(
                    sealer, sealed, _slack_associated(column, install, account_id), count,
                    lambda new, c=column, i=install, old=sealed: con.execute(
                        f"UPDATE slack_installs SET {c} = ? WHERE id = ? AND {c} = ?",
                        (new, i, old),
                    ),
                )  # fmt: skip
    return count.done()
