"""The literal names are reserved for the tests that pin them."""

import ast
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
NAME_CONTRACTS = {
    # This file proves the exact current and retired names in both schemes.
    "unit/test_host_cookies_unit.py": None,
    "unit/test_auth_sessions_unit.py": {
        "test_signing_out_ends_and_clears_every_name_the_session_cookie_has_had",
        "test_signing_out_with_only_the_current_cookie_clears_only_that_one",
    },
    "unit/test_brand_pages_unit.py": {
        "test_under_a_brand_on_https_the_cookies_are_host_only_by_their_names",
    },
    "unit/test_guide_facts_b1_unit.py": {
        "test_under_a_brand_the_cookies_and_the_scope_have_other_names",
    },
    "unit/test_signin_unit.py": {
        "test_the_state_cookie_is_host_only_on_https_and_cleared_after",
    },
    "unit/test_guard_pasted_unit.py": {
        "test_names_that_only_start_like_a_key_are_left_alone",
    },
    "acceptance/test_b2_sign_ins_under_way.py": {
        "<docstring>",
        "test_the_state_cookie_keeps_its_name_and_attributes",
    },
}


def allowed_lines(text: str, contracts: set[str]) -> set[int]:
    lines = set()
    for part in ast.parse(text).body:
        if isinstance(part, ast.FunctionDef) and part.name in contracts:
            first = min([part.lineno, *(decorator.lineno for decorator in part.decorator_list)])
            lines.update(range(first, part.end_lineno + 1))
        if (
            "<docstring>" in contracts
            and isinstance(part, ast.Expr)
            and isinstance(part.value, ast.Constant)
            and isinstance(part.value.value, str)
        ):
            lines.update(range(part.lineno, part.end_lineno + 1))
    return lines


def test_cookie_names_are_taken_from_the_shared_helper():
    # The short branded HTTP names also occur as ordinary words. Check cookie syntax
    # for those two so an unrelated parameter or fixture label can keep its name.
    fixed = re.compile(r"\b(?:__Host-(?:session|signin)|cairn_(?:session|signin))\b")
    short = re.compile(
        r"""\bcookies(?:\.(?:get|set|delete)\(|\[)\s*["'](?:session|signin)["']"""
        r"""|["'](?:session|signin)["']\s+(?:not\s+)?in\s+[^\n]*(?:\.cookies|cookie_names\()"""
        r"""|(?:["']|; )(?:session|signin)="""
    )
    violations = []
    for path in sorted(TESTS.rglob("*.py")):
        rel = path.relative_to(TESTS).as_posix()
        if rel in {"cookie_names.py", "unit/test_cookie_names_unit.py"}:
            continue
        contracts = NAME_CONTRACTS.get(rel, set())
        if contracts is None:
            continue
        text = path.read_text(encoding="utf-8")
        allowed = allowed_lines(text, contracts) if contracts else set()
        matches = {
            text.count("\n", 0, match.start()) + 1
            for pattern in (fixed, short)
            for match in pattern.finditer(text)
        }
        violations.extend(f"{rel}:{number}" for number in sorted(matches - allowed))
    assert not violations, "use cookie_names in " + ", ".join(violations)
