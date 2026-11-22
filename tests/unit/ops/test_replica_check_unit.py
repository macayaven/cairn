"""host.sh's `replica-check`, the freshness line the replica alerts read, run from a copy of
host.sh against stand-ins (tests/unit/deploy_standins.py).

The check's container prints four numbers: the time of the newest transaction the replica
holds (0 when it holds none), the last change to the database's files, the start of the
litestream container (0 when it does not run), and the time now, all in seconds since the
epoch. The stand-in docker prints what the test prepared instead. host.sh decides from them
and writes one line into the litestream container's own log, which is what Cloud Logging
receives from the VM; the line also goes to the unit's output, the journal.
"""

import importlib.util
import re

import pytest

from support.deploy_standins import Standins
from support.paths import REPO_ROOT

REPO = REPO_ROOT
MONITORING = (REPO / "infra" / "gcp" / "monitoring.tf").read_text(encoding="utf-8")
_spec = importlib.util.spec_from_file_location(
    "rehearse_stack_for_replica_check", REPO / "scripts" / "rehearse_stack.py"
)
stack = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stack)

BOUND = 900  # what main.tf gives the template: 15 minutes


@pytest.fixture
def host(tmp_path):
    report = Standins(tmp_path).host()
    report.bound(BOUND)
    report.database(True)
    return report


def test_a_copy_that_holds_the_last_change_is_current(host):
    done = host.replica_check(newest=1000, changed=1000, now=1100)
    assert done.rc == 0, done.text
    assert done.line == "replica: current, newest 100 s old"
    assert host.litestream_log() == ["replica: current, newest 100 s old"]


def test_an_idle_database_is_current_however_old_its_newest_copy(host):
    """Nothing written for a day: the newest file in the replica is a day old, and the database
    has not changed since it was made, so there is nothing to copy."""
    done = host.replica_check(newest=1000, changed=1000, now=1000 + 86400)
    assert done.line == "replica: current, newest 86400 s old"
    assert not host.unconfirmed()


def test_a_change_after_the_newest_copy_is_an_error_once_it_is_older_than_the_bound(host):
    """Not copying: the database changed at 5000 and the replica's newest file is still from
    1000. Within the bound the line stays current; the run after the bound says Error."""
    first = host.replica_check(newest=1000, changed=5000, now=5100)
    assert first.line == "replica: current, newest 4100 s old"
    assert host.unconfirmed() == "5000"
    later = host.replica_check(newest=1000, changed=5600, now=5000 + BOUND + 1)
    assert later.line == f"Error: replica: newest {4000 + BOUND + 1} s old, over the bound"
    assert host.litestream_log()[-1] == later.line
    assert later.rc == 0, later.text


def test_a_change_seen_first_after_the_bound_is_an_error_at_once(host):
    done = host.replica_check(newest=1000, changed=5000, now=5000 + BOUND + 1)
    assert done.line.startswith("Error: replica: newest ")


def test_a_change_made_just_before_the_check_after_an_idle_hour_is_not_an_error(host):
    """Healthy, with a write a few seconds before the check: the newest copy is an hour old and
    the database changed after it. One sample cannot tell this from a dead copy, so the change
    is remembered and judged by the next run."""
    done = host.replica_check(newest=1000, changed=4595, now=4600)
    assert done.line == "replica: current, newest 3600 s old"
    copied = host.replica_check(newest=4596, changed=4596, now=4900)
    assert copied.line == "replica: current, newest 304 s old"
    assert not host.unconfirmed()


def test_a_copy_that_catches_up_clears_what_was_remembered(host):
    host.replica_check(newest=1000, changed=5000, now=5100)
    done = host.replica_check(newest=5001, changed=5300, now=5400)
    assert done.line == "replica: current, newest 399 s old"
    assert host.unconfirmed() == "5300"  # the newer change is still to be copied
    host.replica_check(newest=5300, changed=5300, now=5700)
    assert not host.unconfirmed()


def test_a_change_within_the_grace_of_the_newest_copy_counts_as_copied(host):
    """Litestream stamps a file a moment after the write it holds; file times are whole
    seconds. Ten seconds of slack."""
    done = host.replica_check(newest=1000, changed=1010, now=1000 + 2 * BOUND)
    assert done.line.startswith("replica: current, ")
    assert not host.unconfirmed()
    late = host.replica_check(newest=1000, changed=1011, now=1200)
    assert late.line.startswith("replica: current, ")  # not copied, within the bound
    assert host.unconfirmed() == "1011"


def test_a_change_from_before_litestreams_start_is_not_counted(host):
    """Litestream stops last and SQLite checkpoints the WAL into cairn.db as it closes: the
    file's time moves with nothing new to copy. Litestream copies what changed while it was
    down when it starts, so an idle database must stay current after a restart."""
    for now in (5100, 5000 + 2 * BOUND, 5000 + 20 * BOUND):
        done = host.replica_check(newest=4985, changed=5000, started=5005, now=now)
        assert done.line.startswith("replica: current, "), done.line
        assert not host.unconfirmed()


def test_a_change_after_litestreams_start_counts_again(host):
    host.replica_check(newest=4985, changed=5100, started=5005, now=5200)
    assert host.unconfirmed() == "5100"
    later = host.replica_check(newest=4985, changed=5100, started=5005, now=5100 + BOUND + 1)
    assert later.line.startswith("Error: replica: ")


def test_an_empty_replica_beside_a_database_is_not_current_for_long(host):
    host.replica_check(newest=0, changed=5000, now=5100)
    done = host.replica_check(newest=0, changed=5000, now=5000 + BOUND + 1)
    assert done.line.startswith("Error: replica: newest ")


def test_the_bound_is_read_from_config_env(host):
    host.bound(60)
    host.replica_check(newest=1000, changed=5000, now=5010)
    assert host.replica_check(newest=1000, changed=5000, now=5061).line.startswith("Error: ")


def test_without_the_litestream_container_the_line_reaches_the_journal_only_and_the_run_fails(
    host,
):
    done = host.replica_check(newest=1000, changed=5000, now=5000 + BOUND + 1, litestream=False)
    assert done.line.startswith("Error: replica: newest ")
    assert done.rc != 0
    assert "the litestream container is not running" in done.err
    assert host.litestream_log() == []


def test_a_replica_that_cannot_be_listed_gives_no_line_and_fails(host):
    done = host.replica_check(newest=1000, changed=1000, now=1100, probe_rc=1)
    assert done.rc != 0
    assert done.line == ""
    assert "could not list the replica" in done.err
    assert host.litestream_log() == []


@pytest.mark.parametrize("said", ["", "1000 1000 1100", "1000 x 0 1100", "1000 1000 0 1100 7"])
def test_anything_but_three_numbers_from_the_probe_gives_no_line(host, said):
    done = host.replica_check(said=said)
    assert done.rc != 0 and done.line == ""
    assert host.litestream_log() == []


def test_before_the_first_database_the_line_says_current_and_nothing_is_asked(host):
    # Nothing to copy is current; the line arms the silence alert on a new VM
    # (test_host_replica_at_boot_unit.py).
    host.database(False)
    done = host.replica_check(newest=1000, changed=1000, now=1100)
    assert done.rc == 0
    assert done.line == "replica: current, no database yet, nothing to copy"
    assert host.docker_runs() == []


def test_the_probe_reads_the_replica_and_the_database_read_only_on_the_backup_network(host):
    host.replica_check(newest=1000, changed=1000, now=1100)
    [argv] = host.docker_runs()
    assert argv[argv.index("--name") + 1] == "cairn-replica-check"
    assert argv[argv.index("--network") + 1] == "cairn-backup"
    assert argv[argv.index("--user") + 1] == "10001:10001"
    assert argv[argv.index("--entrypoint") + 1] == "sh"
    volumes = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert any(v.endswith("/cairn:/data:ro") for v in volumes), volumes
    assert "/etc/cairn/litestream.yml:/etc/litestream.yml:ro" in volumes
    script = "\n".join(argv[argv.index("-c") + 1 :])  # the stand-in records a line per word
    for level in ("0", "1", "9"):
        assert re.search(rf"\b{level}\b", script.split("for level in", 1)[1].split(";")[0])
    assert "litestream ltx -json -level" in script
    assert "stat -c %Y /data/cairn.db /data/cairn.db-wal" in script
    environment = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    assert any(e.startswith("LITESTREAM_STARTED=") for e in environment), environment


# --- the lines against the two conditions that read them (monitoring.tf) ----------------------


def metric_filter() -> list:
    start = MONITORING.index('resource "google_logging_metric" "replica_current"')
    found = re.search(r"filter\s*=\s*<<-(\w+)\n(.*?)\n\s*\1\n", MONITORING[start:], re.S)
    assert found, "the metric has no filter = <<-EOT ... EOT"
    return stack.parse_filter(found.group(2), stack.STAND_INS)


def alert_filter() -> list:
    return stack.parse_filter(stack.alert_filter(MONITORING), stack.STAND_INS)


def test_the_error_line_is_caught_by_the_first_alert_and_not_counted_as_current(host):
    host.replica_check(newest=1000, changed=5000, now=5100)
    line = host.replica_check(newest=1000, changed=5000, now=5000 + BOUND + 1).line
    assert stack.matches(alert_filter(), stack.cos_entry(line))
    assert not stack.matches(metric_filter(), stack.cos_entry(line))


def test_the_current_line_is_counted_and_never_alerts(host):
    line = host.replica_check(newest=1000, changed=1000, now=1100).line
    assert stack.matches(metric_filter(), stack.cos_entry(line))
    assert not stack.matches(alert_filter(), stack.cos_entry(line))


def test_only_the_litestream_containers_lines_are_counted(host):
    line = host.replica_check(newest=1000, changed=1000, now=1100).line
    for container in ("cairn-replica-check", "cairn", "caddy"):
        assert not stack.matches(metric_filter(), stack.cos_entry(line, container=container))
    assert not stack.matches(metric_filter(), stack.cos_entry(line, instance_id="1"))
