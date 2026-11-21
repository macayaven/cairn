"""No view hands its template a name that a template global holds. base.html's footer reads
the global `version`, the software's; the related page once handed its template the page's
version under that name, and its footer said "cairn 3" where every other page said the
software's version. The globals a page sets for itself (cairn.web.templating.PER_PAGE: who
signed in, and whether) are the only ones a view may hand over.

Read from the source, so a view no test renders is checked too: every call that makes a page
(`_page`, `_ui`, `_ended`), with its named arguments, and the keys of what it spreads into
them, followed to the dictionary a function returns or a variable is given. Every page a test
renders is also checked as it is made (tests/conftest.py)."""

import ast
from pathlib import Path

import cairn.web.app  # it adds the globals of the source blocks
import cairn.web.google_connect  # noqa: F401
from cairn.web import templating

SOURCE = Path(templating.__file__).resolve().parent.parent  # src/cairn
MAKERS = {"_page", "_ui", "_ended"}
HOMES = {  # where a page maker is imported from: the module, and its file
    "cairn.web.templating": "web/templating.py",
    "cairn.web.ui.common": "web/ui/common.py",
    "cairn.web.ui": "web/ui/__init__.py",
}


def _name(call: ast.Call) -> str | None:
    func = call.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)


def _own(function: ast.AST):
    """The nodes of a function's own body, not those of a function defined inside it."""
    stack = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            stack.extend(ast.iter_child_nodes(node))


def _keys(expr: ast.expr, scope: ast.AST, functions: dict[str, list[ast.AST]]) -> set[str]:
    """The string keys a spread expression gives, followed through one call or one name
    (`functions`: those of the call's own module, then every other's)."""
    if isinstance(expr, ast.Await):
        return _keys(expr.value, scope, functions)
    if isinstance(expr, ast.IfExp):
        return _keys(expr.body, scope, functions) | _keys(expr.orelse, scope, functions)
    if isinstance(expr, ast.Call) and _name(expr) == "run_in_threadpool":
        return _keys(ast.Call(func=expr.args[0], args=[], keywords=[]), scope, functions)
    if isinstance(expr, ast.Dict):
        return {k.value for k in expr.keys if isinstance(k, ast.Constant)}
    if isinstance(expr, ast.Call) and _name(expr) == "dict":
        return {kw.arg for kw in expr.keywords if kw.arg}
    if isinstance(expr, ast.Call) and _name(expr) in functions:
        returned = [
            node.value
            for function in functions[_name(expr)]
            for node in _own(function)
            if isinstance(node, ast.Return) and node.value is not None
        ]
        found = [_keys(value, scope, functions) for value in returned]
        assert all(found), ("a return that gives no keys", ast.unparse(expr))
        return set().union(*found)
    if isinstance(expr, ast.Name):
        given = [
            node.value
            for node in ast.walk(scope)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == expr.id for t in node.targets)
        ]
        found = [_keys(value, scope, functions) for value in given]
        assert all(found), ("a name given no keys", expr.id)
        return set().union(*found)
    raise AssertionError(("a spread not followed", ast.unparse(expr)))


def handed() -> dict[str, set[str]]:
    """Every name a call that makes a page hands its template, by where the call is."""
    trees = {path: ast.parse(path.read_text("utf-8")) for path in sorted(SOURCE.rglob("*.py"))}
    every: dict[str, list[ast.AST]] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                every.setdefault(node.name, []).append(node)
    names: dict[str, set[str]] = {}
    for path, tree in trees.items():
        own: dict[str, list[ast.AST]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                own.setdefault(node.name, []).append(node)
        functions = every | own
        makers = {  # the page makers this module imports from where they are made, or makes
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module in HOMES
            for alias in node.names
            if alias.name in MAKERS
        } | (MAKERS & set(own) if path.relative_to(SOURCE).as_posix() in HOMES.values() else set())
        scopes = [
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        for scope in scopes:
            passed_on = {a.arg for a in (scope.args.vararg, scope.args.kwarg) if a}
            for call in [n for n in ast.walk(scope) if isinstance(n, ast.Call)]:
                if _name(call) not in makers:
                    continue
                at = f"{path.relative_to(SOURCE)}:{call.lineno}"
                names.setdefault(at, set())
                for keyword in call.keywords:
                    if keyword.arg:
                        names.setdefault(at, set()).add(keyword.arg)
                    elif not (
                        isinstance(keyword.value, ast.Name) and keyword.value.id in passed_on
                    ):
                        names.setdefault(at, set()).update(_keys(keyword.value, scope, functions))
    return names


def test_no_view_hands_its_template_a_name_a_template_global_holds():
    reserved = set(templating.templates.globals) - templating.PER_PAGE
    assert {"version", "stylesheet", "software", "section", "path", "help"} <= reserved
    names = handed()
    assert len(names) >= 60  # every call that makes a page (60 in 18 modules), found
    shadowing = {at: sorted(given & reserved) for at, given in names.items() if given & reserved}
    assert shadowing == {}
    assert any("page_version" in given for given in names.values())  # the related page's
