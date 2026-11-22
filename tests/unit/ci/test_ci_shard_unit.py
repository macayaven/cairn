"""Unit tests for scripts/ci_shard.py."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from support.paths import REPO_ROOT

REPO = REPO_ROOT

_spec = importlib.util.spec_from_file_location("ci_shard", REPO / "scripts" / "ci_shard.py")
assert _spec and _spec.loader
ci_shard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci_shard)


pytestmark = pytest.mark.ci_meta


def test_shards_partition_every_non_browser_test_file_once():
    files = ci_shard.collect_test_files([REPO / "tests"], exclude_dirs=[REPO / "tests/browser"])
    assert files
    assert not any((REPO / "tests/browser") in f.parents for f in files)

    shards = ci_shard.assign_shards(files, total=4)
    flattened = [item for shard in shards for item in shard]
    assert len(flattened) == len(files)
    assert set(flattened) == set(files)
    assert all(shard for shard in shards)


def test_shards_are_deterministic_and_balance_byte_weights():
    weights = {
        Path(f"tests/test_{i:02d}.py"): size
        for i, size in enumerate([1000, 900, 800, 700, 100, 100, 100, 100])
    }
    first = ci_shard.assign_shards(list(weights), total=4, weights=weights)
    second = ci_shard.assign_shards(list(weights), total=4, weights=weights)
    assert first == second
    loads = [sum(weights[p] for p in shard) for shard in first]
    assert max(loads) - min(loads) <= 200


def test_cli_rejects_out_of_range_index(capsys):
    with pytest.raises(SystemExit) as exited:
        ci_shard.main(["--index", "4", "--total", "4", str(REPO / "tests")])
    assert exited.value.code == 2
    assert "--index must satisfy" in capsys.readouterr().err


def test_cli_outputs_exact_union_across_four_shards():
    lines: list[str] = []
    for index in range(4):
        done = subprocess.run(
            [sys.executable, "scripts/ci_shard.py", "--index", str(index), "--total", "4"],
            cwd=REPO,
            check=True,
            capture_output=True,
            text=True,
        )
        lines.extend(line for line in done.stdout.splitlines() if line)
    assert len(lines) == len(set(lines))
    assert not any(line.startswith("tests/browser/") for line in lines)
