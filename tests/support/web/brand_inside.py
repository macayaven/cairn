"""Shared test helpers extracted from test_brand_inside_unit."""

import re

from cairn.web.brand import SOFTWARE

IDENTIFIERS = re.compile(
    r"python3? -m cairn(?:\.[a-z_]+)*\b"  # the module
    r"|(?<![A-Za-z0-9_])CAIRN_[A-Z0-9_]+"  # an environment variable
    r"|(?<![A-Za-z0-9_-])cairn\.(?:db|env)\b"  # a file name the software chooses
    r"|\bghcr\.io/[\w.-]+/cairn(?::[\w.-]+)?"  # the container image
    r"|(?<![A-Za-z0-9_-])cairn\.(?:at|rt)\."  # a token's prefix
    r"|/static/cairn\.(?:svg|css)\b"  # a static file's address (no brand: the frame's own)
)


def spelled(text: str) -> list[str]:
    """Each place `text` names the software outside one of IDENTIFIERS, with what is
    around it."""
    kept = [m.span() for m in IDENTIFIERS.finditer(text)]
    return [
        text[max(0, m.start() - 40) : m.end() + 40]
        for m in re.finditer(SOFTWARE, text, re.IGNORECASE)
        if not any(s <= m.start() and m.end() <= e for s, e in kept)
    ]
