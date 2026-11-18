"""Execute the documented shell recipes against an offline Docker boundary."""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
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


def execute(block, root, env):
    return subprocess.run(["bash", "-c", block], cwd=root, env=env, capture_output=True, text=True)


@pytest.mark.parametrize("guide", GUIDES, ids=["compose", "docker"])
def test_each_backup_is_complete_and_distinct(docker_boundary, guide):
    root, env = docker_boundary
    for value in ("first database", "second database"):
        (root / "data/cairn.db").write_text(value)
        result = execute(recipe(guide, "backup"), root, env)
        assert result.returncode == 0, result.stderr
    snapshots = sorted(root.glob("cairn-backup*"))
    assert len(snapshots) == 2
    assert {(p / "cairn.db").read_text() for p in snapshots} == {
        "first database",
        "second database",
    }
    assert all((p / "hidden/state").read_text() == "suppression state" for p in snapshots)
    assert (root / "state").read_text() == "start"


@pytest.mark.parametrize("guide", GUIDES, ids=["compose", "docker"])
@pytest.mark.parametrize("failure", ["cp", "stop", "start"])
def test_failure_is_actionable_and_never_claims_success(docker_boundary, guide, failure):
    root, env = docker_boundary
    result = execute(recipe(guide, "backup"), root, dict(env, FAIL_ACTION=failure))
    assert result.returncode != 0
    assert "Backup saved" not in result.stdout
    assert "failed" in result.stderr.lower() or "refused" in result.stderr.lower()
    if failure == "cp":
        assert (root / "state").read_text() == "start"
        assert "not a successful backup" in result.stderr
    if failure == "start":
        assert "docker" in result.stderr and "start cairn" in result.stderr
        assert "cairn-backup" in result.stderr


def test_restore_copies_chosen_snapshot_into_fresh_storage(docker_boundary):
    root, env = docker_boundary
    result = execute(recipe(GUIDES[1], "backup"), root, env)
    assert result.returncode == 0, result.stderr
    snapshot = next(root.glob("cairn-backup*"))
    (root / "data/cairn.db").write_text("live database")
    block = recipe(GUIDES[1], "restore")
    block = re.sub(r"^BACKUP_DIR=.*$", f'BACKUP_DIR="{snapshot}"', block, flags=re.M)
    result = execute(block, root, env)
    assert result.returncode == 0, result.stderr
    target = Path((root / "restore-path").read_text())
    assert target != snapshot and target != root / "data"
    assert (snapshot / "cairn.db").read_text() == "first database"
    assert (root / "data/cairn.db").read_text() == "live database"
    assert (target / "hidden/state").read_text() == "suppression state"


@pytest.mark.parametrize("guide", GUIDES, ids=["compose", "docker"])
@pytest.mark.parametrize("failure", ["pull", "build"])
def test_a_failed_source_upgrade_keeps_the_running_container(docker_boundary, guide, failure):
    root, env = docker_boundary
    text = guide.read_text()
    heading = "### Update" if guide.name == "README.md" else "## Upgrade"
    upgrade = text.split(heading, 1)[1]
    block = re.findall(r"```bash\n(.*?)```", upgrade, re.S)[0]
    result = execute(block, root, dict(env, FAIL_ACTION=failure))
    assert result.returncode != 0
    calls = root / "calls"
    actions = (
        [json.loads(line)[0] for line in calls.read_text().splitlines()] if calls.exists() else []
    )
    assert not set(actions) & {"stop", "rm", "up"}
