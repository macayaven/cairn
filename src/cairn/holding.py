"""Which entry a full table of counts drops (review: Security 4), without a walk over the table.

A limiter that keeps a bounded table of counts in memory (cairn.web.ratelimit's RateLimiter and
CredentialFailures, cairn.oauth.RateLimit, cairn.web.slack.SignatureFailures) tells a Holding
each time it touches an entry: `seen(key, until)`, with the time the entry stops refusing
anyone (`until`, in the limiter's own clock; None when it refuses no one now, math.inf when it
refuses until it is touched again). An entry's refusal only ends with time, and only a touch
can start one, so what it was last seen with holds until then.

`victim(now)` is the entry to drop: the one seen longest ago of those not refusing at `now`;
only when every entry refuses, the one seen longest ago. It reads no entry but those it can
take: two heaps hold the entries not refusing, by when they were seen, and those refusing,
by when that ends; an entry whose refusal has ended moves from the second to the first when
a victim is next asked for, once. A heap item left by an entry seen again or dropped is
passed over when it comes up, and when the heaps hold more than twice the table's entries
(and SLACK more) they are made again from the table, so they stay within a constant of it.
The work of a call is a constant number of heap steps, amortized, whatever the table holds:
`steps` counts them (each push, each pop, each item put back when the heaps are made again),
so a test pins the work in steps, not time. Not thread-safe: the limiter's lock holds it."""

import heapq
import math
from collections import OrderedDict

SLACK = 64  # heap items allowed beyond twice the table's entries before they are made again


class Holding[K]:
    def __init__(self) -> None:
        self._order: OrderedDict[K, None] = OrderedDict()  # seen longest ago first
        self._stamp: dict[K, int] = {}  # when each was last seen, as a count of touches
        self._until: dict[K, float | None] = {}  # when each stops refusing; None: it does not
        self._calm: list[tuple[int, K]] = []  # (stamp, key): entries not refusing
        self._held: list[tuple[float, int, K]] = []  # (until, stamp, key): entries refusing
        self._touches = 0
        self.steps = 0

    def __len__(self) -> int:
        return len(self._order)

    def seen(self, key: K, until: float | None) -> None:
        """`key` was touched now, and refuses until `until` (None: it refuses no one)."""
        self._touches += 1
        stamp = self._touches
        self._order.pop(key, None)
        self._order[key] = None
        self._stamp[key], self._until[key] = stamp, until
        if until is None:
            heapq.heappush(self._calm, (stamp, key))  # type: ignore[misc]
        else:
            heapq.heappush(self._held, (until, stamp, key))  # type: ignore[misc]
        self.steps += 1
        if len(self._calm) + len(self._held) > 2 * len(self._order) + SLACK:
            self._remake()

    def drop(self, key: K) -> None:
        """Forget `key`; its heap items are passed over when they come up."""
        self._order.pop(key, None)
        self._stamp.pop(key, None)
        self._until.pop(key, None)

    def victim(self, now: float) -> K:
        """The entry to drop at `now` (the module's docstring). KeyError: the table is empty."""
        while self._held and self._held[0][0] <= now:  # refusals that have ended
            until, stamp, key = heapq.heappop(self._held)
            self.steps += 1
            if self._stamp.get(key) == stamp and self._until.get(key) == until:
                self._until[key] = None
                heapq.heappush(self._calm, (stamp, key))  # type: ignore[misc]
                self.steps += 1
        while self._calm:
            stamp, key = self._calm[0]
            if self._stamp.get(key) == stamp and self._until.get(key) is None:
                return key
            heapq.heappop(self._calm)  # seen again, or dropped, since it was pushed
            self.steps += 1
        if not self._order:
            raise KeyError("an empty table has nothing to drop")
        return next(iter(self._order))  # every entry refuses: the one seen longest ago

    def _remake(self) -> None:
        self._calm = [(self._stamp[k], k) for k, u in self._until.items() if u is None]
        self._held = [(u, self._stamp[k], k) for k, u in self._until.items() if u is not None]
        heapq.heapify(self._calm)
        heapq.heapify(self._held)
        self.steps += len(self._order)


def refusing_until(times, limit: int, window: float) -> float | None:
    """When an entry whose counted times are `times` (oldest first, at most the last `limit`
    of them matter) stops refusing: the `limit`-th newest leaves the window; None when it
    holds fewer than `limit`, and math.inf for a limit below 1, which refuses all."""
    if limit < 1:
        return math.inf
    if len(times) < limit:
        return None
    return times[-limit] + window
