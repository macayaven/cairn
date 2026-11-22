"""Shared test helpers extracted from test_connector_check_unit."""

import json

import httpx

SECRET = "check-" + "made-up-" + "secret-4d2a91"


def item(n: int, **fields) -> dict:
    return {"id": f"V-{n}", "text": f"# Valve {n}\n\nValve {n} is shut.\n", **fields}


class Feed:
    """A faithful connector of `size` items, `page` an answer; `change` may alter an answer
    (the request's body, the number of the asking with a null cursor, the answer)."""

    def __init__(self, size: int = 5, page: int = 2) -> None:
        self.items = [item(n) for n in range(1, size + 1)]
        self.page = page
        self.firsts = 0
        self.change = None
        self.wrong = (401, {"error": "no"})
        self.requests: list[dict] = []
        self.bearers: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        self.bearers.append(request.headers["authorization"])
        if request.headers["authorization"] != f"Bearer {SECRET}":
            status, said = self.wrong
            return httpx.Response(status, json=said)
        offset = 0 if body["cursor"] is None else int(body["cursor"].removeprefix("after:"))
        self.firsts += offset == 0
        chunk = self.items[offset : offset + min(self.page, body["limit"])]
        nxt = offset + len(chunk)
        answer = {
            "protocol": 1,
            "items": chunk,
            "cursor": f"after:{nxt}",
            "more": nxt < len(self.items),
        }
        if self.change:
            answer = self.change(body, self.firsts, answer)
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)
