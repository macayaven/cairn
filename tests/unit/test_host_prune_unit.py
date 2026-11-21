"""The boot disk does not fill: after a deployment is promoted, host.sh's image pruning keeps
the running image, the one before it and anything newer than three days. Run from a copy of
host.sh against stand-ins (tests/unit/deploy_standins.py); the stand-in docker records each
call.

`docker image prune -a` removes every image no container uses. The one before is kept by a
container that is created and never started, `cairn-previous`, made from it.
"""

import re
import subprocess
from pathlib import Path

import pytest
from deploy_standins import BASH, IMAGE, NEW, OLD, Standins

REPO = Path(__file__).resolve().parents[2]
VARIABLES = (REPO / "infra" / "gcp" / "variables.tf").read_text(encoding="utf-8")


@pytest.fixture
def host(tmp_path):
    return Standins(tmp_path).host()


def promote(host, candidate: str) -> list[list[str]]:
    before = len(host.s.calls("docker"))
    done = subprocess.run(
        [BASH, str(host.script), "promote", candidate],
        env={**host.s.env({}), "HOST_ROOT": str(host.root)},
        cwd=host.root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return host.s.calls("docker")[before:]


def test_a_promotion_keeps_the_image_before_it_and_prunes_what_is_older_than_three_days(host):
    host.record("image", f"{IMAGE}@{OLD}")
    calls = promote(host, f"{IMAGE}@{NEW}")
    assert ["rm", "-f", "cairn-previous"] in calls
    assert ["create", "--name", "cairn-previous", f"{IMAGE}@{OLD}"] in calls
    prune = ["image", "prune", "-af", "--filter", "until=72h"]
    assert prune in calls
    # The keeping container exists before the prune runs.
    assert calls.index(["create", "--name", "cairn-previous", f"{IMAGE}@{OLD}"]) < calls.index(
        prune
    )
    assert (host.data / "deploy" / "image").read_text() == f"{IMAGE}@{NEW}\n"


def test_a_first_promotion_has_no_image_before_it_to_keep(host):
    calls = promote(host, f"{IMAGE}@{NEW}")
    assert not [c for c in calls if c[:1] == ["create"]]
    assert ["image", "prune", "-af", "--filter", "until=72h"] in calls


def test_finishing_a_promotion_cut_short_does_not_replace_the_image_kept_before(host):
    """settle calls promote again when the candidate is already the proven image: the image
    before it is the one the first call kept, and stays kept."""
    host.record("image", f"{IMAGE}@{NEW}")
    calls = promote(host, f"{IMAGE}@{NEW}")
    assert not [c for c in calls if c[:1] in (["create"], ["rm"])]


def test_nothing_prunes_by_thirty_days_any_more():
    text = (REPO / "infra" / "gcp" / "files" / "host.sh").read_text(encoding="utf-8")
    assert "until=720h" not in text
    assert len(re.findall(r"^\s*docker image prune ", text, re.M)) == 1


def test_the_boot_disk_is_20_gb_by_default_and_10_at_least():
    start = VARIABLES.index('variable "boot_disk_size_gb"')
    block = VARIABLES[start : VARIABLES.index("\n}\n", start)]
    assert re.search(r"^\s*default\s*=\s*20$", block, re.M)
    assert "var.boot_disk_size_gb >= 10" in block
