"""The operator's setting that makes projects opt in (review finding Privacy 4):
<PREFIX>_UNDECIDED_PROJECTS, "send" (the default, also unset or blank: today's behaviour) or
"withhold" (only the pages of projects whose people chose "allowed" go to the model), read as
<PREFIX>_SOURCE_NOTES is and refused the same way. The model is an httpx.MockTransport behind
the command line's model options; nothing leaves the process. Every name is made up."""

import json

import httpx
import pytest
import uvicorn

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
UNDECIDED = "CAIRN_T_UNDECIDED_PROJECTS"
MODEL = ["--account", "acct_default", "--model-env", "CAIRN_T"]


@pytest.fixture
def db(tmp_path):
    """Two projects of one page each, from notes a person typed: "plain" and "kept" (never)."""
    path = tmp_path / "cairn.db"
    store = Store(path)
    for name in ("plain", "kept"):
        project = store.create_project(name)
        for text in (FIRST, SECOND):
            note, _ = store.add_note(project.id, text)
            compile_note(store, note, gate=lint_gate)
    store.set_project_models(store.get_project("kept").id, "never")
    store.close()
    return path


@pytest.fixture
def model(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        user = json.loads(request.content)["messages"][1]["content"]
        count = user.count(' id="cl_')
        answer = {"order": list(range(count, 0, -1)), "same": []}
        message = {"role": "assistant", "content": json.dumps(answer)}
        return httpx.Response(200, json={"choices": [{"message": message}]})

    monkeypatch.delenv(UNDECIDED, raising=False)
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli_arrange, "_model_options", {"transport": httpx.MockTransport(handler)})
    return seen


def statuses(capsys) -> dict[str, str]:
    lines = capsys.readouterr().out.splitlines()
    return {line.split("/", 1)[0]: line.split(": ", 1)[1].split(",", 1)[0] for line in lines}


# Reading the setting

SEND = [None, "", "   ", "send", " send\n"]
WITHHOLD = ["withhold", "\twithhold "]
REFUSED = ["SEND", "Withhold", "opt-in", "yes", "0", "send it"]


@pytest.mark.parametrize("value", SEND + WITHHOLD)
def test_send_unset_or_blank_is_today_and_withhold_makes_projects_opt_in(value):
    environ = {} if value is None else {UNDECIDED: value}
    assert cli_arrange.opts_in("CAIRN_T", environ) is (value in WITHHOLD)


@pytest.mark.parametrize("value", REFUSED)
def test_any_other_word_is_refused_naming_the_variable_and_never_the_value(value):
    with pytest.raises(ValueError) as refused:
        cli_arrange.opts_in("CAIRN_T", {UNDECIDED: value})
    said = str(refused.value)
    assert UNDECIDED in said and "withhold" in said and "send" in said and "\n" not in said
    assert value not in said.replace(UNDECIDED, "")


# arrange


@pytest.mark.parametrize(("value", "plain"), [(None, "arranged"), ("withhold", "undecided")])
def test_arrange_sends_an_undecided_project_only_without_the_setting(
    db, model, monkeypatch, capsys, value, plain
):
    if value is not None:
        monkeypatch.setenv(UNDECIDED, value)
    assert cli.main(["arrange", "--db", str(db), *MODEL]) == 0
    assert statuses(capsys) == {"plain": plain, "kept": "kept from models"}
    assert len(model) == (1 if plain == "arranged" else 0)


def test_arrange_refuses_a_bad_value_before_it_opens_anything(tmp_path, model, capsys):
    path = tmp_path / "missing.db"
    with pytest.MonkeyPatch.context() as patched:
        patched.setenv(UNDECIDED, "Withhold")
        assert cli.main(["arrange", "--db", str(path), *MODEL]) == 2
    out, err = capsys.readouterr()
    assert out == "" and err.count("\n") == 1 and UNDECIDED in err and "Withhold" not in err
    assert not path.exists() and model == []


def test_arrange_tidy_reads_no_such_setting(db, monkeypatch, capsys):
    monkeypatch.setenv(UNDECIDED, "maybe")
    assert cli.main(["arrange", "--db", str(db), "--account", "acct_default", "--tidy"]) == 0


# serve


def test_serve_refuses_a_bad_value_with_one_line(tmp_path, model, monkeypatch, capsys):
    monkeypatch.setenv(UNDECIDED, "yes")
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("serve started"))
    path = tmp_path / "new.db"
    assert cli.main(["serve", "--db", str(path), "--model-env", "CAIRN_T"]) == 2
    out, err = capsys.readouterr()
    assert out == "" and err.count("\n") == 1 and UNDECIDED in err
    assert not path.exists() and model == []


def test_serve_without_a_model_reads_no_such_setting(db, monkeypatch):
    monkeypatch.setenv(UNDECIDED, "maybe")
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: None)
    assert cli.main(["serve", "--db", str(db)]) == 0


def test_serve_passes_the_choice_to_its_worker_and_says_it(db, model, monkeypatch, capsys):
    built = []
    real = cli_arrange.serve_arranger

    def spying(*args, **options):
        built.append(real(*args, **options))
        return built[-1]

    monkeypatch.setattr(cli_arrange, "serve_arranger", spying)
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: None)
    assert cli.main(["serve", "--db", str(db), "--model-env", "CAIRN_T"]) == 0
    first = capsys.readouterr().err
    monkeypatch.setenv(UNDECIDED, "withhold")
    assert cli.main(["serve", "--db", str(db), "--model-env", "CAIRN_T"]) == 0
    second = capsys.readouterr().err
    assert [arranger._opt_in for arranger in built] == [False, True]
    assert UNDECIDED not in first
    assert f"{UNDECIDED}=withhold" in second and "allowed" in second


@pytest.mark.parametrize("command", ["serve", "arrange"])
def test_the_help_names_the_setting(capsys, command):
    with pytest.raises(SystemExit):
        cli.main([command, "--help"])
    said = " ".join(capsys.readouterr().out.split())
    assert "PREFIX_UNDECIDED_PROJECTS" in said and "allowed" in said
