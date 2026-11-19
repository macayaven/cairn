"""The failure counts do not forget the callers they are holding back (review, finding 4).
When a table of counts is full, the entry dropped is one that is not over its limit, the
oldest of those; an entry being refused stays as long as any is not; when every entry is
refused, the oldest goes, so a new caller is never refused for want of room. Each table:
the workspaces' RateLimiter, CredentialFailures, the registration limit (oauth.RateLimit)
and Slack's SignatureFailures. Clocks are moved by hand; every address is made up."""

from datetime import UTC, datetime, timedelta

import pytest

from cairn import oauth
from cairn.web.ratelimit import CredentialFailures, RateLimiter
from cairn.web.slack import SignatureFailures

FABLE, QUILL, BROOK = "sl_0123456789ab", "sl_ba9876543210", "sl_00000000000b"


class Seconds:
    def __init__(self) -> None:
        self.now = 4_000_000_000.0

    def __call__(self) -> float:
        return self.now


class Moment:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 19, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


# CredentialFailures: limit 2 in 60 seconds, room for 3 addresses


def test_credential_failures_keep_a_blocked_address_and_drop_the_oldest_unblocked():
    clock = Seconds()
    limit = CredentialFailures(clock, limit=2, window=60, addresses=3)
    for _ in range(2):
        limit.failed("192.0.2.1")  # blocked, and seen longest ago
    limit.failed("192.0.2.2")
    limit.failed("192.0.2.3")
    limit.failed("192.0.2.4")  # the table is full: 192.0.2.2 goes, not 192.0.2.1
    assert limit.tracked() == 3
    assert limit.blocked("192.0.2.1") > 0
    limit.failed("192.0.2.2")
    assert limit.blocked("192.0.2.2") == 0  # forgotten: counts one, not two
    limit.failed("192.0.2.2")
    assert limit.blocked("192.0.2.2") > 0 and limit.blocked("192.0.2.1") > 0


def test_credential_failures_drop_the_oldest_when_every_address_is_blocked():
    clock = Seconds()
    limit = CredentialFailures(clock, limit=2, window=60, addresses=3)
    for host in ("192.0.2.1", "192.0.2.2", "192.0.2.3"):
        limit.failed(host)
        limit.failed(host)
    limit.failed("192.0.2.4")  # never refused for want of room
    assert limit.tracked() == 3 and limit.blocked("192.0.2.1") == 0
    assert limit.blocked("192.0.2.2") > 0 and limit.blocked("192.0.2.3") > 0


def test_credential_failures_count_a_block_that_has_ended_as_not_over():
    """B was seen last, but its block has ended by 61; A, seen before it, is still blocked.
    The newcomer drops B, not A, though A was seen longer ago."""
    clock = Seconds()
    limit = CredentialFailures(clock, limit=2, window=60, addresses=2)
    limit.failed("192.0.2.2")  # B, at 0
    limit.failed("192.0.2.2")
    clock.now += 20
    limit.failed("192.0.2.1")  # A, at 20: blocked until 80
    limit.failed("192.0.2.1")
    clock.now += 20
    limit.failed("192.0.2.2")  # B, at 40: blocked until 60, and now seen last
    clock.now += 21
    assert limit.blocked("192.0.2.2") == 0 and limit.blocked("192.0.2.1") > 0
    limit.failed("192.0.2.3")
    assert limit.tracked() == 2 and limit.blocked("192.0.2.1") > 0


def test_credential_failures_stay_bounded_under_a_flood_and_keep_the_blocked():
    clock = Seconds()
    limit = CredentialFailures(clock, limit=2, window=60, addresses=50)
    for _ in range(2):
        limit.failed("192.0.2.1")
    for n in range(5_000):
        limit.failed(f"10.{n // 250}.{n % 250}.1")
    assert limit.tracked() == 50 and limit.blocked("192.0.2.1") > 0


# SignatureFailures: limit 2 in 60 seconds, room for 3 pairs


def test_signature_failures_keep_a_refused_pair_and_drop_the_oldest_unrefused():
    clock = Seconds()
    failures = SignatureFailures(clock, limit=2, window=60, keys=3)
    for _ in range(2):
        failures.failed(FABLE, "203.0.113.5")
    failures.failed(QUILL, "203.0.113.5")
    failures.failed(BROOK, "203.0.113.5")
    assert failures.failed(FABLE, "198.51.100.7") == 0  # a new pair: QUILL's goes
    assert failures.tracked() == 3
    assert failures.failed(FABLE, "203.0.113.5") > 0  # still refused
    assert failures.failed(QUILL, "203.0.113.5") == 0  # forgotten: counts again from one


def test_signature_failures_drop_the_oldest_when_every_pair_is_refused():
    clock = Seconds()
    failures = SignatureFailures(clock, limit=1, window=60, keys=2)
    failures.failed(FABLE, "203.0.113.5")
    failures.failed(QUILL, "203.0.113.5")
    assert failures.failed(BROOK, "203.0.113.5") == 0
    assert failures.tracked() == 2
    assert failures.failed(QUILL, "203.0.113.5") > 0
    assert failures.failed(FABLE, "203.0.113.5") == 0  # it was the oldest


def test_signature_failures_stay_bounded_under_a_flood_and_keep_the_refused():
    clock = Seconds()
    failures = SignatureFailures(clock, limit=2, window=60, keys=50)
    for _ in range(2):
        failures.failed(FABLE, "203.0.113.5")
    for n in range(5_000):
        failures.failed(f"sl_{n:012x}", f"10.0.{n // 250}.{n % 250}")
    assert failures.tracked() == 50 and failures.failed(FABLE, "203.0.113.5") > 0


# The registration limit: 2 in 60 seconds, room for 3 addresses


def test_the_registration_limit_keeps_a_refused_address_and_drops_the_oldest_unrefused():
    clock = Seconds()
    limit = oauth.RateLimit(limit=2, window=60, clock=clock, max_addresses=3)
    assert limit.allow("192.0.2.1") and limit.allow("192.0.2.1")  # at its limit
    assert limit.allow("192.0.2.2") and limit.allow("192.0.2.3")
    assert limit.allow("192.0.2.4")  # 192.0.2.2 goes, not 192.0.2.1
    assert limit.tracked == 3
    assert limit.allow("192.0.2.1") is False
    assert limit.allow("192.0.2.2") and limit.allow("192.0.2.2")  # a fresh count


def test_the_registration_limit_drops_the_oldest_when_every_address_is_refused():
    clock = Seconds()
    limit = oauth.RateLimit(limit=1, window=60, clock=clock, max_addresses=2)
    assert limit.allow("192.0.2.1") and limit.allow("192.0.2.2")
    assert limit.allow("192.0.2.3")  # never refused for want of room
    assert limit.tracked == 2
    assert limit.allow("192.0.2.2") is False and limit.allow("192.0.2.1") is True


def test_the_registration_limit_stays_bounded_under_a_flood_and_keeps_the_refused():
    clock = Seconds()
    limit = oauth.RateLimit(limit=2, window=60, clock=clock, max_addresses=50)
    assert limit.allow("192.0.2.1") and limit.allow("192.0.2.1")
    for n in range(5_000):
        limit.allow(f"10.{n // 250}.{n % 250}.1")
    assert limit.tracked == 50 and limit.allow("192.0.2.1") is False


# The workspaces' RateLimiter: each workspace's own limit, room for 3


def test_the_rate_limiter_keeps_a_refused_workspace_and_drops_the_oldest_unrefused():
    clock = Moment()
    limiter = RateLimiter(clock, tracked=3)
    assert limiter.take("fable", 1) == 0  # at its limit, and seen longest ago
    clock.now += timedelta(seconds=1)
    assert limiter.take("quill", 5) == 0
    assert limiter.take("brook", 5) == 0
    assert limiter.take("ada", 5) == 0  # quill goes, though fable is older
    assert limiter.tracked() == 3 and limiter.entries("quill") == 0
    assert limiter.take("fable", 1) > 0


def test_the_rate_limiter_drops_the_oldest_when_every_workspace_is_refused():
    clock = Moment()
    limiter = RateLimiter(clock, tracked=2)
    assert limiter.take("fable", 1) == 0 and limiter.take("quill", 1) == 0
    assert limiter.take("brook", 1) == 0  # never refused for want of room
    assert limiter.tracked() == 2 and limiter.entries("fable") == 0
    assert limiter.take("quill", 1) > 0


def test_the_rate_limiter_stays_bounded_under_a_flood_and_keeps_the_refused():
    clock = Moment()
    limiter = RateLimiter(clock, tracked=50)
    assert limiter.take("fable", 1) == 0
    for n in range(5_000):
        limiter.take(f"acct_{n}", 10)
    assert limiter.tracked() == 50 and limiter.take("fable", 1) > 0


# Cost: a full table reads at most its own size to choose, and stops at the first it may drop


TABLES = ["credential", "signature", "registration", "workspace"]


def table_of(name: str, room: int, limit: int):
    """A table of `name` with room for `room` entries, each allowed `limit`, and a function
    that makes one more attempt of the n-th caller."""
    seconds, moment = Seconds(), Moment()
    if name == "credential":
        made = CredentialFailures(seconds, limit=limit, window=60, addresses=room)
        return made, lambda n: made.failed(f"10.0.0.{n}")
    if name == "signature":
        made = SignatureFailures(seconds, limit=limit, window=60, keys=room)
        return made, lambda n: made.failed(FABLE, f"10.0.0.{n}")
    if name == "registration":
        made = oauth.RateLimit(limit, 60, clock=seconds, max_addresses=room)
        return made, lambda n: made.allow(f"10.0.0.{n}")
    made = RateLimiter(moment, tracked=room)
    return made, lambda n: made.take(f"acct_{n}", limit)


def counting(made, monkeypatch) -> list[int]:
    """Each entry the table reads to decide whether it is over its limit."""
    read: list[int] = []
    over = type(made)._over
    monkeypatch.setattr(type(made), "_over", lambda self, *a: read.append(1) or over(self, *a))
    return read


@pytest.mark.parametrize("name", TABLES)
def test_choosing_among_entries_all_over_their_limit_reads_at_most_the_table(name, monkeypatch):
    made, attempt = table_of(name, room=20, limit=1)
    read = counting(made, monkeypatch)
    for n in range(20):
        attempt(n)
    read.clear()
    attempt(20)
    assert 0 < len(read) <= 20


@pytest.mark.parametrize("name", TABLES)
def test_choosing_stops_at_the_first_entry_not_over_its_limit(name, monkeypatch):
    made, attempt = table_of(name, room=20, limit=5)
    read = counting(made, monkeypatch)
    for n in range(20):
        attempt(n)
    read.clear()
    attempt(20)
    assert len(read) == 1
