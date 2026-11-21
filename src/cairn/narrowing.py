"""A long list of projects narrowed without a script (review: UI and UX, suspicion).

The pages that approve an agent (consent.html), make a key (keys.html) and make or change a set
(sets.html) list every project the person may choose from. With more than FILTER_FROM of them,
each such form has a "Filter projects" field and a button that sends the same form with GET to
the same page (formmethod, formaction): the page reads its fields back from the address, so
every box already ticked comes back ticked, and lists the projects whose name holds the text,
case-insensitively, and every ticked one, so nothing is chosen out of sight. It says how many
are shown of how many, with a link to the same choices without the filter.

`narrowed` works on the projects the page has already read: no statement of its own. It grants
nothing either: the POST handlers read only their own fields and check them as before, and a
`filter` field sent with them is ignored. The text is in the address only; cairn logs no
query string (uvicorn's access log is off, and the Caddyfile drops the query), and nothing here
writes it anywhere. With FILTER_FROM or fewer the pages are as they were: `narrowed` is None."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from starlette.datastructures import QueryParams

FILTER_FROM = 20  # more projects than this to choose from, and the list can be narrowed
FILTER_MAX = 64  # characters of filter text read; a project's name is shorter


@dataclass(frozen=True)
class Narrowed:
    projects: list[Any]  # the ones listed: the name holds the text, or ticked
    ticked: frozenset[str]  # ids (projects and sets) ticked on this form
    text: str  # the filter as typed, trimmed
    total: int  # projects to choose from
    everything: str  # the same page with the same choices and no filter
    scope: str = "all"  # the consent's and the key's choices, read back
    access: str = "write"
    typed: str = ""  # the label or the name typed on the form

    @property
    def shown(self) -> int:
        return len(self.projects)


def narrowed(
    projects: Sequence[Any],
    params: QueryParams,
    path: str,
    *,
    mine: bool = True,
    ticked: Sequence[str] = (),
    typed: str | None = None,
) -> Narrowed | None:
    """The list a form shows of `projects`, narrowed by `params` (the address the page was
    asked with); None with FILTER_FROM or fewer. `mine`: the address carries this form's
    fields (else the form shows `ticked`, unfiltered); `typed`: the name of the field whose
    text the form keeps (a key's label, a set's name)."""
    if len(projects) <= FILTER_FROM:
        return None
    if mine:
        text = (params.get("filter") or "").strip()[:FILTER_MAX]
        chosen = frozenset(params.getlist("projects"))
    else:
        text, chosen = "", frozenset(ticked)
    needle = text.casefold()
    listed = [p for p in projects if p.id in chosen or needle in p.name.casefold()]
    rest = [(k, v) for k, v in params.multi_items() if k != "filter"]
    scope = params.get("scope") if mine else None
    access = params.get("access") if mine else None
    return Narrowed(
        projects=listed,
        ticked=chosen,
        text=text,
        total=len(projects),
        everything=f"{path}?{urlencode(rest)}" if rest else path,
        scope=scope if scope in ("all", "selected") else "all",
        access=access if access in ("write", "read") else "write",
        typed=(params.get(typed) or "") if mine and typed else "",
    )
