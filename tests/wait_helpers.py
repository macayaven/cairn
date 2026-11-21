"""Bounded synchronization for asyncio tasks, SQLite writes and subprocess checks."""

import asyncio
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing


async def next_loop_turn() -> None:
    """Let callbacks already scheduled on this loop run."""
    ready = asyncio.Event()
    asyncio.get_running_loop().call_soon(ready.set)
    await ready.wait()


async def wait_for_event(event: asyncio.Event, what: str, seconds: float = 5.0) -> None:
    try:
        await asyncio.wait_for(event.wait(), seconds)
    except TimeoutError:
        raise AssertionError(f"{what}: not within {seconds:g} s") from None


def poll_until(check, what: str, seconds: float = 5.0, *, every: float = 0.01):
    deadline = time.monotonic() + seconds
    pause = threading.Event()
    while True:
        result = check()
        if result:
            return result
        left = deadline - time.monotonic()
        if left <= 0:
            raise AssertionError(f"{what}: not within {seconds:g} s")
        pause.wait(min(every, left))


async def poll_until_async(check, what: str, seconds: float = 5.0, *, every: float = 0.01):
    loop = asyncio.get_running_loop()
    try:
        async with asyncio.timeout(seconds):
            while True:
                result = await check()
                if result:
                    return result
                next_poll = asyncio.Event()
                wake = loop.call_later(every, next_poll.set)
                try:
                    await next_poll.wait()
                finally:
                    wake.cancel()
    except TimeoutError:
        raise AssertionError(f"{what}: not within {seconds:g} s") from None


def while_database_locked(db, store, call, then):
    """Advance the clock after the worker reaches SQLite's BEGIN, with the lock held."""
    began = threading.Event()

    def trace(statement: str) -> None:
        if statement == "BEGIN IMMEDIATE":
            began.set()

    with closing(sqlite3.connect(db, isolation_level=None)) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        store._con.set_trace_callback(trace)
        try:
            with ThreadPoolExecutor(1) as pool:
                result = pool.submit(call)
                try:
                    assert began.wait(10), "the operation did not start its write transaction"
                    then()
                finally:
                    blocker.execute("COMMIT")
                return result.result(timeout=30)
        finally:
            store._con.set_trace_callback(None)
