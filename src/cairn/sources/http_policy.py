"""Bounded response reads and safe descriptions of upstream failures."""

from collections.abc import Callable, Sequence

import httpx

from cairn.sources import holds_secret
from cairn.sources.url import PRIVATE_REFUSAL, PrivateAddress


async def read_bounded(
    response: httpx.Response,
    most: int,
    *,
    check_deadline: Callable[[], None] | None = None,
) -> bytes | None:
    """Read at most `most` decoded bytes. None when the declared or decoded size exceeds it."""
    declared = response.headers.get("content-length", "")
    if declared.isascii() and declared.isdecimal():
        declared = declared.lstrip("0")
        bound = str(most)
        if len(declared) > len(bound) or (len(declared) == len(bound) and declared > bound):
            return None

    body = bytearray()
    async for part in response.aiter_bytes():
        if check_deadline is not None:
            check_deadline()
        if len(part) > most - len(body):
            return None
        body += part
    return bytes(body)


def request_failure(error: httpx.HTTPError, reaching: str | None = None) -> str:
    """Describe a request failure without the URL, exception text, headers or body."""
    if isinstance(error, PrivateAddress):
        return PRIVATE_REFUSAL
    problem = type(error).__name__
    return f"{reaching}: {problem}" if reaching else problem


def safe_error_text(
    message: str, secrets: Sequence[str], *, looks_like: Callable[[str], bool]
) -> bool:
    """Whether a provider's documented message field is free of known or shaped credentials."""
    return bool(message) and not holds_secret(message, tuple(secrets)) and not looks_like(message)
