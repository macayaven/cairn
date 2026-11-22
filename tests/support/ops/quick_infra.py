"""Shared test helpers extracted from test_quick_infra_unit."""

import base64
import copy
import gzip
import json
import os
import shutil
import subprocess
import tempfile
import tomllib

import pytest
import yaml

from support.paths import REPO_ROOT

REPO = REPO_ROOT

QUICK = REPO / "infra" / "quick"

VERSION = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]

_CLOUD_INIT_CACHE: dict[tuple[str, tuple[str, ...]], dict] = {}


def require_tofu(reason: str) -> str:
    tofu = shutil.which("tofu")
    if tofu is None:
        if os.environ.get("CAIRN_REQUIRE_TOFU") == "1":
            pytest.fail(f"{reason} (CAIRN_REQUIRE_TOFU=1)")
        pytest.skip(reason)
    return tofu


def render_cloud_init(tmp_path, cloud, *, serve_extra_args=()):
    key = (cloud, tuple(serve_extra_args))
    cached = _CLOUD_INIT_CACHE.get(key)
    if cached is not None:
        return copy.deepcopy(cached)
    template = QUICK / "cloud-init.yaml.tftpl"
    assert template.is_file(), "the shared cloud-init template has not been written"
    tofu = require_tofu("OpenTofu is needed to render the cloud-init template")
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
    with tempfile.TemporaryDirectory() as work_dir:
        done = subprocess.run(
            [tofu, "console", "-no-color"],
            input=f"{codec}(templatefile({json.dumps(str(template))}, {arguments}))\n",
            cwd=work_dir,
            text=True,
            capture_output=True,
            timeout=20,
        )
    assert done.returncode == 0, done.stderr
    payload = base64.b64decode(json.loads(done.stdout))
    if cloud == "aws":
        assert len(payload) <= 16384, "EC2 allows at most 16 KiB of raw user data"
        payload = gzip.decompress(payload)
    rendered = yaml.safe_load(payload)
    _CLOUD_INIT_CACHE[key] = copy.deepcopy(rendered)
    return rendered
