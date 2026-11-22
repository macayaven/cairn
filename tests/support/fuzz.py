"""Tiered fuzzing / generated-property sweep controls (`CAIRN_FUZZ_PROFILE=pr|full`)."""

from __future__ import annotations

import os

VALID_PROFILES = frozenset({"pr", "full"})


def fuzz_profile() -> str:
    """Return active fuzz profile (`pr` by default, or `full` in nightly / release sweeps)."""
    raw = os.environ.get("CAIRN_FUZZ_PROFILE", "pr").strip().lower()
    if raw not in VALID_PROFILES:
        allowed = ", ".join(sorted(VALID_PROFILES))
        raise ValueError(f"CAIRN_FUZZ_PROFILE={raw!r} is invalid; expected one of: {allowed}")
    return raw


def is_full() -> bool:
    """Return True when running the full exhaustive fuzz profile."""
    return fuzz_profile() == "full"


def fuzz_budget(pr: int, full: int) -> int:
    """Return `full` under `CAIRN_FUZZ_PROFILE=full`, otherwise `pr`."""
    return full if is_full() else pr


def chunk_seeds(total: int, chunks: int = 4) -> list[range]:
    """Partition `range(total)` into `chunks` non-empty balanced slices for xdist workers."""
    if total <= 0:
        return []
    count = max(1, min(chunks, total))
    step, rem = divmod(total, count)
    ranges: list[range] = []
    start = 0
    for idx in range(count):
        end = start + step + (1 if idx < rem else 0)
        ranges.append(range(start, end))
        start = end
    return ranges
