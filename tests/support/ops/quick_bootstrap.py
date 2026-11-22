"""Shared test helpers extracted from test_quick_bootstrap_unit."""

import base64
import json
import os
import shlex
import shutil
import subprocess
import sys

import pytest

from support.ops.quick_infra import QUICK, render_cloud_init

BOOTSTRAP = QUICK / "bootstrap.sh"

SETTINGS = b"CAIRN_TEST_SETTING='synthetic $value with spaces'\nOTHER=line\n\n"

TOKEN = "synthetic-metadata-token"

AWS_DENIED = (
    "An error occurred (AccessDeniedException) when calling the {} operation: User: "
    "arn:aws:sts::000000000000:assumed-role/cairn-quick-example/i-00000000000000000 is not "
    "authorized to perform: secretsmanager:{} on resource: cairn-quick-example"
)

CURL_DENIED = "curl: (22) The requested URL returned error: 403"

RESTORE_FAILED = 'service "litestream-restore" didn\'t complete successfully: exit 1'

SCRIPT_PYTHON = sys.executable  # what the script's `python3` is, on any machine

SCRIPT_WARNINGS = "error,ignore::ResourceWarning"  # the later filter wins

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
    if args[:3] == ["compose", "--progress", "quiet"]:
        args = ["compose", *args[3:]]
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
            # What Compose 5.5 was seen to print, and the worst it could: the file itself.
            print('time="2026-11-20T00:00:00Z" level=warning msg="The \\"value\\" variable is not'
                  ' set. Defaulting to a blank string."', file=sys.stderr)
            print(f"failed to read {os.getcwd()}/cairn.env: line 1: unterminated quoted value"
                  " 'synthetic $value with spaces", file=sys.stderr)
            print(os.environ["STANDIN_SECRET"], file=sys.stderr)
            print(os.environ["STANDIN_COMPOSE_ERROR"], file=sys.stderr)
            sys.exit(1)
        if mode == "compose-warns":
            print('time="2026-11-20T00:00:00Z" level=warning msg="The \\"value\\" variable is not'
                  ' set. Defaulting to a blank string."', file=sys.stderr)
        (state / "stack-started").touch()
    elif args == ["compose", "ps", "-q", "cairn"]:
        if mode == "no-container":
            sys.exit()
        print("0123456789ab")
    elif args == ["inspect", "-f", "{{.Image}}", "0123456789ab"]:
        print("sha256:0f1e2d3c4b5a")
    elif args[:3] == ["inspect", "-f", "{{json .Config.Env}}"]:
        given = state / "container-env.json"
        if args[3:] != ["0123456789ab", "sha256:0f1e2d3c4b5a"] or mode == "inspect-fails":
            print(os.environ["STANDIN_SECRET"], file=sys.stderr)
            sys.exit(1)
        image = ["PATH=/app/bin:/usr/bin", "PYTHONUNBUFFERED=1"]
        settings = json.loads(given.read_text()) if given.exists() else []
        print(json.dumps(image + settings))
        print(json.dumps(image))
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
    if mode in ("fail-" + kind, "fail-" + kind + "-echo"):
        if kind == "read" and cmd == "aws":  # the value was sent before the tool failed
            print(json.dumps({"ARN": arn, "SecretString": os.environ["STANDIN_SECRET"]}))
        elif kind == "read":
            print(json.dumps({"payload": {"data": os.environ["STANDIN_PAYLOAD"],
                                          "dataCrc32c": os.environ["STANDIN_CRC32C"]}}))
        if mode.endswith("-echo"):  # and a tool that repeats what it read
            print("received: synthetic $value with spaces", file=sys.stderr)
        operation = "ListSecretVersionIds" if kind == "list" else "GetSecretValue"
        print(os.environ["STANDIN_TOOL_ERROR"].format(operation, operation), file=sys.stderr)
        sys.exit(254 if cmd == "aws" else 22)
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
    python3 = bin_dir / "python3"
    python3.write_text(f'#!/bin/sh\nexec "{SCRIPT_PYTHON}" "$@"\n', encoding="utf-8")
    python3.chmod(0o755)
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
        "PYTHONWARNINGS": SCRIPT_WARNINGS,
        "HOME": str(vm),
        "STANDIN_STATE": str(vm),
        "STANDIN_MODE": mode,
        "STANDIN_SECRET": settings.decode(),
        "STANDIN_PAYLOAD": base64.b64encode(settings).decode(),
        "STANDIN_CRC32C": crc32c(settings),
        "STANDIN_TOKEN": TOKEN,
        "STANDIN_TOOL_ERROR": AWS_DENIED if cloud == "aws" else CURL_DENIED,
        "STANDIN_COMPOSE_ERROR": RESTORE_FAILED,
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


def boot_environment(files):
    return dict(
        item.split("=", 1) for item in shlex.split(files["/opt/cairn/quick.env"]["content"])
    )


_COMPOSE_CMD: list[str] | None = None


def _compose_command(env: dict[str, str]) -> list[str]:
    global _COMPOSE_CMD
    if _COMPOSE_CMD is not None:
        return _COMPOSE_CMD
    standalone = shutil.which("docker-compose")
    docker = shutil.which("docker")
    if standalone is None and docker is None:
        pytest.skip("Docker Compose is needed to render the service command")
    compose = [standalone] if standalone else [docker, "compose"]
    version = subprocess.run([*compose, "version"], env=env, capture_output=True, timeout=20)
    if version.returncode != 0:
        pytest.skip("Docker Compose is needed to render the service command")
    _COMPOSE_CMD = compose
    return compose


def compose_service(vm, said=False, refused=False, environment=None):
    # A project render reads the .env and env_file itself and needs no Docker daemon.
    # With `said`, also what Compose wrote on standard error while it read them.
    env = {"HOME": str(vm), "PATH": os.environ["PATH"], "DOCKER_HOST": f"unix://{vm}/no-daemon"}
    env |= environment or {}  # what Compose itself runs with: a name alone takes its value there
    compose = _compose_command(env)
    done = subprocess.run(
        [*compose, "config", "--format", "json"],
        cwd=vm / "deploy",
        env=env,
        capture_output=True,
        timeout=20,
    )
    if refused:  # what Compose said when it would not read the files
        assert done.returncode != 0, "Compose rendered the quick project"
        return done.stderr.decode()
    assert done.returncode == 0, "Compose could not render the quick project"
    service = json.loads(done.stdout)["services"]["cairn"]
    return (service, done.stderr.decode()) if said else service


def judged_with_compose_deciding(vm, settings, environment=None):
    """What Compose resolves for cairn from the boot's own files with these settings, and
    the journal of a start whose container holds exactly that."""
    files = {file["path"]: file for file in render_cloud_init(vm, "aws")["write_files"]}
    done = run(vm, config=boot_environment(files), settings=settings)
    assert done.returncode == 0, done.stderr
    for path, file in files.items():
        if path.startswith("/opt/cairn/deploy/"):
            (vm / path.removeprefix("/opt/cairn/")).write_bytes(base64.b64decode(file["content"]))
    service, said = compose_service(vm, said=True, environment=environment)
    assert said.strip() == ""
    given = {name: value.replace("$$", "$") for name, value in service["environment"].items()}
    (vm / "container-env.json").write_text(json.dumps([f"{n}={v}" for n, v in given.items()]))
    done = run(vm, config=boot_environment(files), settings=settings)
    assert done.returncode == 0, done.stderr
    return given, done.stderr.splitlines()
