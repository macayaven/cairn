"""Shared test helpers extracted from test_google_drive_unit."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl

import httpx

from cairn.sources import google_drive as d

VAR = "GOOGLE_UNIT_DRIVE"

CLIENT_ID = "unit-" + "drive-client" + ".apps.example.test"

SECRET = "GOCSPX-" + "unitDrive" + "MadeUpSecret"

REFRESH = "1//" + "0unitDrive" + "MadeUpRefresh"

TOKEN = "ya29." + "unitDrive" + "MadeUpAccessToken"

CREDENTIAL = json.dumps(
    {
        "type": "authorized_user",
        "client_id": CLIENT_ID,
        "client_secret": SECRET,
        "refresh_token": REFRESH,
    }
)

BASE = "https://drive.example.test/drive/v3"

FOLDER = "1UnitFolder" + "AbCdEfGh"

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)

SINCE = NOW - timedelta(days=3)

MINUTE, HOUR, DAY = timedelta(minutes=1), timedelta(hours=1), timedelta(days=1)


def settings(**given) -> dict:
    return {
        key: value
        for key, value in {
            "credentials_env": VAR,
            "folder_id": FOLDER,
            "projects": ["specs"],
            **given,
        }.items()
        if value is not None
    }


def document(**given) -> "d.Document":
    fields = {
        "id": "1UnitDoc" + "XyZwVuTsRq",
        "name": "Ledger design",
        "mime": d.DOC,
        "modified": datetime(2026, 11, 12, 9, 30, 41, tzinfo=UTC),
        "size": None,
        "link": "https://docs.google.com/document/d/1UnitDoc/edit?usp=drivesdk#h",
        "editor": "Ada Park",
        "can_download": True,
    }
    return d.Document(**{**fields, **given})


def paragraphs(count: int, start: int = 0) -> list[str]:
    return [f"Paragraph {k:05d} of the ledger." for k in range(start, start + count)]


def doc(n: int, modified: datetime, **given) -> "d.Document":
    return document(id=f"1UnitDoc{n:05d}" + "XyZw", modified=modified, **given)


def key(n: int) -> str:
    return d.digest(doc(n, NOW).id)


class Stand:
    """The token endpoint and the Drive API, enough to test what the acceptance file does
    not; `answers` overrides an endpoint ("folder", "list", "export", "media") with a function
    of the request, or a list of them taken in turn."""

    def __init__(self) -> None:
        self.now = NOW
        self.files: dict[str, dict] = {}
        self.texts: dict[str, bytes] = {}
        self.requests: list[httpx.Request] = []
        self.answers: dict[str, object] = {}
        self.slept: list[float] = []
        self.folder = {"id": FOLDER, "mimeType": d.FOLDER_TYPE, "trashed": False}
        self.folder["capabilities"] = {"canListChildren": True}
        self.page = 1000

    def clock(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def add(self, n: int, modified: datetime, text: str = "Some text.\n", **given) -> str:
        file_id = doc(n, modified).id
        self.files[file_id] = {
            "id": file_id,
            "name": f"Note {n}",
            "mimeType": d.DOC,
            "modifiedTime": modified.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "webViewLink": f"https://docs.example.test/d/{file_id}/edit?resourcekey=k",
            "capabilities": {"canDownload": True},
            **given,
        }
        self.texts[file_id] = text.encode()
        return file_id

    def endpoint(self, request: httpx.Request) -> str:
        path = request.url.path.removeprefix("/drive/v3")
        if path == "/files":
            return "list"
        if path.endswith("/export"):
            return "export"
        if request.url.params.get("alt") == "media":
            return "media"
        return "folder" if path == f"/files/{FOLDER}" else "other"

    def of(self, kind: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path != "/token" and self.endpoint(r) == kind]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/token":
            assert dict(parse_qsl(request.content.decode()))["refresh_token"] == REFRESH
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3599})
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        kind = self.endpoint(request)
        answer = self.answers.get(kind)
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if answer is not None:
            return answer(request)
        if kind == "folder":
            return httpx.Response(200, json=self.folder)
        if kind == "list":
            ordered = sorted(self.files.values(), key=lambda f: f["id"])
            start = int(request.url.params.get("pageToken", "0"))
            body: dict = {"files": ordered[start : start + self.page]}
            if start + self.page < len(ordered):
                body["nextPageToken"] = str(start + self.page)
            return httpx.Response(200, json=body)
        file_id = request.url.path.split("/")[4]
        return httpx.Response(200, content=self.texts[file_id])

    def source(self, *, since=SINCE, deadline=None, http=None, **given):
        http = http or httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return d.open_source(
            "drive",
            d.check_config(settings(base_url=BASE, **given)),
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
    return [draft.source_ref for draft in pulled.drafts]
