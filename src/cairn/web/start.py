"""The guided setup: what a person who has just signed in will use cairn for, the projects for
it, and how each agent or app connects to them.

- GET /start asks which uses fit: one checkbox `use` per kind except "general", each with a
  name field `name-<kind>` holding the kind's name hint.
- POST /start makes one project per ticked use, of that kind, under the name given, and
  answers 303 to /start/connect. Each project gets its kind's rule (cairn.kinds.Kind.private:
  a personal project is private to its maker and the owners, any other open to every member).
  Every ticked use and name is checked before any project is
  made, so a refused form makes nothing: nothing ticked, a use the page does not offer, a
  name that is not valid or one name for two uses is 400; a name taken by a project of
  another kind is 409. Each answer is the form again with a message (role="alert") and
  what was ticked and typed: only the uses offered (an unknown use id is never echoed),
  each name cut to NAME_SHOWN characters and an ellipsis. Fields the form does not have
  are ignored, and the form parser refuses a body of more than 1,000 fields. The projects are
  made in one store transaction (`create_projects`), all or none. A project that already exists
  with that name and kind is kept, so the form can be sent twice, or twice at once. The
  setup makes projects; it never renames or deletes one.
- GET /start/connect shows, for each project of the workspace, how what uses it connects:
  agents a person signs in from (kind.connects "agents") get the MCP URL and the
  `claude mcp add` and `codex mcp add` commands, or with OAuth off the way to a key; an app
  (kind.connects "app") gets a form posting to /keys with the fields that route takes, for
  a key that reaches only its project, to write. Someone `may_manage_credentials` refuses
  sees who can make that key instead of the form.
- Opening either page reads and never writes.
"""

from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from cairn.kinds import GENERAL, KINDS, Kind, kind_of
from cairn.memory import ID_PREFIX, Forbidden, Memory, name_unavailable
from cairn.store import PROJECT_NAME, NameTaken, is_project_name
from cairn.store.usage import OverLimit
from cairn.web.auth import _memory, may_manage_credentials
from cairn.web.brand import Brand, product_of
from cairn.web.templating import _page, brand_of

# Every kind but "general", which is what a project is when nothing more specific fits.
OFFERED: tuple[Kind, ...] = tuple(kind for kind in KINDS.values() if kind.id != GENERAL)
NOTHING_TICKED = "Tick at least one use, so {product} knows which projects to make."
NAME_RULE = (
    "use 1 to 64 letters, digits, dots, dashes or underscores, starting with a letter or digit"
)
NAME_SHOWN = 64  # the longest valid name: what a refused form shows of a typed name
NOT_OFFERED = "A use was sent that is not on this page. Tick only the uses shown."


class _Refused(Exception):
    """A choice on the form that makes nothing: shown with the form again."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _choices(form: FormData) -> tuple[list[str], dict[str, str]]:
    """What was ticked, in order and once each, and the name typed beside every use."""
    ticked = list(dict.fromkeys(v for v in form.getlist("use") if isinstance(v, str)))
    names = {}
    for kind in OFFERED:
        typed = form.get(f"name-{kind.id}")
        names[kind.id] = typed.strip() if isinstance(typed, str) else kind.name_hint
    return ticked, names


def _bounded(name: str) -> str:
    """A typed name as a refused form shows it: at most NAME_SHOWN characters, then an
    ellipsis, so what is echoed stays small whatever was sent."""
    return name if len(name) <= NAME_SHOWN else name[:NAME_SHOWN] + "\N{HORIZONTAL ELLIPSIS}"


def _wanted(ticked: list[str], names: dict[str, str], product: str) -> list[tuple[Kind, str]]:
    """The projects to make, (kind, name), once the form's own checks pass. `product`: the
    product's name, as a refusal says it."""
    offered = {kind.id: kind for kind in OFFERED}
    if not ticked:
        raise _Refused(400, NOTHING_TICKED.format(product=product))
    wanted = []
    for kind_id in ticked:
        if kind_id not in offered:
            raise _Refused(400, NOT_OFFERED)  # never echoed: it is whatever was sent
        kind, name = offered[kind_id], names[kind_id]
        if not is_project_name(name):
            shown = f"{_bounded(name)!r} is not" if name else "A blank name is not"
            rule = ID_PREFIX if PROJECT_NAME.fullmatch(name) else NAME_RULE  # only "br_" is left
            raise _Refused(400, f"{shown} a valid name for {kind.title}: {rule}.")
        wanted.append((kind, name))
    seen: dict[str, Kind] = {}
    for kind, name in wanted:
        if (other := seen.setdefault(name.casefold(), kind)) is not kind:
            raise _Refused(
                400, f"{other.title} and {kind.title} are both named {name!r}: give each its own."
            )
    return wanted


def _make(memory: Memory, wanted: list[tuple[Kind, str]]) -> None:
    """Make every project not already there, in one store transaction: all or none. A project
    with the same name and kind (made earlier, or by a form sent at the same moment) is kept;
    a name taken by a project of another kind refuses the whole form and makes nothing."""
    try:
        memory.store.create_projects([(name, kind.id) for kind, name in wanted])
    except NameTaken as taken:  # ProjectTaken included: one sentence, seen or hidden
        kind, name = next((k, n) for k, n in wanted if n.casefold() == taken.name.casefold())
        raise _Refused(
            409, f"{name_unavailable(name)} Choose another name for {kind.title}."
        ) from None
    except OverLimit as over:  # the plan's projects: all or none, so none
        quota = memory.quota_error(over)
        memory.record_refusal("start", None, quota)
        raise _Refused(quota.status, str(quota)) from None
    except (
        PermissionError
    ) as refused:  # a session always reaches every project; a guard all the same
        raise _Refused(Forbidden.status, f"{str(refused)[:1].upper()}{str(refused)[1:]}.") from None


def _form(
    memory: Memory,
    status: int = 200,
    *,
    brand: Brand | None,
    ticked: list[str],
    names: dict[str, str],
    error: str | None = None,
) -> Response:
    return _page(
        "start.html",
        status_code=status,
        brand=brand,
        person=memory.person,
        signed_in=True,
        offered=OFFERED,
        ticked=ticked,
        names=names,
        error=error,
    )


def start_routes(mcp_url: str, *, oauth: bool) -> list[Route]:
    """`mcp_url`: where agents connect; `oauth`: whether they can do so with the URL alone."""

    def start(request: Request) -> Response:
        names = {kind.id: kind.name_hint for kind in OFFERED}
        return _form(_memory(request), brand=brand_of(request), ticked=[], names=names)

    async def choose(request: Request) -> Response:
        form = await request.form()
        memory = _memory(request)
        ticked, names = _choices(form)
        try:
            product = product_of(brand_of(request)).name
            await run_in_threadpool(_make, memory, _wanted(ticked, names, product))
        except _Refused as refused:  # shown again: the uses offered, each name bounded
            offered = {kind.id for kind in OFFERED}
            return _form(
                memory,
                refused.status,
                brand=brand_of(request),
                ticked=[kind_id for kind_id in ticked if kind_id in offered],
                names={kind_id: _bounded(name) for kind_id, name in names.items()},
                error=str(refused),
            )
        return RedirectResponse("/start/connect", status_code=303)

    def connect(request: Request) -> Response:
        memory = _memory(request)
        projects = memory.store.list_projects()
        by: dict[str, list[Any]] = {"agents": [], "app": []}
        for project in projects:
            by[kind_of(project.kind).connects].append(project)
        return _page(
            "start_connect.html",
            brand=brand_of(request),
            person=memory.person,
            signed_in=True,
            agents=by["agents"],
            apps=by["app"],
            manage=may_manage_credentials(memory),
            mcp_url=mcp_url,
            oauth=oauth,
        )

    return [
        Route("/start", start, methods=["GET"]),
        Route("/start", choose, methods=["POST"]),
        Route("/start/connect", connect, methods=["GET"]),
    ]
