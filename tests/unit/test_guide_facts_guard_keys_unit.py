"""Sentences of the security guide about the note guard's new shapes and the keys' prefix
(review, finding 2), each pinned here so the guide cannot go false unnoticed: each test
finds its sentences in the guide as a person reads it (guide_source), then asks cairn."""

import secrets

from guide_source import guide_source

from cairn.guard import PatternGuard
from cairn.store import KEY_PREFIX, Store, credentials


def flat(text: str) -> str:
    return " ".join(text.split())


def says(guide: str, *sentences: str) -> None:
    text = flat(guide_source(guide))
    for sentence in sentences:
        assert flat(sentence) in text, sentence


def refused(text: str, policy: str = "secrets") -> list[str]:
    return [f.rule for f in PatternGuard(policy).check(text)]


def test_the_default_guard_refuses_what_the_guide_lists():
    says(
        "security",
        "- one of cairn's own agent tokens, or one of its API keys made with the prefix"
        " (a key made before has none, and is caught only as a value assigned to a name such"
        " as `api_key`);",
        "- a password in a URL, such as `postgres://user:password@host`;",
        "- an `Authorization: Bearer` or `Authorization: Basic` header with its value;",
        "- a value of 12 characters or more assigned to a name such as `password`, `secret`,"
        " `token` or `api_key`; after `password` or `passphrase`, a quoted value counts"
        " whatever it holds, words and spaces included.",
    )
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    assert refused(key) == ["cairn_api_key"]
    assert refused("postgres://ada" + ":Fable2026works@10.0.0.4/notes") == ["credential_url"]
    assert refused("Authorization: Bearer " + "Zx9Qw3Er7Ty1Ui5O") == ["auth_header"]
    assert refused('password = "the quiet fable ' + 'works at dawn"') == ["assigned_secret"]
    assert refused("Authorization: Bearer <token>") == []
    assert refused("postgres://ada:<password>@10.0.0.4/notes") == []


def test_the_stricter_policy_refuses_what_the_guide_lists_and_no_more():
    says(
        "security",
        "A workspace's policy can also refuse personal data (an email address, a phone number"
        " written with `+` or in the North American way, such as `(415) 555-0199`, a US Social"
        " Security number, a payment card number, an IBAN, and a date of birth or a medical"
        " record number written after its label, such as `DOB:` or `MRN`), or check nothing.",
        "The guard matches patterns. It does not recognise a name, a postal address,"
        " free-text health details, a date of birth or a record number without its label,"
        " keys of other providers, or a secret that was split, encoded or reworded.",
    )
    personal = "secrets+personal"
    assert refused("Ring (415) 555-0199", personal) == ["phone_number"]
    assert refused("DOB: 2015-03-12", personal) == ["date_of_birth"]
    assert refused("MRN 00482913", personal) == ["medical_record_number"]
    assert refused("Born 2015-03-12, record 00482913", personal) == []
    assert refused("Quill has asthma.", personal) == []


def test_a_key_has_a_fixed_prefix_and_one_made_before_keeps_working(tmp_path, monkeypatch):
    says(
        "security",
        "A key made now starts with a fixed prefix, so the note guard and a secret scanner can"
        " recognise one; a key made before has none and keeps working as it did.",
    )
    store = Store(tmp_path / "cairn.db")
    try:
        new = store.create_key("new")
        with monkeypatch.context() as patched:
            patched.setattr(credentials, "KEY_PREFIX", "")
            old = store.create_key("old")
        assert new.startswith(KEY_PREFIX) and not old.startswith(KEY_PREFIX)
        assert store.check_key(new) and store.check_key(old)
    finally:
        store.close()
