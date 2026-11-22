"""Execute the documented shell recipes against an offline Docker boundary."""

import json
import re
import subprocess
from pathlib import Path

import pytest

from support.ops.local_recovery_docs import GUIDES, recipe
from support.ops.local_recovery_docs import docker_boundary as docker_boundary


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
