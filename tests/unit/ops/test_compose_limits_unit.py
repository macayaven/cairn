"""Compose and the hosted startup script apply the same container hardening."""

import re
import shlex

import pytest
import yaml

from support.guide_source import guide_source
from support.paths import REPO_ROOT

ROOT = REPO_ROOT
HOST = (ROOT / "infra/gcp/files/host.sh").read_text(encoding="utf-8")
VARIABLES = (ROOT / "infra/gcp/variables.tf").read_text(encoding="utf-8")
DEFAULTS = {
    name: (int(memory), int(pids))
    for name, memory, pids in re.findall(
        r"^\s*(\w+)\s*=\s*\{ memory_mb = (\d+), pids = (\d+) }$", VARIABLES, re.M
    )
}
CONFIGS = {
    name: yaml.safe_load((ROOT / "deploy" / name).read_text(encoding="utf-8"))["services"]
    for name in ("compose.yaml", "compose.local.yaml", "compose.backup.yaml")
}
SERVICES = {
    ("compose.yaml", "cairn"): "CAIRN",
    ("compose.yaml", "caddy"): "CADDY",
    ("compose.local.yaml", "cairn"): "CAIRN",
    ("compose.backup.yaml", "data-init"): "CAIRN_DATA_INIT",
    ("compose.backup.yaml", "litestream-restore"): "LITESTREAM_RESTORE",
    ("compose.backup.yaml", "litestream"): "LITESTREAM",
}


def host_words(name):
    match = re.search(rf'^{name}="([^"\n]+)"$', HOST, re.M)
    assert match, name
    return shlex.split(match[1])


def host_function(name):
    match = re.search(rf"^{name}\(\) \{{\n(.*?)^}}$", HOST, re.M | re.S)
    assert match, name
    return match[1]


def test_every_compose_service_is_covered_including_the_overlay():
    assert {(file, service) for file, services in CONFIGS.items() for service in services} == {
        *SERVICES,
        ("compose.backup.yaml", "cairn"),
    }
    # The overlay's cairn entry inherits all its hardening from compose.yaml.
    assert set(CONFIGS["compose.backup.yaml"]["cairn"]) == {"depends_on"}


@pytest.mark.parametrize(("file", "service"), SERVICES)
def test_compose_and_host_have_equivalent_container_hardening(file, service):
    spec = CONFIGS[file][service]
    flags = host_words("HARDENING")
    hardening = dict(zip(flags[::2], flags[1::2], strict=True))
    caps = host_words("CADDY_CAPS")
    assert spec["cap_drop"] == [hardening["--cap-drop"]]
    assert spec["security_opt"] == [hardening["--security-opt"]]
    assert caps[0] == "--cap-add"
    assert spec.get("cap_add", []) == (caps[1:] if service == "caddy" else [])
    assert not spec.get("privileged", False)
    log = host_words("LOG_OPTS")
    assert log[::2] == ["--log-driver", "--log-opt", "--log-opt"]
    assert spec["logging"]["driver"] == log[1]
    assert spec["logging"]["options"] == dict(item.split("=", 1) for item in log[3::2])

    prefix = SERVICES[file, service]
    if service == "data-init":
        # Only Compose needs a container to seed ownership of its named volume.
        bounds = (64, 32)
    elif service == "litestream-restore":
        # The hosted restore retries with systemd and has no memory or PID cap.
        # Compose sets an independently adjustable cap from the replica's defaults.
        assert "litestream command cairn-restore" in host_function("cmd_restore")
        assert "limits" not in host_function("cmd_restore")
        bounds = DEFAULTS["litestream"]
    else:
        bounds = DEFAULTS[service]
        limits = host_function("limits")
        assert re.search(
            rf"{service}\) prefix={prefix} memory=\$\{{{prefix}_MEMORY_MB:-}}"
            rf" pids=\$\{{{prefix}_PIDS_LIMIT:-}}",
            limits,
        )
        assert "--memory %sm --memory-swap %sm --pids-limit %s" in limits
        launch = host_function("cmd_run_" + service)
        assert f"$(limits {service})" in launch
        if service == "litestream":
            assert "litestream exec litestream replicate" in launch
            launch = host_function("litestream")
        assert "$HARDENING" in launch and "$LOG_OPTS" in launch

    memory, pids = bounds
    assert spec["mem_limit"] == f"${{{prefix}_MEMORY_MB:-{memory}}}m"
    assert spec["memswap_limit"] == spec["mem_limit"]
    assert spec["pids_limit"] == f"${{{prefix}_PIDS_LIMIT:-{pids}}}"
    if service.startswith("litestream"):
        uid = re.search(r"^CAIRN_UID=(\d+)$", HOST, re.M)[1]
        assert '--user "$CAIRN_UID:$CAIRN_UID"' in host_function("litestream")
        assert spec["user"] == f"{uid}:{uid}"
    # Hosted restart behavior belongs to systemd. Compose retries its long-running services
    # and leaves a failed initializer or restore failed so cairn's dependency cannot pass.
    assert spec["restart"] == (
        "no" if service in ("data-init", "litestream-restore") else "unless-stopped"
    )


LIMIT_ROWS = (
    "| cairn | 1024 MiB | 512 | `CAIRN_MEMORY_MB` | `CAIRN_PIDS_LIMIT` |",
    "| Caddy | 256 MiB | 256 | `CADDY_MEMORY_MB` | `CADDY_PIDS_LIMIT` |",
    "| Litestream | 256 MiB | 256 | `LITESTREAM_MEMORY_MB` | `LITESTREAM_PIDS_LIMIT` |",
    "| Litestream restore | 256 MiB | 256 | `LITESTREAM_RESTORE_MEMORY_MB` |"
    " `LITESTREAM_RESTORE_PIDS_LIMIT` |",
    "| Data initializer | 64 MiB | 32 | `CAIRN_DATA_INIT_MEMORY_MB` |"
    " `CAIRN_DATA_INIT_PIDS_LIMIT` |",
)
LIMIT_SENTENCES = (
    "Set overrides in `deploy/.env` as whole numbers. Memory limits are in MiB and include swap.",
    "A compaction or a large import can raise cairn's memory use.",
    "If the kernel kills a process at the memory limit, Docker applies the container's restart"
    " policy.",
    "A failed initializer or restore keeps cairn from starting.",
    "At the process limit, the kernel refuses new processes or threads.",
)


@pytest.mark.parametrize("document", ["run", "deploy"])
def test_compose_guides_name_the_limits_and_what_happens_when_they_are_reached(document):
    if document == "run":
        text = guide_source("run").split("### Compose limits", 1)[1].split("\n### ", 1)[0]
    else:
        text = (ROOT / "deploy/README.md").read_text(encoding="utf-8")
        text = text.split("### Compose limits", 1)[1].split("\n### ", 1)[0]
    text = " ".join(text.split())
    for fact in (*LIMIT_ROWS, *LIMIT_SENTENCES):
        assert text.count(fact) == 1, fact
