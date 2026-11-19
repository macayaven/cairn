"""Unit tests for the credentials and personal data people actually paste (review, finding
2): a password in a URL's authority, an Authorization header, a quoted passphrase in plain
words, cairn's own API keys, and under the stricter policy a North American phone number
without "+", a labelled date of birth and a labelled medical record number. Each input is
built at run time, so this file holds nothing that looks like a live credential, and each
is one no other rule catches, so a finding names the rule that was added for it."""

import secrets

import pytest

from cairn.guard import PatternGuard, policy_words, rule_words
from cairn.store import KEY_PREFIX

ALNUM = "Zx9Qw3Er7Ty1Ui5Op2As8Df4Gh6Jk0Lm"  # 32 characters, letters and digits


def rules(text: str, policy: str = "secrets") -> list[tuple[str, int]]:
    return [(f.rule, f.line) for f in PatternGuard(policy).check(text)]


def at_line_three(text: str, policy: str = "secrets") -> list[tuple[str, int]]:
    return rules(f"# Notes\n\n{text}\n", policy)


# 1. Under the default policy: the reviewer's six examples, made up again


@pytest.mark.parametrize(
    "rule, text",
    [
        # a connection string with a password in its authority (one that holds "@" too)
        ("credential_url", "postgres://quill" + ":S3cur3P@ssw0rd!@10.0.0.4:5432/fable"),
        ("credential_url", "mysql://ada" + ":plainpassword@db.fable.test/works"),
        ("credential_url", "amqp://brook" + ":" + ALNUM[:10] + "@queue.fable.test"),
        ("credential_url", "Clone https://ada" + ":" + ALNUM[:20] + "@git.fable.test/x.git"),
        ("credential_url", "redis://:" + "hunter2hunter2" + "@cache.fable.test:6379"),
        ("auth_header", "Authorization: Bearer " + ALNUM),
        ("auth_header", "curl -H 'Authorization: Basic " + "YWRhOmZhYmxld29ya3M=" + "'"),
        ("auth_header", '"authorization": "bearer ' + ALNUM[:16] + '"'),
        ("assigned_secret", 'password = "correct horse ' + 'battery staple"'),
        ("assigned_secret", "passphrase: 'the quiet fable " + "works at dawn'"),
        ("assigned_secret", 'db_password = "' + "allinlowercase" + '"'),
        ("assigned_secret", 'PASSWD="' + "ONLYCAPITALSHERE" + '"'),
    ],
)
def test_a_credential_people_paste_is_refused_by_the_default_policy(rule, text):
    assert at_line_three(text) == [(rule, 3)]
    # (the stricter policy may also see an email address in password@host.domain)
    assert (rule, 3) in at_line_three(text, "secrets+personal")


@pytest.mark.parametrize(
    "text",
    [
        # a URL with a user and no password
        "ssh://git@git.fable.test/works.git",
        "https://ada@fable.test/notes",
        "postgres://quill@10.0.0.4:5432/fable",
        # placeholders for the password
        "postgres://quill:<password>@10.0.0.4/fable",
        "postgres://quill:***@10.0.0.4/fable",
        "postgres://quill:${DB_PASSWORD}@10.0.0.4/fable",
        "postgres://quill:$DB_PASSWORD@10.0.0.4/fable",
        "postgres://quill:xxxx@10.0.0.4/fable",
        "postgres://${DB_USER}:${DB_PASSWORD}@db/fable",
        # a port and a path are not a password
        "https://fable.test:8443/works@home",
        "See https://fable.test:443/a/b and mail ada@fable.test",
        # placeholders in a header
        "Authorization: Bearer <token>",
        "Authorization: Bearer $TOKEN",
        "Authorization: Bearer ${API_TOKEN}",
        "Authorization: Bearer {{ token }}",
        "Authorization: Bearer xxxxxxxxxxxx",
        "Authorization: Bearer YOUR_API_TOKEN",
        "Authorization: Bearer your-token-here",
        "Authorization: Basic <base64 of user:password>",
        "The Authorization header carries a Bearer token.",
        "Send Authorization: Bearer and then the token.",
        # prose about passwords, and placeholders in quotes
        "the password is rotated weekly",
        "The password is rotated weekly; ask Brook for the new one.",
        'password = "<your password here>"',
        'password = "short words"',  # under 12 characters
        "password = '************'",
        'passphrase: "${PASSPHRASE}"',
        'error_token = "unexpected end of input"',  # a token's name: classes still count
    ],
)
def test_what_only_looks_like_a_pasted_credential_is_left_alone(text):
    """Under the default policy (the stricter one also sees an email address in some)."""
    assert at_line_three(text) == []


# 2. cairn's own API keys


def test_a_new_key_starts_with_the_fixed_prefix_and_the_guard_refuses_one(tmp_path):
    from cairn.store import Store

    assert KEY_PREFIX == "cairn_"
    store = Store(tmp_path / "cairn.db")
    try:
        key = store.create_key("laptop")
    finally:
        store.close()
    assert key.startswith(KEY_PREFIX) and len(key) == len(KEY_PREFIX) + 43
    assert "." not in key  # so no key can be taken for an access or refresh token
    assert at_line_three(f"CAIRN_API_KEY={key}") != []
    assert at_line_three(f"the key is {key}, keep it") == [("cairn_api_key", 3)]
    assert at_line_three(key) == [("cairn_api_key", 3)]


@pytest.mark.parametrize("seed", range(20))
def test_any_key_the_store_could_make_is_refused(seed):
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    assert ("cairn_api_key", 1) in rules(f"use {key} here")


@pytest.mark.parametrize(
    "text",
    [
        "cairn_session",
        "cairn_credential",
        "cairn_" + "guard_rule_for_every_kind_of_key_people_paste",  # an identifier
        "cairn_" + "a" * 43,
        "the module cairn_" + "x" * 31 + " is short",
    ],
)
def test_names_that_only_start_like_a_key_are_left_alone(text):
    assert at_line_three(text) == []


def test_a_rule_for_people_names_the_product():
    assert rule_words("cairn_api_key") == "a cairn API key"
    assert rule_words("cairn_api_key", "Knowledge Base") == "a Knowledge Base API key"
    assert "a Knowledge Base API key" in policy_words("secrets", "Knowledge Base")
    assert rule_words("credential_url") == "a password in a URL"
    assert rule_words("auth_header") == "an Authorization header with its value"


# 3. Under the stricter policy


@pytest.mark.parametrize(
    "rule, text",
    [
        ("phone_number", "Ring Ada on (415) 555-0199 tomorrow."),
        ("phone_number", "Ring Ada on (415)555-0199 tomorrow."),
        ("phone_number", "Ring Ada on 415-555-0199 tomorrow."),
        ("phone_number", "Ring Ada on 415.555.0199 tomorrow."),
        ("date_of_birth", "DOB: 2015-03-12"),
        ("date_of_birth", "Quill, dob 03/12/2015, in year two."),
        ("date_of_birth", "Date of birth: 12 March 2015"),
        ("date_of_birth", "date of birth March 12, 2015"),
        ("medical_record_number", "MRN 00482913"),
        ("medical_record_number", "MRN: FW-4829131"),
        ("medical_record_number", "Medical record number: 4829131"),
    ],
)
def test_personal_data_people_paste_is_refused_under_the_stricter_policy(rule, text):
    assert at_line_three(text, "secrets+personal") == [(rule, 3)]
    assert at_line_three(text, "secrets") == []


@pytest.mark.parametrize(
    "text",
    [
        "Released on 2026-11-19, version 415.555.01.",
        "Call 600 123 456 or extension 4411.",
        "Order 415-555-01999 is not a phone number.",
        "Build 1415-555-0199 and 415-555-019 are not phone numbers.",
        "Versions 4.15.555 and 415.555 are versions.",
        "Mixed 415-555.0199 separators.",
        "The DOB field is required.",
        "date of birth: unknown",
        "Ada's date of birth is on file.",
        "MRN is the record's number.",
        "MRN 12 is too short.",
        "an mrna vaccine 2026",
    ],
)
def test_what_only_looks_like_that_personal_data_is_left_alone(text):
    assert at_line_three(text, "secrets+personal") == []


def test_the_stricter_policy_says_what_it_now_refuses_and_what_stays_beyond_it():
    said = policy_words("secrets+personal")
    assert "a phone number written with + or in the North American way" in said
    assert "a date of birth" in said and "a medical record number" in said
    assert "a date of birth" not in policy_words("secrets")


# 4. The answer names the rule and the line, never the match


def test_a_finding_of_a_new_rule_holds_no_part_of_what_matched():
    password = "S3cur3P" + "assw0rdFable"
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    note = "\n".join(
        [
            f"postgres://quill:{password}@10.0.0.4:5432/works",
            "Authorization: Bearer " + ALNUM,
            'password = "correct horse ' + 'battery staple"',
            key,
            "Ring (415) 555-0199",
            "DOB: 2015-03-12",
            "MRN 00482913",
        ]
    )
    found = PatternGuard("secrets+personal").check(note)
    assert [(f.rule, f.line) for f in found] == [
        ("credential_url", 1),
        ("auth_header", 2),
        ("assigned_secret", 3),
        ("cairn_api_key", 4),
        ("phone_number", 5),
        ("date_of_birth", 6),
        ("medical_record_number", 7),
    ]
    said = " ".join(repr(f) + f.model_dump_json() for f in found)
    for part in (password, ALNUM, "battery", key, "555-0199", "2015", "00482913"):
        assert part not in said
