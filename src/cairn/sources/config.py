"""Config rules shared by the source kinds."""

from collections.abc import Callable

from cairn.store.memory import is_project_name


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
