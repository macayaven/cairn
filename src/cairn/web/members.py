"""A workspace's people in the web view: /members (the people, their roles, invitations),
/join/<token> (taking an invitation), /workspaces (a person's workspaces, and moving between
them), /b/<project>/access (who reaches a project), /b/<project>/share (one level for a
list of addresses) and /b/<project>/models (M4, frozen by
tests/acceptance/test_m4_a_project_no_model_reads.py: whether a model the operator connects
may be sent the project's pages, posted from the project's page by whoever may change who
reaches it, with `models` "allowed" or "never"; 303 back to the project's page, which says
what is now true; Memory.set_models decides, and a refusal changes nothing).

Every decision is the store's: who manages people (Store.manages, PermissionError from each
call), who may change a project's rule, which projects the caller reaches. The handlers turn the
store's answers into statuses: PermissionError is 403, KeyError (unknown, or out of the
caller's reach) is 404, ValueError is 400. Taking an invitation and moving to another
workspace start a session in another account, so they go through `Accounts`, which holds the
unscoped store; everything else works on the caller's own view.
"""

import re
from typing import Any
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from cairn.memory import ACCESS_LABELS, BadRequest, Forbidden, Memory, NotFound
from cairn.store import LEVELS, ROLES, Store
from cairn.web.auth import Accounts, _memory
from cairn.web.rest import _logged
from cairn.web.ui import _ui

NOT_A_MANAGER = "Only an owner or an admin of this workspace can invite or remove people."
NOT_THE_MAKER = (
    "Only the project's maker, the workspace's owners and a key that belongs to whoever runs the"
    " server, such as one made on its command line, can change who reaches it. So can anyone"
    " this project gives Read and write, and decide who reaches it."
)
DEAD_INVITE = "This invitation is not open: it was used, revoked or replaced, or it expired."
EVERYONE_LABELS = {"write": "Read and write", "read": "Read only", "none": "Nothing: private"}
# A person's level, in the Scope component's words (memory.ACCESS_LABELS) and a third that
# says what it adds; "none" leaves them to what everyone gets.
MANAGE_LABEL = f"{ACCESS_LABELS['write']}, and decide who reaches it"
PERSON_LABELS = {
    "none": "What everyone gets",
    "read": ACCESS_LABELS["read"],
    "write": ACCESS_LABELS["write"],
    "manage": MANAGE_LABEL,
}
SHARE_LABELS = {level: PERSON_LABELS[level] for level in ("read", "write", "manage")}
# What separates the addresses typed into "Add several people".
_SEPARATORS = re.compile(r"[,;\s]+")


def _form_text(form: FormData, name: str) -> str:
    value = form.get(name)
    return value.strip() if isinstance(value, str) else ""


# ASCII letters only, as SQLite's NOCASE folds them.
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def members_page(store: Store, **context: Any) -> dict[str, Any]:
    """What members.html shows: the people, and for a manager the pending invitations.
    `sole_owner`: the only owner's id, whose row offers no role change and no removal (the
    store refuses both: a workspace keeps an owner); None when there are several."""
    manage = store.manages()
    members = store.list_members()
    owners = [member.user_id for member in members if member.role == "owner"]
    return {
        "members": members,
        "sole_owner": owners[0] if len(owners) == 1 else None,
        "invites": store.list_invites() if manage else [],
        "manage": manage,
        "me": store.user_id,
        "roles": ROLES,
        "made": None,
        "error": None,
        "typed": {"email": "", "role": "member"},
        **context,
    }


def access_page(memory: Memory, ref: str, **context: Any) -> dict[str, Any]:
    """What access.html shows; 404 for a project out of reach, 403 for a person who may not see
    or change its rule (Store.project_access). `context`: what "Add several people" answers
    on the page itself, `unknown` (the addresses that are no member's), `error` and `typed`."""
    project = memory.project(ref)
    try:
        rule = memory.store.project_access(project.id)
    except KeyError:
        raise NotFound(f"no project {ref!r}") from None
    except PermissionError:
        raise Forbidden(NOT_THE_MAKER) from None
    members = memory.store.list_members()
    names = {member.user_id: member.name for member in members}
    shown = [m for m in members if m.role != "owner" and m.user_id != rule.maker_id]
    return {
        "project": project,
        "rule": rule,
        "maker": names.get(rule.maker_id or ""),
        "people": shown,
        "levels": LEVELS,
        "everyone_labels": EVERYONE_LABELS,
        "person_labels": PERSON_LABELS,
        "share_labels": SHARE_LABELS,
        "unknown": (),
        "error": None,
        "typed": {"emails": "", "level": "read"},
        **context,
    }


def _people(form: FormData) -> dict[str, str] | None:
    """The `people` the form posts, each "<user id>:<level>" with level "none", "read",
    "write" or "manage" ("none": only what everyone gets, so not in the rule). None when the
    form posts no `people`: the rule's people stay as they are. 400 for anything else."""
    if "people" not in form:
        return None
    people: dict[str, str] = {}
    for entry in form.getlist("people"):
        user_id, _, level = entry.partition(":") if isinstance(entry, str) else ("", "", "")
        if not user_id or level not in PERSON_LABELS or user_id in people:
            raise BadRequest(
                "Each person is given once: what everyone gets, read only, read and write, or"
                " read and write and decide who reaches it."
            )
        people[user_id] = level
    return {user_id: level for user_id, level in people.items() if level != "none"}


def _emails(typed: str) -> list[str]:
    """The addresses typed into "Add several people", separated by commas, semicolons,
    spaces or new lines; the store checks each."""
    return [address for address in _SEPARATORS.split(typed) if address]


def member_routes(accounts: Accounts, public_url: str | None) -> list[Route]:
    def link_base(request: Request) -> str:
        return (public_url or str(request.base_url)).rstrip("/")

    def members(request: Request) -> Response:
        """Everyone in the workspace sees its people; a manager also gets the forms."""
        return _ui(request, "members.html", **members_page(_memory(request).store))

    async def invite(request: Request) -> Response:
        """An invitation, its link shown on this response only (the store keeps a digest)."""
        store = _memory(request).store
        form = await request.form()
        typed = {"email": _form_text(form, "email"), "role": _form_text(form, "role")}
        try:
            made = await run_in_threadpool(store.invite, typed["email"], typed["role"])
        except PermissionError:
            raise Forbidden(NOT_A_MANAGER) from None
        except ValueError as refused:
            error = f"That invitation cannot be made: {refused}."
            page = await run_in_threadpool(members_page, store, error=error, typed=typed)
            return _ui(request, "members.html", status_code=400, **page)
        shown = {
            "email": made.email,
            "role": made.role,
            "expires_at": made.expires_at,
            "link": f"{link_base(request)}/join/{made.token}",
        }
        page = await run_in_threadpool(members_page, store, made=shown)
        return _ui(request, "members.html", **page)

    async def revoke_invite(request: Request) -> Response:
        store = _memory(request).store
        invite_id = request.path_params["invite"]
        try:
            revoked = await run_in_threadpool(store.revoke_invite, invite_id)
        except PermissionError:
            raise Forbidden(NOT_A_MANAGER) from None
        if not revoked:
            raise NotFound(f"no open invitation {invite_id!r}")
        return RedirectResponse("/members", status_code=303)

    async def set_role(request: Request) -> Response:
        store = _memory(request).store
        user_id = request.path_params["user"]
        role = _form_text(await request.form(), "role")
        try:
            await run_in_threadpool(store.set_role, user_id, role)
        except PermissionError as refused:
            raise Forbidden(f"{str(refused)[:1].upper()}{str(refused)[1:]}.") from None
        except KeyError:
            raise NotFound(f"no member {user_id!r}") from None
        except ValueError as refused:
            raise BadRequest(f"That role cannot be given: {refused}.") from None
        return RedirectResponse("/members", status_code=303)

    async def remove(request: Request) -> Response:
        """Removes a person: their sessions here, the agents they approved and the keys they
        made end with it, in one transaction (Store.remove_member)."""
        store = _memory(request).store
        user_id = request.path_params["user"]
        try:
            removed = await run_in_threadpool(store.remove_member, user_id)
        except PermissionError as refused:
            raise Forbidden(f"{str(refused)[:1].upper()}{str(refused)[1:]}.") from None
        except ValueError as refused:
            raise BadRequest(f"That person cannot be removed: {refused}.") from None
        if not removed:
            raise NotFound(f"no member {user_id!r}")
        return RedirectResponse("/members", status_code=303)

    def _for_them(person: object, invited: str) -> bool:
        """Whether the signed-in person is the one the invitation is for, as the store decides
        it (Store.accept_invite: the addresses equal, ASCII letters in either case)."""
        email = getattr(person, "email", None)
        return email is not None and email.translate(_ASCII_LOWER) == invited.translate(
            _ASCII_LOWER
        )

    def join_page(request: Request) -> Response:
        """The invitation's workspace, address and role, and the form that takes it; for a
        person signed in with another address, which address each is and a way to sign out
        instead, since taking it could only be refused. Writes nothing; a dead invitation is
        404."""
        token = request.path_params["token"]
        memory = _memory(request)
        found = memory.store.find_invite(token)
        if found is None:
            raise NotFound(DEAD_INVITE)
        fits = _for_them(memory.person, found.email)
        response = _ui(request, "join.html", invite=found, token=token, fits=fits)
        response.headers["referrer-policy"] = "no-referrer"  # the token is in this URL
        return response

    async def join(request: Request) -> Response:
        """Takes the invitation and starts a session in its workspace."""
        memory = _memory(request)
        token = request.path_params["token"]
        try:
            came_with = request.cookies.get(accounts.sessions.cookie)
            session = await run_in_threadpool(accounts.join, memory, token, came_with)
        except KeyError:
            raise NotFound(DEAD_INVITE) from None
        except PermissionError as refused:
            raise Forbidden(f"{str(refused)[:1].upper()}{str(refused)[1:]}.") from None
        response = RedirectResponse("/", status_code=303)
        accounts.sessions.set(response, session)
        return response

    def workspaces(request: Request) -> Response:
        """The caller's person's workspaces, each with their role there. Writes nothing."""
        store = _memory(request).store
        places = store.list_workspaces(store.user_id) if store.user_id else []
        return _ui(request, "workspaces.html", places=places, here=store.account_id)

    async def switch(request: Request) -> Response:
        """A session in another of the person's workspaces; the one they came with stays."""
        account_id = request.path_params["account"]
        came_with = request.cookies.get(accounts.sessions.cookie)
        memory = _memory(request)
        session = await run_in_threadpool(accounts.switch, memory, account_id, came_with)
        if session is None:
            raise NotFound(f"no workspace {account_id!r} of yours")
        response = RedirectResponse("/", status_code=303)
        accounts.sessions.set(response, session)
        return response

    def access(request: Request) -> Response:
        return _ui(
            request, "access.html", **access_page(_memory(request), request.path_params["project"])
        )

    async def change_access(request: Request) -> Response:
        memory = _memory(request)
        ref = request.path_params["project"]
        form = await request.form()
        everyone = form.get("everyone") if "everyone" in form else None
        people = _people(form)
        project = await run_in_threadpool(memory.project, ref)
        try:
            await run_in_threadpool(
                lambda: memory.store.set_project_access(
                    project.id, everyone=everyone, people=people
                )
            )
        except KeyError:
            raise NotFound(f"no project {ref!r}") from None
        except PermissionError:
            raise Forbidden(NOT_THE_MAKER) from None
        except ValueError as refused:
            raise BadRequest(f"That rule cannot be set: {refused}.") from None
        return RedirectResponse(f"/b/{quote(project.name, safe='')}/access", status_code=303)

    async def share(request: Request) -> Response:
        """ "Add several people": one level for every member in a list of addresses
        (Store.share_project). Every address a member's: 303 back to the access page. Some
        not: the access page again, naming them; the members among them have the level.
        A list or level the store refuses: the page again, 400, with what was typed."""
        memory = _memory(request)
        ref = request.path_params["project"]
        form = await request.form()
        typed = {"emails": _form_text(form, "emails"), "level": _form_text(form, "level")}
        project = await run_in_threadpool(memory.project, ref)
        try:
            shared = await run_in_threadpool(
                memory.store.share_project, project.id, typed["level"], _emails(typed["emails"])
            )
        except KeyError:
            raise NotFound(f"no project {ref!r}") from None
        except PermissionError:
            raise Forbidden(NOT_THE_MAKER) from None
        except ValueError as refused:
            error = f"No one was added: {refused}."
            page = await run_in_threadpool(access_page, memory, ref, error=error, typed=typed)
            return _ui(request, "access.html", status_code=400, **page)
        if not shared.unknown:
            return RedirectResponse(f"/b/{quote(project.name, safe='')}/access", status_code=303)
        page = await run_in_threadpool(access_page, memory, ref, unknown=shared.unknown)
        return _ui(request, "access.html", **page)

    async def models(request: Request) -> Response:
        """Whether a model may be sent the project's pages (Memory.set_models: 400 for another
        value, 403 for a caller who may not change who reaches it, 404 out of reach)."""
        memory = _memory(request)
        value = (await request.form()).get("models")
        said = await run_in_threadpool(memory.set_models, request.path_params["project"], value)
        return RedirectResponse(f"/b/{quote(said['project'], safe='')}", status_code=303)

    return [
        Route("/members", members, methods=["GET"]),
        Route("/members/invite", invite, methods=["POST"]),
        Route("/members/invites/{invite}/revoke", revoke_invite, methods=["POST"]),
        Route("/members/{user}/role", set_role, methods=["POST"]),
        Route("/members/{user}/remove", remove, methods=["POST"]),
        Route("/join/{token}", join_page, methods=["GET"]),
        Route("/join/{token}", join, methods=["POST"]),
        Route("/workspaces", workspaces, methods=["GET"]),
        Route("/workspaces/{account}/switch", switch, methods=["POST"]),
        Route("/b/{project}/access", _logged("project_access", access), methods=["GET"]),
        Route("/b/{project}/access", change_access, methods=["POST"]),
        Route("/b/{project}/share", share, methods=["POST"]),
        Route("/b/{project}/models", models, methods=["POST"]),
    ]
