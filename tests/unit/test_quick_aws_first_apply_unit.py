"""Check the AWS quick deployment against what a first apply to a real account meets."""

import json
import re
import shutil
import subprocess

import pytest
from test_quick_infra_unit import QUICK

AWS = QUICK / "aws"


def block(kind, type_name, name):
    main = (AWS / "main.tf").read_text()
    found = re.search(rf'^{kind} "{type_name}" "{name}" \{{\n(.*?)^}}', main, re.M | re.S)
    assert found, f'{kind} "{type_name}" "{name}" is not defined'
    return found[1]


def test_the_subnet_is_made_in_a_zone_that_offers_the_instance_type():
    """EC2 chooses the zone of a subnet that names none (CreateSubnet, `AvailabilityZone`:
    "Default: AWS selects one for you"), and an instance type that a Region supports "might
    not be supported in all of the Availability Zones for that Region" (Amazon EC2 instance
    types by Region). The instance then fails to launch with `Unsupported`, after the rest
    of the stack was made. So the definitions ask which zones offer the type and name one."""
    offerings = block("data", "aws_ec2_instance_type_offerings", "app")
    assert re.search(r'^\s+location_type\s*=\s*"availability-zone"$', offerings, re.M)
    asked = re.search(
        r'filter \{\s+name\s*=\s*"instance-type"\s+values\s*=\s*\[(.+?)\]\s+\}', offerings
    )
    assert asked, "the offerings are not asked for one instance type"
    # The type asked about is the type launched: one expression, written once.
    assert asked[1] == "local.instance_type"
    instance = block("resource", "aws_instance", "app")
    assert re.search(r"^\s+instance_type\s*=\s*local\.instance_type$", instance, re.M)

    subnet = block("resource", "aws_subnet", "app")
    assert re.search(r"^\s+availability_zone\s*=\s*local\.zones\[0\]$", subnet, re.M), (
        "the subnet names no zone, so EC2 selects one, whether or not it offers the type"
    )


def test_a_later_change_in_the_offerings_cannot_move_the_subnet():
    """The zone is forced-new on a subnet, and the instance lives in the subnet: a zone that
    is offered later and sorts first must not replace both on a routine apply."""
    lifecycle = block("resource", "aws_subnet", "app").split("lifecycle {")[1]
    assert re.search(r"^\s+ignore_changes\s*=\s*\[availability_zone\]$", lifecycle, re.M)


@pytest.mark.parametrize(
    ("offered", "chosen"),
    [
        (["us-east-1c", "us-east-1a", "us-east-1b"], ["us-east-1a", "us-east-1b", "us-east-1c"]),
        # An account that has opted in to a Local Zone is told about it in the same listing
        # (the provider's `aws_availability_zones`: "by default the API and this data source
        # include both Local Zones and Availability Zones"), and its name sorts first.
        (["us-east-1-bos-1a", "us-east-1f", "us-east-1d"], ["us-east-1d", "us-east-1f"]),
        (["us-east-1-bos-1a"], []),
    ],
)
def test_only_the_regions_own_zones_are_candidates_in_a_stable_order(tmp_path, offered, chosen):
    tofu = shutil.which("tofu")
    if tofu is None:
        pytest.skip("OpenTofu is needed to evaluate the zone expression")
    main = (AWS / "main.tf").read_text()
    found = re.search(r"^  zones = (sort\(\[.*?^  \]\))$", main, re.M | re.S)
    assert found, "the candidate zones are not one sorted expression in the locals"
    listing = "data.aws_ec2_instance_type_offerings.app.locations"
    assert listing in found[1]
    expression = found[1].replace(listing, json.dumps(offered))
    (tmp_path / "zones.tf").write_text(
        'variable "region" {\n  default = "us-east-1"\n}\n\n'
        f"locals {{\n  zones = {expression}\n}}\n"
    )
    done = subprocess.run(
        [tofu, "console", "-no-color"],
        input="jsonencode(local.zones)\n",
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
    )
    assert done.returncode == 0, done.stdout
    assert json.loads(json.loads(done.stdout)) == chosen


def test_the_guide_says_where_the_subnet_goes():
    section = (QUICK / "README.md").read_text().split("## AWS\n")[1].split("\n## ")[0]
    assert "the first Availability Zone that offers" in " ".join(section.split())
