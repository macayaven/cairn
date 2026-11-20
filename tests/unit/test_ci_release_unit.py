"""The tag path of .github/workflows/ci-cd.yml: release_check -> publish -> verify -> package
-> attest -> release -> aliases.

- `package` (no permission at all) builds the wheel and the source archive and writes
  SHA256SUMS over what it made; `attest` and `release` each check every file against it.
- `attest` holds only what a build provenance attestation needs (an identity token and the
  right to store attestations), runs nothing from the repository or from the files, and
  attests the wheel, the source archive, SHA256SUMS and the image's digest.
- A release candidate vX.Y.Z-rc.N runs all of it but `aliases`: it publishes main's image for
  its commit as X.Y.Z-rc.N only, and a GitHub pre-release of its own that is not marked
  latest. It never deploys.
- No job that may write, or holds an identity token, builds or runs the repository's code,
  whether in a shell line or through an action that builds (`deploy` runs its one script)."""

import hashlib
import json
import re
import subprocess
from itertools import pairwise
from pathlib import Path

import pytest
import yaml
from ci_shell import first_attempt_gh, run_step
from test_ci_workflow_unit import (
    JOBS,
    MAIN_PUSH,
    RAW_WORKFLOW,
    TAG,
    THE_GATE,
    ancestors,
    evaluate,
    image_tags,
    job_comment,
    jobs_that_run,
    needs,
    not_a_pull_request,
    reaches_the_token,
    release_check,
    release_tags,
    rerun,
)

PACKAGE, ATTEST, RELEASE = JOBS["package"], JOBS["attest"], JOBS["release"]
TAG_PATH = ("release_check", "publish", "verify", "package", "attest", "release")
CANDIDATE = not_a_pull_request("push", "refs/tags/v1.2.3-rc.1")
ATTEST_ACTION = "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6"  # v4.2.2


# --- the order, and what each job may do -------------------------------------------------------


def test_the_tag_path_runs_in_its_order():
    for before, after in pairwise(TAG_PATH):
        assert before in ancestors(after), (before, after)
    assert needs(ATTEST) == {"publish", "package"}
    assert needs(RELEASE) == {"release_check", "publish", "verify", "package", "attest"}


def test_the_permissions_of_every_job_on_the_tag_path():
    assert {name: JOBS[name].get("permissions") for name in TAG_PATH} == {
        "release_check": {"contents": "read"},
        "publish": {"contents": "read", "packages": "write"},
        "verify": {"contents": "read", "packages": "read"},
        "package": {},
        "attest": {"id-token": "write", "attestations": "write"},
        "release": {"contents": "write"},
    }


BUILDS_OR_RUNS_THE_CHECKOUT = re.compile(
    r"\buv\s+(build|sync|pip|run|tool)\b|\bpip3?\b|\bpython3?\b|\bscripts/|\./"
    r"|\bdocker\s+(build|buildx\s+build|buildx\s+bake|compose|run|create|start|exec)\b"
)
# The actions that build: an image from the checkout, or the checkout's Python environment.
BUILDING_ACTIONS = (
    "docker/build-push-action",
    "docker/bake-action",
    "astral-sh/setup-uv",
    "actions/setup-python",
)
# The one script a job holding a token may run: `deploy` holds the deploy identity and runs
# scripts/deploy_production.sh from the checked-out commit of main, which copies an image by
# digest and moves a tag; it builds nothing and runs no image or test code (its own test,
# test_deploy_runs_no_script_but_the_deploy_script, holds that).
ALLOWED = {"deploy": "scripts/deploy_production.sh"}


def holds_a_token(job: dict) -> bool:
    permissions = job.get("permissions") or {}
    return any(level == "write" for level in permissions.values())


def builds_or_runs_the_checkout(name: str, job: dict) -> list[str]:
    """The steps of JOB that build or run repository code: a `run:` line that does, or a
    `uses:` action that builds."""
    found = []
    for step in job.get("steps", []):
        action = step.get("uses", "").split("@")[0]
        if action in BUILDING_ACTIONS:
            found.append(f"{name}: uses {action}")
        command = step.get("run", "")
        if name in ALLOWED:
            command = command.replace(ALLOWED[name], "")
        if BUILDS_OR_RUNS_THE_CHECKOUT.search(command):
            found.append(f"{name}: runs {step.get('name')}")
    return found


CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
LOGIN = "docker/login-action@8aa5831fb8d48d8f76594c7ddde61ae58019f550"
DOWNLOAD = "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333"
ATTEST_ACTION_PINNED = "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6"
GOOGLE_AUTH = "google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093"
# Every step a job holding a write permission or an identity token may have, in order: its
# name and the pinned action it uses, or "run" for a shell step written in the workflow. A
# new step, a new action, or an action at another commit fails here and is decided on.
PRIVILEGED_STEPS = {
    "publish": [
        ("Install oras", "run"),
        ("Log in to GHCR", LOGIN),
        ("Image tags", "docker/metadata-action@dc802804100637a589fabce1cb79ff13a1411302"),
        ("Look for this build in the registry", "run"),
        ("Fetch the build", DOWNLOAD),
        ("Push the scanned build", "run"),
    ],
    "aliases": [
        ("Install oras", "run"),
        ("Log in to GHCR", LOGIN),
        ("Point latest and each X.Y at the highest release", "run"),
    ],
    "attest": [
        ("Fetch what package built", DOWNLOAD),
        ("Check the files", "run"),
        ("The image's name", "run"),
        ("Attest the wheel, the source archive and SHA256SUMS", ATTEST_ACTION_PINNED),
        ("Attest the image", ATTEST_ACTION_PINNED),
    ],
    "attest_main": [("The image's name", "run"), ("Attest the image", ATTEST_ACTION_PINNED)],
    "release": [
        ("Check the GitHub CLI", "run"),
        ("Fetch what package built", DOWNLOAD),
        ("Check the files", "run"),
        ("Create the release", "run"),
    ],
    "deploy": [
        ("Check out", CHECKOUT),
        ("Check the settings", "run"),
        ("Log in to GHCR", LOGIN),
        ("Authenticate to Google Cloud", GOOGLE_AUTH),
        ("Find the registry host", "run"),
        ("Log in to Artifact Registry", LOGIN),
        ("Copy the image to Artifact Registry, by digest", "run"),
        ("Refuse a run that is no longer the head of main", "run"),
        ("Read the VM's report number", "run"),
        ("Point production at the digest", "run"),
        ("Wait for the VM to run it, healthy", "run"),
        ("Check the public address", "run"),
    ],
}  # fmt: skip
# The only lines of those shell steps that run a file: deploy's script, by its path, with one
# of its commands.
DEPLOY_LINE = re.compile(
    r"\bbash scripts/deploy_production\.sh (copy|check-head|seq|promote|wait|check-url)\b"
)
# (`source` and `.` run a file too; a file they run is named with its path or its .sh.)
RUNS_A_FILE = re.compile(r"(^|[\s;|&(`$])(bash|sh)\s+[^-\s]|\.sh\b|\.py\b|\./|\bscripts/")


def privileged_steps(job: dict) -> list[tuple[str, str]]:
    return [(s.get("name"), s.get("uses", "run")) for s in job["steps"]]


def files_run(job: dict) -> list[str]:
    """The lines of JOB's shell steps that run a file, deploy's own lines taken out."""
    return [
        line.strip()
        for s in job["steps"]
        for line in s.get("run", "").splitlines()
        if RUNS_A_FILE.search(DEPLOY_LINE.sub("", line))
    ]


def test_a_privileged_job_runs_exactly_its_listed_steps_and_no_file_but_deploy_s_script():
    holders = {name for name, job in JOBS.items() if holds_a_token(job)}
    assert holders == set(PRIVILEGED_STEPS)
    for name in holders:
        assert privileged_steps(JOBS[name]) == PRIVILEGED_STEPS[name], name
        assert files_run(JOBS[name]) == [], name
    # What the check sees: a new script in deploy, and one under another name or way of
    # running it; deploy's own script with a command it does not have.
    for added in (
        "bash release-helper.sh",
        "sh ./scripts/other.sh",
        "./tools/x",
        "python3 tools/y.py",
        "source env.sh",
        "bash scripts/deploy_production.sh status",
    ):
        job = {"steps": [*JOBS["deploy"]["steps"], {"name": "Check the settings", "run": added}]}
        assert files_run(job) == [added], added
    assert files_run({"steps": [{"run": 'bash scripts/deploy_production.sh wait "$D"'}]}) == []


def definition(value) -> str:
    """The SHA-256 of VALUE as canonical JSON (keys sorted, YAML's `on`, read as True, a
    string), the same on every machine whatever the YAML's layout."""

    def plain(x):
        if isinstance(x, dict):
            return {str(k): plain(v) for k, v in x.items()}
        return [plain(v) for v in x] if isinstance(x, list) else x

    text = json.dumps(plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


# The whole definition of each privileged job: its own keys (permissions, needs, if, runs-on,
# environment, concurrency, env, outputs) and then each step whole, `with` and `env` inputs,
# `shell`, `working-directory` and `if` included. A change to any of them is a change here.
PRIVILEGED_DEFINITIONS = {
    "publish": [
        "abe0ae08af0800c938fcde96faea54bb07276f962dbf16bad96ab39ec4f26a06",
        "1a6b7627d1ef6deb2d94425608ef7bae00108989018ac6ac819381746041cbe0",
        "8306689dab0ead439f5f7fd02a80289fa3c8492078e2f988a4a993978ed26baa",
        "d9ea58238b445cda0110bdacbaf8df6871101638f01b5ce1a827f14fff4e0535",
        "ef6a84363a5151811074777e49202394f0aa77ca636013f73154e1cc5f89998b",
        "ad9651396fe808494b0cdac909f262796fe4930a9055a9d868e6ddd8c5ba2adf",
        "c83e7facece707c4f75350e1de0052f2ce46fcc8a71786a447f6637ef62ea26f",
    ],
    "aliases": [
        "90094caae1a5888d5fcdce1e5bb80a92c0cb7539a7db7fd131655532ed5c4e83",
        "1a6b7627d1ef6deb2d94425608ef7bae00108989018ac6ac819381746041cbe0",
        "8306689dab0ead439f5f7fd02a80289fa3c8492078e2f988a4a993978ed26baa",
        "a494380ef5e74b3f32a7075e134120ee726c3b8a0d321bacf7f6ef2c83ab37cc",
    ],
    "attest": [
        "c131c0a395998bfe5cc8c174d51ff4369af022467b11be44b08ef7229b36955b",
        "61d8d41a68751f74093804f3395e79725021029e66dfb19aa87385d9b65e5574",
        "6561e69038da052bec6e80752c4b0248043b4c2fe503b8c3969f302ba5af7248",
        "cc86b92c64e9151bfa82f40dd1095dbfadd82a494a9bba96082398a87521a4d3",
        "61c61f66fe29242510dcfdbc44aa543de571c3c4c84dfef2d539372ba941b8bc",
        "17a3c14e979955db5dc020a60609de3a40456b894c3987fd0e6396fad1590ba9",
    ],
    "attest_main": [
        "033e875e462bb3bfef8f34ea5b9e037bc71026963ac4fd9e26065cffdd6838b0",
        "cc86b92c64e9151bfa82f40dd1095dbfadd82a494a9bba96082398a87521a4d3",
        "17a3c14e979955db5dc020a60609de3a40456b894c3987fd0e6396fad1590ba9",
    ],
    "release": [
        "5165d1610aa4638259b10262874a992db6b2b557007a329103fbbd9dcba655d4",
        "6153b4d908585eb1ba57111b2d53e509fce748ccd5770de0447b66079fe1fd48",
        "61d8d41a68751f74093804f3395e79725021029e66dfb19aa87385d9b65e5574",
        "6561e69038da052bec6e80752c4b0248043b4c2fe503b8c3969f302ba5af7248",
        "9998a7b8af19d39f6d41fda8c2bf317a9587ddc92f15b6a95d6404cd52bd1d4a",
    ],
    "deploy": [
        "401510e913f6e37136331eeefa6d88fce27bbba126b17c42793f7694b9600454",
        "b18abd84ee482615c40437c08518d017e4714ce2969a8de2177163237511424c",
        "a2535f2ff0de1a3d0277c76a2785eeac643094fc780539743881dac6e7cd3770",
        "8306689dab0ead439f5f7fd02a80289fa3c8492078e2f988a4a993978ed26baa",
        "5adafd3ace605a543d6987d35c06f906bf2906d9568de6f3680c9dd2a958199f",
        "98eea6ec9c1ffb5566953bb347a4eb98ba62f321f5a8a853e05a479fbce9ba19",
        "bbca2994a9d061fd00037a35ce2ee8983531d90544a0994fb2b54199b4a18095",
        "0a121d0a5e8a8d2842f01b512d79eac2b512addcedee827510b874abaa6f1eb7",
        "017f3f0cd69d3c189c0764dadcdbf1d3e3cd3c1793072e5139d8148a58d7e927",
        "33eb2834597be56ac27f28feeb21b8056c4cbaec3ad15858238a992f8be61fc4",
        "d6cbac9b74b53c64fccc732bcca08b479063efedd08a1012e94c1b5f16720142",
        "2017523234a93cd32e28ae852d764a8f1c537530e467fceec2a124648bc930c1",
        "07c4d51c7b032306506491757887c9353c08b9a0dd0e9452832d09b1758acba6",
    ],
}
# The workflow's own keys but its jobs (name, on, permissions, concurrency): a default shell,
# working directory or env there would reach every privileged step.
WORKFLOW_DEFINITION = "b1cee4920f1e889327531def9015032200cb85cf16e9c55d61b6396c655c14af"


def job_definition(job: dict) -> list[str]:
    own = {key: value for key, value in job.items() if key != "steps"}
    return [definition(own), *(definition(step) for step in job["steps"])]


def test_every_privileged_job_and_each_of_its_steps_is_pinned_whole():
    workflow = yaml.safe_load(RAW_WORKFLOW)
    assert definition({k: v for k, v in workflow.items() if k != "jobs"}) == WORKFLOW_DEFINITION
    assert set(PRIVILEGED_DEFINITIONS) == {n for n, j in JOBS.items() if holds_a_token(j)}
    for name, pinned in PRIVILEGED_DEFINITIONS.items():
        assert job_definition(JOBS[name]) == pinned, name
    # What the pin sees that the step list did not: deploy's checkout of another repository,
    # ref or path, a step's shell, working directory, env or inputs, and the job's defaults.
    deploy = JOBS["deploy"]
    checkout = deploy["steps"][0]
    for changed in (
        {**checkout, "with": {**checkout.get("with", {}), "repository": "someone/else"}},
        {**checkout, "with": {**checkout.get("with", {}), "ref": "other"}},
        {**checkout, "with": {**checkout.get("with", {}), "path": "elsewhere"}},
        {**deploy["steps"][1], "shell": "sh"},
        {**deploy["steps"][1], "working-directory": "elsewhere"},
        {**deploy["steps"][1], "env": {"BASH_ENV": "x"}},
    ):
        index = deploy["steps"].index(checkout if "uses" in changed else deploy["steps"][1])
        steps = [*deploy["steps"][:index], changed, *deploy["steps"][index + 1 :]]
        assert job_definition({**deploy, "steps": steps}) != PRIVILEGED_DEFINITIONS["deploy"]
    with_defaults = {**deploy, "defaults": {"run": {"shell": "sh"}}}
    assert job_definition(with_defaults) != PRIVILEGED_DEFINITIONS["deploy"]


def test_no_job_holding_a_write_permission_or_an_identity_token_builds_or_runs_the_checkout():
    holders = [name for name, job in JOBS.items() if holds_a_token(job)]
    assert set(holders) == {"publish", "aliases", "attest", "attest_main", "release", "deploy"}
    assert [f for name in holders for f in builds_or_runs_the_checkout(name, JOBS[name])] == []
    # Neither `publish`, `aliases`, the two attest jobs nor `release` checks out anything.
    for name in ("publish", "aliases", "attest", "attest_main", "release"):
        assert not any("actions/checkout" in s.get("uses", "") for s in JOBS[name]["steps"])
    # The model sees a build where one is: in a shell line, and in an action.
    assert BUILDS_OR_RUNS_THE_CHECKOUT.search("uv build --out-dir dist")
    assert BUILDS_OR_RUNS_THE_CHECKOUT.search("python3 scripts/release_check.py v1")
    assert BUILDS_OR_RUNS_THE_CHECKOUT.search("pip install x")
    assert BUILDS_OR_RUNS_THE_CHECKOUT.search("docker buildx build --push .")
    assert BUILDS_OR_RUNS_THE_CHECKOUT.search("docker run --rm ghcr.io/o/r@sha256:x")
    build = {"uses": "docker/build-push-action@" + "0" * 40, "with": {"push": True}}
    assert builds_or_runs_the_checkout("x", {"steps": [build]}) == [
        "x: uses docker/build-push-action"
    ]
    # And the deploy script counts everywhere but in `deploy`.
    deploy = {"steps": [{"name": "s", "run": "bash scripts/deploy_production.sh copy x"}]}
    assert builds_or_runs_the_checkout("publish", deploy) == ["publish: runs s"]
    assert builds_or_runs_the_checkout("deploy", deploy) == []


def test_only_the_attest_jobs_and_deploy_hold_an_identity_token_and_attest_where_nothing_is_built():
    holders = {name for name, job in JOBS.items() if "id-token" in job.get("permissions", {})}
    assert holders == {"deploy", "attest", "attest_main"}
    assert ATTEST["runs-on"] == JOBS["attest_main"]["runs-on"] == JOBS["publish"]["runs-on"]
    assert ATTEST["if"] == PACKAGE["if"] == RELEASE["if"]
    assert "environment" not in ATTEST and "secrets." not in yaml.safe_dump(ATTEST)


# --- package: the checksums -------------------------------------------------------------------


def shim(bin_) -> None:
    """sha256sum as a runner has it; on this machine, shasum standing in."""
    bin_.mkdir(exist_ok=True)
    (bin_ / "sha256sum").write_text(
        '#!/bin/sh\nif [ -x /usr/bin/sha256sum ]; then exec /usr/bin/sha256sum "$@"; fi\n'
        'exec shasum -a 256 "$@"\n'
    )
    (bin_ / "sha256sum").chmod(0o755)


def bash(run: str, cwd, env: dict) -> subprocess.CompletedProcess:
    return run_step(
        run,
        cwd=cwd,
        env=env,
    )


def test_package_writes_the_checksums_of_what_it_made_before_it_uploads_them(tmp_path):
    names = [step.get("name") for step in PACKAGE["steps"]]
    (step,) = [s for s in PACKAGE["steps"] if "SHA256SUMS" in s.get("run", "")]
    assert names.index(step["name"]) == len(names) - 2  # just before the upload
    release = tmp_path / "release"
    release.mkdir()
    made = ["cairn_memory-1.2.3-py3-none-any.whl", "cairn_memory-1.2.3.tar.gz", "notes.md"]
    for name in [*made, ".gitignore"]:
        (release / name).write_text(f"the bytes of {name}")
    shim(tmp_path / "bin")
    env = {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "RUNNER_TEMP": str(tmp_path)}
    done = bash(step["run"], tmp_path, env)
    assert done.returncode == 0, done.stderr
    lines = (release / "SHA256SUMS").read_text().splitlines()
    assert lines == [
        f"{hashlib.sha256(f'the bytes of {name}'.encode()).hexdigest()}  {name}"
        for name in sorted(made)
    ]


def test_package_holds_no_permission_and_says_what_to_do_if_the_checkout_needs_one():
    assert PACKAGE["permissions"] == {}
    assert (
        "If the checkout ever fails for want of one, give the job `contents: read` again, and"
        " nothing more." in job_comment("package")
    )
    assert "SHA256SUMS, their checksums, which `attest` and `release` each check every file" in (
        job_comment("package")
    )
    assert JOBS["package_check"]["permissions"] == {"contents": "read"}


# --- attest and release: every file checked against SHA256SUMS ---------------------------------

THE_FILES = [
    "cairn_memory-1.2.3-py3-none-any.whl",
    "cairn_memory-1.2.3.tar.gz",
    "notes.md",
]


def artifact(tmp_path, files=THE_FILES, sums=None, tamper=None):
    """The directory `download-artifact` fills: FILES, and SHA256SUMS over SUMS (FILES)."""
    release = tmp_path / "release"
    release.mkdir()
    for name in files:
        (release / name).write_text(f"the bytes of {name}")
    lines = [
        f"{hashlib.sha256((release / n).read_bytes()).hexdigest()}  {n}"
        for n in sorted(files if sums is None else sums)
        if (release / n).exists()
    ]
    (release / "SHA256SUMS").write_text("".join(f"{line}\n" for line in lines))
    if tamper:
        (release / tamper).write_text("other bytes")
    return release


def check_step(job: dict) -> dict:
    (step,) = [s for s in job["steps"] if s.get("name") == "Check the files"]
    return step


def run_check(tmp_path, job: dict, tag: str = "v1.2.3"):
    shim(tmp_path / "bin")
    env = {
        "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_REF_NAME": tag,
    }
    return bash(check_step(job)["run"], tmp_path, env)


@pytest.mark.parametrize("job", [ATTEST, RELEASE], ids=["attest", "release"])
def test_the_files_pass_when_each_matches_its_checksum(tmp_path, job):
    artifact(tmp_path)
    done = run_check(tmp_path, job)
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize("job", [ATTEST, RELEASE], ids=["attest", "release"])
@pytest.mark.parametrize(
    "case",
    ["a changed wheel", "a changed text", "a file left out of the sums", "another file listed"],
)
def test_a_file_that_does_not_match_its_checksum_stops_the_job(tmp_path, job, case):
    if case == "a changed wheel":
        artifact(tmp_path, tamper=THE_FILES[0])
    elif case == "a changed text":
        artifact(tmp_path, tamper="notes.md")
    elif case == "a file left out of the sums":
        artifact(tmp_path, sums=THE_FILES[:2])
    else:
        release = artifact(tmp_path)
        with (release / "SHA256SUMS").open("a") as sums:
            sums.write(f"{'0' * 64}  ../elsewhere.whl\n")
    done = run_check(tmp_path, job)
    assert done.returncode != 0


def test_the_two_jobs_check_with_the_same_step():
    assert check_step(ATTEST)["run"] == check_step(RELEASE)["run"]


@pytest.mark.parametrize("job", [ATTEST, RELEASE], ids=["attest", "release"])
def test_a_candidate_s_files_carry_the_version_of_its_release(tmp_path, job):
    artifact(tmp_path)
    assert run_check(tmp_path, job, tag="v1.2.3-rc.4").returncode == 0


# --- attest: what is attested, and with what ----------------------------------------------------


def test_attest_fetches_checks_and_attests_and_nothing_more():
    steps = ATTEST["steps"]
    kinds = [s.get("uses", "run").split("@")[0] for s in steps]
    assert kinds == ["actions/download-artifact", "run", "run", "actions/attest", "actions/attest"]
    assert steps[0]["with"] == {"name": "release", "path": "${{ runner.temp }}/release"}
    files, image = steps[3], steps[4]
    assert files["uses"].startswith(ATTEST_ACTION) and image["uses"].startswith(ATTEST_ACTION)
    assert files["with"] == {
        "subject-path": "${{ runner.temp }}/release/*.whl\n"
        "${{ runner.temp }}/release/*.tar.gz\n"
        "${{ runner.temp }}/release/SHA256SUMS\n"
    }
    named = steps[2]
    assert image["with"] == {
        "subject-name": "${{ steps." + named["id"] + ".outputs.name }}",
        "subject-digest": "${{ needs.publish.outputs.digest }}",
    }
    # The attestation stays with GitHub (gh attestation verify reads it from there): nothing
    # is pushed to the registry, so the job needs no packages permission.
    assert "push-to-registry" not in image["with"]
    # The token reaches the two attest steps and nothing else.
    assert [s["name"] for s in steps if reaches_the_token(s)] == [files["name"], image["name"]]


def test_the_image_is_named_in_lower_case_after_its_digest_is_checked(tmp_path):
    named = ATTEST["steps"][2]
    assert named["env"] == {"DIGEST": "${{ needs.publish.outputs.digest }}"}
    output = tmp_path / "out"
    env = {"PATH": "/usr/bin:/bin", "GITHUB_REPOSITORY": "Owner/Repo", "GITHUB_OUTPUT": str(output)}
    # bash 4's ${x,,} is what the runner has; the step must say exactly that.
    assert 'echo "name=ghcr.io/${GITHUB_REPOSITORY,,}" >> "$GITHUB_OUTPUT"' in named["run"]
    for digest, code in (("sha256:" + "a" * 64, 0), ("", 1), ("sha256:abc", 1)):
        run = named["run"].replace("${GITHUB_REPOSITORY,,}", "owner/repo")
        done = bash(run, tmp_path, {**env, "DIGEST": digest})
        assert done.returncode == code, digest
    assert output.read_text() == "name=ghcr.io/owner/repo\n"


# --- release: the checksums published, a candidate as a pre-release ----------------------------


def run_create(tmp_path, tag: str, candidate: str):
    (step,) = [s for s in RELEASE["steps"] if s.get("name") == "Create the release"]
    assert step["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "GH_REPO": "${{ github.repository }}",
        "CANDIDATE": "${{ needs.release_check.outputs.candidate }}",
    }
    (tmp_path / "release").mkdir()
    (tmp_path / "release" / "notes.md").write_text("the notes\n")
    for name in ("cairn_memory-1.2.3-py3-none-any.whl", "cairn_memory-1.2.3.tar.gz", "SHA256SUMS"):
        (tmp_path / "release" / name).write_text(name)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    first_attempt_gh(bin_)  # no release for the tag yet, then the draft the step asks for
    env = {
        "PATH": f"{bin_}:/usr/bin:/bin",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_REF_NAME": tag,
        "GITHUB_SHA": COMMIT,
        "GH_REPO": "owner/repo",
        "CANDIDATE": candidate,
    }
    done = bash(step["run"], tmp_path, env)
    assert done.returncode == 0, done.stdout + done.stderr
    return [(tmp_path / f"gh-{write}").read_text().splitlines() for write in ("create", "publish")]


PUBLICATION = ["api", "--method", "PATCH", "repos/owner/repo/releases/7", "--silent", "-F"]
COMMIT = "1a2b3c4d" * 5  # the run's commit, which the tag names


def test_a_release_publishes_the_two_files_and_their_checksums_as_before(tmp_path):
    create, publish = run_create(tmp_path, "v1.2.3", "")
    assert publish == [*PUBLICATION, "draft=false"]
    assert create == [
        "release",
        "create",
        "v1.2.3",
        "--draft",
        "--verify-tag",
        "--target",
        COMMIT,
        "--title",
        "v1.2.3",
        "--notes-file",
        f"{tmp_path}/body.md",
        "cairn_memory-1.2.3-py3-none-any.whl",
        "cairn_memory-1.2.3.tar.gz",
        "SHA256SUMS",
    ]


def test_a_candidate_is_a_pre_release_never_marked_latest(tmp_path):
    """Said when the draft is made and again when it is published, as `gh release create`
    itself said it in both of its requests."""
    create, publish = run_create(tmp_path, "v1.2.3-rc.2", "true")
    assert publish == [*PUBLICATION, "draft=false", "-f", "make_latest=false"]
    assert create == [
        "release",
        "create",
        "v1.2.3-rc.2",
        "--draft",
        "--verify-tag",
        "--target",
        COMMIT,
        "--title",
        "v1.2.3-rc.2",
        "--notes-file",
        f"{tmp_path}/body.md",
        "--prerelease",
        "--latest=false",
        "cairn_memory-1.2.3-py3-none-any.whl",
        "cairn_memory-1.2.3.tar.gz",
        "SHA256SUMS",
    ]


def test_release_check_tells_the_jobs_after_it_that_a_tag_is_a_candidate():
    (check,) = [s for s in JOBS["release_check"]["steps"] if s.get("id")]
    assert JOBS["release_check"]["outputs"] == {
        "candidate": "${{ steps." + check["id"] + ".outputs.candidate }}",
    }


# --- a candidate's image tags, and where it does not go ----------------------------------------


def candidate_tags(tag: str) -> set[str]:
    context = not_a_pull_request("push", f"refs/tags/{tag}")
    outputs = {"release_check": {"outputs": {"latest": "false", "candidate": "true"}}}
    return image_tags({**context, "needs": outputs})


def test_a_candidate_is_published_as_its_own_tag_only():
    # Its image is main's, already sha-<commit>: a tag run writes no sha- tag.
    assert candidate_tags("v1.2.3-rc.1") == {"1.2.3-rc.1"}
    assert candidate_tags("v0.1.0-rc.12") == {"0.1.0-rc.12"}


def test_a_release_after_its_candidates_publishes_its_full_version():
    # `publish` pushes the full version alone; `aliases` moves 0.1 and latest after the release.
    assert release_tags("v0.1.0", ["v0.1.0-rc.1", "v0.1.0-rc.2"]) == {"0.1.0"}
    assert release_tags("v0.1.1", ["v0.1.0", "v0.2.0-rc.1", "v0.2.0"]) == {"0.1.1"}
    assert release_check.is_highest("v0.2.0", ["v0.2.0-rc.1", "v0.3.0-rc.1", "v0.2.0"])


def test_a_candidate_runs_the_whole_tag_path_and_never_deploys():
    ran = jobs_that_run(CANDIDATE, {"release_check": {"candidate": "true"}})
    assert ran == THE_GATE | set(TAG_PATH)
    # Neither deploy nor the aliases: a candidate moves neither X.Y nor latest.
    assert "deploy" not in ran and "aliases" not in ran
    assert "aliases" in jobs_that_run(CANDIDATE, {"release_check": {"candidate": ""}})
    assert not evaluate(JOBS["deploy"]["if"], CANDIDATE)


# --- what the header and the guides say ---------------------------------------------------------

REPO = Path(__file__).resolve().parents[2]
README = (REPO / "README.md").read_text(encoding="utf-8")
DEPLOY_GUIDE = (REPO / "deploy" / "README.md").read_text(encoding="utf-8")
VERIFY = re.compile(r"^gh attestation verify .+$", re.M)


def flat(text: str) -> str:
    return " ".join(text.split())


def test_the_header_says_what_the_tag_path_does_for_a_release_and_a_candidate():
    raw = (REPO / ".github" / "workflows" / "ci-cd.yml").read_text(encoding="utf-8")
    header = flat(raw.split("\nname:")[0].replace("# ", ""))
    for sentence in (
        "`package` writes SHA256SUMS over the files it built, and the release publishes it with"
        " them.",
        "job `attest`, which holds an identity token and nothing that writes to the repository,"
        " checks every file against SHA256SUMS, as `release` does, and gives the wheel, the"
        " source archive, SHA256SUMS and the image's digest each a build provenance"
        " attestation.",
        "Its image, main's for its commit, is published as X.Y.Z-rc.N only, never X.Y, X.Y.Z"
        " or latest, and its GitHub release is a pre-release of its own, with its own files,"
        " not marked latest;",
        "A v* tag builds nothing: image_build reads the image main's run published for the"
        " tag's commit as sha-<commit>, by its digest,",
        "A tag never deploys.",
    ):
        assert sentence in header, sentence


def test_the_guides_give_the_two_checks_of_an_attestation_and_what_a_pass_proves():
    owner = "<owner>/<repo>"
    workflow = f"--repo {owner} --signer-workflow {owner}/.github/workflows/ci-cd.yml"
    expected = {
        f"gh attestation verify cairn_memory-X.Y.Z-py3-none-any.whl {workflow}"
        " --source-ref refs/tags/vX.Y.Z --source-digest <commit>",
        f"gh attestation verify oci://ghcr.io/{owner}:X.Y.Z {workflow}",
        f"gh attestation verify oci://ghcr.io/{owner}:X.Y.Z {workflow} --source-ref"
        " refs/heads/main --source-digest <commit>",
    }
    commit = (
        "`<commit>` is the full commit the tag `vX.Y.Z` names (`git rev-parse"
        " 'vX.Y.Z^{commit}'`): `--source-digest` checks that the attestation was made by a run"
        " of that commit, which is what ties the files and the image to the release's commit."
    )
    ci = README[README.index("## CI/CD") : README.index("## Planned")]
    update = DEPLOY_GUIDE[DEPLOY_GUIDE.index("## Update") :]
    update = update[: update.index("\n## ")]
    proof = (
        "`--source-ref` checks which ref that run was for: a release candidate tagged on the"
        " same commit builds and attests a wheel of its own under the same name, so the wheel's"
        " command names `refs/tags/vX.Y.Z`, and `refs/tags/vX.Y.Z-rc.N` when the file is a"
        " candidate's. Use `gh` 2.102.0 or later: from that version it compares `--source-ref`"
        " with the attestation's ref exactly and `--signer-workflow` with the whole workflow"
        " path, where earlier versions ignored the ref's case and took the workflow as a prefix"
        " (GHSA-4mq3-hpgx-9cx8, GHSA-wjmr-j3rp-mh2g). A pass for the wheel proves that these"
        " bytes were built and attested by"
        " this repository's `ci-cd.yml` in a run of the tag `vX.Y.Z` at that commit, unchanged"
        " since; it does not prove that they are the files the release attaches, since a re-run"
        " of job `package` builds files of its own and `attest` attests those too. A pass for"
        " the image proves less by itself, since the tag's run, which promotes the image and does"
        " not build it, attests its digest too; with `--source-ref refs/heads/main` a pass"
        " proves that `main`'s run of `ci-cd.yml` published and attested that digest, the run"
        " that built it, as the tag's run checked before it promoted it. Neither proves that"
        " the code or its dependencies are free of flaws"
    )
    tag_check = next(
        s for s in JOBS["image_build"]["steps"] if s.get("name", "").startswith("Check that main")
    )
    for guide in (ci, update):
        assert set(VERIFY.findall(guide)) == expected
        assert proof in flat(guide)
        assert commit in flat(guide)
        assert "A pass proves that these bytes are the ones" not in flat(guide)
        assert "built and attested in the tag's run" not in flat(guide)
        # The commands' shape: every flag is one the tag's run gives gh for main's attestation.
        for command in VERIFY.findall(guide):
            assert set(re.findall(r"--[a-z-]+", command)) <= set(
                re.findall(r"--[a-z-]+", tag_check["run"][tag_check["run"].index("verify") :])
            ), command
    # Why the wheel's command names the tag's ref: a candidate and the release tagged on one
    # commit each run `package` and `attest`, on a tag's ref and never on main, and both name
    # the wheel by the version alone. And a re-run of `package` is followed by `attest`.
    for tagged in (TAG, CANDIDATE):
        ran = jobs_that_run(tagged, {"release_check": {"candidate": ""}})
        assert {"package", "attest"} <= ran
    assert not {"package", "attest"} & jobs_that_run(MAIN_PUSH)
    (check,) = [s for s in ATTEST["steps"] if s.get("name") == "Check the files"]
    assert 'version="${version%-rc.*}"' in check["run"]
    assert "attest" in rerun({"package"})
    # The version the guides ask for is the one the tag's run refuses to go below, for the
    # same two advisories (GitHub's own words, read on 2026-11-20: GHSA-4mq3-hpgx-9cx8, "`gh`
    # now compares the `--source-ref` value against the certificate's source ref exactly",
    # patched in v2.102.0; GHSA-wjmr-j3rp-mh2g, "`gh` now requires the `--signer-workflow`
    # value to match the complete workflow path in the SAN", patched in v2.102.0).
    assert '[ "$major" = 2 ] && [ "$minor" -lt 102 ]' in tag_check["run"]
    assert "GHSA-wjmr-j3rp-mh2g and GHSA-4mq3-hpgx-9cx8" in tag_check["run"]
    # The flag the guides give is the one the tag's run checks main's attestation with.
    assert "--source-ref refs/heads/main" in tag_check["run"]
    # The image the guide names is the one `publish` tags and `attest` attests by digest.
    meta = next(s for s in JOBS["publish"]["steps"] if s.get("id") == "meta")
    assert meta["with"]["images"] == "ghcr.io/${{ github.repository }}"


def test_the_deploy_guide_says_how_to_see_the_provenance_and_the_bill_of_materials():
    """Docker's reference for `docker buildx imagetools inspect` gives `--raw` for the index
    and `--format "{{json .Provenance}}"` and `"{{json .SBOM}}"` for the two attestations."""
    update = DEPLOY_GUIDE[DEPLOY_GUIDE.index("## Update") :]
    update = flat(update[: update.index("\n## ")])
    assert (
        "`docker buildx imagetools inspect --raw ghcr.io/<owner>/<repo>:X.Y.Z` prints the image"
        " index, whose attestation manifests (platform `unknown/unknown`) hold each platform's"
        " build provenance and bill of materials; `docker buildx imagetools inspect"
        " ghcr.io/<owner>/<repo>:X.Y.Z --format '{{json .Provenance}}'`, and"
        " `'{{json .SBOM}}'`, print them." in update
    )


def test_the_readme_says_what_a_change_of_documents_alone_still_runs_and_how_tags_move():
    ci = flat(README[README.index("## CI/CD") : README.index("## Planned")])
    for sentence in (
        "One exception: a change that touches only paths no image job reads runs no image check"
        " (item 3) and no stack or host rehearsal (item 4), so a pull request runs items 1 and"
        " 2 alone and a push to `main`"
        " publishes and deploys nothing; the server keeps the image it runs. Its image is still"
        " built for both platforms and scanned, as item 1 says.",
        "`X.Y.Z` never moves: if the registry already has that version, the same digest is"
        " carried on and another digest stops the job.",
        "A `v*` tag builds no image: it promotes the digest that `main`'s run published as"
        " `sha-<commit>` for the tag's commit to `X.Y.Z` (a candidate, to `X.Y.Z-rc.N`), and"
        " writes no `sha-` tag, so a candidate and the release tagged on one commit carry"
        " `main`'s image, the same digest.",
        "When the registry has no `sha-<commit>` for the tag's commit, the tag's run stops"
        " before publishing anything and says to run it again once `main`'s run for that"
        " commit has published it;",
        "After a release, and never after a candidate, job `aliases` points `latest` at the"
        " highest released version and each"
        " `X.Y` at its newest `X.Y.z`, one such job at a time across all runs, deciding from the"
        " releases and the registry as they are when it runs, so overlapping releases cannot"
        " move either backwards.",
        "On `main`, re-running every job builds again and `publish` stops, since"
        " `sha-<commit>` holds another digest: re-run only the failed jobs.",
        "The job that publishes it, one of the two that may write to the registry, builds"
        " nothing, checks out nothing and runs no image.",
    ):
        assert sentence in ci, sentence
    for gone in (
        "touches only paths no image job reads builds no image",
        "a `deploy` job that waits for approval",
        "are replaced by the release's",
        "`X.Y.Z-rc.N` and `sha-<commit>`",
        "the image built for linux/amd64",
        "re-running every job would publish the version again",
        "as `latest` only when it is the highest version tagged",
    ):
        assert gone not in ci, gone


def test_the_readme_says_in_two_sentences_what_a_candidate_is_for_and_what_it_publishes():
    ci = flat(README[README.index("## CI/CD") : README.index("## Planned")])
    assert (
        "A release candidate tag `vX.Y.Z-rc.N` (N from 1) rehearses the release of `X.Y.Z`"
        " before it is made: it needs the version `X.Y.Z` in `pyproject.toml` and its section"
        " in `CHANGELOG.md`, runs every job a release runs but `aliases`, attestation included,"
        " and is refused once `vX.Y.Z` is tagged or released. It publishes `main`'s image for"
        " its commit as `X.Y.Z-rc.N` only, never `X.Y`, `X.Y.Z` or `latest`, and makes a"
        " GitHub pre-release of its own, not marked latest, with its own files, which carry the"
        " version `X.Y.Z`. The release tagged later on the same commit publishes the same image"
        " digest and makes a second GitHub release, with files of its own; the candidate's"
        " pre-release stays as it was." in ci
    )
    # What it rests on: a candidate's run skips `aliases` only, and `release` makes a release
    # for the tag that started the run, a pre-release for a candidate.
    ran = jobs_that_run(CANDIDATE, {"release_check": {"candidate": "true"}})
    released = jobs_that_run(CANDIDATE, {"release_check": {"candidate": ""}})
    assert released - ran == {"aliases"} and ran <= released
    create = next(s for s in RELEASE["steps"] if s.get("name") == "Create the release")["run"]
    assert 'gh release create "$GITHUB_REF_NAME"' in create
    assert "candidate=(--prerelease --latest=false)" in create


def test_the_readme_s_two_limits_are_the_numbers_the_workflow_and_the_script_use():
    ci = flat(README[README.index("## CI/CD") : README.index("## Planned")])
    assert (
        "`release_check.py` and job `aliases` each ask GitHub for at most 1,000 releases"
        " (`release_check.py` counts candidates' pre-releases too): a listing that reaches"
        " 1,000 stops the job and says so, and a person then raises both numbers together,"
        " before the next tag." in ci
    )
    # The two stop at different points: release_check before anything is published, aliases
    # after the version and its GitHub release are, so a release made at 999 whose aliases
    # then list 1,000 is published with `latest` and X.Y not moved.
    assert (
        "`release_check.py` stops before anything is published; `aliases` stops after the"
        " version and its GitHub release are, moving neither `latest` nor `X.Y`." in ci
    )
    assert "release_check" in needs(JOBS["publish"]) and "release" in needs(JOBS["aliases"])
    assert release_check.RELEASES_ASKED == 1000
    aliases = next(s for s in JOBS["aliases"]["steps"] if "gh release list" in s.get("run", ""))
    assert "--limit 1000 " in aliases["run"] and "-ge 1000 ]" in aliases["run"]
    # release_check runs before publish, so its refusal publishes nothing.
    assert "release_check" in needs(JOBS["publish"])
    assert (
        "The build's archive and the release's files are kept as artifacts of the run for five"
        " days: re-running only the failed jobs of a run works within them; after them,"
        " `publish` still finishes when the registry already holds the digest, and a job re-run"
        " alone that needs an artifact fails" in ci
    )
    # (What a tag's run recovers after them: test_ci_retry_limits_unit.)
    kept = {
        s["with"]["name"]: s["with"]["retention-days"]
        for job in JOBS.values()
        for s in job["steps"]
        if s.get("uses", "").startswith("actions/upload-artifact")
    }
    assert kept == {"image": 5, "release": 5}


def test_the_readme_lists_the_scans_and_the_quick_checks_in_items_1_and_2():
    ci = flat(README[README.index("## CI/CD") : README.index("## Planned")])
    item_1 = ci.split("1. **")[1].split("2. **")[0]
    for tool in ("pip-audit", "Trivy", "Bandit", "gitleaks"):
        assert tool in item_1, tool
    assert "`build-constraints.txt`" in item_1 and "redacted" in item_1
    item_2 = ci.split("2. **")[1].split("3. **")[0]
    assert "`infra/quick/aws`, `infra/quick/gcp`" in item_2 and "job `quick`" in item_2


def test_aliases_alone_re_run_in_any_release_s_run_does_what_a_superseded_one_would_have():
    """A waiting `aliases` replaced by a newer one that then fails leaves the aliases as they
    were. Its step reads nothing of the run that started it (no `needs` output, ref or commit),
    only the releases and the registry as they are, so re-running that job alone in any
    release's run moves every alias where it belongs; the README says so."""
    point = next(s for s in JOBS["aliases"]["steps"] if "gh release list" in s.get("run", ""))
    said = yaml.safe_dump(point)
    for run_s_own in ("needs.", "GITHUB_REF", "GITHUB_SHA", "github.ref", "github.sha"):
        assert run_s_own not in said, run_s_own
    ci = flat(README[README.index("## CI/CD") : README.index("## Planned")])
    assert (
        "When a waiting `aliases` is replaced by a newer one that then fails, `latest` and"
        " `X.Y` stay where they were: re-run the job `aliases` alone in any release's run, which"
        " decides from the releases and the registry as they are then and so moves every alias"
        " where it belongs." in ci
    )
    # It moves latest first and then each X.Y, so a stop between them leaves a part done.
    run_ = point["run"]
    assert run_.index("point latest") < run_.index("for line in")
    assert (
        "Job `aliases` moves `latest` first and then each `X.Y`: if it stops in between,"
        " `latest` has moved and an `X.Y` may not have, and running it again finishes the job."
        in ci
    )
    assert (
        "A run re-run later keeps the workflow of its own commit, and with it the cap that"
        " commit had." in ci
    )
