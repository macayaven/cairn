"""Unit tests for <PREFIX>_SOURCE_NOTES, the operator's setting that sends pages holding
withheld notes to the model (tests/acceptance/test_m2_withheld_notes.py), on `serve` and
`arrange`. The model is an httpx.MockTransport behind the command line's model options;
nothing leaves the process. Fixtures are synthetic."""

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
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--dry-run` lists the changes first.\n- `--delete` removes extra files.\n"
)
SECOND = (
    "# Drift sync\n\nDrift mirrors two buckets.\n\n## Flags\n\n"
    "- `--retries 3` helps on flaky links.\n\n## Limits\n\n- At most 500 files go in one batch.\n"
)
NAME = "CAIRN_T_SOURCE_NOTES"


@pytest.fixture
def db(tmp_path):
    """A project with a page of mail (six claims) and nothing else."""
    path = tmp_path / "cairn.db"
    store = Store(path)
    project = store.create_project("alpha")
    for text in (FIRST, SECOND):
        note, _ = store.add_note(project.id, text, via="gmail")
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

    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli_arrange, "_model_options", {"transport": httpx.MockTransport(handler)})
    return seen


# Reading the setting

WITHHOLD = [None, "", "   ", "withhold", " withhold ", "\twithhold\n"]
SEND = ["send", "  send\n"]
REFUSED = ["SEND", "Send", "WITHHOLD", "Withhold", "maybe", "send it", "yes", "0", "with hold"]


@pytest.mark.parametrize("value", WITHHOLD + SEND)
def test_withhold_unset_or_blank_withholds_and_send_sends(value):
    environ = {} if value is None else {NAME: value}
    assert cli_arrange.sends_source_notes("CAIRN_T", environ) is (value in SEND)


@pytest.mark.parametrize("value", REFUSED)
def test_any_other_word_is_refused_naming_the_variable_and_never_the_value(value):
    with pytest.raises(ValueError) as refused:
        cli_arrange.sends_source_notes("CAIRN_T", {NAME: value})
    said = str(refused.value)
    assert NAME in said and "withhold" in said and "send" in said and "\n" not in said
    assert value not in said


def test_the_setting_is_read_under_the_prefix_given_and_no_other():
    environ = {"CAIRN_SOURCE_NOTES": "maybe", "OTHER_SOURCE_NOTES": "send"}
    assert cli_arrange.sends_source_notes("CAIRN_T", environ) is False
    assert cli_arrange.sends_source_notes("OTHER", environ) is True


# On the command line


def test_arrange_refuses_a_bad_value_before_it_builds_the_model_or_opens_anything(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv(NAME, "SEND")  # and no model is configured at all
    path = tmp_path / "missing.db"
    argv = ["arrange", "--db", str(path), "--account", "acct_default", "--model-env", "CAIRN_T"]
    assert cli.main(argv) == 2
    out, err = capsys.readouterr()
    assert out == "" and err.count("\n") == 1 and NAME in err and "SEND" not in err
    assert not path.exists()


def test_serve_refuses_a_bad_value_with_one_line(tmp_path, model, monkeypatch, capsys):
    monkeypatch.setenv(NAME, "Send")
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("serve started"))
    path = tmp_path / "new.db"
    assert cli.main(["serve", "--db", str(path), "--model-env", "CAIRN_T"]) == 2
    out, err = capsys.readouterr()
    assert out == "" and err.count("\n") == 1 and NAME in err and "Send" not in err
    assert not path.exists() and model == []


@pytest.mark.parametrize("setting", ["withhold", "send"])
def test_arrange_withholds_a_page_of_mail_unless_sent(db, model, monkeypatch, capsys, setting):
    monkeypatch.setenv(NAME, setting)
    argv = ["arrange", "--db", str(db), "--account", "acct_default", "--model-env", "CAIRN_T"]
    assert cli.main(argv) == 0
    (line,) = capsys.readouterr().out.splitlines()
    if setting == "send":
        assert line.startswith("alpha/drift-sync: arranged, version 2 -> 3, tokens")
        assert len(model) == 1
    else:
        assert line == "alpha/drift-sync: withheld, version 2 -> 3"  # tidied: a repeat folds
        assert model == []


def test_serve_arranger_withholds_unless_told_to_send(db):
    args = cli._parser().parse_args(["serve", "--db", str(db), "--model-env", "CAIRN_T"])
    store = Store(db)
    try:
        with pytest.MonkeyPatch.context() as patched:
            for name, value in ENV.items():
                patched.setenv(name, value)
            default = cli_arrange.serve_arranger(args, store, None)
            sending = cli_arrange.serve_arranger(args, store, None, send_source_notes=True)
    finally:
        store.close()
    assert default._withheld() == sources.withheld_via() and "gmail" in default._withheld()
    assert sending._withheld() == frozenset()


@pytest.mark.parametrize("command", ["serve", "arrange"])
def test_the_help_says_the_two_words_and_that_case_counts(capsys, command):
    with pytest.raises(SystemExit):
        cli.main([command, "--help"])
    said = " ".join(capsys.readouterr().out.split())
    assert "PREFIX_SOURCE_NOTES" in said
    assert "withhold" in said and "send" in said and "lower case" in said


# The start-up line


@pytest.mark.parametrize("setting", [None, "withhold", "send"])
def test_serve_with_a_model_says_whether_source_notes_are_sent(
    db, model, monkeypatch, capsys, setting
):
    if setting is not None:
        monkeypatch.setenv(NAME, setting)
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: None)
    assert cli.main(["serve", "--db", str(db), "--model-env", "CAIRN_T"]) == 0
    lines = capsys.readouterr().err.splitlines()
    (line,) = [line for line in lines if NAME in line]
    assert "'unit-model'" in line
    if setting == "send":
        assert f"{NAME}=send" in line and "sent" in line and "withheld" not in line
    else:
        assert f"{NAME}=withhold" in line and "withheld" in line


def test_serve_without_a_model_says_nothing_of_source_notes(db, monkeypatch, capsys):
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: None)
    for argv in ([], ["--tidy"], ["--no-tidy"]):
        assert cli.main(["serve", "--db", str(db), *argv]) == 0
    assert "SOURCE_NOTES" not in capsys.readouterr().err
