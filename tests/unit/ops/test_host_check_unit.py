"""What can take the server down without cairn saying so: `host.sh host-check`, every five
minutes (cairn-host-check.timer), writes one line for each finding into the log of a running
container, and monitoring.tf has one alert for each kind of line:

  host-check: disk: ...         the data disk or the boot disk above 80 percent
  host-check: memory: ...       a container killed for memory (Docker's `oom` event)
  host-check: restarts: ...     a unit systemd restarted three times or more since the last run
  host-check: certificate: ...  Caddy logged an error obtaining or renewing a certificate

Run from a copy of host.sh (deploy_standins.py) with stand-ins of its own here for docker,
df and systemctl. Each line is evaluated against the filter monitoring.tf gives its alert,
with the evaluator scripts/rehearse_stack.py uses.
"""

import importlib.util
import re
import stat
import subprocess
from pathlib import Path

import pytest

from support.deploy_standins import BASH, Standins
from support.paths import REPO_ROOT

REPO = REPO_ROOT
INFRA = REPO / "infra" / "gcp"
MONITORING = (INFRA / "monitoring.tf").read_text(encoding="utf-8")
TEMPLATE = (INFRA / "cloud-init.yaml.tftpl").read_text(encoding="utf-8")
_spec = importlib.util.spec_from_file_location(
    "rehearse_stack_for_host_check", REPO / "scripts" / "rehearse_stack.py"
)
stack = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stack)

NOW = 1_800_000_000

# docker: `events` prints $S/oom (names, one a line); `logs` prints $S/logs.NAME; `exec -i NAME`
# appends stdin to $S/log.NAME unless $S/down.NAME exists. Every call is recorded.
DOCKER = r"""#!/bin/bash
S=$CHECK_STATE
printf '%s\n' "$*" >>"$S/docker-calls"
case "$1" in
  events)
    [ -e "$S/events-fail" ] && exit 1
    cat "$S/oom" 2>/dev/null
    exit 0
    ;;
  logs)
    for a in "$@"; do name=$a; done
    [ -e "$S/logs.$name" ] || { echo "Error: No such container: $name" >&2; exit 1; }
    cat "$S/logs.$name" >&2
    exit 0
    ;;
  exec)
    name=$3
    [ -e "$S/down.$name" ] && { cat >/dev/null; exit 1; }
    cat >>"$S/log.$name"
    exit 0
    ;;
esac
exit 0
"""
DF = r"""#!/bin/bash
S=$CHECK_STATE
for a in "$@"; do path=$a; done
key=$(printf '%s' "$path" | tr '/' '_')
echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
echo "/dev/sdx 100 0 100 $(cat "$S/df.$key" 2>/dev/null || echo 10)% $path"
"""
SYSTEMCTL = r"""#!/bin/bash
S=$CHECK_STATE
for a in "$@"; do unit=$a; done
cat "$S/restarts.$unit" 2>/dev/null || echo 0
"""
DATE = r"""#!/bin/bash
[ "$1" = "+%s" ] && { cat "$CHECK_STATE/now"; exit 0; }
exec /bin/date "$@"
"""


class Check:
    def __init__(self, tmp_path: Path):
        self.report = Standins(tmp_path / "host").host()
        self.state = tmp_path / "check"
        self.state.mkdir()
        self.bin = tmp_path / "check-bin"
        self.bin.mkdir()
        for name, text in (
            ("docker", DOCKER),
            ("df", DF),
            ("systemctl", SYSTEMCTL),
            ("date", DATE),
        ):
            path = self.bin / name
            path.write_text(text, encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        script = self.report.script
        text = script.read_text(encoding="utf-8")
        assert text.count("HOST_CHECK_DIR=/run/cairn-host-check\n") == 1
        script.write_text(
            text.replace(
                "HOST_CHECK_DIR=/run/cairn-host-check\n", 'HOST_CHECK_DIR="$HOST_ROOT/run-check"\n'
            ),
            encoding="utf-8",
        )
        self.now(NOW)

    def now(self, value: int) -> None:
        (self.state / "now").write_text(str(value), encoding="utf-8")

    def set(self, name: str, value: str) -> None:
        (self.state / name).write_text(value, encoding="utf-8")

    def disk(self, path: str, percent: int) -> None:
        self.set("df." + path.replace("/", "_"), str(percent))

    def run(self) -> subprocess.CompletedProcess:
        env = self.report.s.env({})
        env["PATH"] = f"{self.bin}:{env['PATH']}"
        env.update(HOST_ROOT=str(self.report.root), CHECK_STATE=str(self.state))
        return subprocess.run(
            [BASH, str(self.report.script), "cmd_host_check"],
            env=env,
            cwd=self.report.root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def logged(self, container: str = "litestream") -> list[str]:
        path = self.state / f"log.{container}"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def calls(self) -> list[str]:
        path = self.state / "docker-calls"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


@pytest.fixture
def check(tmp_path):
    return Check(tmp_path)


def policy_filter(name: str) -> list:
    """The filter of the host alert for NAME: one policy for each key of local.host_alerts."""
    start = MONITORING.index('resource "google_monitoring_alert_policy" "host"')
    found = re.search(r"filter\s*=\s*<<-(\w+)\n(.*?)\n\s*\1\n", MONITORING[start:], re.S)
    assert found, name
    return stack.parse_filter(found.group(2), {**stack.STAND_INS, "each.key": name})


KINDS = ("disk", "memory", "restarts", "certificate")


def caught_by(line: str, container: str = "litestream") -> list[str]:
    return [
        kind
        for kind in KINDS
        if stack.matches(policy_filter(kind), stack.cos_entry(line, container=container))
    ]


def test_a_healthy_host_writes_no_line(check):
    check.set("logs.caddy", '{"level":"info","ts":1.7e9,"logger":"tls","msg":"cleaning storage"}\n')
    done = check.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert check.logged() == []
    assert "host check: nothing found" in done.stdout


@pytest.mark.parametrize(
    ("path", "label"), [("data", "the data disk"), ("/var/lib/docker", "the boot disk")]
)
def test_a_disk_above_80_percent_is_a_line_the_disk_alert_reads(check, path, label):
    where = str(check.report.root / "data") if path == "data" else path
    check.disk(where, 81)
    done = check.run()
    assert done.returncode == 0, done.stdout + done.stderr
    [line] = check.logged()
    assert line == f"host-check: disk: {label} ({where}) is 81% full, over 80%"
    assert caught_by(line) == ["disk"]


def test_a_disk_at_80_percent_is_not_over(check):
    check.disk("/var/lib/docker", 80)
    check.run()
    assert check.logged() == []


def test_a_container_killed_for_memory_is_a_line_the_memory_alert_reads(check):
    check.set("oom", "cairn\n")
    check.set("since-file", "")
    done = check.run()
    assert done.returncode == 0, done.stdout + done.stderr
    [line] = check.logged()
    assert line == "host-check: memory: the container cairn was killed for memory"
    assert caught_by(line) == ["memory"]
    [events] = [c for c in check.calls() if c.startswith("events ")]
    assert f"--since {NOW - 300} --until {NOW}" in events
    assert "--filter event=oom" in events


def test_the_next_run_asks_for_events_since_the_last_one(check):
    check.run()
    check.now(NOW + 301)
    check.run()
    events = [c for c in check.calls() if c.startswith("events ")]
    assert f"--since {NOW} --until {NOW + 301}" in events[-1]


def test_three_restarts_since_the_last_run_are_a_restart_loop(check):
    check.set("restarts.cairn.service", "1")
    check.run()
    assert check.logged() == []
    check.set("restarts.cairn.service", "4")
    check.now(NOW + 300)
    check.run()
    [line] = check.logged()
    assert (
        line == "host-check: restarts: systemd restarted cairn.service 3 times since the last check"
    )
    assert caught_by(line) == ["restarts"]


def test_two_restarts_are_not_a_loop_and_a_reboot_starts_the_count_again(check):
    check.set("restarts.caddy.service", "7")
    check.run()
    [first] = check.logged()  # seven since boot on the first run
    assert "caddy.service 7 times" in first
    check.set("restarts.caddy.service", "9")
    check.run()
    check.set("restarts.caddy.service", "2")  # fewer than before: the VM restarted
    check.run()
    assert check.logged() == [first]


def test_caddy_failing_to_renew_a_certificate_is_a_line_the_certificate_alert_reads(check):
    check.set(
        "logs.caddy",
        '{"level":"info","ts":1.7e9,"logger":"tls.renew","msg":"renewing certificate"}\n'
        '{"level":"error","ts":1.7e9,"logger":"tls.renew","msg":"could not get certificate'
        ' from issuer","identifier":"cairn.example.test"}\n'
        '{"level":"error","ts":1.7e9,"logger":"tls.obtain","msg":"will retry"}\n'
        '{"level":"error","ts":1.7e9,"logger":"http.log.access","msg":"handled request"}\n',
    )
    done = check.run()
    assert done.returncode == 0, done.stdout + done.stderr
    [line] = check.logged()
    assert line == (
        "host-check: certificate: Caddy logged 2 errors obtaining or renewing a certificate"
        " since the last check"
    )
    assert caught_by(line) == ["certificate"]
    [logs] = [c for c in check.calls() if c.startswith("logs ")]
    assert logs == f"logs --since {NOW - 300} --until {NOW} caddy"


def test_with_litestream_down_the_line_goes_into_cairns_log(check):
    check.set("down.litestream", "")
    check.disk("/var/lib/docker", 95)
    done = check.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert check.logged() == []
    [line] = check.logged("cairn")
    assert caught_by(line, container="cairn") == ["disk"]


def test_with_no_container_running_the_run_fails_in_the_journal(check):
    for name in ("litestream", "cairn", "caddy"):
        check.set(f"down.{name}", "")
    check.disk("/var/lib/docker", 95)
    done = check.run()
    assert done.returncode == 1
    assert "host-check: disk: " in done.stdout
    assert "no container runs to write into" in done.stderr


def test_docker_events_that_cannot_be_read_fail_the_run(check):
    check.set("events-fail", "")
    done = check.run()
    assert done.returncode == 1
    assert "could not read Docker's events" in done.stderr


def test_no_line_is_one_the_replica_alerts_read(check):
    check.disk("/var/lib/docker", 95)
    check.set("oom", "caddy\n")
    check.set("restarts.litestream.service", "5")
    check.set("logs.caddy", '{"level":"error","ts":1.7e9,"logger":"tls.renew","msg":"x"}\n')
    check.run()
    lines = check.logged()
    assert len(lines) == 4
    replica = stack.parse_filter(stack.alert_filter(MONITORING), stack.STAND_INS)
    for line in lines:
        assert not stack.matches(replica, stack.cos_entry(line)), line
        assert not line.startswith("replica: current, ")


def test_each_alert_emails_at_most_hourly_and_closes_half_an_hour_after_the_last_line():
    start = MONITORING.index('resource "google_monitoring_alert_policy" "host"')
    block = MONITORING[start : MONITORING.index("\n}\n", start)]
    assert "for_each = local.host_alerts" in block
    assert 'period = "3600s"' in block
    assert 'auto_close = "1800s"' in block
    assert "google_monitoring_notification_channel.email.name" in block
    locals_start = MONITORING.index("host_alerts = {")
    for kind in KINDS:
        assert f'"{kind}" = {{' in MONITORING[locals_start:], kind


def test_a_timer_runs_it_every_five_minutes():
    start = TEMPLATE.index("  - path: /etc/systemd/system/cairn-host-check.timer\n")
    timer = TEMPLATE[start : TEMPLATE.index("\n\n#", start)]
    assert "OnBootSec=5min" in timer
    assert "OnCalendar=*:0/5" in timer
    start = TEMPLATE.index("  - path: /etc/systemd/system/cairn-host-check.service\n")
    service = TEMPLATE[start : TEMPLATE.index("\n  - path: ", start)]
    assert "ExecStart=/bin/bash /etc/cairn/host.sh host-check" in service
    assert "Type=oneshot" in service
    runcmd = TEMPLATE[TEMPLATE.index("runcmd:") :]
    assert "cairn-host-check.timer" in runcmd


def test_host_sh_dispatches_host_check():
    text = (INFRA / "files" / "host.sh").read_text(encoding="utf-8")
    assert re.search(r"^  host-check\) cmd_host_check ;;$", text, re.M)


def test_every_line_the_readme_quotes_is_one_host_check_writes(check):
    readme = (INFRA / "README.md").read_text(encoding="utf-8")
    start = readme.index("**The host alerts**")
    part = readme[start : readme.index("\n\n", readme.index("| certificate |", start))]
    quoted = re.findall(r"`(host-check: [^`]+)`", part)
    assert [q.split(":")[1].strip() for q in quoted] == list(KINDS)
    check.disk(str(check.report.root / "data"), 95)
    check.set("oom", "caddy\n")
    check.set("restarts.litestream.service", "5")
    check.set("logs.caddy", '{"level":"error","ts":1.7e9,"logger":"tls.renew","msg":"x"}\n')
    check.run()
    lines = check.logged()
    holes = {"NN": r"\d+", "N": r"\d+", "NAME": r"\S+", "UNIT": r"\S+"}
    for line in quoted:
        parts = re.split(r"\b(NN|N|NAME|UNIT)\b", line.replace("/mnt/disks/data", "@DATA@"))
        pattern = "".join(holes.get(p, re.escape(p)) for p in parts)
        pattern = pattern.replace(re.escape("@DATA@"), re.escape(str(check.report.root / "data")))
        assert any(re.fullmatch(pattern, x) for x in lines), line
    # The numbers the README states are host.sh's.
    text = (INFRA / "files" / "host.sh").read_text(encoding="utf-8")
    assert "HOST_CHECK_DISK_PERCENT=80\n" in text and "more than 80 percent" in part
    assert "HOST_CHECK_RESTARTS=3\n" in text and "three times or more" in part
