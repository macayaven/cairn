"""Shared test helpers extracted from test_local_recovery_docs_unit."""

import os
import re

import pytest

from support.paths import REPO_ROOT

ROOT = REPO_ROOT

GUIDES = [ROOT / "README.md", ROOT / "src/cairn/guides/run.md"]


def recipe(guide, kind):
    blocks = re.findall(r"```bash\n(.*?)```", guide.read_text(), re.S)
    if kind == "backup":
        return next(
            b
            for b in blocks
            if re.search(r"docker (?:compose )?stop cairn", b) and "cp cairn:/data" in b
        )
    return next((b for b in blocks if "RESTORE_DIR=" in b), "exit 99\n")


@pytest.fixture
def docker_boundary(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "cairn.db").write_text("first database")
    (data / "hidden").mkdir()
    (data / "hidden" / "state").write_text("suppression state")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    date = bin_dir / "date"
    date.write_text("#!/bin/sh\nprintf 20261118-010000\n")
    date.chmod(0o755)
    git = bin_dir / "git"
    git.write_text('#!/bin/sh\n[ "$FAIL_ACTION" != pull ]\n')
    git.chmod(0o755)
    docker = bin_dir / "docker"
    docker.write_text("""#!/usr/bin/env python3
import json, os, pathlib, shutil, sys
root = pathlib.Path(os.environ["FIXTURE_ROOT"])
args = sys.argv[1:]
if args[0] == "compose": args.pop(0)
with (root / "calls").open("a") as log: log.write(json.dumps(args) + "\\n")
action = args[0]
if action == "build":
    if os.environ.get("FAIL_ACTION") == "build": sys.exit(19)
elif action in ("stop", "start"):
    if os.environ.get("FAIL_ACTION") == action:
        print(action + " refused by Docker", file=sys.stderr)
        sys.exit(17)
    (root / "state").write_text(action)
elif action == "cp":
    if os.environ.get("FAIL_ACTION") == "cp":
        print("copy denied by Docker", file=sys.stderr)
        sys.exit(23)
    source, dest = args[1:]
    target = pathlib.Path(dest)
    if source == "cairn:/data/.":
        shutil.copytree(root / "data", target, dirs_exist_ok=True)
    else:
        shutil.copytree(root / "data", target / "data", dirs_exist_ok=True)
elif action == "run":
    assert args[args.index("--pull") + 1] == "never"
    assert args[-1] == "cairn:local"
    mount = args[args.index("-v") + 1]
    target = pathlib.Path(mount.removesuffix(":/data"))
    assert (target / "cairn.db").read_text() == "first database"
    (target / "cairn.db").write_text("recovery changed database")
    (root / "restore-path").write_text(str(target))
else:
    raise AssertionError(args)
""")
    docker.chmod(0o755)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "FIXTURE_ROOT": str(tmp_path)}
    return tmp_path, env
