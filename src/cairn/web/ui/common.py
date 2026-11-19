"""What every page of the web view is answered with: the page for the signed-in caller
(_ui), and the page before an ending a list offers (_ended)."""

from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from cairn.web.auth import _memory
from cairn.web.templating import _page, brand_of


def _ui(request: Request, name: str, status_code: int = 200, **context: Any) -> Response:
    """A page for the signed-in caller: the app's navigation, naming the person when one
    signed in."""
    person = _memory(request).person
    return _page(
        name, status_code, brand=brand_of(request), person=person, signed_in=True, **context
    )


def _ended(
    request: Request,
    *,
    title: str,
    trail: tuple[tuple[str, str], ...],
    said: tuple[str, ...],
    action: str,
    button: str,
    back: str,
    keep: str = "No, keep it",
) -> Response:
    """The page before an ending a list offers (end.html): `title`, what ends (`said`), the
    one form, posting to `action`, and the way back to the list at `back`, said as `keep`."""
    return _ui(
        request, "end.html", title=title, trail=trail, said=said, action=action,
        button=button, back=back, keep=keep,
    )  # fmt: skip
