"""scripts/release_check.py refuses a version's section of CHANGELOG.md that holds a control
character other than a line feed or a tab.

Why: the section is the GitHub release's text, and job `release` takes a release as its own
only when the notes GitHub returns are, byte for byte, the ones it sent. A tab makes that
round trip (the coordinator's measurement of 2026-11-20); no other control character is known
to, and the GitHub CLI's own source says a JSON reply is "sanitized by the transport"
(cli/cli at v2.102.0, pkg/cmd/api/api.go, 342). A section that held one would stop the job on
its own draft, after the version's image was published. So the script that writes the notes
refuses it where the section is read, for the check and for the notes alike: the tag's job
`release_check` then stops before anything is published, and the test of the repository's own
files (test_release_check_unit, "the repository itself would release its version"; again
below) fails a pull request before a tag exists.

Refused: C0 but the line feed and the tab, DEL, and C1. The refusal is one line that names
the file's line and the character's code point, never the character. What an HTML comment
hides is not in the notes, so a character there is not refused."""

import tomllib

import pytest
from test_release_check_unit import CHANGELOG, PYPROJECT, ROOT, refused, release_check

ENTRY = "- **One.** The first entry."  # line 11 of CHANGELOG
REFUSAL = (
    "CHANGELOG.md's section for 1.2.3 holds a control character, U+{code:04X}, on line {line}"
    " of the file: a release's text may hold no control character but line feeds and tabs"
)


def holding(char: str, changelog: str = CHANGELOG) -> str:
    """CHANGELOG with CHAR inside its first entry."""
    assert changelog.count(ENTRY) == 1
    return changelog.replace(ENTRY, f"- **One.** The first{char}entry.")


def notes_refused(changelog: str, tag: str | None = None) -> str:
    with pytest.raises(release_check.Refused) as caught:
        release_check.notes("1.2.3", changelog, tag)
    return str(caught.value)


@pytest.mark.parametrize(
    "code",
    [0x00, 0x01, 0x07, 0x08, 0x0B, 0x0C, 0x0D, 0x0E, 0x1B, 0x1F, 0x7F, 0x80, 0x85, 0x9F],
    ids=lambda code: f"U+{code:04X}",
)
def test_a_section_that_holds_a_control_character_is_refused_in_one_line_that_names_its_place(
    code,
):
    """By the check a tag gets before anything is published, and by the notes writer, for a
    release and for a candidate: the same line, with the file's line and the code point."""
    assert CHANGELOG.splitlines()[10] == ENTRY
    changelog = holding(chr(code))
    said = REFUSAL.format(code=code, line=11)
    assert refused("v1.2.3", changelog=changelog) == said
    assert refused("v1.2.3-rc.1", changelog=changelog) == said
    assert notes_refused(changelog) == said
    assert notes_refused(changelog, "v1.2.3-rc.1") == said
    assert chr(code) not in said and "\n" not in said and said.isprintable()


@pytest.mark.parametrize(
    "char",
    ["\t", "\n", " ", "~", "\u00a0", "\u00e9", "\u2028", "\U0001f600"],
    ids=["tab", "line feed", "space", "tilde", "U+00A0", "e acute", "U+2028", "an emoji"],
)
def test_a_tab_a_line_feed_and_every_character_beside_the_controls_stay_in_the_notes(char):
    """One step past each bound: U+0009 and U+000A inside C0, U+0020 above it, U+007E below
    DEL, U+00A0 above C1; and what is not a control character at all."""
    changelog = holding(char)
    assert release_check.check("v1.2.3", PYPROJECT, changelog) == "1.2.3"
    assert f"The first{char}entry." in release_check.notes("1.2.3", changelog)


def test_what_a_comment_hides_is_not_in_the_notes_and_is_not_refused():
    hidden = CHANGELOG.replace("a later entry, when it lands", "a later\x1bentry\x00\x7f")
    assert hidden != CHANGELOG
    assert release_check.check("v1.2.3", PYPROJECT, hidden) == "1.2.3"
    assert release_check.notes("1.2.3", hidden) == release_check.notes("1.2.3", CHANGELOG)
    # One just past the comment's end is in the notes, and is refused.
    past = CHANGELOG.replace("when it lands -->", "when it lands -->\x1b")
    assert notes_refused(past) == REFUSAL.format(code=0x1B, line=13)


def test_the_line_named_is_the_file_s_also_below_a_comment_and_the_first_is_the_one_named():
    below = holding("\x1b", CHANGELOG.replace(ENTRY, f"<!-- one\ntwo\nthree -->\n{ENTRY}"))
    assert below.splitlines()[13].startswith("- **One.** The first")
    assert notes_refused(below) == REFUSAL.format(code=0x1B, line=14)
    two = holding("\x07").replace("The text of the release.", "The text\x00of the release.")
    assert notes_refused(two) == REFUSAL.format(code=0x00, line=9)


def test_only_the_version_s_own_section_is_read():
    for elsewhere in ("Words before the sections.", "## Unreleased", "- Older."):
        changelog = CHANGELOG.replace(elsewhere, elsewhere + "\x1b")
        assert changelog != CHANGELOG
        assert release_check.check("v1.2.3", PYPROJECT, changelog) == "1.2.3"
        assert release_check.notes("1.2.3", changelog) == release_check.notes("1.2.3", CHANGELOG)


@pytest.mark.parametrize("flags", [[], ["--notes"]], ids=["the check", "the notes"])
def test_main_refuses_it_in_one_line_and_prints_no_notes(tmp_path, capsys, flags):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(holding("\x1b"), encoding="utf-8")
    assert release_check.main(["v1.2.3", *flags, "--root", str(tmp_path)]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == "release refused: " + REFUSAL.format(code=0x1B, line=11) + "\n"


def test_a_carriage_return_cannot_reach_the_notes_from_the_file_and_is_refused_in_a_text(
    tmp_path, capsys
):
    """`main` reads the changelog as text, so Python gives it every line end as a line feed: a
    file written with CR LF, or with a lone CR, yields notes with line feeds alone, and
    nothing is refused. The rule still holds a CR among the controls: a text that holds one,
    handed to the functions, is refused (written CR LF throughout, already at its heading,
    which then is not a heading)."""
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    for written in (CHANGELOG.replace("\n", "\r\n"), holding("\r")):
        (tmp_path / "CHANGELOG.md").write_bytes(written.encode())
        assert release_check.main(["v1.2.3", "--notes", "--root", str(tmp_path)]) == 0
        out, _ = capsys.readouterr()
        assert "\r" not in out
        assert out == release_check.notes(
            "1.2.3", written.replace("\r\n", "\n").replace("\r", "\n")
        )
    assert notes_refused(holding("\r")) == REFUSAL.format(code=0x0D, line=11)
    assert notes_refused(CHANGELOG.replace("\n", "\r\n")) == (
        "CHANGELOG.md has no section '## 1.2.3 - YYYY-MM-DD'"
    )


def test_the_repository_s_own_section_holds_none_so_a_pull_request_that_adds_one_fails_here():
    """Job `test` runs this on every pull request: the notes writer on the repository's own
    CHANGELOG.md, at the version pyproject.toml gives."""
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    text = release_check.notes(version, changelog)
    assert not release_check.CONTROL.search(text)
    assert release_check.CONTROL.pattern == r"[\x00-\x08\x0b-\x1f\x7f-\x9f]"
    # And with one put into that section, the same call refuses.
    first = text.splitlines()[0]
    spoiled = changelog.replace(first, first + "\x1b", 1)
    with pytest.raises(release_check.Refused, match=r"holds a control character, U\+001B"):
        release_check.notes(version, spoiled)


def test_the_script_and_the_documents_say_the_rule():
    doc = " ".join(release_check.__doc__.split())
    assert (
        "The section may hold no control character but line feeds and tabs (C0 otherwise, DEL"
        " and C1 are refused, with the file's line): job `release` compares the notes GitHub"
        " returns with the ones it sent, byte for byte;" in doc
    )
    header = (ROOT / ".github" / "workflows" / "ci-cd.yml").read_text(encoding="utf-8")
    header = " ".join(header.split("\nname:")[0].replace("# ", "").split())
    assert (
        "a version whose section of CHANGELOG.md is missing, has no entry, holds a control"
        " character other than a line feed or a tab, or is dated after the day of the run,"
        in header
    )
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert (
        "`CHANGELOG.md` has a section for that version with an entry and no control character"
        " but line feeds and tabs, dated no later than the day of the run," in readme
    )
