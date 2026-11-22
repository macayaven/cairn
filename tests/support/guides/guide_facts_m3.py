"""Shared test helpers extracted from test_guide_facts_m3_unit."""

import json

from cairn.llm import Reply

SIX = "".join(f"- Crate {n} sits on shelf {n}.\n" for n in range(6))


def answer(page) -> Reply:
    return Reply(text=json.dumps({"order": list(range(len(page.claims), 0, -1)), "same": []}))
