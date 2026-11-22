"""What deploy/Caddyfile keeps out of the proxy's logs, checked without running Caddy.

Caddy's log filter replaces every match of one regular expression in the request's URI. The
expression and its replacement are read from the file as shipped and applied here with
Python's `re`, which agrees with Go's for this pattern (Go's `${1}` is Python's `\\g<1>`).
The CI job `infra` checks that Caddy itself accepts the file and reads the same expression.
"""

import re

import pytest

from support.paths import REPO_ROOT

CADDYFILE = REPO_ROOT / "deploy" / "Caddyfile"
URI_FILTER = re.compile(r'^\s*request>uri regexp `([^`]+)` "([^"]*)"\s*$', re.M)
# Made up, in the shape of an invitation: 43 URL-safe characters.
TOKEN = "made-up-invitation-for-the-log-filter-tests"


def filters() -> list[tuple[str, str]]:
    return URI_FILTER.findall(CADDYFILE.read_text(encoding="utf-8"))


def logged(uri: str, pattern: str, replacement: str) -> str:
    return re.sub(pattern, re.sub(r"\$\{(\d+)\}", r"\\g<\1>", replacement), uri)


def test_both_loggers_filter_the_uri_the_same_way():
    found = filters()
    assert len(found) == 2 and found[0] == found[1]  # the access log and the default logger


@pytest.mark.parametrize(
    ("uri", "kept"),
    [
        (f"/join/{TOKEN}", "/join/redacted"),
        (f"/join/{TOKEN}?from=mail", "/join/redacted?redacted"),
        ("/auth/github/callback?code=abc&state=def", "/auth/github/callback?redacted"),
        ("/oauth/consent?request=eyJ.sig", "/oauth/consent?redacted"),
        ("/static/cairn.css?v=6d746e31cb4d", "/static/cairn.css?redacted"),
        ("/b/ops/p/deploys", "/b/ops/p/deploys"),
        ("/joinery/plans", "/joinery/plans"),
        ("/docs/join/", "/docs/join/"),
        ("/", "/"),
    ],
)
def test_a_logged_uri_holds_no_query_and_no_invitation(uri, kept):
    pattern, replacement = filters()[0]
    line = logged(uri, pattern, replacement)
    assert line == kept
    assert TOKEN not in line and "code=" not in line


def test_headers_that_can_carry_a_secret_are_dropped_in_both_loggers():
    text = CADDYFILE.read_text(encoding="utf-8")
    for field in (
        "request>headers>Referer delete",
        "request>headers>X-Api-Key delete",
        "resp_headers>Location delete",
    ):
        assert text.count(field) == 2, field
