"""The VM starts its three containers as the Compose files do: no Linux capability, no
privilege gained by exec, Caddy alone with NET_BIND_SERVICE back; and each with a memory
limit and a process limit from config.env, which OpenTofu renders from container_limits.

host.sh's run-cairn, run-caddy and run-litestream run from a copy against stand-ins
(tests/unit/deploy_standins.py); the stand-in docker records each `docker run`.
"""

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from deploy_standins import BASH, IMAGE, NEW, Standins

REPO = Path(__file__).resolve().parents[2]
INFRA = REPO / "infra" / "gcp"
HOST_SH = (INFRA / "files" / "host.sh").read_text(encoding="utf-8")
VARIABLES = (INFRA / "variables.tf").read_text(encoding="utf-8")
MAIN = (INFRA / "main.tf").read_text(encoding="utf-8")
TEMPLATE = (INFRA / "cloud-init.yaml.tftpl").read_text(encoding="utf-8")
_spec = importlib.util.spec_from_file_location(
    "rehearse_host_for_hardening", REPO / "scripts/rehearse_host.py"
)
rehearse = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rehearse)

# Made-up limits, none of them a default, so a test cannot pass on a default by chance.
LIMITS = {
    "cairn": (777, 333),
    "caddy": (155, 111),
    "litestream": (166, 122),
}
# The smallest machine the machine_type variable takes: e2-small, 2 GiB.
SMALLEST_MACHINE_MB = 2048
# What the OS, Docker, the logging agent and the host's own short containers need beside them.
HOST_HEADROOM_MB = 512


def compose_services() -> dict[str, dict]:
    """cairn and caddy from compose.yaml, litestream from compose.backup.yaml."""
    plain = yaml.safe_load((REPO / "deploy" / "compose.yaml").read_text(encoding="utf-8"))
    backup = yaml.safe_load((REPO / "deploy" / "compose.backup.yaml").read_text(encoding="utf-8"))
    return {
        "cairn": plain["services"]["cairn"],
        "caddy": plain["services"]["caddy"],
        "litestream": backup["services"]["litestream"],
    }


def config_lines(limits: dict[str, tuple[int, int]]) -> str:
    return "".join(
        f'{name.upper()}_MEMORY_MB="{memory}"\n{name.upper()}_PIDS_LIMIT="{pids}"\n'
        for name, (memory, pids) in limits.items()
    )


@pytest.fixture
def host(tmp_path):
    report = Standins(tmp_path).host()
    config = report.root / "config.env"
    config.write_text(
        config.read_text(encoding="utf-8")
        + 'CADDY_IMAGE="caddy:2.11.9"\nCAIRN_DOMAIN="cairn.example.test"\n'
        + 'ACME_EMAIL="ops@example.test"\n'
        + config_lines(LIMITS),
        encoding="utf-8",
    )
    report.record("image", f"{IMAGE}@{NEW}")
    report.database(True)
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


def started(host, function: str) -> list[str]:
    """The arguments FUNCTION gave the one `docker run` it made."""
    if function == "cmd_run_cairn":
        # The stand-in's image is under a fake registry; run_cairn sets up what cairn needs.
        return host.run_cairn(None)
    before = len(host.docker_runs())
    done = run(host, function)
    assert done.returncode == 0, done.stdout + done.stderr
    runs = host.docker_runs()[before:]
    assert len(runs) == 1, runs
    return runs[0]


def option_values(argv: list[str], option: str) -> list[str]:
    """Every value given to OPTION before the image, written `--opt value` or `--opt=value`."""
    found = []
    for i, word in enumerate(argv):
        if word == option:
            found.append(argv[i + 1])
        elif word.startswith(option + "="):
            found.append(word.split("=", 1)[1])
    return found


RUNS = {"cairn": "cmd_run_cairn", "caddy": "cmd_run_caddy", "litestream": "cmd_run_litestream"}


@pytest.mark.parametrize("name", RUNS)
def test_each_container_drops_what_its_compose_service_drops(host, name):
    service = compose_services()[name]
    argv = started(host, RUNS[name])
    assert option_values(argv, "--cap-drop") == service["cap_drop"] == ["ALL"]
    assert option_values(argv, "--cap-add") == service.get("cap_add", [])
    assert option_values(argv, "--security-opt") == service["security_opt"]


def test_caddy_alone_gets_a_capability_back():
    services = compose_services()
    assert services["caddy"]["cap_add"] == ["NET_BIND_SERVICE"]
    assert [name for name, service in services.items() if service.get("cap_add")] == ["caddy"]


@pytest.mark.parametrize("name", RUNS)
def test_each_container_has_its_memory_and_process_limit_from_config_env(host, name):
    memory, pids = LIMITS[name]
    argv = started(host, RUNS[name])
    assert option_values(argv, "--memory") == [f"{memory}m"]
    # The same as the memory: no swap on top of it.
    assert option_values(argv, "--memory-swap") == [f"{memory}m"]
    assert option_values(argv, "--pids-limit") == [str(pids)]


@pytest.mark.parametrize("name", RUNS)
@pytest.mark.parametrize("setting", ["MEMORY_MB", "PIDS_LIMIT"])
@pytest.mark.parametrize("value", [None, "", "0", "12x", "-5"])
def test_without_a_whole_limit_the_container_does_not_start(host, name, setting, value):
    config = host.root / "config.env"
    key = f"{name.upper()}_{setting}"
    kept = [
        line
        for line in config.read_text(encoding="utf-8").splitlines(keepends=True)
        if not line.startswith(f"{key}=")
    ]
    if value is not None:
        kept.append(f'{key}="{value}"\n')
    config.write_text("".join(kept), encoding="utf-8")
    before = len(host.docker_runs())
    done = run(host, RUNS[name])
    assert done.returncode != 0
    assert host.docker_runs()[before:] == []
    assert f"ERROR: {key} is not a whole number above 0 in config.env" in done.stderr


def test_the_short_litestream_commands_drop_everything_too(host):
    """restore, the replica check's probe and the runbooks' `host.sh litestream` run the same
    image on the same network; they get the same flags, without the long-running limits."""
    done = run(host, "cmd_restore")
    assert done.returncode == 0, done.stdout + done.stderr
    [argv] = host.docker_runs()
    assert option_values(argv, "--cap-drop") == ["ALL"]
    assert option_values(argv, "--security-opt") == ["no-new-privileges:true"]
    assert option_values(argv, "--memory") == []
    host.bound(900)
    host.replica_check(1000, 1000, 1100)
    probe = host.docker_runs()[-1]
    assert "--entrypoint" in probe
    assert option_values(probe, "--cap-drop") == ["ALL"]
    assert option_values(probe, "--security-opt") == ["no-new-privileges:true"]


# --- the variables -----------------------------------------------------------------------


def limits_variable() -> str:
    start = VARIABLES.index('variable "container_limits"')
    return VARIABLES[start : VARIABLES.index("\n}\n", start) + 3]


def default_limits() -> dict[str, tuple[int, int]]:
    return rehearse.container_limit_defaults(VARIABLES)


def test_the_limits_are_one_variable_with_a_default_for_each_container():
    variable = limits_variable()
    assert re.search(r"^\s*type\s*=\s*object\(\{", variable, re.M)
    assert set(default_limits()) == set(RUNS)


def test_the_defaults_fit_the_smallest_machine_the_variables_allow_with_room_for_the_host():
    total = sum(memory for memory, _ in default_limits().values())
    assert total <= SMALLEST_MACHINE_MB - HOST_HEADROOM_MB, total
    assert default_limits() == {"cairn": (1024, 512), "caddy": (256, 256), "litestream": (256, 256)}


def machine_type_checks() -> list[str]:
    start = VARIABLES.index('variable "machine_type"')
    block = VARIABLES[start : VARIABLES.index("\n}\n", start)]
    return re.findall(r"condition\s*=\s*(.+)", block)


@pytest.mark.parametrize("small", ["e2-micro", "f1-micro", "g1-small"])
def test_machines_smaller_than_e2_small_are_refused(small):
    checks = machine_type_checks()
    assert any(f'"{small}"' in check for check in checks), checks


def test_each_limit_must_be_a_whole_number_above_a_floor():
    variable = limits_variable()
    assert "floor(" in variable and ">= 64" in variable and ">= 32" in variable


def test_main_tf_passes_each_limit_to_the_template_and_config_env_carries_it():
    rendering = re.search(r"templatefile\(.*?\n  \}\)", MAIN, re.S).group(0)
    for name in RUNS:
        for setting, field in (("memory_mb", "memory_mb"), ("pids_limit", "pids")):
            key = f"{name}_{setting}"
            assert re.search(
                rf"^\s*{key}\s*=\s*var\.container_limits\.{name}\.{field}$", rendering, re.M
            ), key
            assert f'{name.upper()}_{setting.upper()}="${{{key}}}"\n' in TEMPLATE, key


def test_the_rehearsal_renders_the_default_limits():
    values = rehearse.rehearsal_values(REPO)
    for name, (memory, pids) in default_limits().items():
        assert values[f"{name}_memory_mb"] == str(memory)
        assert values[f"{name}_pids_limit"] == str(pids)


# --- the rehearsal's own check of the running containers ---------------------------------


def host_config(**changes) -> dict:
    config = {
        "CapDrop": ["ALL"],
        "CapAdd": None,
        "SecurityOpt": ["no-new-privileges:true"],
        "Memory": 256 * 1024 * 1024,
        "MemorySwap": 256 * 1024 * 1024,
        "PidsLimit": 256,
    }
    config.update(changes)
    return config


def test_the_rehearsal_accepts_a_container_started_as_compose_does():
    assert rehearse.hardening_problems("litestream", host_config(), (256, 256)) == []
    caddy = host_config(CapAdd=["NET_BIND_SERVICE"])
    assert rehearse.hardening_problems("caddy", caddy, (256, 256)) == []


@pytest.mark.parametrize(
    ("name", "changes", "said"),
    [
        ("cairn", {"CapDrop": None}, "drops"),
        ("cairn", {"CapAdd": ["NET_BIND_SERVICE"]}, "adds"),
        ("caddy", {"CapAdd": None}, "adds"),
        ("caddy", {"CapAdd": ["NET_BIND_SERVICE", "CHOWN"]}, "adds"),
        ("litestream", {"SecurityOpt": None}, "security options"),
        ("litestream", {"Memory": 0}, "memory"),
        ("litestream", {"MemorySwap": -1}, "memory and swap"),
        ("litestream", {"PidsLimit": None}, "process limit"),
    ],
)
def test_the_rehearsal_names_each_flag_a_container_lacks(name, changes, said):
    problems = rehearse.hardening_problems(name, host_config(**changes), (256, 256))
    assert len(problems) == 1 and said in problems[0], problems


def test_the_rehearsal_checks_the_three_containers_where_it_sees_them_serve():
    source = (REPO / "scripts" / "rehearse_host.py").read_text(encoding="utf-8")
    body = source[source.index("    def serves_cairn_from_the_real_units") :]
    body = body[: body.index("\n    def ", 10)]
    assert "hardening_problems(" in body and "{{json .HostConfig}}" in body


# --- host.sh travels compressed in the user-data ------------------------------------------
#
# The VM's whole user-data must stay under Compute Engine's 256 KB for one metadata value,
# beside the largest plans file and logo the variables take (test_infra_tf_unit). host.sh
# grew with these flags and the checks below; it travels gzip-compressed and base64-encoded,
# which cloud-init's write_files decodes (encoding gz+b64), and is written byte for byte.


def test_main_tf_gives_the_template_host_sh_compressed():
    rendering = re.search(r"templatefile\(.*?\n  \}\)", MAIN, re.S).group(0)
    assert re.search(
        r'^\s*host_sh\s*=\s*base64gzip\(file\("\$\{path\.module\}/files/host\.sh"\)\)$',
        rendering,
        re.M,
    )


def test_the_template_writes_host_sh_from_its_compressed_form():
    entry = TEMPLATE[TEMPLATE.index("  - path: /etc/cairn/host.sh\n") :]
    entry = entry[: entry.index("\n\n")]
    assert "    encoding: gz+b64\n" in entry
    assert '    content: "${host_sh}"' in entry


def test_a_gzip_base64_entry_is_its_decompressed_bytes(tmp_path):
    import base64
    import gzip

    data = b"#!/bin/bash\necho caf\xc3\xa9\n"
    document = (
        "#cloud-config\nwrite_files:\n  - path: /etc/x\n    encoding: gz+b64\n"
        f'    content: "{base64.b64encode(gzip.compress(data)).decode()}"\n'
    )
    [entry] = rehearse.write_files(document)
    assert entry.content == data
    broken = (
        '#cloud-config\nwrite_files:\n  - path: /etc/x\n    encoding: gz+b64\n    content: "eA=="\n'
    )
    with pytest.raises(rehearse.Failed, match="gzip"):
        rehearse.write_files(broken)
