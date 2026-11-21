"""The REST API: JSON routes over the caller's Memory. Each request is one record on the
access log (Memory.access), named by the MCP tool that does the same, or by what it does when
no tool does.

POST /api/projects/<project>/models (M4, frozen by
tests/acceptance/test_m4_a_project_no_model_reads.py) takes {"models": "allowed" | "never"},
whether a model the operator connects may be sent the project's pages, and answers 200 with
{"project", "models"}, the state now (Memory.set_models). Another value, a missing one or a
key the address does not take is 400, a caller who may not change who reaches the project
403, a project unknown or out of reach 404; none changes anything. It is beside
/api/projects/<project>, as the web view's /b/<project>/models is beside /b/<project>/access,
and no MCP tool does it. Each project of GET /api/projects, and POST /api/projects's answer,
says its state as `models`.

POST /api/integrations/<name>/preview (R1, tests/acceptance/test_r1_preview.py) takes
{"days"} (1 to 30, 7 unless given) and answers 200 with what the source would bring
(cairn.sources.runner.Preview.answer): {"source", "days", "read": true, "count", "titles",
"more", "written_before", "out_of_reach"}, or {"source", "days", "read": false, "said"}
(for a source made from an account, `said` names no variable and no address: _said_of).
400 for `days`, 403 for a caller who may not switch the source on, 404 for no such source,
409 while a preview of it, or of a source of its account, saved or not, is under way. A key
only: an agent's token is 401, and no MCP tool does it. It stores nothing.

Sources made from an account (R2; the store's rules in cairn.store.sources), a key only, no
MCP tool, all under /api/integrations and /api/credentials (no route under a path holding
"/sources" takes anything but GET). A key that acts for a member (R4) reaches the member's
own accounts and the sources made from them, and anything else as not found:
- GET /api/credentials: {"credentials": [{"name", "kind", "owner" (the person's name, or null
  for the workspace's own), "account" (its subject, when it names one), "added_at",
  "sources"}]}: every one for owners, admins and a key acting for no person; a member's own
  for a member; 403 on a narrowed key.
- DELETE /api/credentials/<name>?confirm=<name>: {"credential", "switched_off"}: its owner,
  an owner of the workspace or a key acting for no person (Store.remove_credential).
- GET /api/integrations: {"sources": [...]}, each as list_sources shows it to the caller,
  with `credential` (null: added with source-add, or its account disconnected),
  `disconnected` (true: its account was disconnected, and nothing switches it on: no
  `switch_on` then), `select`, `options`, `reads` and `switch_on` in words, `last_error`,
  `last_run` (the counts), and `held` (R3: [{"title", "link"}], at most 20, the items its
  last run held because another source of its account selects them too), `held_more`, and
  `held_expired` with `held_said` (held items its last run let go, past the look-back).
  No answer holds a variable's name, an address or a subject: where a sentence would, it
  says "the service", "the account" or "its variable" instead.
- POST /api/integrations {"name", "credential", "select", "options", "project",
  "new_project"} (one of the last two, the other null): 201, the source, off
  (Store.add_source_from).
- POST /api/integrations/preview: that body and `days` (1 to 30, 7 unless given): 200 with
  what the source would bring, as /api/integrations/<name>/preview answers, before it is
  saved (R4): refused as saving refuses, in the same words, but for the name, which it
  does not check; 409 while a preview of that account is under way; it stores nothing.
- POST /api/integrations/<name>/on {"projects", "new_project"} and /off (no body): the
  source (Store.enable_source, Store.disable_source).
- DELETE /api/integrations/<name>?confirm=<name>: {"removed", "notes_kept": true}; a
  source added with source-add is removed on the command line only (403).
A refusal is {"error": sentence}: 400 for what the request or the kind refuses (a
selector missing, a setting the account sets, no project, a taken name, a `confirm` that
does not match), 403 in the store's words, 404 for no such source or credential, or one out
of sight."""

import functools
import inspect
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import QueryParams
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from cairn.kinds import GENERAL
from cairn.memory import (
    HITS_RANGE,
    RELATED_RANGE,
    WAITING,
    BadRequest,
    Conflict,
    Forbidden,
    NotFound,
    not_taken,
    without_missing_page,
)
from cairn.relations import RELATED_DEFAULT
from cairn.sources import CHOOSING, KINDS
from cairn.sources.runner import PreviewBusy, preview_days, preview_new_source, preview_source
from cairn.store import CredentialInfo, SourceInfo
from cairn.store.audit import AUDIT_LIMIT_MAX
from cairn.store.sources import FORMER_MEMBER, HOSTED_LEFT
from cairn.store.usage import OverLimit
from cairn.web.auth import _memory, may_manage_credentials
from cairn.web.turns import RELATED_READS, RelatedReads, caller_of, compile_turn

Handler = Callable[[Request], Response | Awaitable[Response]]
AUDIT_AT_ONCE = 100  # the events GET /api/audit answers when no `limit` is given
NOT_AUDITOR = "only a key that manages the workspace reads its audit log"
AUDIT_LIMIT = f"'limit' is a whole number, 1 to {AUDIT_LIMIT_MAX}"


def _logged(
    action: str, handler: Handler, turn: RelatedReads | None = None
) -> Callable[[Request], Awaitable[Response]]:
    """`handler` as one access-log record named `action`, whatever it answers. A sync
    handler still runs on a worker thread, as Starlette runs one: it reads the database. The
    access runs on that thread with it, so a read's audit event (`serve --audit-reads`),
    written behind the store's lock, never holds up the event loop. `__wrapped__` is the
    handler, so a test can read what it reads (inspect.unwrap). With a `turn`, a sync
    handler waits for its caller's turn of it and then the process's on the event loop,
    holding no thread, before its thread is taken."""

    async def endpoint(request: Request) -> Response:
        memory = _memory(request)
        if inspect.iscoroutinefunction(handler):
            with memory.access(action):
                return await handler(request)

        def accessed() -> Response:
            with memory.access(action):
                return handler(request)  # type: ignore[return-value]

        if turn is None:
            return await run_in_threadpool(accessed)
        async with turn.turn(caller_of(memory)):
            return await run_in_threadpool(accessed)

    endpoint.__wrapped__ = handler  # type: ignore[attr-defined]
    return endpoint


async def _body(request: Request, *takes: str) -> dict[str, Any]:
    """The request's JSON object, whose keys are among `takes`, the keys the address takes
    in its own order (X1). Anything else is refused (BadRequest) before the handler does
    anything: a body that is no object, or a key the address does not take, named with
    every key it takes. The one way an /api handler reads a body."""
    try:
        data = await request.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise BadRequest("the body must be a JSON object")
    if unknown := data.keys() - set(takes):
        raise BadRequest(not_taken("key", unknown, "this address", takes))
    return data


def _query(request: Request, *takes: str) -> QueryParams:
    """The request's query, whose parameters are among `takes`, those the address takes in
    its own order (none: it takes no query). A parameter it does not take is refused
    (BadRequest), named with every one it takes (X1). Every /api handler calls it first,
    and reads its query only through what it returns: each parameter keeps its own rules."""
    query = request.query_params
    if unknown := set(query.keys()) - set(takes):
        raise BadRequest(not_taken("query parameter", unknown, "this address", takes))
    return query


def _text(data: dict[str, Any], name: str, *, required: bool = False) -> str | None:
    value = data.get(name)
    if value is None and required:
        raise BadRequest(f"{name!r} is required")
    if value is not None and not isinstance(value, str):
        raise BadRequest(f"{name!r} must be a string")
    return value


def _whole_number(query: QueryParams, name: str, said: str | None = None) -> int | None:
    """`name` read as a whole number, None when left out; BadRequest otherwise, with `said`
    when the door has a sentence of its own for it."""
    value = query.get(name, "")
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        raise BadRequest(said or f"{name!r} must be a whole number") from None


def rest_routes() -> list[Route]:
    def projects(request: Request) -> Response:
        _query(request)
        return JSONResponse(_memory(request).projects())

    async def new_project(request: Request) -> Response:
        _query(request)
        data = await _body(request, "name", "description", "kind", "private")
        name, description = _text(data, "name", required=True), _text(data, "description")
        kind = _text(data, "kind")  # absent or null: general; "" is refused, as over MCP
        kind = GENERAL if kind is None else kind
        private = data.get("private")  # absent: the kind decides; null is refused below
        if "private" in data and not isinstance(private, bool):
            raise BadRequest("'private' must be true or false")
        created = await run_in_threadpool(
            functools.partial(
                _memory(request).new_project, name, description or "", kind, private=private
            )
        )
        return JSONResponse(created, status_code=201)

    async def note(request: Request) -> Response:
        _query(request)
        named = ("title", "topic", "label", "source_ref")
        data = await _body(request, "text", *named)
        text = _text(data, "text", required=True)
        fields = {name: _text(data, name) for name in named}
        async with compile_turn(request):  # G1: waited for on the loop, holding no thread
            noted = await run_in_threadpool(
                functools.partial(
                    _memory(request).note,
                    request.path_params["project"],
                    text,
                    via="api",
                    **fields,
                )
            )
        # N1: a note stored and still waiting for its page is accepted, not created on it
        status = 202 if noted["status"] == WAITING else 201
        return JSONResponse(without_missing_page(noted), status_code=status)

    def pages(request: Request) -> Response:
        """Q1: `limit` and `after` (the `pages_next` of an earlier answer), as the query."""
        query = _query(request, "limit", "after")
        limit, after = _whole_number(query, "limit"), query.get("after") or None
        project = request.path_params["project"]
        return JSONResponse(_memory(request).pages(project, limit=limit, after=after))

    def page(request: Request) -> Response:
        """Q1: `limit` and `before` (the `versions_next` of an earlier answer), as the query."""
        query = _query(request, "version", "limit", "before")
        params = request.path_params
        version = _whole_number(query, "version")
        limit, before = _whole_number(query, "limit"), _whole_number(query, "before")
        memory = _memory(request)
        found = memory.page(params["project"], params["slug"], version, limit=limit, before=before)
        return JSONResponse(found)

    def related(request: Request) -> Response:
        """GET /api/projects/<project>/pages/<slug>/related: the pages related to the page,
        each with why (Memory.related); `limit`, 1 to 20, 5 when left out."""
        limit = _whole_number(_query(request, "limit"), "limit", RELATED_RANGE)
        params = request.path_params
        found = _memory(request).related(
            params["project"], params["slug"], RELATED_DEFAULT if limit is None else limit
        )
        return JSONResponse(found)

    def evidence(request: Request) -> Response:
        """E1: `version` as the query, a version of the page as `page` takes it."""
        version = _whole_number(_query(request, "version"), "version")
        params = request.path_params
        memory = _memory(request)
        found = memory.evidence(params["project"], params["slug"], params["claim_id"], version)
        return JSONResponse(found)

    def usage(request: Request) -> Response:
        _query(request)
        return JSONResponse(_memory(request).usage())

    def delete_project(request: Request) -> Response:
        """DELETE /api/projects/<project>?confirm=<its name> (H5b)."""
        confirm = _query(request, "confirm").get("confirm")
        return JSONResponse(
            _memory(request).delete_project(request.path_params["project"], confirm)
        )

    async def models(request: Request) -> Response:
        """POST /api/projects/<project>/models (M4): {"models": "allowed" | "never"}."""
        _query(request)
        data = await _body(request, "models")
        memory, project = _memory(request), request.path_params["project"]
        said = await run_in_threadpool(memory.set_models, project, data.get("models"))
        return JSONResponse(said)

    async def source_preview(request: Request) -> Response:
        """POST /api/integrations/<name>/preview (R1): {"days"}, what the source would bring."""
        _query(request)
        data = await _body(request, "days")
        return JSONResponse(await previewed(request, request.path_params["name"], data.get("days")))

    def forget(request: Request) -> Response:
        """DELETE /api/projects/<project>/notes/<note id> (H5b): the pages rebuilt and removed."""
        _query(request)
        params = request.path_params
        return JSONResponse(_memory(request).forget(params["project"], params["note_id"]))

    def search(request: Request) -> Response:
        query = _query(request, "q", "project", "limit")
        words, project = query.get("q", ""), query.get("project") or None
        limit = _whole_number(query, "limit", HITS_RANGE)  # the range, as for 0 or 51
        return JSONResponse(_memory(request).recall(words, project, 8 if limit is None else limit))

    def audit(request: Request) -> Response:
        """GET /api/audit: the audit log as the web page reads it (Store.list_audit), newest
        first, for a caller that manages the workspace (403 otherwise, a narrowed key
        included). `before` (an event's number, as `events_next` gives it), `limit` (1 to
        AUDIT_LIMIT_MAX; AUDIT_AT_ONCE when left out), `action` and `project`. Answers
        {"events", "events_total"} and, while older events match, `events_next`."""
        query = _query(request, "before", "limit", "action", "project")
        limit = _whole_number(query, "limit", AUDIT_LIMIT)
        if limit is not None and not 1 <= limit <= AUDIT_LIMIT_MAX:
            raise BadRequest(AUDIT_LIMIT)
        before = _whole_number(query, "before")
        memory = _memory(request)
        if not may_manage_credentials(memory):
            raise Forbidden(NOT_AUDITOR)
        part = memory.store.list_audit(
            AUDIT_AT_ONCE if limit is None else limit,
            before=before,
            action=query.get("action") or None,
            project=query.get("project") or None,
        )
        answer: dict[str, Any] = {
            "events": [event.model_dump() for event in part],
            "events_total": part.total,
        }
        if part and part.newer + len(part) < part.total:
            answer["events_next"] = part[-1].n
        return JSONResponse(answer)

    def credentials(request: Request) -> Response:
        """GET /api/credentials (R2): the credentials the caller may see."""
        _query(request)
        try:
            listed = _memory(request).store.list_credentials()
        except PermissionError as refused:  # a narrowed key: none of the workspace's
            raise Forbidden(str(refused)) from None
        return JSONResponse({"credentials": [_credential_answer(info) for info in listed]})

    def remove_credential(request: Request) -> Response:
        """DELETE /api/credentials/<name>?confirm=<name> (R2): disconnect it. A Google account
        connected in the browser, removed with the switch off (it is refused with it on), adds
        `google`: HOSTED_LEFT, what the Sources page says."""
        name = request.path_params["name"]
        _confirmed(_query(request, "confirm"), name, "disconnect the account")
        store = _memory(request).store
        try:
            hosted = store.credential_hosted(name)
            off = store.remove_credential(name)
        except KeyError:
            raise NotFound(f"no credential named {name!r}") from None
        except PermissionError as refused:
            raise Forbidden(str(refused)) from None
        answer: dict[str, Any] = {"credential": name, "switched_off": off}
        if hosted:
            answer["google"] = HOSTED_LEFT
        return JSONResponse(answer)

    def integrations(request: Request) -> Response:
        """GET /api/integrations (R2): the sources the caller sees, in words."""
        _query(request)
        store = _memory(request).store
        made_from = store.source_credentials()
        listed = store.list_sources()
        return JSONResponse({"sources": [_source_answer(info, made_from) for info in listed]})

    async def add_integration(request: Request) -> Response:
        """POST /api/integrations (R2): a source made from a credential, added off."""
        _query(request)
        data = await _body(
            request, "name", "credential", "select", "options", "project", "new_project"
        )
        name, credential, select, options, project, new = _made_from(data)
        memory = _memory(request)
        info = await _sourced(
            memory, name, f"no credential named {credential!r}",
            functools.partial(
                memory.store.add_source_from, name, credential, select, options, project,
                new_project=new,
            ),
        )  # fmt: skip
        return JSONResponse(info, status_code=201)

    async def new_integration_preview(request: Request) -> Response:
        """POST /api/integrations/preview (R4): the body of POST /api/integrations and `days`;
        what that source would bring, before it is saved. Stores nothing."""
        _query(request)
        data = await _body(
            request, "name", "credential", "select", "options", "project", "new_project", "days"
        )
        made = _made_from(data)
        return JSONResponse(await previewed_new(request, *made, data.get("days")))

    async def integration_on(request: Request) -> Response:
        """POST /api/integrations/<name>/on (R2): {"projects", "new_project"}."""
        _query(request)
        data = await _body(request, "projects", "new_project")
        projects = data.get("projects")
        if projects is not None and (
            not isinstance(projects, list) or not all(isinstance(p, str) for p in projects)
        ):
            raise BadRequest("'projects' lists the names of projects")
        name, memory = request.path_params["name"], _memory(request)
        info = await _sourced(
            memory, name, f"no source named {name!r}",
            functools.partial(
                memory.store.enable_source, name, projects,
                new_project=_text(data, "new_project"),
            ),
        )  # fmt: skip
        return JSONResponse(info)

    async def integration_off(request: Request) -> Response:
        """POST /api/integrations/<name>/off (R2): no body."""
        _query(request)
        name, memory = request.path_params["name"], _memory(request)
        info = await _sourced(
            memory, name, f"no source named {name!r}",
            functools.partial(memory.store.disable_source, name),
        )  # fmt: skip
        return JSONResponse(info)

    def remove_integration(request: Request) -> Response:
        """DELETE /api/integrations/<name>?confirm=<name> (R2): its notes stay."""
        name = request.path_params["name"]
        _confirmed(_query(request, "confirm"), name, "remove the source")
        try:
            removed = _memory(request).store.remove_source(name)
        except PermissionError as refused:
            raise Forbidden(str(refused)) from None
        if not removed:
            raise NotFound(f"no source named {name!r}")
        return JSONResponse({"removed": name, "notes_kept": True})

    pages_path = "/api/projects/{project}/pages"
    return [
        Route("/api/projects", _logged("projects", projects), methods=["GET"]),
        Route("/api/projects", _logged("new_project", new_project), methods=["POST"]),
        Route(
            "/api/projects/{project}", _logged("delete_project", delete_project), methods=["DELETE"]
        ),
        Route("/api/projects/{project}/notes", _logged("note", note), methods=["POST"]),
        Route(
            "/api/projects/{project}/models", _logged("project_models", models), methods=["POST"]
        ),
        Route(
            "/api/projects/{project}/notes/{note_id}",
            _logged("forget", forget),
            methods=["DELETE"],
        ),
        Route(pages_path, _logged("pages", pages)),
        Route(pages_path + "/{slug}", _logged("page", page)),
        Route(pages_path + "/{slug}/related", _logged("related", related, RELATED_READS)),
        Route(pages_path + "/{slug}/claims/{claim_id}/evidence", _logged("evidence", evidence)),
        Route("/api/search", _logged("recall", search)),
        Route("/api/usage", _logged("usage", usage), methods=["GET"]),
        Route(
            "/api/integrations/preview",
            _logged("source_preview_new", new_integration_preview),
            methods=["POST"],
        ),
        Route(
            "/api/integrations/{name}/preview",
            _logged("source_preview", source_preview),
            methods=["POST"],
        ),
        Route("/api/audit", _logged("audit", audit), methods=["GET"]),
        Route("/api/credentials", _logged("credentials", credentials), methods=["GET"]),
        Route(
            "/api/credentials/{name}",
            _logged("credential_remove", remove_credential),
            methods=["DELETE"],
        ),
        Route("/api/integrations", _logged("sources", integrations), methods=["GET"]),
        Route("/api/integrations", _logged("source_add", add_integration), methods=["POST"]),
        Route(
            "/api/integrations/{name}",
            _logged("source_remove", remove_integration),
            methods=["DELETE"],
        ),
        Route(
            "/api/integrations/{name}/on", _logged("source_on", integration_on), methods=["POST"]
        ),
        Route(
            "/api/integrations/{name}/off",
            _logged("source_off", integration_off),
            methods=["POST"],
        ),
    ]


def _made_from(data: dict[str, Any]) -> tuple[str, str, Any, Any, str | None, str | None]:
    """(name, credential, select, options, project, new_project) of a body that makes a
    source from an account (POST /api/integrations, and its preview), as add_source_from
    takes them: BadRequest for a name or credential missing, or a field of the wrong type."""
    name = _text(data, "name", required=True)
    credential = _text(data, "credential", required=True)
    project, new = _text(data, "project"), _text(data, "new_project")
    select = {} if data.get("select") is None else data["select"]
    options = {} if data.get("options") is None else data["options"]
    return name, credential, select, options, project, new  # type: ignore[return-value]


def _confirmed(query: QueryParams, name: str, what: str) -> None:
    """BadRequest unless `confirm` is `name` (ignoring case), as a delete asks (H5b)."""
    confirm = query.get("confirm")
    if confirm is None or confirm.casefold() != name.casefold():
        raise BadRequest(f"to {what}, send confirm={name}: its name, as a check")


async def _sourced(memory: Any, name: str, missing: str, call: Callable[[], Any]) -> dict:
    """A store call on a source (R2), run on a worker thread, as the API answers it: the
    source in words (_source_answer), or the store's refusal as 400, 403 or 404 in its own
    words; a plan's cap on projects as every write reports it."""
    try:
        info = await run_in_threadpool(call)
    except KeyError:
        raise NotFound(missing) from None
    except OverLimit as over:
        raise memory.quota_error(over) from None
    except PermissionError as refused:
        raise Forbidden(str(refused)) from None
    except ValueError as wrong:
        raise BadRequest(str(wrong)) from None
    made_from = await run_in_threadpool(memory.store.source_credentials)
    return _source_answer(info, made_from)


# What a source's answer says in place of an account's own settings (R2): it names no
# variable, no address and no subject, whoever added it.
_RUN_COUNTS = (
    "saved",
    "unchanged",
    "refused",
    "rejected",
    "skipped",
    "malformed",
    "left",
    "capped",
)
_IN_PLACE = {"subject": "the account", "base_url": "the service", "url": "the service"}
# What a preview of a source made from an account says when the server lacks its key.
ACCOUNT_KEY_UNSET = "its account's key is not set on this server; ask the person who runs it"
PREVIEW_UNREAD = "the service could not be read"  # when nothing else can be said safely


def _hidden(kind: str, config: dict[str, Any]) -> list[tuple[str, str]]:
    """(text, what to say instead) for each value of the source's credential settings, an
    address's forms included (with and without its scheme, its path and its port, and its
    host alone), longest first."""
    found: list[tuple[str, str]] = []
    choosing = CHOOSING.get(kind)
    names = choosing.credential if choosing else tuple(k for k in config if k.endswith("_env"))
    for setting in names:
        value = config.get(setting)
        if not isinstance(value, str) or not value:
            continue
        said = _IN_PLACE.get(setting, "its variable")
        found.append((value, said))
        if setting in ("base_url", "url"):
            parts = urlsplit(value)
            found += [
                (f"{parts.scheme}://{parts.netloc}{parts.path}", said),
                (f"{parts.scheme}://{parts.netloc}", said),
                (f"{parts.netloc}{parts.path}", said),
                (parts.netloc, said),
                (parts.hostname or "", said),
            ]
    return sorted((pair for pair in found if pair[0]), key=lambda pair: -len(pair[0]))


def _scrubbed(text: str | None, hidden: list[tuple[str, str]]) -> str | None:
    """`text` with every value of `hidden` said in words instead, whatever its case; None
    when one is left, compared the same way."""
    if text is None:
        return None
    for value, said in hidden:
        text = re.sub(re.escape(value), said, text, flags=re.IGNORECASE)
    folded = text.casefold()
    return None if any(value.casefold() in folded for value, _ in hidden) else text


def _source_answer(info: SourceInfo, made_from: dict[str, str | None]) -> dict[str, Any]:
    """A source as the API shows it (R2): what it reads (its selector, its reading options
    and a sentence), where it writes as far as the caller reaches, its state and its last
    run's counts, and the credential it was made from (None: added with source-add, or its
    credential disconnected). Never a variable's name, an address or a subject."""
    kind = KINDS.get(info.kind)
    choosing = CHOOSING.get(info.kind)
    config = info.config
    hidden = _hidden(info.kind, config)
    reads = kind.describe(config) if kind is not None and kind.describe is not None else None
    key = info.name.casefold()
    disconnected = key in made_from and made_from[key] is None  # linked, its account gone
    switch_on = None
    if kind is not None and not info.enabled and not disconnected:
        switch_on = kind.switching_on(config=config)
    report = info.last_report or {}
    return {
        "name": info.name,
        "kind": info.kind,
        "credential": made_from.get(key),
        "disconnected": disconnected,
        "enabled": info.enabled,
        "enabled_at": info.enabled_at,
        "projects": list(config.get("projects", [])),
        "unreached": info.unreached,
        "select": {k: config[k] for k in (choosing.selector if choosing else ()) if k in config},
        "options": {k: config[k] for k in (choosing.options if choosing else ()) if k in config},
        "reads": _scrubbed(reads, hidden),
        "switch_on": _scrubbed(switch_on, hidden),
        "created_at": info.created_at,
        "last_run_at": info.last_run_at,
        "last_error": _scrubbed(info.last_error, hidden) if info.last_error else None,
        "last_run": {k: report[k] for k in _RUN_COUNTS if k in report},
        # R3: the items its last run held, another source of its account selecting them too
        "held": [{"title": d["title"], "link": d["link"]} for d in report.get("held_items", [])],
        "held_more": report.get("held_more", 0),
        "held_expired": report.get("held_expired", 0),
        "held_said": _held_said(report.get("held_expired", 0)),
    }


def _held_said(expired: int) -> str | None:
    """R3: the sentence on held items that left the look-back window, or None."""
    if not expired:
        return None
    items = "item" if expired == 1 else "items"
    return f"{expired} held {items} left the look-back window unwritten"


def _credential_answer(info: CredentialInfo) -> dict[str, Any]:
    """A credential as GET /api/credentials shows it: whose (the person's name, or None for
    the workspace's own), the account's address when it names one, and how many sources."""
    return {
        "name": info.name,
        "kind": info.kind,
        "owner": None if info.owner is None else info.owner_name or FORMER_MEMBER,
        "account": info.account,
        "added_at": info.added_at,
        "sources": info.sources,
    }


async def previewed(request: Request, name: str, days: object) -> dict[str, Any]:
    """A preview of the caller's source `name` (cairn.sources.runner.preview_source), read
    through the app's source client (`app.state.source_http`) with the process's environment,
    as the caller: BadRequest for `days`, Forbidden for a caller who may not switch the source
    on (in the store's words), NotFound for no such source, Conflict for a preview of it, or
    of a source of its account, saved or not, already under way. Stores nothing."""
    store = _memory(request).store
    try:
        days = preview_days(days)
        async with request.app.state.source_http() as http:
            shown = await preview_source(store, name, http=http, env=os.environ, days=days)
    except ValueError as wrong:
        raise BadRequest(str(wrong)) from None
    except KeyError:
        raise NotFound(f"no source named {name!r}") from None
    except PermissionError as refused:
        raise Forbidden(str(refused)) from None
    except PreviewBusy as busy:
        raise Conflict(str(busy)) from None
    answer = shown.answer()
    if answer.get("said") is not None:
        answer["said"] = await run_in_threadpool(_said_of, store, name, answer["said"])
    return answer


async def previewed_new(
    request: Request,
    name: str,
    credential: str,
    select: Any,
    options: Any,
    project: str | None,
    new_project: str | None,
    days: object,
) -> dict[str, Any]:
    """A preview before saving (cairn.sources.runner.preview_new_source) of the source the
    caller would make from the account `credential`, through the app's source client with
    the process's environment: refused as saving refuses but for the name, which it does
    not check (NotFound for an account out of sight, in the words of one that does not
    exist; Forbidden and BadRequest in the store's words; a plan's cap as every write
    reports it), BadRequest for `days`, Conflict while a preview of a source of that
    account, saved or not, is under way. Stores nothing; `said`
    names no variable and no address of the account (_said_in)."""
    memory = _memory(request)
    try:
        return await preview_unsaved(
            request, name, credential, select, options, project, new_project, days
        )
    except KeyError:
        raise NotFound(f"no credential named {credential!r}") from None
    except OverLimit as over:
        raise memory.quota_error(over) from None
    except PermissionError as refused:
        raise Forbidden(str(refused)) from None
    except ValueError as wrong:
        raise BadRequest(str(wrong)) from None
    except PreviewBusy as busy:
        raise Conflict(str(busy)) from None


async def preview_unsaved(
    request: Request,
    name: str,
    credential: str,
    select: Any,
    options: Any,
    project: str | None,
    new_project: str | None,
    days: object,
) -> dict[str, Any]:
    """previewed_new with the store's and the runner's own refusals (KeyError, ValueError,
    PermissionError, OverLimit, PreviewBusy), so the Sources page says them as saving does."""
    store = _memory(request).store
    async with request.app.state.source_http() as http:
        shown = await preview_new_source(
            store, name, credential, select, options, project, new_project=new_project,
            http=http, env=os.environ, days=days,
        )  # fmt: skip
    answer = shown.answer()
    if answer.get("said") is not None:
        info = await run_in_threadpool(
            functools.partial(
                store.source_from_to_preview, name, credential, select, options, project,
                new_project=new_project,
            )
        )  # fmt: skip
        answer["said"] = _said_in(info, answer["said"])
    return answer


def _said_of(store: Any, name: str, said: str) -> str:
    """Why a preview could not read the source, as the person is told it (R2): for a source
    made from an account, with no variable and no address (ACCOUNT_KEY_UNSET when it says a
    variable of the account is not set or empty, else _hidden's words, else PREVIEW_UNREAD);
    a source added with source-add keeps the sentence as it was (R1)."""
    info = store.get_source(name)
    if info is None or info.name.casefold() not in store.source_credentials():
        return said
    return _said_in(info, said)


def _said_in(info: SourceInfo, said: str) -> str:
    """Why a preview could not read a source made from an account, with no variable and no
    address of its config (_said_of's rule)."""
    folded = said.casefold()
    variables = [v for k, v in info.config.items() if k.endswith("_env") and isinstance(v, str)]
    named = [v for v in variables if v and v.casefold() in folded]
    if named and (" is not set" in folded or " is empty" in folded):
        return ACCOUNT_KEY_UNSET
    return _scrubbed(said, _hidden(info.kind, info.config)) or PREVIEW_UNREAD
