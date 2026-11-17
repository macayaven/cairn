"""What README.md tells a newcomer to type, run as written in an empty directory."""

import shlex
import stat
from pathlib import Path

from cairn import server
from cairn.web import cli, cli_arrange

README = Path(__file__).resolve().parents[2] / "README.md"


def commands(marker: str) -> list[list[str]]:
    """The arguments of each `python -m cairn.server ...` line of the README naming `marker`."""
    found = []
    for line in README.read_text(encoding="utf-8").splitlines():
        if "python -m cairn.server" in line and marker in line and not line.startswith(("|", ">")):
            found.append(shlex.split(line.split("python -m cairn.server", 1)[1]))
    return found


def from_a_checkout(marker: str) -> list[str]:
    """The README's `marker` command that runs from a checkout: the one whose database is a
    relative path. The others run inside the container, on /data."""
    (found,) = [c for c in commands(marker) if not c[c.index("--db") + 1].startswith("/")]
    return found


def test_the_quickstart_makes_its_key_in_a_fresh_checkout(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # a clone has no runs/ directory: it is not in the repository
    quickstart = from_a_checkout("key-create")
    out = Path(quickstart[quickstart.index("--out") + 1])
    assert not out.is_absolute() and not out.parent.exists()

    assert server.main(quickstart) == 0

    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert out.read_text(encoding="utf-8").startswith("CAIRN_API_KEY=")
    printed = capsys.readouterr()
    assert out.read_text(encoding="utf-8").split("=", 1)[1].strip() not in printed.out + printed.err


def test_the_quickstart_serves_the_database_its_key_was_made_in():
    made, served = from_a_checkout("key-create"), from_a_checkout(" serve ")
    assert served[served.index("--db") + 1] == made[made.index("--db") + 1]


def test_the_tidy_row_names_an_option_serve_takes_and_its_acceptance_file():
    """The README's row for tidying without a model (T4f), on by default (decision 76):
    test_arrange_cli_unit.py::test_serve_tidies_by_default_and_no_tidy_turns_it_off and
    ::test_serve_runs_the_models_worker_only_with_model_env."""
    text = README.read_text(encoding="utf-8")
    (row,) = [line for line in text.splitlines() if line.startswith("| Tidying without a model")]
    assert "off by default" not in row and "no model, key or budget" in row
    assert (
        "On by default in `serve` (`--no-tidy` turns it off; `--model-env` puts a model in its"
        " place)" in row
    )
    assert cli_arrange.TIDY_BY_DEFAULT is True
    assert (README.parent / "tests" / "acceptance" / "test_t4f_tidy.py").is_file()
    assert "`tests/acceptance/test_t4f_tidy.py`" in row
    assert cli._parser().parse_args(["serve", "--db", "x.db", "--no-tidy"]).no_tidy is True


def test_the_google_credential_row_names_the_command_and_its_acceptance_file():
    """The README's row for `google-authorize` (C8): a command the parser has, with no --db,
    whose file is made with mode 0600 (test_cli_google_unit.py pins how)."""
    from cairn.web import cli_google

    text = README.read_text(encoding="utf-8")
    (row,) = [line for line in text.splitlines() if line.startswith("| Google credential")]
    assert (
        "`google-authorize` runs Google's consent flow for a Desktop app client on the computer"
        " whose browser the person uses" in row
    )
    assert "to a new file with mode 0600, never overwriting one; it opens no database" in row
    command = cli._parser()._subparsers._group_actions[0].choices[cli_google.COMMAND]
    assert "--db" not in command._option_string_actions
    assert (README.parent / "tests" / "acceptance" / "test_c8_google_authorize.py").is_file()
    assert "`tests/acceptance/test_c8_google_authorize.py`" in row


def test_the_docker_options_say_how_to_switch_the_tidy_off():
    text = README.read_text(encoding="utf-8")
    (line,) = [line for line in text.splitlines() if line.startswith("- **No tidying:**")]
    assert "add `--no-tidy`" in line and "with no model" in line
    words = ["serve", "--db", "/data/cairn.db", "--host", "0.0.0.0", "--no-tidy"]
    assert cli._parser().parse_args(words).no_tidy is True
    assert "## Tidy pages without a model" in (README.parent / "src/cairn/guides/run.md").read_text(
        encoding="utf-8"
    )
