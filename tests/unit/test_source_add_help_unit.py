"""Unit tests for K1: `source-add --help` is built one way for the kinds that came with C9
and after. Linear's and GitHub's settings are their kinds' `settings` (SourceKind.settings),
as the connector's are, and the command's own text no longer names either kind. The help
reads byte for byte as it did before: source_add_config_help.txt is the --config help as
the command gave it at bb9ee4f, before the move, with what K2 added since: Linear's and
GitHub's `backfill_days`."""

import argparse
import inspect
from pathlib import Path

from cairn.sources import KINDS
from cairn.web import cli

BEFORE = (Path(__file__).parent / "source_add_config_help.txt").read_text(encoding="utf-8")


def config_help() -> str:
    """The help of source-add's --config, as the parser holds it (not wrapped to a terminal)."""
    parser = cli._parser()
    (commands,) = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    (config,) = [
        a for a in commands.choices["source-add"]._actions if "--config" in a.option_strings
    ]
    return config.help


def test_the_help_reads_as_it_did_before_the_move():
    assert config_help() == BEFORE


def test_linear_and_github_say_their_settings_through_their_kind():
    for name in ("linear", "github", "connector"):
        settings = KINDS[name].settings
        assert settings and f". {name}: {settings}" in config_help(), name


def test_the_command_s_own_text_names_neither_kind():
    """What the command writes itself names the kinds that came before C9; Linear, GitHub
    and the connector speak through KINDS."""
    own = inspect.getsource(cli._source_parsers).lower()
    assert "linear" not in own and "github" not in own and "connector" not in own
