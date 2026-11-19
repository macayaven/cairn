"""The rate limit of a workspace's requests (H6), in this process's memory: nothing of it is
written, so counting a read never writes.

A sliding window of WINDOW seconds, counted by whole second: each workspace keeps, oldest
first, the seconds in the window in which it made requests and how many in each, so it holds
at most WINDOW entries whatever its plan allows. A request goes ahead when fewer than the
plan's `requests_per_minute` were made in the window and is then counted; one refused is not
counted, so the wait it is told is exact. At most TRACKED workspaces are kept: when one more
comes, a workspace not at its limit (the limit it was last asked with) is dropped, the least
recently seen of those, and only when every one is at its limit the least recently seen, so
a workspace being refused is not forgotten while another may go; memory stays bounded by
TRACKED x WINDOW small entries, and a new workspace is never refused for want of room (it
does not fail closed). A workspace dropped starts again from nothing. Choosing walks no
table: each workspace counted tells a cairn.holding.Holding when it stops being at its limit,
so the one to drop is found in a constant number of steps, amortized, whatever TRACKED is
(Security 4). A second is the unit: a request counted at
0.9 s leaves the window at 60 s. The time is the app's clock, never earlier than the last
second a workspace was counted in, so a clock that steps back cannot grow it.

These counts, and CredentialFailures', live in this process's memory: they start again when
the server restarts. A limit that must outlast a restart is a reverse proxy's or the
cloud's, in front of the server.

The limit on refused credentials per client address (H1) is CredentialFailures, below.
"""

import math
import threading
from collections import deque
from collections.abc import Callable
from datetime import datetime

from cairn.holding import Holding, refusing_until
from cairn.oauth import _network as network  # the sign-in limit's rule for an address

WINDOW = 60  # seconds
TRACKED = 10_000  # workspaces whose recent requests are kept


class RateLimiter:
    def __init__(
        self, clock: Callable[[], datetime], *, window: int = WINDOW, tracked: int = TRACKED
    ) -> None:
        if tracked < 1:
            raise ValueError("a rate limiter tracks at least one workspace")
        self._clock, self._window, self._tracked = clock, window, tracked
        self._seen: dict[str, deque[list[int]]] = {}
        self._holding: Holding[str] = Holding()  # which workspace to drop (Security 4)
        self._lock = threading.Lock()

    def take(self, key: str, limit: int) -> int:
        """0 when `key` (a workspace) may make one more request with `limit` a window, which
        is then counted; else the whole seconds, at least 1, until it may."""
        second = math.floor(self._clock().timestamp())
        with self._lock:
            seen = self._seen.get(key)
            if seen is None:
                while len(self._seen) >= self._tracked:  # room for one more
                    victim = self._holding.victim(second)
                    del self._seen[victim]
                    self._holding.drop(victim)
                seen = self._seen[key] = deque()
            if seen:
                second = max(second, seen[-1][0])
            while seen and seen[0][0] <= second - self._window:
                seen.popleft()
            made = sum(count for _, count in seen)
            if made < limit:
                if seen and seen[-1][0] == second:
                    seen[-1][1] += 1
                else:
                    seen.append([second, 1])
                self._holding.seen(key, self._free_at(seen, made + 1, limit))
                return 0
            free = self._free_at(seen, made, limit)
            self._holding.seen(key, free)  # now the most recent
            return max(1, int(free - second)) if free != math.inf else self._window

    def _free_at(self, seen: deque[list[int]], made: int, limit: int) -> float | None:
        """The second at which a workspace that made `made` requests in the window `seen`
        (oldest first) is no longer at `limit`: when enough of them have left the window;
        None when it is not at its limit, math.inf for a limit below 1."""
        if limit < 1:
            return math.inf
        if made < limit:
            return None
        over = made - limit + 1  # requests that must leave the window first
        for at, count in seen:
            over -= count
            if over <= 0:
                return at + self._window
        return math.inf  # not reached: `over` is at most what the window holds

    def entries(self, key: str) -> int:
        """How many seconds of `key`'s requests are kept."""
        with self._lock:
            return len(self._seen.get(key, ()))

    def tracked(self) -> int:
        """How many workspaces' requests are kept."""
        with self._lock:
            return len(self._seen)


# H1: refused credentials, counted per client address (CredentialFailures).
FAILURES = 30  # refused credentials one client address may present in any FAILURE_WINDOW
FAILURE_WINDOW = 60  # seconds
FAILURE_ADDRESSES = 10_000  # client addresses whose recent failures are kept
# What a client address over FAILURES is answered, on /api and /mcp (JSON) and by POST /login.
TOO_MANY_FAILURES = (
    "Too many wrong keys or tokens came from your address in the last minute. Wait a minute"
    " and try again."
)


class CredentialFailures:
    """The credentials one client address presented and cairn refused (H1), in this
    process's memory, so a client cannot try key after key: after `limit` refusals in any
    `window` seconds, the address is blocked until the oldest of them leaves the window.
    Auth on /api and /mcp, and POST /login, look the credential up first, as always: a
    correct one is admitted from any address, blocked or not, and never counted, so one
    client that keeps presenting a wrong key (a script retrying a revoked one) locks out
    nobody else behind its address, whether or not the server has seen their credentials
    before. Only a refused credential asks `blocked`: from a blocked address it is answered
    429 with Retry-After and TOO_MANY_FAILURES in place of 401. A refused credential is
    counted (`failed`); a request that presents none and a request answered 429 are not, so
    the address is free again once the window has passed. Nothing of a credential is kept.

    The address is the client's as the sign-in limit reads it (cairn.oauth.RateLimit's
    rule): the peer, or what a trusted proxy says (`serve --trusted-proxy`); one IPv6 /64 is
    one address, and requests without a usable address share one count. At most `addresses`
    addresses are kept: when the table is full, the address forgotten is one not blocked, the
    one seen longest ago of those; a blocked address is forgotten only when every address is,
    and then the one seen longest ago. A new one is never refused, and a forgotten address
    starts again from nothing. Choosing walks no table (cairn.holding.Holding): a constant
    number of steps, amortized, whatever `addresses` is. The time is
    `clock()` in seconds (the app's clock), never earlier than the last failure counted, so a
    clock that steps back frees no address. Thread-safe."""

    def __init__(
        self,
        clock: Callable[[], float],
        *,
        limit: int = FAILURES,
        window: float = FAILURE_WINDOW,
        addresses: int = FAILURE_ADDRESSES,
    ) -> None:
        if limit < 1 or not window > 0 or addresses < 1:
            raise ValueError("a failure limit needs a limit, a window and room of at least 1")
        self._clock, self._limit, self._window = clock, limit, window
        self.addresses = addresses
        self._failures: dict[str, deque[float]] = {}
        self._holding: Holding[str] = Holding()  # which address to forget (Security 4)
        self._lock = threading.Lock()

    def _now(self, failures: deque[float] | None) -> float:
        now = self._clock()
        return max(now, failures[-1]) if failures else now

    def blocked(self, address: str | None) -> int:
        """0 when `address` may present a credential; else the whole seconds, at least 1,
        until it may again. Counts nothing."""
        key = network(address)
        with self._lock:
            failures = self._failures.get(key)
            if not failures:
                return 0
            now = self._now(failures)
            while failures and failures[0] <= now - self._window:
                failures.popleft()
            if len(failures) < self._limit:
                return 0
            oldest = failures[len(failures) - self._limit]
            return max(1, math.ceil(oldest + self._window - now))

    def failed(self, address: str | None) -> None:
        """Count one credential `address` presented and cairn refused."""
        key = network(address)
        with self._lock:
            failures = self._failures.get(key)
            if failures is None:
                while len(self._failures) >= self.addresses:  # room for one more
                    victim = self._holding.victim(self._clock())
                    del self._failures[victim]
                    self._holding.drop(victim)
                failures = self._failures[key] = deque()
            now = self._now(failures)
            while failures and failures[0] <= now - self._window:
                failures.popleft()
            failures.append(now)
            while len(failures) > self._limit:  # older ones no longer decide anything
                failures.popleft()
            self._holding.seen(key, refusing_until(failures, self._limit, self._window))

    def tracked(self) -> int:
        """How many client addresses' failures are kept."""
        with self._lock:
            return len(self._failures)
