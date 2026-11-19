"""The workspace and its people: connected agents, keys, sets, the pages before a member is
removed or an invitation revoked, the audit log and its downloads, and what the workspace
has used of its plan."""

import csv
import functools
import io
import json
from collections.abc import Callable, Iterable, Iterator
from typing import Any
from urllib.parse import quote, urlencode

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import QueryParams
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response, StreamingResponse

from cairn.memory import BadRequest, Conflict, Forbidden, Memory, NotFound, choose_reach
from cairn.narrowing import narrowed
from cairn.plans import limit_sentences
from cairn.store.audit import AUDIT_LIMIT_MAX, AuditPart
from cairn.store.models import AuditEvent
from cairn.web.auth import _memory, may_manage_credentials
from cairn.web.ui.common import _ended, _ui
from cairn.web.workspace import part

# What ends with each ending a list offers, said on the page before it (end.html).
KEY_ENDS = (
    "Every script and agent that sends this key stops working at once, and every web session"
    " opened with it ends."
)
MEMBER_ENDS = (
    "Their sessions here end, and the agents they approved and the keys they made stop"
    " working, at once."
)
MEMBER_KEEPS = "What they wrote stays, and they leave every project's list of people."
INVITE_ENDS = "The link stops working: {email} cannot join with it."
AGENT_ENDS = "It stops working at once: its tokens are refused from then on."
SET_ENDS = "Every key and agent that reaches the set stops reaching its projects."
SET_KEEPS = "The projects stay as they are."
KEY_LABEL_MAX = 80  # characters, for a key made in the UI
_NOT_KEYS_MANAGER = (
    "Only an owner or an admin of this workspace can make or revoke its keys, or change its sets."
)
_NOT_AUDITOR = "Only an owner or an admin of this workspace can read its audit log."
AUDIT_SHOWN = 200  # events on /audit, newest first, a part at a time
AUDIT_FILTERS = ("action", "project", "who")  # what /audit and its downloads filter by
AUDIT_FILTER_MAX = 200  # characters of a filter read; the rest is left out
AUDIT_DOWNLOADED = "audit.exported"  # the event a download of the log is recorded as
# A download's columns, in order, and its formats with their media types.
AUDIT_COLUMNS = ("n", "at", "actor_kind", "actor", "actor_id", "action", "project", "detail")
AUDIT_FORMATS = {"csv": "text/csv; charset=utf-8", "json": "application/json"}
NOT_A_FORMAT = "The audit log downloads as csv or json: choose one of them as the format."
# A cell a spreadsheet would read as a formula starts with one of these; it is written as text.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
# The limits /usage shows, in order, and what a person reads for each.
USAGE_NAMES = (
    ("notes_per_month", "Notes written this month"),
    ("storage_bytes", "Notes stored"),
    ("passages", "Passages in notes"),
    ("projects", "Projects"),
    ("model_budget_micros", "Model use this month"),
)


def _audit_filters(request: Request) -> dict[str, str]:
    """The filters /audit and its downloads were given: each of AUDIT_FILTERS that is not
    empty, cut to AUDIT_FILTER_MAX characters, in that order."""
    query = request.query_params
    found = {name: query.get(name, "").strip()[:AUDIT_FILTER_MAX] for name in AUDIT_FILTERS}
    return {name: value for name, value in found.items() if value}


def _audit_cursor(request: Request) -> int | None:
    """`before`, the number below which /audit shows events; None when it is not a whole
    number of 1 or more (a web page ignores what it does not take)."""
    value = request.query_params.get("before", "")
    return int(value) if value.isdigit() and int(value) > 0 else None


def _cell(value: Any) -> Any:
    """A value as a CSV cell: text a spreadsheet would run as a formula gets a "'" first."""
    if isinstance(value, str) and value.startswith(_FORMULA_STARTS):
        return "'" + value
    return value


def _event_row(event: AuditEvent) -> dict[str, Any]:
    return {column: getattr(event, column) for column in AUDIT_COLUMNS}


def _as_csv(parts: Iterable[AuditPart]) -> Iterator[str]:
    """The events as CSV: a header of AUDIT_COLUMNS, then one row each; `detail` as JSON."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(AUDIT_COLUMNS)
    for events in parts:
        for event in events:
            row = _event_row(event)
            row["detail"] = json.dumps(row["detail"], sort_keys=True)
            writer.writerow([_cell(row[column]) for column in AUDIT_COLUMNS])
        yield out.getvalue()
        out.seek(0)
        out.truncate()
    yield out.getvalue()


def _as_json(parts: Iterable[AuditPart]) -> Iterator[str]:
    """The events as one JSON array of objects with AUDIT_COLUMNS, one per line."""
    yield "["
    sep = "\n"
    for events in parts:
        for event in events:
            yield sep + json.dumps(_event_row(event), sort_keys=True)
            sep = ",\n"
    yield "\n]\n"


def workspace_handlers(app_url: str) -> tuple[Callable[..., Any], ...]:
    """The handlers of the workspace's pages, as ui_routes registers them."""

    def agents(request: Request) -> Response:
        """The connected agents, each with what it reaches, by name, and what it does not
        (the projects and sets the reader sees: components.scope_of)."""
        memory = _memory(request)
        grants, names = memory.store.list_grants(), memory.reach_names()
        return _ui(
            request,
            "agents.html",
            grants=grants,
            names=names,
            sets=memory.store.list_sets(),
            projects=memory.store.list_projects(),
        )

    def key_page(
        memory: Memory,
        created: dict[str, str] | None = None,
        params: QueryParams | None = None,
    ) -> dict[str, Any]:
        """What keys.html shows: the keys with their reach by name, what a new key's reach
        can be chosen from (narrowed by `params`, the address the page was asked with, when
        the list is long: cairn.narrowing), and the address an app is given with a new
        key."""
        projects = memory.store.list_projects()
        return {
            "app_url": app_url,
            "keys": memory.store.list_keys(),
            "projects": projects,
            "narrow": narrowed(projects, params or QueryParams(), "/keys", typed="label"),
            "narrow_action": "/keys",
            "sets": memory.store.list_sets(),
            "names": memory.reach_names(),
            "created": created,
            "manage": may_manage_credentials(memory),
            "makers": {member.user_id: member.name for member in memory.store.list_members()},
        }

    def keys(request: Request) -> Response:
        """The workspace's key labels and reach; the form and the revoke buttons only for a
        caller who may manage them."""
        return _ui(request, "keys.html", **key_page(_memory(request), None, request.query_params))

    async def managed_store(request: Request) -> Any:
        """The caller's store, when the caller may manage the workspace's keys and sets; 403
        else."""
        memory = _memory(request)
        if not await run_in_threadpool(may_manage_credentials, memory):
            raise Forbidden(_NOT_KEYS_MANAGER)
        return memory.store

    async def create_key(request: Request) -> Response:
        """A new key for this workspace, with the reach chosen on the form (every project, to
        write, when nothing is chosen), shown on this response only (never stored as is)."""
        store = await managed_store(request)
        form = await request.form()
        label = form.get("label")
        label = label.strip() if isinstance(label, str) else ""
        if not label or len(label) > KEY_LABEL_MAX or not label.isprintable():
            raise BadRequest(f"A key's label has 1 to {KEY_LABEL_MAX} printable characters.")
        projects, access = choose_reach(
            form.get("scope"), form.getlist("projects"), form.get("access")
        )
        try:
            key = await run_in_threadpool(store.create_key, label, projects, access)
        except ValueError as error:
            raise BadRequest(f"That key cannot be made: {error}.") from None
        created = {"label": label, "key": key}
        page = await run_in_threadpool(key_page, _memory(request), created)
        return _ui(request, "keys.html", **page)

    def ask_revoke_key(request: Request) -> Response:
        """GET /keys/<label>/revoke: the page before a key is revoked (key_revoke.html), its
        one form, or both when the key made anything still live (A1). For whoever may manage
        keys (403 else); 404 for no such key. Writes nothing."""
        memory = _memory(request)
        if not may_manage_credentials(memory):
            raise Forbidden(_NOT_KEYS_MANAGER)
        label = request.path_params["label"]
        if not any(key.label == label for key in memory.store.list_keys()):
            raise NotFound(f"no key labelled {label!r}")
        made = memory.store.made_by(label)
        return _ui(request, "key_revoke.html", label=label, made=made, key_ends=KEY_ENDS)

    async def revoke_key(request: Request) -> Response:
        """Revoke the keys of a label. A1: when they made anything still live
        (Store.made_by), and the form chose neither "keep" nor "take" in `made`, a page that
        names what they made and offers both, each its own form; "take" revokes them with
        it, in one transaction (Store.revoke_key's with_made). A key that made nothing is
        revoked at once. 303 to /keys; 404 for no such key."""
        store = await managed_store(request)
        label = request.path_params["label"]
        choice = (await request.form()).get("made")
        if choice not in ("keep", "take"):
            made = await run_in_threadpool(store.made_by, label)
            if made:
                return _ui(request, "key_revoke.html", label=label, made=made, key_ends=KEY_ENDS)
        revoke = functools.partial(store.revoke_key, label, with_made=choice == "take")
        if not await run_in_threadpool(revoke):
            raise NotFound(f"no key labelled {label!r}")
        return RedirectResponse("/keys", status_code=303)

    def sets(request: Request) -> Response:
        """The workspace's sets with their projects by name; the forms only for a caller who
        may manage them (the same rule as keys). A long list of projects can be narrowed on
        each form (cairn.narrowing): `filtering` in the address says which form sent it,
        "new" or a set's id, and only that form reads its ticks and filter from there."""
        memory = _memory(request)
        groups = memory.store.list_sets()
        projects = memory.store.list_projects()
        params, which = request.query_params, request.query_params.get("filtering")
        return _ui(
            request,
            "sets.html",
            sets=groups,
            projects=projects,
            narrow_new=narrowed(projects, params, "/sets", mine=which == "new", typed="name"),
            narrow_sets={
                group.id: narrowed(
                    projects, params, "/sets", mine=which == group.id, ticked=group.project_ids
                )
                for group in groups
            },
            names=memory.reach_names(),
            manage=may_manage_credentials(memory),
        )

    async def create_set(request: Request) -> Response:
        store = await managed_store(request)
        form = await request.form()
        name = form.get("name")
        name = name.strip() if isinstance(name, str) else ""
        try:
            await run_in_threadpool(store.create_set, name, form.getlist("projects"))
        except ValueError as error:
            taken = await run_in_threadpool(store.get_set, name)  # by id or by name
            if taken is not None and taken.name.casefold() == name.casefold():
                raise Conflict(f"A set named {name!r} already exists.") from None
            raise BadRequest(f"That set cannot be made: {error}.") from None
        return RedirectResponse("/sets", status_code=303)

    async def update_set(request: Request) -> Response:
        """Replace a set's projects with the ticked ones (none ticked: an empty set)."""
        store = await managed_store(request)
        form = await request.form()
        set_id = request.path_params["set"]
        try:
            await run_in_threadpool(store.update_set, set_id, form.getlist("projects"))
        except KeyError:
            raise NotFound(f"no set {set_id!r}") from None
        except ValueError as error:
            raise BadRequest(f"That set cannot be changed: {error}.") from None
        return RedirectResponse("/sets", status_code=303)

    def ask_delete_set(request: Request) -> Response:
        """GET /sets/<id>/delete: the page before a set is deleted. For whoever may manage
        sets (403 else); 404 for no such set. Writes nothing."""
        memory = _memory(request)
        if not may_manage_credentials(memory):
            raise Forbidden(_NOT_KEYS_MANAGER)
        found = memory.store.get_set(request.path_params["set"])
        if found is None or found.id != request.path_params["set"]:
            raise NotFound(f"no set {request.path_params['set']!r}")
        return _ended(
            request,
            title=f"Delete the set {found.name}",
            trail=(("/workspace", "Workspace"), ("/sets", "Sets")),
            said=(SET_ENDS, SET_KEEPS),
            action=f"/sets/{quote(found.id, safe='')}/delete",
            button=f"Delete the set {found.name}",
            back="/sets",
        )

    async def delete_set(request: Request) -> Response:
        store = await managed_store(request)
        set_id = request.path_params["set"]
        if not await run_in_threadpool(store.delete_set, set_id):
            raise NotFound(f"no set {set_id!r}")
        return RedirectResponse("/sets", status_code=303)

    def audit(request: Request) -> Response:
        """The workspace's audit log, newest first: who wrote what, and the security events.
        For whoever may manage the workspace's keys; a web session always reaches the whole
        workspace, and a narrowed view's log would be empty anyway. AUDIT_SHOWN events at a
        time: `before` (an event's number) gives the older ones, and `action`, `project` and
        `who` filter (Store.list_audit), all in the address; the page says which events it
        shows of how many, how many the workspace keeps, and links to the downloads."""
        memory = _memory(request)
        if not part("/audit").opens(memory):
            raise Forbidden(_NOT_AUDITOR)
        filters = _audit_filters(request)
        before = _audit_cursor(request)
        events = memory.store.list_audit(AUDIT_SHOWN, before=before, **filters)
        shown = events.newer + len(events)
        older = None
        if events and shown < events.total:
            older = "/audit?" + urlencode({"before": events[-1].n, **filters})
        return _ui(
            request,
            "audit.html",
            events=events,
            first=events.newer + 1,
            last=shown,
            total=events.total,
            kept=memory.store.audit_keep,
            reads=memory.store.reads_audited,
            filters=filters,
            older=older,
            newest=("/audit?" + urlencode(filters)) if filters else "/audit",
            paged=before is not None,
            downloads={
                f: "/audit/download?" + urlencode({"format": f, **filters}) for f in AUDIT_FORMATS
            },
        )

    def audit_download(request: Request) -> Response:
        """Every event of the log the caller sees that matches the filters, newest first, as
        CSV or JSON (`format`), read a part of AUDIT_LIMIT_MAX at a time. For whoever reads
        the page. The download is itself an event, AUDIT_DOWNLOADED, with its format and how
        many events matched; never what was typed in a filter."""
        memory = _memory(request)
        if not part("/audit").opens(memory):
            raise Forbidden(_NOT_AUDITOR)
        form = request.query_params.get("format", "")
        if form not in AUDIT_FORMATS:
            raise BadRequest(NOT_A_FORMAT)
        filters = _audit_filters(request)
        store = memory.store
        first = store.list_audit(AUDIT_LIMIT_MAX, **filters)
        store._record_read(AUDIT_DOWNLOADED, None, {"format": form, "events": first.total})

        def parts() -> Iterator[AuditPart]:
            part_ = first
            while part_:
                yield part_
                if len(part_) < AUDIT_LIMIT_MAX:
                    return
                part_ = store.list_audit(AUDIT_LIMIT_MAX, before=part_[-1].n, **filters)

        body = _as_csv(parts()) if form == "csv" else _as_json(parts())
        return StreamingResponse(
            body,
            media_type=AUDIT_FORMATS[form],
            headers={
                "content-disposition": f'attachment; filename="audit-{store.account_id}.{form}"',
                "cache-control": "no-store",
                "x-content-type-options": "nosniff",
            },
        )

    def usage(request: Request) -> Response:
        """What the workspace has used of its plan, for whoever may see its totals
        (Memory.sees_totals: an owner, or a session opened with a key that acts for no
        person); 403 otherwise, from Memory.usage. What the plan allows is said in its own
        words (cairn.plans.limit_sentences), as a 402 says it."""
        memory = _memory(request)
        allows = list(limit_sentences(memory.plan[1]).values()) if memory.plan else []
        return _ui(request, "usage.html", usage=memory.usage(), names=USAGE_NAMES, allows=allows)

    def ask_disconnect(request: Request) -> Response:
        """GET /agents/<grant>/revoke: the page before an agent is disconnected; 404 for an
        agent the caller does not see on /agents (Store.list_grants). Writes nothing."""
        grant_id = request.path_params["grant"]
        found = next((g for g in _memory(request).store.list_grants() if g.id == grant_id), None)
        if found is None:
            raise NotFound(f"no connected agent {grant_id!r}")
        return _ended(
            request,
            title=f"Disconnect {found.client_name}",
            trail=(("/workspace", "Workspace"), ("/agents", "Connected agents")),
            said=(AGENT_ENDS,),
            action=f"/agents/{quote(found.id, safe='')}/revoke",
            button=f"Disconnect {found.client_name}",
            back="/agents",
        )

    def ask_remove_member(request: Request) -> Response:
        """GET /members/<user>/remove (its POST is cairn.web.members'): the page before a
        person is removed. For whoever manages people (403 else); 404 for no such member.
        Writes nothing."""
        from cairn.web.members import NOT_A_MANAGER  # it imports this module

        store = _memory(request).store
        if not store.manages():
            raise Forbidden(NOT_A_MANAGER)
        user_id = request.path_params["user"]
        found = next((m for m in store.list_members() if m.user_id == user_id), None)
        if found is None:
            raise NotFound(f"no member {user_id!r}")
        return _ended(
            request,
            title=f"Remove {found.name}",
            trail=(("/workspace", "Workspace"), ("/members", "Members")),
            said=(MEMBER_ENDS, MEMBER_KEEPS),
            action=f"/members/{quote(found.user_id, safe='')}/remove",
            button=f"Remove {found.name}",
            back="/members",
            keep=f"No, keep {found.name}",
        )

    def ask_revoke_invite(request: Request) -> Response:
        """GET /members/invites/<id>/revoke (its POST is cairn.web.members'): the page before
        an invitation is revoked. For whoever manages people (403 else); 404 for no open
        invitation. Writes nothing."""
        from cairn.web.members import NOT_A_MANAGER  # it imports this module

        store = _memory(request).store
        if not store.manages():
            raise Forbidden(NOT_A_MANAGER)
        invite_id = request.path_params["invite"]
        found = next((i for i in store.list_invites() if i.id == invite_id), None)
        if found is None:
            raise NotFound(f"no open invitation {invite_id!r}")
        return _ended(
            request,
            title=f"Revoke the invitation for {found.email}",
            trail=(("/workspace", "Workspace"), ("/members", "Members")),
            said=(INVITE_ENDS.format(email=found.email),),
            action=f"/members/invites/{quote(found.id, safe='')}/revoke",
            button="Revoke the invitation",
            back="/members",
        )

    async def disconnect(request: Request) -> Response:
        grant_id = request.path_params["grant"]
        if not await run_in_threadpool(_memory(request).store.revoke_grant, grant_id):
            raise NotFound(f"no connected agent {grant_id!r}")
        return RedirectResponse("/agents", status_code=303)

    return (
        agents,
        keys,
        create_key,
        ask_revoke_key,
        revoke_key,
        sets,
        create_set,
        update_set,
        ask_delete_set,
        delete_set,
        audit,
        audit_download,
        usage,
        ask_disconnect,
        ask_remove_member,
        ask_revoke_invite,
        disconnect,
    )
