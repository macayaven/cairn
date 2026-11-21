"""Browser cookie names for a given scheme and branding."""

from cairn.web.auth import BRANDED_SESSION_COOKIE, SESSION_COOKIE, branded_cookie
from cairn.web.signin import BRANDED_STATE_COOKIE, STATE_COOKIE


def session_cookie(scheme: str = "http", *, branded: bool = False) -> str:
    name = BRANDED_SESSION_COOKIE if branded else SESSION_COOKIE
    return branded_cookie(name, secure=scheme == "https")


def signin_cookie(scheme: str = "http", *, branded: bool = False) -> str:
    name = BRANDED_STATE_COOKIE if branded else STATE_COOKIE
    return branded_cookie(name, secure=scheme == "https")
