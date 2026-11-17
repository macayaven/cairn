"""What the design system's components need that a page's view does not hold as it is: an
actor's kind as the word a reader sees, a connection's scope (and where a source writes) as
rows that end with what is out of reach, and a source span as the note's own numbered lines.
Everything here is worked out from what a route has already loaded; none of it reads the
store."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cairn.memory import ACCESS_LABELS, reach_label
from cairn.web.brand import SOFTWARE

CONTEXT_LINES = 2  # lines of the note shown before and after the lines a source span holds


# Actor


def actor_kind(kind: str, actor_id: str | None = None, product: str = SOFTWARE) -> str:
    """The word an audit event's actor is shown with: "key", "agent" for an OAuth token,
    the product's name for the system (`product`: the brand's, or the software's without
    one), and for a session "person" or "key", by the id it was recorded under (a person's
    "usr_…", a key's "key_…"). A session recorded before actors had ids could be either,
    and is shown as "session" rather than guessed."""
    if kind == "token":
        return "agent"
    if kind == "system":
        return product
    if kind == "session":
        if actor_id and actor_id.startswith("key_"):
            return "key"
        if actor_id and actor_id.startswith("usr_"):
            return "person"
        return "session"
    return kind


# What a key made (A1): the words key-list, the Keys and Agents pages and a revoke say.
MADE_WITH_REVOKED = "made with a revoked key"  # never the label, which a new key may take


def made_with_words(made_with: str | None, revoked: bool) -> str | None:
    """The key a key or an agent was made with, as a reader is shown it; None for one made
    any other way."""
    if made_with is not None:
        return f"made with {made_with}"
    return MADE_WITH_REVOKED if revoked else None


def unrecorded_words(moment: str) -> str:
    """The line said of a key made before recording what keys make began (`moment`, the
    schema step's): by key-revoke, in the key's row on the Keys page, and on its revoke
    page, in the same words. It names no key and no product."""
    at = datetime.fromisoformat(moment).astimezone(UTC)
    return f"What it made before {at:%Y-%m-%d %H:%M} UTC was not recorded."


def arranger(by: str, product: str) -> str:
    """Who arranged a page, as a reader is shown it: the product's name for the software's
    own tidy (recorded under the software's name, arrange.TIDY, an identifier), else the
    model's name as recorded. Reading arrange.TIDY here would load the arranging code into
    a server that runs without it (serve --no-tidy)."""
    return product if by == SOFTWARE else by


# Scope


@dataclass(frozen=True)
class ScopeRow:
    mode: str  # ACCESS_LABELS' words
    write: bool
    what: str


@dataclass(frozen=True)
class Scope:
    rows: tuple[ScopeRow, ...]
    beyond: str  # what is out of reach, in words


def scope_of(
    reach: Sequence[str] | None,
    access: str,
    names: dict[str, str],
    sets: Iterable[Any],
    projects: Iterable[Any],
    beyond_all: str,
) -> Scope:
    """A key's or an agent's reach as rows (the mode, then a project or a set and its projects
    by name), and what is out of reach: `beyond_all` for one that reaches every project;
    else every other project, with how many of them the reader sees (`projects`, the reader's
    own list, so a project hidden from the reader is neither named nor counted)."""
    mode, write = ACCESS_LABELS.get(access, access), access == "write"
    if reach is None:
        return Scope((ScopeRow(mode, write, "All projects"),), beyond_all)
    groups = {group.id: group for group in sets}
    rows, covered = [], set()
    for ref in reach:
        if ref in groups:
            group = groups[ref]
            covered |= set(group.project_ids)
            held = reach_label(tuple(group.project_ids), names)
            rows.append(ScopeRow(mode, write, f"set {group.name}: {held}"))
        elif ref in names:
            covered.add(ref)
            rows.append(ScopeRow(mode, write, names[ref]))
    if not rows:
        rows.append(ScopeRow(mode, write, "No project"))
    others = sum(1 for project in projects if project.id not in covered)
    seen = f" ({others} you can see)" if others else ""
    return Scope(tuple(rows), f"every other project{seen}")


UNREACHED_DESTINATION = "a project you do not reach"  # how a source's other projects are shown


def destination_of(projects: Sequence[str], unreached: bool = False) -> Scope:
    """Where a source writes (S2), as a Scope: a row a project it names, then one row for the
    projects the viewer does not reach, with no name (SourceInfo.unreached), then every other
    project, which it never writes into. A source that names none yet says so."""
    rows = tuple(ScopeRow("Writes", True, name) for name in projects)
    if unreached:
        rows += (ScopeRow("Writes", True, UNREACHED_DESTINATION),)
    return Scope(rows or (ScopeRow("Writes", False, "No project yet"),), "every other project")


# Source


def numbered_lines(span: Any, note: Any | None) -> list[tuple[int, str, bool]]:
    """(line number, the line, cited) for a source span: the lines it holds at the note's
    own numbers, with up to CONTEXT_LINES of the note around them. When the note is not at
    hand, or its text does not hold the span at that range, the span's own lines at their
    numbers and nothing around them."""
    held = span.text.splitlines() or [""]
    start, end = span.start_line, span.end_line
    lines = note.text.splitlines() if note is not None else []
    if lines[start - 1 : end] != held:
        return [(start + offset, line, True) for offset, line in enumerate(held)]
    first, last = max(1, start - CONTEXT_LINES), min(len(lines), end + CONTEXT_LINES)
    return [(n, lines[n - 1], start <= n <= end) for n in range(first, last + 1)]
