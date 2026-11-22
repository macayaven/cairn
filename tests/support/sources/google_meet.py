"""Shared test helpers extracted from test_google_meet_unit."""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl

import httpx
import pytest

from cairn.sources import google_meet as m

VAR = "GOOGLE_UNIT_MEET"

CLIENT_ID = "unit-" + "meet-client" + ".apps.example.test"

SECRET = "GOCSPX-" + "unitMeet" + "MadeUpSecret"

REFRESH = "1//" + "0unitMeet" + "MadeUpRefresh"

TOKEN = "ya29." + "unitMeet" + "MadeUpAccessToken"

CREDENTIAL = json.dumps(
    {
        "type": "authorized_user",
        "client_id": CLIENT_ID,
        "client_secret": SECRET,
        "refresh_token": REFRESH,
    }
)

BASE = "https://meet.example.test/v2"

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)

SINCE = NOW - timedelta(days=1)

MINUTE, HOUR, DAY = timedelta(minutes=1), timedelta(hours=1), timedelta(days=1)

CANARY = "unit-" + "meet-body-" + "canary"


def settings(**given) -> dict:
    return {
        key: value
        for key, value in {"credentials_env": VAR, "projects": ["calls"], **given}.items()
        if value is not None
    }


def refusal(config) -> str:
    with pytest.raises(ValueError) as refused:
        m.check_config(config)
    return str(refused.value)


def z(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def cursor(**given) -> m.Cursor:
    base = {
        "mark": NOW,
        "waiting": {"conf1": m.Waiting(NOW - HOUR, True)},
        "settled": {"0123456789abcdef": 1_763_000_000},
        "parts": {"fedcba9876543210": (2, 1_763_000_000)},
    }
    return m.Cursor(**{**base, **given})


def person(pid, display, kind="signedinUser") -> m.Person:
    return m.Person(f"conferenceRecords/c1/participants/{pid}", display, kind)


def said(pid, text, seconds) -> m.Entry:
    return m.Entry(
        f"conferenceRecords/c1/participants/{pid}", text, NOW + timedelta(seconds=seconds)
    )


PEOPLE = {
    p.name: p
    for p in (
        person("p1", "Ada Park"),
        person("p2", "Brook Lane"),
        person("p3", None),
        person("p4", "Cy Moss", "anonymousUser"),
        person("p5", "dee@example.test", "anonymousUser"),
        person("p6", "+1 555-xxx-xx42", "phoneUser"),
    )
}


def meeting(**given) -> m.Meeting:
    base = {
        "cid": "c1",
        "start": datetime(2026, 11, 12, 9, 0, tzinfo=UTC),
        "end": datetime(2026, 11, 12, 10, 5, tzinfo=UTC),
        "space": "spaces/s1",
    }
    return m.Meeting(**{**base, **given})


def transcript(**given) -> m.Transcript:
    base = {
        "name": "conferenceRecords/c1/transcripts/t1",
        "state": "FILE_GENERATED",
        "start": datetime(2026, 11, 12, 9, 1, tzinfo=UTC),
        "end": datetime(2026, 11, 12, 10, 6, tzinfo=UTC),
    }
    return m.Transcript(**{**base, **given})


def PEOPLE_NAME(pid: str) -> str:
    return f"conferenceRecords/c1/participants/{pid}"


@dataclass
class Call:
    n: int
    start: datetime
    end: datetime | None
    code: str | None = "abc-defg-hij"
    transcripts: list = field(default_factory=list)  # (tid, state, entries)
    people: list = field(default_factory=lambda: [("p1", "Ada Park"), ("p2", "Brook Lane")])
    gone: set = field(default_factory=set)  # the endpoints that answer 404 for it

    @property
    def cid(self) -> str:
        return f"call{self.n:03d}"


class Stand:
    """The token endpoint and the Meet API, enough to test what the acceptance file does not;
    `answers` overrides an endpoint with a function of the request."""

    def __init__(self) -> None:
        self.now = NOW
        self.calls: dict[str, Call] = {}
        self.requests: list[httpx.Request] = []
        self.answers: dict[str, object] = {}
        self.slept: list[float] = []
        self.lists_ongoing = True

    def clock(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def add(self, n, start, end, *, state="FILE_GENERATED", **given) -> Call:
        transcripts = given.pop(
            "transcripts",
            [("t1", state, [("p1", "Hello.", start + MINUTE), ("p2", "Hi.", start + 2 * MINUTE)])],
        )
        call = Call(n, start, end, transcripts=transcripts, **given)
        self.calls[call.cid] = call
        return call

    def endpoint(self, path: str) -> tuple[str, list[str]]:
        parts = path.removeprefix("/v2/").split("/")
        shape = tuple("*" if i % 2 else p for i, p in enumerate(parts))
        names = {
            ("conferenceRecords",): "records",
            ("conferenceRecords", "*"): "record",
            ("conferenceRecords", "*", "transcripts"): "transcripts",
            ("conferenceRecords", "*", "transcripts", "*", "entries"): "entries",
            ("conferenceRecords", "*", "participants"): "participants",
            ("spaces", "*"): "space",
        }
        return names.get(shape, "other"), parts

    def of(self, kind: str) -> list[httpx.Request]:
        return [r for r in self.requests if self.endpoint(r.url.path)[0] == kind]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/token":
            assert dict(parse_qsl(request.content.decode()))["refresh_token"] == REFRESH
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3599})
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        kind, parts = self.endpoint(request.url.path)
        if kind in self.answers:
            return self.answers[kind](request)
        if kind == "records":
            bound = m.moment(request.url.params["filter"].split('"')[1])
            found = [
                c
                for c in self.calls.values()
                if c.start >= bound and (self.lists_ongoing or (c.end and c.end <= self.now))
            ]
            return httpx.Response(200, json={"conferenceRecords": [self.record(c) for c in found]})
        if kind == "space":
            call = self.calls[parts[1].removeprefix("space-")]
            if "space" in call.gone:
                return httpx.Response(404, json={"error": {"code": 404, "message": CANARY}})
            return httpx.Response(200, json={"name": parts[1], "meetingCode": call.code})
        call = self.calls[parts[1]]
        if kind in call.gone:
            return httpx.Response(404, json={"error": {"code": 404, "message": CANARY}})
        if kind == "record":
            return httpx.Response(200, json=self.record(call))
        if kind == "participants":
            people = [
                {
                    "name": f"conferenceRecords/{call.cid}/participants/{p}",
                    "signedinUser": {"displayName": d},
                }
                for p, d in call.people
            ]
            return httpx.Response(200, json={"participants": people})
        if kind == "transcripts":
            listed = [
                {
                    "name": f"conferenceRecords/{call.cid}/transcripts/{t}",
                    "state": s,
                    "startTime": z(call.start),
                }
                for t, s, _ in call.transcripts
            ]
            return httpx.Response(200, json={"transcripts": listed})
        (said,) = [e for t, _, e in call.transcripts if t == parts[3]]
        entries = [
            {
                "participant": f"conferenceRecords/{call.cid}/participants/{p}",
                "text": text,
                "startTime": z(at),
            }
            for p, text, at in said
        ]
        return httpx.Response(200, json={"transcriptEntries": entries})

    def record(self, call: Call) -> dict:
        body = {
            "name": f"conferenceRecords/{call.cid}",
            "startTime": z(call.start),
            "space": f"spaces/space-{call.cid}",
        }
        if call.end and call.end <= self.now:
            body["endTime"] = z(call.end)
        return body

    def source(self, *, since=SINCE, deadline=None, http=None, **given):
        http = http or httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return m.open_source(
            "meet",
            m.check_config(settings(base_url=BASE, **given)),
            http=http,
            env={VAR: CREDENTIAL},
            clock=self.clock,
            sleep=self.sleep,
            deadline=deadline,
            since=since,
            jitter=lambda: 0.5,
        )

    def pull(self, cursor=None, **given):
        self.requests.clear()
        return asyncio.run(self.source(**given).pull(cursor))


def refs(pulled) -> list[str]:
    return [d.source_ref for d in pulled.drafts]


def error(status: int, reason: str | None = None) -> httpx.Response:
    body = {"error": {"code": status, "message": CANARY, "status": "X"}}
    if reason:
        body["error"]["details"] = [{"@type": "x", "reason": reason}]
    return httpx.Response(status, json=body)
