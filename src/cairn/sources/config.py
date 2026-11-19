"""Config rules shared by the source kinds."""

import re
from collections.abc import Callable, Mapping
from typing import Any

from cairn.store.memory import is_project_name

# The name of an environment variable a config may give: capitals, digits and _, at most 128.
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")


def check_projects(
    projects: object,
    *,
    kind: str,
    looks_like: Callable[[str], bool] | None = None,
    many: bool = False,
) -> list[str]:
    """Check project names. Most kinds can be off with no project and write to one.
    Langfuse requires at least one name and keeps the first spelling of each."""
    if many:
        if not isinstance(projects, list | tuple) or not projects:
            raise ValueError("projects lists the names of the projects the source may write to")
        bad_name = "projects holds project names (not ids)"
    else:
        if not isinstance(projects, list | tuple):
            raise ValueError("projects is a list of at most one project's name")
        if len(projects) > 1:
            raise ValueError(f"projects names at most one project: a {kind} source writes into one")
        bad_name = "projects holds a project's name (not its id)"

    kept: dict[str, str] = {}
    for project in projects:
        if isinstance(project, str) and looks_like is not None and looks_like(project):
            raise ValueError("projects holds a value that looks like a credential")
        if not isinstance(project, str) or not is_project_name(project):
            raise ValueError(bad_name)
        kept.setdefault(project.casefold(), project)
    return list(kept.values())


def check_env_name(
    config: Mapping[str, Any],
    key: str,
    *,
    kind: str,
    holds: str,
    secret: str,
    looks_like: Callable[[str], bool] | None = None,
    wrong: str | None = None,
) -> str:
    """The name of the environment variable `config[key]` gives (key_env, token_env and
    their like), which holds `holds` (as "the Granola API key"); ValueError when it is
    missing, when it looks like the `secret` itself (`looks_like`, when given), or when it
    is not a variable's name (ENV_NAME). Each refusal names the setting, never its value;
    `wrong` replaces the last one's words for a kind that says it its own way."""
    if key not in config:
        raise ValueError(
            f"a {kind} source needs {key}: the name of the environment variable that holds {holds}"
        )
    value = config[key]
    if isinstance(value, str) and looks_like is not None and looks_like(value):
        raise ValueError(
            f"{key} holds what looks like a {secret}: put the {secret} in an environment"
            " variable of the process that syncs, and name that variable here"
        )
    if not isinstance(value, str) or not ENV_NAME.fullmatch(value):
        raise ValueError(
            wrong
            or f"{key} names an environment variable (capitals, digits and _, at most 128),"
            f" which holds {holds}: the {secret} itself never goes in a config"
        )
    return value
