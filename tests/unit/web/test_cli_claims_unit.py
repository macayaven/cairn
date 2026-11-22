"""Unit tests for what the operator's command line says about itself: the module docstrings of
cairn.web.cli and its helpers, the --help of each command and the messages it prints. Each
test checks a sentence against the behaviour it describes, so the two cannot drift apart."""

import argparse
import re

import pytest
import uvicorn

from cairn import server
from cairn.memory import DENIED, record_access
from cairn.plans import Plan, Plans
from cairn.store import DEFAULT_ACCOUNT_ID, Store
from cairn.web import cli, cli_arrange, cli_data
from support.cli_commands import NO_DATABASE


def flat(text: str) -> str:
    """The text with its line breaks and runs of blanks made single spaces."""
    return " ".join(text.split())


def test_the_access_log_line_is_the_one_the_docstring_shows(capsys):
    doc = flat(cli.__doc__)
    (shown,) = re.findall(r'"(cairn\.access: [^"]+)"', doc)
    with cli._access_log_on_stderr():
        record_access("key", "ci-runner", "key_0001", "recall", "alpha", 200)
        record_access("key", "ci-runner", "key_0001", "pages", None, 404)
        record_access("none", None, None, DENIED, None, 401)
    named, unnamed, nobody = capsys.readouterr().err.splitlines()
    (example,) = re.findall(r'\("(denied by [^"]+)"\)', doc)
    assert nobody == f"cairn.access: {example}"
    filled = shown
    for placeholder, value in {
        "<action>": "recall",
        "<actor kind>": "key",
        "<actor>": "ci-runner",
        "<actor id>": "key_0001",
        "<name>": "alpha",
        "<status>": "200",
    }.items():
        filled = filled.replace(placeholder, value)
    assert named == filled
    assert unnamed.endswith(", project None: 404") and "project reads None" in doc


def test_the_arrange_line_shows_the_version_as_optional():
    """The lines themselves are pinned in test_arrange_cli_unit.py, one test a shape."""
    doc = flat(cli_arrange.__doc__)
    assert '"<project>/<slug>: <status>[, version <a>[ -> <b>]][, tokens <in>/<out>]"' in doc
    assert (
        '"?" for a count it did not report and "tokens not reported" when it reported neither'
        in doc
    )


def test_the_data_commands_say_what_they_print_beyond_paths_and_counts():
    """An import prints the workspace's id, the note fields it cut and the private-projects
    notice, and a refusal can name a project (test_portable_unit.py); never note text."""
    for doc in (flat(cli_data.__doc__), flat(server.__doc__)):
        assert "paths and counts only" not in doc
        assert "the workspace's id, the names of the note fields an import cut" in doc
        assert "never note text, keys or tokens" in doc


def subcommands() -> dict[str, argparse.ArgumentParser]:
    return cli._parser()._subparsers._group_actions[0].choices


def test_arranges_usage_line_shows_the_model_as_required_and_serves_as_optional():
    """arrange takes exactly one of a model and --tidy (T4f), and the usage line says so;
    serve takes either, or neither, and one of --tidy and --no-tidy at most (decision 76);
    arrange has no --no-tidy."""
    arrange = flat(subcommands()["arrange"].format_usage())
    assert " (--model-env PREFIX | --tidy) " in arrange
    assert "[--model-env PREFIX]" not in arrange and "[--tidy]" not in arrange
    assert "--no-tidy" not in arrange
    serve = flat(subcommands()["serve"].format_usage())
    assert "[--model-env PREFIX]" in serve and "[--tidy | --no-tidy]" in serve


@pytest.mark.parametrize("hours", ["1000000", "inf", "-1", "nan"])
def test_compact_every_is_refused_with_the_bound_it_broke(capsys, hours):
    with pytest.raises(SystemExit) as refused:
        cli._parser().parse_args(["serve", "--db", "x.db", "--compact-every", hours])
    assert refused.value.code == 2
    assert "0 (never), or more hours but fewer than 1000000" in capsys.readouterr().err
    assert cli._parser().parse_args(["serve", "--db", "x.db", "--compact-every", "999999.5"])
    (option,) = [
        action
        for action in subcommands()["serve"]._actions
        if "--compact-every" in action.option_strings
    ]
    assert "fewer than 1000000" in option.help


PLAN_RULE = "a plan's name has 1 to 64 characters and no blank at either end: ' solo'"


def test_a_refused_plan_name_is_told_the_whole_rule_it_broke(tmp_path, capsys):
    db = tmp_path / "cairn.db"
    Store(db).close()
    argv = ["plan-set", "--db", str(db), "--account", DEFAULT_ACCOUNT_ID, "--plan", " solo"]
    assert cli.main(argv) == 1
    assert capsys.readouterr().err == f"cannot set that plan: {PLAN_RULE}\n"
    # The plans file says it in the same words: one rule, one message.
    with pytest.raises(ValueError) as refused:
        Plans(table={" solo": Plan("Solo")}, default=" solo")
    assert str(refused.value) == PLAN_RULE


def test_plan_set_takes_a_name_by_the_rule_the_docstring_states(tmp_path, capsys):
    """The docstring says which names plan-set records: by length and blanks, not by a
    plans file, which plan-set never reads."""
    doc = flat(cli.__doc__)
    assert "a name of 1 to 64 characters with no blank at either end" in doc
    assert "not checked against a plans file" in doc
    db = tmp_path / "cairn.db"
    Store(db).close()
    argv = ["plan-set", "--db", str(db), "--account", DEFAULT_ACCOUNT_ID, "--plan"]
    assert cli.main([*argv, "x" * 64]) == 0  # no plans file lists it
    assert cli.main([*argv, "x" * 65]) == 1
    assert cli.main([*argv, "solo "]) == 1


def option(command: str, name: str) -> argparse.Action:
    return subcommands()[command]._option_string_actions[name]


def test_the_top_description_names_what_the_commands_do_and_no_erasing():
    """No command erases anything: compact clears what was already deleted, and deleting a
    workspace is done in the web view."""
    described = cli._parser().description
    assert "erase" not in described
    assert "export, import, compact and repair" in described


def test_an_example_in_a_help_line_is_called_an_example_not_put_where_a_default_goes():
    price = option("serve", "--model-price").help
    assert "(0.15,0.6)" not in price and "for example 0.15,0.6" in price
    assert "without it nothing is charged to a budget" in price
    assert option("arrange", "--model-price").help == price
    plans = option("serve", "--plans").help
    assert "(deploy/plans.example.yaml)" not in plans
    assert "deploy/plans.example.yaml is an example" in plans


def test_source_add_says_whose_environment_holds_the_keys():
    config = flat(option("source-add", "--config").help)
    assert "server's environment" not in config
    assert "in the environment of the process that syncs" in config


def test_no_command_takes_an_abbreviated_option():
    assert cli._parser().allow_abbrev is False
    for name, command in subcommands().items():
        assert command.allow_abbrev is False, name


def test_key_revoke_refuses_a_shortened_label_option_and_revokes_nothing(tmp_path, capsys):
    db = tmp_path / "cairn.db"
    store = Store(db)
    key = store.create_key("laptop")
    store.close()
    with pytest.raises(SystemExit) as refused:
        cli.main(["key-revoke", "--db", str(db), "--lab", "laptop"])
    assert refused.value.code == 2 and "--lab" in capsys.readouterr().err
    store = Store(db)
    try:
        assert store.identify_key(key) is not None  # still a key
    finally:
        store.close()


def test_serve_refuses_a_shortened_option_and_never_starts(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("serve started"))
    with pytest.raises(SystemExit) as refused:
        cli.main(["serve", "--db", str(tmp_path / "cairn.db"), "--compact", "5"])
    assert refused.value.code == 2
    assert "unrecognized arguments: --compact 5" in capsys.readouterr().err
    assert not (tmp_path / "cairn.db").exists()


def test_every_option_of_every_command_says_what_it_is():
    for name, command in subcommands().items():
        for action in command._actions:
            assert action.help, f"{name} {'/'.join(action.option_strings)}"


CREATES = ("serve", "key-create", "import")  # each makes its database when there is none


def test_db_says_whether_the_command_creates_the_database(tmp_path, monkeypatch):
    for name, command in subcommands().items():
        if name in NO_DATABASE:  # named there on purpose; test_newer_database_cli_unit.py
            assert "--db" not in command._option_string_actions, name  # checks the list
            continue
        said = command._option_string_actions["--db"].help
        if name in CREATES:
            assert "created if missing" in said, name
        else:
            assert "must exist" in said, name
    # The ones that say it must exist refuse a missing file and create none.
    monkeypatch.setenv("CAIRN_C_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("CAIRN_C_MODEL", "m")
    missing = tmp_path / "missing.db"
    for argv in (
        ["key-revoke", "--label", "laptop"],
        ["source-list"],
        ["export", "--account", DEFAULT_ACCOUNT_ID, "--out", str(tmp_path / "x.zip")],
        ["compact"],
        ["repair"],
        ["plan-set", "--account", DEFAULT_ACCOUNT_ID, "--default"],
        ["usage", "--account", DEFAULT_ACCOUNT_ID],
        ["workspace-list"],
        ["arrange", "--account", DEFAULT_ACCOUNT_ID, "--model-env", "CAIRN_C"],
    ):
        assert cli.main([argv[0], "--db", str(missing), *argv[1:]]) == 1, argv
        assert not missing.exists(), argv


def test_tidy_says_the_same_on_both_commands_and_needs_no_model():
    """Behaviour: test_arrange_cli_unit.py's --tidy tests and tests/unit/test_tidy_unit.py."""
    said = option("serve", "--tidy").help
    assert option("arrange", "--tidy").help == said == cli_arrange.TIDY_HELP
    assert "with no model, key or budget" in said
    assert "it takes none of the --model-* options" in said
    assert "`--tidy` in place of `--model-env` (T4f) arranges with no model" in flat(cli.__doc__)
    args = cli._parser().parse_args(["serve", "--db", "x.db", "--tidy", "--model-deadline", "9"])
    assert cli_arrange.refused(args) == "--tidy uses no model, so it takes no --model-deadline"
