"""How far a project is shared, in words, for the people who see it (the projects list and a
project's page). The store says how far (Store.project_sharing, or Store.projects_sharing for many
projects in one read); this says it."""

from cairn.memory import Memory
from cairn.store import ProjectSharing


def sharing_words(memory: Memory, project_id: str) -> str | None:
    """How far a project is shared, in words, for whoever sees it (words_for); None for a project
    out of reach."""
    try:
        shared = memory.store.project_sharing(project_id)
    except KeyError:
        return None
    return words_for(shared, memory.store.user_id)


def words_for(shared: ProjectSharing | None, me: str | None) -> str | None:
    """How far a project shared as `shared` is, in words, for the reader `me`; None for a project
    open to every member to write, or one out of reach (None). "Members read only";
    "Private: you", "Private: you and 1 other" when the reader is one of the people its rule
    names; else "Private: 2 people", or "Private: owners only" when its rule names nobody."""
    if shared is None or shared.everyone == "write":
        return None
    if shared.everyone == "read":
        return "Members read only"
    if me in shared.people:
        others = len(shared.people) - 1
        if not others:
            return "Private: you"
        return f"Private: you and {others} {'other' if others == 1 else 'others'}"
    if not shared.people:
        return "Private: owners only"
    count = len(shared.people)
    return f"Private: {count} {'person' if count == 1 else 'people'}"
