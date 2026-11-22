"""Shared test helpers extracted from test_slack_link_unit."""

from datetime import UTC, datetime
from urllib.parse import parse_qsl

import httpx
from starlette.testclient import TestClient

from cairn.identity import GitHub
from cairn.server import SignIn, create_app
from cairn.store import Store
from cairn.web.slack import Slack
from support.cookie_names import session_cookie

BASE = "https://cairn.example.test"

HOST = "cairn.example.test"

KEY = bytes(range(32))

ISSUER = "https://sso.example.test"

NOW = datetime(2031, 3, 4, 9, tzinfo=UTC)

SECRET = "unit-signing-" + "0f" * 8

TOKEN = "xoxb-" + "unit-" + "0123456789"

TEAM = "T0FABLE01"

BOT = "U0FABLEBOT"

GUS = "U0GUS0001"


class World:
    def __init__(self, tmp_path) -> None:
        self.store = Store(tmp_path / "cairn.db", clock=lambda: NOW)
        self.users: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict]] = []

        def slack(request: httpx.Request) -> httpx.Response:
            fields = dict(parse_qsl(request.content.decode()))
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            self.calls.append((request.url.path, token, fields))
            user = self.users.get(fields.get("user", ""))
            if user is None:
                return httpx.Response(200, json={"ok": False, "error": "user_not_found"})
            return httpx.Response(200, json={"ok": True, "user": user})

        sign_in = SignIn(
            providers=(GitHub("gh-id", "gh-secret"),),
            http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
            join="personal",
        )
        switch = Slack(
            key=KEY, http=lambda: httpx.AsyncClient(transport=httpx.MockTransport(slack))
        )
        self.app = create_app(self.store, public_url=BASE, sign_in=sign_in, slack=switch)
        made = self.store.sign_in(
            "sso", "sub-gus", "gus@example.test", "Gus", issuer=ISSUER, join="personal"
        )
        self.gus = {"id": made.user.id, "account": made.account_id, "token": made.token}
        self.view = self.store.for_account(made.account_id, made.user.id)
        self.install = self.view.make_slack_install().id
        self.view.ready_slack_install(
            self.install, signing_secret=SECRET, bot_token=TOKEN, team_id=TEAM,
            team_domain="fable-works", bot_user_id=BOT,
        )  # fmt: skip
        self.client = TestClient(self.app, base_url=BASE)
        self.client.cookies.set(session_cookie("https"), made.token, domain=HOST, path="/")

    def offer(self) -> str:
        return self.view.offer_slack_link(self.install, GUS)

    def close(self) -> None:
        self.client.close()
        self.store.close()
