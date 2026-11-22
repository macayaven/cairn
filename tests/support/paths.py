"""Canonical repository paths shared by unit tests across subdirectories."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = REPO_ROOT / "tests"
REFERENCE_ROOT = TESTS_ROOT / "support" / "reference"
FIXTURES_ROOT = TESTS_ROOT / "fixtures"
GOLD_ROOT = TESTS_ROOT / "gold"
SRC_ROOT = REPO_ROOT / "src" / "cairn"
SCRIPTS_ROOT = REPO_ROOT / "scripts"
WORKFLOWS_ROOT = REPO_ROOT / ".github" / "workflows"
