"""What a person is told the workspace export holds. The file is a content export (the format is
cairn.store.portable's): the notes and pages, complete, and a description of each project. Keys,
agents' connections, sets, settings and sources are set up again on another install, and the
audit log stays behind. No page says "everything" of it."""

from pathlib import Path

from guide_source import guide_source

from cairn.web.workspace import PARTS

SRC = Path(__file__).resolve().parents[2] / "src" / "cairn"


def flat(text: str) -> str:
    return " ".join(text.split())


def template(name: str) -> str:
    return flat((SRC / "templates" / name).read_text(encoding="utf-8"))


def test_the_privacy_guide_says_the_export_holds_notes_and_pages_and_what_a_move_needs():
    text = flat(guide_source("privacy"))
    assert (
        "An owner downloads the workspace's notes and pages as one zip file, under Workspace,"
        " then Data and deletion. It is a content export: it holds every note exactly as"
        " written, every page with every version it keeps, and a description of each project."
        in text
    )
    assert (
        "It does not hold keys, sessions, agents' connections, sets, other settings, sources or"
        " the audit log; of the settings it holds one, each project's **Models** choice." in text
    )
    assert (
        "A move to another install needs more than the file: set up keys, agents'"
        " connections, sets, settings and sources again there. The audit log stays behind." in text
    )
    assert "downloads everything" not in text


def test_the_run_guide_says_the_same_of_a_backup_made_with_export():
    text = flat(guide_source("run"))
    assert (
        "**An export** holds the content of your workspace: every project's description,"
        " every note exactly as it was written, and every page version cairn keeps. It does"
        " not hold keys, agents' connections, sets, other settings, sources or the audit log,"
        " so after a restore you set those up again; of the settings it holds one, each"
        " project's **Models** choice." in text
    )


def test_the_data_page_says_what_the_export_holds_and_what_stays_behind():
    text = template("account.html")
    assert (
        "The workspace's notes and pages in one zip file: every project's description, every"
        " note exactly as it was written, and every version of every page kept, with the"
        " source lines each claim cites. Keys, sessions, agents' connections, sets, settings,"
        " sources and the audit log are not in it: to move to another install, set up keys,"
        " agents' connections, sets, settings and sources again there. The audit log stays"
        " here." in text
    )
    assert "export first if you want its notes and pages back." in text
    assert "export first if you may want it back" not in text


def test_the_landing_page_and_the_workspace_page_do_not_call_the_export_everything():
    landing = template("landing.html")
    assert "Download your workspace's notes and pages as one file, or delete it" in landing
    assert "Download everything" not in landing
    (data,) = [part for part in PARTS if part.label == "Data and deletion"]
    assert data.sentence == (
        "Download this workspace's notes and pages as one file, or delete the workspace."
    )
