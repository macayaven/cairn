"""Numbers an operator can read (Q1 C): the install's numbers put together, the responses an
app sent by class, and the line `serve` logs with them (create_app's `stats_every`).

`numbers(read, path, schema)` puts together what cairn.store.numbers counts, one part at a
time through `read`, with the database's file sizes and the free space on its disk
(`shutil.disk_usage` of its folder, looked up each time): for the install, `schema`,
`db_bytes`, `wal_bytes` (0 without a "-wal" file), `free_bytes`, `low_disk` (free_bytes
under twice db_bytes, what a compaction needs) and the totals of COUNTS, `newest_note` and
`sources`; under `workspaces`, the same counts for each workspace by its id. Counts, sizes
and times only: no text, title, name, address, label or reason.

The line: a StatsLog logs, on the logger STATS_LOGGER (not the access log's), `stats` and a
space, then the install's numbers without `workspaces`, and `requests`, the responses the
app sent since the last line by class (2xx, 3xx, 4xx, 5xx, and 429, counted in 4xx too;
Responses, counted by CountResponses around the whole app). The first line comes at start,
then one after each wait of `every` minutes, without end. The numbers are gathered on a
worker thread, never on the event loop, each count in a read of its own
(Store.numbers_part), so the store is held no longer than each count. A pass that fails
logs its error's class, never its message, and the next pass runs. Under low disk the line
is at WARNING, followed by LOW_DISK at WARNING; else INFO. No route serves the numbers.
"""

import asyncio
import json
import logging
import os
import shutil
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from cairn.store import Store

STATS_LOGGER = "cairn.stats"
WORD = "stats"  # the line's first word
COUNTS = ("projects", "notes", "pages", "page_versions", "waiting", "keys", "agents")
CLASSES = ("2xx", "3xx", "4xx", "5xx", "429")
LOW_DISK = (
    "free space on the database's disk is under twice the database's size: writes can fail"
    " when it runs out, and a compaction or a backup copy of the database cannot run; free"
    " some space or move the database to a larger disk"
)
log = logging.getLogger(STATS_LOGGER)


def _blank() -> dict[str, Any]:
    return {**dict.fromkeys(COUNTS, 0), "newest_note": None, "sources": {}}


def numbers(read: Callable[[str], list[tuple]], path: str, schema: int) -> dict[str, Any]:
    """The install's numbers and each workspace's (the module's docstring), counted through
    `read` (a part's name to its rows, cairn.store.numbers.part_rows), for the database at
    `path` ("" for one in memory) of schema `schema`."""
    folder = os.path.dirname(os.path.abspath(path)) if path else os.getcwd()
    db_bytes = os.path.getsize(path) if path else 0
    wal = f"{path}-wal"
    wal_bytes = os.path.getsize(wal) if path and os.path.exists(wal) else 0
    free = shutil.disk_usage(folder).free  # looked up now, through the module (a test swaps it)
    per: dict[str, dict[str, Any]] = {account: _blank() for account, _ in read("accounts")}
    for part in (*COUNTS, "newest_note"):
        for account, value in read(part):
            per.setdefault(account, _blank())[part] = value
    for account, kind, on, failed in read("sources"):
        per.setdefault(account, _blank())["sources"][kind] = {"on": on, "failed": failed}
    install: dict[str, Any] = {
        "schema": schema,
        "db_bytes": db_bytes,
        "wal_bytes": wal_bytes,
        "free_bytes": free,
        "low_disk": free < 2 * db_bytes,
        **{name: sum(mine[name] for mine in per.values()) for name in COUNTS},
        "newest_note": max(
            (mine["newest_note"] for mine in per.values() if mine["newest_note"]), default=None
        ),
        "sources": {},
    }
    for mine in per.values():
        for kind, tally in mine["sources"].items():
            total = install["sources"].setdefault(kind, {"on": 0, "failed": 0})
            total["on"] += tally["on"]
            total["failed"] += tally["failed"]
    install["sources"] = dict(sorted(install["sources"].items()))
    install["workspaces"] = per
    return install


class Responses:
    """The responses an app sent since they were last taken, by class."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts = dict.fromkeys(CLASSES, 0)

    def count(self, status: int) -> None:
        with self._lock:
            if 200 <= status < 600 and status // 100 != 1:
                self._counts[f"{status // 100}xx"] += 1
            if status == 429:
                self._counts["429"] += 1

    def taken(self) -> dict[str, int]:
        """The counts since the last time, which start again from 0."""
        with self._lock:
            counts, self._counts = self._counts, dict.fromkeys(CLASSES, 0)
        return counts


class CountResponses:
    """ASGI middleware around the whole app: each HTTP response's status, as it starts, goes
    to `responses`; a request that ends in an error before a response starts counts as 500,
    the answer Starlette then sends."""

    def __init__(self, app: Any, responses: Responses) -> None:
        self.app, self.responses = app, responses

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def sending(message: Any) -> None:
            nonlocal started
            if message["type"] == "http.response.start" and not started:
                started = True
                self.responses.count(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, sending)
        except BaseException:
            if not started:
                self.responses.count(500)
            raise


class StatsLog:
    """The line `serve` logs (the module's docstring), every `every` minutes after the first,
    awaiting `wait(seconds)` between passes (asyncio.sleep when None)."""

    def __init__(
        self,
        store: Store,
        every: float,
        responses: Responses,
        wait: Callable[[float], Awaitable[Any]] | None = None,
    ) -> None:
        self.store, self.every, self.responses = store, every, responses
        self.wait = wait or asyncio.sleep
        self._gathering = threading.Lock()  # held while a pass reads the store

    def gather(self) -> dict[str, Any]:
        """The install's numbers, without `workspaces`: on a worker thread, each count in a
        read of its own."""
        from cairn.store import SCHEMA_VERSION

        with self._gathering:
            found = numbers(self.store.numbers_part, self.store.database_file(), SCHEMA_VERSION)
        found.pop("workspaces")
        return found

    def stop(self) -> None:
        """Wait for a pass that is reading the store: after it, none reads it again."""
        with self._gathering:
            pass

    async def one_pass(self) -> None:
        try:
            found = await asyncio.to_thread(self.gather)
        except Exception as error:  # its class only: a message may name a path or a value
            log.error("%s pass failed: %s", WORD, type(error).__name__)
            return
        found["requests"] = self.responses.taken()
        low = found["low_disk"]
        log.log(logging.WARNING if low else logging.INFO, "%s %s", WORD, json.dumps(found))
        if low:
            log.warning(LOW_DISK)

    async def loop(self) -> None:
        while True:
            await self.one_pass()
            await self.wait(self.every * 60)
