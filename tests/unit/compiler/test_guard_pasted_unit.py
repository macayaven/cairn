"""Unit tests for the credentials and personal data people actually paste (review, finding
2): a password in a URL's authority, an Authorization header, a quoted passphrase in plain
words, cairn's own API keys, and under the stricter policy a North American phone number
without "+", a labelled date of birth and a labelled medical record number. Each input is
built at run time, so this file holds nothing that looks like a live credential, and each
is one no other rule catches, so a finding names the rule that was added for it."""

import html
import json
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
        ("credential_url", "postgres://quill" + ":password@123@10.0.0.4:5432/fable"),
        ("credential_url", "postgres://quill" + ":@ssw0rd!@10.0.0.4:5432/fable"),
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
        "https://fable.test:443?email=ada@fable.test",
        "https://fable.test:443#ada@fable.test",
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
    assert at_line_three(f"the key is {key}, keep it") == [("server_api_key", 3)]
    assert at_line_three(key) == [("server_api_key", 3)]


@pytest.mark.parametrize("seed", range(20))
def test_any_key_the_store_could_make_is_refused(seed):
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    assert ("server_api_key", 1) in rules(f"use {key} here")


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
    assert rule_words("server_api_key") == "a cairn API key"
    assert rule_words("server_api_key", "Knowledge Base") == "a Knowledge Base API key"
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
        ("server_api_key", 4),
        ("phone_number", 5),
        ("date_of_birth", 6),
        ("medical_record_number", 7),
    ]
    said = " ".join(repr(f) + f.model_dump_json() for f in found)
    for part in (password, ALNUM, "battery", key, "555-0199", "2015", "00482913"):
        assert part not in said


def test_a_template_word_for_the_password_in_a_url_is_a_placeholder():
    """The guide's own example, and documentation like it, is not a credential."""
    for word in ("password", "PASSWORD", "pass", "passwd", "pwd", "secret"):
        assert at_line_three(f"postgres://user:{word}@host/db") == [], word
    assert at_line_three("postgres://user:" + "passw0rd-Fable@host/db") == [("credential_url", 3)]


def test_no_new_rule_is_named_after_the_software():
    """A refusal names its rule over REST and MCP; under a brand only the frozen
    cairn_credential may name the software (cairn.web.brand)."""
    from cairn import SOFTWARE
    from cairn.guard import RULES

    named = [r.name for r in RULES if SOFTWARE in r.name]
    assert named == ["cairn_credential"]


# 4. Through the real answers: REST, the web composer, the audit log


@pytest.fixture
def web(tmp_path):
    from types import SimpleNamespace

    from starlette.testclient import TestClient

    from cairn.server import create_app
    from cairn.store import Store

    store = Store(tmp_path / "cairn.db")
    store.create_project("ops")
    store.set_setting("guard.policy", "secrets+personal")
    key = store.create_key("laptop")
    base = "http://127.0.0.1:8765"
    with TestClient(create_app(store, public_url=base), base_url=base) as client:
        yield SimpleNamespace(client=client, store=store, key=key)
    store.close()


@pytest.mark.parametrize(
    "rule, kind, line",
    [
        ("credential_url", "secret", "postgres://quill:" + "S3cur3Pfable@10.0.0.4/works"),
        ("auth_header", "secret", "Authorization: Bearer " + ALNUM),
        ("medical_record_number", "personal", "MRN 00482913"),
        ("date_of_birth", "personal", "DOB: 2015-03-12"),
    ],
)
def test_a_refusal_names_the_rule_and_the_line_never_the_match(web, rule, kind, line):
    match = line.split(" ", 1)[-1] if " " in line else line
    note = f"# Notes\n\nSome words.\n{line}\n"
    r = web.client.post(
        "/api/projects/ops/notes", headers={"x-api-key": web.key}, json={"text": note}
    )
    assert r.status_code == 422
    assert r.json()["findings"] == [{"rule": rule, "kind": kind, "line": 4}]
    assert match not in r.text
    (event, *_) = web.store.list_audit()
    assert event.action == "note.refused" and event.detail == {"rules": [rule]}
    assert match not in json.dumps(event.detail)
    login = web.client.post(
        "/login",
        data={"key": web.key},
        headers={"sec-fetch-site": "same-origin"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    form = {"text": note.replace("Some words", "Other words"), "project": "ops"}
    page = web.client.post("/notes", data=form, headers={"sec-fetch-site": "same-origin"})
    assert page.status_code == 422 and f'data-rule="{rule}"' in page.text
    # The composer gives the note back as typed, in its text area; nowhere else.
    assert page.text.count(html.escape(match, quote=False)) <= 1
