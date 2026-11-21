"""Read the project controls as a manager and the download advice as an operator."""

import re
from contextlib import closing

import pytest
from starlette.testclient import TestClient
from test_design_system_unit import tree
from test_reviewer6_privacy_statements_unit import BASE, ROOT

from cairn.server import create_app
from cairn.store import Store
from cookie_names import session_cookie


@pytest.mark.parametrize(
    "setting", [None, "allowed", "never"], ids=["undecided", "allowed", "never"]
)
def test_manager_sees_the_scope_of_the_model_setting(setting):
    with closing(Store(":memory:")) as store:
        owner = store.sign_in(
            "sso",
            "Ada",
            "ada@example.test",
            "Ada",
            issuer="https://id.example.test",
            join="personal",
        )
        manager = store.for_account(owner.account_id, owner.user.id)
        project = manager.create_project("ops")
        if setting:
            manager.set_project_models(project.id, setting)
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            client.cookies.set(session_cookie("https"), owner.token)
            response = client.get("/b/ops")
    assert response.status_code == 200
    page = tree(response.text)
    if setting != "never":
        (button,) = [
            b
            for b in page.all("button")
            if b.attrs.get("name") == "models" and b.attrs.get("value") == "never"
        ]
        assert " ".join(button.text().split()) == "Keep from the arranging model"
    assert "every model" not in " ".join(page.text().casefold().split())


def test_download_failure_alone_is_no_reason_to_delete_a_draft():
    rows = [
        [cell.strip().casefold() for cell in line.strip("|").split("|")]
        for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("|")
    ]
    (advice,) = [
        cells[-1]
        for cells in rows
        if "download" in cells[0] and re.search(r"fail|could not|unread", cells[0])
    ]
    # Only a recommendation to delete needs a separate reason to do so.
    deletion = r"\b(?:delet|remov)\w*"
    negative = r"never|do(?:es)? not|don't|no (?:reason|grounds)|not a reason"
    prohibition = rf"(?:delete|remove) nothing|(?:{negative})[^,;:.]*{deletion}"
    sentences = re.split(r"(?<=[.;])\s+", advice)
    for index, sentence in enumerate(sentences):
        if not re.search(deletion, sentence):
            continue
        if re.search(prohibition, sentence) and not re.search(
            r"\b(?:unless|except|but)\b", sentence
        ):
            continue
        reason = sentence
        if index and re.search(r"\b(?:then|if so|in that case)\b", sentence):
            reason = sentences[index - 1] + " " + sentence
        evidence = r"\b(?:confirm\w*|verif\w*|independent\w*|shows?|see|find|found|if|when)\b"
        incomplete = r"\b(?:incomplete|unfinished|missing|absent|cut|truncated|partial)\b"
        assert any(
            re.search(evidence, clause)
            and re.search(incomplete, clause)
            and not re.search(r"\b(?:may|might|could) (?:hold|be|have)\b", clause)
            for clause in reason.split(":")
        ), sentence
