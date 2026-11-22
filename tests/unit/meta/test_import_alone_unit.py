"""Every module of the package imports alone and first, in an interpreter of its own: no
import cycle is hidden by the order in which the rest of the code happens to load modules.
cairn.__main__ is left out: importing it runs the command line (it only imports cairn.cli,
which is checked)."""

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cairn

PACKAGE = Path(cairn.__file__).parent


def modules() -> list[str]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        parts = path.relative_to(PACKAGE.parent).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts[-1] != "__main__":
            found.append(".".join(parts))
    return found


def imported_alone(name: str, where: Path) -> tuple[str, int, str]:
    done = subprocess.run(
        [sys.executable, "-c", f"import importlib; importlib.import_module({name!r})"],
        cwd=where,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return name, done.returncode, done.stderr.strip().splitlines()[-1:]


def test_every_module_imports_alone_and_first(tmp_path):
    names = modules()
    assert "cairn.store.schema" in names and "cairn.web.ui" in names and len(names) > 100
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda name: imported_alone(name, tmp_path), names))
    assert [(name, error) for name, code, error in results if code != 0] == []
