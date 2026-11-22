"""Shared test helpers extracted from test_granola_unit."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from cairn.sources import granola as g

KEY = "grn_" + "unitMadeUpKey42"  # never a real key

VAR = "GRANOLA_UNIT_KEY"

BASE = "https://granola.example.test/v1"

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)

SINCE = NOW - timedelta(days=10)


def settings(**given) -> dict:
    return {"key_env": VAR, "projects": ["meetings"], "base_url": BASE, **given}


def note_body(**given) -> dict:
    body = {
        "id": "not_" + "Abcdefghij0123",
        "object": "note",
        "title": "Payments weekly",
        "owner": {"name": "Dee Quinn", "email": "dee@example.test"},
        "created_at": "2026-11-10T21:00:00Z",
        "updated_at": "2026-11-11T01:00:00Z",
        "deleted_at": None,
        "web_url": "https://notes.granola.example.test/d/x",
        "calendar_event": None,
        "attachments": [],
        "attendees": [
            {"name": "Ada Park", "email": "ada@example.test"},
            {"name": None, "email": "cy@example.test"},
        ],
        "folder_membership": [],
        "summary_text": "Plain.",
        "summary_markdown": "### Decisions\n\n- Ship it.",
        "private_notes_text": "aside",
        "private_notes_markdown": "aside",
        "transcript": None,
    }
    return {**body, **given}


def meeting(**given) -> "g.Meeting":
    return g.parse_note(note_body(**given), "not_" + "Abcdefghij0123")


def item(name, text, second, **speaker) -> dict:
    start = NOW + timedelta(seconds=second)
    stamp = start.isoformat().replace("+00:00", "Z")
    return {
        "speaker": {"source": "microphone", **({"name": name} if name else {}), **speaker},
        "text": text,
        "start_time": stamp,
        "end_time": stamp,
    }


def nid(n: int) -> str:
    return f"not_unit{n:010d}"


def z(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


class Stand:
    """List notes, Get note and Get transcript, inclusive bounds, newest first; `answers`
    overrides an endpoint ("list", "get", "transcript") with a function of the request."""

    def __init__(self) -> None:
        self.now = NOW
        self.notes: dict[str, dict] = {}
        self.segments: dict[str, list] = {}
        self.requests: list[httpx.Request] = []
        self.answers: dict[str, object] = {}
        self.slept: list[float] = []

    def clock(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def add(self, n: int, created: datetime, updated: datetime | None = None, **given) -> str:
        note_id = given.pop("id", nid(n))
        self.notes[note_id] = {
            **note_body(id=note_id, title=f"Meeting {n}"),
            "created_at": z(created),
            "updated_at": z(updated or created + timedelta(minutes=10)),
            **given,
        }
        return note_id

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["authorization"] == f"Bearer {KEY}"
        path = request.url.path.removeprefix("/v1")
        endpoint = "list" if path == "/notes" else "transcript" if path.endswith("t") else "get"
        if endpoint in self.answers:
            return self.answers[endpoint](request)
        params = request.url.params
        if endpoint == "list":
            found = [
                summary
                for summary in self.notes.values()
                if all(
                    datetime.fromisoformat(summary[f"{name}_at"]) >= datetime.fromisoformat(bound)
                    for name, bound in (
                        (key.removesuffix("_after"), value)
                        for key, value in params.items()
                        if key.endswith("_after")
                    )
                )
            ]
            found.sort(key=lambda s: (s["created_at"], s["id"]), reverse=True)
            start = int(params.get("cursor", "0"))
            listed = ("id", "object", "title", "owner", "created_at", "updated_at", "deleted_at")
            page = [{k: s[k] for k in listed} for s in found[start : start + 30]]
            more = start + 30 < len(found)
            return httpx.Response(
                200,
                json={"notes": page, "hasMore": more, "cursor": str(start + 30) if more else None},
            )
        note_id = path.split("/")[2]
        if endpoint == "get":
            return httpx.Response(200, json=self.notes[note_id])
        items = self.segments.get(note_id, [])
        start = int(params.get("cursor", "0"))
        more = start + 100 < len(items)
        page = {"transcript": items[start : start + 100], "hasMore": more}
        return httpx.Response(200, json={**page, "cursor": str(start + 100) if more else None})

    def source(self, *, since=SINCE, deadline=None, http=None, **given) -> "g.GranolaSource":
        checked = g.check_config(settings(**given))
        http = http or httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return g.open_source(
            "meet", checked, http=http, env={VAR: KEY}, clock=self.clock, sleep=self.sleep,
            deadline=deadline, since=since,
        )  # fmt: skip

    def pull(self, cursor=None, **given):
        return asyncio.run(self.source(**given).pull(cursor))


def origins(pulled) -> list[str]:
    return [d.source_ref for d in pulled.drafts]
