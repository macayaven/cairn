"""The cloud provider's version is pinned by a lock file in the repository.

`infra/gcp/versions.tf` gives a range (`~> 8.10`), and a range resolves to whatever is newest
on the day `tofu init` runs: the same commit could be checked with one provider in CI and
applied with another. `infra/gcp/.terraform.lock.hcl` names the one version and its hashes,
and `tofu init` takes that version wherever it runs.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GCP = REPO / "infra" / "gcp"
LOCK = GCP / ".terraform.lock.hcl"
PROVIDER = "registry.opentofu.org/hashicorp/google"


def flat(text: str) -> str:
    return " ".join(text.split())


def constraint() -> str:
    versions = (GCP / "versions.tf").read_text(encoding="utf-8")
    block = versions[versions.index("google = {") :]
    return re.search(r'version\s*=\s*"([^"]+)"', block)[1]


def locked() -> dict[str, str]:
    text = LOCK.read_text(encoding="utf-8")
    block = text[text.index(f'provider "{PROVIDER}"') :]
    return {
        "version": re.search(r'version\s*=\s*"([^"]+)"', block)[1],
        "constraints": re.search(r'constraints\s*=\s*"([^"]+)"', block)[1],
        "hashes": block[block.index("hashes = [") : block.index("]")],
    }


def test_the_lock_file_names_one_version_inside_the_range_the_configuration_gives():
    found = locked()
    assert found["constraints"] == constraint() == "~> 8.10"
    major, minor, patch = (int(part) for part in found["version"].split("."))
    assert major == 8 and minor >= 10 and patch >= 0  # what "~> 8.10" allows


def test_the_lock_file_holds_hashes_a_runner_and_a_laptop_can_check():
    hashes = locked()["hashes"]
    # zh: the registry's checksum of each platform's archive; h1: a checksum of its contents.
    assert len(re.findall(r'"zh:[0-9a-f]{64}"', hashes)) >= 2
    assert len(re.findall(r'"h1:[A-Za-z0-9+/]{43}="', hashes)) >= 2


def test_the_lock_file_locks_nothing_but_the_provider_the_configuration_asks_for():
    assert re.findall(r'provider "([^"]+)"', LOCK.read_text(encoding="utf-8")) == [PROVIDER]


def test_the_guide_says_the_provider_is_pinned_and_how_to_move_it():
    guide = flat((GCP / "README.md").read_text(encoding="utf-8"))
    assert (
        flat(
            "`tofu init` takes the google provider at the version in `.terraform.lock.hcl`, which"
            " is in git, so CI checks these files and you apply them with the same provider. To"
            " move to a newer one, run `tofu init -upgrade` and commit the change to that file."
        )
        in guide
    )
