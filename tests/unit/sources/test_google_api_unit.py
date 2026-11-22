"""Unit tests for cairn.sources.google_api: what every Google source shares when it calls a
Google API with an access token. HTTP goes through httpx.MockTransport; time, sleep and the
backoff's jitter are injected. Every token and body here is made up."""

import ast
import asyncio
import json
import re
from datetime import UTC, datetime, timedelta, timezone
from functools import partial
from pathlib import Path

import httpx
import pytest

from cairn.sources import SourceError
from cairn.sources import google_api as api
from support.linear_cost import assert_near_linear

TOKEN = "ya29." + "c4UnitMadeUp" + "AccessToken"  # never a real token
CANARY = "c4-" + "api-body-" + "canary"
URL = "https://meet.example.test/v2/conferenceRecords"
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
SOURCES = Path(api.__file__).parent


def detail_body(status: int, reason: str) -> dict:
    """AIP-193's shape: error.details[] holding an ErrorInfo with its reason."""
    return {
        "error": {
            "code": status,
            "message": CANARY,
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.LocalizedMessage", "message": CANARY},
                {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason},
            ],
        }
    }


def errors_body(status: int, reason: str) -> dict:
    """The older shape some Workspace APIs send: error.errors[] with a reason each."""
    item = {"domain": "usageLimits", "reason": reason, "message": CANARY}
    return {"error": {"errors": [item], "code": status, "message": CANARY}}


# Reading an error's reason, in both of Google's shapes


def test_the_reasons_are_read_from_both_shapes_and_nothing_else():
    assert api.error_reasons(json.dumps(detail_body(429, "RATE_LIMIT_EXCEEDED")).encode()) == {
        "RATE_LIMIT_EXCEEDED"
    }
    assert api.error_reasons(json.dumps(errors_body(403, "rateLimitExceeded")).encode()) == {
        "rateLimitExceeded"
    }
    both = detail_body(403, "ACCESS_TOKEN_SCOPE_INSUFFICIENT")
    both["error"]["errors"] = errors_body(403, "forbidden")["error"]["errors"]
    assert api.error_reasons(json.dumps(both).encode()) == {
        "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
        "forbidden",
    }


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"<html>" + CANARY.encode() + b"</html>",
        b"[1, 2]",
        json.dumps({"error": "invalid_grant"}).encode(),
        json.dumps({"error": {"details": "x", "errors": 7}}).encode(),
        json.dumps({"error": {"details": [{"reason": 7}, "x"], "errors": [None]}}).encode(),
        json.dumps({"error": {"details": [{"reason": CANARY + " with spaces"}]}}).encode(),
        json.dumps({"error": {"details": [{"reason": "A" * 65}]}}).encode(),
    ],
    ids=["empty", "html", "array", "a token endpoint's", "odd types", "odd items", "words", "long"],
)
def test_a_body_without_a_short_reason_word_gives_none(body):
    assert api.error_reasons(body) == frozenset()


@pytest.mark.parametrize(
    ("status", "reasons", "limited"),
    [
        (429, set(), True),
        (429, {"RESOURCE_EXHAUSTED"}, True),
        (403, {"RATE_LIMIT_EXCEEDED"}, True),
        (403, {"rateLimitExceeded"}, True),
        (403, {"userRateLimitExceeded"}, True),
        (403, {"dailyLimitExceeded"}, False),
        (403, {"quotaExceeded"}, False),  # Calendar's usage limit: waiting does not help
        (403, {"ACCESS_TOKEN_SCOPE_INSUFFICIENT"}, False),
        (403, {"forbidden"}, False),
        (403, set(), False),
        (401, {"RATE_LIMIT_EXCEEDED"}, False),
        (500, set(), False),
    ],
)
def test_a_rate_limit_is_a_429_or_a_403_whose_reason_says_so(status, reasons, limited):
    assert api.rate_limited(status, frozenset(reasons)) is limited


# The backoff


@pytest.mark.parametrize(
    ("attempt", "jitter", "wait"),
    [(0, 0.0, 1.0), (0, 1.0, 2.0), (1, 0.5, 2.5), (2, 0.25, 4.25), (3, 0.0, 8.0), (6, 0.9, 32.0)],
)
def test_the_nth_wait_is_two_to_the_n_seconds_and_up_to_one_more_at_most_32(attempt, jitter, wait):
    assert api.backoff(attempt, lambda: jitter) == wait


# One pull's requests


class Stand:
    def __init__(self, *answers) -> None:
        self.now = NOW
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []
        self.slept: list[float] = []
        self.tick = timedelta(0)

    def clock(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.now += self.tick
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def reader(self, *, deadline=None, budget=100, jitter=0.5, limit=api.MAX_RESPONSE_BYTES):
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return api.Reader(
            http, token=TOKEN, clock=self.clock, sleep=self.sleep, deadline=deadline,
            budget=budget, jitter=lambda: jitter, limit=limit,
        )  # fmt: skip

    def get(self, reader=None, **given):
        reader = reader or self.reader(**given)
        return asyncio.run(reader.get(URL, {"pageSize": "100"}, service="Google Meet", what="it"))


def test_a_get_sends_the_token_only_as_a_bearer_header_and_returns_the_json():
    stand = Stand(httpx.Response(200, json={"conferenceRecords": []}))
    reply = stand.get()
    assert (reply.status, reply.body, reply.limited) == (200, {"conferenceRecords": []}, False)
    (request,) = stand.requests
    assert request.method == "GET" and request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["accept"] == "application/json"
    assert TOKEN not in str(request.url) and dict(request.url.params) == {"pageSize": "100"}


def test_another_status_is_returned_with_its_reasons_and_never_its_body():
    stand = Stand(httpx.Response(403, json=errors_body(403, "forbidden")))
    reply = stand.get()
    assert (reply.status, reply.body, reply.reasons, reply.limited) == (
        403,
        None,
        frozenset({"forbidden"}),
        False,
    )
    assert CANARY not in repr(reply)


def test_a_redirect_is_not_followed():
    stand = Stand(httpx.Response(302, headers={"location": "https://elsewhere.example.test/"}))
    assert stand.get().status == 302 and len(stand.requests) == 1


def test_a_rate_limit_is_retried_four_times_through_sleep_then_returned_as_such():
    stand = Stand(httpx.Response(429, json=detail_body(429, "RATE_LIMIT_EXCEEDED")))
    reply = stand.get()
    assert reply.status == 429 and reply.limited
    assert stand.slept == [1.5, 2.5, 4.5, 8.5] and len(stand.requests) == 5


def test_a_rate_limit_that_clears_returns_the_answer_after_it():
    limited = httpx.Response(403, json=errors_body(403, "rateLimitExceeded"))
    stand = Stand(limited, limited, httpx.Response(200, json={"ok": True}))
    assert stand.get().body == {"ok": True} and stand.slept == [1.5, 2.5]


def test_no_request_starts_once_the_deadline_has_come():
    stand = Stand(httpx.Response(200, json={}))
    with pytest.raises(api.Spent):
        stand.get(deadline=NOW)
    assert stand.requests == []


def test_a_wait_that_would_pass_the_deadline_is_not_waited_for():
    stand = Stand(httpx.Response(429))
    with pytest.raises(api.Spent):
        stand.get(deadline=NOW + timedelta(seconds=1))
    assert stand.slept == [] and len(stand.requests) == 1


def test_a_pull_makes_at_most_its_budget_of_requests():
    stand = Stand(httpx.Response(200, json={}))
    reader = stand.reader(budget=2)
    stand.get(reader)
    stand.get(reader)
    assert reader.made == 2
    with pytest.raises(api.Spent):
        stand.get(reader)
    assert len(stand.requests) == 2


def test_a_retry_counts_against_the_budget():
    stand = Stand(httpx.Response(429))
    with pytest.raises(api.Spent):
        stand.get(budget=3)
    assert len(stand.requests) == 3


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        (httpx.ReadTimeout("timed out"), "cannot reach Google Meet for it: ReadTimeout"),
        (httpx.ConnectError(CANARY), "cannot reach Google Meet for it: ConnectError"),
        (httpx.Response(200, text=f"<p>{CANARY}</p>"), "Google Meet's answer to it is not JSON"),
    ],
)
def test_a_network_error_or_a_body_that_is_not_json_fails_in_words(answer, said):
    stand = Stand(answer)
    with pytest.raises(SourceError) as failed:
        stand.get()
    assert str(failed.value) == said and CANARY not in str(failed.value)
    assert URL not in str(failed.value)


def test_an_answer_past_the_limit_is_not_parsed():
    stand = Stand(httpx.Response(200, json={"text": "x" * 200}))
    with pytest.raises(SourceError, match="larger than this server reads"):
        stand.get(limit=100)


def test_a_raw_get_returns_the_bytes_of_a_200_as_they_came():
    body = b"\xef\xbb\xbf# Plan\r\n\r\nnot JSON \xff\n"
    stand = Stand(httpx.Response(200, content=body, headers={"content-type": "text/markdown"}))
    reader = stand.reader()
    reply = asyncio.run(reader.get(URL, {"alt": "media"}, service="S", what="it", raw=True))
    assert (reply.status, reply.body, reply.limited) == (200, body, False)
    (request,) = stand.requests
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["accept"] == "*/*"


def test_a_raw_get_reads_an_error_for_its_reasons_and_waits_out_a_rate_limit():
    limited = httpx.Response(403, json=errors_body(403, "userRateLimitExceeded"))
    refused = httpx.Response(403, json=errors_body(403, "cannotDownloadFile"))
    stand = Stand(limited, refused)
    reply = asyncio.run(stand.reader().get(URL, {}, service="S", what="it", raw=True))
    assert (reply.status, reply.body, reply.reasons) == (403, None, {"cannotDownloadFile"})
    assert stand.slept == [1.5]


def test_a_raw_200_past_the_limit_is_too_large_for_the_source_to_decide():
    stand = Stand(httpx.Response(200, content=b"x" * 200))
    with pytest.raises(api.TooLarge):
        asyncio.run(stand.reader(limit=100).get(URL, {}, service="S", what="it", raw=True))


def test_an_error_body_past_the_limit_is_not_read_for_its_reason():
    body = detail_body(429, "RATE_LIMIT_EXCEEDED")
    body["error"]["message"] = "x" * 200
    stand = Stand(httpx.Response(403, json=body))
    reply = stand.get(limit=100)
    assert reply.status == 403 and reply.reasons == frozenset() and not reply.limited


# Times, digests and settings every Google source reads the same way


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-12T09:00:00Z", datetime(2026, 11, 12, 9, tzinfo=UTC)),
        ("2026-11-12t09:00:00z", datetime(2026, 11, 12, 9, tzinfo=UTC)),
        ("2026-11-12T09:00:00.123456789Z", datetime(2026, 11, 12, 9, 0, 0, 123456, tzinfo=UTC)),
        ("2026-11-12T10:30:00+01:30", datetime(2026, 11, 12, 9, tzinfo=UTC)),
        ("2026-11-12T01:00:00-08:00", datetime(2026, 11, 12, 9, tzinfo=UTC)),
        ("2026-11-12T09:00:00", None),
        ("2026-11-12", None),
        ("2026-13-12T09:00:00Z", None),
        ("", None),
        (None, None),
        (7, None),
    ],
)
def test_a_moment_is_rfc_3339_with_a_zone_read_in_utc(value, expected):
    assert api.moment(value) == expected


def test_a_stamp_is_rfc_3339_in_utc_with_milliseconds():
    when = datetime(2026, 11, 12, 10, 0, 0, 123456, tzinfo=timezone(timedelta(hours=1)))
    assert api.stamp(when) == "2026-11-12T09:00:00.123Z"
    assert api.moment(api.stamp(when)) == datetime(2026, 11, 12, 9, 0, 0, 123000, tzinfo=UTC)


def test_a_digest_is_sixteen_hex_digits_and_never_the_name():
    one = api.digest("calendars/primary/events/c5qv00001")
    assert len(one) == 16 and int(one, 16) >= 0
    assert one == api.digest("calendars/primary/events/c5qv00001")
    assert one != api.digest("calendars/primary/events/c5qv00002")


@pytest.mark.parametrize(
    ("value", "looks"),
    [
        ('{"type": "service_account"}', True),
        ("-----BEGIN PRIVATE KEY-----", True),
        ("ya29." + "c4UnitMadeUp", True),
        ("1//" + "0c4MadeUp", True),
        ("GOCSPX-" + "c4MadeUp", True),
        ("AIza" + "c4MadeUp", True),
        ("[1, 2]", True),
        ("GOOGLE_CREDENTIAL", False),
        ("ada@example.test", False),
        ("primary", False),
    ],
)
def test_what_looks_like_a_credential(value, looks):
    assert api.looks_like_a_credential(value) is looks


@pytest.mark.perf
@pytest.mark.parametrize(
    "text",
    [
        lambda n: "a" * n,  # every start used to scan the rest of the run for an "@"
        lambda n: "a" * (n // 2) + "@" + "b" * (n // 2),  # and the rest of the domain for a "."
        lambda n: "a." * (n // 2),
    ],
    ids=[f"text{i}" for i in range(3)],  # the ids the strings these replaced were given
)
def test_finding_addresses_in_a_long_text_takes_linear_time(text):
    """Only time shows this. EMAIL.sub took time that grew with the square of the run it
    searched: 2.2 s for 40,000 letters, so 200,000 took about a minute. Linear, eight times
    the text takes about eight times as long (linear_cost)."""
    assert_near_linear(lambda n: partial(api.EMAIL.sub, " ", text(n)), 10_000)


def test_an_address_is_found_whole_wherever_it_starts_and_a_config_still_checks_one():
    found = api.EMAIL.sub("_", "to ada.park+x@example.test, (bo@b.example.test) x@y@z.test")
    assert found == "to _, (_) x@_"
    assert api.EMAIL.fullmatch("ada@example.test")
    assert not api.EMAIL.fullmatch("ada@example")
    assert not api.EMAIL.fullmatch("ada example@test.test")


@pytest.mark.parametrize(
    ("value", "kept"),
    [
        ("https://api.example.test/v3/", "https://api.example.test/v3"),
        ("HTTPS://api.example.test/v3", "https://api.example.test/v3"),
        ("http://127.0.0.1:8782/v3", "http://127.0.0.1:8782/v3"),
        ("http://localhost:8782/v3", "http://localhost:8782/v3"),
        ("http://[::1]:8782/v3", "http://[::1]:8782/v3"),
    ],
)
def test_a_base_url_is_https_or_http_on_this_machine(value, kept):
    assert api.check_base_url(value, "the Example API") == kept


@pytest.mark.parametrize(
    ("value", "said"),
    [
        (7, "base_url is the address of the Example API"),
        ("http://api.example.test/v3", "base_url is https, or http on this machine"),
        ("ftp://api.example.test/v3", "base_url is https, or http on this machine"),
        ("https://user:pw@api.example.test/v3", "must not hold a user or password"),
        ("https://api.example.test/v3?key=1", "no query or fragment"),
        ("https://api.example.test/v3#top", "no query or fragment"),
        ("https://api.example.test/ya29." + "c4MadeUpToken", "looks like it holds a credential"),
        ("https://[::1", "base_url is not a URL"),
    ],
)
def test_a_base_url_that_cannot_be_used_is_refused_without_repeating_it(value, said):
    with pytest.raises(ValueError, match=re.escape(said)) as refused:
        api.check_base_url(value, "the Example API")
    assert "api.example.test" not in str(refused.value)


# What this module is: shared by every Google source, about none of them


def imports(module: str) -> set[str]:
    """What a module of cairn.sources imports, read from its source (importing cairn.sources
    imports every kind, so sys.modules cannot tell)."""
    tree = ast.parse((SOURCES / module).read_text())
    named = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            named |= {alias.name for alias in node.names}
    return named


def test_the_shared_module_imports_no_google_source_and_names_no_api():
    assert not any("meet" in name or "auth" in name for name in imports("google_api.py"))
    text = (SOURCES / "google_api.py").read_text()
    assert "meet.googleapis.com" not in text and "/auth/" not in text


def defined(module: str) -> set[str]:
    """The names a module of cairn.sources defines at its top level."""
    tree = ast.parse((SOURCES / module).read_text())
    named = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.ClassDef):
            named.add(node.name)
        elif isinstance(node, ast.Assign):
            named |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return named


SHARED_COPIES = {
    "LOOPBACK_HOSTS",
    "ENV_NAME",
    "SETTING_NAME",
    "EMAIL",
    "_GOOGLE_PREFIXES",
    "_looks_like_a_credential",
    "_check_base_url",
    "digest",
}


def test_the_drive_source_reads_its_config_with_the_shared_checks_not_copies_of_them():
    """Drive was written beside this module and carried its own copies of what is here; where
    a copy did the same, Drive uses this module's. Its timestamp reading stays its own: Drive's
    `moment` takes neither a lower-case "z" nor white space around a time, this module's does."""
    from cairn.sources import google_drive as drive

    assert "cairn.sources.google_api" in imports("google_drive.py")
    assert not SHARED_COPIES & defined("google_drive.py")
    assert drive.digest is api.digest
    assert drive.moment is not api.moment and "TIME" in defined("google_drive.py")
    for odd in ("2026-11-12T09:00:00z", " 2026-11-12T09:00:00Z "):
        assert drive.moment(odd) is None and api.moment(odd) is not None


@pytest.mark.parametrize(
    "value",
    [
        "2026-11-13T10:00:00+24:00",  # an offset of a whole day is not an offset
        "2026-11-13T10:00:00-99:00",
        "2026-11-13T10:00:00+23:60",
        "0001-01-01T00:00:00+23:59",  # a real offset, but the moment is before year 1 in UTC
        "9999-12-31T23:59:59-23:59",  # and this one is past year 9999
    ],
)
def test_a_timestamp_with_an_offset_that_cannot_be_is_not_a_moment(value):
    """What Google sends is read by every source: a time that cannot be must come back as
    None, as the docstring says, never as an exception that ends a pull without a reason."""
    assert api.moment(value) is None


def test_the_widest_real_offsets_are_still_moments():
    assert api.moment("2026-11-13T10:00:00+23:59") == datetime(2026, 11, 12, 10, 1, tzinfo=UTC)
    assert api.moment("2026-11-13T10:00:00-23:59") == datetime(2026, 11, 14, 9, 59, tzinfo=UTC)
