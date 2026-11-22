"""The licence: every file under the MIT License, and its notice leaves the repository with
the software, in the package's metadata, in the image's build inputs and at a plain path in
the image. Nothing here builds or pulls."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import tomllib

import pytest

from support.paths import REPO_ROOT

REPO = REPO_ROOT

MIT = """\
MIT License

Copyright (c) 2026 Carlos Crespo Macaya

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def test_the_licence_is_the_mit_license_and_nothing_else():
    assert (REPO / "LICENSE").read_text(encoding="utf-8") == MIT


def test_there_is_no_second_licence():
    assert not (REPO / "ee").exists()
    assert not (REPO / "src" / "cairn" / "ee").exists()
    others = [
        p.relative_to(REPO).as_posix()
        for p in REPO.glob("*")
        if re.match(r"(?i)(licen[cs]e|copying|notice)", p.name) and p.name != "LICENSE"
    ]
    assert others == []


def project() -> dict:
    return tomllib.loads((REPO / "pyproject.toml").read_text())["project"]


def test_the_package_says_mit_by_spdx_expression_and_ships_the_file():
    table = project()
    assert table["license"] == "MIT"  # an SPDX expression, not the old {file = ...} table
    assert table["license-files"] == ["LICENSE"]


def test_the_allow_list_lets_in_the_licence_file():
    lines = (REPO / ".dockerignore").read_text().splitlines()
    assert [line for line in lines if line.startswith("!")] == [
        "!pyproject.toml",
        "!uv.lock",
        "!README.md",
        "!LICENSE",
        "!src/",
    ]


def dockerfile_lines() -> list[str]:
    return (REPO / "Dockerfile").read_text().splitlines()


def test_the_build_stage_copies_the_licence_after_the_dependency_layer_and_before_the_install():
    lines = dockerfile_lines()
    syncs = [i for i, line in enumerate(lines) if "uv sync" in line]
    assert len(syncs) == 2 and "--no-install-project" in lines[syncs[0]]
    dependencies_only, install = syncs
    at = lines.index("COPY LICENSE ./")
    assert dependencies_only < at < install


def test_the_image_keeps_a_copy_at_a_plain_path_beside_the_application():
    assert "COPY --from=build /src/LICENSE /app/LICENSE" in dockerfile_lines()


# --- the smoke check ---------------------------------------------------------------------


def smoke_script():
    path = REPO / "scripts" / "smoke_container.py"
    spec = importlib.util.spec_from_file_location("smoke_container", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(name: str) -> str:
    return hashlib.sha256((REPO / name).read_bytes()).hexdigest()


REAL = "the repository's digest"  # stands for sha("LICENSE"), filled in when a test runs


def report(**changes) -> str:
    found = {
        "expression": "MIT",
        "metadata": {"LICENSE": sha("LICENSE")},
        "plain": {"LICENSE": sha("LICENSE")},
    }
    found.update(changes)
    return json.dumps(found)


def run_check(smoke, monkeypatch, out: str) -> str:
    calls = []

    def docker(*args, check=True):
        calls.append(args)
        return out

    monkeypatch.setattr(smoke, "docker", docker)
    stand = type("Stand", (), {"names": ["cairn-smoke-1-1"]})()
    detail = smoke.Smoke.carries_the_licence(stand)
    assert calls[0][:2] == ("exec", "cairn-smoke-1-1")
    return detail


def test_the_smoke_check_is_listed_and_passes_on_matching_bytes(monkeypatch):
    smoke = smoke_script()
    assert "carries_the_licence" in smoke.main.__code__.co_names
    assert "MIT" in run_check(smoke, monkeypatch, report())


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"expression": None}, "MIT"),
        ({"expression": "MIT OR Apache-2.0"}, "MIT"),
        ({"metadata": {}}, "LICENSE is missing from the package's metadata"),
        ({"metadata": {"LICENSE": "0" * 64}}, "LICENSE in the package's metadata"),
        (
            {"metadata": {"LICENSE": REAL, "ee/LICENSE": "0" * 64}},
            "the package's metadata holds another licence file: ee/LICENSE",
        ),
        ({"plain": {}}, "LICENSE is missing from /app/LICENSE"),
        ({"plain": {"LICENSE": "0" * 64}}, "LICENSE in /app/LICENSE"),
    ],
)
def test_the_smoke_check_sees_a_missing_changed_or_extra_notice(monkeypatch, change, match):
    smoke = smoke_script()
    change = json.loads(json.dumps(change).replace(REAL, sha("LICENSE")))
    with pytest.raises(smoke.Failed, match=re.escape(match)):
        run_check(smoke, monkeypatch, report(**change))
