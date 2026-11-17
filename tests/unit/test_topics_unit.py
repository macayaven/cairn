"""The topic key (N1, cairn.topics): the pairs its docstring decides, each pinned here."""

# ruff: noqa: RUF001  # titles in Cyrillic and Greek, whose letters look like Latin ones

import pytest

from cairn.topics import topic_key, topic_tag

LONG = "Incident review for the payments ledger reconciliation service in region"

SAME = [
    ("Retry policy", "retry-policy."),
    ("Retry policy", "RETRY  POLICY"),
    ("retry_policy", "retry/policy"),
    ('"Retry" (policy)!', "retry policy"),
    ("[Retry] {policy}\u2026", "\u2013 Retry policy \u2014"),
    ("Café menu", "Cafe menu"),
    ("Cafe\u0301 menu", "CAFÉ MENU"),
    ("Résumé; draft", "resume draft"),
    ("Πολιτική επανάληψης", "ΠΟΛΙΤΙΚΗ ΕΠΑΝΑΛΗΨΗΣ"),
    ("Настройка", "Настрои\u0306ка"),  # й, composed and decomposed
    ("ガイド", "カ\u3099イド"),
    ("\u095bरा", "ज\u093cरा"),  # a Devanagari nukta, composed and apart
    ("日本語", "日本語?"),
    ("Release 2.10", "release 2.10."),
    (".NET", "NET"),  # left open by the contract: the dot is at the word's edge
    ("Straße", "STRASSE"),  # left open: case folding
    ("!!!", "???"),
    ("!!!", ""),
]
DIFFERENT = [
    ("C++", "C#"),
    ("C#", "C"),
    ("R&D", "R D"),
    ("R D", "RD"),
    ("50% rollout", "50 rollout"),
    ("retry.policy", "retry policy"),
    ("Release 2.10", "Release 2-10"),
    ("Release 2-10", "Release 210"),
    ("Cafe", "\u0421afe"),  # a Cyrillic es
    ("Alpha", "\u0391lpha"),  # a Greek alpha
    ("Настройка", "Настроика"),
    ("ガイド", "カイド"),
    ("がっこう", "かっこう"),
    ("की", "कि"),
    ("ข่าว", "ข้าว"),
    ("น้ำ", "นำ"),
    ("日本語", "中文"),
    (f"{LONG} east", f"{LONG} west"),
    ("e-mail", "email"),  # left open: a hyphen is a separator
    ("ё", "е"),  # left open: the mark counts outside Latin and Greek
    ("R & D", "R D"),  # left open: a symbol alone counts
]


@pytest.mark.parametrize(("one", "other"), SAME)
def test_titles_the_rule_reads_alike_have_one_key(one, other):
    assert topic_key(one) == topic_key(other)


@pytest.mark.parametrize(("one", "other"), DIFFERENT)
def test_titles_the_rule_tells_apart_have_two_keys(one, other):
    assert topic_key(one) != topic_key(other)


def test_a_key_is_the_titles_words_folded_and_joined_by_one_space():
    assert topic_key("  RETRY -- Policy_v2 ") == "retry policy v2"
    assert topic_key("!!!") == "" and topic_key("") == ""
    assert topic_key("Café") == topic_key("Cafe") == "cafe"
    assert topic_key("Настрои\u0306ка") == "настройка"  # composed again


def test_two_titles_alike_in_their_first_150_characters_are_two_topics():
    stem = "word " * 30  # 150 characters
    assert topic_key(stem + "alpha") != topic_key(stem + "beta")


def test_a_tag_is_lower_case_hex_of_the_key_alone():
    assert topic_tag("c#") == topic_tag(topic_key("C#")) != topic_tag("c++")
    assert len(topic_tag("c#")) == 8 and int(topic_tag("c#"), 16) >= 0
    assert topic_tag("c#", 16).startswith(topic_tag("c#"))
    assert topic_tag("") == "e3b0c442"  # the SHA-256 of nothing: the same on every install
