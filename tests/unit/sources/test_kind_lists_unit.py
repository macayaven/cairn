"""Unit tests for K1: every list of the sources the server reads names every kind in KINDS.
The lists stopped at Gmail when Linear, GitHub and the connector came; each list below fails
when a kind in KINDS is missing from it, and WORDS fails when a kind has no words, so the
next kind cannot be forgotten the same way. The words are the sources guide's."""

import re

import pytest

from cairn.sources import KINDS
from support.guide_source import guide_source
from support.paths import REPO_ROOT

ROOT = REPO_ROOT

# What a list calls each kind, in the sources guide's words.
WORDS = {
    "langfuse": "Langfuse",
    "granola": "Granola",
    "google_meet": "Google Meet",
    "google_drive": "Google Drive",
    "google_calendar": "Google Calendar",
    "gmail": "Gmail",
    "linear": "Linear",
    "github": "GitHub",
    "connector": "a connector of your own",
}


def flat(text: str) -> str:
    return " ".join(text.split())


def between(text: str, start: str, end: str) -> str:
    """The text from `start` to the first `end` after it, both included."""
    found = re.search(re.escape(start) + r".*?" + re.escape(end), flat(text))
    assert found, start
    return found.group(0)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


LISTS = {
    # the operator's guide says the name itself
    "src/cairn/guides/run.md": lambda: between(
        read("src/cairn/guides/run.md"), "A source (", ") is read by the server itself"
    ),
    "README.md": lambda: between(read("README.md"), "sources the server reads itself (", ")"),
    "deploy/README.md": lambda: between(
        read("deploy/README.md"), "A source is a system the server reads notes from", "."
    ),
    "docs/pre-release.md": lambda: between(
        read("docs/pre-release.md"), "A source (", ") is read by the server"
    ),
    # the sources guide's opening: the list, then a paragraph for each later kind
    "src/cairn/guides/sources.md": lambda: flat(guide_source("sources").split("\n## ")[0]),
}


def test_every_kind_has_words_a_list_calls_it():
    assert set(WORDS) == set(KINDS)


@pytest.mark.parametrize("where", sorted(LISTS))
@pytest.mark.parametrize("kind", sorted(KINDS))
def test_each_list_of_the_sources_names_every_kind(where, kind):
    listed = LISTS[where]()
    assert WORDS.get(kind, kind) in listed, (where, kind, listed)
