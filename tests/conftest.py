"""Shared by every test: nothing from the developer's shell decides a result.

`serve` reads its settings from CAIRN_* variables, and some of them stop it at start when
they hold a value it cannot use (CAIRN_ALLOWED_EMAILS without --sign-in). A shell that
exports one for a real install would fail tests that have nothing to do with it. Each test
starts without any of them and sets what it needs itself.
"""

import os

import pytest


@pytest.fixture(autouse=True)
def no_cairn_settings_from_the_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [name for name in os.environ if name.startswith("CAIRN_")]:
        monkeypatch.delenv(name)
