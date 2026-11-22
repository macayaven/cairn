"""The smoke check runs the platform it is named for: with --platform, every container it
starts is started for that platform (docker run's own --platform), and a check asks the running
container's own Python which machine it runs on, so a run that silently took the host's image
fails in one sentence. The workflow names the platform wherever it smoke-tests an image."""

import importlib.util
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "smoke_container_platform", REPO / "scripts" / "smoke_container.py"
)
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)
JOBS = yaml.safe_load((REPO / ".github" / "workflows" / "ci-cd.yml").read_text())["jobs"]


def fake_docker(answers: dict[str, str], asked: list):
    def docker(*args, check=True):
        asked.append(args)
        return answers.get(args[0], "")

    return docker


class Bare(smoke.Smoke):
    """The script's own Smoke, with no HTTP: what is under test is how docker is asked."""

    def __init__(self, image: str, platform: str | None = None):
        super().__init__(image, platform)

    def wait_healthy(self, name: str, timeout: float = 0) -> None:
        pass


def test_every_container_is_started_for_the_named_platform(monkeypatch, tmp_path):
    asked: list = []
    answers = {"port": "0.0.0.0:49153", "inspect": "running healthy"}
    monkeypatch.setattr(smoke, "docker", fake_docker(answers, asked))
    run = Bare("cairn:ci", "linux/arm64")
    run.start(f"{tmp_path}:/data")
    (started,) = [args for args in asked if args[0] == "run"]
    assert started[started.index("--platform") + 1] == "linux/arm64"
    assert started.index("--platform") < started.index("cairn:ci")


def test_without_a_platform_nothing_is_added(monkeypatch, tmp_path):
    asked: list = []
    answers = {"port": "0.0.0.0:49153", "inspect": "running healthy"}
    monkeypatch.setattr(smoke, "docker", fake_docker(answers, asked))
    Bare("cairn:ci").start(f"{tmp_path}:/data")
    (started,) = [args for args in asked if args[0] == "run"]
    assert "--platform" not in started


@pytest.mark.parametrize(
    ("platform", "machine", "passes"),
    [
        ("linux/amd64", "x86_64", True),
        ("linux/arm64", "aarch64", True),
        ("linux/arm64", "x86_64", False),
        ("linux/amd64", "aarch64", False),
    ],
)
def test_the_running_container_says_which_machine_it_is(monkeypatch, platform, machine, passes):
    asked: list = []
    monkeypatch.setattr(smoke, "docker", fake_docker({"exec": machine}, asked))
    run = Bare("cairn:ci", platform)
    run.names = ["cairn-smoke-1-1"]
    if passes:
        assert run.runs_on_its_platform() == f"the container runs on {machine}, {platform}"
    else:
        with pytest.raises(smoke.Failed) as failed:
            run.runs_on_its_platform()
        message = str(failed.value)
        assert message.count(". ") == 0 and message.endswith(".")
        assert f"runs on {machine}, not {platform}" in message
    assert asked == [
        (
            "exec",
            "cairn-smoke-1-1",
            "python",
            "-I",
            "-c",
            "import platform; print(platform.machine())",
        )
    ]


def test_without_a_platform_the_check_says_it_has_nothing_to_compare(monkeypatch):
    monkeypatch.setattr(smoke, "docker", fake_docker({"exec": "x86_64"}, []))
    run = Bare("cairn:ci")
    run.names = ["n"]
    assert run.runs_on_its_platform() == "the container runs on x86_64 (no platform was named)"


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (["s", "cairn:ci", "--platform", "linux/riscv64"], 2),
        (["s", "cairn:ci", "--platform"], 2),
        (["s", "cairn:ci", "--colour", "linux/amd64"], 2),
        (["s"], 2),
    ],
)
def test_the_command_line_takes_an_image_and_one_of_the_two_platforms(argv, code, capsys):
    assert smoke.main(argv) == code
    assert (
        "smoke_container.py IMAGE [--platform linux/amd64|linux/arm64]" in capsys.readouterr().err
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["s", "cairn:ci", "--quick"],
        ["s", "cairn:ci", "--platform", "linux/amd64", "--quick"],
        ["s", "--quick", "cairn:ci", "--platform", "linux/arm64"],
        ["s", "cairn:ci", "--quick", "--platform", "linux/amd64"],
    ],
)
def test_quick_flag_runs_only_the_seven_core_checks_regardless_of_position(monkeypatch, argv):
    ran: list[str] = []

    class RecordingSmoke:
        def __init__(self, image: str, platform: str | None = None):
            self.image = image
            self.platform = platform

        def cleanup(self) -> str:
            return "cleaned"

        def __getattr__(self, name: str):
            def _check() -> str:
                ran.append(name)
                return name

            return _check

    monkeypatch.setattr(smoke, "Smoke", RecordingSmoke)
    assert smoke.main(argv) == 0
    assert ran == [
        "starts_healthy",
        "runs_on_its_platform",
        "runs_as_non_root",
        "refuses_without_credentials",
        "mints_a_key_into_the_volume",
        "notes_and_recalls_over_rest",
        "answers_mcp",
    ]


def test_the_check_runs_right_after_the_first_container_is_healthy():
    source = (REPO / "scripts" / "smoke_container.py").read_text(encoding="utf-8")
    checks = source[source.index("checks: list[Callable[[], str]] = [") :]
    names = [line.strip().rstrip(",") for line in checks.splitlines()[1:4]]
    assert names[:2] == ["smoke.starts_healthy", "smoke.runs_on_its_platform"]


def run_lines(job: str) -> list[str]:
    return [s["run"] for s in JOBS[job]["steps"] if "smoke_container.py" in s.get("run", "")]


def test_the_workflow_names_the_platform_wherever_it_smoke_tests():
    (image,) = run_lines("image")
    assert image == 'python3 scripts/smoke_container.py "$LOCAL" --platform "$PLATFORM"'
    (verify,) = run_lines("verify")
    assert 'python3 scripts/smoke_container.py "$image" --platform linux/amd64' in verify
    smoke_step = next(
        s for s in JOBS["verify"]["steps"] if "smoke_container.py" in s.get("run", "")
    )
    assert smoke_step["env"]["QUICK_SMOKE"] == "${{ github.ref_type == 'tag' && '--quick' || '' }}"
