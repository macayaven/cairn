from test_quick_bootstrap_unit import QUICK, judged_with_compose_deciding
from test_quick_bootstrap_unit import vm as vm


def test_three_backslashes_with_a_later_comment_are_counted(vm):
    settings = b"FIRST='a" + bytes([92]) * 3 + b"'\nSECOND='#b'\n"
    given, lines = judged_with_compose_deciding(vm, settings)
    assert set(given) == {"FIRST"}
    assert len(lines) == 1 and "not compared" in lines[0]
    guide = " ".join((QUICK / "README.md").read_text().split())
    assert guide.count("With three backslashes") == 2
