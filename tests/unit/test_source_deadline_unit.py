"""Unit tests for cairn.sources.timing and its use: a request of a pull is given what is left
until the pull's deadline and no more, so a provider that drips its answer, or never sends
its headers, cannot hold a pull (and every other source's sync) past its deadline.

The clock is a frozen one, so "what is left" is decided by the test; the FLOOR is lowered so
the requests here wait a fraction of a second. Each test counts how much of a dripped answer
was read: the old code read all of it, whatever the deadline. Every key here is made up."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from cairn.sources import KINDS, SourceError, timing
from cairn.sources import google_api as api

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
CHUNKS = 40  # of a dripped answer, one every DRIP seconds: two seconds in all
DRIP = 0.05
LEFT = timedelta(seconds=0.2)  # what the frozen clock leaves until the deadline


def frozen() -> datetime:
    return NOW


@pytest.fixture(autouse=True)
def short_floor(monkeypatch):
    monkeypatch.setattr(timing, "FLOOR", 0.01)


class Drip(httpx.AsyncByteStream):
    """A body sent a chunk at a time, each well inside any read timeout."""

    def __init__(self, head: bytes, tail: bytes) -> None:
        self.head, self.tail, self.sent = head, tail, 0

    async def __aiter__(self):
        yield self.head
        for _ in range(CHUNKS):
            await asyncio.sleep(DRIP)
            self.sent += 1
            yield b" "
        yield self.tail


def test_time_left_is_until_the_deadline_by_the_clock_and_at_least_the_floor():
    assert timing.time_left(None, frozen) is None
    assert timing.time_left(NOW + timedelta(hours=1), frozen) == 3600.0
    assert timing.time_left(NOW + timedelta(microseconds=1), frozen) == timing.FLOOR
    assert timing.time_left(NOW - timedelta(hours=1), frozen) == timing.FLOOR


def test_a_google_request_whose_answer_drips_past_the_deadline_is_spent():
    drip = Drip(b'{"items": [', b"]}")
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=drip))
    )
    reader = api.Reader(
        http, token="ya29.unitDeadlineMadeUp", clock=frozen, sleep=asyncio.sleep,
        deadline=NOW + LEFT, budget=10,
    )  # fmt: skip
    with pytest.raises(api.Spent, match="answer still arriving"):
        asyncio.run(reader.get("https://api.example.test/v1/x", {}, service="S", what="w"))
    assert drip.sent < CHUNKS // 2


def test_a_google_request_with_no_deadline_reads_the_whole_answer():
    drip = Drip(b'{"items": [', b"]}")
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=drip))
    )
    reader = api.Reader(
        http, token="ya29.unitDeadlineMadeUp", clock=frozen, sleep=asyncio.sleep,
        deadline=None, budget=10,
    )  # fmt: skip
    reply = asyncio.run(reader.get("https://api.example.test/v1/x", {}, service="S", what="w"))
    assert reply.body == {"items": []} and drip.sent == CHUNKS


def test_a_google_sign_in_that_never_answers_fails_the_pull_at_the_deadline():
    async def stalled(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(CHUNKS * DRIP)
        raise AssertionError("the sign-in was waited for past the deadline")

    credential = json.dumps(
        {
            "type": "authorized_user",
            "client_id": "unit-deadline.apps.example.test",
            "client_secret": "GOCSPX-" + "unitDeadlineMadeUp",
            "refresh_token": "1//" + "0unitDeadlineMadeUp",
        }
    )
    kind = KINDS["google_drive"]
    config = kind.check({"credentials_env": "CRED", "folder_id": "1FolderAbCdEfGh", "projects": []})
    source = kind.open(
        "drive", config, http=httpx.AsyncClient(transport=httpx.MockTransport(stalled)),
        env={"CRED": credential}, clock=frozen, sleep=asyncio.sleep, deadline=NOW + LEFT,
        since=NOW - timedelta(days=1),
    )  # fmt: skip
    with pytest.raises(SourceError, match="signing in within one pull's deadline"):
        asyncio.run(source.pull(None))


GRANOLA_NOTE = "not_" + "a1B2c3D4e5F6g7"
GRANOLA_LATE = "not_" + "z9Y8x7W6v5U4t3"


def granola_note(note_id: str) -> dict:
    return {
        "id": note_id,
        "title": "Weekly sync",
        "created_at": "2026-11-13T09:00:00Z",
        "updated_at": "2026-11-13T09:30:00Z",
        "deleted_at": None,
        "owner": {"name": "Ada Park", "email": "ada@example.test"},
        "attendees": [],
        "calendar_event": None,
        "summary_text": "We agreed.",
        "summary_markdown": None,
    }


def open_granola(handler, deadline=NOW + LEFT):
    kind = KINDS["granola"]
    config = kind.check({"key_env": "K", "projects": ["meetings"], "settle_minutes": 0})
    return kind.open(
        "granola", config, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        env={"K": "unit-deadline-made-up-key"}, clock=frozen, sleep=asyncio.sleep,
        deadline=deadline, since=NOW - timedelta(days=1),
    )  # fmt: skip


def test_a_granola_listing_that_drips_past_the_deadline_fails_the_pull_with_nothing_read():
    """The reviewer's drip.py: the pull used to take 6 s against a deadline of 1 s."""
    drip = Drip(b'{"notes": [', b'], "hasMore": false, "cursor": null}')
    source = open_granola(lambda r: httpx.Response(200, stream=drip))
    with pytest.raises(SourceError, match="could not be listed within one pull's bounds"):
        asyncio.run(source.pull(None))
    assert drip.sent < CHUNKS // 2


def test_a_granola_note_that_drips_past_the_deadline_leaves_what_was_finished():
    drip = Drip(json.dumps(granola_note(GRANOLA_LATE))[:-1].encode(), b"}")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/notes"):
            listed = [
                {k: granola_note(n)[k] for k in ("id", "created_at", "updated_at", "deleted_at")}
                for n in (GRANOLA_NOTE, GRANOLA_LATE)
            ]
            listed[1]["created_at"] = "2026-11-13T09:01:00Z"  # read second
            return httpx.Response(200, json={"notes": listed, "hasMore": False, "cursor": None})
        if request.url.path.endswith(GRANOLA_NOTE):
            return httpx.Response(200, json=granola_note(GRANOLA_NOTE))
        return httpx.Response(200, stream=drip)

    pulled = asyncio.run(open_granola(handler).pull(None))
    assert [d.source_ref for d in pulled.drafts] == [f"meeting:granola/{GRANOLA_NOTE}"]
    assert drip.sent < CHUNKS // 2


def test_a_langfuse_server_that_never_sends_its_headers_is_stopped_at_the_deadline():
    """Langfuse's own check after every chunk of a body could not see a request whose
    headers never came."""

    async def stalled(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(CHUNKS * DRIP)
        raise AssertionError("the answer was waited for past the deadline")

    kind = KINDS["langfuse"]
    config = kind.check(
        {
            "base_url": "https://langfuse.example.test",
            "public_key_env": "LF_PUBLIC",
            "secret_key_env": "LF_SECRET",
            "projects": ["traces"],
        }
    )
    source = kind.open(
        "langfuse", config, http=httpx.AsyncClient(transport=httpx.MockTransport(stalled)),
        env={"LF_PUBLIC": "pk-lf-unitDeadline", "LF_SECRET": "sk-lf-unitDeadline"},
        clock=frozen, sleep=asyncio.sleep, deadline=NOW + LEFT,
    )  # fmt: skip
    with pytest.raises(SourceError, match="one pull's bounds"):
        asyncio.run(source.pull(None))
