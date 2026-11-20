"""Run the VM's bootstrap with stand-ins. The tests never run a daemon or contact a cloud."""

import asyncio
import base64
import json
import os
import random
import shlex
import shutil
import stat
import subprocess
import sys

import pytest
from test_quick_aws_round4_unit import section
from test_quick_infra_unit import QUICK, render_cloud_init

from cairn.web.cli import _parser, _sign_in

BOOTSTRAP = QUICK / "bootstrap.sh"
SETTINGS = b"CAIRN_TEST_SETTING='synthetic $value with spaces'\nOTHER=line\n\n"
TOKEN = "synthetic-metadata-token"
COMPOSE_UP = ["docker", "compose", "up", "-d", "--pull", "missing"]
RULE = ["!", "-i", "br-cairn-backup", "-d", "169.254.169.254", "-p"]
STANDIN = r"""
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

cmd = Path(sys.argv[0]).name
args = sys.argv[1:]
state = Path(os.environ["STANDIN_STATE"])
mode = os.environ["STANDIN_MODE"]
with (state / "calls").open("a") as log:
    log.write(json.dumps([cmd, *args]) + "\n")


def pull_images():
    if (state / "registry-down").exists():
        sys.exit(1)
    (state / "images-cached").touch()


if cmd == "docker":
    if mode == "fail-first-index":
        assert (state / "docker-installed").is_file()
    if args == ["version", "--format", "{{.Server.Version}}"]:
        print("28.3.2" if mode == "old-engine" else "28.3.3")
    elif args == ["compose", "exec", "-T", "cairn", "sh", "-eu"]:
        assert "key-create" in sys.stdin.read()
        print("CAIRN_API_KEY=synthetic-first-key")
    elif args == ["compose", "pull"]:
        pull_images()
    elif args[:3] == ["compose", "up", "-d"]:
        if args[3:] == ["--pull", "missing"] and not (state / "images-cached").exists():
            pull_images()
        elif args[3:] not in ([], ["--pull", "missing"]):
            sys.exit(2)
        if mode.startswith("compose-failure") or not (state / "images-cached").exists():
            print(os.environ["STANDIN_SECRET"], file=sys.stderr)
            sys.exit(1)
        (state / "stack-started").touch()
    elif args[:3] == ["compose", "ps", "--all"]:
        print(os.environ["STANDIN_SECRET"], file=sys.stderr)
        if mode == "compose-failure-no-status":
            print(os.environ["STANDIN_SECRET"])
            sys.exit(1)
        if args[3:] != ["--format", "{{.Service}} {{.State}} (exit={{.ExitCode}})"]:
            print(os.environ["STANDIN_SECRET"])
            sys.exit()
        print("litestream-restore exited (exit=1)")
    else:
        sys.exit(2)
elif cmd == "dpkg":
    if args == ["--print-architecture"]:
        print("amd64")
        sys.exit()
    assert args[0] == "--compare-versions" and args[2:] == ["ge", "28.3.3"]
    sys.exit(args[1] == "28.3.2")
elif cmd == "apt-get":
    if mode == "apt-lock" and "install" in args:
        if "DPkg::Lock::Timeout=120" not in args:
            print("Could not get dpkg lock", file=sys.stderr)
            sys.exit(100)
        with (state / "dpkg-waits").open("a") as log:
            log.write("install\n")
    failed = state / "index-download-failed"
    if (args[-1] == "update" and mode == "fail-first-index" and not failed.exists()
            and (state / "etc/apt/sources.list.d/docker.sources").is_file()):
        failed.touch()
        print("Docker package index download failed", file=sys.stderr)
        sys.exit(100)
    if "docker-ce" in args:
        (state / "docker-installed").touch()
elif cmd == "snap":
    installed = state / args[1]
    if args[0] == "list":
        sys.exit(not installed.exists())
    if args[0] == "install":
        installed.touch(exist_ok=False)
    else:
        assert args[0] == "start" and installed.exists()
elif cmd == "systemctl":
    assert args == ["daemon-reload"] or (args[0] == "enable" and "--now" in args)
elif cmd == "iptables":
    assert args[0] == "-w" and args[2] == "DOCKER-USER"
    chain = state / "chain"
    op = args[1]
    rules = chain.read_text().splitlines() if chain.exists() else []
    if op == "-S":
        sys.exit(not chain.exists())
    if op == "-N":
        chain.touch()
    if op == "-C":
        sys.exit(json.dumps(args[3:]) not in rules)
    if op == "-I":
        assert args[3] == "1" and chain.exists()
        chain.write_text(json.dumps(args[4:]) + "\n" + "\n".join(rules) + "\n")
elif cmd in ("aws", "curl"):
    if cmd == "aws":
        pages = json.loads(os.environ.get("STANDIN_AWS_PAGES", '[[["AWSCURRENT"]]]'))
        arn = os.environ["CAIRN_SETTINGS_SECRET"]
        if "list-secret-version-ids" in args:
            kind = "list"
        elif "get-secret-value" in args:
            kind = "read"
        else:
            sys.exit(2)
    else:
        if "https://download.docker.com/linux/ubuntu/gpg" in args:
            Path(args[args.index("-o") + 1]).write_text("synthetic package signing data\n")
            sys.exit()
        url = urlparse(args[-1])
        if url.path.endswith("/service-accounts/default/token"):
            assert "Metadata-Flavor: Google" in args
            print(json.dumps({"access_token": os.environ["STANDIN_TOKEN"]}))
            sys.exit()
        assert sys.stdin.read() == "Authorization: Bearer " + os.environ["STANDIN_TOKEN"] + "\n"
        prefix = os.environ["CAIRN_SETTINGS_SECRET"] + "/versions/"
        versions = {
            f"{prefix}{index}": state
            for index, state in enumerate(json.loads(os.environ.get(
                "STANDIN_GCP_STATES", '["ENABLED"]')), start=1)
        }
        if url.path.endswith("/versions"):
            kind = "list"
        elif url.path.endswith(":access"):
            kind = "read"
        else:
            sys.exit(2)
    if mode == "fail-" + kind:
        print(os.environ["STANDIN_SECRET"], file=sys.stderr)
        sys.exit(1)
    if kind == "list" and mode == "empty":
        print("{}")
    elif cmd == "aws" and kind == "list":
        index = int(args[args.index("--next-token") + 1]) if "--next-token" in args else 0
        entries = [
            {"VersionStages": stages, "VersionId": f"{index:016d}{offset:016d}"}
            for offset, stages in enumerate(pages[index])
            if stages or "--include-deprecated" in args
        ]
        response = {"ARN": arn, "Name": "example", "Versions": entries}
        if index + 1 < len(pages):
            response["NextToken"] = str(index + 1)
        print(json.dumps(response))
    elif cmd == "aws":
        stage = "AWSCURRENT"
        if "--version-stage" in args:
            stage = args[args.index("--version-stage") + 1]
        if not any(stage in stages for page in pages for stages in page):
            sys.exit(1)
        print(json.dumps({"ARN": arn, "Name": "example", "VersionStages": [stage],
                          "SecretString": os.environ["STANDIN_SECRET"]}))
    elif kind == "list":
        enabled_only = parse_qs(url.query).get("filter") == ["state:ENABLED"]
        candidates = [{"name": name, "state": status}
                      for name, status in reversed(versions.items())
                      if not enabled_only or status == "ENABLED"]
        print(json.dumps({"versions": candidates[:1]} if candidates else {}))
    else:
        name = url.path.removeprefix("/v1/").removesuffix(":access")
        if name == prefix + "latest":
            name = next(reversed(versions))
        if versions[name] != "ENABLED":
            sys.exit(22)
        data = os.environ["STANDIN_PAYLOAD"]
        payload = {"dataCrc32c": os.environ["STANDIN_CRC32C"]}
        if data:
            payload["data"] = data
        print(json.dumps({"name": name, "payload": payload}))
else:
    sys.exit(2)
"""


def crc32c(data):
    remainder = 0xFFFFFFFF
    for byte in data:
        remainder ^= byte
        for _ in range(8):
            remainder = (remainder >> 1) ^ (0x82F63B78 if remainder & 1 else 0)
    return str(remainder ^ 0xFFFFFFFF)


@pytest.fixture
def vm(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("apt-get", "aws", "curl", "docker", "dpkg", "iptables", "snap", "systemctl"):
        stub = bin_dir / name
        stub.write_text(f"#!{sys.executable}\n{STANDIN}", encoding="utf-8")
        stub.chmod(0o755)
    (tmp_path / "deploy").mkdir()
    return tmp_path


def vm_environment(vm, cloud, mode, settings=SETTINGS, config=None):
    replica = "s3://example-replica/cairn" if cloud == "aws" else "gs://example-replica/cairn"
    secret = {
        "aws": "arn:aws:secretsmanager:us-east-1:000000000000:secret:cairn-quick-example",
        "gcp": "projects/example-project/secrets/cairn-quick-env",
    }[cloud]
    return {
        "PATH": f"{vm / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(vm),
        "STANDIN_STATE": str(vm),
        "STANDIN_MODE": mode,
        "STANDIN_SECRET": settings.decode(),
        "STANDIN_PAYLOAD": base64.b64encode(settings).decode(),
        "STANDIN_CRC32C": crc32c(settings),
        "STANDIN_TOKEN": TOKEN,
        "CAIRN_CLOUD": cloud,
        "CAIRN_DOMAIN": "cairn.example.net",
        "ACME_EMAIL": "ops@example.net",
        "CAIRN_IMAGE": "ghcr.io/macayaven/cairn:0.1.0",
        "CAIRN_SETTINGS_SECRET": secret,
        "LITESTREAM_REPLICA_URL": replica,
        **({"AWS_REGION": "us-east-1"} if cloud == "aws" else {}),
        **(config or {}),
    }


def vm_command(vm, args, env):
    return subprocess.run(
        [arg.replace("/opt/cairn", str(vm)) for arg in args],
        cwd=vm,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def run(vm, cloud="aws", mode="with-version", action="start", *, settings=SETTINGS, config=None):
    assert BOOTSTRAP.is_file(), "the shared quick bootstrap has not been written"
    script = vm / "bootstrap.sh"
    script.write_bytes(BOOTSTRAP.read_bytes())
    return vm_command(
        vm, ["/bin/bash", str(script), action], vm_environment(vm, cloud, mode, settings, config)
    )


def calls(vm):
    return [json.loads(line) for line in (vm / "calls").read_text().splitlines()]


def boot_environment(files):
    return dict(
        item.split("=", 1) for item in shlex.split(files["/opt/cairn/quick.env"]["content"])
    )


def run_queued_units(vm, env, files):
    """Run the requested oneshots, advancing one on-failure retry without waiting."""
    seen = set()
    while pending := [
        call[-1]
        for call in calls(vm)
        if call[:2] == ["systemctl", "enable"] and call[-1] not in seen
    ]:
        for name in pending:
            seen.add(name)
            lines = files[f"/etc/systemd/system/{name}"]["content"].splitlines()
            commands = [
                shlex.split(line.split("=", 1)[1])
                for line in lines
                if line.startswith(("ExecStart=", "ExecStartPost="))
            ]
            commands = [
                [env[arg[1:]] if arg.startswith("$") else arg for arg in argv] for argv in commands
            ]
            for _ in range(2 if "Restart=on-failure" in lines else 1):
                if all(vm_command(vm, argv, env).returncode == 0 for argv in commands):
                    break


@pytest.mark.parametrize("cloud", ["aws", "gcp"])
def test_first_boot_recovers_after_a_docker_package_index_download_fails(vm, cloud):
    cloud_init = render_cloud_init(vm, cloud)
    files = {file["path"]: file for file in cloud_init["write_files"]}
    script = base64.b64decode(files["/opt/cairn/bootstrap.sh"]["content"]).decode()
    (vm / "bootstrap.sh").write_text(script.replace("/etc/", "etc/"))
    (vm / "bootstrap.sh").chmod(0o755)
    (vm / "etc/apt/sources.list.d").mkdir(parents=True)
    (vm / "etc/os-release").write_text("VERSION_CODENAME=noble\n")
    env = vm_environment(vm, cloud, "fail-first-index", config=boot_environment(files))

    for argv in cloud_init["runcmd"]:
        vm_command(vm, argv, env)
    run_queued_units(vm, env, files)

    assert (vm / "index-download-failed").is_file()
    assert calls(vm).count(COMPOSE_UP) == 1
    assert (vm / ".host-provisioned").is_file()


def compose_service(vm):
    # A project render reads the .env and env_file itself and needs no Docker daemon.
    standalone = shutil.which("docker-compose")
    docker = shutil.which("docker")
    if standalone is None and docker is None:
        pytest.skip("Docker Compose is needed to render the service command")
    compose = [standalone] if standalone else [docker, "compose"]
    env = {"HOME": str(vm), "PATH": os.environ["PATH"], "DOCKER_HOST": f"unix://{vm}/no-daemon"}
    version = subprocess.run([*compose, "version"], env=env, capture_output=True, timeout=20)
    if version.returncode != 0:
        pytest.skip("Docker Compose is needed to render the service command")
    done = subprocess.run(
        [*compose, "config", "--format", "json"],
        cwd=vm / "deploy",
        env=env,
        capture_output=True,
        timeout=20,
    )
    assert done.returncode == 0, "Compose could not render the quick project"
    return json.loads(done.stdout)["services"]["cairn"]


@pytest.mark.parametrize("cloud", ["aws", "gcp"])
def test_google_settings_enable_provider_sign_in_in_the_compose_command(vm, cloud, monkeypatch):
    extra_args = [
        "--sign-in",
        "google",
        "--allowed-email-domain",
        "example.net",
        "--brand-name",
        "Fable Works",
    ]
    cloud_init = render_cloud_init(vm, cloud, serve_extra_args=extra_args)
    files = {file["path"]: file for file in cloud_init["write_files"]}
    config = boot_environment(files)
    done = run(
        vm,
        cloud,
        config=config,
        settings=(
            b"CAIRN_GOOGLE_CLIENT_ID=synthetic-client\n"
            b"CAIRN_GOOGLE_CLIENT_SECRET=synthetic-value\n"
            b"CAIRN_ALLOWED_EMAILS=ada@example.net\n"
        ),
    )
    assert done.returncode == 0, done.stderr
    for path, file in files.items():
        if path.startswith("/opt/cairn/deploy/"):
            (vm / path.removeprefix("/opt/cairn/")).write_bytes(base64.b64decode(file["content"]))
    service = compose_service(vm)
    args = _parser().parse_args(service["command"])
    monkeypatch.setenv("no_proxy", "*")
    sign_in = _sign_in(args, service["environment"])
    assert sign_in is not None
    try:
        assert service["command"] == [
            "serve",
            "--db",
            "/data/cairn.db",
            "--host",
            "0.0.0.0",
            "--public-url",
            "https://cairn.example.net",
            "--trusted-proxy",
            "10.213.47.0/24",
            *extra_args,
        ]
        assert tuple(sign_in.allowed_email_domains) == ("example.net",)
        assert tuple(provider.name for provider in sign_in.providers) == ("google",)
    finally:
        asyncio.run(sign_in.http.aclose())


@pytest.mark.parametrize("cloud", ["aws", "gcp"])
def test_start_writes_only_the_expected_env_files_and_starts_compose(vm, cloud):
    done = run(vm, cloud)
    assert done.returncode == 0, done.stderr
    deploy = vm / "deploy"
    assert (deploy / ".env").read_text() == (
        "CAIRN_DOMAIN=cairn.example.net\n"
        "ACME_EMAIL=ops@example.net\n"
        "CAIRN_IMAGE=ghcr.io/macayaven/cairn:0.1.0\n"
        "COMPOSE_FILE=compose.yaml:compose.backup.yaml:compose.quick.yaml\n"
    )
    assert (deploy / "litestream.env").read_text() == {
        "aws": "LITESTREAM_REPLICA_URL=s3://example-replica/cairn\nAWS_REGION=us-east-1\n",
        "gcp": "LITESTREAM_REPLICA_URL=gs://example-replica/cairn\n",
    }[cloud]
    assert (deploy / "cairn.env").read_bytes() == SETTINGS
    assert {p.name: stat.S_IMODE(p.stat().st_mode) for p in deploy.iterdir()} == {
        ".env": 0o600,
        "litestream.env": 0o600,
        "cairn.env": 0o600,
    }
    assert calls(vm)[-1] == COMPOSE_UP
    assert ["docker", "compose", "pull"] not in calls(vm)
    assert "$value" not in done.stdout + done.stderr
    assert TOKEN not in done.stdout + done.stderr
    assert all(TOKEN not in call for call in calls(vm))


@pytest.mark.parametrize("cloud", ["aws", "gcp"])
def test_a_secret_with_no_versions_is_an_empty_env_file(vm, cloud):
    done = run(vm, cloud, "empty")
    assert done.returncode == 0, done.stderr
    assert (vm / "deploy" / "cairn.env").read_bytes() == b""
    assert calls(vm)[-1] == COMPOSE_UP


@pytest.mark.parametrize("newer", ["DISABLED", "DESTROYED"])
@pytest.mark.parametrize("older", ["ENABLED", "DISABLED", "DESTROYED"])
def test_gcp_reads_the_newest_enabled_version(vm, older, newer):
    done = run(vm, "gcp", config={"STANDIN_GCP_STATES": json.dumps([older, newer])})
    assert done.returncode == 0, done.stderr
    assert (vm / "deploy" / "cairn.env").read_bytes() == (SETTINGS if older == "ENABLED" else b"")
    accesses = [call[-1] for call in calls(vm) if call[-1].endswith(":access")]
    if older == "ENABLED":
        assert len(accesses) == 1 and accesses[0].endswith("/versions/1:access")
    else:
        assert accesses == []


def test_gcp_accepts_an_empty_payload_with_a_zero_checksum(vm):
    done = run(vm, "gcp", settings=b"", config={"STANDIN_CRC32C": "0"})
    assert done.returncode == 0, done.stderr
    assert (vm / "deploy" / "cairn.env").read_bytes() == b""


@pytest.mark.parametrize(
    "data", [b"123456789", random.Random(20261120).randbytes(31).hex().encode()]
)
def test_gcp_rejects_a_wrong_checksum_before_publishing(vm, data):
    existing = vm / "deploy" / "cairn.env"
    existing.write_text("EXISTING=synthetic\n")
    done = run(vm, "gcp", settings=data, config={"STANDIN_CRC32C": "1"})
    assert done.returncode != 0
    assert "could not read the cairn settings secret" in done.stderr
    assert data.decode() not in done.stdout + done.stderr
    assert existing.read_text() == "EXISTING=synthetic\n"
    assert not any(call[:2] == ["docker", "compose"] for call in calls(vm))


@pytest.mark.parametrize("stages", [[], ["AWSPREVIOUS"], ["AWSPENDING"]])
def test_aws_without_awscurrent_starts_with_empty_settings(vm, stages):
    done = run(vm, config={"STANDIN_AWS_PAGES": json.dumps([[stages]])})
    assert done.returncode == 0, done.stderr
    assert (vm / "deploy" / "cairn.env").read_bytes() == b""
    assert not any("get-secret-value" in call for call in calls(vm))


def test_aws_explicitly_reads_awscurrent_on_a_later_page(vm):
    pages = [[["AWSPREVIOUS"]], [["AWSPENDING"], ["AWSCURRENT"]]]
    done = run(vm, config={"STANDIN_AWS_PAGES": json.dumps(pages)})
    assert done.returncode == 0, done.stderr
    assert (vm / "deploy" / "cairn.env").read_bytes() == SETTINGS
    requests = [call for call in calls(vm) if call[0] == "aws"]
    assert len(requests) == 3
    assert requests[1][-2:] == ["--next-token", "1"]
    assert requests[2][-2:] == ["--version-stage", "AWSCURRENT"]
    assert all(
        "--include-deprecated" not in call and "--max-items" not in call for call in requests
    )


def test_first_boot_pulls_missing_images(vm):
    assert not (vm / "images-cached").exists()
    done = run(vm)
    assert done.returncode == 0, done.stderr
    assert (vm / "images-cached").exists()
    assert (vm / "stack-started").exists()
    assert calls(vm)[-1] == COMPOSE_UP


def test_cached_restart_does_not_contact_the_registry(vm):
    assert run(vm).returncode == 0
    (vm / "registry-down").touch()
    (vm / "stack-started").unlink()
    done = run(vm)
    assert done.returncode == 0, done.stderr
    assert (vm / "stack-started").exists()


@pytest.mark.parametrize("status_available", [True, False])
def test_compose_failure_reports_only_safe_status(vm, status_available):
    mode = "compose-failure" if status_available else "compose-failure-no-status"
    done = run(vm, mode=mode)
    assert done.returncode != 0
    assert "Docker Compose up failed" in done.stderr
    assert "$value" not in done.stdout + done.stderr
    assert ("litestream-restore exited (exit=1)" in done.stderr) is status_available
    assert calls(vm)[-1][:4] == ["docker", "compose", "ps", "--all"]


def test_both_package_installs_wait_for_the_dpkg_lock(vm):
    script = vm / "bootstrap.sh"
    script.write_text(BOOTSTRAP.read_text().replace("/etc/", "etc/"))
    (vm / "etc/apt/sources.list.d").mkdir(parents=True)
    (vm / "etc/os-release").write_text("VERSION_CODENAME=noble\n")
    done = vm_command(
        vm, ["/bin/bash", str(script), "install", "aws"], vm_environment(vm, "aws", "apt-lock")
    )
    assert done.returncode == 0, done.stderr
    assert (vm / "dpkg-waits").read_text().splitlines() == ["install", "install"]


@pytest.mark.parametrize("cloud", ["aws", "gcp"])
@pytest.mark.parametrize("mode", ["fail-list", "fail-read"])
def test_a_failed_secret_read_leaves_existing_settings_and_never_calls_compose(vm, cloud, mode):
    existing = vm / "deploy" / "cairn.env"
    existing.write_text("EXISTING=synthetic\n")
    done = run(vm, cloud, mode)
    assert done.returncode != 0
    assert "could not read the cairn settings secret" in done.stderr
    assert "$value" not in done.stdout + done.stderr
    assert TOKEN not in done.stdout + done.stderr
    assert list((vm / "deploy").iterdir()) == [existing]
    assert existing.read_text() == "EXISTING=synthetic\n"
    assert not any(call[:2] == ["docker", "compose"] for call in calls(vm))


def test_a_second_boot_keeps_file_inodes_and_does_not_add_firewall_rules(vm):
    assert run(vm).returncode == 0
    deploy = vm / "deploy"
    before = {
        p.name: (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes()) for p in deploy.iterdir()
    }
    assert run(vm).returncode == 0
    after = {
        p.name: (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes()) for p in deploy.iterdir()
    }
    assert before == after
    inserts = [call for call in calls(vm) if call[:3] == ["iptables", "-w", "-I"]]
    assert inserts == [
        [
            "iptables",
            "-w",
            "-I",
            "DOCKER-USER",
            "1",
            *RULE,
            proto,
            "!",
            "--dport",
            "53",
            "-j",
            "DROP",
        ]
        for proto in ("tcp", "udp")
    ]


def test_the_firewall_can_run_before_docker_exists(vm):
    done = run(vm, action="firewall")
    assert done.returncode == 0, done.stderr
    assert all(call[0] == "iptables" for call in calls(vm))
    assert (vm / "chain").exists()


def test_an_older_engine_cannot_start_the_stack(vm):
    done = run(vm, mode="old-engine")
    assert done.returncode != 0
    assert "28.3.3" in done.stderr
    assert not any(call[:2] == ["docker", "compose"] for call in calls(vm))


def test_first_key_is_copied_into_a_private_file_without_printing_it(vm):
    done = run(vm, action="first-key")
    assert done.returncode == 0, done.stderr
    [path] = vm.glob("cairn-first-key.*")
    assert path.read_text() == "CAIRN_API_KEY=synthetic-first-key\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert str(path) in done.stdout
    assert "synthetic-first-key" not in done.stdout + done.stderr


def test_documented_key_cleanup_removes_only_the_chosen_file(vm):
    assert run(vm, action="first-key").returncode == 0
    [key] = vm.glob("cairn-first-key.*")
    assert run(vm, action="first-key").returncode == 0
    commands = [
        shlex.split(token.content)
        for token in section("First key cleanup")
        if token.type == "code_inline" and token.content.startswith("sudo rm ")
    ]
    assert commands == [["sudo", "rm", "--", "/opt/cairn/cairn-first-key.abc123"]]
    command = [*commands[0][1:-1], str(key)]
    assert vm_command(vm, command, vm_environment(vm, "aws", "with-version")).returncode == 0
    assert not key.exists()
    assert len(list(vm.glob("cairn-first-key.*"))) == 1
