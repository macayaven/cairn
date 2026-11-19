"""Check the inputs in the quickstart and render its cloud-init away from any cloud provider."""

import base64
import gzip
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
QUICK = REPO / "infra" / "quick"
VERSION = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
INPUTS = {"aws": "region", "gcp": "project_id"}
CLOUD_NAMES = {"aws": "AWS", "gcp": "Google Cloud"}


@pytest.mark.parametrize("cloud", INPUTS)
def test_the_three_example_inputs_are_the_only_required_variables_and_the_image_matches(cloud):
    root = QUICK / cloud
    assert (root / "variables.tf").is_file(), "the quick stack inputs have not been defined"
    variables = (root / "variables.tf").read_text()
    declarations = dict(re.findall(r'^variable "(\w+)" \{(.*?)^}', variables, re.M | re.S))
    required = {
        name for name, body in declarations.items() if not re.search(r"^\s+default\s*=", body, re.M)
    }
    assert required == {INPUTS[cloud], "domain", "acme_email"}
    example = (root / "terraform.tfvars.example").read_text()
    assert set(re.findall(r"^(\w+)\s*=", example, re.M)) == required
    assert re.search(
        rf'^\s+default\s*=\s*"ghcr\.io/macayaven/cairn:{re.escape(VERSION)}"$',
        declarations["image"],
        re.M,
    )


@pytest.mark.parametrize("cloud", INPUTS)
def test_quickstart_commands_match_stack_outputs_and_the_release_tag(cloud):
    readme = QUICK / "README.md"
    assert readme.is_file(), "the quickstart has not been written"
    text = readme.read_text()
    section = re.search(rf"^## {CLOUD_NAMES[cloud]}\n(.*?)(?=^## |\Z)", text, re.M | re.S)[1]
    outputs = (QUICK / cloud / "outputs.tf").read_text()
    declared = set(re.findall(r'^output "(\w+)"', outputs, re.M))
    used = set(re.findall(r"tofu output -raw (\w+)", section))
    assert used <= declared
    assert {
        "public_ipv4",
        "url",
        "connect_command",
        "first_key_command",
        "settings_secret",
        "backup_bucket",
    } <= used
    steps = [
        f"git clone --branch v{VERSION} ",
        f"cd cairn/infra/quick/{cloud}",
        "cp terraform.tfvars.example terraform.tfvars",
        "tofu init",
        "tofu apply",
        "tofu output -raw public_ipv4",
        "tofu output -raw url",
        "tofu output -raw connect_command",
        "tofu output -raw first_key_command",
    ]
    positions = [section.index(step) for step in steps]
    assert positions == sorted(positions)
    assert all(f"`{name}`" in section for name in [INPUTS[cloud], "domain", "acme_email"])
    assert "sudo systemctl restart cairn-quick.service" in section
    assert "[provider sign-in steps](#provider-sign-in)" in section
    assert "tofu destroy" in section
    assert ("put-secret-value" if cloud == "aws" else "secrets versions add") in section


def render_cloud_init(tmp_path, cloud, *, serve_extra_args=()):
    template = QUICK / "cloud-init.yaml.tftpl"
    assert template.is_file(), "the shared cloud-init template has not been written"
    tofu = shutil.which("tofu")
    if tofu is None:
        pytest.skip("OpenTofu is needed to render the cloud-init template")
    arguments = json.dumps(
        {
            "quick_dir": str(QUICK),
            "serve_extra_args": list(serve_extra_args),
            "config": {
                "CAIRN_CLOUD": cloud,
                "CAIRN_DOMAIN": "cairn.example.net",
                "ACME_EMAIL": "ops@example.net",
                "CAIRN_IMAGE": f"ghcr.io/macayaven/cairn:{VERSION}",
                "LITESTREAM_REPLICA_URL": f"{'s3' if cloud == 'aws' else 'gs'}://example-replica/cairn",
                "CAIRN_SETTINGS_SECRET": "example-secret",
                **({"AWS_REGION": "us-east-1"} if cloud == "aws" else {}),
            },
        }
    )
    codec = "base64gzip" if cloud == "aws" else "base64encode"
    done = subprocess.run(
        [tofu, "console", "-no-color"],
        input=f"{codec}(templatefile({json.dumps(str(template))}, {arguments}))\n",
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert done.returncode == 0, done.stderr
    payload = base64.b64decode(json.loads(done.stdout))
    if cloud == "aws":
        assert len(payload) <= 16384, "EC2 allows at most 16 KiB of raw user data"
        payload = gzip.decompress(payload)
    return yaml.safe_load(payload)


@pytest.mark.parametrize("cloud", INPUTS)
def test_the_payload_copies_the_four_deploy_files_as_bytes(tmp_path, cloud):
    main = (QUICK / cloud / "main.tf").read_text()
    assert "${path.module}/../cloud-init.yaml.tftpl" in main
    assert re.search(r"^\s+serve_extra_args\s*=\s*var\.serve_extra_args$", main, re.M)
    cloud_init = render_cloud_init(tmp_path, cloud)
    files = {item["path"]: item for item in cloud_init["write_files"]}
    for name in ("compose.yaml", "compose.backup.yaml", "Caddyfile", "litestream.yml"):
        item = files[f"/opt/cairn/deploy/{name}"]
        assert item["encoding"] == "b64"
        assert base64.b64decode(item["content"]) == (REPO / "deploy" / name).read_bytes()
    assert any("cairn-quick.service" in " ".join(command) for command in cloud_init["runcmd"])
    assert (
        "/opt/cairn/bootstrap.sh firewall"
        in files["/etc/systemd/system/docker.service.d/cairn-quick.conf"]["content"]
    )


@pytest.mark.parametrize("cloud", INPUTS)
def test_serve_arguments_cannot_add_environment_assignments(tmp_path, cloud):
    tofu = shutil.which("tofu")
    if tofu is None:
        pytest.skip("OpenTofu is needed to check the variable validation")
    # Plan only the variable declarations, without a provider or any resources.
    shutil.copyfile(QUICK / cloud / "variables.tf", tmp_path / "variables.tf")
    values = {
        INPUTS[cloud]: "us-east-1" if cloud == "aws" else "example-project",
        "domain": "cairn.example.net",
        "acme_email": "ops@example.net",
        "serve_extra_args": ["--brand-name", "Fable\nEXTRA=value"],
    }
    (tmp_path / "terraform.tfvars.json").write_text(json.dumps(values))
    done = subprocess.run(
        [tofu, "plan", "-input=false", "-no-color"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert done.returncode == 1
    assert "Use nonempty serve arguments" in done.stderr


def test_provider_sign_in_guide_shows_what_to_configure_and_what_the_user_sees():
    section = (QUICK / "README.md").read_text().split("## Provider sign-in\n")[1].split("## ")[0]
    steps = [
        "https://cairn.example.net/auth/google/callback",
        "CAIRN_GOOGLE_CLIENT_ID=",
        "CAIRN_GOOGLE_CLIENT_SECRET=",
        'serve_extra_args = ["--sign-in", "google", "--allowed-email-domain", "example.net"]',
        "Run `tofu apply`. Changing these arguments replaces the VM",
        "https://cairn.example.net/login",
        "Continue with Google",
    ]
    positions = [section.index(step) for step in steps]
    assert positions == sorted(positions)


def test_the_compose_override_only_grants_the_replica_tasks_metadata_access():
    override = QUICK / "compose.quick.yaml"
    assert override.is_file(), "the backup bridge override has not been written"
    # Only read the structure here. Compose handles its own !reset tag.
    compose = yaml.load(override.read_text(), Loader=yaml.BaseLoader)
    services = compose["services"]
    assert {name for name, config in services.items() if "networks" in config} == {
        "litestream",
        "litestream-restore",
    }
    for name in ("litestream", "litestream-restore"):
        assert services[name]["networks"] == ["backup"]
    assert (
        compose["networks"]["backup"]["driver_opts"]["com.docker.network.bridge.name"]
        == "br-cairn-backup"
    )
    for name in ("cairn", "caddy", "litestream"):
        assert services[name]["restart"] == "on-failure"
