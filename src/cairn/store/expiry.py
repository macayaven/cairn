"""The sweep: removes, in every workspace, the rows whose time has passed.

Each of these rows stops working at its time, and reads skip it from then on; the sweep is
what removes it. Without it a row stays until some later write of the same kind removes it
(an OAuth write runs grants._expire, an invitation deletes its workspace's expired ones), and
a session's row stays for good. `serve` runs it every 10 minutes (cairn.web.sweeper)."""

from datetime import timedelta

from cairn.store.base import StoreBase, _iso
from cairn.store.grants import UNUSED_CLIENT_TTL, _expire


class ExpiryMethods(StoreBase):
    def sweep_expired(self) -> int:
        """Remove every row whose time has passed, in every workspace, in one write: sessions
        and invitations at their `expires_at`; what grants._expire removes (codes, access
        tokens, and each grant whose refresh token has expired, with every token it holds);
        rotated refresh tokens remembered past their time (nothing reads one then); and the
        clients registered UNUSED_CLIENT_TTL (7 days) ago or earlier that never got a grant
        and have no code waiting, as register_client removes them. Returns how many rows it
        removed. A row whose time has not come is left as it was; with nothing to remove it
        returns 0 and the database file does not change.

        The time is read once the write lock is held, so a sweep that waited for a request
        judges expiry at the moment it writes. Nothing is audited. The operator's call, like
        compact: only the store that opened the database may run it (PermissionError on any
        view)."""
        if not self._owner:
            raise PermissionError("only the store that opened the database sweeps it")
        with self._write() as con:
            now = self._instant()
            moment = _iso(now)
            before = con.total_changes
            con.execute("DELETE FROM sessions WHERE expires_at <= ?", (moment,))
            con.execute("DELETE FROM invites WHERE expires_at <= ?", (moment,))
            con.execute(
                "DELETE FROM oauth_tokens WHERE kind = 'rotated' AND expires_at <= ?", (moment,)
            )
            _expire(con, now)  # first, so a client whose last code ran out goes below
            con.execute(
                "DELETE FROM oauth_clients WHERE granted_at IS NULL AND created_at <= ?"
                " AND id NOT IN (SELECT client_id FROM oauth_codes)",
                (_iso(now - timedelta(seconds=UNUSED_CLIENT_TTL)),),
            )
            return con.total_changes - before
