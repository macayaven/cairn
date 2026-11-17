"""The two web pages that ask before they remove (H5b): delete one project, forget one note.

GET shows what will go and asks; POST does it and redirects. Both answer 404 when the project
or the note is out of the caller's reach (checked first) and 403 to someone who reaches the
project and may not (cairn.memory.Memory.erasable: the project's maker or an owner). Nothing here
removes anything on GET, so a page that crawls links removes nothing."""

from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from cairn.memory import BadRequest
from cairn.web.auth import _memory
from cairn.web.rest import _logged
from cairn.web.templating import _page, brand_of


def _shown(request: Request, name: str, status_code: int = 200, **context: object) -> Response:
    person, brand = _memory(request).person, brand_of(request)
    return _page(name, status_code, brand=brand, person=person, signed_in=True, **context)


def _delete_page(
    request: Request, ref: str, error: str | None = None, status: int = 200
) -> Response:
    memory = _memory(request)
    target = memory.erasable(ref)
    overview = memory.overview(target.id)
    return _shown(
        request,
        "project_delete.html",
        status,
        project=target,
        notes=overview["notes"],
        pages=len(overview["pages"]),
        error=error,
    )


def erasure_routes() -> list[Route]:
    def ask_delete(request: Request) -> Response:
        return _delete_page(request, request.path_params["project"])

    async def delete(request: Request) -> Response:
        memory, ref = _memory(request), request.path_params["project"]
        form = await request.form()
        try:
            await run_in_threadpool(memory.delete_project, ref, form.get("confirm"))
        except BadRequest as bad:  # another name: the question again, with why
            return await run_in_threadpool(_delete_page, request, ref, str(bad), bad.status)
        return RedirectResponse("/", status_code=303)

    def ask_forget(request: Request) -> Response:
        memory, params = _memory(request), request.path_params
        project, note, preview = memory.forget_preview(params["project"], params["note"])
        titles = {page.slug: page.title for page in memory.store.list_pages(project.id)}
        return _shown(
            request,
            "forget.html",
            project=project,
            note=note,
            rebuilt=[(slug, titles.get(slug, slug)) for slug in preview.rebuilt],
            removed=[(slug, titles.get(slug, slug)) for slug in preview.removed],
        )

    def forget(request: Request) -> Response:
        memory, params = _memory(request), request.path_params
        project = memory.project(params["project"])
        memory.forget(project.id, params["note"])
        return RedirectResponse(f"/b/{quote(project.name, safe='')}", status_code=303)

    return [
        Route("/b/{project}/delete", _logged("ask_delete_project", ask_delete), methods=["GET"]),
        Route("/b/{project}/delete", delete, methods=["POST"]),
        Route(
            "/b/{project}/notes/{note}/forget",
            _logged("ask_forget", ask_forget),
            methods=["GET"],
        ),
        Route("/b/{project}/notes/{note}/forget", forget, methods=["POST"]),
    ]
