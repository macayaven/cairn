"""Unit tests for what the runner, the Sources page and source-add ask of a kind (C9): the
project a kind proposes for a source that names none (SourceKind.own_project, proposed_project), the
settings a kind adds to source-add --help (SourceKind.settings), and the sentence a pull says
about where it stopped (Pulled.stopped), which the run's report keeps (SyncReport.said). A
fake kind stands in; nothing leaves the process. Fixtures are synthetic."""

import asyncio
import re
from typing import ClassVar

import httpx
import pytest
from starlette.testclient import TestClient

from cairn.server import create_app, main
from cairn.sources import KINDS, NoteDraft, Pulled, SourceKind, SyncReport, run_source
from cairn.store import Store
from cairn.web import cli

NOTE = "# Gauges\n\nThe north gauge reads 4 bar.\n"
MORE = "it had more; the next run goes on"
BASE = "http://127.0.0.1:8765"
SECRET = "seam-" + "made-up-" + "secret-77a1c3"


class Fake:
    """A source whose pull answers the next of `answers`."""

    answers: ClassVar[list] = []

    def __init__(self, name: str) -> None:
        self.name = name
        self.secrets = (SECRET,)

    async def pull(self, cursor):
        return Fake.answers.pop(0)


def opened(name, config, **_):
    return Fake(name)


def check(config):
    return {"projects": list(config.get("projects", []))}


@pytest.fixture(autouse=True)
def kinds(monkeypatch):
    monkeypatch.setitem(KINDS, "seam_plain", SourceKind(check=check, open=opened, via="seam-plain"))
    monkeypatch.setitem(
        KINDS,
        "seam_own",
        SourceKind(
            check=check, open=opened, via="seam-own", starts_off=True, own_project=True,
            settings='{"projects": []}; the source starts off',
        ),
    )  # fmt: skip
    Fake.answers = []


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "cairn.db")
    s.create_project("gauges")
    yield s
    s.close()


def draft(item: str, text: str = NOTE) -> NoteDraft:
    return NoteDraft(
        project="gauges", title="Gauges", text=text, source_ref=f"seam:{item}", label="seam",
        item=item,
    )  # fmt: skip


def run(store, name="feed") -> SyncReport:
    return asyncio.run(run_source(store, name, http=httpx.AsyncClient(), env={}))


# The project a kind proposes


def test_a_kind_proposes_its_default_project_or_with_own_project_the_source_s_name():
    plain = SourceKind(check=check, open=opened, via="p")
    assert plain.own_project is False and plain.proposed_project("erp") is None
    meetings = SourceKind(check=check, open=opened, via="m", default_project="meetings")
    assert meetings.proposed_project("erp") == "meetings"
    own = SourceKind(check=check, open=opened, via="o", own_project=True)
    assert own.proposed_project("erp-feed") == "erp-feed"
    assert own.proposed_project("a" * 81) is None  # a source's name that is no project's name


def test_the_built_in_kinds_propose_what_they_did():
    owning = {name for name, kind in KINDS.items() if kind.own_project}
    assert owning - {"seam_own"} == {"connector"}  # the one kind that proposes its own name
    assert KINDS["granola"].proposed_project("erp") == "meetings"
    assert KINDS["langfuse"].proposed_project("erp") is None


def test_source_add_names_the_kind_s_proposed_project(tmp_path, capsys):
    db = tmp_path / "cairn.db"
    Store(db).close()
    argv = ["source-add", "--db", str(db), "--name", "north-gauges", "--kind", "seam_own"]
    assert main([*argv, "--config", "{}"]) == 0
    assert "--project north-gauges" in capsys.readouterr().out


def test_the_sources_page_proposes_the_kind_s_project(tmp_path):
    store = Store(tmp_path / "cairn.db")
    try:
        owner = store.sign_in(
            "sso", "o", "o@example.test", "Olive", issuer="https://id.example.test",
            join="default",
        )  # fmt: skip
        store.add_source("north-gauges", "seam_own", {})
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            client.cookies.set("cairn_session", owner.token)
            page = client.get("/sources").text
        (field,) = re.findall(
            r'<input type="text" id="source-north-gauges-new-project"[^>]*>', page
        )
        assert 'value="north-gauges"' in field
    finally:
        store.close()


# What a kind adds to source-add --help


def test_a_kind_s_settings_are_listed_by_source_add_help(capsys):
    assert SourceKind(check=check, open=opened, via="p").settings is None
    with pytest.raises(SystemExit):
        main(["source-add", "--help"])
    said = " ".join(capsys.readouterr().out.split())
    assert ' seam_own: {"projects": []}; the source starts off' in said
    assert " seam_plain:" not in said  # a kind without settings adds nothing


# The sentence a pull says about where it stopped


def test_a_pull_that_stopped_says_why_in_the_run_s_report(store):
    store.add_source("feed", "seam_plain", {"projects": ["gauges"]})
    Fake.answers = [Pulled([draft("g-1")], cursor="c1", stopped=MORE)]
    report = run(store)
    assert report.saved == 1 and report.said == "it had more; the next run goes on"
    assert store.get_source("feed").last_report["said"] == report.said
    assert report.said in cli._counts(store.get_source("feed").last_report)


def test_a_pull_that_stopped_with_nothing_new_says_that_too(store):
    store.add_source("feed", "seam_plain", {"projects": ["gauges"]})
    Fake.answers = [Pulled([draft("g-1")], cursor="c1")]
    run(store)
    Fake.answers = [Pulled([draft("g-1"), draft("g-1")], cursor="c2", stopped="it had more.")]
    report = run(store)
    assert (report.saved, report.unchanged) == (0, 2)
    assert report.said.startswith("it had more.") and "nothing it gave was new" in report.said
    Fake.answers = [Pulled([draft("g-1"), draft("g-2")], cursor="c3", stopped="it had more.")]
    assert run(store).said == "it had more."  # g-2 was new


def test_a_report_without_a_sentence_keeps_none_and_stores_none(store):
    store.add_source("feed", "seam_plain", {"projects": ["gauges"]})
    Fake.answers = [Pulled([draft("g-1")], cursor="c1")]
    report = run(store)
    assert report.said is None and "said" not in report.counts()
    assert "said" not in store.get_source("feed").last_report
    Fake.answers = [Pulled([draft("g-2"), draft("g-2")], cursor="c2")]  # not stopped
    assert run(store).said is None


def test_a_sentence_that_holds_the_source_s_secret_is_not_kept(store):
    store.add_source("feed", "seam_plain", {"projects": ["gauges"]})
    Fake.answers = [Pulled([draft("g-1")], cursor="c1", stopped=f"it said {SECRET}")]
    report = run(store)
    assert report.saved == 1 and report.said is None
    assert SECRET not in str(store.get_source("feed").last_report)


def test_the_sources_page_shows_the_sentence(store):
    owner = store.sign_in(
        "sso", "o", "o@example.test", "Olive", issuer="https://id.example.test", join="default"
    )
    store.add_source("feed", "seam_plain", {"projects": ["gauges"]})
    Fake.answers = [Pulled([draft("g-1")], cursor="c1", stopped=MORE)]
    run(store)
    with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
        client.cookies.set("cairn_session", owner.token)
        page = client.get("/sources").text
    assert "it had more; the next run goes on" in page
