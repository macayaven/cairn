"""Run every test file in a process of its own, and name the ones that fail there.

A file that passes in the whole suite can still depend on another file having run first in
the same process: a handler that another file's import registered, a cache it filled. A shard
groups files differently from a whole run, so such a file fails there and nowhere else. This
runs each file alone. It is slow, so it runs every night (`.github/workflows/nightly.yml`) and
by hand:

    python3 scripts/ci_alone.py                        # every file under tests/ but tests/browser
    python3 scripts/ci_alone.py tests/unit/slack --jobs 4

A file passes when pytest ends at 0, or at 5 (it collected nothing: every test of the file is
one of the serial timing tests, which are left out here as they are in the shards). Any other
exit names the file, with the last line pytest printed, and the run ends at 1.

It needs only the standard library and `scripts/ci_shard.py` beside it, which finds the files.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ci_shard import collect_test_files

PASSES = (0, 5)  # pytest's exit for "all passed" and for "collected no test"


def run_file(path: Path) -> tuple[int, str]:
    """pytest's exit status for PATH run by an interpreter of its own, and its last line."""
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(path),
            "-q",
            "-m",
            "not perf",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    return done.returncode, lines[-1] if lines else done.stderr.strip()[-200:]


def main(
    argv: Sequence[str] | None = None, run: Callable[[Path], tuple[int, str]] = run_file
) -> int:
    parser = argparse.ArgumentParser(description="Run each test file in a process of its own.")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 2, help="files at a time")
    parser.add_argument("paths", nargs="*", type=Path, default=[Path("tests")], help="test roots")
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs is 1 or more")
    files = collect_test_files(args.paths, exclude_dirs=[Path("tests/browser")])
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(zip(files, pool.map(run, files), strict=True))
    failed = [(path, code, last) for path, (code, last) in results if code not in PASSES]
    for path, code, last in failed:
        print(f"fails alone (pytest ended at {code}): {path.as_posix()}: {last}")
    if failed:
        print(f"{len(failed)} of {len(files)} files fail in a process of their own")
        return 1
    print(f"{len(files)} files, each in a process of its own: none failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
