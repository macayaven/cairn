"""Check the AWS quick deployment against what a first apply to a real account meets."""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile

import pytest
import yaml
from test_quick_infra_unit import QUICK, REPO

from cairn.web.brand import make_brand
from cairn.web.cli import _parser, _plans
from cairn.web.public import read_operator_notes

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


def test_the_upgrade_guide_names_the_files_a_replaced_vm_does_not_get_back(tmp_path):
    """Litestream replicates one file, the database. The deploy guide has a person write a
    plans file, a logo and operator notes onto the same volume, and `serve` does not start
    while one of its options names a file it cannot read. A replaced VM has a new volume, so
    after any replacement the stack stays down until those files are written again, and the
    guide to the quick deploy has to say so where it describes a replacement."""
    replicated = yaml.safe_load((REPO / "deploy" / "litestream.yml").read_text())["dbs"]
    assert [database["path"] for database in replicated] == ["/data/cairn.db"]
    missing = str(tmp_path / "not-written-again")
    serve = ["serve", "--db", "/data/cairn.db", "--sign-in", "google", "--brand-name", "Example"]
    args = _parser().parse_args(
        [*serve, "--plans", missing, "--brand-logo", missing, "--operator-notes", missing]
    )
    refusals = (
        lambda: _plans(args.plans),
        lambda: make_brand(args.brand_name, args.brand_logo, None),
        lambda: read_operator_notes(args.operator_notes),
    )
    for refuse in refusals:
        with pytest.raises(ValueError, match="read"):
            refuse()
    upgrade = " ".join(
        (QUICK / "README.md").read_text().split("## Upgrade\n")[1].split("\n## ")[0].split()
    )
    assert "The replica holds the database and nothing else" in upgrade
    for option in ("--plans", "--brand-logo", "--operator-notes"):
        assert f"`{option}`" in upgrade, option
    assert "`serve` does not start" in upgrade
    assert "(../../deploy/README.md#restore-by-hand)" in upgrade
    headings = (REPO / "deploy" / "README.md").read_text()
    assert "\n### Restore by hand\n" in headings


KEPT = "What is deleted or overwritten in the bucket stays restorable for"
# On AWS the days are a floor: S3 makes a version eligible at the midnight UTC after them and
# "doesn't guarantee a completion time" for removing it.
KEPT_ON_AWS = (
    f"{KEPT} at least 7 days: it becomes an earlier version of the object. From the midnight"
    " UTC after those days a lifecycle rule makes that version eligible for removal, and S3"
    " removes it some time after that, with no deadline it states."
)
KEPT_ON_GOOGLE = f"{KEPT} 7 days: that is the soft delete policy"


def readme_section(title, level="##"):
    text = (QUICK / "README.md").read_text()
    found = re.search(rf"^{level} {re.escape(title)}\n(.*?)(?=^{level} |\Z)", text, re.M | re.S)
    assert found, f"the guide has no section {title!r}"
    return found[1]


def role_policy(tmp_path):
    tofu = shutil.which("tofu")
    if tofu is None:
        pytest.skip("OpenTofu is needed to evaluate the policy expression")
    main = (AWS / "main.tf").read_text()
    expression = re.search(r"^  policy = (jsonencode\(\{.*?^  \}\))$", main, re.M | re.S)[1]
    expression = expression.replace(
        "aws_s3_bucket.replica.arn", json.dumps("arn:aws:s3:::example-replica")
    ).replace(
        "aws_secretsmanager_secret.settings.arn", json.dumps("arn:aws:secretsmanager:example")
    )
    (tmp_path / "policy.tf").write_text(f"locals {{\n  policy = {expression}\n}}\n")
    done = subprocess.run(
        [tofu, "console", "-no-color"],
        input="local.policy\n",
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
    )
    assert done.returncode == 0, done.stdout
    return json.loads(json.loads(done.stdout))["Statement"]


def test_the_bucket_keeps_what_is_deleted_or_overwritten_for_seven_days():
    """Litestream deletes the files it no longer needs, and on a bucket without versions a
    delete is final: so is one made by a mistyped command or from a VM someone else got
    into. With versions, a delete leaves the bytes as a noncurrent version (Amazon S3,
    "Deleting object versions from a versioning-enabled bucket"), and the rule makes them
    eligible for removal at least seven days later, then the delete marker left alone, and
    any upload that never ended."""
    versioning = block("resource", "aws_s3_bucket_versioning", "replica")
    assert re.search(r'^\s+status\s*=\s*"Enabled"$', versioning, re.M)
    lifecycle = block("resource", "aws_s3_bucket_lifecycle_configuration", "replica")
    assert lifecycle.count("rule {") == 1
    # The whole bucket, and the rule is on.
    assert re.search(r"^\s+filter \{\}$", lifecycle, re.M)
    assert re.search(r'^\s+status\s*=\s*"Enabled"$', lifecycle, re.M)
    noncurrent = re.search(r"noncurrent_version_expiration \{(.*?)\}", lifecycle, re.S)[1]
    assert noncurrent.split() == ["noncurrent_days", "=", "7"]
    # No lifetime for a current object: the rule never removes what nobody deleted.
    expiration = re.search(r"^\s+expiration \{(.*?)\}", lifecycle, re.M | re.S)[1]
    assert expiration.split() == ["expired_object_delete_marker", "=", "true"]
    unfinished = re.search(r"abort_incomplete_multipart_upload \{(.*?)\}", lifecycle, re.S)[1]
    assert unfinished.split() == ["days_after_initiation", "=", "7"]
    # A rule on a bucket without versions would keep nothing: versions first, and they before
    # the VM that writes. The VM does not wait for the rule, which acts a week after a delete:
    # the provider says such a configuration "may take some time to fully propagate", and a
    # slow one must not hold up or fail the VM.
    assert "depends_on = [aws_s3_bucket_versioning.replica]" in lifecycle
    waits = block("resource", "aws_instance", "app").split("depends_on = [")[1].split("]")[0]
    assert "aws_s3_bucket_versioning.replica," in waits
    assert "aws_s3_bucket_lifecycle_configuration" not in waits


def test_both_clouds_keep_deletions_as_long_and_the_guide_says_so_in_the_same_words():
    gcp = (QUICK / "gcp" / "main.tf").read_text()
    seconds = int(re.search(r"^\s+retention_duration_seconds\s*=\s*(\d+)$", gcp, re.M)[1])
    lifecycle = block("resource", "aws_s3_bucket_lifecycle_configuration", "replica")
    days = int(re.search(r"^\s+noncurrent_days\s*=\s*(\d+)$", lifecycle, re.M)[1])
    assert seconds == days * 86_400 == 7 * 86_400
    assert KEPT_ON_AWS in " ".join(readme_section("AWS").split())
    assert KEPT_ON_GOOGLE in " ".join(readme_section("Google Cloud").split())
    # Nowhere does the AWS section give the days without "at least".
    aws = " ".join(readme_section("AWS").split())
    assert len(re.findall(r"\b7 days\b", aws)) == len(re.findall(r"at least 7 days\b", aws)) == 3
    comment = (
        (AWS / "main.tf").read_text().split('resource "aws_s3_bucket_lifecycle_configuration"')[0]
    )
    assert "at least seven days" in comment.rsplit("\n\n", 1)[1]
    assert "no deadline" in comment.rsplit("\n\n", 1)[1]


def test_the_vm_can_delete_a_backup_file_but_not_its_kept_version(tmp_path):
    """What the seven days are worth against the VM itself: its role may delete an object
    (Litestream must), which with versions only hides it. Removing a version, or switching
    off what keeps versions, takes actions the role does not have."""
    actions = {
        action.lower()
        for statement in role_policy(tmp_path)
        if statement["Effect"] == "Allow"
        for action in statement["Action"]
    }
    assert "s3:deleteobject" in actions
    assert not any("*" in action for action in actions)
    assert not actions & {
        "s3:deleteobjectversion",
        "s3:putbucketversioning",
        "s3:putlifecycleconfiguration",
        "s3:deletebucket",
    }


def test_destroy_keeps_a_bucket_that_holds_backups_until_a_person_says_otherwise():
    """With versions, emptying a bucket by its current objects leaves the versions, and S3
    deletes no bucket that holds any. The provider empties one itself, versions and delete
    markers included, when `force_destroy` is true in the state (its `emptyBucket`), and
    the state takes the value at an apply. So: false unless a person says so, and the
    guide's teardown is an apply of that one resource, then the destroy."""
    bucket = block("resource", "aws_s3_bucket", "replica")
    assert re.search(r"^\s+force_destroy\s*=\s*var\.delete_backups$", bucket, re.M)
    variables = (AWS / "variables.tf").read_text()
    declared = re.search(r'^variable "delete_backups" \{(.*?)^}', variables, re.M | re.S)
    assert declared, "the variable that lets a destroy delete the backups is not declared"
    assert re.search(r"^\s+type\s*=\s*bool$", declared[1], re.M)
    assert re.search(r"^\s+default\s*=\s*false$", declared[1], re.M)

    teardown = readme_section("AWS teardown", "###")
    said = " ".join(teardown.split())
    assert "The destroy operation refuses to delete a bucket" in said
    commands = [
        line.strip()
        for fence in re.findall(r"```sh\n(.*?)```", teardown, re.S)
        for line in fence.splitlines()
        if line.startswith("tofu ")
    ]
    assert commands == [
        "tofu destroy",
        "tofu apply -target=aws_s3_bucket.replica -var delete_backups=true",
        "tofu destroy -var delete_backups=true",
    ]
    # Removing the current objects no longer empties the bucket, so the guide no longer says to.
    assert "aws s3 rm" not in teardown
    assert "every version" in said


def test_the_recovery_guide_says_where_a_failed_read_or_start_explains_itself():
    """The boot script puts a tool's own error in the unit's journal under one of two labels,
    and the guide's recovery section, which sends a person to that journal, names them."""
    script = (QUICK / "bootstrap.sh").read_text()
    recovery = " ".join(readme_section("First-boot recovery").split())
    for label in ("settings read", "compose"):
        assert re.search(rf"say_errors '?{label}'? ", script), label
        assert f"`cairn-quick: {label}:`" in recovery, label
    assert 'print(f"cairn-quick: {label}: {line}")' in script
    assert "They never hold a setting's value" in recovery


def test_the_vm_may_add_a_cold_copy_and_nothing_else_under_its_prefix(tmp_path):
    """A cold copy leaves the VM through the bucket, under `cold/`. The role gets the one
    action that takes: it can add an object there, and cannot read, list by version or
    delete one. Litestream's statement stays on `cairn/`, so neither reaches the other."""
    statements = {statement["Sid"]: statement for statement in role_policy(tmp_path)}
    bucket = "arn:aws:s3:::example-replica"
    assert statements["ColdCopies"] == {
        "Sid": "ColdCopies",
        "Effect": "Allow",
        "Action": ["s3:PutObject"],
        "Resource": f"{bucket}/cold/*",
    }
    assert statements["ReplicaObjects"]["Resource"] == f"{bucket}/cairn/*"
    reaching_cold = [
        sid
        for sid, statement in statements.items()
        if any(
            resource in (f"{bucket}/cold/*", f"{bucket}/*", "*")
            for resource in [statement["Resource"]]
        )
    ]
    assert reaching_cold == ["ColdCopies"]


STANDINS = {
    "docker": r"""
if args == ["compose", "stop"]:
    (state / "stopped").touch()
elif args[:2] == ["volume", "inspect"]:
    assert args[2:] == ["cairn_cairn-data", "-f", "{{.Mountpoint}}"]
    print(state / "volume")
else:
    sys.exit(2)
""",
    "aws": r"""
assert args[:2] == ["s3", "cp"] and args[4:] == ["--region", "us-east-1"], args
source, target = args[2:4]
if target.startswith("s3://"):  # from the VM, with its role: only under cold/
    assert target.startswith("s3://example-replica/cold/"), target
    target = state / "bucket" / target.removeprefix("s3://example-replica/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(Path(source).read_bytes())
else:  # to a person's machine, with their own credentials
    assert source.startswith("s3://example-replica/cold/"), source
    found = state / "bucket" / source.removeprefix("s3://example-replica/")
    (Path(target) / found.name).write_bytes(found.read_bytes())
""",
    "tofu": r"""
assert args[:2] == ["output", "-raw"]
sys.stdout.write({"backup_bucket": "example-replica", "region": "us-east-1"}[args[2]])
""",
    "systemctl": r"""
assert args == ["restart", "cairn-quick.service"]
assert not (state / "stopped").exists() or (state / "bucket" / "cold").is_dir()
(state / "restarted").touch()
""",
    "sha256sum": r"""
import hashlib
for name in args:
    print(hashlib.sha256(Path(name).read_bytes()).hexdigest(), "", name)
""",
}
STANDINS["shasum"] = STANDINS["sha256sum"].replace("for name in args:", "for name in args[2:]:")


def test_the_cold_copy_commands_put_the_stopped_database_under_cold_and_start_again(tmp_path):
    """The guide's own commands for a cold copy on AWS, run as written against stand-ins:
    the stack is stopped before the volume is packed, the archive goes under `cold/` and
    nowhere else, what a person fetches has the size and digest the VM printed, and the
    stack is started after."""
    section = readme_section("AWS cold copy", "###")
    on_vm, on_laptop, back_on_vm = re.findall(r"```sh\n(.*?)```", section, re.S)
    # On the VM the CLI is a snap, and the boot script itself adds /snap/bin to its PATH:
    # the guide names the path, so that the command cannot fail with the stack stopped.
    assert "\n/snap/bin/aws s3 cp " in on_vm and "\naws " not in on_vm
    state = tmp_path / "state"
    (state / "volume" / ".cairn.db-litestream").mkdir(parents=True)
    (state / "volume" / ".cairn.db-litestream" / "position").write_text("of the old replica")
    (state / "volume" / "cairn.db").write_bytes(b"synthetic database\n" * 64)
    (state / "volume" / "cairn.db-wal").write_bytes(b"synthetic log\n")
    (state / "bucket" / "cairn" / "ltx" / "0").mkdir(parents=True)
    (state / "bucket" / "cairn" / "ltx" / "0" / "0001.ltx").write_bytes(b"replica")
    for folder in ("bin", "opt/deploy", "root", "laptop"):
        (tmp_path / folder).mkdir(parents=True)
    (tmp_path / "opt" / "quick.env").write_text(
        "LITESTREAM_REPLICA_URL=s3://example-replica/cairn\nAWS_REGION=us-east-1\n"
    )
    for name, body in STANDINS.items():
        stub = tmp_path / "bin" / name
        stub.write_text(
            f"#!{sys.executable}\nimport os, sys\nfrom pathlib import Path\n"
            f"args = sys.argv[1:]\nstate = Path({str(state)!r})\n"
            f"with (state / 'calls').open('a') as log:\n"
            f"    log.write({name!r} + ' ' + ' '.join(args) + '\\n')\n"
            f"{body}"
        )
        stub.chmod(0o755)
    env = {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "HOME": str(tmp_path)}

    def run(commands, cwd):
        script = (
            commands.replace("/opt/cairn", str(tmp_path / "opt"))
            .replace("/root", str(tmp_path / "root"))
            .replace("/snap/bin/aws", str(tmp_path / "bin" / "aws"))
        )
        done = subprocess.run(
            ["/bin/bash", "-euc", script],
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        )
        assert done.returncode == 0, done.stdout
        return done.stdout

    said = run(on_vm, tmp_path)
    (archive,) = (state / "bucket" / "cold").iterdir()
    assert re.fullmatch(r"cairn-cold-\d{8}T\d{6}Z\.tgz", archive.name)
    digest, size = re.search(r"^([0-9a-f]{64}) ", said, re.M)[1], archive.stat().st_size
    assert f" {size} " in said
    with tarfile.open(fileobj=io.BytesIO(archive.read_bytes())) as packed:
        names = {os.path.normpath(name) for name in packed.getnames()}
    assert {"cairn.db", "cairn.db-wal"} <= names
    assert not any(".cairn.db-litestream" in name for name in names)
    # Litestream's prefix is as it was, and nothing was started yet.
    assert [p.name for p in (state / "bucket" / "cairn" / "ltx" / "0").iterdir()] == ["0001.ltx"]
    assert not (state / "restarted").exists()
    calls = (state / "calls").read_text().splitlines()
    assert calls[0] == "docker compose stop" and calls[1].startswith("docker volume inspect")

    placeholder = re.search(r"cairn-cold-\d{8}T\d{6}Z\.tgz", on_laptop)[0]
    fetched = run(on_laptop.replace(placeholder, archive.name), tmp_path / "laptop")
    assert digest in fetched and f" {size} " in fetched

    run(back_on_vm.replace(placeholder, archive.name), tmp_path)
    assert (state / "restarted").exists()
    assert not list((tmp_path / "root").iterdir())


def test_the_upgrade_guide_sends_aws_to_its_cold_copy_and_keeps_the_other_way():
    upgrade = readme_section("Upgrade")
    assert "[cold copy of the database](../../docs/guides/run.md#back-it-up)" in upgrade
    assert "[AWS cold copy](#aws-cold-copy)" in upgrade
    cold = " ".join(readme_section("AWS cold copy", "###").split())
    # How the copies are kept apart from Litestream and from the rule that expires versions.
    assert "Litestream reads and writes only under `cairn/`" in cold
    assert "removes only what was deleted or overwritten" in cold


def test_what_was_tried_tells_the_laptop_run_from_an_apply():
    """The AWS files were run on a laptop against the real Compose. The guide says what that
    covered and that none of it was AWS, and still says the path was never applied."""
    tried = " ".join(readme_section("What was tried").split())
    assert "The AWS path has not been applied to a real account" in tried
    assert "run on a laptop" in tried
    assert "a stand-in for the AWS CLI" in tried
    assert "None of that touched AWS" in tried
    # The one real apply ran the shared boot script as it was that day.
    assert "The boot script has changed since that apply" in tried
    assert "has run only on the laptop" in tried
