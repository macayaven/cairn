"""The compose files keep no Linux capability a container does not need, and no process in
them gains a privilege by exec (no-new-privileges). Each service drops every capability;
Caddy alone adds one back, to bind ports 80 and 443. scripts/rehearse_stack.py runs
compose.yaml with compose.backup.yaml as they ship, so each line here was run: these checks
read the files and need no Docker."""

import pytest
import yaml

from support.paths import REPO_ROOT

DEPLOY = REPO_ROOT / "deploy"
NO_NEW_PRIVILEGES = ["no-new-privileges:true"]
# What each service of each file adds back after dropping all of them.
ADDED = {
    ("compose.yaml", "cairn"): None,
    ("compose.yaml", "caddy"): ["NET_BIND_SERVICE"],
    ("compose.backup.yaml", "data-init"): None,
    ("compose.backup.yaml", "litestream-restore"): None,
    ("compose.backup.yaml", "litestream"): None,
    ("compose.local.yaml", "cairn"): None,
}


def services(name: str) -> dict:
    return yaml.safe_load((DEPLOY / name).read_text(encoding="utf-8"))["services"]


def test_every_service_each_file_defines_is_listed_here():
    """compose.backup.yaml is merged over compose.yaml: its `cairn` adds a dependency to the
    service compose.yaml defines, and is not one of its own."""
    files = {name for name, _ in ADDED}
    listed = {(name, service) for name in files for service in services(name)}
    assert listed - set(ADDED) == {("compose.backup.yaml", "cairn")}


@pytest.mark.parametrize(("name", "service"), sorted(ADDED))
def test_each_service_drops_every_capability_and_gains_no_privilege(name, service):
    spec = services(name)[service]
    assert spec["cap_drop"] == ["ALL"]
    assert spec.get("cap_add") == ADDED[(name, service)]
    assert spec["security_opt"] == NO_NEW_PRIVILEGES


def test_the_backup_file_does_not_say_again_what_compose_yaml_says_of_cairn():
    """Compose joins the two files' lists, and refuses a service whose security_opt holds
    the same item twice ("services.cairn.security_opt items at 0 and 1 are equal")."""
    cairn = services("compose.backup.yaml")["cairn"]
    assert set(cairn) == {"depends_on"}
