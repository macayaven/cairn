"""Shared by every test: nothing from the developer's shell decides a result.

`serve` reads its settings from CAIRN_* variables, and some of them stop it at start when
they hold a value it cannot use (CAIRN_ALLOWED_EMAILS without --sign-in). A shell that
exports one for a real install would fail tests that have nothing to do with it. Each test
starts without any of them and sets what it needs itself.

The tests in tests/browser drive a real browser, which most people have not installed: they
are collected only with `--run-browser` (`uv run pytest tests/browser --run-browser`), so
every other run, the whole suite included, stays offline and needs no browser.
"""

import os
from pathlib import Path

import pytest

BROWSER_TESTS = Path(__file__).parent / "browser"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-browser",
        action="store_true",
        help="collect the browser tests in tests/browser (they need Playwright's Chromium)",
    )


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    if collection_path.is_relative_to(BROWSER_TESTS) and not config.getoption("--run-browser"):
        return True  # never enter or import tests/browser without --run-browser
    return None


PERF_ACCEPTANCE_FILES = frozenset({"test_g1_gate_bounds.py", "test_t5c_structure.py"})
TEST_HARNESS_ENV = frozenset({"CAIRN_FUZZ_PROFILE", "CAIRN_REQUIRE_TOFU"})


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Tag fixed-contract timing acceptance modules and deselect browser tests unless opted in."""
    for item in items:
        if item.path.name in PERF_ACCEPTANCE_FILES:
            item.add_marker(pytest.mark.perf)
    if config.getoption("--run-browser"):
        return
    browser = [item for item in items if item.path.is_relative_to(BROWSER_TESTS)]
    if browser:
        config.hook.pytest_deselected(items=browser)
        items[:] = [item for item in items if not item.path.is_relative_to(BROWSER_TESTS)]


@pytest.fixture(scope="session", autouse=True)
def every_page_keeps_the_names_its_frame_reads():
    """Every page any test renders (cairn.web.templating._page, the one way a page is made)
    is checked as it is made: no view hands its template a name that a template global
    holds, but those a page sets for itself (templating.PER_PAGE): base.html's footer reads
    the global `version`, and a page that passed its own would show it there. And the
    footer of a page with no brand names the product and the software's version."""
    from markupsafe import escape

    from cairn import __version__
    from cairn.web import templating

    made = templating.templates.get_template
    reserved = set(templating.templates.globals) - templating.PER_PAGE

    class Checked:
        def __init__(self, name: str, template) -> None:
            self.name, self.template = name, template

        def __getattr__(self, attr: str):  # what Jinja reads of a template it extends
            return getattr(self.template, attr)

        def render(self, *given, **named):
            context = dict(*given, **named)
            shadowed = sorted(set(context) & reserved)
            assert not shadowed, f"{self.name} is handed {shadowed}, a template global's names"
            html = self.template.render(context)
            if '<footer class="colophon">' in html and context.get("brand") is None:
                product = context.get("product") or templating.product_of(None)
                said = f"<span>{escape(product.name)} {escape(__version__)}</span>"
                assert said in html.split('<footer class="colophon">', 1)[1], self.name
            return html

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            templating.templates,
            "get_template",
            lambda name, *a, **k: Checked(name, made(name, *a, **k)),
        )
        yield


@pytest.fixture(autouse=True)
def no_cairn_settings_from_the_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    to_clear = [
        name for name in os.environ if name.startswith("CAIRN_") and name not in TEST_HARNESS_ENV
    ]
    for name in to_clear:
        monkeypatch.delenv(name)
