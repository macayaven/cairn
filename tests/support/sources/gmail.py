"""Shared test helpers extracted from test_gmail_unit."""

import asyncio
import json
from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote

import httpx
import pytest

from cairn.sources import SourceError
from cairn.sources import gmail as g

VAR = "GOOGLE_UNIT_GMAIL"

CLIENT_ID = "unit-" + "gmail-client" + ".apps.example.test"

SECRET = "GOCSPX-" + "unitMail" + "MadeUpSecret"

REFRESH = "1//" + "0unitMail" + "MadeUpRefresh"

TOKEN = "ya29." + "unitMail" + "MadeUpAccessToken"

CREDENTIAL = json.dumps(
    {
        "type": "authorized_user",
        "client_id": CLIENT_ID,
        "client_secret": SECRET,
        "refresh_token": REFRESH,
    }
)

BASE = "https://gmail.example.test/gmail/v1"

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)

SINCE = NOW - timedelta(days=1)

MINUTE, HOUR, DAY = timedelta(minutes=1), timedelta(hours=1), timedelta(days=1)

CANARY = "unit-" + "gmail-body-" + "canary"

LABEL_ID = "unitlbl" + "7"


def settings(**given) -> dict:
    return {
        key: value
        for key, value in {
            "credentials_env": VAR,
            "label": "cairn",
            "projects": ["mail"],
            **given,
        }.items()
        if value is not None
    }


def b64(text: str | bytes, padded: bool = True) -> str:
    raw = text.encode() if isinstance(text, str) else text
    encoded = urlsafe_b64encode(raw).decode()
    return encoded if padded else encoded.rstrip("=")


def part(mime: str, text: str | None = None, *, filename: str = "", **body) -> dict:
    data = {"size": 0, **body}
    if text is not None:
        data["data"] = b64(text)
    return {"mimeType": mime, "filename": filename, "headers": [], "body": data}


def tree(mime: str, *parts: dict) -> dict:
    return {"mimeType": mime, "filename": "", "headers": [], "body": {"size": 0}, "parts": [*parts]}


def mail(n: int = 1, at: datetime = SINCE + HOUR, *, headers=None, payload=None, labels=None):
    """A message as messages.get gives it with format=full."""
    payload = payload or part("text/plain", f"Item {n} is agreed.")
    given = {"Subject": f"Item {n}", "From": "Ada Lovelace <ada@example.test>"}
    given.update(headers or {})
    payload = {**payload, "headers": [{"name": k, "value": v} for k, v in given.items()]}
    return {
        "id": f"unitm{n:04d}",
        "threadId": f"unitt{n:04d}",
        "labelIds": ["INBOX", LABEL_ID] if labels is None else labels,
        "internalDate": str(round(at.timestamp() * 1000)),
        "payload": payload,
    }


def read(item: dict) -> "g.Mail":
    return g.read_message(item["id"], json.dumps(item).encode())


def error(status: int, reason: str = "backendError") -> httpx.Response:
    item = {"domain": "global", "reason": reason, "message": CANARY}
    return httpx.Response(
        status, json={"error": {"errors": [item], "code": status, "message": CANARY}}
    )


class Stand:
    """The token endpoint and the three Gmail methods, enough to test what the acceptance file
    does not; `answers` overrides a method with a function of the request."""

    def __init__(self) -> None:
        self.now = NOW
        self.messages: list[dict] = []
        self.labels = [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": LABEL_ID, "name": "cairn", "type": "user"},
        ]
        self.requests: list[httpx.Request] = []
        self.slept: list[float] = []
        self.answers: dict = {}
        self.page = 500
        self.tick = timedelta(0)

    def clock(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def method(self, request: httpx.Request) -> str:
        path = request.url.path
        if path == "/token":
            return "token"
        if path.endswith("/labels"):
            return "labels"
        return "list" if path.endswith("/messages") else "get"

    def calls(self, method: str) -> list[httpx.Request]:
        return [r for r in self.requests if self.method(r) == method]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.now += self.tick
        method = self.method(request)
        if method == "token":
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3599})
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if method in self.answers:
            return self.answers[method](request)
        if method == "labels":
            return httpx.Response(200, json={"labels": self.labels})
        if method == "list":
            offset = int(request.url.params.get("pageToken", "0"))
            ids = [{"id": m["id"], "threadId": m["threadId"]} for m in self.messages]
            body: dict = {"messages": ids[offset : offset + self.page]}
            if offset + self.page < len(ids):
                body["nextPageToken"] = str(offset + self.page)
            return httpx.Response(200, json=body)
        wanted = unquote(request.url.path.rsplit("/", 1)[1])
        for item in self.messages:
            if item["id"] == wanted:
                return httpx.Response(200, json=item)
        return error(404, "notFound")

    def source(self, *, since=SINCE, deadline=None, **given):
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return g.open_source(
            "mail", g.check_config(settings(base_url=BASE, **given)), http=http,
            env={VAR: CREDENTIAL}, clock=self.clock, sleep=self.sleep, deadline=deadline,
            since=since, jitter=lambda: 0.5,
        )  # fmt: skip

    def pull(self, cursor=None, **given):
        return asyncio.run(self.source(**given).pull(cursor))

    def failed(self, cursor=None, **given) -> str:
        with pytest.raises(SourceError) as failed:
            self.pull(cursor, **given)
        return str(failed.value)


def refs(pulled) -> list[str]:
    return [d.source_ref for d in pulled.drafts]
