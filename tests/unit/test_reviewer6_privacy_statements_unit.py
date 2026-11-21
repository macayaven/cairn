"""Check the privacy statements where project members and release readers see them."""

import re
from contextlib import closing
from pathlib import Path

import pytest
from markdown_it import MarkdownIt
from starlette.testclient import TestClient
from test_design_system_unit import tree

from cairn.server import create_app
from cairn.store import Store
from cookie_names import session_cookie

ROOT = Path(__file__).resolve().parents[2]
BASE = "https://cairn.example.test"


@pytest.mark.parametrize("role", ["manager", "read", "write"])
def test_never_explains_the_arranger_and_connected_assistants_to_every_role(role):
    with closing(Store(":memory:")) as store:
        sessions = [
            store.sign_in(
                "sso",
                person,
                f"{person}@example.test",
                person,
                issuer="https://id.example.test",
                join="default",
            )
            for person in ("Ada", "Sam")
        ]
        owner, member = sessions
        manager = store.for_account(owner.account_id, owner.user.id)
        project = manager.create_project("ops", everyone="read" if role == "manager" else role)
        manager.set_project_models(project.id, "never")
        with TestClient(create_app(store, public_url=BASE), base_url=BASE) as client:
            client.cookies.set(
                session_cookie("https"), (owner if role == "manager" else member).token
            )
            response = client.get("/b/ops")
        assert response.status_code == 200
        (models,) = tree(response.text).all(cls="models")
        words = " ".join(models.text().casefold().split())
        assert "models: never" in words
        # "Whatever ... the operator set. Pages already arranged stay as they are" does
        # not say which model the setting governs. Look for that relationship in one sentence.
        own_model = any(
            re.search(r"server|operator|cairn", s)
            and re.search(r"arrang|order|organi[sz]", s)
            and re.search(r"model|arranger", s)
            for s in re.split(r"[.!?]", words)
        )
        assistant = all(
            re.search(t, words)
            for t in (
                r"assistant|agent",
                r"connect|link",
                r"provider|compan(?:y|ies)|model host",
                r"send|share|receiv|pass|reach|read",
            )
        )
        missing = [
            label
            for label, present in (
                ("server's arranging model", own_model),
                ("connected assistants' providers", assistant),
            )
            if not present
        ]
        assert not missing, (role, missing, words)


def slack_removal():
    tokens = MarkdownIt("commonmark").parse((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    for token in tokens:
        if token.type != "inline" or "slack" not in token.content.casefold():
            continue
        words = " ".join(token.content.casefold().split())
        # A stale notification that was ignored has its own audit event.
        sentences = [
            s
            for s in re.split(r"(?<=[.!?])\s+", words)
            if not re.search(r"at or before|not acted on|stale|ignored|out.of.date", s)
        ]
        for i, sentence in enumerate(sentences):
            if re.search(r"\b(?:remov|uninstall)\w*", sentence) and re.search(
                r"\b(?:slack|app|installation|integration)\b", sentence
            ):
                return sentences[i:]
    pytest.fail("No Slack app removal description in the changelog")


LOG = r"\b(?:log|wal)\b"
EMPTY = r"\b(?:empt\w*|truncat\w*|clear\w*|compact\w*|checkpoint\w*)\b"
CONNECTION = r"\b(?:connections?|readers?|process(?:es)?|clients?|replica|busy|in use)\b"


def conditional_cleanup(words):
    return re.search(CONNECTION, words) and re.search(
        r"\b(?:no|not|nothing|without|unless|until|free|idle|last|clos\w*|releas\w*|prevent\w*|"
        r"block\w*|defer\w*|delay\w*|remain\w*|stay\w*|can keep|may keep)\b",
        words,
    )


def log_statements():
    statements = slack_removal()
    return [
        (s, " ".join(statements[max(0, i - 1) : i + 2]))
        for i, s in enumerate(statements)
        if re.search(LOG, s) and re.search(EMPTY, s)
    ]


def test_slack_removal_has_no_unconditional_log_promise():
    promises = [
        s
        for s, nearby in log_statements()
        if not conditional_cleanup(nearby)
        or (re.search(r"\b(?:as|like) forgetting\b", s) and not conditional_cleanup(s))
    ]
    assert not promises


def test_slack_removal_records_deletion_and_qualifies_log_cleanup():
    sentences = slack_removal()
    recorded = any(
        re.search(r"\b(?:record|mark|track|register|commit|flag|queue|schedule|remember)\w*", s)
        and re.search(
            r"\b(?:delet|remov|uninstall|eras)\w*", " ".join(sentences[max(0, i - 1) : i + 1])
        )
        for i, s in enumerate(sentences)
    )
    conditional = any(conditional_cleanup(nearby) for _, nearby in log_statements())
    assert recorded and conditional, (recorded, conditional, sentences)
