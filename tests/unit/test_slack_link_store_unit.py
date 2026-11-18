"""Linking a Slack user to a person in two steps (cairn.store.slack): the
offer the Slack user asks for, the signed-in person's yes that ties it to them and gives a
code, and the code typed back in Slack by the same Slack user. Every name, id and value here
is made up; every code is chosen by the test through the store's seam (`_new_code`)."""

import sqlite3
from datetime import timedelta

import pytest
from test_slack_store_unit import ISSUER, ready, selects, world  # noqa: F401
from test_store_unit import rows, statements

import cairn.store.slack as slack
from cairn.store.slack import CODE_ALPHABET, OFFER_TTL, TRIES

BOB, CAROL = "U0BOB0001", "U0CAROL01"
CODE = "KM7Q-R2XZ"  # what the person is shown
# Every statement take_slack_offer runs, the transaction's own included: a wrong code, the right one
TAKE_WRONG, TAKE_RIGHT = 10, 15


@pytest.fixture
def code(monkeypatch):
    """Every code the store makes is CODE."""
    monkeypatch.setattr(slack, "_new_code", lambda: CODE.replace("-", ""))
    return CODE


def offers(w) -> list[tuple]:
    return rows(
        w.db,
        "SELECT slack_user_id, user_id, code_digest IS NOT NULL, tries FROM slack_link_offers"
        " ORDER BY slack_user_id",
    )


def system(w):
    """The workspace's view acting for no person, as the slash command has it."""
    return w.store.for_account(w.account)


# The code


def test_a_code_is_8_characters_of_the_alphabet_from_secrets_shown_in_two_fours(monkeypatch):
    asked: list[str] = []

    def choice(alphabet):
        asked.append(alphabet)
        return alphabet[len(asked) - 1]

    monkeypatch.setattr(slack.secrets, "choice", choice)
    made = slack._new_code()
    assert asked == [CODE_ALPHABET] * 8
    assert CODE_ALPHABET == "ABCDEFGHJKMNPQRSTVWXYZ23456789"
    assert made == "ABCDEFGH" and slack._shown(made) == "ABCD-EFGH"


@pytest.mark.parametrize(
    "typed", ["KM7Q-R2XZ", "km7q-r2xz", "KM7QR2XZ", " km7q r2xz ", "Km7q - R2xZ"]
)
def test_what_a_person_types_is_read_without_regard_to_case_hyphens_or_spaces(typed):
    assert slack._typed(typed) == "KM7QR2XZ"


@pytest.mark.parametrize("typed", ["", "KM7Q-R2X", "KM7Q-R2XZZ", "KM7Q-R2X0", "KM7Q_R2XZ", None])
def test_what_is_not_a_code_is_read_as_none(typed):
    assert slack._typed(typed) is None


# The offer


def test_an_offer_replaces_that_slack_users_earlier_one_claimed_or_not(world, code):  # noqa: F811
    install = ready(world)
    first = world.view.offer_slack_link(install.id, BOB)
    second = world.view.offer_slack_link(install.id, BOB)
    assert world.member_view.slack_offer(first) is None
    world.member_view.claim_slack_offer(second)
    third = world.view.offer_slack_link(install.id, BOB)
    world.view.offer_slack_link(install.id, CAROL)
    assert offers(world) == [(BOB, None, 0, 0), (CAROL, None, 0, 0)]
    assert world.member_view.slack_offer(third).slack_user_id == BOB
    with pytest.raises(KeyError):  # the claimed one and its code are gone
        system(world).take_slack_offer(install.id, BOB, code)


def test_one_offer_for_a_slack_user_at_a_time_is_the_tables_own_rule(world):  # noqa: F811
    install = ready(world)
    world.view.offer_slack_link(install.id, BOB)
    con = sqlite3.connect(world.db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO slack_link_offers (digest, install_id, slack_user_id, expires_at)"
                " VALUES ('d', ?, ?, 'x')",
                (install.id, BOB),
            )
    finally:
        con.close()


# The yes


def test_the_yes_ties_the_offer_to_the_person_and_returns_the_code_kept_as_a_digest(
    world,  # noqa: F811
    code,
):
    install = ready(world)
    value = world.view.offer_slack_link(install.id, BOB)
    assert world.member_view.claim_slack_offer(value) == code
    assert offers(world) == [(BOB, world.member, 1, 0)]
    dump = "\n".join(statements(world.db))
    assert code not in dump and code.replace("-", "") not in dump and value not in dump
    assert world.member_view.slack_offer(value) is None  # nobody said yes to it yet: no more
    assert world.view.slack_person(install.id, BOB) is None  # linked to nobody yet
    with pytest.raises(KeyError):  # someone already said yes to it
        world.view.claim_slack_offer(value)
    assert offers(world) == [(BOB, world.member, 1, 0)]


def test_a_yes_to_an_offer_run_out_unknown_or_of_another_workspace_is_a_key_error(
    world,  # noqa: F811
):
    install = ready(world)
    value = world.view.offer_slack_link(install.id, BOB)
    stranger = world.store.sign_in(
        "sso", "sub-cy", "cy@example.test", "Cy", issuer=ISSUER, join="personal"
    )
    theirs = world.store.for_account(stranger.account_id, stranger.user.id)
    for view, given in ((theirs, value), (world.member_view, "nothing"), (world.member_view, "")):
        with pytest.raises(KeyError):
            view.claim_slack_offer(given)
    world.clock.now += OFFER_TTL
    with pytest.raises(KeyError):
        world.member_view.claim_slack_offer(value)
    assert offers(world) == [(BOB, None, 0, 0)]


def test_a_yes_from_a_view_for_no_person_or_a_person_no_longer_a_member_is_refused(
    world,  # noqa: F811
):
    install = ready(world)
    value = world.view.offer_slack_link(install.id, BOB)
    with pytest.raises(PermissionError):
        system(world).claim_slack_offer(value)
    world.view.remove_member(world.member)
    with pytest.raises(PermissionError):
        world.member_view.claim_slack_offer(value)
    assert offers(world) == [(BOB, None, 0, 0)]


# The code typed in Slack


def claimed(w, code_, slack_user=BOB, view=None) -> str:
    install = ready(w) if not w.view.get_slack_install() else w.view.get_slack_install()
    value = w.view.offer_slack_link(install.id, slack_user)
    (view or w.member_view).claim_slack_offer(value)
    return install.id


@pytest.mark.parametrize("typed", [CODE, CODE.lower(), CODE.replace("-", " ")])
def test_the_code_from_its_slack_user_in_time_links_them_to_who_said_yes(world, code, typed):  # noqa: F811
    install = claimed(world, code)
    world.clock.now += OFFER_TTL - timedelta(seconds=1)
    person = system(world).take_slack_offer(install, BOB, typed)
    assert (person.slack_user_id, person.user_id) == (BOB, world.member)
    assert world.view.slack_person(install, BOB).user_id == world.member
    assert offers(world) == []


def test_a_wrong_code_counts_a_try_and_the_fifth_deletes_the_offer(world, code):  # noqa: F811
    install = claimed(world, code)
    assert TRIES == 5
    for tried in range(1, TRIES):
        with pytest.raises(KeyError):
            system(world).take_slack_offer(install, BOB, "AAAA-AAAA")
        assert offers(world) == [(BOB, world.member, 1, tried)]  # kept although it raised
    with pytest.raises(KeyError):
        system(world).take_slack_offer(install, BOB, "not a code at all")
    assert offers(world) == []
    with pytest.raises(KeyError):
        system(world).take_slack_offer(install, BOB, code)
    assert world.view.slack_person(install, BOB) is None


def test_the_code_after_ten_minutes_or_before_a_yes_links_nothing(world, code):  # noqa: F811
    install = claimed(world, code)
    world.clock.now += OFFER_TTL
    with pytest.raises(KeyError):
        system(world).take_slack_offer(install, BOB, code)
    world.clock.now -= OFFER_TTL
    world.view.offer_slack_link(install, CAROL)  # nobody said yes to it
    with pytest.raises(KeyError):
        system(world).take_slack_offer(install, CAROL, code)
    assert world.view.slack_person(install, BOB) is None
    assert world.view.slack_person(install, CAROL) is None


def test_a_code_typed_by_another_slack_user_links_nothing_and_kills_the_code(world, code):  # noqa: F811
    install = claimed(world, code)
    world.view.offer_slack_link(install, CAROL)  # an offer of Carol's own, unclaimed
    with pytest.raises(KeyError):
        system(world).take_slack_offer(install, CAROL, code)
    assert world.view.slack_person(install, CAROL) is None
    assert [row[0] for row in offers(world)] == [CAROL]  # Bob's offer, whose code it was, gone
    with pytest.raises(KeyError):
        system(world).take_slack_offer(install, BOB, code)
    assert world.view.slack_person(install, BOB) is None


def test_a_code_typed_by_another_slack_user_holding_a_claimed_offer_links_nothing(
    world,  # noqa: F811
    monkeypatch,
):
    made = iter(["KM7QR2XZ", "PQRSTVWX"])
    monkeypatch.setattr(slack, "_new_code", lambda: next(made))
    install = claimed(world, None)
    claimed(world, None, slack_user=CAROL, view=world.view)
    with pytest.raises(KeyError):
        system(world).take_slack_offer(install, CAROL, "KM7Q-R2XZ")
    assert offers(world) == [(CAROL, world.owner, 1, 1)]
    assert world.view.slack_person(install, CAROL) is None


def test_when_the_person_who_said_yes_is_no_longer_a_member_nothing_is_linked(world, code):  # noqa: F811
    install = claimed(world, code)
    world.view.remove_member(world.member)
    with pytest.raises(KeyError):
        system(world).take_slack_offer(install, BOB, code)
    assert world.view.slack_person(install, BOB) is None
    assert offers(world) == []


def test_removing_a_person_deletes_the_offers_they_said_yes_to(world, code):  # noqa: F811
    """A removed person's Slack rows go with them, the offer they said yes to among them."""
    claimed(world, code)
    assert offers(world) != []
    world.view.remove_member(world.member)
    assert offers(world) == []


def test_no_exception_names_the_code_or_the_value(world, code):  # noqa: F811
    install = ready(world)
    value = world.view.offer_slack_link(install.id, BOB)
    world.member_view.claim_slack_offer(value)
    said: list[str] = []
    for call in (
        lambda: world.member_view.claim_slack_offer(value),
        lambda: system(world).take_slack_offer(install.id, BOB, "AAAA-AAAA"),
        lambda: system(world).take_slack_offer(install.id, CAROL, code),
    ):
        with pytest.raises(KeyError) as raised:
            call()
        said.append(str(raised.value) + repr(raised.value))
    for text in said:
        assert value not in text and code not in text and code.replace("-", "") not in text
    offer = world.view.offer_slack_link(install.id, BOB)
    assert offer not in repr(world.member_view.slack_offer(offer))


def test_taking_a_code_costs_the_same_statements_whatever_the_workspace_holds(world, code):  # noqa: F811
    install = ready(world)

    def counted(act) -> int:
        asked: list[str] = []
        world.store._con.set_trace_callback(asked.append)
        try:
            act()
        except KeyError:
            pass
        finally:
            world.store._con.set_trace_callback(None)
        return len(asked)

    costs = []
    for others in (1, 40):
        for n in range(others):
            world.view.offer_slack_link(install.id, f"U0MANY{len(costs)}{n:03d}")
        world.view.offer_slack_link(install.id, BOB)
        value = world.view.offer_slack_link(install.id, BOB)
        world.member_view.claim_slack_offer(value)
        wrong = counted(lambda: system(world).take_slack_offer(install.id, BOB, "AAAA-AAAA"))
        right = counted(lambda: system(world).take_slack_offer(install.id, BOB, code))
        world.view.unlink_slack_user(install.id, BOB)
        costs.append((wrong, right))
    assert costs == [(TAKE_WRONG, TAKE_RIGHT)] * 2


# Links


def test_a_link_is_never_moved_to_another_person(world):  # noqa: F811
    install = ready(world)
    first = world.view.link_slack_user(install.id, BOB, world.member)
    world.view.set_slack_project(install.id, BOB, world.project.id)
    again = world.view.link_slack_user(install.id, BOB, world.member)
    assert again == world.view.slack_person(install.id, BOB)
    assert (again.linked_at, again.project_id) == (first.linked_at, world.project.id)
    with pytest.raises(ValueError):
        world.view.link_slack_user(install.id, BOB, world.owner)
    assert world.view.slack_person(install.id, BOB).user_id == world.member


def test_a_slack_user_linked_by_any_path_has_no_offer_left_at_that_install(world):  # noqa: F811
    install = ready(world)
    world.view.offer_slack_link(install.id, BOB)
    world.view.offer_slack_link(install.id, CAROL)
    world.view.link_slack_user(install.id, BOB, world.member)
    assert [row[0] for row in offers(world)] == [CAROL]
