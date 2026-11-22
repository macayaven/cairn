"""Shared test helpers extracted from test_source_translated_layouts_unit."""

import asyncio

import httpx

from cairn.sources.runner import source_client
from support.sources.source_url import SECRET, Resolver, recording


def fetch(address: str, answers: list[str] | None = None, scheme: str = "https"):
    sent, transport = recording()
    resolve = Resolver({"t.fable.test": answers or []})

    async def run() -> httpx.Response:
        async with source_client(transport=transport, resolve=resolve) as client:
            return await client.get(address, headers={"authorization": f"Bearer {SECRET}"})

    return sent, lambda: asyncio.run(run())
