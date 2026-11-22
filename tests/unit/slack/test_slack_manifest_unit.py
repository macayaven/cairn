"""The generated app asks Slack for only its three deliberate entry points."""

import importlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest


def build(**kwargs):
    # Import in the test call so a missing implementation is a failing test, not collection failure.
    module = importlib.import_module("cairn.slack.manifest")
    return module.build_manifest(
        public_url="https://cairn.example", install="sl_012345abcdef", **kwargs
    )


def test_manifest_wires_command_message_shortcut_and_dm_events():
    manifest = build()
    assert manifest["display_information"]["name"] == "cairn"
    assert manifest["features"]["bot_user"]["display_name"] == "cairn"
    assert manifest["oauth_config"] == {
        "scopes": {"bot": ["chat:write", "commands", "im:history", "users:read"]}
    }
    commands = manifest["features"]["slash_commands"]
    assert len(commands) == 1
    assert commands[0]["command"] == "/cairn"
    assert commands[0]["description"]
    assert commands[0]["url"] == (
        "https://cairn.example/integrations/slack/sl_012345abcdef/commands"
    )
    shortcuts = manifest["features"]["shortcuts"]
    assert len(shortcuts) == 1
    assert shortcuts[0]["type"] == "message"
    assert shortcuts[0]["name"] == "File in cairn"
    assert 1 <= len(shortcuts[0]["description"]) <= 150
    assert shortcuts[0]["callback_id"] == "file_in_cairn"
    assert manifest["settings"]["event_subscriptions"] == {
        "request_url": "https://cairn.example/integrations/slack/sl_012345abcdef/events",
        "bot_events": ["app_uninstalled", "message.im", "tokens_revoked"],
    }
    assert manifest["settings"]["interactivity"] == {
        "is_enabled": True,
        "request_url": "https://cairn.example/integrations/slack/sl_012345abcdef/actions",
    }
    assert json.loads(json.dumps(manifest)) == manifest


def test_manifest_accepts_custom_name_command_and_deployment_prefix():
    module = importlib.import_module("cairn.slack.manifest")
    manifest = module.build_manifest(
        public_url="https://cairn.example:8443/notes/",
        install="sl_abcdef012345",
        command="/team-notes_2",
        name="Team notes",
    )
    assert manifest["display_information"]["name"] == "Team notes"
    assert manifest["features"]["bot_user"]["display_name"] == "Team notes"
    command = manifest["features"]["slash_commands"][0]
    assert command["command"] == "/team-notes_2"
    assert command["url"] == (
        "https://cairn.example:8443/notes/integrations/slack/sl_abcdef012345/commands"
    )


@pytest.mark.parametrize(
    "public_url",
    [
        "",
        "http://cairn.example",
        "//cairn.example",
        "https://",
        "https:///missing",
        "https://user:pass@cairn.example",
        "https://cairn.example?x=1",
        "https://cairn.example#fragment",
        "https://cairn.example:",
        "https://cairn.example:bad",
        "https://cairn.example:65536",
        " https://cairn.example",
        "https://cairn.example/\n",
        "https://cairn.example/a b",
        "https://cairn.example\\@evil.example",
        "https://[malformed",
    ],
)
def test_manifest_rejects_unusable_callback_base_urls(public_url):
    module = importlib.import_module("cairn.slack.manifest")
    with pytest.raises(ValueError, match="public_url"):
        module.build_manifest(public_url=public_url, install="sl_012345abcdef")


@pytest.mark.parametrize(
    "install",
    [
        "",
        "sl_012345abcde",
        "sl_012345abcdef0",
        "sl_012345abcdeg",
        "sl_012345ABCDEf",
        "012345abcdef",
        "sl_012345abcdef/events",
        "sl_012345abcdef\n",
    ],
)
def test_manifest_rejects_install_ids_that_cannot_select_one_receiver(install):
    module = importlib.import_module("cairn.slack.manifest")
    with pytest.raises(ValueError, match="install"):
        module.build_manifest(public_url="https://cairn.example", install=install)


@pytest.mark.parametrize(
    "command",
    [
        "",
        "/",
        "cairn",
        "//cairn",
        "/two words",
        "/notes?",
        "/notes/extra",
        "/cairn\n",
        "/" + "a" * 32,
    ],
)
def test_manifest_rejects_malformed_slash_commands(command):
    with pytest.raises(ValueError, match="command"):
        build(command=command)


def test_manifest_accepts_maximum_command_length():
    manifest = build(command="/" + "a" * 31)
    assert manifest["features"]["slash_commands"][0]["command"] == "/" + "a" * 31


def test_manifest_link_round_trips_complete_json_in_one_documented_parameter():
    module = importlib.import_module("cairn.slack.manifest")
    manifest = build(name='Notes & "ideas" + café / <test>')
    link = module.manifest_link(manifest)
    parts = urlsplit(link)
    assert (parts.scheme, parts.netloc, parts.path, parts.fragment) == (
        "https",
        "api.slack.com",
        "/apps",
        "",
    )
    parameters = parse_qs(parts.query, strict_parsing=True)
    assert set(parameters) == {"new_app", "manifest_json"}
    assert parameters["new_app"] == ["1"]
    assert len(parameters["manifest_json"]) == 1
    assert json.loads(parameters["manifest_json"][0]) == manifest
    assert "%7B" in parts.query
    assert "{" not in link
    assert " " not in link
    assert module.manifest_link(manifest) == link


def test_manifest_opens_the_messages_tab_for_deliberate_direct_messages():
    manifest = build()
    assert manifest["features"]["app_home"] == {
        "messages_tab_enabled": True,
        "messages_tab_read_only_enabled": False,
    }
