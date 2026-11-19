"""Run the VM's bootstrap with stand-ins. The tests never run a daemon or contact a cloud."""

import asyncio
import base64
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys

import pytest
from test_quick_infra_unit import QUICK, render_cloud_init

from cairn.web.cli import _parser, _sign_in

BOOTSTRAP = QUICK / "bootstrap.sh"
SETTINGS = b"CAIRN_TEST_SETTING='synthetic $value with spaces'\nOTHER=line\n\n"
TOKEN = "synthetic-metadata-token"
RULE = ["!", "-i", "br-cairn-backup", "-d", "169.254.169.254", "-p"]
STANDIN = r"""
import json
import os
import sys
from pathlib import Path

cmd = Path(sys.argv[0]).name
args = sys.argv[1:]
state = Path(os.environ["STANDIN_STATE"])
mode = os.environ["STANDIN_MODE"]
with (state / "calls").open("a") as log:
    log.write(json.dumps([cmd, *args]) + "\n")
if cmd == "docker":
    if mode == "fail-first-index":
        assert (state / "docker-installed").is_file()
    if args == ["version", "--format", "{{.Server.Version}}"]:
        print("28.3.2" if mode == "old-engine" else "28.3.3")
    elif args == ["compose", "exec", "-T", "cairn", "sh", "-eu"]:
        assert "key-create" in sys.stdin.read()
        print("CAIRN_API_KEY=synthetic-first-key")
    elif args not in (["compose", "pull"], ["compose", "up", "-d"]):
        sys.exit(2)
elif cmd == "dpkg":
    if args == ["--print-architecture"]:
        print("amd64")
        sys.exit()
    assert args[0] == "--compare-versions" and args[2:] == ["ge", "28.3.3"]
    sys.exit(args[1] == "28.3.2")
elif cmd == "apt-get":
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
        url = args[-1]
        if url.endswith("/service-accounts/default/token"):
            assert "Metadata-Flavor: Google" in args
            print(json.dumps({"access_token": os.environ["STANDIN_TOKEN"]}))
            sys.exit()
        assert sys.stdin.read() == "Authorization: Bearer " + os.environ["STANDIN_TOKEN"] + "\n"
        if url.endswith("/versions?pageSize=1"):
            kind = "list"
        elif url.endswith("/versions/latest:access"):
            kind = "read"
        else:
            sys.exit(2)
    if mode == "fail-" + kind:
        print(os.environ["STANDIN_SECRET"], file=sys.stderr)
        sys.exit(1)
    if kind == "list":
        field = "Versions" if cmd == "aws" else "versions"
        print(json.dumps({field: [{"id": "example"}]} if mode != "empty" else {}))
    elif cmd == "aws":
        print(json.dumps({"SecretString": os.environ["STANDIN_SECRET"]}))
    else:
        print(json.dumps({"payload": {"data": os.environ["STANDIN_PAYLOAD"]}}))
else:
    sys.exit(2)
"""


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
    assert calls(vm).count(["docker", "compose", "up", "-d"]) == 1
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
    assert calls(vm)[-2:] == [["docker", "compose", "pull"], ["docker", "compose", "up", "-d"]]
    assert "$value" not in done.stdout + done.stderr
    assert TOKEN not in done.stdout + done.stderr
    assert all(TOKEN not in call for call in calls(vm))


@pytest.mark.parametrize("cloud", ["aws", "gcp"])
def test_a_secret_with_no_versions_is_an_empty_env_file(vm, cloud):
    done = run(vm, cloud, "empty")
    assert done.returncode == 0, done.stderr
    assert (vm / "deploy" / "cairn.env").read_bytes() == b""
    assert calls(vm)[-1] == ["docker", "compose", "up", "-d"]


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
