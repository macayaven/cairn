"""Structural invariants for the domain-grouped unit suite and tests/support/."""

from __future__ import annotations

import ast
import json
import pathlib
import subprocess
import sys

from support.paths import TESTS_ROOT

DOMAINS = frozenset({"ci", "compiler", "guides", "meta", "ops", "slack", "sources", "store", "web"})


def _cross_test_imports(path: pathlib.Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text("utf-8"), str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("test_") or ".test_" in alias.name:
                    found.append((node.lineno, alias.name))
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module.startswith("test_") or ".test_" in node.module)
        ):
            found.append((node.lineno, node.module))
    return found


def test_no_cross_test_imports_in_unit_or_support():
    violations = []
    for base in (TESTS_ROOT / "unit", TESTS_ROOT / "support"):
        for path in sorted(base.rglob("*.py")):
            hits = _cross_test_imports(path)
            if hits:
                violations.append((path.relative_to(TESTS_ROOT).as_posix(), hits))
    assert not violations, violations


def test_unit_files_live_inside_domain_subdirectories_without_reviewer_names():
    unit = TESTS_ROOT / "unit"
    assert sorted(p.name for p in unit.glob("*.py")) == []
    dirs = {p.name for p in unit.iterdir() if p.is_dir() and p.name != "__pycache__"}
    assert dirs == DOMAINS, dirs
    reviewer_files = [p.relative_to(TESTS_ROOT).as_posix() for p in unit.rglob("test_reviewer*.py")]
    assert not reviewer_files, reviewer_files


def test_support_holds_no_pytest_collected_test_files():
    bad = [
        p.relative_to(TESTS_ROOT).as_posix()
        for p in sorted((TESTS_ROOT / "support").rglob("*.py"))
        if p.name.startswith("test_") or p.name.endswith("_test.py")
    ]
    assert not bad, bad


def test_a_slack_bench_hears_its_handlers_in_a_process_of_its_own():
    """A Slack handler is registered when its module is imported. A bench imports them itself,
    so a test file that uses one passes whether or not another file ran before it: with only a
    file of another kind imported first, the dispatcher answered an empty 200 and filed nothing."""
    code = (
        "import json, sys; sys.path[:0] = [sys.argv[1], sys.argv[1] + '/support'];"
        " import support.slack.slack_commands; from cairn.slack import dispatch;"
        " print(json.dumps({kind: sorted(found) for kind, found in dispatch.REGISTRIES.items()}))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code, str(TESTS_ROOT)], capture_output=True, text=True, check=True
    )
    heard = json.loads(done.stdout)
    assert "file_in_cairn" in heard["action"]
    assert {"", "help", "find", "note", "project", "link", "unlink"} <= set(heard["command"])
    assert "message" in heard["event"]
