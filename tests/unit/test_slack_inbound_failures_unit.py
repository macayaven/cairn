"""The count of failed signatures at Slack's addresses (cairn.web.slack.SignatureFailures):
kept per (install, client address), never per address alone, and bounded whatever arrives.
The inner loop under tests/acceptance/test_k1_slack.py. Every id and address is made up."""

import pytest

from cairn.web.slack import (
    SIGNATURE_FAILURES,
    SIGNATURE_KEYS,
    SIGNATURE_WINDOW,
    SignatureFailures,
    signature_failures,
)

FABLE, QUILL = "sl_0123456789ab", "sl_ba9876543210"
HERE, THERE = "203.0.113.5", "198.51.100.7"


class Clock:
    def __init__(self) -> None:
        self.now = 1_900_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


def test_the_limit_window_and_bound_are_20_in_60_seconds_for_10000_pairs(clock):
    assert (SIGNATURE_FAILURES, SIGNATURE_WINDOW, SIGNATURE_KEYS) == (20, 60, 10_000)
    made = signature_failures(clock)
    assert isinstance(made, SignatureFailures) and made.keys == SIGNATURE_KEYS


def test_20_failures_are_counted_and_the_21st_is_told_how_long_to_wait(clock):
    failures = SignatureFailures(clock)
    assert [failures.failed(FABLE, HERE) for _ in range(20)] == [0] * 20
    assert failures.failed(FABLE, HERE) == 60
    clock.now += 59.5
    assert failures.failed(FABLE, HERE) == 1  # whole seconds, at least 1
    clock.now += 0.5
    assert failures.failed(FABLE, HERE) == 0  # the oldest left the window


def test_a_failure_answered_429_is_not_counted(clock):
    failures = SignatureFailures(clock)
    for _ in range(20):
        failures.failed(FABLE, HERE)
    for _ in range(100):
        failures.failed(FABLE, HERE)
    clock.now += 60
    assert [failures.failed(FABLE, HERE) for _ in range(20)] == [0] * 20


def test_failures_at_one_install_count_nothing_at_another_or_from_another_address(clock):
    failures = SignatureFailures(clock)
    for _ in range(20):
        failures.failed(FABLE, HERE)
    assert failures.failed(QUILL, HERE) == 0
    assert failures.failed(FABLE, THERE) == 0
    assert failures.failed(FABLE, HERE) > 0


def test_an_address_is_read_as_the_sign_in_limit_reads_it(clock):
    failures = SignatureFailures(clock)
    for n in range(20):  # one IPv6 /64 is one address
        failures.failed(FABLE, f"2001:db8:0:1::{n + 1:x}")
    assert failures.failed(FABLE, "2001:db8:0:1::ffff") > 0
    assert failures.failed(FABLE, "2001:db8:0:2::1") == 0
    for _ in range(20):  # requests without a usable address share one count
        failures.failed(QUILL, None)
    assert failures.failed(QUILL, "not an address") > 0


def test_what_is_kept_is_bounded_whatever_arrives(clock):
    failures = SignatureFailures(clock, keys=50)
    for n in range(2_000):  # many addresses at many installs
        failures.failed(f"sl_{n:012x}", f"10.0.{n // 250}.{n % 250}")
    assert failures.tracked() == 50
    for _ in range(500):  # many failures of one pair
        failures.failed(FABLE, HERE)
    assert failures.tracked() == 50
    assert failures.held() <= 50 * 20


def test_when_full_the_oldest_pair_not_refused_is_forgotten_never_a_new_one_refused(clock):
    failures = SignatureFailures(clock, keys=2)
    for _ in range(20):
        failures.failed(FABLE, HERE)  # at its limit: refused from now on
    failures.failed(QUILL, HERE)
    assert failures.failed("sl_000000000001", HERE) == 0  # QUILL's pair is forgotten
    assert failures.tracked() == 2
    assert failures.failed(FABLE, HERE) > 0  # FABLE's, being refused, is kept
    assert failures.failed(QUILL, HERE) == 0  # QUILL's starts again from nothing


def test_a_clock_that_steps_back_frees_no_pair(clock):
    failures = SignatureFailures(clock)
    for _ in range(20):
        failures.failed(FABLE, HERE)
    clock.now -= 3_600
    assert failures.failed(FABLE, HERE) == 60


def test_it_needs_a_limit_a_window_and_room(clock):
    for options in ({"limit": 0}, {"window": 0}, {"keys": 0}):
        with pytest.raises(ValueError):
            SignatureFailures(clock, **options)
