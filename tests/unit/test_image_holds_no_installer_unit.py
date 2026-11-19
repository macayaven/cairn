"""The image that ships carries no package installer, and the base image's Debian packages
come up to the security archive's fixed versions in the build.

The scan's first run on GitHub (Trivy 0.76.2, 2026-11-19) failed on six HIGH findings with a
fix, none of them cairn's own dependencies: `zlib1g` in the base image (fixed in
`1:1.3.dfsg+really1.3.1-1+deb13u1`, in trixie-security and not yet in the newest
`python:3.13-slim` index, whose digest is the one the Dockerfile pins), and `msgpack`,
`urllib3` and `setuptools`, which are the copies pip 26.2.2 carries in `pip/_vendor` (its
`vendor.txt` names exactly the versions Trivy reported). cairn needs neither pip nor
setuptools at run time, so the final stage removes pip, and the bundled wheel `ensurepip`
would put back; it upgrades the base image's packages from Debian's archive before it drops
root. `scripts/smoke_container.py` checks the first in the running image."""

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = (REPO / "Dockerfile").read_text(encoding="utf-8")
_spec = importlib.util.spec_from_file_location(
    "smoke_container_no_installer", REPO / "scripts" / "smoke_container.py"
)
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)


def final_stage() -> list[str]:
    """The final stage's instructions, continuation lines joined."""
    text = DOCKERFILE.replace("\\\n", " ")
    lines = [line.strip() for line in text.splitlines()]
    instructions = [line for line in lines if line and not line.startswith("#")]
    starts = [i for i, line in enumerate(instructions) if line.startswith("FROM ")]
    return instructions[starts[-1] :]


def runs(stage: list[str]) -> list[str]:
    return [" ".join(line.split()) for line in stage if line.startswith("RUN ")]


def test_the_final_stage_removes_pip_and_the_wheel_that_would_bring_it_back():
    stage = final_stage()
    commands = " && ".join(runs(stage))
    assert "python -m pip uninstall --yes pip" in commands
    assert "rm -rf /usr/local/lib/python3.13/ensurepip/_bundled" in commands
    # Removed as root, before the server's user takes over.
    user = next(i for i, line in enumerate(stage) if line.startswith("USER "))
    removing = next(i for i, line in enumerate(stage) if "pip uninstall" in line)
    assert removing < user


def test_the_final_stage_upgrades_the_base_packages_from_debian_and_keeps_no_lists():
    stage = final_stage()
    (upgrade,) = [run for run in runs(stage) if "apt-get" in run]
    assert "apt-get update" in upgrade
    assert "DEBIAN_FRONTEND=noninteractive apt-get upgrade --yes" in upgrade
    assert upgrade.endswith("rm -rf /var/lib/apt/lists/*")
    # It installs nothing new: an upgrade, no `apt-get install`.
    assert "apt-get install" not in upgrade
    user = next(i for i, line in enumerate(stage) if line.startswith("USER "))
    assert stage.index(next(line for line in stage if "apt-get" in line)) < user


def test_the_header_says_the_base_is_pinned_and_its_packages_are_upgraded_in_the_build():
    header = " ".join(DOCKERFILE.split("FROM ")[0].replace("#", "").split())
    assert "a later build runs the same bytes as this one" not in header
    assert (
        "The final stage upgrades the base image's Debian packages from Debian's archive"
        " (its security fixes reach the archive before a new index of the base image), so"
        " two builds of the same commit can differ in those packages." in header
    )


# --- the smoke check -------------------------------------------------------------------------


class FakeSmoke(smoke.Smoke):
    def __init__(self):  # no docker, no temporary directory
        self.names = ["cairn-smoke-test-1"]


def found(**changes) -> str:
    report = {"pip": False, "setuptools": False, "commands": [], "bundled": []}
    report.update(changes)
    return json.dumps(report)


@pytest.mark.parametrize(
    "report",
    [
        found(pip=True),
        found(setuptools=True),
        found(commands=["/usr/local/bin/pip"]),
        found(bundled=["pip-26.2.2-py3-none-any.whl"]),
    ],
)
def test_the_smoke_check_fails_in_one_sentence_when_an_installer_is_left(monkeypatch, report):
    asked = []

    def docker(*args, check=True):
        asked.append(args)
        return report

    monkeypatch.setattr(smoke, "docker", docker)
    with pytest.raises(smoke.Failed) as failed:
        FakeSmoke().carries_no_installer()
    message = str(failed.value)
    assert message.count(". ") == 0 and message.endswith(".")
    assert asked == [("exec", "cairn-smoke-test-1", "python", "-I", "-c", smoke.INSTALLER_PROBE)]


def test_the_smoke_check_passes_when_nothing_is_left(monkeypatch):
    monkeypatch.setattr(smoke, "docker", lambda *args, check=True: found())
    assert FakeSmoke().carries_no_installer() == (
        "no pip, no setuptools, no pip command and no bundled wheel"
    )


def test_the_probe_runs_as_a_program_of_its_own():
    """What the container runs is one JSON line; run here, it reports this machine."""
    printed = []
    exec(smoke.INSTALLER_PROBE, {"print": printed.append})
    report = json.loads(printed[0])
    assert set(report) == {"pip", "setuptools", "commands", "bundled"}
    assert isinstance(report["pip"], bool) and isinstance(report["commands"], list)


def test_the_smoke_run_includes_the_check():
    source = Path(smoke.__file__).read_text(encoding="utf-8")
    start = "checks: list[Callable[[], str]] = ["
    checks = source[source.index(start) + len(start) :]
    assert "smoke.carries_no_installer," in checks[: checks.index("]")]
    assert re.search(r"the image's own Python finds no pip", smoke.__doc__.replace("\n", " "))
