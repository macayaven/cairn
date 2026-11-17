"""How long one request of a pull may take: until the pull's deadline, never less than FLOOR.

The HTTP client's timeout is per read, so an answer dripped a byte at a time never times out,
and a deadline checked only before a request starts does not stop one that has started: a
slow provider could hold a pull, and with it every other source's sync, for as long as it
liked. So every request a source sends (and a Google source's sign-in) runs under
`limit(deadline, clock)`, an asyncio timeout of what is left until `deadline` by the
injected `clock`; the source turns its TimeoutError into the outcome a deadline reached
between two requests has (what was finished is returned; nothing finished is a
SourceError). What is left is measured on `clock` once, as the request starts, and then
waited out on the event loop's own clock, so a test that moves `clock` by hand still
decides how much time a request is given.

- `time_left(deadline, clock)`: seconds until `deadline`, at least FLOOR, so a request is
  never started with no time at all (a pull checks that the deadline has not come before it
  starts one); None without a deadline, and then no limit but the client's own.
- `limit(deadline, clock)`: `asyncio.timeout(time_left(deadline, clock))`.
"""

import asyncio
from datetime import datetime

from cairn.sources import Clock

FLOOR = 2.0  # seconds: the least a request is given, well above the event loop's jitter


def time_left(deadline: datetime | None, clock: Clock) -> float | None:
    """Seconds until `deadline` by `clock`, at least FLOOR; None without a deadline."""
    if deadline is None:
        return None
    return max((deadline - clock()).total_seconds(), FLOOR)


def limit(deadline: datetime | None, clock: Clock) -> asyncio.Timeout:
    """An asyncio timeout of what is left until `deadline` (see the module docstring)."""
    return asyncio.timeout(time_left(deadline, clock))
