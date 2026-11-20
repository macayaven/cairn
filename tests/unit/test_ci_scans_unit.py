"""The gate scans what it ships: four jobs of .github/workflows/ci-cd.yml that every pull
request and every push runs, each needed by `publish`, each reading the repository and
nothing more, each running a tool pinned to one version.

- `dependencies`: pip-audit on uv.lock (exported with its hashes) and build-constraints.txt;
  a known vulnerability with a fixed version fails the job (.github/scanners/audit_policy.py).
- `image_scan`: Trivy on both platforms of the one build `image_build` made, each pulled by the
  index's digest from a registry on the runner; HIGH or CRITICAL with a fix fails, the unfixed
  are listed in the job's summary.
- `static_analysis`: Bandit over src/ at medium severity and medium confidence.
- `secrets`: gitleaks over the whole history checked out, redacted.

Each ignore list says why and until when; a test here fails once that day has passed."""

import importlib.util
import json
import re
import subprocess
import sys
import tomllib
from datetime import date
from pathlib import Path

import pytest
import yaml
from test_ci_workflow_unit import JOBS, RAW_WORKFLOW, ancestors, needs

REPO = Path(__file__).resolve().parents[2]
SCANNERS = REPO / ".github" / "scanners"
_spec = importlib.util.spec_from_file_location("audit_policy", SCANNERS / "audit_policy.py")
policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(policy)

SCANS = ("dependencies", "image_scan", "static_analysis", "secrets")
TODAY = date(2026, 11, 19)


# --- what pip-audit found, and what fails the gate -------------------------------------------


def report(*vulns: tuple[str, str, str, list[str]], aliases=()) -> dict:
    """A pip-audit JSON report: (name, version, id, fix_versions) for each vulnerability."""
    dependencies = {}
    for name, version, ident, fixes in vulns:
        entry = dependencies.setdefault(name, {"name": name, "version": version, "vulns": []})
        entry["vulns"].append(
            {"id": ident, "fix_versions": fixes, "aliases": list(aliases), "description": "x"}
        )
    dependencies.setdefault("clean", {"name": "clean", "version": "1.0", "vulns": []})
    return {"dependencies": list(dependencies.values()), "fixes": []}


def judge(tmp_path, found: dict, ignores: str = "ignore = []\n", today: date = TODAY):
    (tmp_path / "audit.json").write_text(json.dumps(found))
    (tmp_path / "ignore.toml").write_text(ignores)
    summary = tmp_path / "summary.md"
    done = subprocess.run(
        [
            sys.executable,
            str(SCANNERS / "audit_policy.py"),
            str(tmp_path / "audit.json"),
            str(tmp_path / "ignore.toml"),
            "--today",
            today.isoformat(),
            "--summary",
            str(summary),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode, done.stdout, summary.read_text() if summary.exists() else ""


def test_a_vulnerability_with_a_fixed_version_fails_the_gate(tmp_path):
    code, out, summary = judge(tmp_path, report(("leaky", "1.0", "PYSEC-1", ["1.1", "2.0"])))
    assert code == 1
    assert "::error::leaky 1.0: PYSEC-1 is fixed in 1.1, 2.0" in out
    assert "PYSEC-1" in summary


def test_a_vulnerability_without_a_fix_is_listed_and_does_not_fail(tmp_path):
    code, out, summary = judge(tmp_path, report(("leaky", "1.0", "GHSA-a", [])))
    assert code == 0
    assert "::warning::leaky 1.0: GHSA-a has no fixed version yet" in out
    assert "GHSA-a" in summary and "no fixed version" in summary


def test_nothing_found_passes_and_says_how_many_were_audited(tmp_path):
    code, out, _ = judge(tmp_path, report())
    assert code == 0 and "1 dependencies audited: none has a known vulnerability" in out


IGNORED = """
[[ignore]]
id = "PYSEC-1"
reason = "Reached only through a feature cairn does not use."
until = 2026-12-31
"""


def test_an_ignored_vulnerability_passes_until_its_day_and_fails_after_it(tmp_path):
    found = report(("leaky", "1.0", "PYSEC-1", ["1.1"]))
    code, out, summary = judge(tmp_path, found, IGNORED)
    assert code == 0
    assert "leaky 1.0: PYSEC-1 is ignored until 2026-12-31" in out
    assert "Reached only through a feature cairn does not use." in summary
    code, out, _ = judge(tmp_path, found, IGNORED, today=date(2027, 1, 1))
    assert code == 1
    assert "::error::the ignore entry for PYSEC-1 ended on 2026-12-31" in out


def test_an_ignore_entry_matches_an_alias_too(tmp_path):
    found = report(("leaky", "1.0", "GHSA-b", ["1.1"]), aliases=("PYSEC-1",))
    assert judge(tmp_path, found, IGNORED)[0] == 0


@pytest.mark.parametrize(
    ("entry", "said"),
    [
        ('id = "X-1"\nuntil = 2026-12-31\n', "the ignore entry for X-1 gives no reason"),
        (
            'id = "X-1"\nreason = " "\nuntil = 2026-12-31\n',
            "the ignore entry for X-1 gives no reason",
        ),
        ('id = "X-1"\nreason = "r"\n', "the ignore entry for X-1 has no date `until`"),
        (
            'id = "X-1"\nreason = "r"\nuntil = "2026-12-31"\n',
            "the ignore entry for X-1 has no date",
        ),
        ('reason = "r"\nuntil = 2026-12-31\n', "an ignore entry has no id"),
        ('id = "X-1"\nreason = "r"\nuntil = 2026-12-31\nwhy = "x"\n', "unknown keys: why"),
    ],
)
def test_an_ignore_entry_without_its_reason_or_its_date_fails(tmp_path, entry, said):
    code, out, _ = judge(tmp_path, report(), f"[[ignore]]\n{entry}")
    assert code == 1 and said in out


def test_a_report_that_is_not_pip_audits_fails(tmp_path):
    (tmp_path / "audit.json").write_text("not json")
    (tmp_path / "ignore.toml").write_text("ignore = []\n")
    done = subprocess.run(
        [
            sys.executable,
            str(SCANNERS / "audit_policy.py"),
            *(str(tmp_path / n) for n in ("audit.json", "ignore.toml")),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 1 and "::error::" in done.stdout


def test_the_policy_needs_nothing_but_the_standard_library():
    source = (SCANNERS / "audit_policy.py").read_text(encoding="utf-8")
    imported = {
        line.split()[1].split(".")[0]
        for line in source.splitlines()
        if line.startswith(("import ", "from "))
    }
    assert imported <= {"__future__", "argparse", "datetime", "json", "os", "sys", "tomllib"}


# --- the ignore lists: each entry says why, and ends ------------------------------------------


def test_every_dependency_ignore_says_why_and_has_not_ended():
    """Fails on the day after an entry's date: an ignore cannot stay forever."""
    entries = policy.load_ignores(SCANNERS / "pip-audit-ignore.toml", date.today())
    for entry in entries:
        assert entry["reason"].strip() and entry["until"] >= date.today(), entry


def test_every_image_ignore_says_why_and_has_not_ended():
    data = yaml.safe_load((SCANNERS / "trivy-ignore.yaml").read_text(encoding="utf-8"))
    assert set(data) == {"vulnerabilities"}
    for entry in data["vulnerabilities"] or []:
        assert set(entry) <= {"id", "statement", "expired_at", "paths", "purls"}, entry
        assert entry["id"] and str(entry.get("statement", "")).strip(), entry
        assert isinstance(entry.get("expired_at"), date), entry
        assert entry["expired_at"] >= date.today(), entry


def test_both_ignore_lists_start_empty():
    assert policy.load_ignores(SCANNERS / "pip-audit-ignore.toml", TODAY) == []
    data = yaml.safe_load((SCANNERS / "trivy-ignore.yaml").read_text(encoding="utf-8"))
    assert data["vulnerabilities"] == []


# --- the four jobs ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", SCANS)
def test_each_scan_is_a_job_publish_needs_reading_the_repository_and_nothing_more(name):
    job = JOBS[name]
    assert name in needs(JOBS["publish"])
    assert name in ancestors("publish")
    assert job["permissions"] == {"contents": "read"}
    # The image scan needs the build it scans, which also runs on every change.
    assert "if" not in job and needs(job) <= {"image_build"}
    assert "if" not in JOBS["image_build"] and "needs" not in JOBS["image_build"]
    configuration = yaml.safe_dump(job)
    assert "secrets." not in configuration and "github.token" not in configuration
    assert "environment" not in job
    checkout = job["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["persist-credentials"] is False
    assert job["runs-on"] == JOBS["test"]["runs-on"]


def runs(name: str) -> str:
    """The job's shell lines, with their continuations joined."""
    text = "\n".join(step.get("run", "") for step in JOBS[name]["steps"])
    return text.replace("\\\n", " ")


def requirements(name: str) -> dict[str, list[str]]:
    """A hashed requirements file under .github/scanners as {name==version: [hashes]}."""
    text = (SCANNERS / name).read_text(encoding="utf-8").replace("\\\n", " ")
    found = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            pinned, _, rest = line.partition(" --hash=")
            options = re.findall(r"--hash=(\S+)", "--hash=" + rest)
            assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", o) for o in options), line
            found[pinned.split(";")[0].strip()] = options
    return found


@pytest.mark.parametrize(
    ("job", "tool", "version"),
    [("dependencies", "pip-audit", "2.10.1"), ("static_analysis", "bandit", "1.9.4")],
)
def test_the_python_scanners_are_installed_by_hash_at_one_version(job, tool, version):
    pinned = requirements(f"{tool}.txt")
    assert f"{tool}=={version}" in pinned
    for requirement, hashes in pinned.items():
        assert re.fullmatch(r"[a-z0-9_.-]+==\d+(\.\d+)*", requirement), requirement
        assert hashes, requirement
    install = " ".join(runs(job).split())
    assert f"--require-hashes -r .github/scanners/{tool}.txt" in install
    uv = next(s for s in JOBS[job]["steps"] if s.get("uses", "").startswith("astral-sh/setup-uv"))
    assert uv["with"] == {"version": "0.11.19", "enable-cache": False, "github-token": ""}


def test_pip_audit_reads_the_lock_with_hashes_and_the_build_constraints():
    text = " ".join(runs("dependencies").split())
    assert (
        "uv export --frozen --format requirements.txt --all-extras --all-groups"
        ' --no-emit-project --no-header -o "$RUNNER_TEMP/locked.txt"' in text
    )
    assert (
        '"$RUNNER_TEMP/scan/bin/pip-audit" --disable-pip --require-hashes --strict'
        ' --progress-spinner off -r "$RUNNER_TEMP/locked.txt" -r build-constraints.txt'
        ' --format json -o "$RUNNER_TEMP/audit.json"' in text
    )
    assert (
        'python3 .github/scanners/audit_policy.py "$RUNNER_TEMP/audit.json"'
        ' .github/scanners/pip-audit-ignore.toml --summary "$GITHUB_STEP_SUMMARY"' in text
    )


def test_bandit_reads_src_at_medium_severity_and_confidence_with_its_configuration():
    text = " ".join(runs("static_analysis").split())
    assert (
        '"$RUNNER_TEMP/scan/bin/bandit" -c pyproject.toml -r src'
        " --severity-level medium --confidence-level medium" in text
    )
    assert "tests" not in text


def test_bandit_switches_off_one_rule_and_says_why():
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    config = tomllib.loads(text)["tool"]["bandit"]
    assert config == {"skips": ["B608"]}
    part = text[text.index("[tool.bandit]") :]
    part = " ".join(part[: part.index("skips")].replace("#", "").split())
    assert "B608" in part and "every value" in part and "parameters" in part


NOSEC = re.compile(r"#\s*nosec\b(?P<rest>.*)$")


def test_every_silenced_line_names_its_rule_and_says_why():
    silenced = []
    for path in sorted((REPO / "src").rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if found := NOSEC.search(line):
                silenced.append((path.relative_to(REPO).as_posix(), number))
                assert re.fullmatch(r" B\d{3} # \S.{9,}", found["rest"]), (path, number, line)
    assert len(silenced) == 8, silenced


# --- the two binaries: one version, checked by its SHA-256 before it runs ---------------------

BINARIES = {
    "secrets": (
        "gitleaks",
        "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/",
        {
            "gitleaks_8.30.1_linux_x64.tar.gz": (
                "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
            ),
            "gitleaks_8.30.1_linux_arm64.tar.gz": (
                "e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080"
            ),
        },
    ),
    "image_scan": (
        "trivy",
        "https://github.com/aquasecurity/trivy/releases/download/v0.76.2/",
        {
            "trivy_0.76.2_Linux-64bit.tar.gz": (
                "bc0fc726e017b8927a62b2d3531d7a84ee01769daf5c99ed866dced54089fdbf"
            ),
            "trivy_0.76.2_Linux-ARM64.tar.gz": (
                "ab9d51f86e6a5fb936f605faff6b593ac51bc378fff4d3e8b9d58358b9c367c3"
            ),
        },
    ),
    # Read from the release's oras_1.3.6_checksums.txt on 2026-11-19.
    "publish": (
        "oras",
        "https://github.com/oras-project/oras/releases/download/v1.3.6/",
        {
            "oras_1.3.6_linux_amd64.tar.gz": (
                "66fb0ad7206f3f6fa77ed5b552049d364ccf4b50b1684554f731f76302b52ca0"
            ),
            "oras_1.3.6_linux_arm64.tar.gz": (
                "b84269e78dfc2d5329a4aab373da3200de95e6c138338cf6c66c10c0a3f248c1"
            ),
        },
    ),
}


def test_every_job_that_uses_oras_installs_it_with_the_same_step():
    users = [
        name
        for name, job in JOBS.items()
        if any(re.search(r"\boras\b", s.get("run", "")) for s in job["steps"])
    ]
    assert set(users) == {"image_scan", "image", "publish", "aliases"}
    steps = [
        next(s for s in JOBS[name]["steps"] if s.get("name") == "Install oras") for name in users
    ]
    assert all(step == steps[0] for step in steps)


def install_step(job: str) -> dict:
    tool = BINARIES[job][0]
    (step,) = [s for s in JOBS[job]["steps"] if s.get("name") == f"Install {tool}"]
    return step


@pytest.mark.parametrize("job", BINARIES)
def test_each_binary_is_one_release_checked_by_its_sha256(job):
    _, base, assets = BINARIES[job]
    run = install_step(job)["run"]
    assert f'base="{base}"' in run
    for asset, digest in assets.items():
        assert f"asset={asset} sum={digest}" in run
    assert "sha256sum --check --strict" in run


def run_install(tmp_path, job: str, arch: str, tamper: bool = False):
    """The install step as bash runs it, with a curl that writes a made-up archive whose
    SHA-256 is the pinned one only if the step checks a different file."""
    tool, _, assets = BINARIES[job]
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    archive, made_up = tmp_path / "made-up.tar.gz", tmp_path / "made-up"
    made_up.mkdir()
    (made_up / tool).write_text("#!/bin/sh\necho made up\n")
    subprocess.run(["tar", "-czf", str(archive), "-C", str(made_up), tool], check=True)
    (bin_ / "curl").write_text(
        f'#!/bin/sh\nwhile [ "$1" != -o ]; do shift; done\ncp "{archive}" "$2"\n'
    )
    (bin_ / "sha256sum").write_text(
        '#!/bin/sh\nif [ -x /usr/bin/sha256sum ]; then exec /usr/bin/sha256sum "$@"; fi\n'
        'exec shasum -a 256 "$@"\n'
    )
    for name in ("curl", "sha256sum"):
        (bin_ / name).chmod(0o755)
    run = install_step(job)["run"]
    if not tamper:  # pin the made-up archive's own digest in place of the real one
        made = subprocess.run(
            [str(bin_ / "sha256sum"), str(archive)], capture_output=True, text=True, check=True
        ).stdout.split()[0]
        for digest in assets.values():
            run = run.replace(digest, made)
    return subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", run],
        env={
            "PATH": f"{bin_}:/usr/bin:/bin",
            "RUNNER_TEMP": str(tmp_path),
            "RUNNER_ARCH": arch,
            "GITHUB_PATH": str(tmp_path / "path"),
        },
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("job", BINARIES)
@pytest.mark.parametrize("arch", ["X64", "ARM64"])
def test_an_archive_whose_digest_differs_is_never_unpacked(tmp_path, job, arch):
    done = run_install(tmp_path, job, arch, tamper=True)
    assert done.returncode != 0
    assert not (tmp_path / BINARIES[job][0] / BINARIES[job][0]).exists()


@pytest.mark.parametrize("job", BINARIES)
def test_the_checked_archive_is_unpacked_and_put_on_the_path(tmp_path, job):
    done = run_install(tmp_path, job, "X64")
    assert done.returncode == 0, done.stderr
    tool = BINARIES[job][0]
    assert (tmp_path / tool / tool).is_file()
    assert (tmp_path / "path").read_text() == f"{tmp_path / tool}\n"


def test_a_runner_of_another_architecture_is_refused(tmp_path):
    done = run_install(tmp_path, "secrets", "ARM")
    assert done.returncode == 1 and "::error::" in done.stdout


# --- secrets: the whole history, redacted -----------------------------------------------------


def test_the_secret_scan_reads_the_whole_history_redacted_and_fails_on_a_match():
    checkout = JOBS["secrets"]["steps"][0]
    assert checkout["with"] == {"persist-credentials": False, "fetch-depth": 0}
    (scan,) = [s for s in JOBS["secrets"]["steps"] if "gitleaks git" in s.get("run", "")]
    words = scan["run"].split()
    assert words[:2] == ["gitleaks", "git"]
    assert "--redact" in words and not any(w.startswith("--redact=") for w in words)
    assert "--ignore-gitleaks-allow" in words
    assert words[words.index("--config") + 1] == ".gitleaks.toml"
    assert words[words.index("--exit-code") + 1] == "1"
    assert "--report-path" not in words  # nothing but the redacted log leaves the job


def test_every_allowlist_names_its_rules_and_its_exact_files():
    config = tomllib.loads((REPO / ".gitleaks.toml").read_text(encoding="utf-8"))
    assert config["extend"] == {"useDefault": True}
    assert set(config) == {"extend", "allowlists"}
    for entry in config["allowlists"]:
        assert set(entry) == {"description", "targetRules", "paths"}, entry
        assert entry["description"].strip() and entry["targetRules"], entry
        for pattern in entry["paths"]:
            assert pattern.startswith("^") and pattern.endswith("$"), pattern
            path = pattern[1:-1].replace("\\.", ".")
            assert re.fullmatch(r"[\w/.-]+", path) and (REPO / path).is_file(), pattern


# --- the image: both platforms of image_build's build, scanned before anything is published ----


def test_the_image_scan_scans_both_platforms_of_the_build_and_fails_on_a_fixed_high_or_critical():
    job = JOBS["image_scan"]
    platforms = [entry["platform"] for entry in job["strategy"]["matrix"]["include"]]
    assert sorted(platforms) == ["linux/amd64", "linux/arm64"]
    assert job["env"] == {
        "IMAGE": "localhost:5000/cairn@${{ needs.image_build.outputs[matrix.arch] }}",
        "PLATFORM": "${{ matrix.platform }}",
        "ARCH": "${{ matrix.arch }}",
    }
    text = runs("image_scan")
    # It builds nothing and pulls nothing into the engine: Trivy reads this leg's platform of
    # the build from the registry on the runner, by that platform's own manifest digest (Trivy
    # loads a linux/amd64 image unless told otherwise, and ignores --platform for an image that
    # is one platform already, so the digest is what decides).
    assert "docker build" not in text and "docker pull" not in text
    assert not any(s.get("uses", "").startswith("docker/build-push") for s in job["steps"])
    flat = " ".join(text.split())
    remote = "trivy image --image-src remote --insecure"
    assert (
        f"{remote} --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed"
        ' --ignorefile .github/scanners/trivy-ignore.yaml --exit-code 1 --no-progress "$IMAGE"'
        in flat
    )
    # The unfixed are listed in the job's summary and fail nothing.
    assert (
        f"{remote} --scanners vuln --severity HIGH,CRITICAL --ignore-status fixed"
        " --ignorefile .github/scanners/trivy-ignore.yaml --exit-code 0 --no-progress"
        ' --format table "$IMAGE"' in flat
    )
    assert flat.count("trivy image ") == flat.count(remote) == 2
    assert '>> "$GITHUB_STEP_SUMMARY"' in text
    assert "publish" not in needs(job) and "image_scan" in needs(JOBS["publish"])


def test_the_header_names_the_four_scans():
    header = " ".join(RAW_WORKFLOW.split("\nname:")[0].replace("# ", "").split())
    assert (
        "Four scans run on every pull request and every push, drafts and changes of documents"
        " only included, and `publish` needs each" in header
    )
