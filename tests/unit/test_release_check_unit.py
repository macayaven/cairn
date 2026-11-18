"""scripts/release_check.py: a tag is released only when it is `v` + pyproject.toml's version,
that version is a plain MAJOR.MINOR.PATCH, and CHANGELOG.md has a dated section for it with
at least one entry. The section is the release's text."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("release_check", ROOT / "scripts/release_check.py")
release_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_check)

PYPROJECT = '[project]\nname = "x"\nversion = "1.2.3"\n'
CHANGELOG = """# Changelog

Words before the sections.

## Unreleased

## 1.2.3 - 2026-01-31

The text of the release.

- **One.** The first entry.
- Two.
<!-- 1.2.3: a later entry, when it lands -->

## 1.2.2 - 2025-12-01

- Older.
"""


def refused(tag: str, pyproject: str = PYPROJECT, changelog: str = CHANGELOG) -> str:
    with pytest.raises(release_check.Refused) as caught:
        release_check.check(tag, pyproject, changelog)
    line = str(caught.value)
    assert "\n" not in line
    return line


def test_a_tag_that_is_v_and_the_version_with_a_dated_section_passes():
    assert release_check.check("v1.2.3", PYPROJECT, CHANGELOG) == "1.2.3"


def test_a_tag_that_is_not_v_and_the_version_is_refused():
    for tag in ("v1.2.4", "1.2.3", "v1.2.3.0", "v1.2", "V1.2.3", "v1.2.3 ", ""):
        assert refused(tag) == f"the tag {tag!r} is not v1.2.3, the version in pyproject.toml"


def test_a_version_that_is_not_plain_major_minor_patch_is_refused():
    for version in ("1.2", "1.2.3rc1", "1.2.3-beta.1", "01.2.3", "1.2.3.4", "1.2.3+local", " "):
        pyproject = f'[project]\nversion = "{version}"\n'
        assert refused(f"v{version}", pyproject) == (
            f"the version in pyproject.toml, {version!r}, is not a plain MAJOR.MINOR.PATCH"
        )


def test_a_pyproject_without_a_version_or_not_toml_is_refused():
    assert refused("v1.2.3", '[project]\nname = "x"\n') == "pyproject.toml has no project version"
    assert refused("v1.2.3", "[project\n") == "pyproject.toml is not TOML"


def test_a_changelog_without_a_dated_section_for_the_version_is_refused():
    missing = "CHANGELOG.md has no section '## 1.2.3 - YYYY-MM-DD'"
    for changelog in (
        CHANGELOG.replace("## 1.2.3 - 2026-01-31", "## 1.2.4 - 2026-01-31"),
        CHANGELOG.replace("## 1.2.3 - 2026-01-31", "## 1.2.3"),
        CHANGELOG.replace("## 1.2.3 - 2026-01-31", "### 1.2.3 - 2026-01-31"),
        CHANGELOG.replace("## 1.2.3 - 2026-01-31", "## 1.2.3 - 31 January 2026"),
        CHANGELOG.replace("## 1.2.3 - 2026-01-31", "## 11.2.3 - 2026-01-31"),
        "",
    ):
        assert refused("v1.2.3", changelog=changelog) == missing
    bad = CHANGELOG.replace("2026-01-31", "2026-02-30")
    assert refused("v1.2.3", changelog=bad) == (
        "CHANGELOG.md's section for 1.2.3 is dated 2026-02-30, which is not a date"
    )
    twice = CHANGELOG + "\n## 1.2.3 - 2026-02-01\n\n- Again.\n"
    assert refused("v1.2.3", changelog=twice) == "CHANGELOG.md has 2 sections for 1.2.3"


def test_a_section_without_an_entry_is_refused_and_a_comment_is_not_an_entry():
    empty = CHANGELOG.replace("- **One.** The first entry.\n- Two.\n", "")
    assert refused("v1.2.3", changelog=empty) == (
        "CHANGELOG.md's section for 1.2.3 has no entry (a line that starts with '- ')"
    )
    hidden = CHANGELOG.replace(
        "- **One.** The first entry.\n- Two.\n", "<!--\n- Not an entry.\n-->\n"
    )
    assert refused("v1.2.3", changelog=hidden).endswith(
        "has no entry (a line that starts with '- ')"
    )


def test_the_notes_are_the_section_without_its_heading_and_its_comments():
    assert release_check.notes("1.2.3", CHANGELOG) == (
        "The text of the release.\n\n- **One.** The first entry.\n- Two.\n"
    )


def test_main_says_one_line_and_exits_non_zero_for_a_refusal(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    assert release_check.main(["v1.2.4", "--root", str(tmp_path)]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == (
        "release refused: the tag 'v1.2.4' is not v1.2.3, the version in pyproject.toml\n"
    )
    assert release_check.main(["v1.2.4", "--notes", "--root", str(tmp_path)]) == 1
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("release refused: ")


def test_main_passes_and_prints_the_notes_on_request(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    assert release_check.main(["v1.2.3", "--root", str(tmp_path)]) == 0
    out, err = capsys.readouterr()
    assert out == "v1.2.3 may be released: it is the version, and CHANGELOG.md has its section\n"
    assert err == ""
    assert release_check.main(["v1.2.3", "--notes", "--root", str(tmp_path)]) == 0
    out, _ = capsys.readouterr()
    assert out == release_check.notes("1.2.3", CHANGELOG)


def test_main_refuses_a_missing_file_in_one_line(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    assert release_check.main(["v1.2.3", "--root", str(tmp_path)]) == 1
    assert capsys.readouterr().err == "release refused: there is no CHANGELOG.md\n"


def test_the_repository_itself_would_release_its_version(capsys):
    """The tag a release of this commit would carry passes against the files as they are."""
    import tomllib

    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    assert release_check.main([f"v{version}", "--root", str(ROOT)]) == 0
    assert release_check.main([f"v{version}", "--notes", "--root", str(ROOT)]) == 0
    text = capsys.readouterr().out.split("\n", 1)[1]
    assert "<!--" not in text and "\n- " in text


def test_the_script_needs_nothing_but_the_standard_library():
    """The workflow runs it with the runner's python3, before any environment is installed."""
    source = (ROOT / "scripts" / "release_check.py").read_text(encoding="utf-8")
    imported = {
        line.split()[1].split(".")[0]
        for line in source.splitlines()
        if line.startswith(("import ", "from "))
    }
    assert imported <= {"__future__", "argparse", "datetime", "pathlib", "re", "sys", "tomllib"}
