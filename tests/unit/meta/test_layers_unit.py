"""The web layer depends on the core, never the other way round: only the entry point
(cairn/server.py) may import from cairn.web. A core module that reaches into the web layer
for a constant makes the core load, or one day need, what serves pages."""

import ast
from pathlib import Path

import cairn

PACKAGE = Path(cairn.__file__).parent
ENTRY = {"server.py"}  # the command line's entry point re-exports the web app on purpose


def imports_of(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def test_nothing_outside_the_web_layer_imports_from_it():
    reaching = {}
    for path in PACKAGE.rglob("*.py"):
        inside = path.relative_to(PACKAGE)
        if inside.parts[0] == "web" or str(inside) in ENTRY:
            continue
        web = sorted(m for m in imports_of(path) if m == "cairn.web" or m.startswith("cairn.web."))
        if web:
            reaching[str(inside)] = web
    assert reaching == {}


def test_the_softwares_own_name_is_the_packages():
    """One place says how the software names itself; the web layer's brand reads it there."""
    from cairn.web import brand

    assert cairn.SOFTWARE == "cairn" == brand.SOFTWARE
