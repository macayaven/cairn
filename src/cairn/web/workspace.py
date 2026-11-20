"""GET /workspace: the workspace's own page, which holds everything the main navigation
leaves out (Projects, Search and New note are there; the rest is here).

Each part is an entry: a link and one sentence saying what it is for. An entry is shown
exactly when its page opens for the caller, and the page and the entry ask the same
function (`Part.opens`): the audit log asks `may_manage_credentials`, the sources ask the store
whether the caller may list accounts (`may_see_sources`: a member too, R4), and every other
part opens for anyone signed in to the workspace. A page that may refuse a caller asks its
part here, so the two cannot drift apart.

Under the parts, the section "guard" says what the note guard refuses in this workspace
(cairn.guard.policy_words), and for its managers (`may_manage_credentials`, the rule
Store.set_setting enforces inside the write) holds the form that changes it: POST
/workspace/guard. The store decides who may and which values exist; this turns its answers
into statuses (PermissionError 403, ValueError 400). A server given a guard of its own
(`create_app(guard=...)`) ignores the workspace's choice, so there the section says the
operator decides, offers no form, and the POST is 409.
"""

from collections.abc import Callable
from dataclasses import dataclass

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from cairn.guard import POLICIES, policy_choice, policy_words
from cairn.memory import BadRequest, Conflict, Forbidden, Memory
from cairn.store import DEFAULT_GUARD_POLICY, POLICY_SETTING
from cairn.web.auth import _memory, may_manage_credentials
from cairn.web.brand import product_of
from cairn.web.templating import _page, brand_of


def _anyone(memory: Memory) -> bool:
    """Open to whoever is signed in to the workspace, a member included."""
    return True


def may_see_sources(memory: Memory) -> bool:
    """Whether the Sources page opens for the caller (R4): for anyone signed in to the
    workspace with its whole reach, a member included, who sees there only the sources of
    their own account (Store.list_sources); not for a session narrowed to some projects or to
    reading, which the store refuses the workspace's accounts (Store.list_credentials)."""
    try:
        memory.store.list_credentials()
    except PermissionError:
        return False
    return True


@dataclass(frozen=True)
class Part:
    """One entry of the workspace page: its label, its address, what it is for, and whether
    its page opens for a caller."""

    label: str
    href: str
    sentence: str
    opens: Callable[[Memory], bool] = _anyone


PARTS: tuple[Part, ...] = (
    Part(
        "Agents",
        "/agents",
        "The agents you approved in the browser, what each one reaches, and where to"
        " disconnect one.",
    ),
    Part(
        "Keys",
        "/keys",
        "API keys for scripts and agents without a browser, and the projects each key reaches.",
    ),
    Part(
        "Sets",
        "/sets",
        "Named groups of projects, so a key or an agent can be given a whole group at once.",
    ),
    Part(
        "Members",
        "/members",
        "The people in this workspace and their roles, and invitations for new people.",
    ),
    Part(
        "Sources",
        "/sources",
        "Systems that write notes into chosen projects on their own, and how each last ran.",
        may_see_sources,
    ),
    Part(
        "Usage",
        "/usage",
        "What this workspace has used this month, against the limits of its plan.",
        Memory.sees_totals,
    ),
    Part(
        "Audit log",
        "/audit",
        "Who wrote what in this workspace, and every change to its keys, agents and people.",
        may_manage_credentials,
    ),
    Part(
        "Setup",
        "/start/connect",
        "How the agents and apps that use each project connect to it.",
    ),
    Part(
        "Data and deletion",
        "/account",
        "Download this workspace's notes and pages as one file, or delete the workspace.",
    ),
)


def part(href: str) -> Part:
    """The part at `href`, for the page that answers there to ask whether it opens."""
    return next(p for p in PARTS if p.href == href)


def parts_for(memory: Memory) -> list[Part]:
    """The parts whose pages open for this caller, in order."""
    return [p for p in PARTS if p.opens(memory)]


OPERATOR_DECIDES = (
    "The operator of this server decides what notes are refused here, so it is not chosen"
    " on this page"
)
NOT_A_MANAGER = "Only an owner or an admin of this workspace can change what notes are refused"
NOT_A_POLICY = "Choose one of the choices offered for what notes are refused"


def workspace_routes(*, own_guard: bool = False) -> list[Route]:
    """`own_guard`: the server was given a guard of its own, which every note passes instead
    of the workspace's policy."""

    def workspace(request: Request) -> Response:
        memory = _memory(request)
        store = memory.store
        account = store.get_account(store.account_id)
        role = store.role() if store.user_id else None
        policy = None if own_guard else store.get_setting(POLICY_SETTING, DEFAULT_GUARD_POLICY)
        brand = brand_of(request)
        product = product_of(brand).name
        return _page(
            "workspace.html",
            brand=brand,
            person=memory.person,
            signed_in=True,
            account=account,
            role=role,
            parts=parts_for(memory),
            policy=policy,
            policy_said=policy_words(policy, product) if policy else OPERATOR_DECIDES + ".",
            policies=[(value, *policy_choice(value)) for value in POLICIES],
            chooses=policy is not None and may_manage_credentials(memory),
        )

    async def choose_guard(request: Request) -> Response:
        """Sets the workspace's guard policy (Store.set_setting: recorded as setting.changed,
        and nothing written when it already has that value)."""
        if own_guard:
            raise Conflict(OPERATOR_DECIDES)
        store = _memory(request).store
        value = (await request.form()).get("policy")
        try:
            await run_in_threadpool(
                store.set_setting, POLICY_SETTING, value if isinstance(value, str) else ""
            )
        except PermissionError:
            raise Forbidden(NOT_A_MANAGER) from None
        except ValueError:
            raise BadRequest(NOT_A_POLICY) from None
        return RedirectResponse("/workspace", status_code=303)

    return [
        Route("/workspace", workspace, methods=["GET"]),
        Route("/workspace/guard", choose_guard, methods=["POST"]),
    ]
