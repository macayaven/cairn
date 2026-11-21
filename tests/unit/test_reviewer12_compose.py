from test_quick_bootstrap_unit import QUICK, judged_with_compose_deciding
from test_quick_bootstrap_unit import vm as vm


def test_three_backslashes_with_a_later_comment_are_counted(vm):
    settings = b"FIRST='a" + bytes([92]) * 3 + b"'\nSECOND='#b'\n"
    given, lines = judged_with_compose_deciding(vm, settings)
    assert set(given) == {"FIRST"}
    assert len(lines) == 1 and "not compared" in lines[0]
    guide = " ".join((QUICK / "README.md").read_text().split())
    # The guide says so in both places the sentence stands: quiet with one backslash before
    # the quote, the line counted with three or five.
    assert guide.count("(one before the quote, then, not three or five)") == 2
    assert guide.count("otherwise it counts the line") == 2
