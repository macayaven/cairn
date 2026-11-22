"""Unit tests for cairn.plans (H6): plans are the operator's configuration, read from YAML."""

from datetime import UTC, datetime

import pytest

from cairn.plans import Plan, Plans, load, quantity
from cairn.store import Store
from cairn.store.usage import Limits
from support.paths import REPO_ROOT

EXAMPLE = REPO_ROOT / "deploy" / "plans.example.yaml"


def test_a_plans_caps_are_the_limits_of_a_view():
    plan = Plan("Free", notes_per_month=3, storage_bytes=400, projects=2, model_budget_micros=0)
    assert plan.limits() == Limits(
        notes_per_month=3, storage_bytes=400, projects=2, model_budget_micros=0
    )
    assert Plan("Open").limits() == Limits()


@pytest.mark.parametrize(
    "fields",
    [
        {"label": ""},
        {"label": "  "},
        {"label": "Free", "projects": -1},
        {"label": "Free", "notes_per_month": 2.5},
        {"label": "Free", "storage_bytes": True},
        {"label": "Free", "requests_per_minute": 0},  # a plan that could make no request
    ],
)
def test_a_plan_is_refused_when_it_cannot_be_meant(fields):
    with pytest.raises(ValueError):
        Plan(**fields)


def test_a_workspace_on_an_unknown_name_is_on_the_default():
    plans = Plans({"free": Plan("Free"), "solo": Plan("Solo")}, default="free")
    assert plans.resolve("solo") == ("solo", Plan("Solo"))
    assert plans.resolve(None) == ("free", Plan("Free"))
    assert plans.resolve("gold") == ("free", Plan("Free"))


def test_limits_of_is_resolve_then_the_plans_limits():
    """The resolver a limited view asks inside each write (Store.limited(resolve=...))."""
    plans = Plans({"free": Plan("Free", projects=1), "solo": Plan("Solo", projects=9)}, "free")
    assert plans.limits_of("solo") == Plan("Solo", projects=9).limits()
    assert plans.limits_of(None) == plans.limits_of("gold") == Plan("Free", projects=1).limits()


@pytest.mark.parametrize("name", ["", " free", "x" * 65])
def test_a_plans_name_is_one_a_workspace_can_record(name):
    with pytest.raises(ValueError):
        Plans({name: Plan("Free")}, default=name)


@pytest.mark.parametrize(
    "text, named",
    [
        ("plans:\n  free:\n    label: Free\n", "default"),
        ("default: free\nplans:\n  free:\n    label: Free\n    notes: 3\n", "notes"),
        ("default: free\nplans:\n  free:\n    label: Free\n    projects: -1\n", "projects"),
        ("default: gold\nplans:\n  free:\n    label: Free\n", "gold"),
        ("default: free\nplans:\n  free:\n    projects: 2\n", "label"),
        ("default: free\nplans:\n  free:\n    label: Free\n    projects: 1.5\n", "projects"),
        ("default: free\nprices: {}\nplans:\n  free:\n    label: Free\n", "prices"),
        ("default: free\nplans: [free]\n", "plans"),
        ("default: free\nplans:\n  free: Free\n", "free"),
        ("- free\n", "mapping"),
        ("default: free\nplans: {}\n", "plans"),
        ("default: free\nplans:\n  free:\n    label: Free\n  free:\n    label: Again\n", "free"),
    ],
)
def test_a_plans_file_that_is_wrong_says_what_is_wrong(tmp_path, text, named):
    file = tmp_path / "plans.yaml"
    file.write_text(text)
    with pytest.raises(ValueError, match=named):
        load(file)


def test_a_plans_file_with_a_storage_cap_names_a_cap_on_notes_a_month(tmp_path):
    """What a workspace takes grows by a fixed cost for each note it holds, which only a
    cap on notes a month bounds: a plan file's plan with a storage cap must name one."""
    file = tmp_path / "plans.yaml"
    file.write_text(
        "default: free\nplans:\n  free:\n    label: Free\n    notes_per_month: 3\n"
        "  roomy:\n    label: Roomy\n    storage_bytes: 4000\n    projects: 2\n"
    )
    with pytest.raises(ValueError, match=r"plans\.roomy") as refused:
        load(file)
    said = str(refused.value)
    assert "storage_bytes" in said and "notes_per_month" in said and "each note" in said
    file.write_text(
        "default: free\nplans:\n  free:\n    label: Free\n    notes_per_month: 0\n"
        "    storage_bytes: 4000\n  open:\n    label: Open\n    projects: 2\n"
    )
    plans = load(file)  # both caps, or no storage cap: as before
    assert plans.table["free"] == Plan("Free", notes_per_month=0, storage_bytes=4000)
    assert plans.table["open"] == Plan("Open", projects=2)
    assert Plan("Roomy", storage_bytes=4000).storage_bytes == 4000  # in code: unchanged


def test_a_file_that_is_not_yaml_is_a_value_error(tmp_path):
    file = tmp_path / "plans.yaml"
    file.write_text("default: [unclosed\n")
    with pytest.raises(ValueError, match="YAML"):
        load(file)


def test_the_example_file_loads_and_says_its_numbers_are_placeholders():
    first = EXAMPLE.read_text().splitlines()[0]
    assert first.startswith("#") and "placeholder" in first
    plans = load(EXAMPLE)
    assert plans.default in plans.table


def test_the_deploy_docs_say_how_to_give_serve_a_plans_file():
    deploy = EXAMPLE.parent
    readme = deploy.joinpath("README.md").read_text()
    section = readme.split("## Plans", 1)[1].split("\n## ", 1)[0]
    assert "--plans /data/plans.yaml" in section and "plans.example.yaml" in section
    assert "placeholder" in section and "plan-set" in section and "source-sync" in section
    compose = deploy.joinpath("compose.yaml").read_text()
    comments = "\n".join(line for line in compose.splitlines() if line.lstrip().startswith("#"))
    assert "--plans" in comments and "placeholder" in comments


def test_the_deploy_docs_say_where_a_workspaces_id_comes_from_with_a_command_that_runs():
    """plan-set takes an id nobody chose on a sign-in install: the Plans section and the
    example file name workspace-list (H10), and the line shown is one the parser takes."""
    from cairn.web import cli

    section = EXAMPLE.parent.joinpath("README.md").read_text().split("## Plans", 1)[1]
    section = section.split("\n## ", 1)[0]
    shown = "python -m cairn.server workspace-list --db /data/cairn.db"
    assert f"docker compose exec cairn {shown}" in section
    assert section.index("plan-set") < section.index("workspace-list")
    assert cli._parser().parse_args(shown.split()[3:]).command == "workspace-list"
    assert "workspace-list --db PATH" in EXAMPLE.read_text()


# The plan in force: the one place a workspace's plan is resolved

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
THREE = Plans({"free": Plan("Free"), "team": Plan("Team"), "solo": Plan("Solo")}, "free")


@pytest.mark.parametrize(("operator", "in_force"), [(None, "free"), ("team", "team")])
def test_the_plan_in_force_is_the_operators_else_the_default(tmp_path, operator, in_force):
    store = Store(tmp_path / "cairn.db", clock=lambda: NOW)
    try:
        if operator:
            store.set_plan(store.account_id, operator)
        assert THREE.in_force(store, store.account_id) == (in_force, THREE.table[in_force])
        with pytest.raises(KeyError):
            THREE.in_force(store, "acct_gone")
    finally:
        store.close()


def test_an_amount_of_a_limit_reads_as_people_say_it():
    """quantity(limit, count): what /usage says was used of a limit, and of its cap."""
    assert quantity("notes_per_month", 12_345) == "12,345"
    assert quantity("projects", 1) == "1"
    assert quantity("storage_bytes", 768) == "768 bytes"
    assert quantity("storage_bytes", 1) == "1 byte"
    assert quantity("storage_bytes", 500_000_000) == "500 MB"
    assert quantity("storage_bytes", 2_500_000_000) == "2.5 GB"
    assert quantity("model_budget_micros", 0) == "$0.00"
    assert quantity("model_budget_micros", 1_234_567_890) == "$1,234.57"
    with pytest.raises(ValueError):
        quantity("requests_per_minute", 3)
