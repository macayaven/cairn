"""The note guard: what a note may not carry into memory.

Contract (frozen by tests/acceptance/test_g1_guard.py):
- `Guard` is the seam: anything with `check(text) -> list[Finding]`. A Finding has `rule`,
  `kind` ("secret" or "personal") and `line` (1-based, counted by line feeds), and never
  the matched text. `create_app(..., guard=...)` replaces the default guard altogether.
- `PatternGuard(policy="secrets")` is the default. Policies: "off" (nothing is checked),
  "secrets" and "secrets+personal" (POLICIES; a workspace's policy is its setting
  "guard.policy"); another policy is a ValueError. Each rule reports each line it matches
  once, in line order, and at most FINDINGS_PER_RULE lines: enough to refuse a note.
- `Store.add_note`, the one place a note is written, checks every field it stores
  (text, title, topic, label, source_ref, via) under the workspace's policy, or with
  the guard its view carries (`Store.with_guard`, which `create_app(..., guard=...)` uses);
  a note with any finding is refused (NoteRefused, recorded as note.refused) and
  nothing of it is stored or logged. `Memory.note` answers that refusal with Refused (422).

Each rule also has a few words for people (`SAID`, `rule_words`): the web view's refusal and
the audit page say those, keeping the rule's name as `data-rule`; REST and MCP answers name
the rule as it is. Each policy has a sentence for people too (`POLICY_SAID`,
`policy_words`): the workspace page says its own and offers the others to its managers.

This is pattern matching, not understanding. It recognises the shapes below and nothing
else.

Secrets ("secrets" and "secrets+personal"):
- private_key: the BEGIN line of a PEM or PGP private key block.
- aws_access_key: an AWS access key id (AKIA, ASIA, ABIA or ACCA and 16 capitals or
  digits).
- github_token: a GitHub token (ghp_, gho_, ghu_, ghs_, ghr_ and 36 or more letters and
  digits, or github_pat_ and 22 or more).
- slack_token: a Slack token (xoxa-, xoxb-, xoxp-, xoxo-, xoxs-, xoxr- or xoxe- and 10 or
  more letters, digits and dashes).
- model_api_key: a model provider's key: sk- and 20 or more characters holding a digit
  (OpenAI, Anthropic, OpenRouter, Langfuse and others that use that prefix), AIza and 35
  (Google), hf_ and 30 or more (Hugging Face), gsk_ and 40 or more (Groq). Only sk- needs
  a digit; the others are keys whatever their characters.
- jwt: a JSON Web Token: three base64url parts, the first two starting "eyJ", signed.
- cairn_credential: cairn's own OAuth access and refresh tokens.
- cairn_api_key: cairn's own API keys made since keys have their prefix (KEY_PREFIX and 43
  letters, digits, "_" and "-", capitals and small letters among them). A key made before,
  and a session token, have no prefix and cannot be told from other random text: only
  assigned_secret catches one, when it is assigned to a secret's name.
- credential_url: a URL with a password in its authority (scheme://user:password@host, any
  scheme), the password not a placeholder (<password>, ***, ${VAR}, xxxx).
- auth_header: an `Authorization: Bearer` or `Authorization: Basic` header (any case, ":" or
  "=", quoted or not) whose value is 8 or more token characters of two kinds at least, and
  not a placeholder or a variable's name (<token>, $TOKEN, YOUR_API_TOKEN).
- assigned_secret: a name ending in password, passwd, passphrase, secret, token, api key,
  access key, private key or secret key (any case, "_", "-" or "." between the words),
  then ":", "=", ":=" or "=>", then a literal of 12 or more characters with no whitespace.
  Not literals: placeholders, recognised by the whole value's form and never by a word
  inside it (<...>, ${VAR}, $VAR, $(cmd), {{ var }}, %VAR%, %(name)s, runs of x or *,
  your-...-here, changeme, replace-me, example, placeholder or redacted, or one character
  repeated), and, unquoted, variable references (a dotted name such as
  settings.db_password, an environment variable's NAME; each part a word with at most two
  digits at its end) and values without both a letter and a digit. A quoted literal needs
  two of: lower case, upper case, digits, symbols, and no whitespace; but a quoted literal
  assigned to a name ending in password, passwd or passphrase is one whatever its
  characters, words and spaces included. Any other value assigned to such a name is treated
  as a secret, whatever it looks like (a hex digest or a UUID too).
Personal data ("secrets+personal" only):
- email_address: local@domain.tld.
- phone_number: an international number written with "+", a country code and 7 to 14
  more digits, single spaces, dots or dashes between them; or a North American number
  without "+": (415) 555-0199, 415-555-0199 or 415.555.0199. Any other number without "+"
  is not recognised.
- us_ssn: a US Social Security number written ddd-dd-dddd, without the ranges never issued
  (000, 666 and 9xx areas, 00 groups, 0000 serials).
- payment_card: 13 to 19 digits, together or grouped 4-4-4-4(-3) or 4-6-4/5 with one kind
  of separator, that pass the Luhn check.
- iban: an IBAN in capitals, together or in groups of four, 15 to 34 characters, that
  passes its mod-97 check.
- date_of_birth: a date after its label (DOB, D.O.B., date of birth, birthdate): digits
  such as 2015-03-12 or 03/12/2015, or with the month's name (12 March 2015, March 12,
  2015).
- medical_record_number: 4 to 15 digits, perhaps after up to three letters, after its label
  (MRN, medical record number).
It does not recognise a person's name, a postal address, a date of birth or a medical record
number without its label, free-text health or financial details, national ids other than
the US SSN, other phone numbers without "+", keys of providers not listed, or a secret that
has been split, encoded or reworded. The patterns
read ASCII: a secret spelled with fullwidth or look-alike (homoglyph) characters is out of
scope. A note that passes is not thereby free of secrets or personal data.

Cost: every pattern is bounded (no unbounded repeat; the widest match, a JWT, is about
16,000 characters) and begins with a literal or a lookbehind that rules out starting inside
the run it reads, so a run is read from its start only: a check is linear in the length of
the note (tests/unit/test_guard_unit.py measures it on 4 MiB of each rule's worst input).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from cairn import SOFTWARE
from cairn.store import (
    ACCESS_TOKEN_PREFIX,
    DEFAULT_GUARD_POLICY,
    GUARD_POLICIES,
    KEY_PREFIX,
    POLICY_KINDS,
    REFRESH_TOKEN_PREFIX,
)

POLICIES = GUARD_POLICIES  # "off", "secrets", "secrets+personal": the setting's values
DEFAULT_POLICY = DEFAULT_GUARD_POLICY
# Each policy in a sentence a member understands: what it refuses. The workspace page says
# the workspace's own, and offers these as the choices to the people who manage it. A secret
# is refused only in a shape the rules above recognise, so the sentences name those shapes.
# "{product}" in these and in SAID is the product's name, which policy_words and rule_words
# are given (the brand's under a brand, B3; the software's without one).
_SHAPES = (
    "a private key, an AWS, GitHub, Slack or model provider's key or token, a signed JSON Web"
    " Token, a {product} API key, a {product} access or refresh token, a password in a URL, an"
    " Authorization header with its value, or a value assigned to a name like password, token"
    " or API key"
)
_PLAIN_WORDS = "A password written in plain words is not recognised."
POLICY_SAID = {
    "off": "Nothing is checked: notes are kept whatever they hold, keys, passwords and"
    " personal data included.",
    "secrets": f"Notes that hold a secret in a shape the guard recognises are refused: {_SHAPES}."
    f" {_PLAIN_WORDS} Personal data is not checked.",
    "secrets+personal": "Notes that hold a secret in a shape the guard recognises are"
    f" refused ({_SHAPES}), and so are notes that hold personal data: an email address, a"
    " phone number written with + or in the North American way, a US Social Security number,"
    " a payment card number, a bank account number (IBAN), and a date of birth or a medical"
    f" record number given with its label. {_PLAIN_WORDS}",
}


def policy_words(policy: str, product: str = SOFTWARE) -> str:
    """A policy in a sentence for people (POLICY_SAID), naming the product as `product`.
    KeyError: not a policy."""
    return POLICY_SAID[policy].format(product=product)


FINDINGS_PER_RULE = 20  # lines reported per rule: enough to refuse, bounded on any note

Kind = Literal["secret", "personal"]


class Finding(BaseModel):
    """One rule matched on one line. Never the matched text."""

    model_config = ConfigDict(frozen=True)

    rule: str
    kind: Kind
    line: int = Field(ge=1)


class Guard(Protocol):
    """The note guard seam: what a note may not carry. A classifier, or a workspace's
    own rules, would be another implementation."""

    def check(self, text: str) -> list[Finding]: ...


@dataclass(frozen=True)
class Rule:
    name: str
    kind: Kind
    pattern: re.Pattern[str]
    valid: Callable[[re.Match[str]], bool] = lambda match: True  # beyond the shape


# Validators: what a pattern alone cannot say.


def _luhn(match: re.Match[str]) -> bool:
    digits = [int(c) for c in match.group() if c.isdigit()]
    total = 0
    for position, digit in enumerate(reversed(digits)):
        if position % 2:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        total += digit
    return total % 10 == 0


def _iban(match: re.Match[str]) -> bool:
    compact = match.group().replace(" ", "")
    if not 15 <= len(compact) <= 34:
        return False
    moved = compact[4:] + compact[:4]
    return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1


def _sk_has_digit(match: re.Match[str]) -> bool:
    """An sk- key holds a digit (a slug such as sk-learn-... has none); the other prefixes
    are keys whatever their characters."""
    found = match.group()
    return not found.startswith("sk-") or any(c.isdigit() for c in found[3:])


# A placeholder is recognised by its whole form, never by a word inside the value: a
# password that holds "your" or "example" is still a password. (Values are at most 256
# characters, so these full matches are bounded.)
_PLACEHOLDER = re.compile(
    r"<[^<>]*>"  # <your password>
    r"|\$\{[^{}]*\}|\$[A-Za-z_][A-Za-z0-9_]*|\$\([^()]*\)"  # ${VAR}, ${VAR:-x}, $VAR, $(cmd)
    r"|\{\{[^{}]*\}\}|%[A-Za-z_][A-Za-z0-9_]*%|%\([A-Za-z_][A-Za-z0-9_]*\)s"  # {{ v }}, %V%
    r"|[xX*]+(?:[-_. ][xX*]+)*"  # xxxx, ****, XXXX-XXXX
    r"|your[-_.][A-Za-z0-9_.-]*[-_.]here"  # your-password-here
    r"|[\[(<]?(?:change[-_]?me|replace[-_]?me|example|placeholder|redacted)[\])>]?",
    re.ASCII | re.IGNORECASE,
)
# A reference to a value, not a value: a dotted name such as settings.db_password, or an
# environment variable's NAME. Each part is a word with at most two digits at its end, so
# random letters and digits (abcdefghij123456.example, QWERTY12ASDFGH34) are not names.
_DOTTED_NAME = re.compile(r"[A-Za-z_]+[0-9]{0,2}(?:\.[A-Za-z_]+[0-9]{0,2})+", re.ASCII)
_ENV_NAME = re.compile(r"_?[A-Z]+[0-9]{0,2}(?:_[A-Z]+[0-9]{0,2})*_?", re.ASCII)
LITERAL_MIN = 12  # characters of an assigned secret


def _placeholder(value: str) -> bool:
    return _PLACEHOLDER.fullmatch(value) is not None or len(set(value)) == 1


def _classes(value: str) -> int:
    return sum(
        (
            any(c.islower() for c in value),
            any(c.isupper() for c in value),
            any(c.isdigit() for c in value),
            any(not c.isalnum() for c in value),
        )
    )


_PASSWORD_NAMES = ("password", "passwd", "passphrase")


def _literal(match: re.Match[str]) -> bool:
    """An assigned value that is a literal secret, not a placeholder or a reference. A quoted
    value assigned to a password's or a passphrase's name is one whatever its characters,
    words and spaces included (`password = "correct horse battery staple"`)."""
    quoted = match.group("dq") if match.group("dq") is not None else match.group("sq")
    value = quoted if quoted is not None else match.group("bare")
    if len(value) < LITERAL_MIN or _placeholder(value.strip()):
        return False
    if quoted is not None and match.group().lower().startswith(_PASSWORD_NAMES):
        return bool(value.strip())
    if any(c.isspace() for c in value):
        return False
    if quoted is not None:
        return _classes(value) >= 2
    if _DOTTED_NAME.fullmatch(value) or _ENV_NAME.fullmatch(value):
        return False
    return any(c.isalpha() for c in value) and any(c.isdigit() for c in value)


def _url_password(match: re.Match[str]) -> bool:
    """A URL's password that is one, not a placeholder (<password>, ***, ${VAR}, xxxx)."""
    return not _placeholder(match.group("pw"))


def _header_value(match: re.Match[str]) -> bool:
    """An Authorization header's value that looks real: not a placeholder or a variable's
    name (YOUR_API_TOKEN), and of two kinds of character at least."""
    value = match.group("value")
    if _placeholder(value) or _ENV_NAME.fullmatch(value) or _DOTTED_NAME.fullmatch(value):
        return False
    return _classes(value) >= 2


def _mixed_case(match: re.Match[str]) -> bool:
    """A key holds capitals and small letters (an identifier such as cairn_some_name holds
    no capital); a random key lacks either about once in five billion."""
    tail = match.group()[len(KEY_PREFIX) :]
    return any(c.isupper() for c in tail) and any(c.islower() for c in tail)


def _compile(pattern: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(pattern, re.ASCII | flags)


_B64 = "A-Za-z0-9_-"  # base64url, the alphabet of most tokens
_TOKEN_TAIL = "[A-Za-z0-9_-]{32,64}"
_CAIRN_PREFIXES = "|".join(re.escape(p) for p in (ACCESS_TOKEN_PREFIX, REFRESH_TOKEN_PREFIX))

SECRET_RULES = (
    "private_key",
    "aws_access_key",
    "github_token",
    "slack_token",
    "model_api_key",
    "jwt",
    "cairn_credential",
    "cairn_api_key",
    "credential_url",
    "auth_header",
    "assigned_secret",
)
PERSONAL_RULES = (
    "email_address",
    "phone_number",
    "us_ssn",
    "payment_card",
    "iban",
    "date_of_birth",
    "medical_record_number",
)
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]{0,6}\.?"
_DATE = (
    r"(?:\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}"  # 2015-03-12, 03/12/2015, 12.03.2015
    rf"|\d{{1,2}}[ \t]{{1,4}}{_MONTH}[ \t,]{{1,4}}\d{{4}}"  # 12 March 2015
    rf"|{_MONTH}[ \t]{{1,4}}\d{{1,2}}(?:st|nd|rd|th)?,?[ \t]{{1,4}}\d{{4}})"  # March 12, 2015
)

RULES: tuple[Rule, ...] = (
    Rule(
        "private_key",
        "secret",
        _compile(r"-----BEGIN (?:[A-Z0-9]{1,20} ){0,4}PRIVATE KEY(?: BLOCK)?-----"),
    ),
    Rule(
        "aws_access_key",
        "secret",
        _compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}(?![A-Za-z0-9])"),
    ),
    Rule(
        "github_token",
        "secret",
        _compile(
            r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{36,251}|github_pat_[A-Za-z0-9_]{22,251})"
            r"(?![A-Za-z0-9_])"
        ),
    ),
    Rule(
        "slack_token",
        "secret",
        _compile(r"(?<![A-Za-z0-9-])xox[abposre]-[A-Za-z0-9-]{10,250}(?![A-Za-z0-9-])"),
    ),
    Rule(
        "model_api_key",
        "secret",
        _compile(
            rf"(?<![{_B64}])(?:sk-[{_B64}]{{20,250}}|AIza[{_B64}]{{35}}|hf_[A-Za-z0-9]{{30,60}}"
            rf"|gsk_[A-Za-z0-9]{{40,60}})(?![{_B64}])"
        ),
        _sk_has_digit,
    ),
    Rule(
        "jwt",
        "secret",
        _compile(
            rf"(?<![{_B64}])eyJ[{_B64}]{{8,4096}}\.eyJ[{_B64}]{{8,8192}}\.[{_B64}]{{16,4096}}"
            rf"(?![{_B64}])"
        ),
    ),
    Rule(
        "cairn_credential",
        "secret",
        _compile(rf"(?<![A-Za-z0-9_.-])(?:{_CAIRN_PREFIXES}){_TOKEN_TAIL}(?![A-Za-z0-9_-])"),
    ),
    Rule(
        "cairn_api_key",
        "secret",
        _compile(
            rf"(?<![A-Za-z0-9_.-]){re.escape(KEY_PREFIX)}[A-Za-z0-9_-]{{43}}(?![A-Za-z0-9_-])"
        ),
        _mixed_case,
    ),
    Rule(
        "credential_url",
        "secret",
        _compile(
            r"(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]{0,30}://"
            r"[^\s/:@]{0,128}:(?P<pw>[^\s/@]{1,256})@"
        ),
        _url_password,
    ),
    Rule(
        "auth_header",
        "secret",
        _compile(
            r"(?<![A-Za-z0-9_-])authorization[\"']?[ \t]{0,8}[:=][ \t]{0,8}[\"']?"
            r"(?:bearer|basic)[ \t]{1,8}(?P<value>[A-Za-z0-9._~+/=-]{8,4096})"
            r"(?![A-Za-z0-9._~+/=-])",
            re.IGNORECASE,
        ),
        _header_value,
    ),
    Rule(
        "assigned_secret",
        "secret",
        _compile(
            r"(?:password|passwd|passphrase|secret|token|api[_.-]?key|access[_.-]?key"
            r"|private[_.-]?key|secret[_.-]?key)(?![A-Za-z0-9_.-])"  # the end of the name
            r"[\"']?[ \t]{0,8}(?::=|=>|=|:)[ \t]{0,8}"
            r"(?:\"(?P<dq>[^\"\n]{0,256})\"|'(?P<sq>[^'\n]{0,256})'"
            r"|(?P<bare>[^\s\"'`,;()\[\]]{1,256}))",
            re.IGNORECASE,
        ),
        _literal,
    ),
    Rule(
        "email_address",
        "personal",
        _compile(
            r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@(?:[A-Za-z0-9-]{1,63}\.){1,8}"
            r"[A-Za-z]{2,63}(?![A-Za-z0-9-])"
        ),
    ),
    Rule(
        "phone_number",
        "personal",
        _compile(
            r"(?<![\w+])(?:\+[1-9](?:[ .-]?\d){7,14}(?!\w)"  # +1 415 555 0100, +4915112345678
            # North American, without "+": (415) 555-0199, 415-555-0199, 415.555.0199
            r"|(?:\(\d{3}\)[ ]?\d{3}-|\d{3}-\d{3}-|(?<![.])\d{3}\.\d{3}\.)\d{4}(?![\w-]|\.\d))"
        ),
    ),
    Rule(
        "us_ssn",
        "personal",
        _compile(r"(?<![\w-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\w-])"),
    ),
    Rule(
        "payment_card",
        "personal",
        _compile(
            r"(?<![\w+.-])(?:\d{13,19}|\d{4}(?P<s>[ -])\d{4}(?P=s)\d{4}(?P=s)\d{4}(?:(?P=s)\d{3})?"
            r"|\d{4}(?P<t>[ -])\d{6}(?P=t)\d{4,5})(?![\w-]|\.\d)"
        ),
        _luhn,
    ),
    Rule(
        "iban",
        "personal",
        _compile(
            r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}"
            r"(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?(?![A-Za-z0-9])"
        ),
        _iban,
    ),
    Rule(
        "date_of_birth",
        "personal",
        _compile(
            rf"(?<![A-Za-z0-9_])(?:dob|d\.o\.b\.?|date[ \t]of[ \t]birth|birth[ \t]?date)"
            rf"[ \t]{{0,4}}[:=-]?[ \t]{{0,4}}{_DATE}(?![A-Za-z0-9])",
            re.IGNORECASE,
        ),
    ),
    Rule(
        "medical_record_number",
        "personal",
        _compile(
            r"(?<![A-Za-z0-9_])(?:mrn|medical[ \t]record[ \t](?:number|no\.?))"
            r"[ \t]{0,4}(?:#|no\.?)?[ \t]{0,4}[:=#]?[ \t]{0,4}(?:[A-Za-z]{1,3}-?)?\d{4,15}"
            r"(?![A-Za-z0-9])",
            re.IGNORECASE,
        ),
    ),
)
# Each rule in a few words, for people: the web view's refusal and the audit page say these,
# and keep the rule's name for machines (REST and MCP answers name the rule as it is).
SAID = {
    "private_key": "a private key",
    "aws_access_key": "an AWS access key",
    "github_token": "a GitHub token",
    "slack_token": "a Slack token",
    "model_api_key": "a model provider's API key",
    "jwt": "a signed JSON Web Token",
    "cairn_credential": "a {product} access or refresh token",
    "cairn_api_key": "a {product} API key",
    "credential_url": "a password in a URL",
    "auth_header": "an Authorization header with its value",
    "assigned_secret": "a value assigned to a name like password, token or API key",
    "email_address": "an email address",
    "phone_number": "a phone number",
    "us_ssn": "a US Social Security number",
    "payment_card": "a payment card number",
    "iban": "a bank account number (IBAN)",
    "date_of_birth": "a date of birth",
    "medical_record_number": "a medical record number",
}


def rule_words(name: str, product: str = SOFTWARE) -> str:
    """A rule in words for people (SAID), naming the product as `product`; another guard's
    rule, by its own name."""
    said = SAID.get(name)
    return name if said is None else said.format(product=product)


class PatternGuard:
    """The default guard: the rules of one policy (see the module docstring)."""

    def __init__(self, policy: str = DEFAULT_POLICY) -> None:
        if policy not in POLICIES:
            raise ValueError(f"a guard policy is one of {POLICIES}, not {policy!r}")
        self.policy = policy
        self.rules = tuple(rule for rule in RULES if rule.kind in POLICY_KINDS[policy])

    def check(self, text: str) -> list[Finding]:
        findings = []
        for rule in self.rules:
            findings += _lines(rule, text)
        order = {rule.name: n for n, rule in enumerate(self.rules)}
        return sorted(findings, key=lambda f: (f.line, order[f.rule]))


def _lines(rule: Rule, text: str) -> list[Finding]:
    """The lines `rule` matches, each once, at most FINDINGS_PER_RULE. Matches come in
    order, so lines are counted on from the last one: one pass over the text."""
    found: list[Finding] = []
    line, counted = 1, 0
    for match in rule.pattern.finditer(text):
        line += text.count("\n", counted, match.start())
        counted = match.start()
        if (found and found[-1].line == line) or not rule.valid(match):
            continue
        found.append(Finding(rule=rule.name, kind=rule.kind, line=line))
        if len(found) == FINDINGS_PER_RULE:
            break
    return found
