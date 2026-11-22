"""Shared test helpers extracted from test_google_calendar_unit."""

import asyncio
import json
from datetime import UTC, date, datetime, time, timedelta, timezone

import httpx

from cairn.sources import google_calendar as c

VAR = "GOOGLE_UNIT_CALENDAR"

CLIENT_ID = "unit-" + "calendar-client" + ".apps.example.test"

SECRET = "GOCSPX-" + "unitCal" + "MadeUpSecret"

REFRESH = "1//" + "0unitCal" + "MadeUpRefresh"

TOKEN = "ya29." + "unitCal" + "MadeUpAccessToken"

CREDENTIAL = json.dumps(
    {
        "type": "authorized_user",
        "client_id": CLIENT_ID,
        "client_secret": SECRET,
        "refresh_token": REFRESH,
    }
)

BASE = "https://calendar.example.test/calendar/v3"

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)

SINCE = NOW - timedelta(days=1)

MINUTE, HOUR, DAY = timedelta(minutes=1), timedelta(hours=1), timedelta(days=1)


def settings(**given) -> dict:
    return {
        key: value
        for key, value in {"credentials_env": VAR, "projects": ["calendar"], **given}.items()
        if value is not None
    }


def z(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def timed(n: int, start: datetime, end: datetime, **fields) -> dict:
    """An event as Google lists it, with the fields given."""
    return {
        "kind": "calendar#event",
        "id": f"unitev{n:04d}",
        "status": "confirmed",
        "summary": f"Event {n}",
        "start": {"dateTime": z(start)},
        "end": {"dateTime": z(end)},
        **fields,
    }


def all_day(n: int, first: date, after: date, **fields) -> dict:
    return {
        "kind": "calendar#event",
        "id": f"unitev{n:04d}",
        "status": "confirmed",
        "summary": f"Day {n}",
        "start": {"date": first.isoformat()},
        "end": {"date": after.isoformat()},
        **fields,
    }


def me(answer: str, **more) -> dict:
    return {"email": "ada@example.test", "self": True, "responseStatus": answer, **more}


def full_event(**changes) -> dict:
    start = datetime(2026, 11, 12, 9, 0, tzinfo=timezone(timedelta(hours=2)))
    item = timed(
        1,
        start,
        start + 90 * MINUTE,
        summary="Quarterly planning",
        location="Harbour Street,\nroom 4",
        organizer={"email": "cy@example.test", "displayName": "Cy Moss"},
        attendees=[
            me("accepted", displayName="Ada Park"),
            {"email": "brook@example.test", "displayName": "Brook Lane"},
            {"email": "board@resource.example.test", "displayName": "Board", "resource": True},
            {"email": "guest@example.test"},
            {"email": "odd@example.test", "displayName": "odd@example.test"},
        ],
        conferenceData={
            "conferenceId": "abc-defg-hij",
            "conferenceSolution": {"key": {"type": "hangoutsMeet"}},
        },
        description="<p>Bring the <b>figures</b>.</p><ul><li>Budget</li></ul>",
    )
    item["start"] = {"dateTime": "2026-11-12T09:00:00+02:00"}
    item["end"] = {"dateTime": "2026-11-12T10:30:00+02:00"}
    item.update(changes)
    return item


class Stand:
    """The token endpoint and events.list, enough to test what the acceptance file does not;
    `answer` overrides events.list with a function of the request."""

    def __init__(self) -> None:
        self.now = NOW
        self.items: list[dict] = []
        self.requests: list[httpx.Request] = []
        self.slept: list[float] = []
        self.answer = None
        self.page = 2500
        self.top: dict = {"timeZone": "UTC", "accessRole": "owner"}
        self.later_top: dict | None = None  # what pages after the first say instead
        self.tick = timedelta(0)

    def clock(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def lists(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/events")]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.now += self.tick
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3599})
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if self.answer is not None:
            return self.answer(request)
        params = request.url.params
        offset = int(params.get("pageToken", "0"))
        low, high = (datetime.fromisoformat(params[k]) for k in ("timeMin", "timeMax"))
        listed = [i for i in self.items if self.bounds(i)[1] > low and self.bounds(i)[0] < high]
        top = self.top if offset == 0 or self.later_top is None else self.later_top
        body = {"kind": "calendar#events", **top}
        body["items"] = listed[offset : offset + self.page]
        if offset + self.page < len(listed):
            body["nextPageToken"] = str(offset + self.page)
        return httpx.Response(200, json=body)

    def bounds(self, item: dict) -> tuple[datetime, datetime]:
        """Where events.list bounds an event: its times, or an all-day event's days at
        midnight in the calendar's zone (UTC when the stand names none)."""
        zone = c.zone_of(self.top.get("timeZone")) or UTC
        found = []
        for key in ("start", "end"):
            when = item[key]
            if "dateTime" in when:
                found.append(datetime.fromisoformat(when["dateTime"]))
            else:
                found.append(datetime.combine(date.fromisoformat(when["date"]), time(0), zone))
        return found[0], found[1]

    def source(self, *, since=SINCE, deadline=None, **given):
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return c.open_source(
            "cal", c.check_config(settings(base_url=BASE, **given)), http=http,
            env={VAR: CREDENTIAL}, clock=self.clock, sleep=self.sleep, deadline=deadline,
            since=since, jitter=lambda: 0.5,
        )  # fmt: skip

    def pull(self, cursor=None, **given):
        return asyncio.run(self.source(**given).pull(cursor))


def refs(pulled) -> list[str]:
    return [d.source_ref for d in pulled.drafts]
