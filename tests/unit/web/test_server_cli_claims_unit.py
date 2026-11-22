"""Unit tests for what the command-line section of cairn.server's docstring says, and for
`python -m cairn`: each test checks a sentence against the behaviour it describes, so the two
cannot drift apart. The behaviour itself is pinned here or where the test names it."""

import argparse
import inspect
import logging
import re
import sqlite3

import pytest
import uvicorn

from cairn import cli as lint_cli
from cairn import server
from cairn.memory import ACCESS_LOGGER
from cairn.store import CompactIncomplete, Store
from cairn.web import cli, cli_arrange, cli_data
from cairn.web.compactor import Compactor
from support.paths import REPO_ROOT


def flat(text: str) -> str:
    """The text with its line breaks and runs of blanks made single spaces."""
    return " ".join(text.split())


DOC = flat(server.__doc__)


def subcommands() -> dict[str, argparse.ArgumentParser]:
    return cli._parser()._subparsers._group_actions[0].choices


def synopsis(command: str) -> str:
    """The docstring's `<command> --db ...` span, the one that lists the command's options."""
    (span,) = re.findall(rf"`(?:python -m cairn\.server )?{command} --db [^`]+`", DOC)
    return span


def test_a_refused_import_is_said_to_leave_no_database_it_made():
    """Behaviour: test_portable_unit.py::test_an_import_refused_into_a_new_database_leaves_
    nothing_it_made and ..._into_a_database_that_was_there_leaves_it_as_it_was."""
    assert (
        "1 when the import is refused, nothing written: a database this run created is removed"
        " again, with the directories made for it" in DOC
    )


def test_a_failed_export_is_said_to_be_one_line_and_no_file():
    """Behaviour: test_portable_unit.py::test_an_export_that_fails_says_so_in_one_line_and_
    leaves_no_file."""
    assert (
        "a write that fails, \"cannot export to FILE: <the system's reason or the error's"
        ' class>", which removes the file' in DOC
    )
    for doc in (DOC, flat(cli_data.__doc__)):
        assert "failures that name the file and the system's reason or the error's class" in doc


# 1. What the arranger queues


def test_a_note_a_source_sync_saves_is_said_to_queue_its_page():
    """Behaviour: test_arrange_unit.py::test_a_note_a_source_sync_writes_queues_its_page, and
    the app hands its queue to the background sync (test_the_apps_sync_and_the_apps_guard_
    reach_the_worker); `source-sync` on the command line builds no arranger."""
    assert "Notes written by a source sync are not queued" not in DOC
    assert (
        "queues the page of every note a request (REST, MCP or the web composer) or the app's"
        " background source sync saves (Memory.saved)" in DOC
    )
    assert "`source-sync` on the command line runs no arranger" in DOC
    assert "arrange" not in inspect.getsource(cli._sync)


# 2. arrange's synopsis


def test_arranges_synopsis_names_every_option_its_parser_has():
    """An option of a required group of alternatives is shown as one of them, "(--a A |
    --b)", as the usage line shows it; every other option as required or not."""
    shown = synopsis("arrange")
    parser = subcommands()["arrange"]
    alternatives = []
    for group in parser._mutually_exclusive_groups:
        assert group.required
        named = [
            " ".join(filter(None, (a.option_strings[0], a.metavar))) for a in group._group_actions
        ]
        assert f" ({' | '.join(named)}) " in shown
        alternatives += group._group_actions
    assert alternatives  # --model-env and --tidy
    for action in parser._actions:
        if action.option_strings == ["-h", "--help"] or action in alternatives:
            continue
        option = action.option_strings[0]
        if action.metavar:  # the value as --help shows it
            assert f"{option} {action.metavar}" in shown, option
        if action.required:
            assert f" {option} " in shown and f"[{option} " not in shown, option
        else:
            assert f"[{option} " in shown, option


def test_google_authorizes_synopsis_names_every_option_and_no_db():
    """`google-authorize` (C8; cairn.web.cli_google holds the contract) takes no --db, so its
    synopsis is found by its own first option; each option shows its value as --help does,
    the required ones bare and --kind once per value."""
    (shown,) = re.findall(r"`google-authorize --client [^`]+`", DOC)
    parser = subcommands()["google-authorize"]
    assert "--db" not in parser._option_string_actions and "--db" not in shown
    for action in parser._actions:
        if action.option_strings == ["-h", "--help"]:
            continue
        option = f"{action.option_strings[0]} {action.metavar}"
        if action.required:
            assert f" {option} " in f" {shown[1:-1]} ", option
        else:
            assert f"[{option}]" in shown, option
    kind = parser._option_string_actions["--kind"]
    assert isinstance(kind, argparse._AppendAction)
    assert "--kind KIND [--kind KIND]..." in shown
    assert "opens no database and takes no --db" in DOC


def test_arrange_is_said_to_need_plans_where_a_workspace_has_one():
    """Behaviour: test_arrange_cli_unit.py::test_arrange_needs_the_plans_when_a_workspace_has_
    a_plan (exit 2 with cli_arrange.NEEDS_PLANS)."""
    assert (
        "`--plans` is required when any workspace in the database has a plan the operator set"
        f' (without it: exit 2, "{cli_arrange.NEEDS_PLANS.split(":")[0]}")' in DOC
    )


# 3. Options given once per value


def repeatable() -> list[argparse.Action]:
    return [a for a in subcommands()["serve"]._actions if isinstance(a, argparse._AppendAction)]


def test_every_repeatable_serve_option_is_shown_once_per_value():
    names = {action.option_strings[0] for action in repeatable()}
    assert names == {
        "--allowed-host",
        "--trusted-proxy",
        "--sign-in",
        "--allowed-email-domain",
        "--google-connect-for",
    }
    for action in repeatable():
        value = "|".join(action.choices) if action.choices else action.metavar
        assert f"[{action.option_strings[0]} {value}]..." in DOC, action.option_strings[0]
    assert re.search(r" \.\.\.\]", DOC) is None  # never "[--x X ...]": values after one flag
    assert (
        'an option shown with "..." takes one value and is given once per value, as often as'
        " needed" in DOC
    )


@pytest.mark.parametrize(
    "values",
    [
        ["--allowed-host", "a.example", "b.example"],
        ["--trusted-proxy", "10.0.0.1", "10.0.0.2"],
        ["--sign-in", "github", "google"],
        ["--allowed-email-domain", "a.example", "b.example"],
    ],
    ids=lambda values: values[0],
)
def test_two_values_after_one_flag_exit_2_and_never_start(tmp_path, monkeypatch, capsys, values):
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("serve started"))
    db = tmp_path / "cairn.db"
    with pytest.raises(SystemExit) as refused:
        cli.main(["serve", "--db", str(db), *values])
    assert refused.value.code == 2
    assert f"unrecognized arguments: {values[-1]}" in capsys.readouterr().err
    assert not db.exists()


# 5. The compactor's three lines


def test_each_compaction_logs_the_line_the_docstring_shows(tmp_path, monkeypatch, caplog):
    (said,) = re.findall(r"each run is one access-log line: (.+?)\. `serve --sweep-every", DOC)
    done, held, failed = re.findall(r'"(compact [^"]+ in N ms)"', said)
    store = Store(tmp_path / "cairn.db")
    compactor = Compactor(store, hours=1)
    outcomes = {
        done: None,
        held: CompactIncomplete("another connection is reading"),
        failed: sqlite3.OperationalError("disk I/O error at /elsewhere/cairn.db"),
    }
    real = store.compact  # done: the real one, which clears the record as a run does
    try:
        for shown, error in outcomes.items():
            if store.compaction_state().deleted_at is None:
                store.delete_account(store.create_account("Gone").id)

            def compact(error=error):
                if error is not None:
                    raise error
                real()

            monkeypatch.setattr(store, "compact", compact)
            caplog.clear()
            with caplog.at_level(logging.INFO, logger=ACCESS_LOGGER):
                assert compactor.run_once() is (error is None)
            (record,) = [r for r in caplog.records if r.name == ACCESS_LOGGER]
            line = re.sub(r" in \d+ ms$", " in N ms", record.getMessage())
            assert line == shown.replace("<the error's class>", "OperationalError")
    finally:
        store.close()


# 8. arrange's line


def test_the_arrange_line_is_shown_as_cli_arrange_shows_it():
    """Behaviour: test_arrange_cli_unit.py, one test a shape of the line."""
    line = '"<project>/<slug>: <status>[, version <a>[ -> <b>]][, tokens <in>/<out>]"'
    assert line in flat(cli_arrange.__doc__) and line in DOC
    assert "(status, versions, tokens)" not in DOC


# What the docstring had left out


def test_key_revoke_is_described_as_it_behaves(tmp_path):
    assert (
        "`python -m cairn.server key-revoke --db PATH --label L` (revokes every key with that"
        " label; 1 for a missing database, which it does not create, or no key with that label)"
        in DOC
    )
    db = tmp_path / "cairn.db"
    assert cli.main(["key-revoke", "--db", str(db), "--label", "laptop"]) == 1
    assert not db.exists()
    store = Store(db)
    kept, gone = store.create_key("ci"), [store.create_key("laptop"), store.create_key("laptop")]
    store.close()
    assert cli.main(["key-revoke", "--db", str(db), "--label", "laptop"]) == 0
    assert cli.main(["key-revoke", "--db", str(db), "--label", "laptop"]) == 1
    store = Store(db)
    try:
        assert [store.identify_key(key) is None for key in gone] == [True, True]
        assert store.identify_key(kept) is not None
    finally:
        store.close()


@pytest.mark.parametrize(
    ("option", "bad", "said"),
    [
        ("--registrations-per-hour", "0",
         "--registrations-per-hour is how many agents one client address may register in an"
         " hour, 20 by default, a whole number of at least 1, else exit 2"),
        ("--model-deadline", "0",
         "--model-deadline is how long one call may take, retries included (300 by default; a"
         " number above 0, else exit 2)"),
    ],
)  # fmt: skip
def test_a_serve_option_it_had_left_out_is_described_and_refused_with_2(
    tmp_path, monkeypatch, option, bad, said
):
    assert said in DOC
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("serve started"))
    with pytest.raises(SystemExit) as refused:
        cli.main(["serve", "--db", str(tmp_path / "cairn.db"), option, bad])
    assert refused.value.code == 2


def test_sign_in_without_a_public_url_is_said_to_exit_2(tmp_path, monkeypatch, capsys):
    assert "sign-in needs --public-url, else exit 2" in DOC
    monkeypatch.setenv("CAIRN_GITHUB_CLIENT_ID", "client-id")
    monkeypatch.setenv("CAIRN_GITHUB_CLIENT_SECRET", "client-secret-do-not-print")
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("serve started"))
    assert cli.main(["serve", "--db", str(tmp_path / "cairn.db"), "--sign-in", "github"]) == 2
    err = capsys.readouterr().err
    assert "sign-in needs public_url" in err and "client-secret-do-not-print" not in err


def test_the_linter_is_named_by_the_command_that_runs_it(capsys):
    """No `cairn` command is installed (pyproject.toml has no [project.scripts]): the linter
    is `python -m cairn lint`, in its usage lines, its docstring and the README."""
    root = REPO_ROOT
    assert "[project.scripts]" not in (root / "pyproject.toml").read_text()
    parser = lint_cli._parser()
    lint = parser._subparsers._group_actions[0].choices["lint"]
    for usage in (parser.format_usage(), lint.format_usage()):
        assert usage.startswith("usage: python -m cairn "), usage
    assert lint_cli.main(["lint"]) == 2
    assert "usage: python -m cairn lint " in capsys.readouterr().err
    doc = flat(lint_cli.__doc__)
    readme = (root / "README.md").read_text()
    for text in (doc, readme):
        for named in re.finditer(r"(\S*\s*\S*\s*)cairn lint\b", text):
            words = [word.strip("`|") for word in named.group(1).split()]
            assert words[-2:] == ["python", "-m"], named.group(0)
    assert "`python -m cairn lint --source SRC --pages DIR" in doc


@pytest.mark.parametrize("seconds", ["0", "0.5", "0.999", "-1", "nan", "soon"])
def test_sync_every_below_one_second_exits_2_and_never_starts(
    tmp_path, monkeypatch, capsys, seconds
):
    assert "`serve --sync-every SECONDS` (at least 1, else exit 2)" in DOC
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("serve started"))
    db = tmp_path / "cairn.db"
    with pytest.raises(SystemExit) as refused:
        cli.main(["serve", "--db", str(db), "--sync-every", seconds])
    assert refused.value.code == 2
    assert "argument --sync-every" in capsys.readouterr().err
    assert not db.exists()
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: apps.append(app))
    assert cli.main(["serve", "--db", str(db), "--sync-every", "1"]) == 0  # 1 itself is taken
    assert len(apps) == 1


def test_usage_with_a_plans_file_it_cannot_use_is_said_to_exit_2():
    """Behaviour: test_quota_web_unit.py::test_usage_refuses_plans_it_cannot_read."""
    assert "`usage --plans` with a file it cannot use exits 2" in DOC


# 9. --tidy (T4f)


def test_the_tidy_refusal_is_the_line_the_docstrings_show(tmp_path, monkeypatch, capsys):
    """Behaviour: test_arrange_cli_unit.py::test_serve_tidy_refuses_a_model_option_in_one_
    line_and_starts_nothing and ::test_arrange_tidy_refuses_a_model_option_in_one_line."""
    shown = '"--tidy uses no model, so it takes no <option>"'
    assert shown in DOC and shown in flat(cli_arrange.__doc__)
    assert (
        "`serve --tidy` with --model-env, --model-price or --model-deadline, and `arrange"
        " --tidy` with --model-price or --model-deadline, exit 2 before anything is opened,"
        " with one line" in DOC
    )
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("serve started"))
    db = tmp_path / "cairn.db"
    for option, value in (("--model-env", "M"), ("--model-price", "1,2")):
        assert cli.main(["serve", "--db", str(db), "--tidy", option, value]) == 2
        said = shown.strip('"').replace("<option>", option)
        assert capsys.readouterr().err == f"{said}\n"
    assert not db.exists()


def test_the_tidy_section_says_what_arrange_and_serve_do_with_it():
    """Behaviour: test_arrange_cli_unit.py::test_arrange_tidy_needs_the_plans_where_a_
    workspace_has_one, ::test_arrange_tidy_makes_one_pass_with_no_model_and_prints_a_line_a_
    page and ::test_serve_tidy_runs_the_worker_with_no_model; tests/unit/test_tidy_unit.py::
    test_the_web_view_says_cairn_tidied_a_version."""
    assert '(without it: exit 2, "arrange needs --plans FILE here"), with --tidy too' in DOC
    assert "(exactly one of --model-env and --tidy, else exit 2)" in DOC
    assert '"<project>/<slug>: arranged, version <a> -> <b>", with no tokens' in DOC
    assert '"Arranged by cairn" in the web view' in DOC
    assert "Off by default: without --tidy nothing changes." not in DOC
    doc = flat(cli_arrange.__doc__)
    assert '"<project>/<slug>: unchanged, version <a>", never with tokens' in doc
    assert (
        "`arrange --tidy` spends nothing and needs --plans where `arrange --model-env` does" in doc
    )


def test_the_docstrings_say_serve_tidies_by_default_and_how_to_turn_it_off():
    """Behaviour: test_arrange_cli_unit.py::test_serve_tidies_by_default_and_no_tidy_turns_it_
    off, ::test_the_default_is_one_constant, ::test_serve_refuses_tidy_with_no_tidy,
    ::test_serve_no_tidy_loads_no_arranging_code, ::test_serve_runs_the_models_worker_only_
    with_model_env and ::test_model_deadline_without_a_model_is_accepted_and_ignored; tests/
    acceptance/test_t4b_arrange.py::test_without_an_arranger_no_model_code_is_loaded."""
    assert cli_arrange.TIDY_BY_DEFAULT is True
    assert (
        "On by default in `serve` (decision 76; cairn.web.cli_arrange.TIDY_BY_DEFAULT): `serve`"
        " with no arranging option runs it as `serve --tidy` does, `serve --no-tidy` runs no"
        " worker and imports no arranging code, the parser refuses --tidy with --no-tidy, and"
        " `serve --model-env` runs the model's worker in its place. `create_app` runs no"
        " arranger unless one is passed." in DOC
    )
    doc = flat(cli_arrange.__doc__)
    assert doc.startswith(
        "The model that arranges pages, on the command line (T4b), and tidying with no model"
        " (T4f): `serve [--model-env PREFIX] [--tidy | --no-tidy] [--model-price IN,OUT]"
        " [--model-deadline SECONDS]`"
    )
    assert "without --model-env or --tidy there is no worker" not in doc
    assert (
        "with --no-tidy and without --model-env there is no worker and no arranging code is"
        " loaded" in doc
    )
    assert "`--model-price` without `--model-env` exits 2, with --no-tidy or without it" in doc
    assert (
        "`serve` runs it in the background by default (TIDY_BY_DEFAULT, decision 76), a pass"
        " every ARRANGE_EVERY seconds, as `serve --tidy` does; `serve --no-tidy` runs no worker"
        " (the parser refuses --tidy with --no-tidy), and `serve --model-env` runs the model's"
        " in its place. `serve --model-deadline` without --model-env is accepted and changes"
        " nothing, as before the default." in doc
    )
    assert "`serve --no-tidy` without --model-env or --model-price imports neither" in doc
    assert (
        "`serve` tidies by default (decision 76): `serve --no-tidy` runs no worker and loads no"
        " arranging code, and `serve --model-env` runs the model in its place" in flat(cli.__doc__)
    )
    commands = subcommands()
    said = commands["serve"]._option_string_actions["--no-tidy"].help
    assert said == cli_arrange.NO_TIDY_HELP and said.endswith(
        "; serve tidies pages unless this is given"
    )
    assert (
        "no arranging code is loaded" in said
        and "--no-tidy" not in commands["arrange"]._option_string_actions
    )
