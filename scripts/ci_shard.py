"""Assign repository test files to balanced CI shards deterministically."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path


def collect_test_files(
    roots: Sequence[Path],
    *,
    exclude_dirs: Sequence[Path] = (),
) -> list[Path]:
    """Collect sorted test modules under `roots`, skipping any `exclude_dirs`."""
    excluded = {path.as_posix().rstrip("/") for path in exclude_dirs}
    found: dict[str, Path] = {}
    for root in roots:
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else root.rglob("*.py")
        for path in candidates:
            name = path.name
            if not (name.startswith("test_") or name.endswith("_test.py")):
                continue
            posix = path.as_posix()
            if any(posix == ex or posix.startswith(ex + "/") for ex in excluded):
                continue
            found[posix] = path
    return [found[key] for key in sorted(found)]


def assign_shards(
    files: Sequence[Path],
    total: int,
    *,
    weights: dict[Path, int] | None = None,
) -> list[list[Path]]:
    """Partition `files` into `total` shards via Longest Processing Time first."""
    if total < 1:
        raise ValueError(f"total shards must be >= 1, got {total}")
    sizes = weights if weights is not None else {path: path.stat().st_size for path in files}
    buckets: list[list[Path]] = [[] for _ in range(total)]
    loads = [0] * total
    for path in sorted(files, key=lambda p: (-sizes[p], p.as_posix())):
        target = min(range(total), key=lambda idx: (loads[idx], idx))
        buckets[target].append(path)
        loads[target] += sizes[path]
    for bucket in buckets:
        bucket.sort(key=lambda p: p.as_posix())
    return buckets


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit pytest file paths for one CI shard.")
    parser.add_argument("--index", type=int, required=True, help="0-based shard index")
    parser.add_argument("--total", type=int, required=True, help="total number of shards")
    parser.add_argument(
        "--exclude",
        type=Path,
        action="append",
        default=[],
        help="directory prefix to exclude from sharding",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[Path("tests")],
        help="test roots to scan (default: tests)",
    )
    args = parser.parse_args(argv)
    if args.total < 1 or not 0 <= args.index < args.total:
        parser.error(f"--index must satisfy 0 <= index < --total ({args.total})")
    excludes = args.exclude or [Path("tests/browser")]
    files = collect_test_files(args.paths, exclude_dirs=excludes)
    shards = assign_shards(files, args.total)
    for path in shards[args.index]:
        print(path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
