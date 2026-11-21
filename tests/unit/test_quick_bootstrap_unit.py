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
COMPOSE_UP = ["docker", "compose", "--progress", "quiet", "up", "-d", "--pull", "missing"]
AWS_DENIED = (
    "An error occurred (AccessDeniedException) when calling the {} operation: User: "
    "arn:aws:sts::000000000000:assumed-role/cairn-quick-example/i-00000000000000000 is not "
    "authorized to perform: secretsmanager:{} on resource: cairn-quick-example"
)
CURL_DENIED = "curl: (22) The requested URL returned error: 403"
RESTORE_FAILED = 'service "litestream-restore" didn\'t complete successfully: exit 1'
LEFT_OUT = "(a line that repeats part of a setting was left out)"
CONTAINER = "0123456789ab"
# What the boot asks Docker after a start, to compare the settings: which container, and
# the environment it was made with.
IMAGE = "sha256:0f1e2d3c4b5a"
# The container, its image, and the environment each was made with: the image's own is what
# tells a setting Compose gave from a variable the image brings.
SETTINGS_CHECK = [
    ["docker", "compose", "ps", "-q", "cairn"],
    ["docker", "inspect", "-f", "{{.Image}}", CONTAINER],
    ["docker", "inspect", "-f", "{{json .Config.Env}}", CONTAINER, IMAGE],
]
NOT_COMPARED = "cairn-quick: settings check: {} line(s) of the settings not compared"
DIFFERS = (
    "cairn-quick: settings check: {} differs in cairn from what the secret holds. How to write"
    ' a value: "AWS settings" or "Google Cloud settings" in infra/quick/README.md'
)
DID_NOT_RUN = "cairn-quick: settings check: did not run"
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


def compose_service(vm, said=False):
    # A project render reads the .env and env_file itself and needs no Docker daemon.
    # With `said`, also what Compose wrote on standard error while it read them.
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
    service = json.loads(done.stdout)["services"]["cairn"]
    return (service, done.stderr.decode()) if said else service


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
    assert calls(vm)[-4:] == [COMPOSE_UP, *SETTINGS_CHECK]
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
    assert "cairn-quick: settings read: the data of the secret does not match its checksum" in (
        done.stderr.splitlines()
    )
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
    assert calls(vm)[-4:] == [COMPOSE_UP, *SETTINGS_CHECK]


def test_cached_restart_does_not_contact_the_registry(vm):
    assert run(vm).returncode == 0
    (vm / "registry-down").touch()
    (vm / "stack-started").unlink()
    done = run(vm)
    assert done.returncode == 0, done.stderr
    assert (vm / "stack-started").exists()


@pytest.mark.parametrize("status_available", [True, False])
def test_compose_failure_says_why_in_composes_words_and_repeats_no_setting(vm, status_available):
    """A failed `up` leaves Compose's own error in the unit's journal, one line each, and
    the unit fails. What Compose can say about the settings file is said without the
    settings: its syntax error keeps the line number and loses what it quotes, its warning
    that names what follows a $ in a value is put in other words, and a line that repeats
    a value is left out."""
    mode = "compose-failure" if status_available else "compose-failure-no-status"
    done = run(vm, mode=mode)
    assert done.returncode != 0
    assert done.stdout == ""
    said = done.stderr.splitlines()
    assert f"cairn-quick: compose: {RESTORE_FAILED}" in said
    assert "cairn-quick: compose: the settings file, line 1: unterminated quoted value" in said
    assert any("in single quotes" in line for line in said)
    assert said.count(f"cairn-quick: compose: {LEFT_OUT}") == 2  # the file's two lines
    assert "Docker Compose up failed" in said[-1]
    for part in ("$value", "synthetic", "with spaces", "OTHER=line", '"value"'):
        assert part not in done.stderr, part
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
    assert "could not read the cairn settings secret" in done.stderr.splitlines()[-1]
    # The tool's own error, on a line of its own, in the unit's journal.
    operation = "ListSecretVersionIds" if mode == "fail-list" else "GetSecretValue"
    denied = AWS_DENIED.format(operation, operation) if cloud == "aws" else CURL_DENIED
    assert f"cairn-quick: settings read: {denied}" in done.stderr.splitlines()
    assert "Traceback" not in done.stderr and "  File" not in done.stderr
    # A failing read sent the value before it failed (`fail-read`): none of it is printed.
    for part in ("$value", "synthetic", "with spaces", "OTHER", "SecretString", "payload"):
        assert part not in done.stdout + done.stderr, part
    assert TOKEN not in done.stdout + done.stderr
    assert list((vm / "deploy").iterdir()) == [existing]
    assert existing.read_text() == "EXISTING=synthetic\n"
    assert not any(call[:2] == ["docker", "compose"] for call in calls(vm))


def test_a_start_that_worked_passes_on_what_compose_warned_of(vm):
    """Compose warns when a settings value holds a $ before a name no variable has: it puts
    nothing in its place, and the stack starts with another value than the secret holds. The
    boot passes that warning on, in other words, since Compose's own name what follows the $.
    It can pass on only what Compose says (the next test: most changes come without a word)."""
    done = run(vm, mode="compose-warns")
    assert done.returncode == 0, done.stderr
    assert (vm / "stack-started").exists()
    (said,) = done.stderr.splitlines()
    assert said.startswith("cairn-quick: compose: a settings value holds a $")
    assert "single quotes" in said
    assert '"value"' not in done.stderr and "$value" not in done.stderr


COMPOSE_READS = (
    "Compose reads the file, and a value arrives as written only between single quotes (a"
    " single quote inside is written `\\'`). Outside them Compose changes three kinds of value:"
    " it puts a variable's value in place of a `$` and the name after it, or nothing when no"
    " variable has that name; it drops a `#` after a space, with the rest of the line; and it"
    " removes quotes around the value."
)
COMPOSE_SAYS = (
    "Compose reports one of these, the `$` before a name no variable has, and the boot passes"
    " on what Compose says, as a line in the unit's journal that starts with"
    " `cairn-quick: compose:`. The other changes come without a word from Compose, so after"
    " every start the boot compares the settings in the secret with what cairn's container was"
    " given. It reads the lines written `NAME=value`, the value between single quotes or to"
    " the end of the line, and names each of those that differs, the name and nothing of its"
    " value, on a line that starts with `cairn-quick: settings check:`. A value in double"
    " quotes that runs over more than one line is not read as one value: its first line is"
    " named as differing, and a later line of it that looks like a setting can be named too."
    " Compose reads more"
    " forms than that (a colon for the equals sign, `export` before the name, spaces before"
    " it), so every other line that is not blank or a comment is counted on one line,"
    " `cairn-quick: settings check: N line(s) of the settings not compared`, with the names"
    " cairn was given: write those as `NAME=value`. A start with neither kind of line shows"
    " that every setting cairn was given arrived as written; with a `not compared` line, every"
    " setting but those; `cairn-quick: settings check: did not run` means that nothing was"
    " compared."
)


def test_compose_changes_three_kinds_of_setting_and_reports_one(vm):
    """What the guide tells a person about writing `cairn.env` (`COMPOSE_READS`,
    `COMPOSE_SAYS`), held to Compose itself, reading the files the boot wrote: which values
    arrive changed, and for which of them Compose says anything on standard error."""
    written = {
        "PLAIN": "two words, a=b and c#d",
        "SINGLE": "'pa$CAIRN_DOMAIN #x \"y\" it\\'s'",
        "SET": "pa$CAIRN_DOMAIN",  # a name the boot's own .env sets
        "UNSET": "pa$word9",
        "COMMENT": "abc #def",
        "DOUBLE": '"quoted"',
    }
    settings = "".join(f"{name}={value}\n" for name, value in written.items()).encode()
    files = {file["path"]: file for file in render_cloud_init(vm, "aws")["write_files"]}
    done = run(vm, config=boot_environment(files), settings=settings)
    assert done.returncode == 0, done.stderr
    for path, file in files.items():
        if path.startswith("/opt/cairn/deploy/"):
            (vm / path.removeprefix("/opt/cairn/")).write_bytes(base64.b64decode(file["content"]))
    service, said = compose_service(vm, said=True)
    # `config` writes a literal $ as $$, so that its output can be read again.
    got = {name: value.replace("$$", "$") for name, value in service["environment"].items()}
    assert got["PLAIN"] == "two words, a=b and c#d"
    assert got["SINGLE"] == 'pa$CAIRN_DOMAIN #x "y" it\'s'
    assert got["SET"] == "pacairn.example.net"  # the variable's value, in place
    assert got["UNSET"] == "pa"  # nothing in place: no variable word9
    assert got["COMMENT"] == "abc"  # dropped from the # after a space
    assert got["DOUBLE"] == "quoted"  # without its quotes
    # One line for four changed values: the unset name. Nothing of the other three.
    (warning,) = [line for line in said.splitlines() if line.strip()]
    assert 'The \\"word9\\" variable is not set' in warning
    assert "CAIRN_DOMAIN" not in said and "abc" not in said and "quoted" not in said
    guide = " ".join((QUICK / "README.md").read_text().split())
    for sentence in (COMPOSE_READS, COMPOSE_SAYS):  # under each cloud's settings, the same words
        assert guide.count(sentence) == 2, sentence
    assert guide.count(f"{COMPOSE_READS} {COMPOSE_SAYS}") == 2


def test_the_boot_script_says_what_its_passing_on_of_warnings_cannot_show():
    """The script's own comment at the line that passes Compose's warnings on, and the
    comparison that follows it for what Compose changes without a warning."""
    script = BOOTSTRAP.read_text().splitlines()
    at = max(n for n, line in enumerate(script) if line.startswith("  say_errors compose"))
    comment = " ".join(line.strip().lstrip("# ") for line in script[at - 2 : at])
    assert "it warns of a $ before a name no variable has" in comment
    assert "changes other values without a word. check_settings names those." in comment
    assert (
        script[at + 1] == "  check_settings || echo 'cairn-quick: settings check: did not run' >&2"
    )


def digests(*values):
    """Each value as it is and as the digests a careless check might print."""
    import hashlib

    forms = set()
    for value in values:
        data = value.encode()
        forms |= {value, base64.b64encode(data).decode()}
        forms |= {hashlib.new(kind, data).hexdigest() for kind in ("md5", "sha1", "sha256")}
        forms |= {base64.b64encode(hashlib.sha256(data).digest()).decode()}
    return {form for form in forms if len(form) >= 4}


AWKWARD = 'a$CAIRN_DOMAIN #c "d" it\'s e\\f g=h\nsecond line $word9'


def test_a_setting_that_reached_cairn_changed_is_named_and_nothing_else(vm):
    """After a start, the boot compares each setting the secret holds with what cairn's
    container was given and names the ones that differ: the name, never a value, a part of
    one, its length or a digest, of that setting or another. A setting the container was
    not given at all is no difference (the stand-in's container has no ABSENT). The start
    has worked, and the unit's result stays."""
    wrote = {
        "PLAIN": "zq7 wm2, a=b and c#d",
        "SINGLE": "'"
        + AWKWARD.replace("'", "\\'")
        + "'  # between single quotes, as the guide says",
        "SET": "kx9$CAIRN_DOMAIN",
        "COMMENT": "tblp4 #nvh8",
        "DOUBLE": '"rj3ydc"',
        "ABSENT": "hj8rd3 #yc",
    }
    settings = "".join(f"{name}={value}\n" for name, value in wrote.items()).encode()
    given = {  # as Compose 5.5.0 was seen to give them
        "PLAIN": wrote["PLAIN"],
        "SINGLE": AWKWARD,
        "SET": "kx9cairn.example.net",
        "COMMENT": "tblp4",
        "DOUBLE": "rj3ydc",
    }
    env = [f"{name}={value}" for name, value in given.items()]
    (vm / "container-env.json").write_text(json.dumps(env))
    done = run(vm, settings=settings)
    assert done.returncode == 0, done.stderr
    assert done.stdout == ""
    assert (vm / "stack-started").exists()
    assert done.stderr.splitlines() == [
        DIFFERS.format(name) for name in ("SET", "COMMENT", "DOUBLE")
    ]
    assert calls(vm)[-4:] == [COMPOSE_UP, *SETTINGS_CHECK]
    parts = ["zq7", "wm2", "kx9", "tblp4", "nvh8", "rj3ydc", "hj8rd3", "second line", "word9"]
    for form in digests(*wrote.values(), *given.values()) | set(parts):
        assert form not in done.stderr, form
    guide = (QUICK / "README.md").read_text()
    assert "\n### AWS settings\n" in guide and "\n### Google Cloud settings\n" in guide


def test_composes_three_silent_changes_are_each_named_and_the_guides_way_is_not(vm):
    """The same, with Compose itself deciding what cairn's container is given: the boot's
    own files are read by Compose, and what it resolves for cairn is what the stand-in's
    container holds. A `$` before a set name, a `#` after a space and quotes around a value
    are each named. A value written between single quotes, with every awkward character and
    a line break in it, arrives as written and is not."""
    wrote = {
        "PLAIN": "zq7 wm2, a=b and c#d",
        "SINGLE": "'" + AWKWARD.replace("'", "\\'") + "'",
        "SET": "kx9$CAIRN_DOMAIN",
        "COMMENT": "tblp4 #nvh8",
        "DOUBLE": '"rj3ydc"',
    }
    settings = "".join(f"{name}={value}\n" for name, value in wrote.items()).encode()
    files = {file["path"]: file for file in render_cloud_init(vm, "aws")["write_files"]}
    done = run(vm, config=boot_environment(files), settings=settings)
    assert done.returncode == 0, done.stderr
    for path, file in files.items():
        if path.startswith("/opt/cairn/deploy/"):
            (vm / path.removeprefix("/opt/cairn/")).write_bytes(base64.b64decode(file["content"]))
    service, said = compose_service(vm, said=True)
    assert said.strip() == ""  # Compose said nothing of any of them
    # `config` writes a literal $ as $$, so that its output can be read again.
    given = {name: value.replace("$$", "$") for name, value in service["environment"].items()}
    assert given["SINGLE"] == AWKWARD and given["PLAIN"] == wrote["PLAIN"]
    assert (given["SET"], given["COMMENT"], given["DOUBLE"]) == (
        "kx9cairn.example.net",
        "tblp4",
        "rj3ydc",
    )
    (vm / "container-env.json").write_text(json.dumps([f"{n}={v}" for n, v in given.items()]))
    done = run(vm, config=boot_environment(files), settings=settings)
    assert done.returncode == 0, done.stderr
    assert done.stderr.splitlines() == [
        DIFFERS.format(name) for name in ("SET", "COMMENT", "DOUBLE")
    ]
    for form in digests(*wrote.values(), *given.values()) | {"zq7", "kx9", "tblp4", "rj3ydc"}:
        assert form not in done.stderr, form


# The forms of a settings line that the check does not read, each with what Compose 5.5.0 was
# seen to give cairn for it (None: not given). The reviewer's case is the first.
UNREAD = (
    ('COLON: "zq7 wm2"', "COLON", "zq7 wm2"),
    ("export EXPORTED=kx9 #tblp4", "EXPORTED", "kx9"),
    ("BARE", "BARE", None),
    ("  INDENTED=nvh8 #rj3", "INDENTED", "nvh8"),
    ("BACKSLASHES='ydc\\\\'", "BACKSLASHES", "ydc\\\\"),
    ("colon2:hj8$CAIRN_DOMAIN", "colon2", "hj8cairn.example.net"),
)
# Lines it does read, among them: an equals sign with spaces round it, and a value in double
# quotes that goes on over a line, whose second line is no setting.
READ = (
    ('EQUALS="pm4 wq"', "EQUALS", "pm4 wq"),
    ('SPACED = "xz5"', "SPACED", "xz5"),
    ('MULTI="lp4', "MULTI", "lp4\nINNER=rd3"),
    ('INNER=rd3"', "INNER", None),
    ("AFTER=bt9", "AFTER", "bt9"),
)
SETTINGS_OF_EVERY_FORM = "".join(f"{line}\n" for line, _, _ in (*UNREAD[:3], *READ, *UNREAD[3:]))
JUDGED = [
    *(DIFFERS.format(name) for name in ("EQUALS", "SPACED", "MULTI")),
    NOT_COMPARED.format(6) + ": BACKSLASHES, COLON, EXPORTED, INDENTED, colon2",
]


def searched_for_values(journal):
    values = [line.partition("=")[2] or line.partition(":")[2] for line, _, _ in (*UNREAD, *READ)]
    values += [given for _, _, given in (*UNREAD, *READ) if given]
    parts = {"zq7", "wm2", "kx9", "tblp4", "nvh8", "rj3", "ydc", "hj8", "pm4", "xz5", "lp4", "rd3"}
    for form in digests(*values) | parts | {"bt9", "example.net"}:
        assert form not in journal, form


def test_a_line_the_check_does_not_read_is_counted_and_named_never_passed_over(vm):
    """The check reads a name, an equals sign and a value between single quotes or the rest
    of the line. Compose reads more (a colon for the equals sign, `export`, a line that
    begins with spaces), and a setting written so reaches cairn, changed or not, unjudged.
    Each such line is counted, and named where the container has a variable of that name,
    so that a journal without a "not compared" line means no line went unjudged."""
    given = [f"{name}={value}" for _, name, value in (*UNREAD, *READ) if value is not None]
    (vm / "container-env.json").write_text(json.dumps(given))
    done = run(vm, settings=SETTINGS_OF_EVERY_FORM.encode())
    assert done.returncode == 0, done.stderr
    assert done.stderr.splitlines() == JUDGED
    searched_for_values(done.stderr)


def test_compose_itself_gives_what_the_check_counts_as_not_compared(vm):
    """The same lines, with Compose deciding what cairn is given for each."""
    files = {file["path"]: file for file in render_cloud_init(vm, "aws")["write_files"]}
    done = run(vm, config=boot_environment(files), settings=SETTINGS_OF_EVERY_FORM.encode())
    assert done.returncode == 0, done.stderr
    for path, file in files.items():
        if path.startswith("/opt/cairn/deploy/"):
            (vm / path.removeprefix("/opt/cairn/")).write_bytes(base64.b64decode(file["content"]))
    service, said = compose_service(vm, said=True)
    assert said.strip() == ""  # Compose reads every one of them, and says nothing
    given = {name: value.replace("$$", "$") for name, value in service["environment"].items()}
    assert given == {name: value for _, name, value in (*UNREAD, *READ) if value is not None}
    (vm / "container-env.json").write_text(json.dumps([f"{n}={v}" for n, v in given.items()]))
    done = run(vm, config=boot_environment(files), settings=SETTINGS_OF_EVERY_FORM.encode())
    assert done.returncode == 0, done.stderr
    assert done.stderr.splitlines() == JUDGED
    searched_for_values(done.stderr)


def test_a_value_in_double_quotes_over_lines_names_a_line_too_many_and_the_script_says_so(vm):
    """The check does not read double quotes. A value in them that runs over lines is read
    line by line: its first line differs from what cairn was given, and its second, which
    here begins like the setting REAL, is taken for that setting and named, though REAL
    arrived as written. A line too many, never one too few; the comment over the check and
    the guide (`COMPOSE_SAYS`) say so."""
    settings = b"REAL=nvh8\nMULTI=\"rj3\nREAL=ydc\"\nLAST='hj8 $x'\n"
    files = {file["path"]: file for file in render_cloud_init(vm, "aws")["write_files"]}
    done = run(vm, config=boot_environment(files), settings=settings)
    assert done.returncode == 0, done.stderr
    for path, file in files.items():
        if path.startswith("/opt/cairn/deploy/"):
            (vm / path.removeprefix("/opt/cairn/")).write_bytes(base64.b64decode(file["content"]))
    service, said = compose_service(vm, said=True)
    assert said.strip() == ""
    given = {name: value.replace("$$", "$") for name, value in service["environment"].items()}
    assert given == {"REAL": "nvh8", "MULTI": "rj3\nREAL=ydc", "LAST": "hj8 $x"}
    (vm / "container-env.json").write_text(json.dumps([f"{n}={v}" for n, v in given.items()]))
    done = run(vm, config=boot_environment(files), settings=settings)
    assert done.returncode == 0, done.stderr
    assert done.stderr.splitlines() == [DIFFERS.format("REAL"), DIFFERS.format("MULTI")]
    for form in digests(*given.values()) | {"nvh8", "rj3", "ydc", "hj8"}:
        assert form not in done.stderr, form
    script = BOOTSTRAP.read_text()
    comment = script[: script.index("check_settings() {")].rsplit("\n\n", 1)[1]
    said_there = " ".join(line.lstrip("# ") for line in comment.splitlines())
    assert "double quotes over lines is not read" in said_there
    assert "first line is named as differing" in said_there
    assert "a later line of it that looks like a setting can be named too" in said_there


def test_a_setting_cairn_was_given_that_no_line_read_holds_is_counted_too(vm):
    """The other way round: what the container was given beyond its image's own variables
    came from the settings. One that no line the check read holds went unjudged, wherever
    it came from, and is counted and named like a line not read."""
    (vm / "container-env.json").write_text(json.dumps(["PLAIN=zq7 wm2", "GHOST=kx9 tblp4"]))
    done = run(vm, settings=b"PLAIN=zq7 wm2\n")
    assert done.returncode == 0, done.stderr
    assert done.stderr.splitlines() == [NOT_COMPARED.format(1) + ": GHOST"]
    for form in digests("zq7 wm2", "kx9 tblp4") | {"zq7", "kx9", "tblp4"}:
        assert form not in done.stderr, form


@pytest.mark.parametrize("mode", ["no-container", "inspect-fails"])
def test_a_settings_check_that_cannot_run_says_so_and_changes_nothing(vm, mode):
    """Without a container of cairn, or with Docker failing, the comparison cannot be made:
    one line says it did not run, nothing Docker said of it is printed, and the unit's result
    is the start's."""
    done = run(vm, mode=mode)
    assert done.returncode == 0, done.stderr
    assert (vm / "stack-started").exists()
    assert done.stderr.splitlines() == [DID_NOT_RUN]
    assert "$value" not in done.stderr and "synthetic" not in done.stderr


def test_a_secret_without_settings_asks_docker_nothing_after_the_start(vm):
    done = run(vm, mode="empty")
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    assert calls(vm)[-1] == COMPOSE_UP


def test_a_tool_that_repeats_what_it_read_does_not_get_it_into_the_journal(vm):
    """The value travels on standard output into a staged file and nowhere else. Should a
    tool also repeat it among its errors, that line is left out and the others stay."""
    done = run(vm, "gcp", "fail-read-echo")
    assert done.returncode != 0
    said = done.stderr.splitlines()
    assert f"cairn-quick: settings read: {CURL_DENIED}" in said
    assert f"cairn-quick: settings read: {LEFT_OUT}" in said
    for part in ("$value", "synthetic", "with spaces"):
        assert part not in done.stdout + done.stderr, part
    assert not (vm / "deploy" / "cairn.env").exists()
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
