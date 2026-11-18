"""Sentences of run.md's "Connect a model" that could cost a secret or a failed start, each
checked against cairn itself: the two settings files it shows build the model they claim
(through cairn.llm.from_env, as `serve --model-env` does), the command it shows parses, and
its warnings hold. Each check is a function of the guide's text, so it can be run on a
doctored copy to see it fail. Every HTTP call goes through httpx.MockTransport."""

import json
import re
import shlex
from importlib.resources import files
from pathlib import Path

import httpx
import pytest
import yaml

from cairn.llm import AnthropicMessages, LLMConfigError, OpenAICompatible, from_env
from cairn.web import cli, cli_arrange


def section() -> str:
    text = (files("cairn") / "guides" / "run.md").read_text(encoding="utf-8")
    return text.split("## Connect a model", 1)[1].split("\n## ", 1)[0]


def env_files(text: str) -> dict[str, dict[str, str]]:
    """Each settings file the section shows (a ```text block under a line naming the file),
    read as `docker run --env-file` reads one: NAME=VALUE per line, nothing stripped or
    unquoted, blank lines and # comments skipped."""
    found = {}
    for name, body in re.findall(r"`([\w.-]+\.env)`[^\n]*\n\n```text\n(.*?)```", text, re.DOTALL):
        lines = [line for line in body.splitlines() if line and not line.startswith("#")]
        found[name] = dict(line.split("=", 1) for line in lines)
    return found


def command(text: str) -> list[str]:
    """The words of the section's `docker run` command."""
    (block,) = re.findall(r"```bash\n(docker run .*?)```", text, re.DOTALL)
    return shlex.split(block.replace("\\\n", " "))


def prefix(text: str) -> str:
    words = command(text)
    return words[words.index("--model-env") + 1]


def answers(request: httpx.Request) -> httpx.Response:
    """Either format's reply, by the path asked."""
    if request.url.path.endswith("/messages"):
        reply = {"type": "message", "content": [{"type": "text", "text": "{}"}]}
        return httpx.Response(200, json={**reply, "stop_reason": "end_turn"})
    choice = {"message": {"content": "{}"}, "finish_reason": "stop"}
    return httpx.Response(200, json={"choices": [choice]})


def built(settings: dict[str, str], name: str) -> tuple[object, httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return answers(request)

    model = from_env(environ=settings, prefix=name, transport=httpx.MockTransport(handler))
    model.complete([{"role": "user", "content": "hi"}])
    return model, seen[0]


def test_the_two_settings_files_build_the_model_they_claim():
    """'An OpenAI-compatible endpoint' and 'Anthropic's Messages API', side by side, each a
    file of the variables it needs: every value is unquoted, and each file builds its class,
    posting where the guide says, with the key in that format's header."""
    text = section()
    name = prefix(text)
    files_shown = env_files(text)
    assert set(files_shown) == {"openai.env", "anthropic.env"}
    for settings in files_shown.values():
        assert all(name_.startswith(f"{name}_") for name_ in settings)
        assert not any(value[:1] in "\"'" for value in settings.values())  # docker keeps quotes

    openai, request = built(files_shown["openai.env"], name)
    assert type(openai) is OpenAICompatible
    assert str(request.url) == files_shown["openai.env"][f"{name}_BASE_URL"] + "/chat/completions"
    assert request.headers["authorization"].startswith("Bearer ")

    claude, request = built(files_shown["anthropic.env"], name)
    assert type(claude) is AnthropicMessages and claude.name == "claude-haiku-5-5"
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert "x-api-key" in request.headers and "authorization" not in request.headers
    assert "Claude Haiku 5.5" in text


def test_anthropics_own_api_needs_the_key_and_says_so_before_any_call():
    """'Leave out <PREFIX>_BASE_URL for Anthropic's own API ... it then needs the key.'"""
    text = section()
    name = prefix(text)
    assert f"Leave out `{name}_BASE_URL` for Anthropic's own API" in text
    settings = dict(env_files(text)["anthropic.env"])
    assert f"{name}_BASE_URL" not in settings
    del settings[f"{name}_API_KEY"]
    with pytest.raises(LLMConfigError, match=f"{name}_API_KEY"):
        from_env(environ=settings, prefix=name)


def test_the_command_starts_serve_with_the_model_and_the_settings_file():
    """The command repeats the image's default command, adds --model-env with the files'
    prefix, and gives the settings with --env-file: the key is on no command line."""
    words = command(section())
    assert words[:2] == ["docker", "run"]
    image = words.index("cairn:local")
    assert words[words.index("--env-file") + 1] in {"./openai.env", "./anthropic.env"}
    args = cli._parser().parse_args(words[image + 1 :])
    assert (args.command, str(args.db), args.host) == ("serve", "/data/cairn.db", "0.0.0.0")
    assert args.model_env == prefix(section())
    assert not any("KEY=" in word for word in words)
    # "Start it" already made a container named cairn: it is removed first, or the name is taken.
    text = section()
    assert words[words.index("--name") + 1] == "cairn"
    assert text.index("docker stop cairn\ndocker rm cairn") < text.index("docker run")


def test_the_prefix_is_never_anthropic_and_the_guide_says_why():
    """'Do not name the variables ANTHROPIC_API_KEY or ANTHROPIC_BASE_URL in a shell where
    Claude Code runs: other tools read those names. Use a prefix of your own.'"""
    text = section()
    assert prefix(text) != "ANTHROPIC"
    for settings in env_files(text).values():
        assert not set(settings) & {"ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"}
    flat = " ".join(text.split())
    assert "Do not name the variables `ANTHROPIC_API_KEY` or `ANTHROPIC_BASE_URL`" in flat
    assert "Claude Code" in flat and "use a prefix of your own" in flat


def test_an_endpoint_over_plain_http_off_the_container_is_refused_at_start():
    """'The address is https, or plain http to the container itself only': a model the host
    serves over plain http is refused, naming the variable and not the address."""
    text = section()
    name = prefix(text)
    assert "or plain http to the container itself" in " ".join(text.split())
    settings = {
        **env_files(text)["openai.env"],
        f"{name}_BASE_URL": "http://host.docker.internal:8080/v1",
    }
    with pytest.raises(LLMConfigError) as caught:
        from_env(environ=settings, prefix=name)
    assert f"{name}_BASE_URL" in str(caught.value) and "docker" not in str(caught.value)


def test_thinking_is_off_unless_the_extra_fields_say_otherwise():
    """'cairn switches thinking off ... `{"thinking": null}` leaves the field out.'"""
    text = section()
    name = prefix(text)
    flat = " ".join(text.split())
    shown = re.search(r"`(\{\"thinking\": null\})`", flat)
    assert shown and "switches thinking off" in flat
    settings = {**env_files(text)["anthropic.env"], f"{name}_REQUEST_EXTRA": shown[1]}
    _, request = built(settings, name)
    assert "thinking" not in json.loads(request.content)
    _, request = built(env_files(text)["anthropic.env"], name)
    assert json.loads(request.content)["thinking"] == {"type": "disabled"}
    # The line it shows for a model that refuses that, added to the file as it stands.
    (line,) = re.findall(rf"`({name}_REQUEST_EXTRA=[^`]+)`", flat)
    extra_name, extra_value = line.split("=", 1)
    _, request = built({**env_files(text)["anthropic.env"], extra_name: extra_value}, name)
    body = json.loads(request.content)
    assert body["thinking"] == {"type": "adaptive"} and body["output_config"] == {"effort": "low"}


def test_the_checks_see_a_doctored_guide():
    """The parsing above is not vacuous: a quoted value and a wrong prefix are caught."""
    text = section()
    name = prefix(text)
    quoted = text.replace(f"{name}_MODEL=claude-haiku-5-5", f'{name}_MODEL="claude-haiku-5-5"')
    assert env_files(quoted)["anthropic.env"][f"{name}_MODEL"].startswith('"')
    renamed = text.replace(f"--model-env {name}", "--model-env ANTHROPIC")
    assert prefix(renamed) == "ANTHROPIC"


# "Tidy pages without a model" (T4f), the section before "Connect a model"


def tidy_section() -> str:
    text = (files("cairn") / "guides" / "run.md").read_text(encoding="utf-8")
    return text.split("## Tidy pages without a model", 1)[1].split("\n## ", 1)[0]


def test_the_tidy_command_switches_it_off_with_no_tidy_and_no_model():
    """The command repeats the image's default command and adds --no-tidy only: no settings
    file, no model, and no worker (test_arrange_cli_unit.py::test_serve_tidies_by_default_and_
    no_tidy_turns_it_off). "Start it" made a container named cairn, so it is removed first."""
    text = tidy_section()
    words = command(text)
    image = words.index("cairn:local")
    args = cli._parser().parse_args(words[image + 1 :])
    assert (args.command, str(args.db), args.host) == ("serve", "/data/cairn.db", "0.0.0.0")
    assert args.no_tidy and not args.tidy and args.model_env is None
    assert "--env-file" not in words
    assert words[words.index("--name") + 1] == "cairn"
    assert text.index("docker stop cairn\ndocker rm cairn") < text.index("docker run")
    flat = " ".join(text.split())
    assert "To switch it off, replace the container" in flat
    assert "repeating the default command with `--no-tidy`" in flat
    whole = (files("cairn") / "guides" / "run.md").read_text(encoding="utf-8")
    assert whole.index("## Tidy pages without a model") < whole.index("## Connect a model")


def test_the_tidy_section_switches_it_off_through_the_compose_files_extra_args():
    """'add `--no-tidy` to `CAIRN_EXTRA_ARGS`': compose.yaml's command for cairn ends with
    CAIRN_EXTRA_ARGS after `serve`, as deploy/README.md uses it for other options, and serve
    takes --no-tidy there."""
    flat = " ".join(tidy_section().split())
    assert (
        "With the compose files in `deploy/`, add `--no-tidy` to `CAIRN_EXTRA_ARGS` in"
        " `deploy/.env` and run `docker compose up -d`." in flat
    )
    compose = (Path(__file__).parents[2] / "deploy" / "compose.yaml").read_text("utf-8")
    serve = yaml.safe_load(compose)["services"]["cairn"]["command"]
    assert serve.startswith("serve ") and serve.endswith(" ${CAIRN_EXTRA_ARGS:-}")
    serve = re.sub(r"\$\{CAIRN_DOMAIN[^}]*\}", "cairn.example.test", serve)
    serve = re.sub(r"\$\{CAIRN_SUBNET[^}]*\}", "10.213.47.0/24", serve)
    args = cli._parser().parse_args(serve.replace("${CAIRN_EXTRA_ARGS:-}", "--no-tidy").split())
    assert args.no_tidy and not args.tidy and args.public_url == "https://cairn.example.test"
    readme = (Path(__file__).parents[2] / "deploy" / "README.md").read_text("utf-8")
    assert "`CAIRN_EXTRA_ARGS`, extra arguments for `serve`" in readme


def test_the_tidy_section_says_what_it_needs_and_what_a_page_shows():
    """'no model, no key and no budget' (tests/unit/test_tidy_unit.py::test_a_tidy_is_never_
    screened_reserved_or_charged); 'Arranged by cairn' is the web view's own words (::test_
    the_web_view_says_cairn_tidied_a_version); it is on by default and a model takes its
    place (test_arrange_cli_unit.py::test_serve_tidies_by_default_and_no_tidy_turns_it_off
    and ::test_serve_runs_the_models_worker_only_with_model_env); a tidy is a version like
    any other, which a plan's versions_per_page counts (test_arrange_cli_unit.py::test_a_
    tidy_saved_through_the_worker_view_counts_toward_the_plans_versions)."""
    flat = " ".join(tidy_section().split())
    assert "It needs no model, no key and no budget, and nothing leaves the container" in flat
    assert 'a tidied page says "Arranged by cairn"' in flat
    assert "It is off unless you switch it on" not in flat
    assert "It is on by default" in flat and cli_arrange.TIDY_BY_DEFAULT is True
    assert "A model connected with `--model-env` (below) arranges pages in its place" in flat
    assert (
        "where `serve` runs with `--plans` and a plan keeps only the last versions of each page,"
        " it counts toward them like any other version" in flat
    )
    args = cli._parser().parse_args(["serve", "--db", "x.db", "--tidy", "--model-env", "M"])
    assert cli_arrange.refused(args) == "--tidy uses no model, so it takes no --model-env"


def test_the_tidy_section_tidies_the_pages_already_there_with_arrange():
    """'cairn then tidies a page each time a note arrives on it' (the worker is queued by a
    note: tests/acceptance/test_t4f_tidy.py::test_the_worker_without_a_model_tidies_the_pages_
    notes_touch); after an upgrade, the pages already there take one `arrange --tidy` pass,
    which parses."""
    flat = " ".join(tidy_section().split())
    assert "cairn then tidies a page each time a note arrives on it, a moment after the note" in (
        flat
    )
    assert (
        "After an upgrade from a version that did not tidy, the pages already there are tidied"
        " as notes arrive on them. To tidy them all in one pass, run" in flat
    )
    (line,) = re.findall(r"`(docker exec cairn python -m cairn\.server arrange [^`]+)`", flat)
    words = shlex.split(line)
    args = cli._parser().parse_args(words[words.index("cairn.server") + 1 :])
    assert (args.command, str(args.db), args.tidy, args.model_env) == (
        "arrange",
        "/data/cairn.db",
        True,
        None,
    )
    assert args.account == "acct_default" and cli_arrange.refused(args) is None
