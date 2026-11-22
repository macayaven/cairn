"""Shared test helpers extracted from test_guide_facts_own_project_unit."""

from importlib.resources import files


def guide(name: str) -> str:
    return " ".join(files("cairn.guides").joinpath(f"{name}.md").read_text().split())


RUN_COMMANDS = (  # its list: test_every_command_that_opens_the_database_is_named
    "`serve` runs the upgrade once it holds the lock that keeps one server on the database,"
    " which an older version's server holds too, and only `serve` does; every other command"
    " refuses a database of an older schema with one line on stderr that says to start the"
    " server once on it, exits 1 and changes nothing. An older version's other commands take"
    " no such lock: stop them before the upgrade, as `docker stop cairn` does for those run in"
    " its container, since one still open across the upgrade can write into the upgraded"
    " database."
)
