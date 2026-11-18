"""The last step of a link, in Slack (cairn.slack.commands `link`, cairn.slack.people and
blocks.link_prompt, review finding B2): `/cairn link <code>` from the Slack user whose offer
it is links them to the person who said yes in the browser; anything else links nothing and
is answered with one fixed sentence. Every name is made up; every call to Slack goes to an
httpx.MockTransport; every code is chosen by the test through the store's seam."""

import asyncio
import logging
import re

import pytest
from test_slack_commands_unit import ADA_SLACK, BASE, BROOK_SLACK, Bench, ask, said
from test_slack_messages_unit import handle

import cairn.store.slack as store_slack
from cairn.slack import blocks, commands
from cairn.store.slack import OFFER_TTL, TRIES

CODE = "KM7Q-R2XZ"
ADDRESS = re.compile(re.escape(BASE) + r"/integrations/slack/link\?t=([A-Za-z0-9_-]+)")


@pytest.fixture
def bench(tmp_path, monkeypatch):
    monkeypatch.setattr(store_slack, "_new_code", lambda: CODE.replace("-", ""))
    made = Bench(tmp_path)
    yield made
    made.store.close()


def offers(bench: Bench) -> list[tuple]:
    return bench.store._con.execute(
        "SELECT slack_user_id, user_id, code_digest IS NOT NULL, tries FROM slack_link_offers"
        " ORDER BY slack_user_id"
    ).fetchall()


def yes_from_brook(bench: Bench, slack_user: str = ADA_SLACK) -> str:
    """The Slack user asks for the address with `/cairn link`; Brook opens it and says yes.
    The code Brook was shown."""
    (value,) = set(ADDRESS.findall(said(ask(bench, "link", user=slack_user))))
    return bench.store.for_account(bench.account, bench.brook).claim_slack_offer(value)


def no_link(bench: Bench) -> str:
    return said(blocks.refused(commands.NO_LINK.format(command="/cairn")))


def linked_to(bench: Bench, slack_user: str):
    found = bench.owner.slack_person(bench.install.id, slack_user)
    return found.user_id if found else None


def test_the_code_from_its_slack_user_in_time_links_them_and_says_to_whom(bench):
    code = yes_from_brook(bench)
    bench.clock.now += OFFER_TTL / 2
    answer = said(ask(bench, f"link {code.lower().replace('-', ' ')}"))
    assert answer == said(blocks.linked("Brook"))
    assert linked_to(bench, ADA_SLACK) == bench.brook
    assert offers(bench) == []


def test_a_wrong_code_links_nothing_counts_a_try_and_the_fifth_deletes_the_offer(bench):
    yes_from_brook(bench)
    for tried in range(1, TRIES):
        assert said(ask(bench, "link AAAA-AAAA")) == no_link(bench)
        assert offers(bench) == [(ADA_SLACK, bench.brook, 1, tried)]
    assert said(ask(bench, "link AAAA-AAAA")) == no_link(bench)
    assert offers(bench) == []
    assert said(ask(bench, f"link {CODE}")) == no_link(bench)
    assert linked_to(bench, ADA_SLACK) is None


def test_the_one_sentence_says_neither_wrong_nor_ran_out_and_how_to_start_again(bench):
    sentence = commands.NO_LINK.format(command="/notes")
    assert "`/notes link`" in sentence and "start again" in sentence
    assert "wrong" not in sentence.lower() and "ran out" not in sentence.lower()
    assert "expired" not in sentence.lower()


def test_a_code_run_out_not_yet_given_or_malformed_gets_the_same_sentence(bench):
    assert said(ask(bench, f"link {CODE}")) == no_link(bench)  # no offer at all
    said(ask(bench, "link"))  # an offer nobody said yes to
    assert said(ask(bench, f"link {CODE}")) == no_link(bench)
    yes_from_brook(bench)
    assert said(ask(bench, "link not-a-code")) == no_link(bench)
    bench.clock.now += OFFER_TTL
    assert said(ask(bench, f"link {CODE}")) == no_link(bench)
    assert linked_to(bench, ADA_SLACK) is None


def test_a_code_typed_by_another_slack_user_links_nothing_and_is_dead(bench):
    code = yes_from_brook(bench)
    said(ask(bench, "link", user=BROOK_SLACK))  # an offer of Brook's Slack user's own
    assert said(ask(bench, f"link {code}", user=BROOK_SLACK)) == no_link(bench)
    assert linked_to(bench, BROOK_SLACK) is None
    assert [row[0] for row in offers(bench)] == [BROOK_SLACK]
    assert said(ask(bench, f"link {code}")) == no_link(bench)
    assert linked_to(bench, ADA_SLACK) is None


def test_when_the_person_who_said_yes_is_no_longer_a_member_nothing_is_linked(bench):
    code = yes_from_brook(bench)
    bench.owner.remove_member(bench.brook)
    assert said(ask(bench, f"link {code}")) == no_link(bench)
    assert linked_to(bench, ADA_SLACK) is None and offers(bench) == []


def test_link_with_no_code_from_a_linked_user_says_who_and_offers_nothing(bench):
    bench.link(ADA_SLACK, bench.ada)
    assert said(ask(bench, "link")) == said(blocks.linked("Ada"))
    assert offers(bench) == []


def test_link_with_no_code_replaces_an_earlier_offer_and_its_code(bench):
    yes_from_brook(bench)
    answer = said(ask(bench, "link"))
    assert ADDRESS.search(answer)
    assert offers(bench) == [(ADA_SLACK, None, 0, 0)]
    assert said(ask(bench, f"link {CODE}")) == no_link(bench)


def test_link_with_a_code_from_a_linked_user_says_who_and_changes_nothing(bench):
    code = yes_from_brook(bench, slack_user=BROOK_SLACK)
    bench.link(ADA_SLACK, bench.ada)
    before = bench.store._con.execute("SELECT * FROM slack_link_offers").fetchall()
    assert said(ask(bench, f"link {code}")) == said(blocks.linked("Ada"))
    assert bench.store._con.execute("SELECT * FROM slack_link_offers").fetchall() == before
    assert linked_to(bench, ADA_SLACK) == bench.ada


def test_the_prompt_says_both_steps_in_plain_words(bench):
    text = said(ask(bench, "find jars"))
    for words in (
        "Open the address below, signed in",
        "It shows you a code",
        "type `/cairn link` and the code here",
        "within 10 minutes",
        "The address is yours alone",
    ):
        assert words in text, words


def test_the_prompt_names_the_installs_own_command():
    text = said(blocks.link_prompt("https://notes.example.test/x", "/notes"))
    assert "`/notes link`" in text and "/cairn" not in text


@pytest.mark.parametrize("text", [CODE, f"link {CODE}", "note: a code", "jars"])
def test_a_direct_message_from_an_unlinked_user_is_never_stored_and_gets_the_prompt(bench, text):
    yes_from_brook(bench)
    response = handle(bench, text)
    asyncio.run(response.background())
    (_, posted) = bench.calls[-1]
    assert ADDRESS.search(said(posted))
    assert bench.notes() == []
    assert linked_to(bench, ADA_SLACK) is None


def test_neither_the_value_nor_the_code_is_in_a_log_line(bench, caplog):
    caplog.set_level(logging.DEBUG)
    (value,) = set(ADDRESS.findall(said(ask(bench, "link"))))
    code = bench.store.for_account(bench.account, bench.brook).claim_slack_offer(value)
    said(ask(bench, "link AAAA-AAAA"))
    said(ask(bench, f"link {code}", user=BROOK_SLACK))
    said(ask(bench, f"link {code}"))
    assert caplog.records
    for record in caplog.records:
        text = record.getMessage() + repr(record.__dict__)
        for secret in (value, code, code.replace("-", "")):
            assert secret not in text
