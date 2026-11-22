"""The security guide's sentence on where the failure counts live (review, finding 4): in
the server's memory, so they start again with it; an operator who needs more puts a reverse
proxy's or the cloud's own limit in front. Pinned here, then asked of cairn."""

from cairn import oauth
from cairn.web.ratelimit import CredentialFailures
from cairn.web.slack import SignatureFailures
from support.guide_source import guide_source


def flat(text: str) -> str:
    return " ".join(text.split())


def test_the_guide_says_the_counts_start_again_with_the_server():
    sentence = (
        "- These limits are counted in the server's memory, so they start again when it"
        " restarts; for a limit that outlasts a restart, put a reverse proxy's or the cloud's"
        " own rate limit in front of cairn."
    )
    assert flat(sentence) in flat(guide_source("security"))
    # Nothing of them is written anywhere: a new table (a restarted server) starts empty.
    now = lambda: 4_000_000_000.0  # noqa: E731
    first = CredentialFailures(now, limit=1)
    first.failed("192.0.2.1")
    assert first.blocked("192.0.2.1") > 0
    assert CredentialFailures(now, limit=1).blocked("192.0.2.1") == 0
    assert SignatureFailures(now).tracked() == 0
    assert oauth.RateLimit(1, 60, clock=now).tracked == 0
