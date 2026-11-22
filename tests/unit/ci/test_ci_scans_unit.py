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

from support.ci.ci_workflow import JOBS, RAW_WORKFLOW, ancestors, needs
from support.ci_shell import run_step
from support.paths import REPO_ROOT

REPO = REPO_ROOT
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
    assert isinstance(job.get("timeout-minutes"), int)
    if name == "image_scan":
        # Runs once image_build succeeds (skipped when docs/tests-only PRs skip image_build).
        assert needs(job) == {"image_build"}
        assert job["if"] == "needs.image_build.result == 'success'"
        assert needs(JOBS["image_build"]) == {"changes"}
        assert JOBS["image_build"]["if"] == "needs.changes.outputs.image != 'false'"
    else:
        assert "if" not in job and needs(job) == set()
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


def test_bandit_reads_src_and_scripts_at_medium_severity_and_confidence_with_its_configuration():
    text = " ".join(runs("static_analysis").split())
    assert (
        '"$RUNNER_TEMP/scan/bin/bandit" -c pyproject.toml -r src'
        " --severity-level medium --confidence-level medium" in text
    )
    assert (
        '"$RUNNER_TEMP/scan/bin/bandit" -c .github/scanners/bandit-scripts.toml'
        " -r scripts .github/scanners --severity-level medium --confidence-level medium" in text
    )
    assert "tests" not in text
    scripts_cfg = tomllib.loads((SCANNERS / "bandit-scripts.toml").read_text(encoding="utf-8"))
    assert scripts_cfg == {"tool": {"bandit": {"skips": ["B104", "B608"]}}}


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


# --- pinned CLI tools (.github/tool-pins.conf) & composite installer -------------------------

PINS_FILE = REPO / ".github" / "tool-pins.conf"
INSTALL_ACTION = yaml.safe_load(
    (REPO / ".github" / "actions" / "install-pinned-binary" / "action.yml").read_text(
        encoding="utf-8"
    )
)
STAGE_ACTION = yaml.safe_load(
    (REPO / ".github" / "actions" / "stage-oci-image" / "action.yml").read_text(encoding="utf-8")
)


def load_tool_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in PINS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, val = line.split("=", 1)
        pins[key] = val
    return pins


PINS = load_tool_pins()


@pytest.mark.parametrize("prefix", ["GITLEAKS", "ORAS", "TRIVY", "ACTIONLINT"])
def test_tool_pins_conf_defines_both_arches_and_sha256_digests(prefix):
    for suffix in (
        "VERSION",
        "REPO",
        "BIN",
        "X64_ASSET",
        "X64_SHA256",
        "ARM64_ASSET",
        "ARM64_SHA256",
    ):
        assert PINS.get(f"{prefix}_{suffix}"), f"{prefix}_{suffix}"
    for arch in ("X64", "ARM64"):
        assert re.fullmatch(r"[0-9a-f]{64}", PINS[f"{prefix}_{arch}_SHA256"])


def _uses_composite(job_name: str, action: str) -> list[dict]:
    return [s for s in JOBS[job_name]["steps"] if s.get("uses") == f"./.github/actions/{action}"]


def test_non_privileged_jobs_install_binaries_via_pinned_composite_action():
    assert [s["with"] for s in _uses_composite("secrets", "install-pinned-binary")] == [
        {"tool": "gitleaks"}
    ]
    assert [s["with"] for s in _uses_composite("lint", "install-pinned-binary")] == [
        {"tool": "actionlint"}
    ]
    assert [s["with"] for s in _uses_composite("image_build", "install-pinned-binary")] == [
        {"tool": "oras"}
    ]
    assert [s["with"] for s in _uses_composite("image_scan", "install-pinned-binary")] == [
        {"tool": "trivy"}
    ]
    for job_name in ("image_scan", "image", "stack", "host"):
        assert _uses_composite(job_name, "stage-oci-image"), job_name
    assert [
        s["with"]
        for s in STAGE_ACTION["runs"]["steps"]
        if s.get("uses") == "./.github/actions/install-pinned-binary"
    ] == [{"tool": "oras"}]


def test_privileged_oras_users_match_tool_pins_conf_with_zero_checkout():
    base = f"https://github.com/{PINS['ORAS_REPO']}/releases/download/v{PINS['ORAS_VERSION']}/"
    for job_name in ("publish", "aliases"):
        steps = JOBS[job_name]["steps"]
        assert not any(s.get("uses", "").startswith("actions/checkout") for s in steps)
        assert not any(s.get("uses", "").startswith("./.github/actions/") for s in steps)
        (install,) = [s for s in steps if s.get("name") == "Install oras"]
        run = install["run"]
        assert f'base="{base}"' in run
        for arch in ("X64", "ARM64"):
            asset = PINS[f"ORAS_{arch}_ASSET"]
            digest = PINS[f"ORAS_{arch}_SHA256"]
            assert f"asset={asset} sum={digest}" in run
        assert "sha256sum --check --strict" in run


def run_composite_install(tmp_path: Path, tool: str, arch: str, tamper: bool = False):
    """Run install-pinned-binary's bash step against a local temporary pins copy."""
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    archive, made_up = tmp_path / "made-up.tar.gz", tmp_path / "made-up"
    made_up.mkdir(exist_ok=True)
    tool_bin = PINS[f"{tool.upper()}_BIN"]
    (made_up / tool_bin).write_text("#!/bin/sh\necho made up\n")
    subprocess.run(["tar", "-czf", str(archive), "-C", str(made_up), tool_bin], check=True)
    (bin_ / "curl").write_text(
        f'#!/bin/sh\nwhile [ "$1" != -o ]; do shift; done\ncp "{archive}" "$2"\n'
    )
    (bin_ / "sha256sum").write_text(
        '#!/bin/sh\nif [ -x /usr/bin/sha256sum ]; then exec /usr/bin/sha256sum "$@"; fi\n'
        'exec shasum -a 256 "$@"\n'
    )
    for name in ("curl", "sha256sum"):
        (bin_ / name).chmod(0o755)

    pins_copy = tmp_path / "tool-pins.conf"
    pins_text = PINS_FILE.read_text(encoding="utf-8")
    if not tamper:
        made = subprocess.run(
            [str(bin_ / "sha256sum"), str(archive)], capture_output=True, text=True, check=True
        ).stdout.split()[0]
        prefix = tool.upper()
        for a in ("X64", "ARM64"):
            pins_text = pins_text.replace(PINS[f"{prefix}_{a}_SHA256"], made)
    pins_copy.write_text(pins_text, encoding="utf-8")

    (step,) = INSTALL_ACTION["runs"]["steps"]
    assert step["env"]["PINS_FILE"] == ".github/tool-pins.conf"
    return run_step(
        step["run"],
        env={
            "PATH": f"{bin_}:/usr/bin:/bin",
            "TOOL": tool,
            "PINS_FILE": str(pins_copy),
            "RUNNER_TEMP": str(tmp_path),
            "RUNNER_ARCH": arch,
            "GITHUB_PATH": str(tmp_path / "path"),
        },
    )


@pytest.mark.parametrize("tool", ["gitleaks", "oras", "trivy", "actionlint"])
@pytest.mark.parametrize("arch", ["X64", "ARM64"])
def test_an_archive_whose_digest_differs_is_never_unpacked(tmp_path, tool, arch):
    done = run_composite_install(tmp_path, tool, arch, tamper=True)
    assert done.returncode != 0
    tool_bin = PINS[f"{tool.upper()}_BIN"]
    assert not (tmp_path / tool / tool_bin).exists()


@pytest.mark.parametrize("tool", ["gitleaks", "oras", "trivy", "actionlint"])
def test_the_checked_archive_is_unpacked_and_put_on_the_path(tmp_path, tool):
    done = run_composite_install(tmp_path, tool, "X64")
    assert done.returncode == 0, done.stderr
    tool_bin = PINS[f"{tool.upper()}_BIN"]
    assert (tmp_path / tool / tool_bin).is_file()
    assert (tmp_path / "path").read_text() == f"{tmp_path / tool}\n"


def test_a_runner_of_another_architecture_is_refused(tmp_path):
    done = run_composite_install(tmp_path, "gitleaks", "ARM")
    assert (
        done.returncode == 1
        and "::error::no gitleaks archive is pinned for a runner of ARM" in done.stdout
    )


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


HISTORICAL_MOVED_PATHS = frozenset(
    {
        "tests/unit/test_caddy_log_unit.py",
        "tests/unit/test_guard_unit.py",
        "tests/unit/test_portable_unit.py",
        "tests/unit/test_slack_repr_unit.py",
        "tests/unit/test_source_items_unit.py",
        "tests/unit/test_source_url_unit.py",
        "tests/unit/test_quick_bootstrap_unit.py",
    }
)


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
            assert re.fullmatch(r"[\w/.-]+", path), pattern
            if not (REPO / path).is_file():
                assert path in HISTORICAL_MOVED_PATHS, pattern
                sha = subprocess.check_output(
                    ["git", "log", "-1", "--format=%H", "--", path],
                    cwd=REPO,
                    text=True,
                ).strip()
                assert sha, pattern


# --- image_scan: single job scanning both platforms via trivy_policy.py ----------------------

TRIVY_POLICY_SPEC = importlib.util.spec_from_file_location(
    "trivy_policy", SCANNERS / "trivy_policy.py"
)
trivy_policy = importlib.util.module_from_spec(TRIVY_POLICY_SPEC)
TRIVY_POLICY_SPEC.loader.exec_module(trivy_policy)


def test_the_image_scan_scans_both_platforms_of_the_build_and_fails_on_a_fixed_high_or_critical():
    job = JOBS["image_scan"]
    assert "strategy" not in job  # consolidated single-runner scan of both platform digests
    stage = next(s for s in job["steps"] if s.get("uses") == "./.github/actions/stage-oci-image")
    assert stage["with"]["check-copy"] == "true"
    assert stage["with"]["amd64-digest"] == "${{ needs.image_build.outputs.amd64 }}"
    assert stage["with"]["arm64-digest"] == "${{ needs.image_build.outputs.arm64 }}"
    cache = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/cache@"))
    assert cache["with"]["path"] == "~/.cache/trivy"
    text = runs("image_scan")
    assert "docker build" not in text and "docker pull" not in text
    assert not any(s.get("uses", "").startswith("docker/build-push") for s in job["steps"])
    flat = " ".join(text.split())
    remote = "trivy image --image-src remote --insecure"
    assert flat.count("trivy image ") == flat.count(remote) == 2
    for digest_var in ('"localhost:5000/cairn@$AMD64"', '"localhost:5000/cairn@$ARM64"'):
        assert (
            f"{remote} --scanners vuln --severity HIGH,CRITICAL"
            " --ignorefile .github/scanners/trivy-ignore.yaml --format json --no-progress"
            f' --output "$RUNNER_TEMP/' in flat
        )
        assert digest_var in flat
    assert (
        'python3 .github/scanners/trivy_policy.py "$RUNNER_TEMP/trivy-amd64.json"'
        ' "$RUNNER_TEMP/trivy-arm64.json" --ignorefile .github/scanners/trivy-ignore.yaml'
        ' --summary "$GITHUB_STEP_SUMMARY"' in flat
    )
    assert "publish" not in needs(job) and "image_scan" in needs(JOBS["publish"])


def _trivy_json(vulns: list[dict], target: str = "cairn (debian 12)") -> str:
    return json.dumps({"Results": [{"Target": target, "Vulnerabilities": vulns}]})


def test_trivy_policy_is_stdlib_only():
    import ast

    tree = ast.parse((SCANNERS / "trivy_policy.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= sys.stdlib_module_names


def test_trivy_policy_judges_fixed_unfixed_severity_expired_and_malformed(tmp_path, capsys):
    amd = tmp_path / "amd64.json"
    arm = tmp_path / "arm64.json"
    summary = tmp_path / "summary.md"
    ignore = tmp_path / "trivy-ignore.yaml"
    ignore.write_text("vulnerabilities: []\n", encoding="utf-8")

    # Unfixed CRITICAL and fixed MEDIUM pass, and unfixed CRITICAL is summarized.
    amd.write_text(
        _trivy_json(
            [
                {
                    "VulnerabilityID": "CVE-2026-0001",
                    "PkgName": "openssl",
                    "InstalledVersion": "3.0.1",
                    "FixedVersion": "",
                    "Severity": "CRITICAL",
                },
                {
                    "VulnerabilityID": "CVE-2026-0002",
                    "PkgName": "curl",
                    "InstalledVersion": "8.0",
                    "FixedVersion": "8.1",
                    "Severity": "MEDIUM",
                },
            ]
        ),
        encoding="utf-8",
    )
    arm.write_text(_trivy_json([]), encoding="utf-8")
    assert (
        trivy_policy.main(
            [str(amd), str(arm), "--ignorefile", str(ignore), "--summary", str(summary)]
        )
        == 0
    )
    md = summary.read_text(encoding="utf-8")
    assert "CVE-2026-0001" in md and "CVE-2026-0002" not in md

    # Fixed HIGH fails exit 1.
    arm.write_text(
        _trivy_json(
            [
                {
                    "VulnerabilityID": "CVE-2026-9999",
                    "PkgName": "sqlite3",
                    "InstalledVersion": "3.45.0",
                    "FixedVersion": "3.45.1",
                    "Severity": "HIGH",
                }
            ]
        ),
        encoding="utf-8",
    )
    assert (
        trivy_policy.main(
            [str(amd), str(arm), "--ignorefile", str(ignore), "--summary", str(summary)]
        )
        == 1
    )
    err = capsys.readouterr().err
    assert "FAIL HIGH CVE-2026-9999 in sqlite3" in err

    # Expired ignore fails exit 1 even when report is clean.
    ignore.write_text(
        "vulnerabilities:\n  - id: CVE-2020-1111\n    expired_at: 2020-01-01\n", encoding="utf-8"
    )
    assert trivy_policy.expired_ignores(ignore, today=TODAY) == ["CVE-2020-1111"]
    assert trivy_policy.expired_ignores(ignore, today=date(2019, 12, 31)) == []
    amd.write_text(_trivy_json([]), encoding="utf-8")
    arm.write_text(_trivy_json([]), encoding="utf-8")
    assert trivy_policy.main([str(amd), "--ignorefile", str(ignore)]) == 1
    assert "expired Trivy ignore entries: CVE-2020-1111" in capsys.readouterr().err

    # Malformed JSON returns exit 2.
    bad = tmp_path / "bad.json"
    bad.write_text("{not-json", encoding="utf-8")
    assert trivy_policy.main([str(bad)]) == 2
    bad.write_text("{}", encoding="utf-8")
    assert trivy_policy.main([str(bad)]) == 2


def test_nightly_workflow_runs_scheduled_cve_fuzz_and_py312_safely():
    nightly = yaml.safe_load((REPO / ".github" / "workflows" / "nightly.yml").read_text("utf-8"))
    triggers = nightly.get("on", nightly.get(True))
    assert "schedule" in triggers and "workflow_dispatch" in triggers
    assert nightly["permissions"] == {"contents": "read"}
    jobs = nightly["jobs"]
    assert set(jobs) == {"dependencies", "image_scan", "fuzz_full", "test_py312"}
    for name, job in jobs.items():
        assert job["permissions"] == {"contents": "read"}, name
        assert isinstance(job.get("timeout-minutes"), int), name
        for step in job["steps"]:
            uses = step.get("uses")
            if uses and not uses.startswith("./"):
                _, ref = uses.split("@", 1)
                assert re.fullmatch(r"[0-9a-f]{40}", ref), (name, uses)
    assert jobs["fuzz_full"]["steps"][-1]["env"] == {
        "CAIRN_FUZZ_PROFILE": "full",
        "RT2_FULL": "1",
    }
    assert "--python 3.12" in "\n".join(s.get("run", "") for s in jobs["test_py312"]["steps"])
    assert "trivy_policy.py" in "\n".join(s.get("run", "") for s in jobs["image_scan"]["steps"])


def test_the_header_names_the_four_scans():
    header = " ".join(RAW_WORKFLOW.split("\nname:")[0].replace("# ", "").split())
    assert (
        "Four scans run on every pull request and every push, drafts and changes of documents"
        " only included, and `publish` needs each" in header
    )
