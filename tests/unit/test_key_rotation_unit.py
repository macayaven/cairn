"""Changing a key that seals stored secrets (`CAIRN_GOOGLE_CONNECT_KEY`, `CAIRN_SLACK_KEY`)
without losing what it sealed: earlier keys beside the current one (`<VARIABLE>_PREVIOUS`),
the `reseal` command, and what `serve` refuses.

Every key, token and secret here is made up. No assertion compares a key, a token or a sealed
value in a way that would print it when it fails: each comparison is made into a bool first.
"""

import base64

import pytest

from cairn.store.hosted import Sealer, associated

CURRENT = bytes(range(32))
EARLIER = bytes(range(1, 33))
OLDEST = bytes(range(2, 34))
STRANGER = bytes(range(3, 35))
TOKEN = "1//rotation-refresh-token-ada-0123456789"
TIED = associated("acct_fable", "gmail.ada.0123456789", "user_ada")


def b64(key: bytes) -> str:
    return base64.b64encode(key).decode()


# Sealer: earlier keys open, the current key seals


def test_a_value_sealed_under_an_earlier_key_opens_beside_the_current_one():
    for earlier in (EARLIER, OLDEST):
        sealed = Sealer(earlier).seal(TOKEN, TIED)
        opened = Sealer(CURRENT, (EARLIER, OLDEST)).open(sealed, TIED)
        assert (opened == TOKEN) is True


def test_what_is_sealed_from_now_on_is_sealed_under_the_current_key():
    sealed = Sealer(CURRENT, (EARLIER,)).seal(TOKEN, TIED)
    assert (Sealer(CURRENT).open(sealed, TIED) == TOKEN) is True
    assert Sealer(EARLIER).open(sealed, TIED) is None


def test_a_value_of_no_key_given_does_not_open():
    sealed = Sealer(STRANGER).seal(TOKEN, TIED)
    assert Sealer(CURRENT, (EARLIER, OLDEST)).open(sealed, TIED) is None


def test_a_sealed_value_carries_nothing_that_says_which_key_sealed_it():
    """Trial in a fixed order: the form of a value is the same under every key and the same as
    before earlier keys could be given (base64url of a 12-byte nonce and the sealed text)."""
    now = Sealer(CURRENT, (EARLIER,)).seal(TOKEN, TIED)
    before = Sealer(EARLIER).seal(TOKEN, TIED)
    assert len(now) == len(before)
    assert len(base64.urlsafe_b64decode(now)) == 12 + len(TOKEN.encode()) + 16


def test_the_sealer_says_whether_the_current_key_opens_a_value():
    sealer = Sealer(CURRENT, (EARLIER,))
    assert sealer.current(sealer.seal(TOKEN, TIED), TIED) is True
    assert sealer.current(Sealer(EARLIER).seal(TOKEN, TIED), TIED) is False
    assert sealer.current(Sealer(STRANGER).seal(TOKEN, TIED), TIED) is False
    assert sealer.current(None, TIED) is False


@pytest.mark.parametrize(
    "previous",
    [(CURRENT,), (EARLIER, CURRENT), (bytes(16),), (b"",)],
    ids=["the-current-key", "the-current-key-second", "16-bytes", "empty"],
)
def test_an_earlier_key_equal_to_the_current_one_or_not_32_bytes_is_refused(previous):
    with pytest.raises(ValueError) as refused:
        Sealer(CURRENT, previous)
    said = str(refused.value)
    leaked = any(k.hex() in said or b64(k) in said for k in (CURRENT, *previous) if k)
    assert leaked is False


def test_no_repr_of_a_sealer_with_earlier_keys_shows_a_key():
    shown = repr(Sealer(CURRENT, (EARLIER, OLDEST)))
    leaked = any(k.hex() in shown or b64(k) in shown for k in (CURRENT, EARLIER, OLDEST))
    assert leaked is False and shown == "Sealer()"
