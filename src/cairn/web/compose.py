"""Writing from the web view: the note composers and the new-project form (with its kind).

Every form here is plain HTML posted with a web session; Auth has already refused it when it
came from another site. A note goes through `Memory.note`, as the `note` tool's does, with
via "web". Errors come back as the same form with a message (role="alert") and what was
typed still in it; an unknown project, or another account's, raises NotFound like everywhere
else. A note the note guard refuses comes back the same way: nothing of it is stored, and
the form holds the text and every field exactly as typed (escaped, like any value), with
each rule and its line or field listed above it, so the writer removes those and saves
again. What was typed goes back only to the session that sent it; no log or audit event
holds it. Opening a composer reads and never writes. The note form sent after the sign-in
ended comes back the same way, 401, with a link to sign in in a new tab (signed_out_draft,
which Auth calls).
"""

import functools
import re
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from cairn.guard import rule_words
from cairn.kinds import GENERAL
from cairn.memory import (
    CHECKED_FIELDS,
    WAITING,
    BadRequest,
    Conflict,
    FiledOnce,
    Memory,
    NoteForgotten,
    OverQuota,
    PageFull,
    Refused,
)
from cairn.sources import near_miss, withheld_forms
from cairn.store import Project
from cairn.web.auth import _memory
from cairn.web.brand import Brand, product_of
from cairn.web.sharing import sharing_words
from cairn.web.templating import _page, brand_of
from cairn.web.turns import compile_turn

OPTIONAL = ("title", "topic", "source_ref")  # the note fields a composer may leave blank
EMPTY_NOTE = "Write something first: the note is empty."
NO_PROJECT = "Choose the project this note goes in."
REFUSED = "Nothing was saved. Remove what is listed below and save again."


def _value(form: FormData, name: str) -> str:
    value = form.get(name)
    return value if isinstance(value, str) else ""


def _note_text(form: FormData) -> str:
    """The textarea as typed. Browsers send its line ends as CRLF; the `note` tool gets LF,
    so the web stores LF too and the same text is the same note whichever way it came."""
    return _value(form, "text").replace("\r\n", "\n").replace("\r", "\n")


def shared_text(text: str, url: str) -> str:
    """What a share sheet sent, as one note: the text, then the link on a line of its own
    unless the text already holds it."""
    text, url = text.strip(), url.strip()
    if not url or url in text:
        return text
    return f"{text}\n\n{url}" if text else url


def _seg(value: str) -> str:
    return quote(value, safe="")


def _landing(project: Project, noted: dict[str, Any]) -> str:
    """The page the note landed on; its project when it compiled into no page (a note of only
    headings, say)."""
    if noted["version"] is None:
        return f"/b/{_seg(project.name)}"
    return f"/b/{_seg(project.name)}/p/{_seg(noted['page'])}"


def _composer(
    memory: Memory,
    status: int = 200,
    *,
    brand: Brand | None,
    project: Project | None,
    projects: list[Project] | None,
    values: dict[str, str],
    error: str | None = None,
    violations: list[str] | None = None,
    findings: list[dict[str, Any]] | None = None,
    near: str | None = None,
    as_written: bool = False,
) -> Response:
    """The note form: for one project (`project`), or with a project picker (`projects`).
    `near`: the form the Source comes close to (cairn.sources.near_miss): beside Source, a
    tick box that stores it as written, ticked when `as_written`; with no other `error` and
    the box not ticked, the alert names the form."""
    return _page(
        "compose.html",
        status_code=status,
        brand=brand,
        person=memory.person,
        signed_in=True,
        project=project,
        projects=projects,
        signed_out=False,
        action=f"/b/{_seg(project.name)}/notes" if project else "/notes",
        values={"text": "", "project": "", **{name: "" for name in OPTIONAL}, **values},
        error=error,
        violations=violations or [],
        findings=findings or [],
        near=near,
        as_written=as_written,
        ref_forms=sorted(withheld_forms()),  # the words near_miss names too
        project_form={"name": "", "description": ""},
        project_error=None,
    )


_NOTE_FORM = re.compile(r"/b/([^/]+)/notes")  # the project composer's address; /notes is the other
_FORM_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


async def signed_out_draft(request: Request) -> Response | None:
    """R1.7: what a browser gets for the note form posted after its sign-in ended, or None
    for any other request, which Auth then answers as before. Auth calls it with no session,
    after refusing a cross-origin post, and inside the app's body cap (MAX_BODY: 413 before
    anything is read here). The answer is 401: the same form, holding everything typed
    (escaped, as every value is), an alert saying nothing was stored, and a link that opens
    the sign-in page in a new tab. Sent again once signed in there, the form carries the
    session cookie (SameSite=Strict is sent on a same-origin post) and is stored as usual.
    Nothing typed is stored, logged or put in a cookie; the page is not cached."""
    path = request.scope["path"]
    named = _NOTE_FORM.fullmatch(path)
    if request.method != "POST" or not (path == "/notes" or named):
        return None
    sent = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if sent not in _FORM_TYPES:
        return None
    form = await request.form()
    values = {name: _value(form, name) for name in ("project", *OPTIONAL)}
    values["text"] = _note_text(form)
    picker = named is None
    page = _page(
        "compose.html",
        status_code=401,
        brand=brand_of(request),
        signed_out=True,
        project=None,
        projects=[SimpleNamespace(name=values["project"])] if picker else None,
        action="/notes" if picker else f"/b/{_seg(named[1])}/notes",
        values=values,
        near=near_miss(values["source_ref"]),
        as_written="as_written" in form,
        ref_forms=sorted(withheld_forms()),
    )
    page.headers["cache-control"] = "no-store"
    return page


def _file(
    memory: Memory, ref: str, form: FormData, *, picker: bool, brand: Brand | None
) -> Response:
    """Store and compile the posted note in project `ref`; the form again when it is refused."""
    values = {name: _value(form, name) for name in ("project", *OPTIONAL)}
    values["text"] = _note_text(form)
    target = memory.project(ref) if ref.strip() or not picker else None
    shown = None if picker else target  # the picker's form names no project in its address
    # R1.5: a Source close to a form is asked about once; the tick box, kept ticked on any
    # other refusal, stores it as written.
    near, ticked = near_miss(values["source_ref"]), "as_written" in form

    def again(status: int, **problem: Any) -> Response:
        projects = memory.store.list_projects() if picker else None
        asked = {"near": near, "as_written": ticked, **problem}
        return _composer(
            memory, status, brand=brand, project=shown, projects=projects, values=values, **asked
        )

    if target is None:
        return again(400, error=NO_PROJECT)
    values["project"] = target.name
    if not values["text"].strip():
        return again(400, error=EMPTY_NOTE)
    if near and not ticked:
        return again(400)
    fields = {name: values[name].strip() or None for name in OPTIONAL}
    try:
        noted = memory.note(target.id, values["text"], via="web", **fields)
    except Refused as refused:  # what was typed stays, so the line numbers point into it
        return again(422, error=REFUSED, findings=_shown(refused, product_of(brand).name))
    except OverQuota as over:  # nothing stored; what was typed stays for later
        memory.record_refusal("note", target.name, over)
        return again(over.status, error=str(over))
    except BadRequest as bad:  # a field past its bound (FIELD_MAX): nothing stored
        return again(bad.status, error=f"{str(bad)[:1].upper()}{str(bad)[1:]}.")
    except (NoteForgotten, PageFull, FiledOnce) as conflict:  # forgotten, full, filed (N1)
        return again(conflict.status, error=str(conflict))
    if noted["status"] == WAITING:  # N1: stored, and filed by itself: nothing to send again
        values.update({name: "" for name in ("text", *OPTIONAL)})
        return again(202, error=noted["message"], near=None, as_written=False)  # a blank form
    if noted["status"] == "rejected":
        refused = "The note was kept, but no page was written: the gate refused it."
        return again(422, error=refused, violations=noted["violations"])
    return RedirectResponse(_landing(target, noted), status_code=303)


def _shown(refused: Refused, product: str) -> list[dict[str, Any]]:
    """What the composer lists of a refusal: each rule in words (guard.rule_words, naming
    the product as `product`), with its name kept as data-rule, and its line or field."""
    return [
        {
            "rule": finding.rule,
            "said": rule_words(finding.rule, product),
            "where": (
                f"line {finding.line}"
                if field == "text"
                else f"the {CHECKED_FIELDS.get(field, field)}"
            ),
        }
        for field, finding in refused.findings
    ]


def compose_routes() -> list[Route]:
    def project_composer(request: Request) -> Response:
        memory = _memory(request)
        target = memory.project(request.path_params["project"])
        return _composer(memory, brand=brand_of(request), project=target, projects=None, values={})

    async def project_note(request: Request) -> Response:
        form = await request.form()
        memory, ref = _memory(request), request.path_params["project"]
        brand = brand_of(request)
        filed = functools.partial(_file, memory, ref, form, picker=False, brand=brand)
        async with compile_turn(request):  # G1: one of the server's compiles
            return await run_in_threadpool(filed)

    def quick_composer(request: Request) -> Response:
        query = request.query_params
        text = shared_text(query.get("text", ""), query.get("url", ""))
        values = {"title": query.get("title", "").strip(), "text": text}
        memory = _memory(request)
        projects, brand = memory.store.list_projects(), brand_of(request)
        return _composer(memory, brand=brand, project=None, projects=projects, values=values)

    async def quick_note(request: Request) -> Response:
        form = await request.form()
        memory = _memory(request)
        ref = _value(form, "project")
        brand = brand_of(request)
        filed = functools.partial(_file, memory, ref, form, picker=True, brand=brand)
        async with compile_turn(request):  # G1: one of the server's compiles
            return await run_in_threadpool(filed)

    async def new_project(request: Request) -> Response:
        form = await request.form()
        memory = _memory(request)
        typed = {
            "name": _value(form, "name").strip(),
            "description": _value(form, "description"),
            "kind": _value(form, "kind") or GENERAL,
            "private": "private" in form,
        }
        try:
            created = await run_in_threadpool(
                functools.partial(
                    memory.new_project,
                    typed["name"],
                    typed["description"].strip(),
                    typed["kind"],
                    private=typed["private"] or None,  # unticked: the kind decides
                )
            )
        except (BadRequest, Conflict, OverQuota) as refused:
            if isinstance(refused, OverQuota):
                memory.record_refusal("new_project", None, refused)
            projects = await run_in_threadpool(lambda: memory.projects()["projects"])
            shared = await run_in_threadpool(
                lambda: {
                    project["id"]: sharing_words(memory, project["id"]) for project in projects
                }
            )
            return _page(
                "home.html",
                status_code=refused.status,
                brand=brand_of(request),
                person=memory.person,
                signed_in=True,
                projects=projects,
                shared=shared,
                project_form=typed,
                project_error=str(refused),
            )
        return RedirectResponse(f"/b/{_seg(created['name'])}", status_code=303)

    return [
        Route("/b/{project}/new", project_composer, methods=["GET"]),
        Route("/b/{project}/notes", project_note, methods=["POST"]),
        Route("/new", quick_composer, methods=["GET"]),
        Route("/notes", quick_note, methods=["POST"]),
        Route("/projects", new_project, methods=["POST"]),
    ]
