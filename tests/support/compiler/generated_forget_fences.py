"""Shared test helpers extracted from test_generated_forget_fences_unit."""

import random

MARKERS = ("- ", "-   ", "1. ")


def pad(indent: int) -> str:
    return " " * indent


def sibling(rng: random.Random, n: int, indent: int, marker: str) -> str:
    """A list item whose fenced example no line closes: the fence ends with the item."""
    inner = pad(indent + len(marker))
    if rng.random() < 0.5:
        return f"{pad(indent)}{marker}~~~\n{inner}sample {n}"
    return f"{pad(indent)}{marker}item {n}\n{inner}~~~\n{inner}sample {n}"


def closed(n: int, indent: int) -> str:
    return f"{pad(indent)}```text\n{pad(indent)}closed {n}\n{pad(indent)}```"


def running(n: int, indent: int) -> str:
    return f"{pad(indent)}~~~\n{pad(indent)}running {n}"


def plain(n: int, indent: int) -> str:
    return f"{pad(indent)}plain {n}"


KINDS = (
    ["sibling"],
    ["sibling"] * 8 + ["closed", "wide"],
    ["sibling"] * 4 + ["under", "closed", "plain", "running", "wide"],
    ["closed", "plain", "running", "under"],
)


def before_it(rng: random.Random, numbers, column: int, count: int, spare: int, kinds) -> list[str]:
    """What stands under a list item whose text starts at `column`, before the paragraph at
    the margin: `count` claims of `kinds`. About `spare` of them stand where a fence before
    them is open, so the body spends a closer there; the others stand clear of it."""
    base = max(4, column + rng.choice((0, 0, 1)))
    marker = rng.choice(MARKERS)
    out, indent, inner = [], base, None  # inner: the text column of an item whose fence is open
    for _ in range(count):
        kind, n = rng.choice(kinds), next(numbers)
        if kind == "running" and spare <= 0:
            kind = "plain"
        if kind in ("sibling", "wide"):  # wide: a sibling whose marker is another width
            mark = marker if kind == "sibling" else rng.choice([m for m in MARKERS if m != marker])
            out.append(sibling(rng, n, indent, mark))
            inner = indent + len(mark)
        elif kind == "under":  # an item with no fence: what follows is nested in it
            out.append(f"{pad(indent)}{marker}item {n}")
            indent, inner = indent + len(marker), None
        else:
            at = indent
            if inner is not None and spare > 0 and rng.random() < 0.5:
                at, spare = inner + rng.choice((0, 2)), spare - 1
            if kind == "running":
                spare -= 1
            out.append({"closed": closed, "running": running, "plain": plain}[kind](n, at))
            inner = None
        if rng.random() < 0.1:
            indent = base
    return out


def after_it(rng: random.Random, numbers, count: int, shape: str) -> list[str]:
    """What stands after the paragraph at the margin: until the forget, indented text."""
    if shape == "tail":
        out = [plain(next(numbers), rng.choice((2, 4, 5, 6, 7, 8, 8, 10, 12)))]
        if rng.random() < 0.3:
            out.append(rng.choice((plain, closed, running))(next(numbers), rng.choice((4, 8))))
        return out
    if shape == "running":
        at = rng.choice((4, 4, 6, 8))
        out = [running(next(numbers), at) for _ in range(count)]
        for _ in range(rng.choice((0, 1, 1, 2, 3))):
            kind = rng.choice((plain, plain, closed, running))
            out.append(kind(next(numbers), rng.choice((2, 4, 6, 8, 10))))
        return out
    out = []
    for _ in range(count):
        kind, n = rng.choice("ppcrrs"), next(numbers)
        indent = rng.choice((4, 5, 6, 8, 10))
        if kind == "p":
            out.append(plain(n, rng.choice((2, indent))))
        elif kind == "c":
            out.append(closed(n, indent))
        elif kind == "r":
            out.append(running(n, indent))
        else:
            out.append(sibling(rng, n, indent, rng.choice(MARKERS)))
    return out
