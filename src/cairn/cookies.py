"""Host-only cookie naming shared across the web layer and Slack setup."""

HOST_ONLY = "__Host-"  # a browser takes such a cookie only Secure, at Path=/, with no Domain


def branded_cookie(name: str, secure: bool) -> str:
    """The name of a cookie: `name` with the __Host- prefix when the site is https
    (`secure`), else `name` as it is (a browser refuses the prefix without Secure).

    Why the prefix: another host under the same parent domain can set a cookie of a plain
    name for the whole parent domain, and the browser sends it along with this server's own,
    so a planted `session` would put a person in the planter's workspace and a planted
    `signin` would sign them in as the planter. No host can set a __Host- cookie for another
    host, and the server reads only the name it sets.

    Over plain http no name carries the prefix; there a planted cookie can still shadow
    them."""
    return f"{HOST_ONLY}{name}" if secure else name


def cookie_path(name: str, default: str) -> str:
    """The path of a cookie: `/` when `name` carries `__Host-` (browsers reject any other),
    else `default`."""
    return "/" if name.startswith(HOST_ONLY) else default
