"""deploy/compose.local.yaml: cairn on one computer with Docker Compose, no domain and no proxy.

The README's first steps run this file. These checks read it and compose.yaml; they need no
Docker.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"


def load(name: str) -> dict:
    return yaml.safe_load((DEPLOY / name).read_text(encoding="utf-8"))


LOCAL = load("compose.local.yaml")
DOMAIN = load("compose.yaml")


def test_it_runs_cairn_alone_under_the_same_project_name_as_the_domain_stack():
    # The same project name means the same volume, cairn_cairn-data, for both files.
    assert LOCAL["name"] == DOMAIN["name"] == "cairn"
    assert list(LOCAL["services"]) == ["cairn"]
    assert list(LOCAL["volumes"]) == ["cairn-data"]


def test_it_builds_or_pulls_the_same_image_as_the_domain_stack():
    local, domain = LOCAL["services"]["cairn"], DOMAIN["services"]["cairn"]
    assert local["image"] == domain["image"] == "${CAIRN_IMAGE:-cairn:local}"
    assert local["build"] == domain["build"] == {"context": ".."}


def test_it_publishes_one_port_on_this_computer_only():
    assert LOCAL["services"]["cairn"]["ports"] == ["127.0.0.1:${CAIRN_PORT:-8765}:8765"]


def test_it_keeps_the_database_on_the_volume_and_restarts_after_a_reboot():
    cairn = LOCAL["services"]["cairn"]
    assert cairn["volumes"] == ["cairn-data:/data"]
    assert cairn["restart"] == "unless-stopped"
    assert cairn["env_file"] == [{"path": "cairn.env", "required": False}]


def test_its_command_is_the_images_default_with_room_for_more():
    command = LOCAL["services"]["cairn"]["command"].split()
    assert command[:5] == ["serve", "--db", "/data/cairn.db", "--host", "0.0.0.0"]
    assert command[5:] == ["${CAIRN_EXTRA_ARGS:-}"]
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert 'CMD ["serve", "--db", "/data/cairn.db", "--host", "0.0.0.0"]' in dockerfile
