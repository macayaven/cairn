"""What deploy/README.md says about the shipped deploy files, checked against the files.

The stack itself runs only in CI (scripts/rehearse_stack.py). These checks need no Docker:
they read the compose files, litestream.yml, .gitignore and the guide, and run the
rehearsal's guard and its guide-reading helpers on their own.
"""

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
GUIDE = (DEPLOY / "README.md").read_text(encoding="utf-8")

SCRIPT = ROOT / "scripts" / "rehearse_stack.py"
spec = importlib.util.spec_from_file_location("rehearse_stack_for_deploy_files", SCRIPT)
rehearse = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rehearse)

NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}


def _load(name: str) -> dict:
    return yaml.safe_load((DEPLOY / name).read_text(encoding="utf-8"))


def _section(heading: str) -> str:
    """The guide's text from `heading` to the next heading of the same or a higher level."""
    level = heading.split(" ", 1)[0]
    start = GUIDE.index(f"\n{heading}\n")
    ends = [
        match.start()
        for match in re.finditer(r"\n(#+) ", GUIDE[start + 1 :])
        if len(match.group(1)) <= len(level)
    ]
    return GUIDE[start : start + 1 + ends[0]] if ends else GUIDE[start:]


def _seconds(duration: str) -> int:
    """A Litestream duration with one unit (s, m or h), in seconds."""
    match = re.fullmatch(r"(\d+)([smh])", duration)
    assert match, f"not a one-unit duration: {duration!r}"
    return int(match.group(1)) * {"s": 1, "m": 60, "h": 3600}[match.group(2)]


# -- 1. the restore cairn waits for --


def test_cairn_starts_only_after_a_successful_restore():
    """A failed restore must keep cairn down: an empty database would be replicated over the
    good backup. Compose enforces that only for a required dependency on a completed step."""
    restore = _load("compose.backup.yaml")["services"]["cairn"]["depends_on"]["litestream-restore"]
    assert restore["condition"] == "service_completed_successfully"
    assert restore["required"] is True


def test_compose_yaml_alone_has_no_litestream_and_no_dependency_on_it():
    services = _load("compose.yaml")["services"]
    assert not any("litestream" in name for name in services)
    assert "depends_on" not in services["cairn"]


# -- 2. what Litestream keeps --


def test_litestream_keeps_what_the_guide_says():
    backups = _section("## Backups")
    every = re.search(r"uploads changes every (\d+) seconds", backups)
    apart = re.search(
        r"(\d+) seconds apart for the last (\w+) minutes and (\d+) seconds apart before that",
        backups,
    )
    days = re.search(r"in the last (\w+) days", backups)
    assert every and apart and days, "the Backups section no longer gives these numbers"

    config = _load("litestream.yml")
    (db,) = config["dbs"]
    sync = _seconds(db["replica"]["sync-interval"])
    assert sync == int(every.group(1))
    # Level 0 holds one file per sync, kept for l0-retention; level 1 is the next spacing.
    assert int(apart.group(1)) == sync
    assert _seconds(config["l0-retention"]) == NUMBERS[apart.group(2)] * 60
    assert _seconds(config["levels"][0]["interval"]) == int(apart.group(3))
    # The oldest snapshot left is retention minus one snapshot interval old at the least.
    interval = _seconds(config["snapshot"]["interval"])
    retention = _seconds(config["snapshot"]["retention"])
    assert retention - interval >= NUMBERS[days.group(1)] * 86400
    assert (interval, retention) == (24 * 3600, 192 * 3600)


# -- 3. secrets stay out of git --


@pytest.mark.parametrize(
    "path", ["deploy/.env", "deploy/cairn.env", "deploy/litestream.env", "deploy/gcs-key.json"]
)
def test_every_secret_file_the_guide_names_is_ignored_by_git(path):
    assert path.split("/", 1)[1] in GUIDE
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT, check=False
    )
    assert result.returncode == 0, f"{path} is not ignored by git"


# -- 4. the Google Cloud Storage snippet --


def test_the_gcs_snippet_gives_both_litestream_services_the_key():
    blocks = re.findall(r"```yaml\n(.*?)```", GUIDE, re.S)
    (snippet,) = [block for block in blocks if "compose.gcs.yaml" in block]
    services = yaml.safe_load(snippet)["services"]
    backup = _load("compose.backup.yaml")["services"]
    assert set(services) == {"litestream", "litestream-restore"} <= set(backup)
    for name, service in services.items():
        assert "./gcs-key.json:/gcs-key.json:ro" in service["volumes"], name
        assert service["environment"]["GOOGLE_APPLICATION_CREDENTIALS"] == "/gcs-key.json", name
    assert "COMPOSE_FILE=compose.yaml:compose.backup.yaml:compose.gcs.yaml" in GUIDE


# -- 5. the rehearsal never runs over an operator's own files --


@pytest.mark.parametrize(
    "name", [".env", "cairn.env", "litestream.env", "laptop.env", "plans.yaml"]
)
def test_the_rehearsal_refuses_to_start_beside_an_operators_file(name, tmp_path, monkeypatch):
    own = tmp_path / name
    own.write_text("CAIRN_DOMAIN=cairn.example.net\n", encoding="utf-8")

    def no_docker(*args, **kwargs):
        raise AssertionError(f"the rehearsal ran {args[:3]} beside deploy/{name}")

    monkeypatch.setattr(rehearse, "DEPLOY", tmp_path)
    monkeypatch.setattr(rehearse, "run", no_docker)
    monkeypatch.setattr(rehearse.subprocess, "run", no_docker)
    rehearsal = rehearse.Rehearsal("cairn:test")
    try:
        with pytest.raises(rehearse.Failed, match=re.escape(f"deploy/{name} exists")):
            rehearsal.starts_the_shipped_stack()
    finally:
        rehearse.shutil.rmtree(rehearsal.tmp, ignore_errors=True)

    assert own.read_text(encoding="utf-8") == "CAIRN_DOMAIN=cairn.example.net\n"
    assert sorted(path.name for path in tmp_path.iterdir()) == [name]


# -- the guide's commands, as the rehearsal reads them --


def test_the_restore_by_hand_commands_are_read_from_the_guide():
    commands = rehearse.guide_block(GUIDE, "### Restore by hand")
    assert [command.split()[:3] for command in commands] == [
        ["docker", "compose", "down"],
        ["docker", "compose", "run"],
        ["docker", "compose", "up"],
    ]
    assert "\\\n" not in commands[1] and "mv cairn.db" in commands[1]


def test_the_compaction_with_the_replica_stopped_is_four_commands_the_rehearsal_reads():
    commands = rehearse.guide_block(GUIDE, "### After a workspace is deleted")
    assert commands == [
        "docker compose stop litestream",
        "docker compose exec cairn python -m cairn.server compact --db /data/cairn.db",
        "docker compose start litestream",
        "sleep 15; docker compose exec litestream litestream ltx -level 0 /data/cairn.db"
        " | tail -n 1",
    ]
    # The services are the shipped ones, the database the one Litestream replicates, and the
    # compaction the run guide's own command.
    assert "litestream" in _load("compose.backup.yaml")["services"]
    assert "cairn" in _load("compose.yaml")["services"]
    assert _load("litestream.yml")["dbs"][0]["path"] == "/data/cairn.db"
    run_guide = (ROOT / "src" / "cairn" / "guides" / "run.md").read_text(encoding="utf-8")
    assert "python -m cairn.server compact --db /data/cairn.db" in run_guide


def test_the_compaction_paragraph_points_to_the_commands_and_says_why():
    flat = " ".join(GUIDE.split())
    assert (
        "Until then what the deleted workspace left in the log file stays there; to remove it"
        ' at once, stop the replica service for the seconds a compaction takes (Backups, "After'
        ' a workspace is deleted", below).'
    ) in flat
    section = GUIDE[GUIDE.index("\n### After a workspace is deleted\n") :]
    section = " ".join(section[: section.index("\n### ", 1)].split())
    for sentence in (
        "Start `litestream` again whatever `compact` did: while it is stopped nothing reaches"
        " the replica, and cairn takes writes as usual.",
        "A write that arrives while `litestream` is stopped is copied once it starts.",
        "[docs/compaction-beside-a-replica.md](../docs/compaction-beside-a-replica.md)",
    ):
        assert sentence in section, sentence


def test_the_rehearsal_runs_the_compaction_commands_last_and_the_guide_says_so():
    source = SCRIPT.read_text(encoding="utf-8")
    opening = "checks: list[Callable[[], str]] = ["
    checks = source[source.index(opening) + len(opening) :]
    checks = checks[: checks.index("]")]
    names = re.findall(r"rehearsal\.(\w+),", checks)
    assert names[-2:] == [
        "copies_files_in_and_out_as_the_guide_says",
        "compacts_with_the_replica_paused",
    ]
    assert 'the guide\'s "After a workspace is deleted" commands' in " ".join(
        (rehearse.__doc__ or "").split()
    )
    rehearse_section = GUIDE[GUIDE.index("\n### Rehearse the whole stack\n") :]
    rehearse_section = " ".join(rehearse_section[: rehearse_section.index("\n### ", 1)].split())
    assert (
        'It runs this guide\'s "After a workspace is deleted" commands as written after a'
        " workspace is deleted, and checks that right after `compact` the log file is empty"
    ) in rehearse_section


def test_a_block_is_the_first_bash_block_under_its_heading_with_continuations_joined():
    text = "# T\n\n## A\n\n```bash\none \\\n  two\nthree\n```\n\n## B\n\n```bash\nfour\n```\n"
    assert rehearse.guide_block(text, "## A") == ["one two", "three"]
    assert rehearse.guide_block(text, "## B") == ["four"]


def test_the_key_copy_and_the_plans_copy_are_read_from_the_guide():
    out = rehearse.guide_lines(GUIDE, "laptop.env")
    assert [line.split()[:3] for line in out] == [
        ["docker", "compose", "exec"],
        ["docker", "compose", "cp"],
        ["docker", "compose", "exec"],
    ]
    plans_in = rehearse.guide_lines(_section("## Plans"), "/data/plans.yaml")
    assert plans_in[0].startswith("docker compose ")
    assert "plans.yaml" in plans_in[0]


def test_the_plans_section_keeps_the_rehearsals_commands():
    """scripts/rehearse_stack.py takes the first `docker compose` command in "## Plans" that
    names /data/plans.yaml (step 2's copy) and exactly three that name laptop.env."""
    plans = _section("## Plans")
    assert rehearse.guide_lines(plans, "/data/plans.yaml")[0] == (
        "docker compose exec -T cairn sh -c 'cat > /data/plans.yaml' < plans.yaml"
    )
    assert len(rehearse.guide_lines(GUIDE, "laptop.env")) == 3


def test_the_hosted_vm_takes_a_plans_file_and_refuses_plans_beside_it():
    """The hosted VM: both settings exist, and plan time refuses --plans beside the file."""
    variables = (ROOT / "infra" / "gcp" / "variables.tf").read_text(encoding="utf-8")
    main = (ROOT / "infra" / "gcp" / "main.tf").read_text(encoding="utf-8")
    for name in ("cairn_plans_file", "cairn_extra_args"):
        assert f'variable "{name}"' in variables
    assert '!can(regex("(^| )--plans( |=|$)", var.cairn_extra_args))' in main


def test_inline_commands_are_read_from_backticks_and_blocks_alike():
    text = "Run `docker compose cp a b:/x` then:\n\n```bash\ndocker compose exec b ls /x\n```\n"
    assert rehearse.guide_lines(text, "/x") == [
        "docker compose cp a b:/x",
        "docker compose exec b ls /x",
    ]


def test_the_body_cap_is_read_from_caddys_adapted_config():
    adapted = {
        "apps": {
            "http": {
                "servers": {
                    "srv0": {
                        "routes": [
                            {
                                "handle": [
                                    {
                                        "handler": "subroute",
                                        "routes": [
                                            {
                                                "handle": [
                                                    {
                                                        "handler": "request_body",
                                                        "max_size": 4194304,
                                                    },
                                                    {"handler": "reverse_proxy"},
                                                ]
                                            }
                                        ],
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
        }
    }
    assert rehearse.body_caps(adapted) == [4194304]
    assert rehearse.body_caps({"apps": {}}) == []


def test_the_guide_says_serve_tidies_by_default_and_how_to_switch_it_off():
    """Decision 76: serve tidies by default (tests/unit/test_arrange_cli_unit.py::test_serve_
    tidies_by_default_and_no_tidy_turns_it_off); --no-tidy goes through CAIRN_EXTRA_ARGS, the
    last of the cairn service's command (test_guide_facts_model_unit.py::test_the_tidy_
    section_switches_it_off_through_the_compose_files_extra_args); a tidy counts toward a
    plan's versions (test_arrange_cli_unit.py::test_a_tidy_saved_through_the_worker_view_
    counts_toward_the_plans_versions); the section it names is the run guide's."""
    flat = " ".join(GUIDE.split())
    assert "`serve` tidies pages by default, with no model" in flat
    assert (
        "and the version before stays readable, unless a plan keeps only the last versions of"
        " each page (Plans, below): a tidy counts toward them like any other version" in flat
    )
    assert "## Plans" in GUIDE and "versions_per_page" in (DEPLOY / "plans.example.yaml").read_text(
        encoding="utf-8"
    )
    assert (
        "Nothing leaves the container. To switch it off, add `--no-tidy` to `CAIRN_EXTRA_ARGS`."
        in flat
    )
    run = (ROOT / "src" / "cairn" / "guides" / "run.md").read_text(encoding="utf-8")
    assert '"Tidy pages without a model"' in flat and "## Tidy pages without a model" in run
    command = _load("compose.yaml")["services"]["cairn"]["command"]
    assert command.startswith("serve ") and command.endswith("${CAIRN_EXTRA_ARGS:-}")
    assert "--tidy" not in command  # the default command needs nothing for the default


def test_the_compose_comment_says_writes_wait_for_a_compaction_and_reads_do_not():
    """Reads have connections of their own (cairn.store.base's Readers): a compaction holds
    the writer alone, as Store.compact's docstring says."""
    text = (DEPLOY / "compose.yaml").read_text(encoding="utf-8")
    flat = " ".join(line.strip().lstrip("# ") for line in text.splitlines())
    assert "every request waits" not in flat
    assert "every write waits while it runs, about as long as copying the database file" in flat
    assert "takes; reads do not wait." in flat
