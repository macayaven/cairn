"""What the Slack switch holds in clear never shows in a repr (as test_secret_repr_unit has it
for the store's models): the key the installs' secrets are sealed with, an install's signing
secret opened for a request, what a person wrote in a request, and the store a handler is
handed. Every value here is made up."""

import dataclasses
import inspect

from cairn.slack import dispatch, verify
from cairn.store import slack as store_slack
from cairn.store.slack import SlackInbound, SlackInstall
from cairn.web import slack as web_slack
from cairn.web.slack import Slack

KEY = bytes(range(32))
INSTALL = SlackInstall(
    id="sl_0123456789ab", account_id="acct_0123456789ab", state="on", team_id="T0FABLE01",
    enterprise_id=None, team_domain="fable", bot_user_id="U0BOT0001", command="/cairn",
    made_by=None, made_at="2031-03-04T09:00:00.000000+00:00", removed_at=None,
    holds_secrets=True, holds_bot_token=True,
)  # fmt: skip
SECRET_NAMES = (
    "key",
    "signing_secret",
    "sealed",
    "bot_token",
    "token",
    "secret",
    "payload",
    "view",
)


def test_the_switch_does_not_print_its_key():
    shown = repr(Slack(key=KEY)) + str(Slack(key=KEY))
    assert KEY.hex() not in shown and repr(KEY) not in shown


def test_an_inbound_install_does_not_print_its_signing_secret():
    inbound = SlackInbound(INSTALL, "fable-signing-0123456789abcdef")
    shown = repr(inbound) + str(inbound)
    assert "fable-signing-0123456789abcdef" not in shown
    assert INSTALL.id in shown  # what is not secret still shows
    assert inbound.signing_secret == "fable-signing-0123456789abcdef"


def test_an_envelope_does_not_print_what_was_written():
    found = verify.Envelope(
        "commands", "command", "find", INSTALL.id, INSTALL.account_id, "T0FABLE01",
        "U0ADA0001", {"text": "find marmalade recipes"},
    )  # fmt: skip
    assert "marmalade" not in repr(found) and found.payload["text"].endswith("recipes")


def test_no_slack_dataclass_shows_a_field_that_holds_a_secret():
    shown = []
    for module in (store_slack, web_slack, verify, dispatch):
        for name, kind in inspect.getmembers(module, inspect.isclass):
            if kind.__module__ != module.__name__ or not dataclasses.is_dataclass(kind):
                continue
            for field in dataclasses.fields(kind):
                if field.name in SECRET_NAMES and field.repr:
                    shown.append(f"{module.__name__}.{name}.{field.name}")
    assert shown == [], shown
