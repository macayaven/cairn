"""What Slack is told names the product as the app names it (dispatch.Context.product, the
app's product name; blocks.PRODUCT when the app names none): the sentences of
cairn.slack.people and the blocks that name the product. `--slack` under a brand is still
refused, so a running server names the software; the default sentences are today's."""

import asyncio
import dataclasses

import pytest
from test_slack_commands_unit import ADA_SLACK, Bench, ask, said
from test_slack_shortcuts_unit import CEDAR, cedar, said_reason

from cairn.slack import blocks, people
from cairn.slack.dispatch import Context

NAME = "Fable Notes"


@pytest.fixture
def bench(tmp_path):
    made = Bench(tmp_path)
    made.users[CEDAR] = cedar()
    yield made
    made.store.close()


def named(bench: Bench) -> Bench:
    bench.context = dataclasses.replace(bench.context, product=NAME)
    return bench


def test_the_context_names_the_software_unless_the_app_says():
    assert Context.__dataclass_fields__["product"].default == blocks.PRODUCT == "cairn"


@pytest.mark.parametrize(
    "sentence",
    ["BUSY", "STORED_NOT_FILED", "NOT_A_NOTE", "NOT_A_MESSAGE", "OTHER_ORGANISATION",
     "RATE_LIMITED", "NO_TOKEN"],
)  # fmt: skip
def test_each_sentence_that_names_the_product_is_built_from_its_name(sentence):
    default = getattr(people, sentence)
    assert "cairn" in default
    built = people.with_product(default, NAME)
    assert NAME in built and "cairn" not in built
    assert people.with_product(default, blocks.PRODUCT) == default


def test_a_message_that_cannot_be_filed_names_the_app_s_product(bench):
    named(bench).link(ADA_SLACK, bench.ada, bench.library)
    message = {"type": "message", "user": CEDAR, "text": "Row 4."}  # no time, no conversation
    reason = said_reason(bench, message)
    assert reason == people.with_product(people.NOT_A_MESSAGE, NAME)
    assert NAME in reason and "cairn" not in reason


def test_no_bot_token_names_the_app_s_product(bench, monkeypatch):
    named(bench)
    monkeypatch.setattr(Context, "bot_token", lambda self: None)  # the install holds none
    people.AUTHORS.clear()
    message = {"type": "message", "user": CEDAR, "ts": "1900000000.000100", "text": "Row 4."}
    with pytest.raises(people.NotFiled) as refused:
        asyncio.run(people.author(bench.context, message))
    assert str(refused.value) == people.with_product(people.NO_TOKEN, NAME)


def test_help_names_the_app_s_product(bench):
    told = said(ask(named(bench), "help"))
    assert NAME in told
