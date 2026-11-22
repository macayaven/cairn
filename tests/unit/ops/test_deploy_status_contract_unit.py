"""The status line between the VM and the deploy job: host.sh writes it, the deploy script reads it.

host.sh's `report_status` runs from a copy of host.sh (tests/unit/deploy_standins.py says
which three lines the copy changes, and checks each is still there once) against a stand-in
data disk, docker and curl. The line it PUTs to the guest attribute is served to the real
`deploy_production.sh wait`, which must reach the verdict the guide gives for that state.
"""

import re

import pytest

from support.deploy_standins import (
    DEPLOY_SCRIPT,
    HOST_SCRIPT,
    IMAGE,
    MISSING_TOOLS,
    NEW,
    OLD,
    README,
    Standins,
    status_line,
)

pytestmark = pytest.mark.skipif(
    bool(MISSING_TOOLS), reason=f"the deploy script needs {', '.join(MISSING_TOOLS)}"
)


def fields(line: str) -> list[str]:
    return [word.split("=", 1)[0] for word in line.split()]


@pytest.fixture
def s(tmp_path):
    return Standins(tmp_path)


@pytest.fixture
def host(s):
    return s.host()


def test_the_deploy_script_reads_only_fields_host_sh_writes(host):
    host.record("image", f"{IMAGE}@{OLD}")
    host.running(OLD, healthy=True)
    written = fields(host.report())
    read = set(re.findall(r'field "\$line" (\w+)', DEPLOY_SCRIPT.read_text(encoding="utf-8")))
    assert {"seq", "running", "healthy", "proven", "failed", "hold"} <= read
    assert read <= set(written)
    # `updated` is the VM's clock, for people (README, "How the VM reports"); nothing reads it.
    assert set(written) - read == {"updated"}


def test_the_guides_example_line_has_the_fields_host_sh_writes_in_its_order(host):
    example = re.findall(r"^seq=\S+(?: [a-z]+=\S+)+$", README.read_text(encoding="utf-8"), re.M)
    assert len(example) == 1, example
    assert fields(example[0]) == fields(host.report())


def test_the_lines_the_deploy_tests_build_have_host_sh_fields_in_its_order(host):
    """tests/unit/test_deploy_production_unit.py builds its status lines by hand."""
    assert fields(status_line(1)) == fields(host.report())


def test_both_scripts_name_the_same_guest_attribute():
    pattern = re.compile(r"^STATUS_ATTRIBUTE=(\S+)$", re.M)
    host = pattern.findall(HOST_SCRIPT.read_text(encoding="utf-8"))
    deploy = pattern.findall(DEPLOY_SCRIPT.read_text(encoding="utf-8"))
    assert host == deploy == ["cairn/status"]


def test_seq_grows_by_one_per_report_and_starts_again_on_a_blank_disk(host):
    assert [fields_of(host.report())["seq"] for _ in range(3)] == ["1", "2", "3"]
    assert (host.data / "deploy" / "status-seq").read_text() == "3\n"
    (host.data / "deploy" / "status-seq").unlink()
    assert fields_of(host.report())["seq"] == "1"
    host.mounted(False)
    assert fields_of(host.report())["seq"] == "0"


def fields_of(line: str) -> dict[str, str]:
    return dict(word.split("=", 1) for word in line.split())


def serve_and_wait(s, line: str, after: str = "0", timeout: str = "0"):
    s.statuses(line)
    return s.deploy("wait", NEW, CAIRN_AFTER_SEQ=after, CAIRN_DEPLOY_TIMEOUT=timeout)


def test_a_promoted_healthy_deployment_is_a_success(s, host):
    host.record("image", f"{IMAGE}@{NEW}")
    host.running(NEW, healthy=True)
    result = serve_and_wait(s, host.report())
    assert result.rc == 0, result.text
    assert f"deploy: the VM runs {NEW} and /healthz answers" in result.out


def test_a_rollback_to_a_healthy_earlier_image_fails_and_says_so(s, host):
    host.record("image", f"{IMAGE}@{OLD}")
    host.record("failed-image", f"{IMAGE}@{NEW}")
    host.running(OLD, healthy=True)
    result = serve_and_wait(s, host.report(), timeout="60")
    assert result.rc == 1
    assert f"the VM runs the earlier image {OLD}, healthy" in result.err


def test_a_rollback_where_nothing_is_healthy_fails_and_says_so(s, host):
    host.record("image", f"{IMAGE}@{OLD}")
    host.record("failed-image", f"{IMAGE}@{NEW}")
    host.running(OLD, healthy=False)
    result = serve_and_wait(s, host.report(), timeout="60")
    assert result.rc == 1
    assert f"the VM rolled back from {NEW} and nothing healthy runs now" in result.err


def test_a_hold_fails_the_wait(s, host):
    host.record("image", f"{IMAGE}@{OLD}")
    host.record("hold", "held since 2026-11-10T08:00:00Z")
    host.running(OLD, healthy=True)
    line = host.report()
    assert fields_of(line)["hold"] == "yes"
    result = serve_and_wait(s, line, timeout="60")
    assert result.rc == 1
    assert "deployments are on hold on the VM" in result.err


def test_a_deployment_in_its_health_check_is_not_yet_a_success(s, host):
    host.record("image", f"{IMAGE}@{OLD}")
    host.record("pending", f"{IMAGE}@{NEW}", "a-boot-id")
    host.running(NEW, healthy=True)
    result = serve_and_wait(s, host.report())
    assert result.rc == 1
    assert f"deploy: deploying: {NEW} is in its health check" in result.out
    assert "within 0 s" in result.err


def test_a_report_from_before_the_move_decides_nothing(s, host):
    host.record("image", f"{IMAGE}@{NEW}")
    host.running(NEW, healthy=True)
    line = host.report()
    result = serve_and_wait(s, line, after=fields_of(line)["seq"])
    assert result.rc == 1
    assert "no report after #1 yet" in result.err


def test_an_image_that_is_not_a_digest_of_the_configured_one_is_reported_as_other(host):
    (host.s.state / "docker-running").write_text("docker.io/someone/else:latest\n")
    host.s.set_file("docker-healthz-rc", "0")
    assert fields_of(host.report())["running"] == "other"
