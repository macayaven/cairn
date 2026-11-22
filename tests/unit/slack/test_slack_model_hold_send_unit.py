"""What lifts the hold on notes that came through Slack: only the operator's choice that names
Slack, <PREFIX>_SLACK_NOTES=send (cairn.web.cli_arrange.sends_slack_notes), read as
<PREFIX>_SOURCE_NOTES is. <PREFIX>_SOURCE_NOTES=send, chosen before Slack existed, does not send
Slack's notes. The model is an httpx.MockTransport behind the command line's model options;
nothing leaves the process. Every name is made up."""

import json

import httpx
import pytest
import uvicorn

from cairn import sources
from cairn.compiler import compile_note
from cairn.gate import lint_gate
from cairn.store import Store
from cairn.web import cli, cli_arrange

ENV = {"CAIRN_T_BASE_URL": "http://127.0.0.1:9/v1", "CAIRN_T_MODEL": "unit-model"}
FIRST = (
    "# Quince rows\n\nRow 4 holds the quince jars.\n\n## Shelves\n\n"
    "- The top shelf is for labels.\n- The low shelf is for lids.\n"
)
SECOND = (
    "# Quince rows\n\nRow 4 holds the quince jars.\n\n## Shelves\n\n"
    "- Brook moved the twine.\n\n## Counts\n\n- Ada counts the jars on Fridays.\n"
)
PERMALINK = "https://fable-works.slack.com/archives/C0LIBRARY1/p1900000000000100"
SLACK = "CAIRN_T_SLACK_NOTES"
SOURCE = "CAIRN_T_SOURCE_NOTES"
MODEL = ["--account", "acct_default", "--model-env", "CAIRN_T"]


@pytest.fixture
def db(tmp_path):
    """Three projects of one page each: "filed" from Slack, "mailed" from Gmail, "plain" from
    no source."""
    path = tmp_path / "cairn.db"
    store = Store(path)
    for name, via, source_ref in (
        ("filed", "slack", PERMALINK),
        ("mailed", "gmail", None),
        ("plain", None, None),
    ):
        project = store.create_project(name)
        for text in (FIRST, SECOND):
            note, _ = store.add_note(project.id, text, via=via, source_ref=source_ref)
            compile_note(store, note, gate=lint_gate)
    store.close()
    return path


@pytest.fixture
def model(monkeypatch):
    """The endpoint: answers with the page's claim numbers reversed; records each request."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        user = json.loads(request.content)["messages"][1]["content"]
        count = user.count(' id="cl_')
        answer = {"order": list(range(count, 0, -1)), "same": []}
        message = {"role": "assistant", "content": json.dumps(answer)}
        return httpx.Response(200, json={"choices": [{"message": message}]})

    for name in (SLACK, SOURCE):
        monkeypatch.delenv(name, raising=False)
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli_arrange, "_model_options", {"transport": httpx.MockTransport(handler)})
    return seen


def statuses(capsys) -> dict[str, str]:
    lines = capsys.readouterr().out.splitlines()
    return {line.split("/", 1)[0]: line.split(": ", 1)[1].split(",", 1)[0] for line in lines}


# Reading the setting

WITHHOLD = [None, "", "   ", "withhold", " withhold ", "\twithhold\n"]
SEND = ["send", "  send\n"]
REFUSED = ["SEND", "Send", "WITHHOLD", "maybe", "send it", "yes", "slack", "0"]


@pytest.mark.parametrize("value", WITHHOLD + SEND)
def test_withhold_unset_or_blank_holds_slack_and_send_sends_it(value):
    environ = {} if value is None else {SLACK: value}
    assert cli_arrange.sends_slack_notes("CAIRN_T", environ) is (value in SEND)


@pytest.mark.parametrize("value", REFUSED)
def test_any_other_word_is_refused_naming_the_variable_and_never_the_value(value):
    with pytest.raises(ValueError) as refused:
        cli_arrange.sends_slack_notes("CAIRN_T", {SLACK: value})
    said = str(refused.value)
    assert SLACK in said and "withhold" in said and "send" in said and "\n" not in said
    assert value not in said.replace(SLACK, "")


def test_the_choice_that_sends_source_notes_does_not_name_slack():
    environ = {SOURCE: "send"}
    assert cli_arrange.sends_source_notes("CAIRN_T", environ) is True
    assert cli_arrange.sends_slack_notes("CAIRN_T", environ) is False


# arrange


@pytest.mark.parametrize(
    ("source", "slack", "sent"),
    [
        (None, None, {"plain"}),
        ("send", None, {"plain", "mailed"}),  # chosen before Slack: Slack stays held
        (None, "send", {"plain", "filed"}),  # Slack alone: Gmail stays held
        ("send", "send", {"plain", "mailed", "filed"}),
    ],
)
def test_arrange_sends_slack_s_page_only_when_slack_is_named(
    db, model, monkeypatch, capsys, source, slack, sent
):
    for name, value in ((SOURCE, source), (SLACK, slack)):
        if value is not None:
            monkeypatch.setenv(name, value)
    assert cli.main(["arrange", "--db", str(db), *MODEL]) == 0
    done = statuses(capsys)
    assert {name for name, status in done.items() if status == "arranged"} == sent
    assert {name for name, status in done.items() if status == "withheld"} == (
        {"plain", "mailed", "filed"} - sent
    )
    assert len(model) == len(sent)


def test_arrange_refuses_a_bad_slack_value_before_it_opens_anything(tmp_path, model, capsys):
    path = tmp_path / "missing.db"
    with pytest.MonkeyPatch.context() as patched:
        patched.setenv(SLACK, "Send")
        assert cli.main(["arrange", "--db", str(path), *MODEL]) == 2
    out, err = capsys.readouterr()
    assert out == "" and err.count("\n") == 1 and SLACK in err and "Send" not in err
    assert not path.exists() and model == []


# serve


def test_serve_refuses_a_bad_slack_value_with_one_line(tmp_path, model, monkeypatch, capsys):
    monkeypatch.setenv(SLACK, "yes")
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("serve started"))
    path = tmp_path / "new.db"
    assert cli.main(["serve", "--db", str(path), "--model-env", "CAIRN_T"]) == 2
    out, err = capsys.readouterr()
    assert out == "" and err.count("\n") == 1 and SLACK in err
    assert not path.exists() and model == []


def test_serve_without_a_model_reads_no_slack_setting(db, monkeypatch):
    monkeypatch.setenv(SLACK, "maybe")
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: None)
    assert cli.main(["serve", "--db", str(db)]) == 0


@pytest.mark.parametrize(("source", "slack"), [(False, False), (True, False), (False, True)])
def test_serve_arranger_holds_slack_unless_slack_is_named(db, model, source, slack):
    args = cli._parser().parse_args(["serve", "--db", str(db), "--model-env", "CAIRN_T"])
    store = Store(db)
    try:
        arranger = cli_arrange.serve_arranger(
            args, store, None, send_source_notes=source, send_slack_notes=slack
        )
    finally:
        store.close()
    assert arranger._held_via == (frozenset() if slack else sources.HELD_VIA)
    assert arranger._withheld() == (frozenset() if source else sources.withheld_via())


def test_serve_passes_the_slack_choice_to_its_worker(db, model, monkeypatch):
    built = []
    real = cli_arrange.serve_arranger

    def spying(*args, **options):
        built.append(real(*args, **options))
        return built[-1]

    monkeypatch.setattr(cli_arrange, "serve_arranger", spying)
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: None)
    monkeypatch.setenv(SOURCE, "send")
    assert cli.main(["serve", "--db", str(db), "--model-env", "CAIRN_T"]) == 0
    monkeypatch.setenv(SLACK, "send")
    assert cli.main(["serve", "--db", str(db), "--model-env", "CAIRN_T"]) == 0
    assert [arranger._held_via for arranger in built] == [sources.HELD_VIA, frozenset()]


@pytest.mark.parametrize("command", ["serve", "arrange"])
def test_the_help_names_the_slack_setting(capsys, command):
    with pytest.raises(SystemExit):
        cli.main([command, "--help"])
    said = " ".join(capsys.readouterr().out.split())
    assert "PREFIX_SLACK_NOTES" in said and "Slack" in said
