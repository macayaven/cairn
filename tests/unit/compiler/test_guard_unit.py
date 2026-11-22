"""Unit tests for the note guard's patterns: the inner TDD loop under
tests/acceptance/test_g1_guard.py. Every secret-shaped string is built at run time, so this
file holds nothing that looks like a live credential."""

import functools
import re
import time

import pytest

from cairn.guard import (
    FINDINGS_PER_RULE,
    PERSONAL_RULES,
    POLICIES,
    RULES,
    SECRET_RULES,
    Finding,
    PatternGuard,
    policy_words,
    rule_words,
)
from cairn.store import GUARD_POLICIES

ALNUM = "Zx9Qw3Er7Ty1Ui5Op2As8Df4Gh6Jk0Lm"  # 32 characters, letters and digits
LETTERS = "ZxQwErTyUiOpAsDfGhJkLmNbVcXzPoIuYtReWqAsDfGh"  # 44 letters, no digit
MIB = 1024 * 1024
WIDTH_MAX = 20_000  # characters one match attempt can read, for the widest rule (a JWT)


def rules(text: str, policy: str = "secrets+personal") -> list[tuple[str, int]]:
    return [(f.rule, f.line) for f in PatternGuard(policy).check(text)]


def test_every_rule_has_words_for_people_and_another_guards_rule_its_name():
    for rule in RULES:
        words = rule_words(rule.name)
        assert words and words != rule.name and "_" not in words, rule.name
    assert rule_words("github_token") == "a GitHub token"
    assert rule_words("house_rule") == "house_rule"  # a custom guard's rule, as it is named


def test_every_policy_says_in_a_sentence_what_it_refuses():
    for policy in POLICIES:
        said = policy_words(policy)
        assert said.endswith(".") and policy not in said and "_" not in said, policy
    assert policy_words("off").startswith("Nothing is checked")
    assert "Personal data is not checked." in policy_words("secrets")
    for rule in PERSONAL_RULES:  # the widest policy names each kind of personal data it refuses
        assert rule_words(rule) in policy_words("secrets+personal"), rule
        assert rule_words(rule) not in policy_words("secrets"), rule
    with pytest.raises(KeyError):
        policy_words("everything")


def test_a_refusing_policy_says_it_knows_secrets_only_by_their_shape():
    """A secret is refused only in a shape a rule recognises: each refusing policy says so,
    and says a password in plain words is not recognised, which it is not."""
    plain = "The deploy password is Tr0ub4dor-3xyz-long"
    for policy in ("secrets", "secrets+personal"):
        said = policy_words(policy)
        assert said.startswith("Notes that hold a secret in a shape the guard recognises")
        assert "A password written in plain words is not recognised." in said, policy
        assert PatternGuard(policy).check(plain) == [], policy
    assert PatternGuard().check("password = 'Tr0ub4dor-3xyz-long'")  # a shape it knows


def test_the_policies_are_the_settings_values_and_name_their_rules():
    assert POLICIES == GUARD_POLICIES == ("off", "secrets", "secrets+personal")
    assert [r.name for r in RULES] == [*SECRET_RULES, *PERSONAL_RULES]
    assert PatternGuard().policy == "secrets"
    assert {r.kind for r in PatternGuard("secrets").rules} == {"secret"}
    assert [r.name for r in PatternGuard("secrets+personal").rules] == [r.name for r in RULES]
    assert PatternGuard("off").rules == ()
    for bad in ("", "SECRETS", "personal", "secrets,personal"):
        with pytest.raises(ValueError):
            PatternGuard(bad)


def test_a_finding_is_a_rule_a_kind_and_a_line_and_nothing_else():
    assert set(Finding.model_fields) == {"rule", "kind", "line"}
    with pytest.raises(ValueError):
        Finding(rule="x", kind="secret", line=0)  # lines are 1-based
    with pytest.raises(ValueError):
        Finding(rule="x", kind="other", line=1)


# Secrets: each rule finds its shape, and leaves alone what only looks like it


@pytest.mark.parametrize(
    "rule, text",
    [
        ("private_key", "-----BEGIN PRIVATE KEY-----"),
        ("private_key", "-----BEGIN OPENSSH PRIVATE KEY-----"),
        ("private_key", "-----BEGIN PGP PRIVATE KEY BLOCK-----"),
        ("aws_access_key", "ASIA" + "Q" * 12 + "7XZ3"),
        ("github_token", "gho_" + ALNUM + "abcd"),
        ("github_token", "github_pat_" + ALNUM[:22] + "_" + ALNUM),
        ("slack_token", "xoxp-" + "2" * 11 + "-" + ALNUM),
        ("model_api_key", "sk-proj-" + ALNUM),
        ("model_api_key", "AIza" + ALNUM + "abc"),
        # Only sk- needs a digit (a slug like sk-learn-... has none): the other prefixes
        # are keys whatever their letters.
        ("model_api_key", "AIza" + LETTERS[:35]),
        ("model_api_key", "hf_" + LETTERS[:30]),
        ("model_api_key", "gsk_" + LETTERS[:40]),
        ("jwt", "eyJ" + ALNUM + ".eyJ" + ALNUM + "." + ALNUM),
        ("cairn_credential", "cairn.rt." + ALNUM + "abcdefghijk"),
        ("assigned_secret", "PGPASSWORD=" + ALNUM[:14]),
        ("assigned_secret", '"client_secret": "' + ALNUM[:16] + '"'),
        ("assigned_secret", "api-key: '" + ALNUM[:12] + "'"),
        ("assigned_secret", "AWS_SECRET_ACCESS_KEY = " + ALNUM),
        ("assigned_secret", "token := " + ALNUM[:20]),
        # A placeholder is a whole form, never a word inside the value.
        ("assigned_secret", 'password="your' + 'ActualPassword123"'),
        ("assigned_secret", "token=abcdefghij123456" + ".example"),
        ("assigned_secret", "secret = 'my" + "ExampleSecret42'"),
        ("assigned_secret", 'password = "dummy' + 'Pass123456"'),
        ("assigned_secret", "secret: change" + "me123456"),
        ("assigned_secret", 'token = "ab12cd34' + '...wxyz9876"'),
        ("assigned_secret", "api_key = " + "QWERTY12ASDFGH34ZXCV"),  # not a variable name
    ],
)
def test_a_secret_is_found_on_its_line(rule, text):
    assert rules(f"# Notes\n\nSome words.\n{text}\n", "secrets") == [(rule, 4)]


@pytest.mark.parametrize(
    "text",
    [
        "-----BEGIN PUBLIC KEY-----",
        "-----BEGIN CERTIFICATE-----",
        "AKIA" + "Q" * 15,  # one character short
        "XAKIA" + "Q" * 16,  # inside a longer word
        "ghp_" + ALNUM[:20],  # too short for a token
        "sk-learn-is-a-python-library-name",  # no digit: a slug
        "eyJhbGciOiJIUzI1NiJ9 is the start of a header",
        "cairn.at.short",
        "password = <your password>",
        "API_KEY=${CAIRN_API_KEY}",
        "password: $DB_PASSWORD_FROM_THE_ENV",
        "password = os.environ.get",  # a reference, not a literal
        "password = settings.database_password",
        "token = GITHUB_TOKEN_FROM_CI",  # an environment variable's name
        "password = 'your-password-here'",
        'api_key = "your_api_key_here"',
        "password = <your-database-password>",
        "token = ${GITHUB_TOKEN:-unset-in-dev}",
        'token = "{{ secrets.deploy_token }}"',
        "password = %DATABASE_PASSWORD%",
        "token: XXXX-XXXX-XXXX-XXXX",
        'secret = "<placeholder-for-secret>"',
        "token = '************'",
        "token = 'xxxxxxxxxxxxxxxx'",
        'password = "short1"',
        "token: 3 retries a minute",
        'error_token = "unexpected end of input"',  # prose in quotes
        "token_url = https://auth.example.test/oauth/token",  # not a secret's name
        "Passwords: rotate them every 90 days",
        "The secretary: Ada2024 is on leave",
        "pwd = /home/ada/projects/2026",
    ],
)
def test_what_only_looks_like_a_secret_is_left_alone(text):
    assert rules(f"# Notes\n\n{text}\n", "secrets") == []


def test_ids_digests_versions_dates_and_urls_are_not_secrets_or_personal_data():
    harmless = "\n".join(
        [
            "Commit 9dc01794e638a1b2c3d4e5f60718293a4b5c6d7e in v2.14.3 and v10.0.0-rc.1.",
            "Run 3f2504e0-4f89-11d3-9a0c-0305e82c3301 on 2026-11-10T09:00:00Z.",
            "sha256:e2a413d8659298fc83aa53de91305a48e9ca5c34b494d9626448b9500c249e06",
            "md5 9e107d9d372bb6826bd81d3542a419d6, base64 aGVsbG8gd29ybGQ=",
            "https://example.test/a/b?page=2&sort=asc#top, port 8765, 60 calls a minute",
            "Order 1234567890123 shipped; 1234 5678 9012 3456 fails the checksum.",
            "Pi is 3.14159265358979, and 0.30000000000000004 is a float.",
            "Account GB00 WEST 1234 5698 7654 32 has a bad checksum.",
        ]
    )
    assert rules(harmless) == []


# Personal data


@pytest.mark.parametrize(
    "rule, text",
    [
        ("email_address", "Mail grace.hopper+cairn@mail.example.test now."),
        ("phone_number", "Ring +1 415.555.0100 tomorrow."),
        ("phone_number", "Ring +4915112345678 tomorrow."),
        ("us_ssn", "SSN: 219-09-9999."),
        ("payment_card", "Paid with 5555-5555-5555-4444."),
        ("payment_card", "Paid with 378282246310005."),  # 15 digits, no spaces
        ("payment_card", "Paid with 3782 822463 10005."),  # Amex grouping
        ("iban", "IBAN DE89370400440532013000."),
        ("iban", "IBAN FR14 2004 1010 0505 0001 3M02 606."),
    ],
)
def test_personal_data_is_found_only_under_the_personal_policy(rule, text):
    assert rules(f"# Contact\n\n{text}\n") == [(rule, 3)]
    assert rules(f"# Contact\n\n{text}\n", "secrets") == []


@pytest.mark.parametrize(
    "text",
    [
        "Mail me at grace at example dot test.",
        "The decorator @functools.cache works.",
        "Call 600 123 456 or extension 4411.",  # no country code: not recognised
        "Build 000-12-3456 and 666-12-3456 and 900-12-3456 are not SSNs.",
        "Card 4111 1111 1111 1112 fails Luhn.",
        "Card 4111 1111-1111 1111 mixes its separators.",
        "Twenty digits 41111111111111111111 are too many.",
        "IBAN DE89370400440532013001 fails its checksum.",
    ],
)
def test_what_only_looks_like_personal_data_is_left_alone(text):
    assert rules(f"# Contact\n\n{text}\n") == []


# Findings


def test_each_rule_reports_each_line_once_in_line_order():
    email = "a.b@example.test"
    text = f"{email} and {email}\nnothing\n{email}\n" + "-----BEGIN RSA PRIVATE KEY-----\n"
    assert rules(text) == [
        ("email_address", 1),
        ("email_address", 3),
        ("private_key", 4),
    ]


def test_lines_are_counted_by_line_feeds_and_the_last_line_needs_none():
    key = "AKIA" + "Q" * 16
    assert rules(key) == [("aws_access_key", 1)]
    assert rules("\r\n\r\n" + key) == [("aws_access_key", 3)]


def test_a_note_full_of_findings_reports_a_bounded_number_of_them():
    text = "\n".join(f"user{n}@example.test" for n in range(1000))
    found = PatternGuard("secrets+personal").check(text)
    assert len(found) == FINDINGS_PER_RULE and found[-1].line == FINDINGS_PER_RULE


# Linear time: every pattern is bounded, and adversarial input stays within its budget


def test_every_pattern_matches_a_bounded_width_and_starts_at_a_boundary():
    """Each rule's match is at most `width` characters, so an attempt at one position reads
    at most that many; and each pattern begins with a literal or a lookbehind that rules out
    a start inside the run it reads, so a run is read from its start only. Together: at most
    len(text) * width character steps per rule, and in practice about len(text)."""
    parser = re._parser  # the stdlib's own parser: the widths the engine itself computes
    for rule in RULES:
        parsed = parser.parse(rule.pattern.pattern, rule.pattern.flags)
        low, high = parsed.getwidth()
        assert 0 < low <= high <= WIDTH_MAX, (rule.name, high)
        first = parsed[0][0]
        if rule.name == "assigned_secret":  # a keyword, which an identifier may end in
            assert first is parser.BRANCH, rule.name
        else:
            assert first in (parser.ASSERT_NOT, parser.LITERAL), (rule.name, first)


# For each rule, the piece of text that makes it work hardest: each repeat starts a match
# attempt that reads as far as the pattern allows (or backtracks as far as it can) and
# then fails, so a note made of them matches nothing.
NEAR_MISSES = {
    "private_key": "-----BEGIN " + "A " * 4 + "PRIVATE KE ",
    "aws_access_key": "AKIA" + "Q" * 15 + " ",
    "github_token": "ghp_" + "a" * 252 + " ",  # one longer than a token can be
    "slack_token": "xoxb-" + "1" * 251 + " ",
    "model_api_key": "sk-" + "a" * 251 + " ",
    "jwt": "eyJ" + "a" * 4096 + ".eyJ" + "a" * 8192 + "." + "a" * 4097 + " ",
    "cairn_credential": "cairn.at." + "a" * 65 + " ",
    "server_api_key": "cairn_" + "a" * 44 + " ",  # one longer than a key is
    "credential_url": "a" * 31 + "://" + "a" * 128 + ":" + "b" * 257 + " ",  # no "@" after
    "auth_header": "authorization: bearer " + "a" * 4097 + " ",
    "assigned_secret": "password" * 30 + " =        '" + "a" * 300 + " ",  # never closed
    "email_address": "a" * 64 + "@" + ("a" * 62 + "1.") * 8 + "a1 ",  # no top-level domain
    "phone_number": "+1" + "1" * 15 + " ",  # 16 digits: one too many
    "us_ssn": "123-45-678 ",
    "payment_card": "4111 1111 1111 111 " + "1" * 20 + " ",
    "iban": "GB82" + "ABCD" * 8 + "x ",  # runs on into a lower-case letter
    "date_of_birth": "date of birth: 1111-11-11111 ",
    "medical_record_number": "medical record number: " + "1" * 16 + " ",
}


@functools.cache
def adversarial(name: str) -> str:
    """4 MiB of one rule's near-misses."""
    piece = NEAR_MISSES[name]
    return (piece * (4 * MIB // len(piece) + 1))[: 4 * MIB]


def test_every_rule_has_its_near_misses_and_they_match_nothing():
    assert set(NEAR_MISSES) == {rule.name for rule in RULES}
    for rule in RULES:
        assert not rule.pattern.search(NEAR_MISSES[rule.name] * 3), rule.name


def cheapest(run, repeats: int = 3) -> float:
    best = float("inf")
    for _ in range(repeats):
        started = time.process_time()
        run()
        best = min(best, time.process_time() - started)
    return best


# One reference operation: a regex pass that tries a match at every position of the text
# and fails, as each rule does on a text that holds nothing of its shape.
REFERENCE = re.compile(r"(?<!\x00)\x01")  # a lookbehind first: no fast prefix scan
BUDGET = 40  # reference passes per rule; catastrophic backtracking costs thousands of them


@pytest.mark.perf
@pytest.mark.parametrize("name", sorted(NEAR_MISSES))
def test_each_rule_reads_4_mib_of_its_worst_input_within_budget(name):
    """A budget in operations, not seconds: the rule's pass over 4 MiB of its own
    near-misses, measured in passes of the reference over the same text on the same
    machine."""
    text = adversarial(name)
    (rule,) = [r for r in RULES if r.name == name]
    reference = cheapest(lambda: REFERENCE.search(text))
    spent = cheapest(lambda: rule.pattern.search(text), repeats=2)
    assert spent <= BUDGET * reference, f"{name}: {spent / reference:.0f} reference passes"


def test_the_whole_guard_answers_on_a_4_mib_note_of_every_near_miss():
    text = "".join(piece * (MIB // 3 // len(piece) + 1) for piece in NEAR_MISSES.values())
    assert len(text) > 4 * MIB
    assert PatternGuard("secrets+personal").check(text) == []
