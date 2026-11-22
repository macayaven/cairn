"""Shared test helpers extracted from test_release_check_unit."""

from __future__ import annotations

import importlib.util

import pytest

from support.paths import REPO_ROOT

ROOT = REPO_ROOT

_spec = importlib.util.spec_from_file_location("release_check", ROOT / "scripts/release_check.py")

release_check = importlib.util.module_from_spec(_spec)

_spec.loader.exec_module(release_check)

PYPROJECT = '[project]\nname = "x"\nversion = "1.2.3"\n'

CHANGELOG = """# Changelog

Words before the sections.

## Unreleased

## 1.2.3 - 2026-01-31

The text of the release.

- **One.** The first entry.
- Two.
<!-- 1.2.3: a later entry, when it lands -->

## 1.2.2 - 2025-12-01

- Older.
"""


def refused(tag: str, pyproject: str = PYPROJECT, changelog: str = CHANGELOG) -> str:
    with pytest.raises(release_check.Refused) as caught:
        release_check.check(tag, pyproject, changelog)
    line = str(caught.value)
    assert "\n" not in line
    return line
