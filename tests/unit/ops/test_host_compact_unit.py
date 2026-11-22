"""host.sh's `compact`, the compaction an operator runs by hand with the replica service
stopped for its seconds, run from a copy of host.sh against stand-ins
(tests/unit/deploy_standins.py).

Beside a running Litestream the last step of a compaction, truncating the write-ahead log,
never finishes (docs/compaction-beside-a-replica.md). The command checks that cairn answers
and the replica is current, stops litestream.service, runs cairn's own `compact` in the
running container, starts litestream.service again whatever happened, and waits until the
replica holds a copy made after the stop. The stand-in systemctl records the stop and the
start; the stand-in docker answers the health check, the replica probe (four numbers: the
newest copy, the last change, Litestream's start, now) and the compaction record.
"""

import re

import pytest

from support.deploy_standins import HOST_MAIN_MARKER, HOST_SCRIPT, Standins

CURRENT = "1000 1000 0 1100\n"  # the check before: the newest copy holds the last change
OLD_COPY = "1000 1000 0 1100\n"  # after the start: no copy newer than the stop yet
NEW_COPY = "4102444800 4102444800 4102444801 4102444900\n"  # a copy made after the stop
SEQUENCE = ["stop litestream", "compact", "start litestream"]


@pytest.fixture
def host(tmp_path):
    report = Standins(tmp_path).host()
    report.bound(900)
    report.files(db=2891776, wal=14045112)
    return report


def lines(done) -> list[str]:
    return [line.removeprefix("cairn-host: ") for line in done.out.splitlines()]


def test_the_sequence_stops_compacts_starts_and_waits_for_a_new_copy(host):
    truncate = ': >"$HOST_ROOT/data/cairn/cairn.db-wal"\n'
    shrink = 'head -c 1609728 /dev/zero >"$HOST_ROOT/data/cairn/cairn.db"\n'
    done = host.compact([CURRENT, OLD_COPY, OLD_COPY, NEW_COPY], compact_does=truncate + shrink)
    assert done.rc == 0, done.text
    assert host.events() == SEQUENCE
    # By hand it is the whole compaction: no --if-due (the nightly run's, its own tests).
    assert host.compact_args() == ["exec cairn python -m cairn.server compact --db /data/cairn.db"]
    said = lines(done)
    assert said[0] == "compact: step 1/6: cairn answers /healthz"
    assert said[1] == "compact: step 2/6: replica: current, newest 100 s old"
    assert said[2] == (
        "compact: before: database 2891776 bytes, write-ahead log 14045112 bytes;"
        " a deletion waits for a compaction: yes, since 2026-11-17T04:27:54+00:00"
    )
    assert said[3] == "compact: step 3/6: litestream.service stopped"
    assert "compacted /data/cairn.db: 2891776 -> 1609728 bytes" in said
    assert "compact: step 4/6: compact finished" in said
    assert (
        "compact: after: database 1609728 bytes, write-ahead log 0 bytes;"
        " a deletion waits for a compaction: no"
    ) in said
    started = [x for x in said if x.startswith("compact: step 5/6: ")]
    assert len(started) == 1
    assert re.fullmatch(
        r"compact: step 5/6: litestream\.service started; it was stopped for \d+ s", started[0]
    )
    current = [x for x in said if x.startswith("compact: step 6/6: ")]
    assert current == [
        "compact: step 6/6: replica current again: it holds a copy made after the stop"
        " (3 checks after the start)"
    ]
    assert said[-1] == "compact: done"


def test_the_sizes_after_are_read_before_litestream_starts_again(host):
    """Litestream may write as it starts; the sizes after are the compaction's own."""
    done = host.compact([CURRENT, NEW_COPY])
    said = lines(done)
    after = next(i for i, x in enumerate(said) if x.startswith("compact: after: "))
    start = next(i for i, x in enumerate(said) if x.startswith("compact: step 5/6: "))
    assert after < start


def test_a_missing_log_file_counts_as_zero_bytes(host):
    host.files(db=4096, wal=None)
    done = host.compact([CURRENT, NEW_COPY])
    assert done.rc == 0, done.text
    assert "write-ahead log 0 bytes;" in done.out


def test_cairn_not_answering_stops_it_before_anything_changes(host):
    done = host.compact([CURRENT, NEW_COPY], healthy=False)
    assert done.rc != 0
    assert host.events() == []
    assert "step 1/6" in done.err and "/healthz" in done.err
    assert "nothing was changed" in done.err


def test_a_replica_that_is_not_current_stops_it_before_anything_changes(host):
    """The same check replica-check makes: an Error line, or a check that cannot run."""
    over = "1000 5000 0 99999\n"  # a change waits past the bound for its copy
    done = host.compact([over, NEW_COPY])
    assert done.rc != 0
    assert host.events() == []
    assert "step 2/6" in done.err and "nothing was changed" in done.err
    assert "Error: replica: newest" in done.text


def test_a_replica_that_cannot_be_listed_stops_it_before_anything_changes(tmp_path):
    report = Standins(tmp_path).host()
    report.bound(900)
    report.files(db=4096, wal=0)
    report.s.set_file("docker-run-out.1", "")
    report.s.set_file("docker-run-rc.1", "1")
    done = report.compact(["", NEW_COPY])
    assert done.rc != 0
    assert report.events() == []
    assert "step 2/6" in done.err


def test_a_compaction_that_does_not_finish_still_starts_litestream_and_fails(host):
    done = host.compact(
        [CURRENT, NEW_COPY],
        compact_rc=1,
        compact_out="",
        records=("2026-11-17T04:27:54+00:00", "2026-11-17T04:27:54+00:00"),
    )
    assert done.rc != 0
    assert host.events() == SEQUENCE
    assert "step 4/6" in done.err and "exited 1" in done.err
    [after] = [x for x in lines(done) if x.startswith("compact: after: ")]
    assert after.endswith("a deletion waits for a compaction: yes, since 2026-11-17T04:27:54+00:00")
    assert any(x.startswith("compact: step 6/6: replica current again") for x in lines(done))
    assert "failed at step 4/6 (compact)" in done.err


@pytest.mark.parametrize("signal", ["INT", "TERM", "HUP"])
def test_an_interrupt_during_the_compaction_starts_litestream_again(host, signal):
    """Ctrl-C, a kill, a lost SSH session: the trap starts litestream.service before host.sh
    exits, and the exit status says it did not finish."""
    done = host.compact([CURRENT, NEW_COPY], signal=signal)
    assert done.rc != 0
    assert host.events() == SEQUENCE
    assert "interrupted" in done.err
    assert "litestream.service started again" in done.out


def test_a_stop_that_fails_is_undone_and_nothing_is_compacted(host):
    done = host.compact([CURRENT, NEW_COPY], stop_fails=True)
    assert done.rc != 0
    assert host.events() == ["stop litestream", "start litestream"]
    assert "step 3/6" in done.err and "nothing was compacted" in done.err


def test_a_start_that_fails_is_said_loudly_with_what_to_type(host):
    done = host.compact([CURRENT, NEW_COPY], start_fails=True)
    assert done.rc != 0
    assert host.events() == SEQUENCE
    assert "step 5/6" in done.err
    assert "sudo systemctl start litestream" in done.err
    assert "failed at step 5/6" in done.err


def test_a_replica_that_takes_no_new_copy_fails_after_a_bounded_wait(host):
    done = host.compact([CURRENT, OLD_COPY])
    assert done.rc != 0
    assert host.events() == SEQUENCE
    assert "step 6/6" in done.err
    assert "no copy made after the stop after 24 checks, 5 s apart" in done.err
    run_count = len(host.docker_runs())
    assert run_count == 1 + 24  # the check before, then the bounded wait
    assert "failed at step 6/6" in done.err


def test_the_compaction_runs_in_the_running_cairn_container_as_the_run_guide_shows(host):
    host.compact([CURRENT, NEW_COPY])
    execs = [argv for argv in host.s.calls("docker") if argv[:1] == ["exec"]]
    compact = [argv for argv in execs if "compact" in argv and "cairn.server" in argv]
    assert compact == [
        ["exec", "cairn", "python", "-m", "cairn.server", "compact", "--db", "/data/cairn.db"]
    ]
    stops = [argv for argv in host.s.calls("systemctl") if argv[:1] == ["stop"]]
    assert stops == [["stop", "litestream.service"]]


def test_the_command_is_dispatched_and_named_in_the_usage_and_the_header():
    text = HOST_SCRIPT.read_text(encoding="utf-8")
    _, _, dispatch = text.partition(HOST_MAIN_MARKER)
    assert re.search(r"^  compact\) cmd_compact ;;$", dispatch, re.M)
    assert "|compact|" in dispatch.split("usage: host.sh", 1)[1].splitlines()[0]
    assert re.search(r"^#   compact +", text, re.M)
