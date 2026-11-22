"""Unit tests for a project kept from every model in cairn.memory (M4): the projects'
representation, a new project's answer and the change, with the errors each door turns into a
status. The inner TDD loop under tests/acceptance/test_m4_a_project_no_model_reads.py.
Fixtures are synthetic."""

import pytest

from cairn.gate import lint_gate
from cairn.memory import BadRequest, Forbidden, Memory, NotFound
from cairn.store import Store

ISSUER = "https://id.example.test/pool-m4"


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "cairn.db")
    yield opened
    opened.close()


def as_person(store, name):
    signed = store.sign_in(
        "sso", f"sub-{name}", f"{name.lower()}@example.test", name, issuer=ISSUER, join="default"
    )
    return Memory(store.for_account(signed.account_id, signed.user.id), lint_gate)


def test_each_project_says_whether_a_model_may_be_sent_its_pages(store):
    memory = Memory(store, lint_gate)
    memory.new_project("plans")
    made = memory.new_project("diary", kind="personal")
    assert made["models"] == "never"
    assert memory.new_project("ledger")["models"] == "allowed"
    listed = {p["name"]: p["models"] for p in memory.projects()["projects"]}
    assert listed == {"diary": "never", "ledger": "allowed", "plans": "allowed"}
    first = memory.projects()["projects"][0]
    assert list(first)[-1] == "models"  # beside today's fields, after them


def test_set_models_answers_with_the_project_and_its_state(store):
    memory = Memory(store, lint_gate)
    memory.new_project("plans")
    assert memory.set_models("plans", "never") == {"project": "plans", "models": "never"}
    assert memory.set_models("plans", "never") == {"project": "plans", "models": "never"}
    assert memory.set_models("plans", "allowed") == {"project": "plans", "models": "allowed"}


@pytest.mark.parametrize("value", ["", "sometimes", None, 1, True])
def test_set_models_refuses_another_value_naming_the_two(store, value):
    memory = Memory(store, lint_gate)
    memory.new_project("plans")
    with pytest.raises(BadRequest) as refused:
        memory.set_models("plans", value)
    assert "'allowed'" in str(refused.value) and "'never'" in str(refused.value)
    assert "sometimes" not in str(refused.value)  # what was given is never echoed


def test_set_models_says_who_may_and_which_project_is_unknown(store):
    ada = as_person(store, "Ada")
    cy = as_person(store, "Cy")
    ada.new_project("handbook")
    with pytest.raises(Forbidden) as refused:
        cy.set_models("handbook", "never")
    assert "maker" in str(refused.value) and "owners" in str(refused.value)
    with pytest.raises(NotFound):
        ada.set_models("nowhere", "never")
    limited = Memory(store.narrow(None, "read"), lint_gate)
    with pytest.raises(Forbidden) as read_only:
        limited.set_models("handbook", "never")
    assert "read-only" in str(read_only.value)
