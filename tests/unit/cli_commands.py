"""The commands of `python -m cairn.server` and the smallest arguments that make each open
its database.

Not a test module: the tests of what every command answers about its database (a newer one,
one it cannot open) import it. The commands are read from the parser, so one added later is
covered as soon as it has an entry in ARGS; test_newer_database_cli_unit.py fails until then.
A command that opens no database (it takes no --db) is named in NO_DATABASE instead, on
purpose and by name: test_newer_database_cli_unit.py fails when the parser and the two lists
disagree, so a command cannot fall out of the database tests by accident.
"""

from cairn.store import DEFAULT_ACCOUNT_ID
from cairn.web import cli

# What each command takes after `--db PATH`: the smallest list that makes it open the
# database. "{dir}" is a directory the test makes, so a file a command would make lands where
# the test looks for new files. archive.zip and plans.yaml are there before the command runs.
ARGS: dict[str, list[str]] = {
    "serve": [],
    "key-create": ["--label", "ci", "--out", "{dir}/key.env"],
    "key-revoke": ["--label", "ci"],
    "key-list": [],
    "session-end": ["--email", "ada@example.test"],
    "source-add": ["--name", "traces", "--kind", "langfuse", "--config", "{{}}"],
    "source-list": [],
    "source-sync": ["--name", "traces"],
    "source-remove": ["--name", "traces"],
    "source-enable": ["--name", "traces", "--project", "core"],
    "source-disable": ["--name", "traces"],
    "source-preview": ["--name", "traces"],  # R1
    "credential-add": ["--name", "acct", "--kind", "github", "--config", '{{"token_env": "T"}}'],
    "credential-list": [],  # R2
    "credential-remove": ["--name", "acct"],
    "export": ["--account", DEFAULT_ACCOUNT_ID, "--out", "{dir}/export.zip"],
    "import": ["--account", DEFAULT_ACCOUNT_ID, "--in", "{dir}/archive.zip"],
    "compact": [],
    "plan-set": ["--account", DEFAULT_ACCOUNT_ID, "--default"],
    "usage": ["--account", DEFAULT_ACCOUNT_ID],
    "workspace-list": [],
    "arrange": ["--account", DEFAULT_ACCOUNT_ID, "--tidy"],
    "waiting": [],  # N1
    "stats": [],  # Q1: it only reads
    "reseal": ["--slack"],  # the keys are read once the database is open
}
# The commands that only read their database (Q1): each reads one on a volume it may not
# write, where SQLite lets it, instead of refusing as the commands that write must.
READS_ONLY = ("stats",)
# The commands that open no database and take no --db, each with why.
NO_DATABASE: dict[str, str] = {
    "google-authorize": "obtains a Google credential into a file; it reads and writes no store",
    "connector-check": "asks a connector, prints a line per rule; it reads and writes no store",
}
# Other ways in that print something of their own before or after opening the store.
VARIANTS: list[tuple[str, list[str]]] = [
    ("source-sync", ["--all"]),
    ("source-sync", ["--name", "traces", "--plans", "{dir}/plans.yaml"]),
    ("import", [*ARGS["import"], "--plans", "{dir}/plans.yaml"]),
    ("usage", [*ARGS["usage"], "--plans", "{dir}/plans.yaml"]),
    ("arrange", [*ARGS["arrange"], "--plans", "{dir}/plans.yaml"]),
]
VARIANT_IDS = [" ".join([c, *(a for a in e if a.startswith("--"))]) for c, e in VARIANTS]
PLANS_YAML = "default: tiny\nplans:\n  tiny: {label: Tiny}\n"
_CHOICES = cli._parser()._subparsers._group_actions[0].choices
ALL_COMMANDS = sorted(_CHOICES)
# The commands of the parser that open a database: every one not named in NO_DATABASE.
COMMANDS = sorted(command for command in ALL_COMMANDS if command not in NO_DATABASE)


def db_options(command: str) -> list:
    """The command's --db options, as the parser has them: one, or none."""
    return [a for a in _CHOICES[command]._actions if "--db" in a.option_strings]


def db_help(command: str) -> str:
    """The help of the command's --db option, as the parser has it."""
    (option,) = db_options(command)
    return option.help


# The commands that make the database when there is none: their --db help does not say it
# must exist. The others answer "no database at PATH".
CREATES = sorted(command for command in COMMANDS if db_help(command) != cli.DB_EXISTS)
