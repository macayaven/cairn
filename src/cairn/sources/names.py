"""Whether a display name may be written: the one check every connector runs on a name.

A display name is typed by a person (a guest at a meeting, a calendar invitee, a sender),
and it often is their email address. A connector writes names, never addresses, so a name
that holds an address counts as no name: it is left out, counted or replaced as each
connector's docstring says, never written.

- `holds_an_address(name)`: True when `name` holds "@" once NFKC-normalised, so the
  fullwidth at sign (U+FF20) and the small one (U+FE6B), which read as "@", count too.
  The normalised form is for the check only: a name that passes is written as it was given.
- `shown(value)`: `value` on one line (its white space runs made one space); None when it is
  not a string, is blank, or holds an address.

It imports nothing from cairn, so any connector can use it.
"""

import unicodedata


def holds_an_address(name: str) -> bool:
    """Whether `name` holds an at sign of any width (see the module docstring)."""
    return "@" in unicodedata.normalize("NFKC", name)


def shown(value: object) -> str | None:
    """`value` on one line; None when it is no string, blank, or holds an address."""
    if not isinstance(value, str):
        return None
    named = " ".join(value.split())
    return named if named and not holds_an_address(named) else None
