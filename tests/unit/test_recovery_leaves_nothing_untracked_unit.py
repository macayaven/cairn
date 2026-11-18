"""What the documented backup, restore and export commands leave in a clone, git ignores.

The README's commands run from deploy/ and the run guide's from the clone's top folder. Each
backup makes a cairn-backup.XXXXXX folder there, a restore a cairn-restore.XXXXXX folder
that a running cairn then fills (cairn.db, its -wal and -shm, cairn.db.serve.lock), and an
export is copied out as cairn-export.zip. The commands run here as written, from a git
clone holding the repository's .gitignore, against the stand-in for Docker of
test_local_recovery_docs_unit.py; `git status` must then show nothing they made."""

import os
import re
import shutil
import subprocess

from test_local_recovery_docs_unit import GUIDES, ROOT, docker_boundary, recipe  # noqa: F401

SERVE_FILES = ("cairn.db-wal", "cairn.db-shm", "cairn.db.serve.lock")


def git(clone, *args, env):
    return subprocess.run(
        ["git", "-C", str(clone), *args], capture_output=True, text=True, env=env, check=True
    ).stdout


def run(block, cwd, env):
    result = subprocess.run(["bash", "-c", block], cwd=cwd, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def exported_as(guide) -> str:
    """The file name an export is copied out to: `docker [compose] cp cairn:/data/export.zip
    ./NAME`."""
    (name,) = set(re.findall(r"cp cairn:/data/export\.zip \./(\S+)", guide.read_text()))
    return name


def test_the_documented_recovery_commands_leave_nothing_git_shows(docker_boundary, tmp_path):  # noqa: F811
    root, env = docker_boundary
    (root / "data" / "cairn.db.serve.lock").write_text("")  # a backup of a running cairn's /data
    clone = root / "clone"
    (clone / "deploy").mkdir(parents=True)
    shutil.copy(ROOT / ".gitignore", clone / ".gitignore")
    home = tmp_path / "home"
    home.mkdir()
    # Only the repository's own rules: no global or system configuration, no excludes file.
    git_env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    git(clone, "init", "-q", env=git_env)
    readme, run_guide = GUIDES

    run(recipe(readme, "backup"), clone / "deploy", env)
    run(recipe(run_guide, "backup"), clone, env)
    (snapshot,) = clone.glob("cairn-backup.*")
    block = re.sub(r"^BACKUP_DIR=.*$", f'BACKUP_DIR="{snapshot}"', recipe(run_guide, "restore"),
                   flags=re.M)  # fmt: skip
    run(block, clone, env)
    (restored,) = clone.glob("cairn-restore.*")
    for name in SERVE_FILES:  # what the restored cairn writes beside its database
        (restored / name).write_text("")
    (clone / "deploy" / exported_as(readme)).write_bytes(b"PK")
    (clone / exported_as(run_guide)).write_bytes(b"PK")

    made = sorted(p.relative_to(clone).as_posix() for p in clone.rglob("*") if p.is_file())
    made = [path for path in made if not path.startswith(".git/") and path != ".gitignore"]
    assert any(path.startswith("deploy/cairn-backup.") for path in made), made
    assert any(path.startswith("cairn-restore.") for path in made), made
    assert "deploy/cairn-export.zip" in made and "cairn-export.zip" in made
    shown = git(clone, "status", "--porcelain", "--untracked-files=all", env=git_env)
    assert shown.splitlines() == ["?? .gitignore"]
