"""Shared test helpers extracted from test_source_url_unit."""

import httpx

from cairn.sources import (
    connector,
    github,
    google_api,
    google_meet,
    granola,
    langfuse,
    linear,
)

CHECKERS = {
    "github": (github._check_base_url, "base_url"),
    "linear": (linear._check_base_url, "base_url"),
    "granola": (granola._check_base_url, "base_url"),
    "langfuse": (langfuse._check_base_url, "base_url"),
    "google": (lambda v: google_api.check_base_url(v, "the Gmail API"), "base_url"),
    "google_meet": (google_meet._check_base_url, "base_url"),
    "connector": (connector.check_url, "url"),
}


class Resolver:
    """A resolver handed in: answers from a table, and remembers what it was asked."""

    def __init__(self, table: dict[str, list[str]] | None = None, fails: bool = False) -> None:
        self.table, self.fails, self.asked = table or {}, fails, []

    async def __call__(self, host: str, port: int) -> list[str]:
        self.asked.append((host, port))
        if self.fails:
            raise OSError("no such name")
        return self.table.get(host, ["10.0.0.5"])


def recording():
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    return sent, httpx.MockTransport(handler)


SECRET = "Zx9Qw3Er7Ty1Ui5Op2As8Df4Gh6Jk0Lm"
