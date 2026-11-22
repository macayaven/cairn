"""Shared test helpers extracted from test_core_promise_title_unit."""

import json
import re

BASE = "http://127.0.0.1:8765"

SAME_ORIGIN = {"sec-fetch-site": "same-origin"}

MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}

PRE = re.compile(r'<details class="body">.*?<pre>(.*?)</pre>', re.S)


def mcp_page(client, key, slug, version=None) -> dict:
    arguments = {"project": "alpha", "slug": slug}
    if version is not None:
        arguments["version"] = version
    call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "page", "arguments": arguments},
    }
    answer = client.post("/mcp", headers={**MCP_HEADERS, "x-api-key": key}, json=call).json()
    assert answer["result"]["isError"] is False, answer
    return json.loads(answer["result"]["content"][0]["text"])
