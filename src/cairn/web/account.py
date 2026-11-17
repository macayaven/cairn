"""The workspace's own data: GET /account, its export (in the web view and over the API) and
its deletion. What an export holds is cairn.store.portable's; who may take it or delete the
workspace is web.auth.may_manage_credentials's one rule, narrowed to owners (OWNER_ROLES)
for a person: an archive holds every project.

An export is written to a temporary file (in memory up to SPOOL_BYTES, on disk past that)
and streamed from it, so a large workspace never sits whole in the server's memory. One
export at a time per workspace: another asked while one runs, its download included, is
429 with Retry-After."""

import tempfile
import threading
from collections.abc import Callable, Iterator
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from cairn.memory import Forbidden, Memory, RequestError
from cairn.store import DEFAULT_ACCOUNT_ID
from cairn.web.auth import OWNER_ROLES, Accounts, _memory, may_manage_credentials
from cairn.web.brand import product_of
from cairn.web.rest import _logged, _query
from cairn.web.templating import _page, brand_of

EXPORTED = "workspace.exported"  # the access log's action for an export
SPOOL_BYTES = 8 * 1024 * 1024  # an export past this spills from memory to a temporary file
CHUNK_BYTES = 64 * 1024  # what the download sends at a time
RETRY_SECONDS = 30  # Retry-After on a second export while one runs
_NOT_EXPORTER = "Only an owner of this workspace can export it."
_NOT_OWNER = "Only an owner of this workspace can delete it."
_DEFAULT = (
    "The default workspace cannot be deleted: it is the install's own. Delete the database"
    " file to remove everything."
)
_NOT_CONFIRMED = "Type the workspace's name exactly as it is shown to delete it."


def _rights(memory: Memory) -> dict[str, Any]:
    """What the caller may do with the workspace, and the workspace itself."""
    store = memory.store
    owner = may_manage_credentials(memory, OWNER_ROLES)
    return {
        "account": store.get_account(store.account_id),
        "export": owner,
        "delete": owner,
        "default": store.account_id == DEFAULT_ACCOUNT_ID,
    }


class ExportRunning(RequestError):
    """Another export of this workspace is running: 429, ask again after `retry_after`."""

    status = 429
    retry_after = RETRY_SECONDS


class _Exports:
    """The workspaces with an export running in this process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: set[str] = set()

    def start(self, account_id: str) -> Callable[[], None]:
        """Claim the workspace's one export (ExportRunning when it is taken); the call
        returned lets go of it once, however often it is made."""
        with self._lock:
            if account_id in self._running:
                raise ExportRunning("An export of this workspace is running. Try again soon.")
            self._running.add(account_id)
        once = threading.Lock()

        def finish() -> None:
            if once.acquire(blocking=False):
                with self._lock:
                    self._running.discard(account_id)

        return finish


class _Streamed(StreamingResponse):
    """A StreamingResponse that calls `done` when it ends, however it ends: Starlette runs
    no background task when the client goes, and an abandoned stream is closed only when
    it is collected."""

    def __init__(self, *args: Any, done: Callable[[], None], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._done = done

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._done()


def _download(memory: Memory, exports: _Exports, slug: str) -> Response:
    """The export as a zip download, named by the product's slug (brand.product_of: the
    brand's under a brand, B3) and the workspace's id (a name is free text, and a header is
    no place for it), streamed from a temporary file. The workspace's export is claimed
    until the download ends or the client goes."""
    release = exports.start(memory.store.account_id)
    spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES)  # noqa: SIM115 (done() closes)
    try:
        memory.export(spool)
        size = spool.tell()
        spool.seek(0)
    except BaseException:
        spool.close()
        release()
        raise

    def done() -> None:
        spool.close()
        release()

    def chunks() -> Iterator[bytes]:  # a sync iterator: Starlette reads it on a worker thread
        try:
            while data := spool.read(CHUNK_BYTES):
                yield data
        finally:
            done()

    return _Streamed(
        chunks(),
        media_type="application/zip",
        headers={
            "content-disposition": f'attachment; filename="{slug}-{memory.store.account_id}.zip"',
            "content-length": str(size),
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
        },
        done=done,  # also when the client goes before the end
    )


def account_routes(accounts: Accounts) -> list[Route]:
    exports = _Exports()

    def page(request: Request, status_code: int = 200, error: str | None = None) -> Response:
        memory = _memory(request)
        return _page(
            "account.html",
            status_code,
            brand=brand_of(request),
            person=memory.person,
            signed_in=True,
            error=error,
            **_rights(memory),
        )

    def account(request: Request) -> Response:
        return page(request)

    def export(request: Request) -> Response:
        """The download in the web view: for an owner (an archive holds every project, and an
        admin does not reach the private ones)."""
        memory = _memory(request)
        with memory.access(EXPORTED):
            if not may_manage_credentials(memory, OWNER_ROLES):
                raise Forbidden(_NOT_EXPORTER)
            return _download(memory, exports, product_of(brand_of(request)).slug)

    def api_export(request: Request) -> Response:
        """The same over the API, for a key that reaches the whole workspace to write. It
        takes no query (X1)."""
        _query(request)
        return _download(_memory(request), exports, product_of(brand_of(request)).slug)

    async def delete(request: Request) -> Response:
        """Delete the workspace when `confirm` is its name exactly, compact the database,
        end the session (it went with the workspace) and go to /."""
        memory = _memory(request)
        form = await request.form()
        confirm = form.get("confirm")
        rights = await run_in_threadpool(_rights, memory)
        if not rights["delete"]:
            return await run_in_threadpool(page, request, 403, _NOT_OWNER)
        if rights["default"]:
            return await run_in_threadpool(page, request, 403, _DEFAULT)
        if rights["account"] is None or confirm != rights["account"].name:
            return await run_in_threadpool(page, request, 400, _NOT_CONFIRMED)
        try:
            await run_in_threadpool(accounts.delete_workspace, memory)
        except PermissionError:  # no longer an owner when the write ran
            return await run_in_threadpool(page, request, 403, _NOT_OWNER)
        response = RedirectResponse("/", status_code=303)
        accounts.sessions.clear(response)
        return response

    return [
        Route("/account", account, methods=["GET"]),
        Route("/account/export", export, methods=["GET"]),
        Route("/account/delete", delete, methods=["POST"]),
        Route("/api/account/export", _logged(EXPORTED, api_export), methods=["GET"]),
    ]
