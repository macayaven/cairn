"""Shared test helpers extracted from test_sources_runner_unit."""

import asyncio
from typing import ClassVar

import httpx
import pytest

from cairn.sources import (
    KINDS,
    NoteDraft,
    SourceError,
    SourceKind,
    run_source,
)
from cairn.store import Store

NOTE = "# Retries\n\nQuill retries a failed charge 3 times.\n"


class Fake:
    """A source whose pull answers what the test put in `answers` (or raises it)."""

    answers: ClassVar[list] = []
    cursors: ClassVar[list] = []  # the cursor each pull started from
    deadlines: ClassVar[list] = []  # the deadline each source was opened with
    during_pull = None  # called inside pull, to change the world while a run is under way
    secrets = ("fake/secret+value=",)  # what the source authenticates with

    def __init__(self, name: str) -> None:
        self.name = name

    async def pull(self, cursor):
        Fake.cursors.append(cursor)
        if Fake.during_pull:
            Fake.during_pull()
        answer = Fake.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def check(config):
    if not isinstance(config.get("projects"), list):
        raise ValueError("projects")
    return dict(config)


def opened(name, config, *, http, env, clock=None, sleep=None, deadline=None, since=None):
    if "FAKE_KEY" not in env:
        raise SourceError("set FAKE_KEY in the environment")
    Fake.deadlines.append(deadline)
    return Fake(name)


@pytest.fixture(autouse=True)
def fake_kind(monkeypatch):
    monkeypatch.setitem(KINDS, "fake", SourceKind(check=check, open=opened, via="fake"))
    Fake.answers, Fake.during_pull, Fake.cursors, Fake.deadlines = [], None, [], []


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    s.create_project("api")
    s.add_source("feed", "fake", {"projects": ["api"]})
    yield s
    s.close()


def draft(project="api", text=NOTE, ref="https://example.test/1"):
    return NoteDraft(project=project, title="Retries", text=text, source_ref=ref, label="fake")


def run(store, name="feed", env=None):
    return asyncio.run(
        run_source(
            store, name, http=httpx.AsyncClient(), env={"FAKE_KEY": "x"} if env is None else env
        )
    )
