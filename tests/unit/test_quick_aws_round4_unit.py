"""Check the AWS quick deployment and the shared upgrade instructions offline."""

import json
import re
import shlex
import shutil
import subprocess
from fnmatch import fnmatchcase

import pytest
import yaml
from markdown_it import MarkdownIt
from test_quick_infra_unit import QUICK, REPO

AWS = QUICK / "aws"
README = QUICK / "README.md"


@pytest.mark.parametrize(
    "archive_digest",
    [
        pytest.param(
            "156fe7164a3d26ef6b35734c43e99fb198df90575ed897d1182b8e930b8cd523", id="linux_amd64"
        ),
        pytest.param(
            "2545a8478bd551fdc9694f6cc1a1ad24617f6736f8bde0ad6cae90987c65380f", id="linux_arm64"
        ),
        pytest.param(
            "7ae460049b75bd4aefee465ef7c53a01ac2df46d4d3e3ac00824afa8b5cb83fb", id="darwin_amd64"
        ),
        pytest.param(
            "b03ff5efdee310502aaaeb460144dc059bce72a0d8217e6b989099ef8aef9283", id="darwin_arm64"
        ),
        pytest.param(
            "4070db1ee369ccb41cb610bfd887386bc0a9b9ecad60aeb4dbce58443d2519dd", id="windows_amd64"
        ),
    ],
)
def test_aws_lock_includes_the_published_archive_digests(archive_digest):
    text = (AWS / ".terraform.lock.hcl").read_text()
    assert f'"zh:{archive_digest}"' in text


def test_abort_multipart_upload_is_limited_to_the_replica_prefix(tmp_path):
    tofu = shutil.which("tofu")
    if tofu is None:
        pytest.skip("OpenTofu is needed to evaluate the policy expression")
    main = (AWS / "main.tf").read_text()
    expression = re.search(r"^  policy = (jsonencode\(\{.*?^  \}\))$", main, re.M | re.S)[1]
    bucket = "arn:aws:s3:::example-replica"
    expression = expression.replace("aws_s3_bucket.replica.arn", json.dumps(bucket)).replace(
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
    policy = json.loads(json.loads(done.stdout))
    resources = [
        rule["Resource"]
        for rule in policy["Statement"]
        if rule["Effect"] == "Allow"
        and any(fnmatchcase("s3:AbortMultipartUpload", action) for action in rule["Action"])
    ]
    assert resources == [bucket + "/cairn/*"]


def section(title):
    tokens = MarkdownIt().parse(README.read_text())
    start = next(
        index + 3
        for index, token in enumerate(tokens)
        if token.type == "heading_open" and token.tag == "h2" and tokens[index + 1].content == title
    )
    end = next(
        (index for index in range(start, len(tokens)) if tokens[index].tag == "h2"), len(tokens)
    )
    return [child for token in tokens[start:end] for child in token.children or []]


def test_upgrade_links_to_the_published_backup_guide():
    link = next(token.attrGet("href") for token in section("Upgrade") if token.type == "link_open")
    target, fragment = link.split("#")
    assert (README.parent / target).resolve() == REPO / "docs/guides/run.md"
    headings = MarkdownIt().parse((README.parent / target).read_text())
    assert any(
        token.type == "heading_open"
        and headings[index + 1].content.lower().replace(" ", "-") == fragment
        for index, token in enumerate(headings)
    )


def test_upgrade_and_sign_in_name_the_certificate_storage_and_issuance_limit():
    compose = yaml.safe_load((REPO / "deploy/compose.yaml").read_text())
    assert "caddy-data:/data" in compose["services"]["caddy"]["volumes"]
    assert compose["volumes"]["caddy-data"] is None
    upgrade = section("Upgrade")
    assert any(token.type == "code_inline" and token.content == "caddy-data" for token in upgrade)
    assert any(
        token.attrGet("href")
        == "https://letsencrypt.org/docs/rate-limits/#new-certificates-per-exact-set-of-identifiers"
        for token in upgrade
        if token.type == "link_open"
    )
    assert any(token.attrGet("href") == "#upgrade" for token in section("Provider sign-in"))


def test_aws_documents_deliberate_ami_replacement():
    commands = [
        shlex.split(token.content)
        for token in section("AWS")
        if token.type == "code_inline" and token.content.startswith("tofu apply -replace=")
    ]
    assert commands == [["tofu", "apply", "-replace=aws_instance.app"]]
