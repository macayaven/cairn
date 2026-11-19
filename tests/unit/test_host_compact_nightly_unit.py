"""Compaction after a deletion does not wait for a person: cairn-compact.timer runs
`host.sh compact-if-due` once a night, which runs `host.sh compact` when, and only when, a
deletion waits for a compaction (the record store.compact_due). On success it logs one line
with the sizes before and after; on failure Litestream runs again (compact's own trap) and an
`Error:` line goes into the litestream container's log, where the replica alert reads it.

Run from a copy of host.sh against stand-ins (deploy_standins.py), as test_host_compact_unit.py
runs `compact` itself.
"""

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest
from deploy_standins import BASH, Standins

REPO = Path(__file__).resolve().parents[2]
INFRA = REPO / "infra" / "gcp"
MONITORING = (INFRA / "monitoring.tf").read_text(encoding="utf-8")
TEMPLATE = (INFRA / "cloud-init.yaml.tftpl").read_text(encoding="utf-8")
_spec = importlib.util.spec_from_file_location(
    "rehearse_stack_for_nightly", REPO / "scripts" / "rehearse_stack.py"
)
stack = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stack)

CURRENT = "1000 1000 0 1100\n"
NEW_COPY = "4102444800 4102444800 4102444801 4102444900\n"
DUE = "2026-11-17T04:27:54+00:00"
SHRINK = (
    ': >"$HOST_ROOT/data/cairn/cairn.db-wal"\n'
    'head -c 1609728 /dev/zero >"$HOST_ROOT/data/cairn/cairn.db"\n'
)


@pytest.fixture
def host(tmp_path):
    report = Standins(tmp_path).host()
    report.bound(900)
    report.files(db=2891776, wal=14045112)
    return report


def nightly(
    host,
    probes: list[str],
    *,
    records: tuple[str, ...],
    healthy: bool = True,
    compact_rc: int = 0,
    compact_does: str = SHRINK,
    start_fails: bool = False,
):
    """Run cmd_compact_if_due. RECORDS are what each read of store.compact_due prints, in
    order: the nightly check's own, then compact's before and after."""
    s = host.s
    s.set_file("docker-healthz-rc", "0" if healthy else "1")
    for i, said in enumerate(probes, 1):
        s.set_file(f"docker-run-out.{i}", said)
    s.set_file("docker-run-out", probes[-1])
    s.set_file("docker-run-rc", "0")
    for i, value in enumerate(records, 1):
        s.set_file(f"docker-record.{i}", f"{value}\n")
    s.set_file("docker-record.last", f"{records[-1]}\n")
    s.set_file("docker-compact-out", "compacted /data/cairn.db: 2891776 -> 1609728 bytes\n")
    s.set_file("docker-compact-rc", str(compact_rc))
    s.set_file("docker-compact-does", compact_does)
    if start_fails:
        s.set_file("systemctl-start-fails", "")
    return subprocess.run(
        [BASH, str(host.script), "cmd_compact_if_due"],
        env={**s.env({}), "HOST_ROOT": str(host.root)},
        cwd=host.root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_with_no_deletion_waiting_nothing_is_stopped_or_compacted(host):
    done = nightly(host, [CURRENT], records=("",))
    assert done.returncode == 0, done.stdout + done.stderr
    assert host.events() == []
    assert done.stdout.splitlines() == [
        "cairn-host: compact: nightly: no deletion waits for a compaction; nothing to do"
    ]
    assert host.litestream_log() == []


def test_with_a_deletion_waiting_it_compacts_as_by_hand_and_logs_one_line_of_sizes(host):
    done = nightly(host, [CURRENT, NEW_COPY], records=(DUE, DUE, ""))
    assert done.returncode == 0, done.stdout + done.stderr
    assert host.events() == ["stop litestream", "compact", "start litestream"]
    said = done.stdout.splitlines()
    assert "cairn-host: compact: done" in said
    summary = [x for x in said if x.startswith("cairn-host: compact: nightly: ")]
    assert summary == [
        "cairn-host: compact: nightly: database 2891776 -> 1609728 bytes,"
        " write-ahead log 14045112 -> 0 bytes"
    ]
    assert said[-1] == summary[0]
    # Step 2's replica check writes its own current line there; nothing else is written.
    assert [x for x in host.litestream_log() if not x.startswith("replica: current, ")] == []


@pytest.mark.parametrize(
    ("case", "kwargs", "step"),
    [
        ("cairn down", {"healthy": False}, "step 1/6: cairn does not answer /healthz"),
        ("compact fails", {"compact_rc": 3, "compact_does": ""}, "failed at step 4/6 (compact)"),
        ("no new copy", {}, "failed at step 6/6 (replica)"),
    ],
)
def test_a_failure_leaves_litestream_running_and_writes_an_error_line_the_replica_alert_reads(
    host, case, kwargs, step
):
    probes = [CURRENT, CURRENT]  # after the start, no copy newer than the stop
    if case != "no new copy":
        probes = [CURRENT, NEW_COPY]
    done = nightly(host, probes, records=(DUE, DUE, DUE), **kwargs)
    assert done.returncode == 1
    events = host.events()
    if "stop litestream" in events:
        assert events[-1] == "start litestream"
    [line] = [x for x in host.litestream_log() if x.startswith("Error: compact: ")]
    assert line.startswith("Error: compact: the nightly compaction failed: ")
    assert step in line
    alert = stack.parse_filter(stack.alert_filter(MONITORING), stack.STAND_INS)
    assert stack.matches(alert, stack.cos_entry(line))
    # The same words in the journal, at error priority.
    words = line.removeprefix("Error: ")
    assert f"<3>cairn-host: ERROR: {words}" in done.stderr.splitlines()


def test_a_record_that_cannot_be_read_is_a_failure_too(host):
    done = nightly(host, [CURRENT], records=("?",))
    # The stand-in prints "?" as the record; host.sh reads that as "could not read".
    assert done.returncode == 1
    assert host.events() == []
    [line] = host.litestream_log()
    assert line == (
        "Error: compact: the nightly compaction failed: whether a deletion waits could not be"
        " read (is cairn running?)"
    )


def unit(name: str) -> str:
    start = TEMPLATE.index(f"  - path: /etc/systemd/system/{name}\n")
    end = re.compile(r"^(  - path: |\S)", re.M).search(TEMPLATE, start + 1)
    return TEMPLATE[start : end.start() if end else len(TEMPLATE)]


def test_a_timer_runs_it_once_a_night_before_the_disk_snapshot():
    timer = unit("cairn-compact.timer")
    assert "OnCalendar=*-*-* 03:00:00 UTC\n" in timer
    service = unit("cairn-compact.service")
    assert "Type=oneshot\n" in service
    assert "ExecStart=/bin/bash /etc/cairn/host.sh compact-if-due\n" in service
    assert "Requires=cairn-disk.service cairn-network.service\n" in service
    # The snapshot of the data disk is taken at 04:00, after the night's compaction.
    main = (INFRA / "main.tf").read_text(encoding="utf-8")
    assert re.search(r'start_time\s*=\s*"04:00"', main)
    runcmd = TEMPLATE[TEMPLATE.index("runcmd:") :]
    assert "cairn-compact.timer" in runcmd


def test_host_sh_dispatches_compact_if_due():
    text = (INFRA / "files" / "host.sh").read_text(encoding="utf-8")
    assert re.search(r"^  compact-if-due\) cmd_compact_if_due ;;$", text, re.M)


def test_every_nightly_line_the_runbook_quotes_is_one_compact_if_due_prints(tmp_path):
    readme = (INFRA / "README.md").read_text(encoding="utf-8")
    start = readme.index("### After a workspace is deleted")
    section = readme[start : readme.index("\n## ", start)]
    quoted = re.findall(r"`((?:Error: )?compact: (?:nightly|the nightly)[^`]*)`", section)
    assert len(quoted) == 3, quoted
    printed = []
    for i, (probes, records, kwargs) in enumerate(
        [
            ([CURRENT], ("",), {}),
            ([CURRENT, NEW_COPY], (DUE, DUE, ""), {}),
            ([CURRENT, NEW_COPY], (DUE, DUE, DUE), {"healthy": False}),
        ]
    ):
        report = Standins(tmp_path / str(i)).host()
        report.bound(900)
        report.files(db=2891776, wal=14045112)
        done = nightly(report, probes, records=records, **kwargs)
        printed += [x.removeprefix("cairn-host: ") for x in done.stdout.splitlines()]
        printed += report.litestream_log()
    for line in quoted:
        parts = re.split(r"(\bN\b|\.\.\.)", line)
        holes = {"N": r"\d+", "...": r".*"}
        pattern = re.compile("".join(holes.get(p, re.escape(p)) for p in parts))
        assert any(pattern.fullmatch(x) for x in printed), line
