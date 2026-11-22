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


# --- scripts/ci_alone.py: every test file in a process of its own ----------------------------

_alone = importlib.util.spec_from_file_location("ci_alone", REPO / "scripts" / "ci_alone.py")
assert _alone and _alone.loader
ci_alone = importlib.util.module_from_spec(_alone)
_alone.loader.exec_module(ci_alone)


def _three_files(tmp_path: Path) -> dict[Path, int]:
    """A file that passes alone, one that fails alone, and one with nothing to run (pytest's 5)."""
    codes = {
        tmp_path / "test_good.py": 0,
        tmp_path / "test_bad.py": 1,
        tmp_path / "test_none.py": 5,
    }
    for path in codes:
        path.write_text("", encoding="utf-8")
    return codes


def test_a_file_that_fails_in_a_process_of_its_own_is_named_and_fails_the_run(tmp_path, capsys):
    codes = _three_files(tmp_path)
    seen: list[Path] = []

    def run(path: Path) -> tuple[int, str]:
        seen.append(path)
        return codes[path], "1 failed in 0.01s" if codes[path] == 1 else ""

    assert ci_alone.main([str(tmp_path), "--jobs", "2"], run=run) == 1
    said = capsys.readouterr().out
    assert sorted(seen) == sorted(codes)  # every file, each once
    assert "test_bad.py" in said and "1 failed in 0.01s" in said
    assert "test_good.py" not in said and "test_none.py" not in said


def test_files_that_all_pass_alone_end_the_run_at_zero(tmp_path, capsys):
    codes = _three_files(tmp_path)
    codes[tmp_path / "test_bad.py"] = 0
    assert ci_alone.main([str(tmp_path)], run=lambda path: (codes[path], "")) == 0
    assert "3 files, each in a process of its own: none failed" in capsys.readouterr().out


def test_one_file_is_run_by_an_interpreter_of_its_own(tmp_path):
    passing, failing = tmp_path / "test_passing.py", tmp_path / "test_failing.py"
    passing.write_text("def test_it():\n    assert True\n", encoding="utf-8")
    failing.write_text("def test_it():\n    assert False\n", encoding="utf-8")
    assert ci_alone.run_file(passing)[0] == 0
    code, last = ci_alone.run_file(failing)
    assert code == 1 and "1 failed" in last
