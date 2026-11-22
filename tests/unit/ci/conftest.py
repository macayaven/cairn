"""Mark every test in this directory as `ci_meta`."""

from pathlib import Path

import pytest

HERE = Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.path.is_relative_to(HERE):
            item.add_marker(pytest.mark.ci_meta)
