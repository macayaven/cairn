"""Sources (S2; frozen by tests/acceptance/test_s2_source_switch.py). GET /sources opens for
whoever reaches the whole workspace (cairn.web.workspace.may_see_sources; 403 for a narrowed
session) and shows each source the viewer handles (Store.list_sources: every one, whatever it
writes into, to owners and admins; to a member, those of their own account, R4, and with none
only NOTHING_READ and NO_ACCOUNT, no form) as a section: its state in words
("On since <when>" or "Off"), where it writes as a Scope (the projects the viewer reaches by
name, the others as one row, "a project you do not reach"), what it reads (in
the words of its kind, SourceKind.describe; no such fact for a kind that does not describe
itself), its last run, and the forms that apply. An off source has one form, which posts to
/integrations/<name>/on and says what switching on reads, as the kind says it
(SourceKind.switching_on: "only what happens from then on" for a kind that starts off).
For a source that names no project, of a kind with a default project (SourceKind.default_project),
the form ticks a project of that name the viewer reaches, else fills it in as the new project's
name; only the viewer's own projects are looked at, so it tells nothing of others. The forms
offer what Store.enable_source allows the viewer: an owner (or a session acting for no
person) every project they reach and a new project; an admin only the projects every member
reaches (the project's rule gives everyone "read" or "write"), and no new project. An
on source has a quiet button that posts to /integrations/<name>/off and, under "Change
where it writes", a form that posts to /integrations/<name>/on with other projects. A source
that writes into a project an admin does not reach offers that admin only the switch off
(Store.enable_source refuses the rest: OWNER_DECIDES). No
credential and no environment variable's value is shown. The switches live under
/integrations/, never under /sources, which only answers GET (decision 25: the web view
adds no source from a config; it makes one from an account the operator registered, R2).

The page for a person who has never seen a command line (R2, section 5 of the contract):
on top, NOTHING_READ; then "Your accounts" (the credentials the viewer may make sources
from: their own and the workspace's, each with its service, whose, the account's address
when known, and how many sources read through it); then each source with its first line in
words ("From your Gmail: messages labelled “to-team” → into decisions"). A source made from
an account names no variable, no address and no subject anywhere on the page
(cairn.web.rest._hidden); one added on the command line is said in its kind's words, as
before. A source made from another person's account offers the viewer only the switch off.
Last, "Choose something new": choosing the account is a GET of this page with
`?account=<name>` (no script: the kind's fields depend on the account), which shows a form
posting to /integrations/new with that kind's selector fields (CHOOSE_FIELDS), where it
writes, a Preview button (R4: what saving would bring, before saving) and two buttons, "Save,
off" and "Save and switch on". A member's own source offers the projects they manage and a
new private one, as their account's form does.
- POST /integrations/new: `credential`, `name`, `select_<setting>` for each of the kind's
  selector settings (blank: left out; Linear's teams separated by commas), `project` or
  `new_project`, `days`, and `then` ("on" switches it on once saved; "preview" saves
  nothing and answers 200 with the page, the form still filled and the result above its Save
  buttons, cairn.web.rest.preview_unsaved). Store.add_source_from, then
  Store.enable_source for "on". 303 to /sources?saved=<name>#source-<name>-name; 400 or 403
  with the page filled again with what was typed, the refusal on top (id "refused") naming
  its field, which points at it with aria-describedby; 404 for an account out of sight.
- The four endings are said beside their buttons before they are done (SWITCH_OFF_SAYS,
  REMOVE_SAYS, DISCONNECT_SAYS, FORGET_SAYS, in the sources guide's words). POST
  /integrations/<name>/remove and POST /integrations/accounts/<name>/disconnect ask first:
  without `confirm` they answer the question (source_end.html: what it does, and the name to
  type, as a project's delete asks); with the name, Store.remove_source or
  Store.remove_credential and 303 to /sources; another name, the question again, 400. 404
  for no such source or account in sight (for a member, anything not of their own
  account); 403 for a member with no account, and in the store's words for whoever may not
  (a member's own account, which an owner disconnects).
- POST /integrations/<name>/on, for owners, admins and a member's own source (a web
  session, same-origin, as every form): `project` (repeatable: names of existing projects;
  blanks are left out) and `new_project` (optional: the name of a project to make, private,
  of the general kind, made by the person who posted). The source is switched on with the
  ticked projects, plus the new one, or with the projects it names when neither is given
  (Store.enable_source, one write: the new project is made only when the switch works). 303
  to /sources; 400 with the reason on /sources when the kind or the store refuses (a taken
  or invalid new name, no project, a project that does not exist or that the person may
  only read, an admin's new project or private project: OWNER_DECIDES); 404 for no such
  source; 403 for a member with no account.
- POST /integrations/<name>/off switches it off (Store.disable_source): 303 to /sources; 404
  for no such source; 403 for a member with no account.
- POST /integrations/<name>/preview (R1): `days` (optional, for an off source). 200 with the
  Sources page, the preview (cairn.web.rest.previewed) in the source's section, in a region
  labelled by its heading and marked aria-live="polite"; the form posts to the heading's
  fragment, so the browser moves to it. 400, 403 or 409 with the refusal on top; 404 for no
  such source; 403 for a member with no account. Stores nothing."""

import functools
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from cairn.memory import BadRequest, Conflict, Forbidden, NotFound
from cairn.sources.runner import PREVIEW_DAYS, PREVIEW_DAYS_MAX, PreviewBusy
from cairn.store.sources import HOSTED_LEFT, NotASourceManager
from cairn.store.usage import OverLimit
from cairn.web.auth import _memory
from cairn.web.brand import product_of
from cairn.web.components import destination_of
from cairn.web.rest import _scrubbed, preview_unsaved, previewed
from cairn.web.templating import _page, brand_of
from cairn.web.ui.common import _ui
from cairn.web.ui.memory_pages import _sees_rule
from cairn.web.ui.source_forms import (
    _NOT_SOURCES,
    ACCOUNT_FIELD,
    CHANGE_SAYS,
    DISCONNECT_SAYS,
    FORGET_SAYS,
    HELD_SAID,
    NO_ACCOUNT,
    NOTHING_READ,
    PREVIEW_SAYS,
    REMOVE_SAYS,
    REMOVED_THERE,
    SOURCE_COUNTS,
    SWITCH_OFF_SAYS,
    _account_rows,
    _chosen,
    _days_typed,
    _new_source_form,
    _options,
    _other_accounts,
    _proposed,
    _refused_field,
    _refused_setting,
    _source_rows,
)
from cairn.web.workspace import part


def source_handlers() -> tuple[Callable[..., Any], ...]:
    """The handlers of the Sources page and its forms, as ui_routes registers them."""

    def sources_page(
        request: Request,
        status_code: int = 200,
        error: str | None = None,
        preview: dict[str, Any] | None = None,
        *,
        account: str | None = None,
        typed: dict[str, Any] | None = None,
        invalid: str | None = None,
        saved: str | None = None,
        unsaved: dict[str, Any] | None = None,
    ) -> Response:
        """/sources: each source with its state, where it writes and the switches (see the
        module), with `error` said on top when a switch was refused, and `preview` (R1: a
        preview's answer) in its source's section. `account`: the account chosen for a new
        source, whose kind's fields the form shows, filled with `typed` and with `invalid`
        (WHAT_FIELD, WHERE_FIELD or NAME_FIELD) pointing at the refusal. `saved`: the source
        just saved, off. `unsaved`: a preview before saving (R4), shown in the new-source form
        above its Save buttons."""
        memory = _memory(request)
        found = memory.store.list_sources()
        reached = memory.store.list_projects()  # only what the viewer reaches
        store = memory.store
        decides = store.user_id is None or store.role() == "owner"  # Store.enable_source's rule
        offered = (
            reached
            if decides
            else [b for b in reached if store.project_sharing(b.id).everyone != "none"]
        )
        product = product_of(brand_of(request)).name
        rows = _source_rows(store, found, product)
        managed = None  # the projects the viewer manages, read once when a source is theirs
        for row in rows.values():  # where each source's forms may send it (Store.enable_source)
            if row["mine"]:
                if managed is None:
                    managed = [b for b in reached if decides or _sees_rule(memory, b)]
                row["projects"], row["decides"] = managed, True
            else:
                row["projects"], row["decides"] = offered, decides
        manages = store.manages()
        if preview is not None and preview.get("said") and preview.get("source") in rows:
            hidden = rows[preview["source"]]["hidden"]
            preview = {**preview, "said": _scrubbed(preview["said"], hidden) or "it was refused"}
        accounts = _account_rows(store)
        new = _new_source_form(memory, accounts, account, typed or {}, invalid, product)
        return _page(
            "sources.html",
            status_code,
            brand=brand_of(request),
            person=memory.person,
            signed_in=True,
            sources=found,
            manages=manages,
            # R4: a member with no account and no source sees the one sentence, and no form.
            bare=not manages and not accounts and not found,
            counts=SOURCE_COUNTS,
            projects=offered,
            decides=decides,
            destinations={
                s.name: destination_of(s.config.get("projects", []), s.unreached) for s in found
            },
            rows=rows,
            accounts=accounts,
            others=_other_accounts(store),
            choosable=[row for row in accounts if not row["refused"]],
            new=new,
            saved=saved,
            nothing_read=NOTHING_READ,
            no_account=NO_ACCOUNT,
            removed_there=REMOVED_THERE,
            held_said=HELD_SAID,
            says={
                "preview": PREVIEW_SAYS,
                "off": SWITCH_OFF_SAYS,
                "remove": REMOVE_SAYS,
                "disconnect": DISCONNECT_SAYS,
                "forget": FORGET_SAYS,
                "change": CHANGE_SAYS,
            },
            proposed={s.name: _proposed(s, reached, decides) for s in found},
            error=error,
            preview=preview,
            unsaved=unsaved,
            preview_days=PREVIEW_DAYS,
            preview_days_max=PREVIEW_DAYS_MAX,
        )

    def sources(request: Request) -> Response:
        """The Sources page (see the module), for whoever may manage the workspace's keys.
        `account`: the account chosen for a new source; `saved`: the source just saved."""
        memory = _memory(request)
        if not part("/sources").opens(memory):
            raise Forbidden(_NOT_SOURCES)
        query = request.query_params  # a web page: what it does not know it ignores (X1)
        return sources_page(
            request, account=query.get("account") or None, saved=query.get("saved") or None
        )

    async def new_source(request: Request) -> Response:
        """POST /integrations/new (R2): a source made from an account (Store.add_source_from)
        with what the person typed, saved off, then switched on when `then` is "on". 303 to
        its section, with "saved"; the page again, filled with what was typed and the
        refusal on top naming its field, when the store or the kind refuses."""
        memory = _memory(request)
        if not await run_in_threadpool(part("/sources").opens, memory):
            raise Forbidden(_NOT_SOURCES)
        form = await request.form()
        credential = form.get("credential")
        credential = credential if isinstance(credential, str) else ""
        held = {
            info.name.casefold(): info
            for info in await run_in_threadpool(memory.store.list_credentials)
        }
        if credential.casefold() not in held:
            raise NotFound(f"no account named {credential!r}")
        kind = held[credential.casefold()].kind
        select, shown = _chosen(kind, form)
        options, set_options = _options(kind, form)
        name, project, new = (
            value.strip() if isinstance(value, str) else ""
            for value in (form.get("name"), form.get("project"), form.get("new_project"))
        )
        typed = {
            "name": name, "select": shown, "options": set_options, "project": project,
            "new_project": new,
        }  # fmt: skip
        make = functools.partial(
            memory.store.add_source_from, name, credential, select, options, project or None,
            new_project=new or None,
        )  # fmt: skip
        then = form.get("then")
        days = _days_typed(form)
        if then == "preview":  # R4: what saving would bring, saving nothing
            typed["days"] = days if days is not None else ""
        try:
            if then == "preview":
                shown = await preview_unsaved(
                    request, name, credential, select, options, project or None, new or None,
                    days,
                )  # fmt: skip
                page = functools.partial(
                    sources_page, request, 200, None, None, account=credential, typed=typed,
                    unsaved=shown,
                )  # fmt: skip
                return await run_in_threadpool(page)
            await run_in_threadpool(make)
        except KeyError:  # gone since the account was looked up
            raise NotFound(f"no account named {credential!r}") from None
        except PreviewBusy as busy:
            said = f"{ACCOUNT_FIELD}: {busy}."  # a preview holds the account it reads
            page = functools.partial(
                sources_page, request, 409, said, account=credential, typed=typed
            )
            return await run_in_threadpool(page)
        except OverLimit as over:
            raise memory.quota_error(over) from None
        except (ValueError, PermissionError) as refused:
            field = _refused_field(refused, kind)
            typed["refused_setting"] = _refused_setting(refused, kind)
            said = f"{field}: {refused}."
            status = 403 if isinstance(refused, PermissionError) else 400
            page = functools.partial(
                sources_page, request, status, said,
                account=credential, typed=typed, invalid=field,
            )  # fmt: skip
            return await run_in_threadpool(page)
        if then == "on":
            try:
                await run_in_threadpool(memory.store.enable_source, name)
            except (ValueError, PermissionError) as refused:
                said = f"Source {name!r} was saved, off, and not switched on: {refused}."
                return await run_in_threadpool(sources_page, request, 400, said)
            return RedirectResponse(f"/sources#source-{quote(name)}-name", status_code=303)
        return RedirectResponse(
            f"/sources?saved={quote(name)}#source-{quote(name)}-name", status_code=303
        )

    async def switch_on(request: Request) -> Response:
        memory = _memory(request)
        if not await run_in_threadpool(part("/sources").opens, memory):
            raise Forbidden(_NOT_SOURCES)
        form = await request.form()
        name = request.path_params["name"]
        ticked = [b.strip() for b in form.getlist("project") if isinstance(b, str) and b.strip()]
        typed = form.get("new_project")
        new = typed.strip() if isinstance(typed, str) and typed.strip() else None
        projects = ticked if ticked or new else None  # neither: the projects it names
        try:
            await run_in_threadpool(
                functools.partial(memory.store.enable_source, name, projects, new_project=new)
            )
        except KeyError:
            raise NotFound(f"no source named {name!r}") from None
        except OverLimit as over:  # the plan's projects, all made
            raise memory.quota_error(over) from None
        except NotASourceManager as refused:  # no source is theirs: none to switch (R4)
            raise Forbidden(str(refused)) from None
        except (ValueError, PermissionError) as refused:
            said = f"Source {name!r} was not switched on: {refused}."
            return await run_in_threadpool(sources_page, request, 400, said)
        return RedirectResponse("/sources", status_code=303)

    def asked(
        request: Request,
        ending: str,
        name: str,
        status_code: int = 200,
        error: str | None = None,
        *,
        hosted: bool = False,
    ) -> Response:
        """The question before an ending that cannot be undone from the page: what it does,
        and the name typed as a check, as a project's delete asks (cairn.web.erasure)."""
        what = {
            "remove": ("Remove the source", REMOVE_SAYS, f"/integrations/{quote(name)}/remove"),
            "disconnect": (
                "Disconnect the account",
                DISCONNECT_SAYS,
                f"/integrations/accounts/{quote(name)}/disconnect",
            ),
        }[ending]
        title, said, action = what
        if hosted:  # a Google account connected in the browser, removed with the switch off
            said = f"{said} {HOSTED_LEFT}"
        return _ui(
            request,
            "source_end.html",
            status_code,
            title=title,
            ended=name,
            said=said,
            action=action,
            error=error,
        )

    async def remove(request: Request) -> Response:
        """POST /integrations/<name>/remove: without `confirm`, the question; with the source's
        name, Store.remove_source (its notes stay) and 303 to /sources; another name, the
        question again, 400. 404 for no such source, or one the store removes nothing of
        for this view (it writes where the view does not reach), 403 for whoever may not."""
        memory = _memory(request)
        if not await run_in_threadpool(part("/sources").opens, memory):
            raise Forbidden(_NOT_SOURCES)
        name = request.path_params["name"]
        found = await run_in_threadpool(memory.store.get_source, name)
        if found is None:
            raise NotFound(f"no source named {name!r}")
        confirm = (await request.form()).get("confirm")
        if confirm is None:
            return asked(request, "remove", found.name)
        if not isinstance(confirm, str) or confirm.strip().casefold() != found.name.casefold():
            told = f"Type {found.name} to remove it: its name, as a check."
            return asked(request, "remove", found.name, 400, told)
        try:
            removed = await run_in_threadpool(memory.store.remove_source, found.name)
        except PermissionError as refused:
            raise Forbidden(str(refused)) from None
        if not removed:  # one it writes where the view does not reach: nothing changed
            raise NotFound(f"no source named {name!r}")
        return RedirectResponse("/sources", status_code=303)

    async def disconnect_account(request: Request) -> Response:
        """POST /integrations/accounts/<name>/disconnect: as remove, for an account
        (Store.remove_credential: its sources are switched off and stay listed)."""
        memory = _memory(request)
        if not await run_in_threadpool(part("/sources").opens, memory):
            raise Forbidden(_NOT_SOURCES)
        name = request.path_params["name"]
        held = {
            info.name.casefold(): info.name
            for info in await run_in_threadpool(memory.store.list_credentials)
        }
        if name.casefold() not in held:
            raise NotFound(f"no account named {name!r}")
        name = held[name.casefold()]
        confirm = (await request.form()).get("confirm")
        hosted = getattr(memory.store, "google_custody", None) is None and (
            await run_in_threadpool(memory.store.credential_hosted, name)
        )  # with the switch on, Store.remove_credential refuses it (HOSTED_DISCONNECT)
        if confirm is None:
            return asked(request, "disconnect", name, hosted=hosted)
        if not isinstance(confirm, str) or confirm.strip().casefold() != name.casefold():
            told = f"Type {name} to disconnect it: its name, as a check."
            return asked(request, "disconnect", name, 400, told, hosted=hosted)
        try:
            await run_in_threadpool(memory.store.remove_credential, name)
        except KeyError:
            raise NotFound(f"no account named {name!r}") from None
        except PermissionError as refused:
            raise Forbidden(str(refused)) from None
        if hosted:  # the switch is off (it refuses otherwise): say what is left at Google
            return _ui(request, "source_ended.html", title="Account removed", ended=name,
                       said=HOSTED_LEFT)  # fmt: skip
        return RedirectResponse("/sources", status_code=303)

    async def switch_off(request: Request) -> Response:
        memory = _memory(request)
        if not await run_in_threadpool(part("/sources").opens, memory):
            raise Forbidden(_NOT_SOURCES)
        name = request.path_params["name"]
        try:
            await run_in_threadpool(memory.store.disable_source, name)
        except KeyError:
            raise NotFound(f"no source named {name!r}") from None
        except PermissionError as refused:  # NotASourceManager: no source is theirs (R4)
            raise Forbidden(str(refused)) from None
        return RedirectResponse("/sources", status_code=303)

    async def preview(request: Request) -> Response:
        """POST /integrations/<name>/preview (R1): the Sources page with what the source would
        bring in its section, or the refusal on top."""
        memory = _memory(request)
        if not await run_in_threadpool(part("/sources").opens, memory):
            raise Forbidden(_NOT_SOURCES)
        form = await request.form()
        name = request.path_params["name"]
        days = _days_typed(form)
        try:
            said = await previewed(request, name, days)
        except (BadRequest, Forbidden, Conflict) as refused:
            told = f"Source {name!r} was not previewed: {refused}."
            return await run_in_threadpool(sources_page, request, refused.status, told)
        return await run_in_threadpool(sources_page, request, 200, None, said)

    return sources, new_source, switch_on, remove, disconnect_account, switch_off, preview
