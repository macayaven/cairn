"""The silence alert arms itself on a new machine. Google arms an absence condition only once
its metric has had a point, and the point is a `replica: current, ` line in the litestream
container's log. So the container runs from boot, waiting inside for the database cairn
creates, and the replica check writes a current line while there is no database: nothing to
copy is current.

Run from a copy of host.sh against stand-ins (deploy_standins.py); the wait itself runs
under /bin/sh with a stand-in `litestream`.
"""

import importlib.util
import os
import re
import signal
import stat
import subprocess

import pytest

from support.deploy_standins import BASH, Standins
from support.paths import REPO_ROOT
from wait_helpers import poll_until

REPO = REPO_ROOT
INFRA = REPO / "infra" / "gcp"
MONITORING = (INFRA / "monitoring.tf").read_text(encoding="utf-8")
TEMPLATE = (INFRA / "cloud-init.yaml.tftpl").read_text(encoding="utf-8")
_spec = importlib.util.spec_from_file_location(
    "rehearse_stack_for_boot", REPO / "scripts" / "rehearse_stack.py"
)
stack = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stack)


@pytest.fixture
def host(tmp_path):
    report = Standins(tmp_path).host()
    report.bound(900)
    config = report.root / "config.env"
    config.write_text(
        config.read_text(encoding="utf-8")
        + 'LITESTREAM_MEMORY_MB="256"\nLITESTREAM_PIDS_LIMIT="256"\n',
        encoding="utf-8",
    )
    return report


def run(host, function: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, str(host.script), function],
        env={**host.s.env({}), "HOST_ROOT": str(host.root)},
        cwd=host.root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )


def litestream_run(host) -> list[str]:
    done = run(host, "cmd_run_litestream")
    assert done.returncode == 0, done.stdout + done.stderr
    [argv] = host.docker_runs()
    return argv


def wait_script(argv: list[str]) -> str:
    """The -c script given to sh. The stand-in docker records one line per argument, and a
    script of several lines comes back as several entries."""
    start = argv.index("-c") + 1
    end = argv.index("sh", start)  # $0 of the script, then litestream's own arguments
    return "\n".join(argv[start:end])


def test_the_container_starts_before_the_database_exists_and_waits_inside(host):
    host.database(False)
    argv = litestream_run(host)
    # Nothing waited on the host: the container was started at once.
    assert host.s.calls("sleep") == []
    assert argv[argv.index("--name") + 1] == "litestream"
    assert argv[argv.index("--entrypoint") + 1] == "sh"
    script = wait_script(argv)
    assert "/data/cairn.db" in script
    assert 'exec litestream "$@"' in script
    # After the script's $0, the arguments litestream replicates with.
    assert argv[-1] == "replicate"


def test_the_container_is_the_same_once_the_database_exists(host):
    host.database(True)
    argv = litestream_run(host)
    assert argv[argv.index("--entrypoint") + 1] == "sh"
    assert argv[-1] == "replicate"


def test_the_other_litestream_runs_keep_the_images_own_entrypoint(host):
    host.database(True)
    done = run(host, "cmd_restore")
    assert done.returncode == 0, done.stdout + done.stderr
    [argv] = host.docker_runs()
    assert "--entrypoint" not in argv
    assert argv[-4:] == ["restore", "-if-db-not-exists", "-if-replica-exists", "/data/cairn.db"]


@pytest.fixture
def wait(tmp_path, host):
    """The script the container runs, with /data as a directory here and `litestream` a
    stand-in that says what it was given."""
    host.database(False)
    script = wait_script(litestream_run(host)).replace("/data/", f"{tmp_path}/data/")
    (tmp_path / "data").mkdir()
    bin_dir = tmp_path / "wait-bin"
    bin_dir.mkdir()
    stub = bin_dir / "litestream"
    stub.write_text('#!/bin/sh\necho "litestream $*"\n', encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    entered = tmp_path / "wait-entered"
    sleeper = bin_dir / "sleep"
    sleeper.write_text('#!/bin/sh\n: > "$WAIT_ENTERED"\nexec /bin/sleep "$@"\n', encoding="utf-8")
    sleeper.chmod(sleeper.stat().st_mode | stat.S_IXUSR)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "WAIT_ENTERED": str(entered)}

    def start() -> subprocess.Popen:
        return subprocess.Popen(
            ["/bin/sh", "-c", script, "sh", "replicate"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    return start, tmp_path / "data" / "cairn.db", entered


def test_the_wait_runs_litestream_once_the_database_appears(wait):
    start, db, entered = wait
    with start() as proc:
        try:
            poll_until(entered.exists, "replica did not start waiting for the database")
            assert proc.poll() is None
            db.write_bytes(b"")
            out, _ = proc.communicate(timeout=20)
        finally:
            if proc.poll() is None:
                proc.kill()
        assert proc.returncode == 0
    assert out.splitlines() == ["litestream replicate"]


def test_the_wait_ends_at_once_on_a_stop(wait):
    start, _, entered = wait
    with start() as proc:
        try:
            poll_until(entered.exists, "replica did not start waiting for the database")
            os.kill(proc.pid, signal.SIGTERM)
            out, _ = proc.communicate(timeout=15)
        finally:
            if proc.poll() is None:
                proc.kill()
        assert proc.returncode == 0
    assert "litestream" not in out


# --- the check's line before the first database ------------------------------------------------


def metric_filter() -> list:
    start = MONITORING.index('resource "google_logging_metric" "replica_current"')
    found = re.search(r"filter\s*=\s*<<-(\w+)\n(.*?)\n\s*\1\n", MONITORING[start:], re.S)
    assert found
    return stack.parse_filter(found.group(2), stack.STAND_INS)


def test_before_the_first_database_the_check_says_current_where_the_metric_counts_it(host):
    host.database(False)
    done = host.replica_check()
    assert done.rc == 0, done.out + done.err
    assert done.line == "replica: current, no database yet, nothing to copy"
    assert host.litestream_log() == [done.line]
    assert stack.matches(metric_filter(), stack.cos_entry(done.line))
    alert = stack.parse_filter(stack.alert_filter(MONITORING), stack.STAND_INS)
    assert not stack.matches(alert, stack.cos_entry(done.line))
    # Nothing was asked of the replica.
    assert host.docker_runs() == []


def test_before_the_first_database_a_stopped_litestream_fails_in_the_journal(host):
    host.database(False)
    done = host.replica_check(litestream=False)
    assert done.rc != 0
    assert host.litestream_log() == []
    assert "the litestream container is not running" in done.err


def test_the_check_also_runs_soon_after_boot():
    start = TEMPLATE.index("  - path: /etc/systemd/system/cairn-replica-check.timer\n")
    timer = TEMPLATE[start : TEMPLATE.index("\n  - path: ", start + 1)]
    assert re.search(r"^\s*OnBootSec=2min$", timer, re.M)
    assert re.search(r"^\s*OnCalendar=\*:0/5$", timer, re.M)


def test_a_database_gone_after_a_deployment_is_not_current_and_writes_no_line(host):
    """Only before the first deployment does no database mean nothing to copy. Once an image
    is proven, a missing database writes no line, and the silence alert fires."""
    host.record("image", "registry.example.test/cairn@sha256:" + "a" * 64)
    host.database(False)
    done = host.replica_check()
    assert done.line == ""
    assert host.litestream_log() == []
    assert done.rc != 0
    assert "no database, though a deployment was proven" in done.err
