"""A lockout is not evicted while something else can be (review: Security 4). Every limiter
that drops an entry when its table is full drops the oldest one not refusing anyone now, and
one refusing only when every entry is; finding it costs no walk over the table, so the work a
request does stays the same whether the table holds 100 entries or 10,000 (counted in
cairn.holding.Holding.steps, never in time). Clocks are moved by hand; every address and name
is made up."""

from datetime import UTC, datetime

import pytest

from cairn import oauth
from cairn.holding import Holding
from cairn.web.ratelimit import CredentialFailures, RateLimiter
from cairn.web.slack import SignatureFailures

FABLE = "sl_0123456789ab"
ROOM = 10_000


class Seconds:
    def __init__(self) -> None:
        self.now = 4_000_000_000.0

    def __call__(self) -> float:
        return self.now


class Moment:
    def __init__(self) -> None:
        self.now = datetime(2099, 11, 19, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def address(n: int) -> str:
    return f"10.{n // 65_536 % 256}.{n // 256 % 256}.{n % 256}"


# Holding, on its own


def test_the_victim_is_the_oldest_not_refusing_now():
    held: Holding[str] = Holding()
    held.seen("ada", until=100.0)  # refusing until 100
    held.seen("brook", until=None)
    held.seen("quill", until=None)
    assert held.victim(50.0) == "brook"
    held.drop("brook")
    assert held.victim(50.0) == "quill"
    assert held.victim(100.0) == "ada"  # its refusal has ended: the oldest not refusing


def test_when_every_entry_refuses_the_oldest_seen_goes():
    held: Holding[str] = Holding()
    held.seen("ada", until=100.0)
    held.seen("brook", until=90.0)
    assert held.victim(10.0) == "ada"
    held.seen("ada", until=200.0)  # seen again: now the most recent
    assert held.victim(10.0) == "brook"


def test_an_entry_seen_again_is_judged_by_what_it_was_last_seen_with():
    held: Holding[str] = Holding()
    held.seen("ada", until=None)
    held.seen("brook", until=None)
    held.seen("ada", until=100.0)  # now refusing
    assert held.victim(10.0) == "brook"
    held.drop("brook")
    held.seen("ada", until=None)
    assert held.victim(10.0) == "ada" and len(held) == 1


# One test per limiter: a refused key stays refused through a flood of others


def test_credential_failures_keep_a_blocked_address_through_a_flood():
    limit = CredentialFailures(Seconds(), limit=2, window=60, addresses=ROOM)
    for _ in range(2):
        limit.failed("192.0.2.1")
    for n in range(2 * ROOM):
        limit.failed(address(n))
    assert limit.tracked() == ROOM and limit.blocked("192.0.2.1") > 0


def test_signature_failures_keep_a_refused_pair_through_a_flood():
    failures = SignatureFailures(Seconds(), limit=2, window=60, keys=ROOM)
    for _ in range(2):
        failures.failed(FABLE, "203.0.113.5")
    for n in range(2 * ROOM):
        failures.failed(FABLE, address(n))
    assert failures.tracked() == ROOM and failures.failed(FABLE, "203.0.113.5") > 0


def test_the_registration_limit_keeps_a_refused_address_through_a_flood():
    limit = oauth.RateLimit(limit=2, window=60, clock=Seconds(), max_addresses=ROOM)
    assert limit.allow("192.0.2.1") and limit.allow("192.0.2.1")
    for n in range(2 * ROOM):
        limit.allow(address(n))
    assert limit.tracked == ROOM and limit.allow("192.0.2.1") is False


def test_the_rate_limiter_keeps_a_refused_workspace_through_a_flood():
    limiter = RateLimiter(Moment(), tracked=ROOM)
    assert limiter.take("fable", 1) == 0
    for n in range(2 * ROOM):
        limiter.take(f"acct_{n}", 10)
    assert limiter.tracked() == ROOM and limiter.take("fable", 1) > 0


# The work per request does not grow with the table


def table_of(name: str, room: int):
    """A table of `name` with room for `room` entries, each allowed 2, and a function that
    makes one attempt of the n-th caller."""
    if name == "credential":
        made = CredentialFailures(Seconds(), limit=2, window=60, addresses=room)
        return made, lambda n: made.failed(address(n))
    if name == "signature":
        made = SignatureFailures(Seconds(), limit=2, window=60, keys=room)
        return made, lambda n: made.failed(FABLE, address(n))
    if name == "registration":
        made = oauth.RateLimit(2, 60, clock=Seconds(), max_addresses=room)
        return made, lambda n: made.allow(address(n))
    made = RateLimiter(Moment(), tracked=room)
    return made, lambda n: made.take(f"acct_{n}", 2)


def steps_per_request(name: str, room: int) -> float:
    """The worst case for a walk: every entry but the newest is refusing, so the oldest one
    not refusing is at the far end; then as many new callers as the table holds come, each
    dropping one. The steps of every request since the table was made, over their number
    (the heaps are made again now and then: that work is counted too)."""
    made, attempt = table_of(name, room)
    for n in range(room - 1):
        attempt(n)
        attempt(n)  # at its limit: refusing
    attempt(room - 1)  # the one entry not refusing, seen last
    for n in range(room, 2 * room):
        attempt(n)
    assert made._holding.steps > 0
    return made._holding.steps / (3 * room - 1)


@pytest.mark.parametrize("name", ["credential", "signature", "registration", "workspace"])
def test_the_work_per_request_is_the_same_for_100_entries_and_for_10_000(name):
    small, large = steps_per_request(name, 100), steps_per_request(name, ROOM)
    assert small <= 8 and large <= 8, (small, large)


# 7. security.md: the limits stay in memory, and what stands behind them


SAYS_MEMORY = (
    "Each server process keeps its own, and they start empty after a restart: they are kept in"
    " memory on purpose, since writing a row for every failed attempt would let anyone who can"
    " send failing requests keep the database's one writer busy."
)
SAYS_BEHIND = (
    "What stands behind them when they are gone: a key is 256 bits from the system's random"
    " source, so guessing one is not a matter of how often one may try, and signing in is the"
    " provider's."
)


def flat(text: str) -> str:
    return " ".join(text.split())


def test_security_md_says_the_limits_are_each_process_s_own_and_kept_in_memory(tmp_path):
    from guide_source import guide_source

    raw = guide_source("security")
    part = raw[raw.index("\n## What cairn leaves to the operator\n") :]
    assert flat(SAYS_MEMORY) in flat(part) and flat(raw).count(flat(SAYS_MEMORY)) == 1
    # Two limiters of one process share nothing, and a new one (a restart) starts empty;
    # none of them holds a store to write to.
    first, second = CredentialFailures(Seconds()), CredentialFailures(Seconds())
    for _ in range(30):
        first.failed("192.0.2.1")
    assert first.blocked("192.0.2.1") > 0 and second.blocked("192.0.2.1") == 0
    assert second.tracked() == 0
    for made in (
        first,
        oauth.RateLimit(2, 60),
        RateLimiter(Moment()),
        SignatureFailures(Seconds()),
    ):
        assert not any("store" in name for name in vars(made)), type(made).__name__


def test_security_md_says_a_key_is_256_bits_from_the_system_s_random_source(tmp_path, monkeypatch):
    from guide_source import guide_source

    import cairn.store.credentials as credentials
    from cairn.store import Store

    raw = guide_source("security")
    part = raw[raw.index("\n## What cairn leaves to the operator\n") :]
    assert flat(SAYS_BEHIND) in flat(part) and flat(raw).count(flat(SAYS_BEHIND)) == 1
    asked: list[int] = []
    real = credentials.secrets.token_urlsafe

    def token(nbytes):
        asked.append(nbytes)
        return real(nbytes)

    monkeypatch.setattr(credentials.secrets, "token_urlsafe", token)
    store = Store(tmp_path / "cairn.db")
    try:
        key = store.create_key("laptop")
    finally:
        store.close()
    assert asked == [32] and 8 * 32 == 256  # bytes from secrets, the system's random source
    assert key.startswith(credentials.KEY_PREFIX) and len(key) == len(credentials.KEY_PREFIX) + 43
