"""T1 smoke test: the package imports and exposes a version string."""

import re

import cairn


def test_package_exposes_semver_version() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[a-z0-9.+-]*)?", cairn.__version__)
