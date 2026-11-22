"""Consolidated deterministic tests for job `release`'s `Create the release` and CLI checks.

Consolidates all deterministic scenarios previously spread across:
- test_ci_release_gh_version_unit.py (CLI >= 2.102.0 validation before any write/fetch)
- test_ci_release_retry_unit.py (draft create, idempotent retry, zero-delete, pagination)
- test_ci_release_tag_unit.py (tag commit verification at start, pre-publish, post-publish)
- test_ci_release_notes_title_unit.py (byte-for-byte notes & title checks)
- test_ci_release_assets_unit.py (asset-ID fetch, size/state/bytes/replacement checks)
- test_ci_release_fresh_read_unit.py (fresh read by ID vs stale listing)
- test_ci_release_listing_lag_unit.py (bounded lag waits LOOKS=10, APART=3)
- test_ci_release_latest_unit.py (`make_latest` semver ordering & post-publish race)
- test_ci_release_lines_unit.py (diagnostic accuracy & README stop table contracts)
- test_reviewer{4,5,6,7}_release_*_unit.py (boundary mutations, lost ack, subprocess E2E)
"""

from __future__ import annotations

import ast
import json
import random
import re
from pathlib import Path

import pytest

from support.ci.ci_workflow import ERROR, EXPANSION, JOBS, RAW_WORKFLOW, REPO, stops
from support.ci_shell import SYSTEM, run_step
from support.github_release_hub import (
    DEFAULT_COMMIT,
    DEFAULT_MARK,
    DEFAULT_RUN_ID,
    OTHER_COMMIT,
    ReleaseHub,
    extract_release_script,
    make_release_record,
    run_release_in_process,
    run_release_subprocess_e2e,
    strip_asset_bytes,
)

pytestmark = pytest.mark.ci_meta

RELEASE = JOBS["release"]
GH_CHECK = next(s for s in RELEASE["steps"] if s.get("name") == "Check the GitHub CLI")
PY_CHECK = next(s for s in RELEASE["steps"] if s.get("name") == "Check Python 3")
CREATE = next(s for s in RELEASE["steps"] if s.get("name") == "Create the release")
ALIASES_STEP = next(s for s in JOBS["aliases"]["steps"] if "gh release list" in s.get("run", ""))
README = (REPO / "README.md").read_text(encoding="utf-8")

WHEEL = "cairn_memory-1.2.3-py3-none-any.whl"
SOURCE = "cairn_memory-1.2.3.tar.gz"
SUMS = "SHA256SUMS"
BOTH = [("v1.2.3", ""), ("v1.2.3-rc.1", "true")]
WROTE = f"the notes\n\n{DEFAULT_MARK}\n"
EDITED = f"other notes\n\n{DEFAULT_MARK}\n"

SHIM_GH_VERSION = """#!/bin/sh
printf '%s\\n' "$*" >> "$CALLS"
[ "$1" = --version ] || exit 2
printf '%s\\n' "$GH_VERSION"
[ -z "$GH_VERSION" ] || echo "https://github.com/cli/cli/releases/tag/v0"
"""


def cells(key: str) -> tuple[str, str]:
    assert key in stops(), key
    (row,) = [line for line in README.splitlines() if line.startswith(f"| `{key}` |")]
    _, means, next_step = (cell.strip() for cell in row.strip("|").split(" | "))
    return means, next_step


def other_release(tag: str, rid: int, *, draft: bool = False, prerelease: bool = False) -> dict:
    return make_release_record(
        tag,
        prerelease,
        rid=rid,
        draft=draft,
        body=f"the notes of {tag}\n",
        asset_files={},
    )


def calls_joined(hub: ReleaseHub) -> list[str]:
    return ["gh " + " ".join(c) for c in hub.calls]


def writes_joined(hub: ReleaseHub) -> list[str]:
    return [" ".join(w) for w in hub.writes]


# --- 1. Inline script properties & GitHub CLI version gate -----------------------------------


def test_inline_python_release_step_is_stdlib_isolated_and_zero_delete():
    assert CREATE["run"].startswith("python3 -I - <<'PY'\n")
    script = extract_release_script()
    tree = ast.parse(script)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    import sys

    assert imports <= sys.stdlib_module_names
    assert "DELETE" not in script and "release delete" not in script and "--clobber" not in script
    assert "LOOKS = 10" in script and "APART = 3" in script
    comment_flat = " ".join(script.replace("#", "").split())
    assert "cli/cli at v2.102.0" in comment_flat
    assert '`if opts.Target != "" {`, `params["target_commitish"] = opts.Target`' in comment_flat
    assert (
        "Unused if the Git tag already exists. Default: the repository's default branch."
        in comment_flat
    )
    assert "`GET /repos/{owner}/{repo}/commits/{ref}`" in comment_flat


def _run_gh_version_check(tmp_path: Path, **env: str):
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    if "GH_VERSION" in env:
        (bin_ / "gh").write_text(SHIM_GH_VERSION, encoding="utf-8")
        (bin_ / "gh").chmod(0o755)
    calls = tmp_path / "calls"
    calls.write_text("", encoding="utf-8")
    done = run_step(
        GH_CHECK["run"],
        cwd=tmp_path,
        env={
            "PATH": f"{bin_}:{SYSTEM}",
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_REF_NAME": "v1.2.3",
            "GITHUB_RUN_ID": DEFAULT_RUN_ID,
            "GH_REPO": "owner/repo",
            "CALLS": str(calls),
            **env,
        },
    )
    errors = [line for line in done.stdout.splitlines() if line.startswith("::error::")]
    return done, calls.read_text().splitlines(), errors


@pytest.mark.parametrize("version", ["2.102.0", "2.102.1", "2.105.0", "3.0.0", "10.0.0"])
@pytest.mark.parametrize("built", [" (2026-11-01)", ""])
def test_gh_at_or_after_2_102_0_passes(tmp_path, version, built):
    done, calls, errors = _run_gh_version_check(tmp_path, GH_VERSION=f"gh version {version}{built}")
    assert (done.returncode, errors) == (0, []), done.stderr
    assert calls == ["--version"]


@pytest.mark.parametrize(
    ("printed", "said"),
    [
        ("gh version 2.101.0 (2026-09-15)", "2.101.0"),
        ("gh version 2.51.3 (2024-06-01)", "2.51.3"),
        (
            "gh version 2.102.0-rc.1 (2026-10-01)",
            "of a version it does not print as three plain numbers",
        ),
        (
            "gh version 2.102.0+local (2026-10-01)",
            "of a version it does not print as three plain numbers",
        ),
        ("gh version (devel)", "of a version it does not print as three plain numbers"),
        ("", "of a version it does not print as three plain numbers"),
    ],
)
def test_gh_before_2_102_0_or_prerelease_is_refused(tmp_path, printed, said):
    done, calls, errors = _run_gh_version_check(tmp_path, GH_VERSION=printed)
    assert done.returncode == 1 and calls == ["--version"]
    assert errors == [
        f"::error::gh on this runner is {said}, before 2.102.0, the version in whose source"
        " this job's handling of a draft was read, so it is not trusted with the release;"
        " update GitHub CLI on the runner CAIRN_RELEASE_RUNNER names, or unset it to use GitHub's"
    ]


def test_missing_gh_on_release_runner_is_refused(tmp_path):
    done, calls, errors = _run_gh_version_check(tmp_path, PATH=str(tmp_path / "bin"))
    assert done.returncode == 1 and calls == []
    assert errors == [
        "::error::gh is not on this runner, so the GitHub release for v1.2.3 cannot be made;"
        " install GitHub CLI 2.102.0 or later on the runner CAIRN_RELEASE_RUNNER names, or unset"
        " it to use GitHub's"
    ]


SHIM_PY_VERSION = """#!/bin/sh
if [ -n "$PY_STDERR" ]; then
  printf '%s\\n' "$PY_VERSION" >&2
else
  printf '%s\\n' "$PY_VERSION"
fi
"""


def _run_py_version_check(tmp_path: Path, **env: str):
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    if "PY_VERSION" in env:
        (bin_ / "python3").write_text(SHIM_PY_VERSION, encoding="utf-8")
        (bin_ / "python3").chmod(0o755)
    done = run_step(
        PY_CHECK["run"],
        cwd=tmp_path,
        env={
            "PATH": f"{bin_}:{SYSTEM}",
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_REF_NAME": "v1.2.3",
            "GITHUB_RUN_ID": DEFAULT_RUN_ID,
            **env,
        },
    )
    errors = [line for line in done.stdout.splitlines() if line.startswith("::error::")]
    return done, errors


@pytest.mark.parametrize("version", ["3.9.0", "3.10.12", "3.12.7", "3.13.2", "4.0.0"])
@pytest.mark.parametrize("extra", ["", " (main, Nov 2026)"])
def test_python3_at_or_after_3_9_passes(tmp_path, version, extra):
    done, errors = _run_py_version_check(tmp_path, PY_VERSION=f"Python {version}{extra}")
    assert (done.returncode, errors) == (0, []), done.stderr


@pytest.mark.parametrize(
    ("printed", "said", "on_stderr"),
    [
        ("Python 3.8.18", "3.8.18", False),
        ("Python 2.7.18", "2.7.18", True),
        ("Python 3.9.0rc1", "of a version it does not print as three plain numbers", False),
        ("Python (devel)", "of a version it does not print as three plain numbers", False),
        ("", "of a version it does not print as three plain numbers", False),
    ],
)
def test_python3_before_3_9_or_prerelease_is_refused(tmp_path, printed, said, on_stderr):
    done, errors = _run_py_version_check(
        tmp_path, PY_VERSION=printed, **({"PY_STDERR": "1"} if on_stderr else {})
    )
    assert done.returncode == 1
    assert errors == [
        f"::error::Python 3 on this runner is {said}, before 3.9, so the GitHub release for"
        " v1.2.3 cannot be made; update Python 3 on the runner CAIRN_RELEASE_RUNNER names, or"
        " unset it to use GitHub's"
    ]


def test_missing_python3_on_release_runner_is_refused(tmp_path):
    done, errors = _run_py_version_check(tmp_path, PATH=str(tmp_path / "bin"))
    assert done.returncode == 1
    assert errors == [
        "::error::Python 3 is not on this runner, so the GitHub release for v1.2.3 cannot be"
        " made; install Python 3.9 or later on the runner CAIRN_RELEASE_RUNNER names, or unset"
        " it to use GitHub's"
    ]


# --- 2. Core create -> verify -> PATCH publish -> verify + idempotent re-run ------------------


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
def test_fresh_run_creates_draft_verifies_by_id_publishes_and_verifies_again(
    tmp_path, tag, candidate
):
    hub = ReleaseHub(tag=tag)
    res = run_release_in_process(hub, tmp_path, tag=tag, candidate=candidate)
    assert res.returncode == 0, res.stdout + res.stderr
    assert f"The GitHub release {tag} is this run's and is published" in res.stdout
    assert len(hub.releases) == 1
    rel = hub.releases[0]
    assert rel["draft"] is False and rel["prerelease"] is (candidate == "true")
    assert hub.deletes == []
    assert len(hub.writes) == 2
    assert hub.writes[0][:3] == ["release", "create", tag]
    assert "--draft" in hub.writes[0] and "--verify-tag" in hub.writes[0]
    assert hub.writes[0][hub.writes[0].index("--target") : hub.writes[0].index("--target") + 2] == [
        "--target",
        DEFAULT_COMMIT,
    ]
    expected_latest = "false" if candidate == "true" else "true"
    assert hub.writes[1] == [
        "api",
        "--method",
        "PATCH",
        "repos/owner/repo/releases/88",
        "--silent",
        "-F",
        "draft=false",
        "-f",
        f"make_latest={expected_latest}",
    ]
    # Re-run on the already-published release makes zero writes and succeeds cleanly.
    res2 = run_release_in_process(hub, tmp_path, tag=tag, candidate=candidate)
    assert res2.returncode == 0
    assert hub.writes == [] and hub.sleeps == []


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
def test_existing_matching_draft_is_verified_and_published(tmp_path, tag, candidate):
    existing = make_release_record(tag, candidate == "true", rid=77, draft=True)
    hub = ReleaseHub(tag=tag, releases=[existing])
    res = run_release_in_process(hub, tmp_path, tag=tag, candidate=candidate)
    assert res.returncode == 0, res.stdout + res.stderr
    assert hub.releases[0]["id"] == 77 and hub.releases[0]["draft"] is False
    assert len(hub.writes) == 1 and hub.writes[0][1] == "--method"


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
def test_listing_paginates_across_pages_and_ignores_other_tags(tmp_path, tag, candidate):
    other = other_release("v0.1.0", 50)
    other_draft = other_release("v0.2.0", 51, draft=True)
    ours = make_release_record(tag, candidate == "true", rid=77, draft=False)
    hub = ReleaseHub(tag=tag, releases=[other, other_draft, ours], paginate_multi_page=True)
    res = run_release_in_process(hub, tmp_path, tag=tag, candidate=candidate)
    assert res.returncode == 0, res.stdout + res.stderr
    assert hub.writes == []


def test_real_subprocess_bash_python3_isolated_e2e(tmp_path):
    """Verify the actual `bash -e` -> `python3 -I` step executes cleanly end-to-end."""
    done, state = run_release_subprocess_e2e(tmp_path / "fresh", tag="v1.2.3", candidate="")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "The GitHub release v1.2.3 is this run's and is published" in done.stdout
    assert len(state["writes"]) == 2
    assert state["releases"][0]["draft"] is False

    # Re-run against the already-published release from subprocess
    existing = [make_release_record("v1.2.3-rc.1", True, rid=77, draft=False)]
    done_rc, state_rc = run_release_subprocess_e2e(
        tmp_path / "rerun", tag="v1.2.3-rc.1", candidate="true", releases=existing
    )
    assert done_rc.returncode == 0, done_rc.stdout + done_rc.stderr
    assert state_rc["writes"] == []


def _draft(**over):
    rel = make_release_record("v1.2.3", False, rid=77, draft=True)
    rel.update(over)
    return rel


def _tampered_draft_wheel():
    rel = make_release_record("v1.2.3", False, rid=77, draft=True)
    for asset in rel["assets"]:
        if asset["name"] == WHEEL:
            asset["_bytes"] = b"corrupt!"
            asset["size"] = len(b"corrupt!")
    return rel


STOP_CASES = [
    (
        "tag_unreadable",
        {"tag_replies": [{"rc": 1, "stdout": "", "stderr": "gh: HTTP 502\n"}]},
        "::error::the tag v1.2.3 could not be read as a commit of the repository"
        " (it is gone, or GitHub did not answer: gh's own words, if any, are above),"
        " so this job writes nothing more for its release and does not call it finished",
    ),
    (
        "tag_non_sha_exit_0",
        {"tag_replies": [{"rc": 0, "stdout": "not-a-sha\n", "stderr": ""}]},
        "::error::the tag v1.2.3 could not be read as a commit of the repository",
    ),
    (
        "tag_empty_exit_0",
        {"tag_replies": [{"rc": 0, "stdout": "", "stderr": ""}]},
        "::error::the tag v1.2.3 could not be read as a commit of the repository",
    ),
    (
        "tag_invalid_utf8_bytes",
        {"tag_replies": [{"rc": 0, "stdout_hex": "fffe80", "stderr_hex": "ff80"}]},
        "::error::the tag v1.2.3 could not be read as a commit of the repository",
    ),
    (
        "tag_moved",
        {"tag_replies": [{"rc": 0, "stdout": OTHER_COMMIT, "stderr": ""}]},
        f"::error::the tag v1.2.3 names commit {OTHER_COMMIT}, not this run's commit"
        f" {DEFAULT_COMMIT}, whose files this run built and attested, so this job writes"
        " nothing more for its release and does not call it finished",
    ),
    (
        "unlisted_http",
        {"list_replies": [{"rc": 1, "stdout": "", "stderr": "gh: HTTP 502\n"}]},
        "::error::the repository's releases could not be listed, so whether v1.2.3 has one"
        " is not known",
    ),
    (
        "unlisted_non_json",
        {"list_replies": [{"rc": 0, "stdout": "<html>502</html>", "stderr": ""}]},
        "::error::the repository's releases could not be listed, so whether v1.2.3 has one"
        " is not known",
    ),
    (
        "unlisted_empty",
        {"list_replies": [{"rc": 0, "stdout": "", "stderr": ""}]},
        "::error::the repository's releases could not be listed, so whether v1.2.3 has one"
        " is not known",
    ),
    (
        "unlisted_non_list",
        {"list_replies": [{"rc": 0, "stdout": '{"message":"bad"}', "stderr": ""}]},
        "::error::the repository's releases could not be listed, so whether v1.2.3 has one"
        " is not known",
    ),
    (
        "unlisted_missing_fields",
        {"list_replies": [{"rc": 0, "stdout": '[{"tag_name": "v1.2.3"}]', "stderr": ""}]},
        "::error::the repository's releases could not be listed, so whether v1.2.3 has one"
        " is not known",
    ),
    (
        "list_invalid_utf8_bytes",
        {"list_replies": [{"rc": 0, "stdout_hex": "fffe80", "stderr": ""}]},
        "::error::the repository's releases could not be listed, so whether v1.2.3 has one"
        " is not known",
    ),
    (
        "crowded",
        {"releases": [_draft(id=77), _draft(id=78)]},
        "::error::GitHub's listing shows 2 releases for v1.2.3, and this job finishes a"
        " release only when it is its tag's only one",
    ),
    (
        "behind",
        {
            "releases": [_draft()],
            "list_replies": [
                {
                    "rc": 0,
                    "stdout": json.dumps([strip_asset_bytes(_draft(name="Other"))]),
                    "stderr": "",
                }
            ],
        },
        "::error::GitHub's listing shows the release for v1.2.3 without this run's mark,"
        " pre-release flag, title or notes, while read by its id it is a draft and has"
        " all four as this run wrote them",
    ),
    (
        "read_404",
        {
            "releases": [_draft()],
            "read_replies": [{"rc": 1, "stdout": "", "stderr": "gh: Not Found (HTTP 404)\n"}],
        },
        "::error::the GitHub release for v1.2.3, which GitHub's listing showed, was not"
        " found when read by its id, so it has probably been deleted",
    ),
    (
        "read_502",
        {
            "releases": [_draft()],
            "read_replies": [{"rc": 1, "stdout": "", "stderr": "gh: Server Error (HTTP 502)\n"}],
        },
        "::error::the GitHub release for v1.2.3 could not be read by its id, as the lines"
        " above say, so nothing was compared",
    ),
    (
        "read_non_json",
        {
            "releases": [_draft()],
            "read_replies": [{"rc": 0, "stdout": "<html>502</html>", "stderr": ""}],
        },
        "::error::the GitHub release for v1.2.3 could not be read by its id, as the lines"
        " above say, so nothing was compared",
    ),
    (
        "read_empty",
        {"releases": [_draft()], "read_replies": [{"rc": 0, "stdout": "", "stderr": ""}]},
        "::error::the GitHub release for v1.2.3 could not be read by its id, as the lines"
        " above say, so nothing was compared",
    ),
    (
        "read_missing_tag_name",
        {"releases": [_draft()], "read_replies": [{"rc": 0, "stdout": '{"id": 77}', "stderr": ""}]},
        "::error::the GitHub release for v1.2.3 could not be read by its id, as the lines"
        " above say, so nothing was compared",
    ),
    (
        "read_missing_fields",
        {
            "releases": [_draft()],
            "read_replies": [{"rc": 0, "stdout": '{"id": 77, "tag_name": "v1.2.3"}', "stderr": ""}],
        },
        "::error::the GitHub release for v1.2.3 could not be read by its id, as the lines"
        " above say, so nothing was compared",
    ),
    (
        "read_invalid_utf8_bytes",
        {
            "releases": [_draft()],
            "read_replies": [{"rc": 0, "stdout_hex": "fffe80", "stderr": ""}],
        },
        "::error::the GitHub release for v1.2.3 could not be read by its id, as the lines"
        " above say, so nothing was compared",
    ),
    (
        "read_body_lone_surrogate",
        {
            "releases": [_draft()],
            "read_replies": [
                {
                    "rc": 0,
                    "stdout": json.dumps(strip_asset_bytes(_draft())).replace(
                        "the notes", "the notes\\ud800"
                    ),
                    "stderr": "",
                }
            ],
        },
        f"::error::read by its id, the GitHub release for v1.2.3, a draft, carries run"
        f" {DEFAULT_RUN_ID}'s mark and differs from what that run wrote in its notes",
    ),
    (
        "read_asset_name_lone_surrogate",
        {
            "releases": [_draft()],
            "read_replies": [
                {
                    "rc": 0,
                    "stdout": json.dumps(
                        {
                            **strip_asset_bytes(_draft()),
                            "assets": [
                                {
                                    "id": 770,
                                    "name": "bad_PLACEHOLDER.whl",
                                    "state": "uploaded",
                                    "size": 7,
                                    "updated_at": "2026-11-20T12:00:00Z",
                                }
                            ],
                        }
                    ).replace("bad_PLACEHOLDER.whl", "bad\\ud800.whl"),
                    "stderr": "",
                }
            ],
        },
        "::error::read by its id, the GitHub release for v1.2.3 is this run's draft and"
        " does not hold exactly the files this run attested",
    ),
    (
        "elsewhere",
        {
            "releases": [_draft()],
            "read_replies": [
                {
                    "rc": 0,
                    "stdout": json.dumps(strip_asset_bytes(_draft(tag_name="v9.9.9"))),
                    "stderr": "",
                }
            ],
        },
        "::error::the release GitHub's listing showed for v1.2.3 is, read by its id, a"
        " release of another tag",
    ),
    (
        "refuse_no_mark",
        {"releases": [_draft(body="hand made\n")]},
        "::error::read by its id, a GitHub release for v1.2.3 exists and is not one this"
        f" run can finish: its text does not carry run {DEFAULT_RUN_ID}'s mark",
    ),
    (
        "differs_title",
        {"releases": [_draft(name="Edited Title")]},
        f"::error::read by its id, the GitHub release for v1.2.3, a draft, carries run"
        f" {DEFAULT_RUN_ID}'s mark and differs from what that run wrote in its title",
    ),
    (
        "unfinished_draft_bytes",
        {"releases": [_tampered_draft_wheel()]},
        "::error::read by its id, the GitHub release for v1.2.3 is this run's draft and"
        f" does not hold exactly the files this run attested: {WHEEL} is not the file this"
        " run attested",
    ),
    (
        "unread_asset",
        {
            "releases": [_draft()],
            "asset_replies": [
                {"rc": 1, "stdout_hex": "", "stderr": "gh: Bad Gateway (HTTP 502)\n"}
            ],
        },
        "::error::the files of the GitHub release for v1.2.3, a draft as read by its id,"
        " could not be downloaded, so they were not compared and nothing shows that they"
        " differ",
    ),
    (
        "asset_corrupt_bytes_exit_0",
        {
            "releases": [_draft()],
            "asset_replies": [{"rc": 0, "stdout_hex": b"garbage".hex(), "stderr": ""}],
        },
        "::error::read by its id, the GitHub release for v1.2.3 is this run's draft and"
        f" does not hold exactly the files this run attested: {WHEEL} is not the file this"
        " run attested",
    ),
    (
        "changed_around_download",
        {"releases": [_draft()], "on_after_asset_download": "bump_updated_at"},
        "::error::the GitHub release for v1.2.3, a draft, changed between this job's two"
        " reads of it by its id, around the download of its files, so the files it compared"
        " may not be the ones the release holds",
    ),
    (
        "unseen_after_patch",
        {"on_patch": "delete_all"},
        "::error::GitHub's listing, at this job's last look, does not list the release for"
        " v1.2.3 as this job has just left it, and it can be seconds behind a write",
    ),
    (
        "drafted_after_publish",
        {"releases": [_draft()], "on_patch": "redraft_keep"},
        "::error::the GitHub release for v1.2.3, read by its id after this job published"
        " it, is a draft again",
    ),
    (
        "reverted_draft_tampered_final_check_refuses_not_unfinished",
        {"releases": [_draft()], "on_patch": "redraft_tamper_wheel"},
        "::error::read by its id, a GitHub release for v1.2.3 exists and is not one this"
        f" run can finish: {WHEEL} is not the file this run attested",
    ),
    (
        "lost_latest",
        {"on_patch": "add_higher"},
        "::error::the GitHub release for v1.2.3, read by its id, is this run's and"
        " public, with the title and the notes this run wrote and exactly the files this"
        " run attested, and this job's publication asked GitHub to make it the"
        " repository's latest release",
    ),
]


@pytest.mark.parametrize(
    ("name", "kwargs", "expected_prefix"), STOP_CASES, ids=[c[0] for c in STOP_CASES]
)
def test_real_wrapper_stops_exit_1_with_exact_error_line(tmp_path, name, kwargs, expected_prefix):
    """Run all stops, reverted-draft tamper refusal, and bad replies via bash -> python3 -I."""
    done, state = run_release_subprocess_e2e(tmp_path / name, **kwargs)
    assert done.returncode == 1, (name, done.stdout, done.stderr)
    assert "Traceback" not in done.stderr, (name, done.stderr)
    errors = [line for line in done.stdout.splitlines() if line.startswith("::error::")]
    assert len(errors) == 1, (name, errors)
    line = errors[0]
    assert line.startswith(expected_prefix), (name, line)
    head = line.removeprefix("::error::").split("; ")[0]
    assert any(re.fullmatch(re.escape(key).replace("…", ".+"), head) for key in stops()), (
        name,
        head,
    )
    assert not any((w[:2] == ["release", "delete"] or "DELETE" in w) for w in state["writes"]), (
        state["writes"]
    )


def test_real_wrapper_stop_cases_cover_all_16_stops_of_create_the_release():
    create_stops = {
        EXPANSION.sub("…", found.group(1)).split("; ")[0]
        for found in ERROR.finditer(CREATE["run"] + "\n")
    }
    assert len(create_stops) == 16, sorted(create_stops)
    for stop in create_stops:
        pattern = re.compile(r"^::error::" + re.escape(stop).replace("…", ".*"))
        assert any(pattern.match(prefix) for _, _, prefix in STOP_CASES), stop


def _is_valid_stop_line(line: str) -> bool:
    head = line.removeprefix("::error::").split("; ")[0]
    return any(re.fullmatch(re.escape(key).replace("…", ".+"), head) for key in stops())


def test_generated_hostile_replies_never_raise_and_always_emit_one_stop_line(tmp_path):
    """Any corrupt bytes, truncated JSON, wrong types, or lone surrogates from any read stop
    cleanly with exit 1, zero tracebacks, zero deletes, and one documented `::error::` line."""
    rng = random.Random(20261122)
    valid_draft = strip_asset_bytes(_draft())
    valid_list_bytes = json.dumps([valid_draft]).encode("utf-8")
    valid_read_bytes = json.dumps(valid_draft).encode("utf-8")

    # Ensure even literal JSON strings inside bytes test surrogate escapes as GitHub REST sends.
    surrogate_read_marked = (
        f'{{"id":77,"tag_name":"v1.2.3","name":"v1.2.3","draft":true,"prerelease":false,'
        f'"body":"the notes\\n\\n{DEFAULT_MARK}\\n\\ud800","assets":[]}}'
    ).encode()
    surrogate_read_unmarked = (
        b'{"id":77,"tag_name":"v1.2.3","name":"\\ud800","draft":true,"prerelease":false,'
        b'"body":"\\udfff","assets":[]}'
    )
    surrogate_asset_read = (
        f'{{"id":77,"tag_name":"v1.2.3","name":"v1.2.3","draft":true,"prerelease":false,'
        f'"body":"the notes\\n\\n{DEFAULT_MARK}\\n",'
        f'"assets":[{{"id":770,"name":"bad\\ud800.whl","state":"uploaded","size":1}}]}}'
    ).encode()

    wrong_json_shapes = [
        b"null",
        b"true",
        b"false",
        b"42",
        b'"string"',
        b"{}",
        b'{"id": true, "tag_name": "v1.2.3"}',
        b'{"id": "77", "tag_name": "v1.2.3"}',
        b'{"id": 77, "tag_name": null}',
        b'{"id": 77, "tag_name": 123}',
        b'{"id": 77, "tag_name": "v1.2.3", "draft": "true", "prerelease": false, "assets": []}',
        b'{"id": 77, "tag_name": "v1.2.3", "draft": true, "prerelease": 0, "assets": []}',
        b'{"id": 77, "tag_name": "v1.2.3", "draft": true, "prerelease": false, "assets": null}',
        b'{"id": 77, "tag_name": "v1.2.3", "draft": true, "prerelease": false, "assets": ["x"]}',
        b'{"id": 77, "tag_name": "v1.2.3", "draft": true, "prerelease": false,'
        b' "assets": [{"id":1}]}',
        b'{"id": ' + b"9" * 5000 + b', "tag_name": "v1.2.3"}',
        b"[" * 2000 + b"]" * 2000,
    ]
    raw_bad_bytes = [
        b"",
        b"   \n",
        b"\xff\xfe\x00\x80",
        b"\x80\x81\xfe\xffnot-utf8",
        *(bytes(rng.randrange(256) for _ in range(rng.randint(1, 64))) for _ in range(30)),
    ]

    tag_payloads = [
        *raw_bad_bytes,
        *wrong_json_shapes[:6],
        DEFAULT_COMMIT[:39].encode("utf-8"),
        b"0" * 40,
        b"v1.2.3\x00" + b"a" * 33,
    ]
    list_payloads = [
        *raw_bad_bytes,
        *wrong_json_shapes,
        b"[1, 2]",
        b"[null]",
        b'[{"tag_name": "v1.2.3"}]',
        b'[{"id": 77, "tag_name": "v1.2.3", "assets": null}]',
        b"[" + surrogate_read_marked + b"]",
        b"[" + surrogate_read_unmarked + b"]",
        *[valid_list_bytes[:cut] for cut in range(1, min(40, len(valid_list_bytes)), 3)],
    ]
    read_payloads = [
        *raw_bad_bytes,
        *wrong_json_shapes,
        b"[]",
        b'[{"id": 77, "tag_name": "v1.2.3"}]',
        surrogate_read_marked,
        surrogate_read_unmarked,
        surrogate_asset_read,
        *[valid_read_bytes[:cut] for cut in range(1, min(50, len(valid_read_bytes)), 3)],
    ]
    asset_payloads = [
        *raw_bad_bytes,
        b"truncated-wheel-bytes",
        b"\xff" * len(b"a wheel"),
    ]

    def _run_hostile(case_dir: Path, *, rc: int, err: bytes, kind: str, payload: bytes) -> None:
        hub = ReleaseHub(tag="v1.2.3", releases=[] if kind in ("tag", "list") else [_draft()])
        setattr(hub, f"raw_{kind}_reply", (rc, payload, err))
        res = run_release_in_process(hub, case_dir, tag="v1.2.3")
        assert res.returncode == 1, (kind, rc, payload[:60], res.stdout, res.stderr)
        assert "Traceback" not in res.stderr, (kind, res.stderr)
        assert hub.deletes == []
        err_line = res.single_error()
        assert _is_valid_stop_line(err_line), (kind, err_line)

    shared_dir = tmp_path / "hostile"
    for idx, payload in enumerate(tag_payloads):
        _run_hostile(shared_dir, rc=idx % 2, err=b"\xffbad-stderr\n", kind="tag", payload=payload)
    for idx, payload in enumerate(list_payloads):
        _run_hostile(
            shared_dir, rc=1 if idx % 7 == 0 else 0, err=b"\xfe", kind="list", payload=payload
        )
    for idx, payload in enumerate(read_payloads):
        _run_hostile(
            shared_dir, rc=1 if idx % 9 == 0 else 0, err=b"\xff", kind="read", payload=payload
        )
    for idx, payload in enumerate(asset_payloads):
        _run_hostile(
            shared_dir, rc=1 if idx % 3 == 0 else 0, err=b"\xff", kind="asset", payload=payload
        )


# --- 3. Tag verification invariants ----------------------------------------------------------


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("becomes", ["gone", OTHER_COMMIT])
def test_tag_deleted_or_moved_before_start_stops_before_listing(tmp_path, tag, candidate, becomes):
    hub = ReleaseHub(tag=tag, tag_mutates_at_read=(1, becomes))
    res = run_release_in_process(hub, tmp_path, tag=tag, candidate=candidate)
    assert res.returncode == 1 and hub.writes == [] and len(hub.calls) == 1
    msg = res.single_error()
    if becomes == "gone":
        assert msg.startswith(f"::error::the tag {tag} could not be read as a commit")
    else:
        assert msg.startswith(
            f"::error::the tag {tag} names commit {OTHER_COMMIT},"
            f" not this run's commit {DEFAULT_COMMIT}"
        )


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("becomes", ["gone", OTHER_COMMIT])
def test_tag_deleted_or_moved_after_draft_stops_before_publish(tmp_path, tag, candidate, becomes):
    hub = ReleaseHub(tag=tag, tag_after_create=becomes)
    res = run_release_in_process(hub, tmp_path, tag=tag, candidate=candidate)
    assert res.returncode == 1
    assert len(hub.writes) == 1 and hub.writes[0][:2] == ["release", "create"]
    assert hub.releases[0]["draft"] is True and hub.tag_recreated_at is None
    res.single_error()


@pytest.mark.parametrize(("tag", "candidate"), BOTH)
@pytest.mark.parametrize("becomes", ["gone", OTHER_COMMIT])
def test_tag_deleted_or_moved_after_publish_stops_before_success(tmp_path, tag, candidate, becomes):
    hub = ReleaseHub(tag=tag, tag_after_publish=becomes)
    res = run_release_in_process(hub, tmp_path, tag=tag, candidate=candidate)
    assert res.returncode == 1 and "is this run's and is published" not in res.stdout
    res.single_error()


def test_tag_deleted_just_before_patch_uses_target_commitish_never_main_head(tmp_path):
    hub = ReleaseHub(tag="v1.2.3", tag_before_publish="gone")
    res = run_release_in_process(hub, tmp_path, tag="v1.2.3")
    assert res.returncode == 0
    assert hub.tag_recreated_at == DEFAULT_COMMIT != hub.main_head


@pytest.mark.parametrize("fail_at", [1, 2, 3])
def test_tag_read_http_error_stops_then_plain_rerun_finishes(tmp_path, fail_at):
    hub = ReleaseHub(tag="v1.2.3", tag_fails_at_read=fail_at)
    res = run_release_in_process(hub, tmp_path, tag="v1.2.3")
    assert res.returncode == 1 and "HTTP 502" in res.stderr
    assert res.single_error().startswith("::error::the tag v1.2.3 could not be read as a commit")
    assert len(hub.writes) == {1: 0, 2: 1, 3: 2}[fail_at]
    hub.tag_fails_at_read = 0
    assert run_release_in_process(hub, tmp_path, tag="v1.2.3").returncode == 0


# --- 4. Notes & title byte-for-byte comparisons ----------------------------------------------


@pytest.mark.parametrize(
    "bad_body",
    [
        WROTE.replace("\n", "\r\n"),
        WROTE.replace("\n", "\r"),
        WROTE + "\n",
        WROTE + " ",
        WROTE[:-1],
        " " + WROTE,
        WROTE.replace(DEFAULT_MARK, DEFAULT_MARK + " "),
        WROTE.replace("\n\n", "\n"),
        DEFAULT_MARK,
    ],
)
@pytest.mark.parametrize("draft", [True, False])
def test_notes_are_compared_byte_for_byte_without_normalisation(tmp_path, bad_body, draft):
    existing = make_release_record("v1.2.3", False, rid=77, draft=draft, body=bad_body)
    hub = ReleaseHub(tag="v1.2.3", releases=[existing])
    res = run_release_in_process(hub, tmp_path, tag="v1.2.3")
    assert res.returncode == 1 and hub.writes == []
    state = "a draft" if draft else "public"
    assert res.single_error().startswith(
        f"::error::read by its id, the GitHub release for v1.2.3, {state}, carries run"
        f" {DEFAULT_RUN_ID}'s mark and differs from what that run wrote in its notes"
    )


@pytest.mark.parametrize(
    "notes",
    [
        b"the notes",
        b"the notes\n\n",
        b"- `code`, \"quotes\", 'more', \\ and $HOME\n",
        b"ends in space \n",
        b"\ttab and unicode \xc3\xb6 \xe2\x80\xa8\n",
        b"",
    ],
)
def test_step_preserves_notes_bytes_verbatim(tmp_path, notes):
    hub = ReleaseHub(tag="v1.2.3")
    res = run_release_in_process(hub, tmp_path, tag="v1.2.3", notes=notes)
    assert res.returncode == 0, res.stderr
    assert hub.releases[0]["body"] == notes.decode("utf-8") + f"\n{DEFAULT_MARK}\n"
    # Mutate by one space -> detected immediately
    hub.releases[0]["body"] += " "
    res2 = run_release_in_process(hub, tmp_path, tag="v1.2.3", notes=notes)
    assert res2.returncode == 1 and hub.writes == []
    assert "differs from what that run wrote in its notes" in res2.single_error()


@pytest.mark.parametrize("title", [None, "", "v1.2.3"])
def test_empty_null_or_exact_tag_title_accepted(tmp_path, title):
    existing = make_release_record("v1.2.3", False, rid=77, draft=True)
    existing["name"] = title
    hub = ReleaseHub(tag="v1.2.3", releases=[existing])
    assert run_release_in_process(hub, tmp_path, tag="v1.2.3").returncode == 0


@pytest.mark.parametrize("bad_title", ["v1.2.3 ", "Release 1.2.3", "Spring"])
@pytest.mark.parametrize("draft", [True, False])
def test_any_other_title_rejected(tmp_path, bad_title, draft):
    existing = make_release_record("v1.2.3", False, rid=77, draft=draft, title=bad_title)
    hub = ReleaseHub(tag="v1.2.3", releases=[existing])
    res = run_release_in_process(hub, tmp_path, tag="v1.2.3")
    assert res.returncode == 1 and hub.writes == []
    assert "differs from what that run wrote in its title;" in res.single_error()


def test_title_and_notes_both_edited_names_both(tmp_path):
    existing = make_release_record("v1.2.3", False, rid=77, draft=True, title="Other", body=EDITED)
    hub = ReleaseHub(tag="v1.2.3", releases=[existing])
    res = run_release_in_process(hub, tmp_path, tag="v1.2.3")
    assert res.returncode == 1
    assert "differs from what that run wrote in its title and notes;" in res.single_error()


# --- 5. Assets, binary downloads, concurrent swaps, and fresh reads --------------------------


@pytest.mark.parametrize(
    ("change", "override_bytes"),
    [({"state": "starter"}, None), ({}, b"wrong")],
    ids=["not uploaded", "wrong size"],
)
@pytest.mark.parametrize("draft", [True, False])
def test_asset_not_uploaded_or_wrong_size_stops_before_fetch(
    tmp_path, change, override_bytes, draft
):
    existing = make_release_record(
        "v1.2.3", False, rid=77, draft=draft, asset_overrides={SOURCE: change}
    )
    overrides = {SOURCE: override_bytes} if override_bytes is not None else None
    hub = ReleaseHub(tag="v1.2.3", releases=[existing])
    res = run_release_in_process(hub, tmp_path, tag="v1.2.3", file_overrides=overrides)
    assert res.returncode == 1 and hub.writes == []
    msg = res.single_error()
    if draft:
        assert "is this run's draft and does not hold exactly the files" in msg
    else:
        expected = (
            f"a GitHub release for v1.2.3 exists and is not one this run can finish:"
            f" {SOURCE} is not the file this run attested"
        )
        assert expected in msg


def test_binary_escape_bytes_compared_exact_and_concurrent_swap_detected(tmp_path):
    # Asset containing ESC (\x1b) + high bytes passes via --allow-escape-sequences.
    binary_whl = b"\x1b[31m\x00\xff\xfe"
    all_files = {
        WHEEL: binary_whl,
        SOURCE: b"a source archive",
        "notes.md": b"the notes\n",
    }
    import hashlib

    sums = "".join(f"{hashlib.sha256(d).hexdigest()}  {n}\n" for n, d in all_files.items()).encode()
    overrides = {WHEEL: binary_whl, SUMS: sums}
    existing = make_release_record(
        "v1.2.3",
        False,
        rid=77,
        draft=True,
        asset_files={WHEEL: binary_whl, SOURCE: b"a source archive", SUMS: sums},
    )
    hub = ReleaseHub(tag="v1.2.3", releases=[existing])
    res = run_release_in_process(hub, tmp_path, tag="v1.2.3", file_overrides=overrides)
    assert res.returncode == 0, res.stderr

    # Replace an asset right after its fetch -> second read by ID detects snapshot drift!
    existing2 = make_release_record("v1.2.3", False, rid=77, draft=True)

    def swap_source(h: ReleaseHub) -> None:
        a = next(x for x in h.releases[0]["assets"] if x["name"] == SOURCE)
        a["id"] = 999
        a["updated_at"] = "2026-11-20T12:59:59Z"

    hub2 = ReleaseHub(tag="v1.2.3", releases=[existing2], on_after_fetch={3: swap_source})
    res2 = run_release_in_process(hub2, tmp_path / "swap", tag="v1.2.3")
    assert res2.returncode == 1 and hub2.writes == []
    assert "changed between this job's two reads of it by its id" in res2.single_error()


@pytest.mark.parametrize("which", ["draft", "published"])
def test_download_http_failure_reports_unread_not_mismatch(tmp_path, which):
    existing = make_release_record("v1.2.3", False, rid=77, draft=(which == "draft"))
    hub = ReleaseHub(
        tag="v1.2.3",
        releases=[existing],
        download_fails_draft=(which == "draft"),
        download_fails_published=(which == "published"),
    )
    res = run_release_in_process(hub, tmp_path, tag="v1.2.3")
    assert res.returncode == 1
    state = "a draft" if which == "draft" else "public"
    assert res.single_error().startswith(
        f"::error::the files of the GitHub release for v1.2.3,"
        f" {state} as read by its id, could not be downloaded"
    )


# --- 6. Stale listing vs fresh read by ID & bounded lag waits --------------------------------


@pytest.mark.parametrize("field", ["name", "body", "prerelease"])
def test_matching_stale_listing_cannot_authorize_edited_release(tmp_path, field):
    matching = make_release_record("v1.2.3", False, rid=77, draft=True)
    edited = make_release_record("v1.2.3", False, rid=77, draft=True)
    if field == "name":
        edited["name"] = "Edited"
    elif field == "body":
        edited["body"] = EDITED
    else:
        edited["prerelease"] = True
    hub = ReleaseHub(
        tag="v1.2.3", releases=[edited], stale_override=([strip_asset_bytes(matching)], None)
    )
    res = run_release_in_process(hub, tmp_path, tag="v1.2.3")
    assert res.returncode == 1 and hub.writes == [] and hub.sleeps == []
    res.single_error()


def test_listing_lag_behind_create_and_publish_waits_then_succeeds_or_stops(tmp_path):
    # Delayed 10th look (9 sleeps * 3s) succeeds.
    hub = ReleaseHub(tag="v1.2.3", made_shown_from_look=10)
    res = run_release_in_process(hub, tmp_path / "ok", tag="v1.2.3")
    assert res.returncode == 0
    assert hub.sleeps == [3.0] * 9

    # Delayed past look 10 stops with unseen and never creates a second release.
    hub2 = ReleaseHub(tag="v1.2.3", made_shown_from_look=11)
    res2 = run_release_in_process(hub2, tmp_path / "timeout", tag="v1.2.3")
    assert res2.returncode == 1 and hub2.sleeps == [3.0] * 9
    assert len(hub2.releases) == 1 and hub2.releases[0]["draft"] is True
    assert (
        "does not list the release for v1.2.3 as this job has just left it" in res2.single_error()
    )

    # Subsequent re-run finishes cleanly without creating another release.
    hub2.made_shown_from_look = 1
    res3 = run_release_in_process(hub2, tmp_path / "timeout", tag="v1.2.3")
    assert res3.returncode == 0 and len(hub2.releases) == 1 and hub2.releases[0]["draft"] is False


def test_partial_asset_names_in_listing_waited_and_duplicate_stops_crowded(tmp_path):
    hub = ReleaseHub(tag="v1.2.3", names_listed_at_first=[WHEEL], names_listed_for_looks=3)
    res = run_release_in_process(hub, tmp_path / "partial", tag="v1.2.3")
    assert res.returncode == 0 and hub.sleeps == [3.0] * 3

    d1 = make_release_record("v1.2.3", False, rid=77, draft=True)
    d2 = make_release_record("v1.2.3", False, rid=78, draft=True)
    hub2 = ReleaseHub(tag="v1.2.3", releases=[d1, d2])
    res2 = run_release_in_process(hub2, tmp_path / "crowded", tag="v1.2.3")
    assert res2.returncode == 1 and hub2.writes == []
    assert "GitHub's listing shows 2 releases for v1.2.3" in res2.single_error()


def test_stale_row_with_valid_release_by_id_stops_with_behind_message(tmp_path):
    valid = make_release_record("v1.2.3", False, rid=77, draft=True)
    stale_row = {**strip_asset_bytes(valid), "name": "OtherTitle"}
    hub = ReleaseHub(tag="v1.2.3", releases=[valid], stale_override=([stale_row], None))
    res = run_release_in_process(hub, tmp_path, tag="v1.2.3")
    assert res.returncode == 1 and hub.writes == []
    assert (
        "while read by its id it is a draft and has all four as this run wrote them"
        in res.single_error()
    )


def test_read_by_id_404_502_tag_changed_and_drafted_diagnostics(tmp_path):
    # 404 after listing showed release
    d = make_release_record("v1.2.3", False, rid=77, draft=True)
    hub = ReleaseHub(tag="v1.2.3", releases=[d], deleted_before_read=1)
    assert (
        "was not found when read by its id"
        in run_release_in_process(hub, tmp_path / "404").single_error()
    )

    # 502 reports compared stage accurately
    hub_err = ReleaseHub(tag="v1.2.3", releases=[d], read_fails_at={1: (502, "Bad Gateway")})
    assert (
        "so nothing was compared"
        in run_release_in_process(hub_err, tmp_path / "502").single_error()
    )

    # tag_name mutated by person before read by ID -> elsewhere
    hub_tag = ReleaseHub(
        tag="v1.2.3",
        releases=[d],
        on_before_read={1: lambda h: h.releases[0].update(tag_name="v9.9.9")},
    )
    assert (
        "is, read by its id, a release of another tag"
        in run_release_in_process(hub_tag, tmp_path / "other").single_error()
    )

    # Re-drafted after publish with matching files -> drafted.
    hub_redraft = ReleaseHub(
        tag="v1.2.3",
        person_listed_from_look=100,
        on_once_public=lambda h: h.by_hand_mutate(lambda rs: [{**r, "draft": True} for r in rs]),
    )
    res_redraft = run_release_in_process(hub_redraft, tmp_path / "redraft")
    assert (
        "read by its id after this job published it, is a draft again" in res_redraft.single_error()
    )

    # Re-drafted after publish AND file tampered before final check -> refuse (not unfinished).
    def redraft_tamper(h: ReleaseHub) -> None:
        def mutate(rs):
            out = []
            for r in rs:
                nr = {**r, "draft": True}
                nr["assets"] = [
                    {**a, "_bytes": b"tampered", "size": 8} if a["name"] == WHEEL else a
                    for a in r["assets"]
                ]
                out.append(nr)
            return out

        h.by_hand_mutate(mutate)

    hub_tamper = ReleaseHub(
        tag="v1.2.3",
        person_listed_from_look=100,
        on_once_public=redraft_tamper,
    )
    res_tamper = run_release_in_process(hub_tamper, tmp_path / "redraft_tamper")
    assert res_tamper.returncode == 1
    assert res_tamper.single_error().startswith(
        "::error::read by its id, a GitHub release for v1.2.3 exists and is not one"
        f" this run can finish: {WHEEL} is not the file this run attested"
    )


def test_read_by_id_missing_or_null_tag_name_stops_unread_distinct_from_other_tag(tmp_path):
    """Pin the intentional distinction vs base jq: a read-by-ID JSON object missing `tag_name`
    or with `tag_name: null` stops with `could not be read by its id` (malformed response),
    whereas a string `tag_name != tag` stops with `is, read by its id, a release of another tag`."""
    d = make_release_record("v1.2.3", False, rid=77, draft=True)
    for payload in (b'{"id": 77}', b'{"id": 77, "tag_name": null}'):
        hub = ReleaseHub(tag="v1.2.3", releases=[d], raw_read_reply=(0, payload, b""))
        res = run_release_in_process(hub, tmp_path / "missing_tag")
        assert res.returncode == 1
        assert res.single_error().startswith(
            "::error::the GitHub release for v1.2.3 could not be read by its id, as the lines"
            " above say"
        )
    other_tag = make_release_record("v9.9.9", False, rid=77, draft=True)
    hub_other = ReleaseHub(
        tag="v1.2.3",
        releases=[d],
        raw_read_reply=(0, json.dumps(strip_asset_bytes(other_tag)).encode("utf-8"), b""),
    )
    res_other = run_release_in_process(hub_other, tmp_path / "other_tag")
    assert res_other.returncode == 1
    assert res_other.single_error().startswith(
        "::error::the release GitHub's listing showed for v1.2.3 is, read by its id, a"
        " release of another tag"
    )


# --- 7. Semver ordering & `make_latest` parity with `aliases` --------------------------------


@pytest.mark.parametrize(
    ("others", "expected_latest"),
    [
        ([], "true"),
        ([other_release("v1.3.0", 60)], "false"),
        ([other_release("v1.2.2", 60), other_release("v1.1.9", 61)], "true"),
        ([other_release("v1.2.10", 60)], "false"),
        ([other_release("v1.10.0", 60)], "false"),
        ([other_release("v2.0.0-rc.1", 60, prerelease=True)], "true"),
        ([other_release("v1.3.0", 60, draft=True)], "true"),
        ([other_release("nightly", 60), other_release("v1.3", 61)], "true"),
    ],
)
def test_make_latest_respects_numeric_semver_ordering(tmp_path, others, expected_latest):
    hub = ReleaseHub(tag="v1.2.3", releases=list(others))
    res = run_release_in_process(hub, tmp_path, tag="v1.2.3")
    assert res.returncode == 0
    assert f"make_latest={expected_latest}" in writes_joined(hub)[1]


def test_higher_stable_published_meanwhile_triggers_lost_latest_and_does_not_edit_others(tmp_path):
    higher = other_release("v1.3.0", 60)
    hub = ReleaseHub(
        tag="v1.2.3",
        releases=[other_release("v1.2.2", 61)],
        on_before_publish=lambda h: h.releases.append(higher),
    )
    res = run_release_in_process(hub, tmp_path, tag="v1.2.3")
    assert res.returncode == 1
    msg = res.single_error()
    assert (
        "GitHub's listing after the publication shows v1.3.0 published, a higher stable version"
        in msg
    )
    assert "make_latest=true" in writes_joined(hub)[1]


def test_python_stable_versions_matches_aliases_bash_ordering(tmp_path):
    ns: dict = {}
    exec(extract_release_script(), ns)
    sample = ["v0.10.0", "v0.9.12", "nightly", "v0.9.2", "v0.2", "v1.0.0-rc.1", "v01.3.0"]
    py_sorted = [".".join(map(str, v)) for v in ns["stable_versions"](sample)]
    aliases_fn = re.search(
        r"^( *)stable_versions\(\) \{\n.*?\n\1\}$", ALIASES_STEP["run"], re.M | re.S
    )[0]
    bash_done = run_step(
        aliases_fn + "\n" + f"printf '%s\\n' {' '.join(sample)} | stable_versions",
        {"PATH": SYSTEM},
    )
    assert bash_done.returncode == 0
    assert py_sorted == bash_done.stdout.splitlines() == ["0.9.2", "0.9.12", "0.10.0"]
    snap = ns["release_snapshot"]({"assets": [{"id": None, "name": "b"}, {"id": 1, "name": "a"}]})
    assert len(snap[-1]) == 2


# --- 8. README error table & header completeness ---------------------------------------------


def test_readme_error_table_rows_cover_every_release_step_error():
    assert "gh is not on this runner, so the GitHub release for … cannot be made" in stops()
    assert (
        len(
            cells(
                "the repository's releases could not be listed, so whether … has one is not known"
            )
        )
        == 2
    )
    assert (
        len(
            cells(
                "GitHub's listing shows … releases for …, and this job finishes a release only"
                " when it is its tag's only one"
            )
        )
        == 2
    )
    assert (
        len(
            cells(
                "the GitHub release for …, read by its id, is this run's and public, with the"
                " title and the notes this run wrote and exactly the files this run attested,"
                " and this job's publication asked GitHub to make it the repository's latest"
                " release"
            )
        )
        == 2
    )
    assert "release" in "".join(RAW_WORKFLOW)
